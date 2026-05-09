from __future__ import annotations

import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session

from apps.api.src.services.publish_diagnostics import build_publish_diagnostics
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from packages.core.src.config import get_settings
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.condition_mapping import (
    condition_id_to_inventory_enum,
    inventory_enum_to_condition_id,
)
from packages.ebay.src.public_image_urls import extract_public_image_urls
from packages.testing.src.e2e_guard import redact_mapping

DIAGNOSTIC_VERSION = "publish-debug-diagnostics.v1"
REPORT_TYPE = "publish_diagnostics_batch"
PROJECT_NAME = "resale-ai-system"
MAX_BATCH_SKUS = 50
SESSION_WARNING = (
    "This diagnostic session is intended for temporary debug cockpit use. Refreshing the future cockpit page may clear "
    "unsaved session history unless the report is exported or submitted."
)

RELATED_FILES_BY_FAMILY = {
    "condition": [
        "packages/ebay/src/condition_mapping.py",
        "packages/ebay/src/inventory_client.py",
        "apps/api/src/services/publish_diagnostics.py",
    ],
    "images": [
        "packages/ebay/src/public_image_urls.py",
        "packages/ebay/src/photo_uploader.py",
        "apps/api/src/services/publish_readiness.py",
    ],
    "offer_policy": [
        "packages/ebay/src/inventory_client.py",
        "apps/api/src/services/stale_offer_remediation.py",
        "apps/api/src/routes/listings.py",
    ],
    "readiness": [
        "apps/api/src/services/publish_readiness.py",
        "packages/ebay/src/aspect_validation.py",
        "apps/api/src/routes/listings.py",
    ],
    "missing_local_item": [
        "packages/data/src/repositories/item_repo.py",
        "apps/api/src/routes/listings.py",
    ],
    "route": [
        "apps/api/src/routes/listings.py",
        "apps/api/src/services/publish_debug_diagnostics.py",
    ],
}

BLOCKER_FAMILY_BY_CODE = {
    "missing_local_item": "missing_local_item",
    "missing_offer_id": "offer_policy",
    "missing_live_inventory_item": "offer_policy",
    "missing_live_offer": "offer_policy",
    "missing_hosted_images": "images",
    "local_image_path_only": "images",
    "missing_merchant_location": "offer_policy",
    "missing_listing_policies": "offer_policy",
    "local_live_condition_mismatch": "condition",
    "local_live_category_mismatch": "offer_policy",
    "live_inventory_condition_not_allowed_by_policy": "condition",
    "condition_id_enum_mapping_mismatch": "condition",
    "offer_inventory_category_mismatch": "offer_policy",
    "offer_inventory_condition_mismatch": "offer_policy",
    "required_aspects_missing": "readiness",
    "marketplace_mismatch": "offer_policy",
    "stale_live_inventory_condition_suspected": "condition",
    "stale_unpublished_offer_state_suspected": "offer_policy",
    "unknown_needs_manual_review": "readiness",
}


def build_publish_debug_diagnostics_batch(
    session: Session,
    skus: list[str],
    *,
    allow_live_readonly: bool = False,
) -> dict:
    normalized_skus = _normalize_skus(skus)
    if len(normalized_skus) > MAX_BATCH_SKUS:
        raise ValueError(f"Batch publish diagnostics is limited to {MAX_BATCH_SKUS} SKUs per request.")

    generated_at = datetime.now(timezone.utc).isoformat()
    session_id = f"pubdiag-{uuid.uuid4().hex[:12]}"
    repo = ItemRepository(session)
    per_sku_results = [
        _build_one_sku_result(
            session,
            repo,
            sku,
            allow_live_readonly=allow_live_readonly,
        )
        for sku in normalized_skus
    ]
    summary = _summary(per_sku_results)
    grouped = _grouped_blocker_families(per_sku_results)

    response = {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "report_type": REPORT_TYPE,
        "project": PROJECT_NAME,
        "environment": _safe_environment(),
        "persistable": True,
        "session_id": session_id,
        "generated_at": generated_at,
        "session_warning": SESSION_WARNING,
        "live_readonly_requested": bool(allow_live_readonly),
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "summary": summary,
        "grouped_blocker_families": grouped,
        "per_sku_results": per_sku_results,
    }
    response["copyable_report_markdown"] = _render_report_markdown(response)
    response["copyable_codex_prompt"] = _render_codex_prompt(response)
    return response


