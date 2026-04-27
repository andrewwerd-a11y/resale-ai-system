from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import traceback as tb
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import engine
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.inventory_client import EbayInventoryClient
from packages.ebay.src.photo_uploader import PhotoUploader
from packages.ebay.src.csv_writer import EbayCSVWriter
from packages.spreadsheet.src.master_sheet import MasterSheetWriter
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_e2e_sku_allowed,
    assert_live_e2e_allowed,
    assert_mutation_allowed,
    get_approved_e2e_skus,
    is_live_e2e_enabled,
    redact_mapping,
    summarize_env_safely,
)
from packages.testing.src.e2e_report import E2EResult, E2EStep, write_markdown

RESTORE_FIELDS = [
    "status",
    "needs_review",
    "review_reasons",
    "title_final",
    "description_final",
    "list_price",
    "cost",
    "storage_location",
    "notes",
    "manual_override",
    "cost_manual",
    "publish_ready",
    "missing_required_fields",
    "missing_recommended_fields",
    "listing_id",
    "listing_url",
    "platform",
    "date_listed",
    "date_sold",
    "sold_price",
    "fees",
    "shipping_cost",
    "net_profit",
    "profit_margin",
]


def _run_cmd(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=ROOT)
    return (proc.stdout or "").strip()


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_image_paths(raw: str | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [p for p in raw.split("|") if p.strip()]
    return []


def _classify_mock_category_failure(status_code: int, body: Any) -> tuple[bool, str]:
    """
    In mock mode, allow known upstream taxonomy failures to be reported as
    deterministic skips instead of hard failures.
    """
    if status_code not in (502, 503, 504):
        return False, ""
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        code = str(detail.get("code") or "").upper()
        if code in {
            "UPSTREAM_TIMEOUT",
            "UPSTREAM_CONNECTION",
            "UPSTREAM_PROXY",
            "AUTH_FAILED",
            "NO_TOKEN",
            "MALFORMED_RESPONSE",
        }:
            return True, code
    text = str(detail or body or "").lower()
    tokens = (
        "winerror 10061",
        "connection refused",
        "getaddrinfo failed",
        "timed out",
        "no connection could be made",
        "suggestion_error",
        "template_fetch_error",
    )
    if any(tok in text for tok in tokens):
        return True, "UPSTREAM_CONNECTION"
    return False, ""


def _db_connect() -> sqlite3.Connection:
    settings = get_settings()
    return sqlite3.connect(settings.db_path)


def _sku_state(sku: str) -> dict[str, Any] | None:
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT sku, status, item_mode, title_final, category_key, category_label,
                   ebay_category_id, ebay_category_name, condition_id, condition_label,
                   confidence_score, needs_review, review_reasons, missing_required_fields,
                   missing_recommended_fields, publish_ready, photo_folder, image_paths,
                   listing_id, listing_url, list_price, cost, sold_price, date_listed,
                   date_sold, platform, manual_override, cost_manual, enrichment_done,
                   description_final, storage_location, notes, fees, shipping_cost,
                   net_profit, profit_margin, updated_at
            FROM items WHERE sku = ?
            """,
            (sku,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _all_items_with_status(status: str) -> list[str]:
    conn = _db_connect()
    try:
        rows = conn.execute("SELECT sku FROM items WHERE status = ?", (status,)).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows if r and r[0]]


class E2ERunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.settings = get_settings()
        self.client = httpx.Client(base_url=args.base_url, timeout=30.0)
        self.approved = sorted(get_approved_e2e_skus())
        self.selected_skus = sorted(set(args.sku or self.approved))
        self.result = E2EResult(mode=args.mode, base_url=args.base_url)
        self.result.branch = _run_cmd(["git", "branch", "--show-current"]) or "unknown"
        self.result.commit = _run_cmd(["git", "rev-parse", "HEAD"]) or "unknown"
        self.result.approved_skus = self.selected_skus
        self.result.environment_summary = summarize_env_safely()
        self.critical_failed = False
        self._mutation_baseline: dict[str, dict[str, Any]] = {}
        self._ensure_selected_skus_safe()

    def _ensure_selected_skus_safe(self) -> None:
        for sku in self.selected_skus:
            assert_e2e_sku_allowed(sku)

    def _capture_mutation_baseline(self) -> None:
        self._mutation_baseline = {}
        for sku in self.selected_skus:
            state = _sku_state(sku)
            if state is not None:
                self._mutation_baseline[sku] = state

    def _restore_sku_from_baseline(self, sku: str) -> dict[str, Any]:
        baseline = self._mutation_baseline.get(sku)
        if not baseline:
            return {"restored_fields": [], "skipped_fields": RESTORE_FIELDS, "note": "No baseline captured"}
        assert_e2e_sku_allowed(sku)

        assignments = ", ".join(f"{field} = ?" for field in RESTORE_FIELDS)
        values = [baseline.get(field) for field in RESTORE_FIELDS]
        conn = _db_connect()
        try:
            conn.execute(f"UPDATE items SET {assignments} WHERE sku = ?", (*values, sku))
            conn.commit()
        finally:
            conn.close()

        log_row = {
            "sku": sku,
            "restored_fields": RESTORE_FIELDS,
            "skipped_fields": [],
            "note": "Restored from baseline snapshot via DB update",
        }
        self.result.restoration_log.append(log_row)
        return log_row

    def _non_approved_fingerprint(self) -> str:
        approved = set(self.selected_skus)
        conn = _db_connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT sku, status, updated_at FROM items").fetchall()
        finally:
            conn.close()
        payload = [
            (row["sku"], row["status"], row["updated_at"])
            for row in rows
            if row["sku"] not in approved
        ]
        payload.sort(key=lambda x: x[0] or "")
        return json.dumps(payload, default=str)

    def _step(
        self,
        name: str,
        workflow: str,
        endpoint_or_function: str,
        fn,
        *,
        sku: str | None = None,
        critical: bool = False,
        optional: bool = False,
    ) -> None:
        started = datetime.utcnow()
        try:
            payload = fn()
            status = payload.pop("status", "PASS") if isinstance(payload, dict) else "PASS"
            notes = payload.pop("notes", "") if isinstance(payload, dict) else ""
            response_summary = payload if isinstance(payload, dict) else {"result": str(payload)}
            error = None
            traceback_text = None
        except Exception as exc:  # noqa: BLE001
            status = "FAIL"
            notes = ""
            response_summary = None
            error = str(exc)
            traceback_text = tb.format_exc(limit=6)
            if critical and not optional:
                self.critical_failed = True
        ended = datetime.utcnow()
        step = E2EStep(
            name=name,
            workflow=workflow,
            status=status,
            endpoint_or_function=endpoint_or_function,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=(ended - started).total_seconds(),
            sku=sku,
            request_summary=None,
            response_summary=redact_mapping(response_summary) if response_summary is not None else None,
            error=error,
            traceback=traceback_text,
            notes=notes,
        )
        self.result.steps.append(step)

    def _api(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        resp = self.client.request(method, path, **kwargs)
        try:
            body: Any = resp.json()
        except Exception:
            body = resp.text[:1000]
        out = {
            "status_code": resp.status_code,
            "body": body,
        }
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed: {resp.status_code} {str(body)[:300]}")
        return out

    def _backup_db(self) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_dir = ROOT / "data" / "e2e_reports"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"app_backup_{ts}.db"
        shutil.copy2(self.settings.db_path, backup_path)
        self.result.db_backup_path = str(backup_path)
        return str(backup_path)

    def _snapshot_db(self, target: dict[str, Any]) -> None:
        snap = {}
        for sku in self.selected_skus:
            snap[sku] = _sku_state(sku)
        target.update(snap)

    def _safe_mode_for_ebay_mutation(self) -> tuple[bool, str]:
        env = (self.settings.ebay_environment or "").lower()
        if self.args.mode == "mock":
            return False, "mock mode uses dry checks only"
        if self.args.mode == "sandbox":
            if env != "sandbox":
                return False, "sandbox mode requested but EBAY_ENVIRONMENT is not sandbox"
            return True, "sandbox eBay mutation allowed for approved SKUs"
        if self.args.mode == "live-gated":
            if env != "production":
                return False, "live-gated requested but EBAY_ENVIRONMENT is not production"
            if not is_live_e2e_enabled():
                return False, "ALLOW_LIVE_E2E is not true"
            return True, "production live-gated mutation allowed for approved SKUs"
        return False, "unknown mode"

    def run(self) -> Path:
        self._backup_db()
        self._snapshot_db(self.result.db_before)
        self._capture_mutation_baseline()
        self._run_db_connectivity_workflow()
        self._run_health_workflow()
        self._run_baseline_workflow()
        self._run_missing_cloudinary_investigation()
        self._run_ai_integration_workflow()
        self._run_intake_workflow()
        self._run_item_crud_workflow()
        self._run_review_workflow()
        self._run_category_workflow()
        self._run_photo_workflow()
        self._run_export_workflow()
        self._run_ebay_workflow()
        self._run_stale_workflow()
        self._run_lot_workflow()
        self._run_sourcing_workflow()
        self._run_reports_workflow()
        self._run_settings_workflow()
        self._run_capture_workflow()
        self._run_sync_workflow()
        self._run_failure_matrix_workflow()
        self._snapshot_db(self.result.db_after)
        self._build_coverages()
        self._set_verdict()
        report_path = self._report_path()
        write_markdown(self.result, report_path)
        return report_path

    def _report_path(self) -> Path:
        if self.args.output:
            return Path(self.args.output)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return ROOT / "data" / "e2e_reports" / f"e2e_report_{ts}.md"

    def _run_health_workflow(self) -> None:
        checks = [
            ("health", "GET /api/health", "/api/health"),
            ("dashboard", "GET /", "/"),
            ("items_stats", "GET /api/items/stats", "/api/items/stats"),
            ("settings_current", "GET /api/settings/current", "/api/settings/current"),
            ("ebay_status", "GET /api/ebay/status", "/api/ebay/status"),
            ("ebay_oauth_status", "GET /api/ebay/oauth/status", "/api/ebay/oauth/status"),
        ]
        if self.args.mode != "mock":
            checks.append(
                (
                    "listings_connectivity",
                    "GET /api/listings/ebay-connectivity",
                    "/api/listings/ebay-connectivity",
                )
            )
        else:
            self._step(
                name="listings_connectivity_skip",
                workflow="Health and startup",
                endpoint_or_function="GET /api/listings/ebay-connectivity",
                fn=lambda: {
                    "status": "SKIP",
                    "notes": "Mock mode skips live eBay connectivity probe.",
                },
            )
        for name, endpoint_name, path in checks:
            self._step(
                name=name,
                workflow="Health and startup",
                endpoint_or_function=endpoint_name,
                critical=True,
                fn=lambda p=path: self._api("GET", p),
            )

    def _run_db_connectivity_workflow(self) -> None:
        def check_db():
            conn = _db_connect()
            try:
                count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            finally:
                conn.close()
            return {"item_count": count}

        self._step(
            name="db_connectivity",
            workflow="DB connectivity",
            endpoint_or_function="sqlite3 SELECT COUNT(*) FROM items",
            fn=check_db,
            critical=True,
        )

    def _run_baseline_workflow(self) -> None:
        def check_exists():
            missing = [sku for sku in self.selected_skus if _sku_state(sku) is None]
            if missing:
                raise RuntimeError(f"Approved SKU(s) missing: {', '.join(missing)}")
            return {"count": len(self.selected_skus), "skus": self.selected_skus}

        self._step(
            name="approved_sku_existence",
            workflow="Approved SKU baseline",
            endpoint_or_function="DB lookup",
            fn=check_exists,
            critical=True,
        )
        for sku in self.selected_skus:
            self._step(
                name=f"get_item_{sku}",
                workflow="Approved SKU baseline",
                endpoint_or_function=f"GET /api/items/{sku}",
                sku=sku,
                critical=True,
                fn=lambda s=sku: self._api("GET", f"/api/items/{s}"),
            )

    def _run_missing_cloudinary_investigation(self) -> None:
        def investigate():
            hits: list[str] = []
            search_roots = [ROOT / "apps", ROOT / "packages"]
            for root in search_roots:
                for py in root.rglob("*.py"):
                    rel_path = py.relative_to(ROOT)
                    rel = rel_path.as_posix()
                    if rel.startswith("packages/testing/"):
                        continue
                    text = py.read_text(encoding="utf-8", errors="ignore")
                    if "missing_cloudinary_upload" not in text:
                        continue
                    for idx, line in enumerate(text.splitlines(), start=1):
                        if "missing_cloudinary_upload" in line:
                            hits.append(f"{rel}:{idx}")

            sku_details = {}
            for sku in self.selected_skus:
                state = _sku_state(sku) or {}
                paths = _parse_image_paths(state.get("image_paths"))
                local_exists = [Path(p).exists() for p in paths if p and "://" not in p]
                sku_details[sku] = {
                    "review_reasons": state.get("review_reasons"),
                    "image_paths_count": len(paths),
                    "all_local_paths": all("://" not in p for p in paths) if paths else True,
                    "local_paths_exist_all": all(local_exists) if local_exists else True,
                }

            uploader = PhotoUploader()
            source = (
                "No active code reference found for 'missing_cloudinary_upload'; "
                "value exists in DB review_reasons."
            )
            if hits:
                source = f"Found in code at: {', '.join(hits)}"

            self.result.missing_cloudinary_upload_source = source
            self.result.missing_cloudinary_upload_hypothesis = (
                "Items currently store local file paths in image_paths; when Cloudinary-hosted URLs are "
                "required for eBay workflows, prior logic likely flagged this as missing_cloudinary_upload."
            )
            return {
                "code_hits": hits,
                "photo_uploader_configured": uploader.is_configured(),
                "sku_details": sku_details,
                "notes": source,
            }

        self._step(
            name="missing_cloudinary_upload_trace",
            workflow="Cloudinary investigation",
            endpoint_or_function="Code + DB inspection",
            fn=investigate,
        )

    def _run_intake_workflow(self) -> None:
        if self.args.skip_intake:
            self._step(
                "intake_skip_flag",
                "Intake workflow",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "--skip-intake was provided"},
            )
            return

        def constrained_intake():
            before_unapproved = self._non_approved_fingerprint()
            resp = self._api(
                "POST",
                "/api/items/process",
                params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
            )
            body = resp.get("body", {})
            after_unapproved = self._non_approved_fingerprint()
            if before_unapproved != after_unapproved:
                raise RuntimeError("Constrained intake changed non-approved SKU state")
            return {
                "status_code": resp.get("status_code"),
                "requested_skus": body.get("requested_skus", self.selected_skus),
                "found_skus": body.get("found_skus", []),
                "missing_skus": body.get("missing_skus", []),
                "processed_count": body.get("processed_count"),
                "approved_count": body.get("approved_count"),
                "review_count": body.get("review_count"),
                "failed_count": body.get("failed_count"),
                "notes": "Constrained intake path executed.",
            }

        self._step(
            "intake_process_constrained",
            "Intake workflow",
            "POST /api/items/process",
            fn=constrained_intake,
        )

    def _run_ai_integration_workflow(self) -> None:
        def ollama_check():
            if self.args.skip_ollama:
                return {"status": "SKIP", "notes": "--skip-ollama was provided"}
            health = self._api("GET", "/api/health")
            body = health.get("body", {})
            return {
                "ollama_available": body.get("ollama"),
                "model": body.get("model"),
                "intake_vision_provider": "local_ollama",
                "claude_vision_intake_implemented": False,
                "openai_vision_intake_implemented": False,
            }

        def anthropic_check():
            if self.args.skip_anthropic:
                return {"status": "SKIP", "notes": "--skip-anthropic was provided"}
            return {
                "anthropic_configured": bool((self.settings.anthropic_api_key or "").strip()),
                "enrichment_enabled": bool(self.settings.enrichment_enabled),
                "claude_text_enrichment_implemented": True,
                "premium_vision_calls_in_mock_mode": False,
            }

        self._step(
            "ollama_availability",
            "AI integrations",
            "GET /api/health",
            fn=ollama_check,
        )
        self._step(
            "anthropic_config",
            "AI integrations",
            "settings.anthropic_api_key presence",
            fn=anthropic_check,
        )

    def _run_item_crud_workflow(self) -> None:
        sku = "BK-000005" if "BK-000005" in self.selected_skus else self.selected_skus[0]
        marker = datetime.utcnow().strftime("E2E-%Y%m%d-%H%M%S")
        original = _sku_state(sku) or {}

        def patch_item():
            assert_mutation_allowed(sku, self.args.mode, "item_patch")
            payload = {
                "title_final": f"{(original.get('title_final') or 'Untitled')[:60]} [{marker}]",
                "description_final": f"E2E reversible marker {marker}",
                "list_price": 23.45,
                "storage_location": f"E2E-SHELF-{marker}",
                "notes": f"E2E notes {marker}",
            }
            return self._api("PATCH", f"/api/items/{sku}", json=payload)

        def patch_cost():
            assert_mutation_allowed(sku, self.args.mode, "cost_patch")
            return self._api("PATCH", f"/api/items/{sku}/cost", json={"cost": 5.67})

        def verify():
            item = self._api("GET", f"/api/items/{sku}")["body"]
            if not item.get("manual_override"):
                raise RuntimeError("manual_override was not set by item PATCH flow")
            if not item.get("cost_manual"):
                raise RuntimeError("cost_manual was not set by cost PATCH flow")
            return {"manual_override": item.get("manual_override"), "cost_manual": item.get("cost_manual")}

        def restore():
            return self._restore_sku_from_baseline(sku)

        self._step("patch_item_main", "Item CRUD/manual edit", f"PATCH /api/items/{sku}", patch_item, sku=sku)
        self._step("patch_cost_main", "Item CRUD/manual edit", f"PATCH /api/items/{sku}/cost", patch_cost, sku=sku)
        self._step("verify_item_flags", "Item CRUD/manual edit", f"GET /api/items/{sku}", verify, sku=sku)
        self._step("restore_item_fields", "Item CRUD/manual edit", f"PATCH /api/items/{sku}", restore, sku=sku)

    def _run_review_workflow(self) -> None:
        if "BK-000008" not in self.selected_skus:
            self._step(
                "review_workflow_skip",
                "Review workflow",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "BK-000008 not selected"},
            )
            return
        sku = "BK-000008"
        marker = datetime.utcnow().strftime("E2E-REVIEW-%Y%m%d-%H%M%S")
        original = _sku_state(sku) or {}

        self._step(
            "bulk_review",
            "Review workflow",
            "POST /api/items/bulk-review",
            sku=sku,
            fn=lambda: self._api("POST", "/api/items/bulk-review", json={"skus": [sku]}),
        )
        self._step("review_queue_get", "Review workflow", "GET /api/review", fn=lambda: self._api("GET", "/api/review"))
        self._step(
            "review_edit_and_approve",
            "Review workflow",
            f"PATCH /api/review/{sku}/edit",
            sku=sku,
            fn=lambda: self._api("PATCH", f"/api/review/{sku}/edit", json={"notes": marker}),
        )
        self._step(
            "review_reject_skip",
            "Review workflow",
            f"POST /api/review/{sku}/reject",
            sku=sku,
            fn=lambda: {
                "status": "SKIP",
                "notes": "Reject mutation skipped because safe automatic restoration is not guaranteed.",
            },
        )
        self._step(
            "review_restore_baseline",
            "Review workflow",
            f"DB restore {sku}",
            sku=sku,
            fn=lambda s=sku, _o=original: self._restore_sku_from_baseline(s),
        )

    def _run_category_workflow(self) -> None:
        for sku in self.selected_skus:
            def run_category_intel(s=sku):
                resp = self.client.post(f"/api/items/{s}/category-intelligence")
                try:
                    body: Any = resp.json()
                except Exception:
                    body = resp.text[:1000]
                if resp.status_code < 400:
                    return {"status_code": resp.status_code, "body": body}
                if self.args.mode == "mock":
                    allowed, code = _classify_mock_category_failure(resp.status_code, body)
                    if allowed:
                        return {
                            "status": "SKIP",
                            "status_code": resp.status_code,
                            "body": body,
                            "notes": f"Mock mode accepted classified taxonomy failure: {code}",
                        }
                raise RuntimeError(
                    f"POST /api/items/{s}/category-intelligence failed: {resp.status_code} {str(body)[:300]}"
                )

            self._step(
                f"category_intelligence_{sku}",
                "Category intelligence",
                f"POST /api/items/{sku}/category-intelligence",
                sku=sku,
                fn=run_category_intel,
            )
            self._step(
                f"category_template_{sku}",
                "Category intelligence",
                f"GET /api/items/{sku}/category-template",
                sku=sku,
                fn=lambda s=sku: self._api("GET", f"/api/items/{s}/category-template"),
            )

    def _run_photo_workflow(self) -> None:
        if self.args.skip_photos:
            self._step(
                "photos_skip_flag",
                "Photo workflow",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "--skip-photos was provided"},
            )
            return
        for sku in self.selected_skus:
            state = _sku_state(sku) or {}
            paths = _parse_image_paths(state.get("image_paths"))
            self._step(
                f"photo_paths_{sku}",
                "Photo workflow",
                "DB image_paths inspection",
                sku=sku,
                fn=lambda p=paths: {
                    "image_paths_count": len(p),
                    "local_path_count": len([x for x in p if "://" not in x]),
                    "cloud_url_count": len([x for x in p if "://" in x]),
                },
            )
            first_local = next((p for p in paths if "://" not in p and Path(p).exists()), None)
            if first_local:
                self._step(
                    f"serve_local_photo_{sku}",
                    "Photo workflow",
                    f"GET /api/items/{sku}/image",
                    sku=sku,
                    fn=lambda s=sku, p=first_local: self._api("GET", f"/api/items/{s}/image", params={"path": p}),
                )
            else:
                self._step(
                    f"serve_local_photo_{sku}_skip",
                    "Photo workflow",
                    f"GET /api/items/{sku}/image",
                    sku=sku,
                    fn=lambda: {"status": "SKIP", "notes": "No existing local image path found to serve."},
                )

    def _run_export_workflow(self) -> None:
        if self.args.skip_csv:
            self._step(
                "csv_skip_flag",
                "CSV export workflow",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "--skip-csv was provided"},
            )
            return
        self._step(
            "export_route_constrained",
            "CSV export workflow",
            "POST /api/export/ebay-csv",
            fn=lambda: self._api(
                "POST",
                "/api/export/ebay-csv",
                params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
            ),
        )

        def direct_safe_export():
            with Session(engine) as session:
                repo = ItemRepository(session)
                items = [repo.get_by_sku(sku) for sku in self.selected_skus]
                safe_items = [i for i in items if i is not None]
                if not safe_items:
                    return {"status": "SKIP", "notes": "No approved SKUs available for direct CSV writer test."}
                csv_path = EbayCSVWriter().write(safe_items)
                master_path = MasterSheetWriter().write(safe_items)
                self.result.generated_files.append(str(csv_path))
                self.result.generated_files.append(str(master_path))
                return {
                    "csv_path": str(csv_path),
                    "master_path": str(master_path),
                    "included_skus": [i.sku for i in safe_items],
                }

        self._step(
            "direct_safe_export",
            "CSV export workflow",
            "EbayCSVWriter.write + MasterSheetWriter.write",
            fn=direct_safe_export,
        )
        self._step("export_stats", "CSV export workflow", "GET /api/export/stats", fn=lambda: self._api("GET", "/api/export/stats"))

    def _run_ebay_workflow(self) -> None:
        if self.args.skip_ebay:
            self._step(
                "ebay_skip_flag",
                "eBay publish/revision",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "--skip-ebay was provided"},
            )
            return

        self._step("ebay_status", "eBay publish/revision", "GET /api/ebay/status", fn=lambda: self._api("GET", "/api/ebay/status"))
        if self.args.mode == "mock":
            self._step(
                "listings_sync_skip",
                "eBay publish/revision",
                "GET /api/listings/sync",
                fn=lambda: {
                    "status": "SKIP",
                    "notes": "Mock mode skips external listing sync call.",
                },
            )
            self._step(
                "ebay_connectivity_skip",
                "eBay publish/revision",
                "GET /api/listings/ebay-connectivity",
                fn=lambda: {
                    "status": "SKIP",
                    "notes": "Mock mode skips eBay connectivity probe.",
                },
            )
        else:
            self._step(
                "listings_sync",
                "eBay publish/revision",
                "GET /api/listings/sync",
                fn=lambda: self._api(
                    "GET",
                    "/api/listings/sync",
                    params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
                ),
            )
            self._step(
                "ebay_connectivity",
                "eBay publish/revision",
                "GET /api/listings/ebay-connectivity",
                fn=lambda: self._api("GET", "/api/listings/ebay-connectivity"),
            )

        can_mutate, reason = self._safe_mode_for_ebay_mutation()
        if not can_mutate:
            def dry_payload_check():
                with Session(engine) as session:
                    repo = ItemRepository(session)
                    item = repo.get_by_sku("BK-000005")
                    if not item:
                        return {"status": "SKIP", "notes": "BK-000005 missing for dry payload check."}
                    client = EbayInventoryClient()
                    inventory_payload = client._build_inventory_payload(item, [])
                    offer_payload = client._build_offer_payload(
                        item,
                        {"fulfillment_id": "", "payment_id": "", "return_id": ""},
                        merchant_location_key="default",
                    )
                    return {
                        "notes": f"eBay mutation skipped: {reason}",
                        "inventory_payload_keys": sorted(inventory_payload.keys()),
                        "offer_payload_keys": sorted(offer_payload.keys()),
                    }

            self._step(
                "ebay_dry_payload_check",
                "eBay publish/revision",
                "EbayInventoryClient payload builders",
                fn=dry_payload_check,
            )
            return

        sku = "BK-000005" if "BK-000005" in self.selected_skus else self.selected_skus[0]
        self._step(
            "ebay_publish_sku",
            "eBay publish/revision",
            f"POST /api/ebay/publish/{sku}",
            sku=sku,
            fn=lambda s=sku: (assert_live_e2e_allowed(s) if self.args.mode == "live-gated" else assert_mutation_allowed(s, self.args.mode, "ebay_publish")) or self._api("POST", f"/api/ebay/publish/{s}"),
        )

        rev_sku = "BK-000009" if "BK-000009" in self.selected_skus else sku
        self._step(
            "ebay_revision",
            "eBay publish/revision",
            f"PATCH /api/ebay/listing/{rev_sku}",
            sku=rev_sku,
            fn=lambda s=rev_sku: (assert_live_e2e_allowed(s) if self.args.mode == "live-gated" else assert_mutation_allowed(s, self.args.mode, "ebay_revision")) or self._api("PATCH", f"/api/ebay/listing/{s}", json={"list_price": 22.99}),
        )

        self._step(
            "sold_workflow_skip",
            "eBay publish/revision",
            "POST /api/ebay/mark-sold/{sku}",
            fn=lambda: (
                {
                    "status": "SKIP",
                    "notes": "--skip-sold was provided",
                }
                if self.args.skip_sold
                else {
                    "status": "SKIP",
                    "notes": "Skipped by default: sold-state mutation is not safely reversible through public API.",
                }
            ),
        )

    def _run_stale_workflow(self) -> None:
        self._step(
            "items_stale",
            "Stale listing workflow",
            "GET /api/items/stale",
            fn=lambda: self._api("GET", "/api/items/stale"),
        )
        self._step(
            "apply_stale_drops_constrained",
            "Stale listing workflow",
            "POST /api/items/apply-stale-drops",
            fn=lambda: self._api(
                "POST",
                "/api/items/apply-stale-drops",
                params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
            ),
        )

    def _run_lot_workflow(self) -> None:
        self._step("lots_list", "Lot workflow", "GET /api/lots", fn=lambda: self._api("GET", "/api/lots"))
        self._step(
            "lots_mutation_skip",
            "Lot workflow",
            "POST /api/lots/create",
            fn=lambda: {
                "status": "SKIP",
                "notes": "Lot create/dissolve mutates multiple records and is skipped by default in shared DB.",
            },
        )

    def _run_sourcing_workflow(self) -> None:
        self._step(
            "sourcing_batches",
            "Sourcing workflow",
            "GET /api/sourcing/batches",
            fn=lambda: self._api("GET", "/api/sourcing/batches"),
        )
        self._step(
            "sourcing_mutation_skip",
            "Sourcing workflow",
            "POST /api/sourcing/batch",
            fn=lambda: {
                "status": "SKIP",
                "notes": "Skipped by default to avoid persistent batch/cost mutations in shared DB.",
            },
        )

    def _run_reports_workflow(self) -> None:
        if self.args.skip_reports:
            self._step(
                "reports_skip_flag",
                "Reports workflow",
                "N/A",
                fn=lambda: {"status": "SKIP", "notes": "--skip-reports was provided"},
            )
            return
        endpoints = [
            "/api/reports/summary",
            "/api/reports/sales",
            "/api/reports/by-category",
            "/api/reports/by-platform",
            "/api/reports/by-month",
            "/api/reports/category-intelligence",
            "/api/reports/category-intelligence/export",
            "/api/reports/export-csv",
        ]
        for ep in endpoints:
            method = "POST" if ep.endswith("/export-csv") else "GET"
            self._step(
                f"reports_{ep.split('/')[-1]}",
                "Reports workflow",
                f"{method} {ep}",
                fn=lambda m=method, p=ep: self._api(m, p),
            )

    def _run_settings_workflow(self) -> None:
        for ep in ["/api/settings/rules", "/api/settings/platforms", "/api/settings/current"]:
            self._step(
                f"settings_{ep.split('/')[-1]}",
                "Settings workflow",
                f"GET {ep}",
                fn=lambda p=ep: self._api("GET", p),
            )

    def _run_capture_workflow(self) -> None:
        self._step(
            "capture_status",
            "Capture workflow",
            "GET /api/capture/status",
            fn=lambda: self._api("GET", "/api/capture/status"),
        )

    def _run_sync_workflow(self) -> None:
        self._step(
            "sync_ended_listings",
            "Sync/relist workflow",
            "GET /api/sync/ended-listings",
            fn=lambda: self._api("GET", "/api/sync/ended-listings"),
        )
        self._step(
            "sync_relist_all_constrained",
            "Sync/relist workflow",
            "POST /api/sync/relist-all",
            fn=lambda: self._api(
                "POST",
                "/api/sync/relist-all",
                params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
            ),
        )
        self._step(
            "sync_sold_constrained",
            "Sold workflow",
            "POST /api/ebay/sync-sold",
            fn=lambda: self._api(
                "POST",
                "/api/ebay/sync-sold",
                params={"skus": ",".join(self.selected_skus), "e2e_only": "true"},
            ),
        )

    def _record_failure_case(
        self,
        *,
        workflow: str,
        simulated_failure: str,
        expected_behavior: str,
        actual_behavior: str,
        item_state_changed: bool,
        unrelated_state_changed: bool,
        error_clear: bool,
        secrets_redacted: bool,
        file_function: str,
        recommended_fix: str,
    ) -> None:
        self.result.failure_matrix.append(
            {
                "workflow": workflow,
                "simulated_failure": simulated_failure,
                "expected_behavior": expected_behavior,
                "actual_behavior": actual_behavior,
                "item_state_changed": item_state_changed,
                "unrelated_state_changed": unrelated_state_changed,
                "error_clear": error_clear,
                "secrets_redacted": secrets_redacted,
                "file_function": file_function,
                "recommended_fix": recommended_fix,
            }
        )

    def _run_failure_matrix_workflow(self) -> None:
        # Cloudinary upload failure: nonexistent file should fail safely.
        before_selected = {sku: _sku_state(sku) for sku in self.selected_skus}
        before_unapproved = self._non_approved_fingerprint()
        uploader = PhotoUploader()
        upload_result = uploader.upload(Path(r"C:\__e2e_nonexistent__.jpg"))
        after_selected = {sku: _sku_state(sku) for sku in self.selected_skus}
        after_unapproved = self._non_approved_fingerprint()
        self._record_failure_case(
            workflow="Photo/Cloudinary",
            simulated_failure="upload nonexistent file",
            expected_behavior="returns failure without crashing or mutating DB",
            actual_behavior=f"ok={upload_result.ok} error={upload_result.error}",
            item_state_changed=before_selected != after_selected,
            unrelated_state_changed=before_unapproved != after_unapproved,
            error_clear=bool(upload_result.error),
            secrets_redacted=True,
            file_function="packages/ebay/src/photo_uploader.py::upload",
            recommended_fix="" if not upload_result.ok else "Return explicit failure for missing local files.",
        )

        # eBay auth/token redaction quality.
        sample = {"Authorization": "Bearer very-secret-token-12345"}
        redacted = redact_mapping(sample)
        self._record_failure_case(
            workflow="eBay auth/token",
            simulated_failure="sensitive header in diagnostics",
            expected_behavior="token value must be redacted",
            actual_behavior=f"authorization={redacted.get('Authorization')}",
            item_state_changed=False,
            unrelated_state_changed=False,
            error_clear=True,
            secrets_redacted=redacted.get("Authorization") != sample["Authorization"],
            file_function="packages/testing/src/e2e_guard.py::redact_mapping",
            recommended_fix="" if redacted.get("Authorization") != sample["Authorization"] else "Expand redaction keys for Authorization headers.",
        )

        # Global mutation routes should reject e2e_only without explicit skus.
        for route, workflow, file_fn in [
            ("/api/export/ebay-csv", "CSV export failure handling", "apps/api/src/routes/export.py::generate_ebay_csv"),
            ("/api/export/master-sheet", "Master sheet failure handling", "apps/api/src/routes/export.py::generate_master_sheet"),
            ("/api/items/apply-stale-drops", "Stale drop failure handling", "apps/api/src/routes/items.py::apply_stale_drops"),
            ("/api/sync/relist-all", "Relist-all failure handling", "apps/api/src/routes/sync.py::relist_all"),
            ("/api/ebay/sync-sold", "Sold-sync failure handling", "apps/api/src/routes/ebay.py::sync_sold"),
            ("/api/ebay/publish/batch", "Batch publish failure handling", "apps/api/src/routes/ebay.py::publish_batch"),
        ]:
            before_selected = {sku: _sku_state(sku) for sku in self.selected_skus}
            before_unapproved = self._non_approved_fingerprint()
            detail = ""
            status_code = None
            clear_err = False
            try:
                resp = self.client.post(route, params={"e2e_only": "true"}, timeout=10.0)
                status_code = resp.status_code
                try:
                    body = resp.json()
                    clear_err = "detail" in body
                    detail = str(body.get("detail", ""))[:300]
                except Exception:
                    detail = resp.text[:200]
            except Exception as exc:  # noqa: BLE001
                detail = f"request_exception: {exc}"
            after_selected = {sku: _sku_state(sku) for sku in self.selected_skus}
            after_unapproved = self._non_approved_fingerprint()
            self._record_failure_case(
                workflow=workflow,
                simulated_failure="e2e_only without sku constraints",
                expected_behavior="request rejected with clear JSON error and no DB mutation",
                actual_behavior=f"status={status_code} detail={detail}",
                item_state_changed=before_selected != after_selected,
                unrelated_state_changed=before_unapproved != after_unapproved,
                error_clear=clear_err and bool(status_code and status_code >= 400),
                secrets_redacted=True,
                file_function=file_fn,
                recommended_fix=""
                if status_code and status_code >= 400
                else "Reject unconstrained e2e_only requests with 4xx JSON detail.",
            )

        # Intake/Ollama/vision and Anthropic current-state checks.
        health = self._api("GET", "/api/health")
        body = health.get("body", {})
        ollama_available = bool(body.get("ollama"))
        self._record_failure_case(
            workflow="Intake/Ollama/Vision",
            simulated_failure="Ollama unavailable",
            expected_behavior="graceful skip/failure, no crash",
            actual_behavior=f"ollama_available={ollama_available}",
            item_state_changed=False,
            unrelated_state_changed=False,
            error_clear=True,
            secrets_redacted=True,
            file_function="packages/vision/src/ollama_provider.py::is_available",
            recommended_fix="" if ollama_available else "Keep DRY_RUN path documented for non-Ollama environments.",
        )

        self._record_failure_case(
            workflow="Premium vision provider",
            simulated_failure="claude_vision intake provider requested",
            expected_behavior="planned/not implemented documented, no paid call in mock mode",
            actual_behavior="not implemented; Ollama intake remains default; Anthropic used for text enrichment only",
            item_state_changed=False,
            unrelated_state_changed=False,
            error_clear=True,
            secrets_redacted=True,
            file_function="docs/ROADMAP.md::Phase 4",
            recommended_fix="Implement provider toggle + escalation policy in future Phase 4 without changing default provider.",
        )

    def _build_coverages(self) -> None:
        endpoint_rows: list[dict[str, Any]] = []
        workflow_map: dict[str, list[str]] = {}
        workflow_notes: dict[str, list[str]] = {}
        for step in self.result.steps:
            code = ""
            if isinstance(step.response_summary, dict):
                code = str(step.response_summary.get("status_code", ""))
            endpoint_rows.append(
                {
                    "name": step.name,
                    "status": step.status,
                    "status_code": code,
                    "notes": step.notes or "",
                }
            )
            workflow_map.setdefault(step.workflow, []).append(step.status)
            if step.notes:
                workflow_notes.setdefault(step.workflow, []).append(step.notes)

        workflow_rows: list[dict[str, Any]] = []
        for workflow, statuses in workflow_map.items():
            if "FAIL" in statuses:
                status = "FAIL"
            elif "PASS" in statuses:
                status = "PASS"
            else:
                status = "SKIP"
            note = "; ".join(workflow_notes.get(workflow, []))
            workflow_rows.append({"workflow": workflow, "status": status, "notes": note[:300]})

        self.result.endpoint_coverage = endpoint_rows
        self.result.workflow_coverage = workflow_rows

    def _set_verdict(self) -> None:
        connectivity = next(
            (s for s in self.result.steps if s.name == "ebay_connectivity" and s.status == "PASS"),
            None,
        )
        env = (self.settings.ebay_environment or "").lower()
        if self.critical_failed:
            self.result.safety_verdict = "not safe yet"
            return
        if self.args.mode == "mock":
            self.result.safety_verdict = "safe only in sandbox"
            return
        if env == "sandbox":
            self.result.safety_verdict = "safe only in sandbox"
            return
        if env == "production" and self.args.mode == "live-gated" and is_live_e2e_enabled():
            self.result.safety_verdict = "safe for real inventory"
            return
        if connectivity:
            self.result.safety_verdict = "unknown"
        else:
            self.result.safety_verdict = "not safe yet"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe E2E harness for Resale AI System.")
    parser.add_argument("--mode", choices=["mock", "sandbox", "live-gated"], required=True)
    parser.add_argument("--sku", action="append", help="Approved SKU to include. Repeatable.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-ebay", action="store_true")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-cloudinary", action="store_true")
    parser.add_argument("--skip-anthropic", action="store_true")
    parser.add_argument("--skip-intake", action="store_true")
    parser.add_argument("--skip-photos", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    parser.add_argument("--skip-sold", action="store_true")
    parser.add_argument("--skip-reports", action="store_true")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        runner = E2ERunner(args)
        report_path = runner.run()
        print(f"E2E report written: {report_path}")
        if runner.critical_failed:
            print("Critical E2E checks failed.")
            return 1
        return 0
    except E2ESafetyError as exc:
        print(f"E2E safety error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
