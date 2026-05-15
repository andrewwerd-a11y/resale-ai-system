"""Bulk reintake preview reporting.

Read-only, draft-only aggregation for operator planning. This module never
publishes, never mutates item records, never resolves repair plans, and never
calls external intake providers unless a caller explicitly opts into deep
analysis preview.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from apps.api.src.services.intake_pipeline import build_pipeline_snapshot
from apps.api.src.services.publish_debug_diagnostics import (
    MAX_BATCH_SKUS,
    build_publish_debug_diagnostics_batch,
)
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from packages.core.src.constants import ItemStatus
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.intake.src.quality_gate import evaluate_intake_quality

DEFAULT_BULK_REINTAKE_STATUSES = [
    ItemStatus.NEEDS_REVIEW,
    ItemStatus.EXPORT_READY,
    ItemStatus.LISTED,
]
DEFAULT_REPORT_DIR = Path("data/reports/bulk_reintake_preview")
REPORT_TYPE = "bulk_reintake_preview"
REPORT_VERSION = "bulk-reintake-preview.v1"


def build_bulk_reintake_preview(
    session: Session,
    *,
    skus: list[str] | None = None,
    statuses: list[str] | None = None,
    run_deep_analysis_preview: bool = False,
    allow_live_readonly: bool = False,
    report_dir: Path | str | None = None,
    write_reports: bool = True,
) -> dict:
    """Build and optionally persist a read-only bulk reintake preview."""
    selected_statuses = _normalize_statuses(statuses)
    selected_skus = _select_skus(session, skus=skus, statuses=selected_statuses)
    repo = ItemRepository(session)
    diagnostics_by_sku = _diagnostics_by_sku(
        session,
        selected_skus,
        allow_live_readonly=allow_live_readonly,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    report_id = f"bulk-reintake-{uuid.uuid4().hex[:12]}"
    per_sku = [
        _build_sku_preview(
            repo,
            sku,
            diagnostics_by_sku.get(sku, {}),
            run_deep_analysis_preview=run_deep_analysis_preview,
        )
        for sku in selected_skus
    ]
    summary = _build_summary(per_sku)
    response = {
        "report_type": REPORT_TYPE,
        "report_version": REPORT_VERSION,
        "report_id": report_id,
        "generated_at": generated_at,
        "selected_statuses": selected_statuses,
        "requested_skus": _normalize_skus(skus or []),
        "read_only": True,
        "draft_only": True,
        "manual_approval_required": True,
        "no_publish_performed": True,
        "no_ebay_mutation_performed": True,
        "no_repair_plan_resolution_performed": True,
        "no_approval_mutation_performed": True,
        "no_item_record_overwrite_performed": True,
        "no_external_provider_called": not _any_external_provider_called(per_sku),
        "deep_analysis_preview_requested": bool(run_deep_analysis_preview),
        "live_readonly_requested": bool(allow_live_readonly),
        "generated_artifact_warning": (
            "Generated local report artifact. Do not commit JSON, Markdown, local DB, tokens, or secrets."
        ),
        "summary": summary,
        "per_sku_results": per_sku,
    }
    response["report_markdown"] = render_bulk_reintake_markdown(response)
    if write_reports:
        paths = write_bulk_reintake_reports(response, report_dir=report_dir)
        response["json_report_path"] = str(paths["json"])
        response["markdown_report_path"] = str(paths["markdown"])
    return response


def write_bulk_reintake_reports(response: dict, *, report_dir: Path | str | None = None) -> dict[str, Path]:
    target_dir = Path(report_dir) if report_dir is not None else DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    report_id = str(response.get("report_id") or "bulk-reintake-preview")
    json_path = target_dir / f"{report_id}.json"
    markdown_path = target_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(response, indent=2, sort_keys=True, default=str), encoding="utf-8")
    markdown_path.write_text(str(response.get("report_markdown") or ""), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def render_bulk_reintake_markdown(response: dict) -> str:
    summary = response.get("summary") or {}
    lines = [
        "# Bulk Reintake Preview",
        "",
        "Generated local artifact. Do not commit this report, local DB files, tokens, or secrets.",
        "",
        "## Safety",
        "- Read-only analysis only.",
        "- Do not publish automatically.",
        "- No eBay mutation, revise, end, relist, repair-plan resolution, approval mutation, or item overwrite was performed.",
        "- Provider output, when present, is draft/proposal-only and requires manual review.",
        "",
        "## Executive Summary",
        f"- Total SKUs: {summary.get('total_skus', 0)}",
        f"- Ready for publish preview: {summary.get('ready_for_publish_preview_count', 0)}",
        f"- Blocked: {summary.get('blocked_count', 0)}",
        f"- External provider called: {not bool(response.get('no_external_provider_called', True))}",
        "",
        "## Per-SKU Preview",
    ]
    for result in response.get("per_sku_results") or []:
        blockers = ", ".join(result.get("blockers") or []) or "none"
        missing = ", ".join(result.get("missing_photo_types") or []) or "none"
        lines.append(
            f"- {result.get('sku')}: status={result.get('current_local_status') or ''}; "
            f"category={result.get('category') or ''}; "
            f"intake_quality_status={result.get('intake_quality_status') or ''}; "
            f"needs_more_photos_for_analysis={result.get('needs_more_photos_for_analysis')}; "
            f"missing_photo_types={missing}; "
            f"workflow_lane={result.get('workflow_lane') or ''}; "
            f"primary_blocker_family={result.get('primary_blocker_family') or ''}; "
            f"blockers={blockers}; "
            f"next_safest_action={result.get('next_safest_action') or ''}"
        )
    return "\n".join(lines)


def _select_skus(session: Session, *, skus: list[str] | None, statuses: list[str]) -> list[str]:
    normalized = _normalize_skus(skus or [])
    if normalized:
        return normalized
    if not statuses:
        return []
    stmt = select(ItemRecord.sku).where(ItemRecord.status.in_(statuses)).order_by(ItemRecord.sku)
    return [str(value).upper() for value in session.exec(stmt).all() if value]


def _diagnostics_by_sku(session: Session, skus: list[str], *, allow_live_readonly: bool) -> dict[str, dict]:
    diagnostics: dict[str, dict] = {}
    for start in range(0, len(skus), MAX_BATCH_SKUS):
        batch = skus[start:start + MAX_BATCH_SKUS]
        if not batch:
            continue
        response = build_publish_debug_diagnostics_batch(
            session,
            batch,
            allow_live_readonly=allow_live_readonly,
        )
        for result in response.get("per_sku_results") or []:
            sku = str(result.get("sku") or "").upper()
            if sku:
                diagnostics[sku] = result
    return diagnostics


def _build_sku_preview(
    repo: ItemRepository,
    sku: str,
    diagnostics: dict,
    *,
    run_deep_analysis_preview: bool,
) -> dict:
    item = repo.get_by_sku(sku)
    if item is None:
        return {
            "sku": sku,
            "found": False,
            "current_local_status": "",
            "category": "",
            "intake_quality_status": "",
            "needs_more_photos_for_analysis": False,
            "missing_photo_types": [],
            "correction_report_v2_summary": {"available": False, "reason": "missing local item"},
            "operator_photo_evidence": {},
            "intake_pipeline_status": {"available": False, "reason": "missing local item"},
            "platform_draft_readiness": {"draft_only": True, "available": False, "reason": "missing local item"},
            "publish_readiness_summary": {"ready": False, "blockers": []},
            "publish_diagnostics_summary": _publish_diagnostics_summary(diagnostics),
            "ready_for_publish_preview": False,
            "workflow_lane": diagnostics.get("workflow_lane") or "unknown_manual_review",
            "primary_blocker_family": diagnostics.get("primary_blocker_family") or "missing_local_item",
            "blockers": list(diagnostics.get("blocker_codes") or ["missing_local_item"]),
            "next_safest_action": diagnostics.get("recommended_next_action") or "Create or import the local item record before reintake review.",
            "no_external_provider_called": True,
        }

    quality = evaluate_intake_quality(item).as_dict()
    pipeline = build_pipeline_snapshot(
        item,
        run_deep_analysis=run_deep_analysis_preview,
    )
    readiness = evaluate_publish_readiness(item).as_dict()
    deep = (pipeline.get("stages") or {}).get("deep_analysis")
    return {
        "sku": sku,
        "found": True,
        "current_local_status": str(item.status or ""),
        "category": str(item.category_key or item.ebay_category_name or item.ebay_category_id or ""),
        "intake_quality_status": quality.get("intake_quality_status"),
        "needs_more_photos_for_analysis": bool(quality.get("needs_more_photos_for_analysis")),
        "missing_photo_types": list(quality.get("missing_photo_types") or []),
        "correction_report_v2_summary": {
            "available": True,
            "publish_approval_blocked": bool(
                quality.get("should_block_publish_approval")
                or not readiness.get("ready")
                or bool(deep and deep.get("should_block_publish_approval"))
            ),
            "human_review_required": bool(
                quality.get("intake_quality_status") != "READY_FOR_DEEP_ANALYSIS"
                or bool(deep and deep.get("should_require_manual_review"))
            ),
            "grouped_next_action_count": 0,
        },
        "operator_photo_evidence": {
            "intake_quality_status": quality.get("intake_quality_status"),
            "needs_more_photos_for_analysis": bool(quality.get("needs_more_photos_for_analysis")),
            "missing_photo_types": list(quality.get("missing_photo_types") or []),
            "selected_photo_types": list((deep or {}).get("selected_photo_types") or []),
            "selected_image_count": (deep or {}).get("selected_image_count"),
            "skipped_image_count": (deep or {}).get("skipped_image_count"),
            "skipped_image_reasons": list((deep or {}).get("skipped_image_reasons") or []),
            "deep_analysis_image_selection_available": deep is not None,
        },
        "intake_pipeline_status": {
            "available": True,
            "category_family": pipeline.get("category_family"),
            "read_only": bool(pipeline.get("read_only")),
            "draft_only": bool(pipeline.get("draft_only")),
            "no_external_provider_called": bool(pipeline.get("no_external_provider_called")),
            "deep_analysis_preview_available": deep is not None,
        },
        "platform_draft_readiness": {
            "draft_only": True,
            "available": True,
            "summary": "Platform draft generation remains a separate read-only preview step.",
        },
        "publish_readiness_summary": {
            "ready": bool(readiness.get("ready")),
            "blockers": list(readiness.get("blockers") or []),
            "required_actions": list(readiness.get("required_actions") or []),
        },
        "publish_diagnostics_summary": _publish_diagnostics_summary(diagnostics),
        "ready_for_publish_preview": bool(diagnostics.get("ready_for_publish_preview")),
        "workflow_lane": diagnostics.get("workflow_lane") or "unknown_manual_review",
        "primary_blocker_family": diagnostics.get("primary_blocker_family") or "unknown_needs_manual_review",
        "blockers": list(diagnostics.get("blocker_codes") or []),
        "next_safest_action": diagnostics.get("recommended_next_action") or "Review diagnostics manually before any publish attempt.",
        "no_external_provider_called": bool(pipeline.get("no_external_provider_called")),
    }


def _publish_diagnostics_summary(diagnostics: dict) -> dict:
    return {
        "available": bool(diagnostics),
        "workflow_lane": diagnostics.get("workflow_lane"),
        "workflow_hint": diagnostics.get("workflow_hint"),
        "primary_blocker_family": diagnostics.get("primary_blocker_family"),
        "blockers": list(diagnostics.get("blocker_codes") or []),
        "next_safest_action": diagnostics.get("recommended_next_action"),
    }


def _build_summary(results: list[dict]) -> dict:
    return {
        "total_skus": len(results),
        "found": sum(1 for result in results if result.get("found")),
        "missing": sum(1 for result in results if not result.get("found")),
        "ready_for_publish_preview_count": sum(1 for result in results if result.get("ready_for_publish_preview")),
        "blocked_count": sum(1 for result in results if result.get("blockers")),
    }


def _any_external_provider_called(results: list[dict]) -> bool:
    return any(not bool(result.get("no_external_provider_called", True)) for result in results)


def _normalize_skus(skus: list[str]) -> list[str]:
    normalized: list[str] = []
    for sku in skus or []:
        value = str(sku or "").strip().upper()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_statuses(statuses: list[str] | None) -> list[str]:
    values = statuses if statuses is not None else DEFAULT_BULK_REINTAKE_STATUSES
    normalized: list[str] = []
    for status in values:
        value = str(status or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def counter_dict(values: list[Any]) -> dict[str, int]:
    return dict(Counter(str(value) for value in values if value))