def _build_one_sku_result(
    session: Session,
    repo: ItemRepository,
    sku: str,
    *,
    allow_live_readonly: bool,
) -> dict:
    item = repo.get_by_sku(sku)
    diagnostics = build_publish_diagnostics(session, sku, allow_live_readonly=allow_live_readonly)
    if not item or not diagnostics.get("found"):
        blocker_codes = ["missing_local_item"]
        return {
            "sku": sku,
            "found": False,
            "ready_for_publish_preview": False,
            "local_item_state": {"status": "", "offer_id": "", "listing_id": ""},
            "local_category_id": "",
            "local_condition_id": "",
            "expected_inventory_enum": "",
            "live_inventory_item_state": {"read_available": False, "exists": "unknown"},
            "live_inventory_condition_enum": "",
            "live_inventory_condition_id": "",
            "live_offer_state": {"read_available": False, "exists": "unknown"},
            "live_category_policy_allowed_condition_ids": [],
            "image_hosting_readiness": {"status": "missing", "hosted_count": 0, "local_only_count": 0},
            "seller_policy_readiness": {"status": "unknown", "missing_policy_keys": []},
            "merchant_location_readiness": {"status": "unknown", "merchant_location_key": ""},
            "blocker_codes": blocker_codes,
            "warning_codes": [],
            "success_checks": [],
            "status_codes": [],
            "likely_root_cause_family": "missing_local_item",
            "recommended_next_action": "Create or import the local item record before diagnosing publish readiness.",
            "related_files_services": _related_files_for_codes(blocker_codes),
            "raw_details": _safe_raw_details(diagnostics=diagnostics, readiness={}),
        }

    readiness = evaluate_publish_readiness(item).as_dict()
    expected_inventory_enum = condition_id_to_inventory_enum(item.condition_id, default="")
    live_inventory = diagnostics.get("inventory_item_diagnostics") or {}
    live_offer = diagnostics.get("existing_offer_diagnostics") or {}
    live_policy = diagnostics.get("category_condition_policy_diagnostics") or {}
    mapping = diagnostics.get("condition_mapping_diagnostics") or {}
    live_inventory_condition_enum = str(live_inventory.get("condition_enum") or "")
    live_inventory_condition_id = (
        str(mapping.get("live_inventory_condition_id") or "")
        or inventory_enum_to_condition_id(live_inventory_condition_enum, default="")
    )
    blocker_codes = _classify_blockers(
        item=item,
        diagnostics=diagnostics,
        readiness=readiness,
        expected_inventory_enum=expected_inventory_enum,
        live_inventory_condition_enum=live_inventory_condition_enum,
        live_inventory_condition_id=live_inventory_condition_id,
    )
    warning_codes = _classify_warnings(item=item, readiness=readiness, diagnostics=diagnostics)
    ready = not blocker_codes and bool(readiness.get("ready")) and not bool(diagnostics.get("blocked_by_repair_queue"))
    status_codes = ["ready_for_publish_preview"] if ready else []
    success_checks = _success_checks(readiness=readiness, diagnostics=diagnostics, ready=ready)
    next_action = (
        "Open publish preview and review payloads before any separately approved publish flow."
        if ready
        else _recommended_next_action(blocker_codes, diagnostics)
    )

    return {
        "sku": sku,
        "found": True,
        "ready_for_publish_preview": ready,
        "local_item_state": {
            "status": str(item.status or ""),
            "offer_id": str(item.offer_id or ""),
            "listing_id": str(item.listing_id or ""),
            "planned_action": str(diagnostics.get("planned_action") or ""),
            "blocked_by_repair_queue": bool(diagnostics.get("blocked_by_repair_queue")),
        },
        "local_category_id": str(item.ebay_category_id or ""),
        "local_condition_id": str(item.condition_id or ""),
        "expected_inventory_enum": expected_inventory_enum,
        "live_inventory_item_state": {
            "read_available": bool(live_inventory.get("read_available")),
            "exists": live_inventory.get("inventory_item_exists", "unknown"),
            "source": live_inventory.get("source") or "",
        },
        "live_inventory_condition_enum": live_inventory_condition_enum,
        "live_inventory_condition_id": live_inventory_condition_id,
        "live_offer_state": {
            "read_available": bool(live_offer.get("read_available")),
            "exists": live_offer.get("offer_exists", "unknown"),
            "status": str(live_offer.get("status") or ""),
            "category_id": str(live_offer.get("category_id") or ""),
            "condition_id": str(live_offer.get("condition_id") or ""),
            "marketplace_id": str(live_offer.get("marketplace_id") or ""),
        },
        "live_category_policy_allowed_condition_ids": [str(value) for value in live_policy.get("allowed_condition_ids") or []],
        "image_hosting_readiness": _image_hosting_readiness(item),
        "seller_policy_readiness": _seller_policy_readiness(readiness),
        "merchant_location_readiness": _merchant_location_readiness(live_offer, allow_live_readonly=allow_live_readonly),
        "blocker_codes": blocker_codes,
        "warning_codes": warning_codes,
        "success_checks": success_checks,
        "status_codes": status_codes,
        "likely_root_cause_family": _likely_root_cause_family(blocker_codes, ready=ready),
        "recommended_next_action": next_action,
        "related_files_services": _related_files_for_codes(blocker_codes or status_codes),
        "raw_details": _safe_raw_details(diagnostics=diagnostics, readiness=readiness),
    }


def _classify_blockers(
    *,
    item,
    diagnostics: dict,
    readiness: dict,
    expected_inventory_enum: str,
    live_inventory_condition_enum: str,
    live_inventory_condition_id: str,
) -> list[str]:
    codes: list[str] = []
    live_inventory = diagnostics.get("inventory_item_diagnostics") or {}
    live_offer = diagnostics.get("existing_offer_diagnostics") or {}
    live_policy = diagnostics.get("category_condition_policy_diagnostics") or {}
    mapping = diagnostics.get("condition_mapping_diagnostics") or {}
    image = _image_hosting_readiness(item)
    seller_policy = _seller_policy_readiness(readiness)
    merchant_location = _merchant_location_readiness(
        live_offer,
        allow_live_readonly=bool(diagnostics.get("live_readonly_requested")),
    )

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if not str(item.offer_id or "").strip() and diagnostics.get("planned_action") == "publish_existing_offer":
        add("missing_offer_id")
    if diagnostics.get("live_readonly_requested") and live_inventory.get("read_available") is not True:
        add("missing_live_inventory_item")
    if diagnostics.get("live_readonly_requested") and str(item.offer_id or "").strip() and live_offer.get("read_available") is not True:
        add("missing_live_offer")
    if image["status"] == "missing":
        add("missing_hosted_images")
    if image["local_only_count"] and not image["hosted_count"]:
        add("local_image_path_only")
    if merchant_location["status"] == "missing":
        add("missing_merchant_location")
    if seller_policy["status"] == "missing":
        add("missing_listing_policies")
    if live_inventory_condition_enum and expected_inventory_enum and live_inventory_condition_enum != expected_inventory_enum:
        add("local_live_condition_mismatch")
    if live_offer.get("category_differs_from_local") is True:
        add("local_live_category_mismatch")
        add("offer_inventory_category_mismatch")
    if live_offer.get("condition_differs_from_local") is True:
        add("offer_inventory_condition_mismatch")
    if live_offer.get("read_available") is True:
        marketplace_id = str(live_offer.get("marketplace_id") or "")
        expected_marketplace = str(
            (diagnostics.get("live_readonly_auth") or {}).get("marketplace_id")
            or get_settings().ebay_marketplace_id
            or ""
        )
        if expected_marketplace and marketplace_id and marketplace_id != expected_marketplace:
            add("marketplace_mismatch")
    for finding in mapping.get("findings") or []:
        code = str(finding.get("code") or "")
        if code in {"condition_id_enum_mapping_mismatch", "live_inventory_condition_not_allowed_by_policy"}:
            add(code)
    if _has_required_aspect_blocker(readiness):
        add("required_aspects_missing")
    allowed_ids = [str(value) for value in live_policy.get("allowed_condition_ids") or []]
    if (
        str(item.condition_id or "") == "3000"
        and expected_inventory_enum == "USED_EXCELLENT"
        and live_inventory_condition_enum == "USED_GOOD"
        and live_inventory_condition_id == "5000"
        and "3000" in allowed_ids
        and "5000" not in allowed_ids
    ):
        add("stale_live_inventory_condition_suspected")
    if diagnostics.get("stale_existing_offer_hypothesis") is True:
        add("stale_unpublished_offer_state_suspected")
    if (readiness.get("ready") is False or diagnostics.get("blocked_by_repair_queue")) and not codes:
        add("unknown_needs_manual_review")
    return codes


def _classify_warnings(*, item, readiness: dict, diagnostics: dict) -> list[str]:
    warnings: list[str] = []
    if readiness.get("warnings"):
        warnings.append("publish_readiness_warnings")
    if diagnostics.get("live_readonly_errors"):
        warnings.append("live_readonly_errors")
    if diagnostics.get("live_readonly_unavailable"):
        warnings.append("live_readonly_unavailable")
    image = _image_hosting_readiness(item)
    if image["invalid_or_missing_local_count"]:
        warnings.append("invalid_or_missing_local_image_paths")
    return list(dict.fromkeys(warnings))


def _success_checks(*, readiness: dict, diagnostics: dict, ready: bool) -> list[str]:
    checks = [
        str(check.get("name"))
        for check in readiness.get("checks") or []
        if check.get("ok") is True and check.get("name")
    ]
    if diagnostics.get("live_readonly_performed"):
        checks.append("live_readonly_diagnostics_completed")
    if ready:
        checks.append("ready_for_publish_preview")
    return checks


def _image_hosting_readiness(item) -> dict:
    paths = [str(path).strip() for path in (item.image_paths or []) if str(path).strip()]
    hosted = extract_public_image_urls(paths)
    local_candidates = [path for path in paths if path not in hosted]
    existing_local = [path for path in local_candidates if Path(path).is_file()]
    missing_local = [path for path in local_candidates if not Path(path).is_file()]
    status = "ready" if hosted else ("local_only" if existing_local else "missing")
    return {
        "status": status,
        "hosted_count": len(hosted),
        "local_only_count": len(existing_local),
        "invalid_or_missing_local_count": len(missing_local),
        "hosted_photo_urls": _bounded_list(hosted, limit=12),
    }


def _seller_policy_readiness(readiness: dict) -> dict:
    check = _readiness_check(readiness, "seller_policy_readiness")
    context = check.get("context") or {}
    missing = [str(value) for value in context.get("missing_policy_keys") or []]
    ok = bool(check.get("ok"))
    status = "ready" if ok and not missing else ("discoverable" if ok else "missing")
    return {
        "status": status,
        "ok": ok,
        "missing_policy_keys": missing,
        "discovery_available": bool(context.get("discovery_available")),
        "needs_discovery": bool(context.get("needs_discovery")),
    }


def _merchant_location_readiness(live_offer: dict, *, allow_live_readonly: bool) -> dict:
    key = str(live_offer.get("merchant_location_key") or "")
    if key:
        status = "ready"
    elif allow_live_readonly and live_offer.get("read_available") is True:
        status = "missing"
    else:
        status = "not_checked"
    return {"status": status, "merchant_location_key": key}


def _has_required_aspect_blocker(readiness: dict) -> bool:
    for check in readiness.get("checks") or []:
        name = str(check.get("name") or "")
        detail = str(check.get("detail") or "").lower()
        if name == "category_template_validation" and check.get("ok") is False and "missing required" in detail:
            return True
    return False


def _readiness_check(readiness: dict, name: str) -> dict:
    return next((check for check in readiness.get("checks") or [] if check.get("name") == name), {})


def _recommended_next_action(blocker_codes: list[str], diagnostics: dict) -> str:
    if "missing_local_item" in blocker_codes:
        return "Create or import the local item record before diagnosing publish readiness."
    if "stale_live_inventory_condition_suspected" in blocker_codes:
        return "Do not publish. Refresh the stale live inventory item in a separately approved remediation flow, then rerun diagnostics."
    if "condition_id_enum_mapping_mismatch" in blocker_codes or "local_live_condition_mismatch" in blocker_codes:
        return "Resolve the condition mapping/live inventory mismatch before any publish retry."
    if "missing_hosted_images" in blocker_codes or "local_image_path_only" in blocker_codes:
        return "Host item images to public URLs before publish preview or publish."
    if "missing_listing_policies" in blocker_codes:
        return "Configure or verify seller listing policies before publish preview."
    if "missing_live_offer" in blocker_codes or "missing_live_inventory_item" in blocker_codes:
        return "Review missing live read-only eBay objects before any publish retry."
    return str(diagnostics.get("recommended_next_action") or "Review diagnostics manually before any publish attempt.")


def _likely_root_cause_family(blocker_codes: list[str], *, ready: bool) -> str:
    if ready:
        return "ready_for_publish_preview"
    for code in blocker_codes:
        family = BLOCKER_FAMILY_BY_CODE.get(code)
        if family:
            return family
    return "unknown_needs_manual_review"


def _related_files_for_codes(codes: list[str]) -> list[str]:
    files: list[str] = []
    for code in codes:
        family = BLOCKER_FAMILY_BY_CODE.get(code, "route")
        for path in RELATED_FILES_BY_FAMILY.get(family, RELATED_FILES_BY_FAMILY["route"]):
            if path not in files:
                files.append(path)
    return files or RELATED_FILES_BY_FAMILY["route"]


def _safe_raw_details(*, diagnostics: dict, readiness: dict) -> dict:
    payload = {
        "diagnostics": {
            "found": bool(diagnostics.get("found")),
            "live_readonly_requested": bool(diagnostics.get("live_readonly_requested")),
            "live_readonly_performed": bool(diagnostics.get("live_readonly_performed")),
            "live_readonly_methods_called": _bounded_list(diagnostics.get("live_readonly_methods_called") or []),
            "live_readonly_errors": _bounded_list(diagnostics.get("live_readonly_errors") or [], limit=5),
            "live_readonly_unavailable": _bounded_list(diagnostics.get("live_readonly_unavailable") or [], limit=5),
            "local_status": diagnostics.get("local_status") or "",
            "planned_action": diagnostics.get("planned_action") or "",
            "blocked_by_repair_queue": bool(diagnostics.get("blocked_by_repair_queue")),
            "classified_error_code": diagnostics.get("classified_error_code") or "",
            "condition_mapping_findings": _bounded_list(
                (diagnostics.get("condition_mapping_diagnostics") or {}).get("findings") or [],
                limit=5,
            ),
            "inventory_summary": _subset(
                diagnostics.get("inventory_item_diagnostics") or {},
                ["read_available", "inventory_item_exists", "condition_enum", "condition_differs_from_local", "source"],
            ),
            "offer_summary": _subset(
                diagnostics.get("existing_offer_diagnostics") or {},
                [
                    "read_available",
                    "offer_exists",
                    "status",
                    "category_id",
                    "condition_id",
                    "marketplace_id",
                    "category_differs_from_local",
                    "condition_differs_from_local",
                    "source",
                ],
            ),
            "policy_summary": _subset(
                diagnostics.get("category_condition_policy_diagnostics") or {},
                ["read_available", "allowed_condition_ids", "live_policy_allows_condition", "source"],
            ),
        },
        "readiness": {
            "ready": bool(readiness.get("ready")),
            "check_names": _bounded_list([check.get("name") for check in readiness.get("checks") or [] if check.get("name")], limit=30),
            "blocker_count": len(readiness.get("blockers") or []),
            "warning_count": len(readiness.get("warnings") or []),
        },
    }
    return _bound_value(redact_mapping(payload))


def _subset(value: dict, keys: list[str]) -> dict:
    return {key: value.get(key) for key in keys if key in value}


def _summary(results: list[dict]) -> dict:
    blocked = [result for result in results if result.get("blocker_codes")]
    return {
        "total": len(results),
        "found": sum(1 for result in results if result.get("found")),
        "missing": sum(1 for result in results if not result.get("found")),
        "ready_for_publish_preview": sum(1 for result in results if result.get("ready_for_publish_preview")),
        "blocked": len(blocked),
        "warnings": sum(1 for result in results if result.get("warning_codes")),
    }


def _grouped_blocker_families(results: list[dict]) -> dict:
    grouped: dict[str, list[str]] = defaultdict(list)
    for result in results:
        sku = str(result.get("sku") or "")
        for code in result.get("blocker_codes") or []:
            family = BLOCKER_FAMILY_BY_CODE.get(code, "unknown_needs_manual_review")
            if sku and sku not in grouped[family]:
                grouped[family].append(sku)
    return dict(sorted(grouped.items()))


def _render_report_markdown(response: dict) -> str:
    lines = [
        "# Publish Diagnostics Batch",
        "",
        f"- Session: `{response['session_id']}`",
        f"- Generated: `{response['generated_at']}`",
        f"- Version: `{response['diagnostic_version']}`",
        "",
        "## Summary",
    ]
    for key, value in (response.get("summary") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Grouped Blocker Families"])
    grouped = response.get("grouped_blocker_families") or {}
    if grouped:
        for family, skus in grouped.items():
            lines.append(f"- {family}: {', '.join(skus)}")
    else:
        lines.append("- none")
    lines.extend(["", "## Per-SKU Results"])
    for result in response.get("per_sku_results") or []:
        blockers = ", ".join(result.get("blocker_codes") or []) or "none"
        lines.append(f"- {result.get('sku')}: blockers={blockers}; next={result.get('recommended_next_action') or ''}")
    return "\n".join(lines)


def _render_codex_prompt(response: dict) -> str:
    return (
        "Analyze this read-only publish diagnostics batch for root cause and next safest action. "
        "Do not recommend eBay mutation unless separately approved. Focus on blocker codes, condition mapping, "
        "live read-only state, image readiness, and related files/services.\n\n"
        f"{_render_report_markdown(response)}"
    )


def _normalize_skus(skus: list[str]) -> list[str]:
    normalized: list[str] = []
    for sku in skus or []:
        value = str(sku or "").strip().upper()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _safe_environment() -> str:
    value = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "local").strip().lower()
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    return safe or "local"


def _bounded_list(values: list[Any], *, limit: int = 20) -> list[Any]:
    return list(values)[:limit]


def _bound_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        return {str(k): _bound_value(v, depth=depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_bound_value(v, depth=depth + 1) for v in value[:25]]
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:500]}..."
    return value
