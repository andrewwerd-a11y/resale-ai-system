from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from apps.api.src.services.ebay_auth_diagnostics import get_ebay_auth_readiness
from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility
from apps.api.src.services.publish_diagnostics import build_publish_diagnostics
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from apps.api.src.services.publish_repair import get_publish_repair_blocker
from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.condition_mapping import condition_id_to_inventory_enum
from packages.ebay.src.public_image_urls import (
    extract_public_image_urls,
    is_valid_public_image_url,
    looks_like_public_image_url_candidate,
    normalize_public_image_url,
)

MAX_BULK_PREVIEW_SKUS = 250
DECISION_WOULD_PUBLISH = "WOULD_PUBLISH"
DECISION_SKIP = "SKIP"
DECISION_REPAIR = "REPAIR"
DECISION_REVIEW = "REVIEW"
DECISION_ALREADY_LISTED = "ALREADY_LISTED"
DECISION_AUTH_BLOCKED = "AUTH_BLOCKED"
PERSISTENT_REPORT_BASENAME = "bulk_publish_preview"
ALLOWED_STATUS_FILTERS = {
    ItemStatus.APPROVED,
    ItemStatus.EXPORT_READY,
    ItemStatus.LISTED,
    ItemStatus.NEEDS_REVIEW,
}
DEFAULT_BATCH_PUBLISH_STATUSES = [
    ItemStatus.EXPORT_READY,
    ItemStatus.APPROVED,
]
NEXT_BEST_ACTION_PRIORITY = [
    "Fix invalid condition ID",
    "Missing required condition data",
    "Fix invalid aspects",
    "Resolve existing-offer / repair queue blockers",
    "Fill required fields",
    "Fetch/confirm category condition policy",
    "Host photos",
    "Add missing photos / run image review",
    "Move out of needs_review / approve item",
    "Manual high-value/authenticity review",
    "Publish-readiness blocked",
    "Would publish",
]


def build_bulk_publish_preview(
    session: Session,
    *,
    skus: list[str] | None = None,
    statuses: list[str] | None = None,
    persist_report: bool = True,
) -> dict:
    normalized_skus = _normalize_skus(skus or [])
    normalized_statuses = _normalize_statuses(statuses or [])
    if not normalized_skus and not normalized_statuses:
        raise ValueError("At least one SKU or status filter is required for batch publish preview.")
    if len(normalized_skus) > MAX_BULK_PREVIEW_SKUS:
        raise ValueError(f"Batch publish preview is limited to {MAX_BULK_PREVIEW_SKUS} explicit SKUs per request.")

    auth_readiness = get_ebay_auth_readiness()
    repo = ItemRepository(session)
    items, missing_skus = collect_batch_preview_items(
        repo,
        skus=normalized_skus,
        statuses=normalized_statuses,
    )

    decisions = [evaluate_bulk_publish_candidate(session, item, auth_readiness=auth_readiness) for item in items]
    for sku in missing_skus:
        decisions.append(
            {
                "sku": sku,
                "decision": DECISION_SKIP,
                "local_publish_ready": False,
                "effective_publish_ready": False,
                "blocked_by_repair_queue": False,
                "repair_plan_id": "",
                "classified_error_code": "missing_item",
                "photo_hosting_state": "missing",
                "category_id": "",
                "condition_id": "",
                "inventory_condition_enum": "",
                "offer_id": "",
                "listing_id": "",
                "planned_action": "",
                "primary_reason_code": "missing_item",
                "secondary_blockers": [],
                "condition_id_valid": False,
                "category_policy_cached": False,
                "category_policy_known": False,
                "needs_review_status_blocker": False,
                "reason_code": "missing_item",
                "message": "Item was not found.",
                "next_action": "Create or import the item before batch publish.",
                "retry_allowed": False,
                "requires_review": True,
                "status": "",
                "details": {},
            }
        )

    for decision in decisions:
        decision["next_best_action_group"] = next_best_action_group(decision)
        decision["next_action_sequence"] = next_action_sequence(decision)

    summary = summarize_bulk_publish_preview(decisions)
    grouped = group_bulk_publish_preview_decisions(decisions)
    actionable_groups = group_bulk_publish_preview_by_next_step(decisions)
    next_best_groups = group_bulk_publish_preview_by_next_best_action(decisions)
    report_markdown = render_bulk_publish_preview_markdown(
        summary=summary,
        decisions=decisions,
        grouped=grouped,
        actionable_groups=actionable_groups,
        next_best_groups=next_best_groups,
        selected_skus=normalized_skus,
        selected_statuses=normalized_statuses,
    )
    persisted_report_path = persist_bulk_publish_preview_report(report_markdown) if persist_report else ""

    sanitized_decisions = [sanitize_bulk_publish_decision(decision) for decision in decisions]

    return {
        "report_type": PERSISTENT_REPORT_BASENAME,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_skus": normalized_skus,
        "selected_statuses": normalized_statuses,
        "auth_blocked": bool(auth_readiness.get("blockers")),
        "summary": summary,
        "grouped_decisions": grouped,
        "grouped_actionable_next_steps": actionable_groups,
        "grouped_next_best_actions": next_best_groups,
        "decisions": sanitized_decisions,
        "report_markdown": report_markdown,
        "persisted_report_path": persisted_report_path,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
    }


def collect_batch_preview_items(
    repo: ItemRepository,
    *,
    skus: list[str],
    statuses: list[str],
) -> tuple[list, list[str]]:
    items: list = []
    missing_skus: list[str] = []
    seen: set[str] = set()

    if skus:
        for sku in skus:
            item = repo.get_by_sku(sku)
            if item is None:
                missing_skus.append(sku)
                continue
            if statuses and str(item.status or "") not in statuses:
                continue
            normalized = str(item.sku or "").upper()
            if normalized not in seen:
                seen.add(normalized)
                items.append(item)
        return items, missing_skus

    for status in statuses:
        for item in repo.list_by_status(status):
            normalized = str(item.sku or "").upper()
            if normalized not in seen:
                seen.add(normalized)
                items.append(item)
    return items, missing_skus


def evaluate_bulk_publish_candidate(
    session: Session,
    item,
    *,
    auth_readiness: dict | None = None,
) -> dict:
    auth = auth_readiness or get_ebay_auth_readiness()
    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)
    diagnostics = build_publish_diagnostics(session, item.sku or "", allow_live_readonly=False)
    repair_blocker = get_publish_repair_blocker(session, item.sku or "")
    preflight_blockers = list(dict.fromkeys(readiness["blockers"] + compatibility["blockers"]))
    category_id = str(item.ebay_category_id or "")
    condition_id = str(item.condition_id or "")
    offer_id = str(item.offer_id or "")
    listing_id = str(item.listing_id or "")
    planned_action = str(diagnostics.get("planned_action") or "")
    blocker_entries = _blocker_entries(readiness, compatibility)
    blocker_entries.extend(_state_blocker_entries(offer_id=offer_id, listing_id=listing_id, planned_action=planned_action))
    photo_hosting_state = _photo_hosting_state(item, compatibility)
    local_publish_ready = bool(diagnostics.get("local_publish_ready"))
    effective_publish_ready = bool(diagnostics.get("effective_publish_ready"))
    condition_id_valid = _condition_id_valid(compatibility)
    inventory_condition_enum = condition_id_to_inventory_enum(condition_id, default="") if condition_id_valid else ""
    category_policy_known = _category_policy_known(compatibility)
    status_blocker = _status_blocker(readiness)

    payload = {
        "sku": str(item.sku or "").upper(),
        "decision": DECISION_SKIP,
        "local_publish_ready": local_publish_ready,
        "effective_publish_ready": effective_publish_ready,
        "blocked_by_repair_queue": bool(repair_blocker.get("blocked_by_repair_queue")),
        "repair_plan_id": str(repair_blocker.get("repair_plan_id") or ""),
        "classified_error_code": str(repair_blocker.get("classified_error_code") or diagnostics.get("classified_error_code") or ""),
        "photo_hosting_state": photo_hosting_state,
        "category_id": category_id,
        "condition_id": condition_id,
        "inventory_condition_enum": inventory_condition_enum,
        "offer_id": offer_id,
        "listing_id": listing_id,
        "planned_action": planned_action,
        "primary_reason_code": "",
        "secondary_blockers": [],
        "condition_id_valid": condition_id_valid,
        "category_policy_cached": category_policy_known,
        "category_policy_known": category_policy_known,
        "needs_review_status_blocker": status_blocker,
        "reason_code": "",
        "message": "",
        "next_action": "",
        "retry_allowed": bool(repair_blocker.get("retry_allowed")),
        "requires_review": bool(repair_blocker.get("requires_review")),
        "status": str(item.status or ""),
        "details": {
            "readiness_blockers": list(readiness.get("blockers") or []),
            "compatibility_blockers": list(compatibility.get("blockers") or []),
            "effective_publish_blockers": list(diagnostics.get("effective_publish_blockers") or []),
            "publish_block_summary": str(diagnostics.get("publish_block_summary") or ""),
        },
        "readiness": readiness,
        "compatibility": compatibility,
        "diagnostics": diagnostics,
        "repair_blocker": repair_blocker,
        "preflight_blockers": preflight_blockers,
        "blocker_entries": blocker_entries,
    }

    if str(item.status or "") == ItemStatus.LISTED or listing_id:
        return _with_reason(payload, {
            "decision": DECISION_ALREADY_LISTED,
            "reason_code": "already_listed",
            "message": "Item already has listed status or a listing_id; publishing again would risk a duplicate listing.",
            "next_action": "Use revise/update or listing sync flows for already-listed items.",
            "retry_allowed": False,
            "requires_review": True,
        })

    if auth.get("blockers"):
        return _with_reason(payload, {
            "decision": DECISION_AUTH_BLOCKED,
            "reason_code": str(auth.get("code") or "ebay_auth_not_ready"),
            "message": str(auth.get("message") or "eBay auth is not ready for publish."),
            "next_action": str(auth.get("next_action") or "Reconnect or refresh eBay auth before publishing."),
            "retry_allowed": False,
            "requires_review": True,
        })

    if repair_blocker.get("blocked_by_repair_queue"):
        repair_code = str(repair_blocker.get("classified_error_code") or "blocked_by_repair_queue")
        stale_or_manual = bool(
            repair_code == "requires_publish_decision_after_refresh"
            or diagnostics.get("stale_existing_offer_hypothesis")
            or planned_action == "publish_existing_offer" and offer_id and not listing_id
        )
        return _with_reason(payload, {
            "decision": DECISION_REVIEW if stale_or_manual else DECISION_REPAIR,
            "reason_code": repair_code or "blocked_by_repair_queue",
            "message": str(repair_blocker.get("reason") or diagnostics.get("publish_block_summary") or "Publish is blocked by an active repair plan."),
            "next_action": _next_action_from_repair_blocker(repair_blocker),
            "requires_review": True,
        })

    if diagnostics.get("stale_existing_offer_hypothesis") is True:
        return _with_reason(payload, {
            "decision": DECISION_REVIEW,
            "reason_code": "stale_existing_offer_hypothesis",
            "message": "Existing unpublished offer may contain stale category or condition state.",
            "next_action": "Run read-only diagnostics and route through the manual stale-offer decision flow before publishing.",
            "retry_allowed": False,
            "requires_review": True,
        })

    if preflight_blockers:
        reason_code, decision, message, next_action = _classify_preflight_blockers(
            preflight_blockers,
            compatibility=compatibility,
            diagnostics=diagnostics,
            photo_hosting_state=photo_hosting_state,
        )
        return _with_reason(payload, {
            "decision": decision,
            "reason_code": reason_code,
            "message": message,
            "next_action": next_action,
            "retry_allowed": False,
            "requires_review": decision in {DECISION_REPAIR, DECISION_REVIEW},
        })

    return _with_reason(payload, {
        "decision": DECISION_WOULD_PUBLISH,
        "reason_code": "would_publish",
        "classified_error_code": "would_publish",
        "message": "All local publish safety checks passed for batch dry-run.",
        "next_action": "Proceed only through separately approved publish flow with live mutation controls still enforced.",
        "retry_allowed": True,
        "requires_review": False,
    })


def sanitize_bulk_publish_decision(decision: dict) -> dict:
    """Expose operator-facing preview fields while preserving internal gate details for local reuse."""
    classified = str(decision.get("classified_error_code") or decision.get("reason_code") or "")
    return decision | {"classified_error_code": classified}


def _with_reason(payload: dict, update: dict) -> dict:
    reason_code = str(update.get("reason_code") or "")
    entries = list(payload.get("blocker_entries") or [])
    secondary = [entry for entry in entries if entry.get("code") != reason_code]
    return payload | update | {
        "primary_reason_code": reason_code,
        "secondary_blockers": secondary,
    }


def _blocker_entries(readiness: dict, compatibility: dict) -> list[dict]:
    entries: list[dict] = []
    for check in list(readiness.get("checks") or []):
        if check.get("blocking") and not check.get("ok"):
            entries.append(_entry_from_check(check, source="readiness"))
    for check in list(compatibility.get("checks") or []):
        if check.get("blocking") and not check.get("ok"):
            entries.append(_entry_from_check(check, source="compatibility"))
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (str(entry.get("code") or ""), str(entry.get("message") or ""))
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def _state_blocker_entries(*, offer_id: str, listing_id: str, planned_action: str) -> list[dict]:
    if offer_id and not listing_id and planned_action == "publish_existing_offer":
        return [
            {
                "code": "existing_unpublished_offer",
                "message": "Local item has an existing unpublished eBay offer_id; preview is planning publish_existing_offer instead of creating a new offer.",
                "next_action": "Confirm the existing offer is current with read-only diagnostics before publishing.",
                "source": "listing_state",
                "check": "existing_offer_state",
                "context": {
                    "offer_id": offer_id,
                    "listing_id": listing_id,
                    "planned_action": planned_action,
                },
            }
        ]
    return []


def _entry_from_check(check: dict, *, source: str) -> dict:
    return {
        "code": _code_for_check(check),
        "message": str(check.get("detail") or ""),
        "next_action": str(check.get("action") or ""),
        "source": source,
        "check": str(check.get("name") or ""),
        "context": check.get("context") or {},
    }


def _code_for_check(check: dict) -> str:
    name = str(check.get("name") or "")
    detail = str(check.get("detail") or "").lower()
    if name == "condition_id_format":
        return "invalid_condition_id_format"
    if name == "public_image_urls":
        return "photo_hosting_required"
    if name in {"publishable_status", "not_blocked_from_publish"}:
        return "status_not_publishable"
    if name == "category_condition_policy":
        if "not cached" in detail or "unknown" in detail:
            return "category_condition_policy_unknown"
        return "invalid_category_condition"
    if name in {"category_template_requirements", "category_template_validation"}:
        return "missing_required_aspects" if "missing" in detail else "category_template_invalid"
    if name in {"required_title", "required_description", "required_price", "required_category_id", "required_condition_id", "offer_basics"}:
        return "missing_required_fields"
    if name in {"aspect_value_constraints", "aspect_value_lengths"}:
        return "invalid_aspect_value"
    if name == "seller_policy_readiness":
        return "seller_policy_missing_or_invalid"
    if name == "environment_sku_guard":
        return "sku_guard_blocked"
    return name or "publish_readiness_blocked"


def _condition_id_valid(compatibility: dict) -> bool:
    check = _check_by_name(compatibility, "condition_id_format")
    return bool(check and check.get("ok"))


def _category_policy_known(compatibility: dict) -> bool:
    check = _check_by_name(compatibility, "category_condition_policy")
    if not check:
        check = _check_by_name(compatibility, "condition_id_format")
    context = check.get("context") if check else {}
    return bool((context or {}).get("known"))


def _status_blocker(readiness: dict) -> bool:
    checks = list(readiness.get("checks") or [])
    return any(
        str(check.get("name") or "") in {"publishable_status", "not_blocked_from_publish"}
        and bool(check.get("blocking"))
        and not bool(check.get("ok"))
        for check in checks
    )


def _check_by_name(payload: dict, name: str) -> dict:
    for check in list(payload.get("checks") or []):
        if str(check.get("name") or "") == name:
            return check
    return {}


def summarize_bulk_publish_preview(decisions: list[dict]) -> dict:
    reason_counts = Counter(str(decision.get("reason_code") or "") for decision in decisions)
    decision_counts = Counter(str(decision.get("decision") or "") for decision in decisions)
    photo_states = Counter(str(decision.get("photo_hosting_state") or "") for decision in decisions)
    return {
        "total": len(decisions),
        "would_publish_count": decision_counts[DECISION_WOULD_PUBLISH],
        "skip_count": decision_counts[DECISION_SKIP],
        "repair_count": decision_counts[DECISION_REPAIR],
        "review_count": decision_counts[DECISION_REVIEW],
        "already_listed_count": decision_counts[DECISION_ALREADY_LISTED],
        "auth_blocked_count": decision_counts[DECISION_AUTH_BLOCKED],
        "missing_photo_count": sum(
            1
            for decision in decisions
            if decision.get("photo_hosting_state") in {"missing", "local_only", "invalid_public_url", "missing_local_files"}
        ),
        "stale_offer_count": sum(
            1
            for decision in decisions
            if decision.get("reason_code") in {"requires_publish_decision_after_refresh", "stale_existing_offer_hypothesis"}
        ),
        "invalid_category_condition_count": sum(
            1
            for decision in decisions
            if decision.get("reason_code") in {"invalid_category_condition", "publish_readiness_blocked"}
            and any("Condition ID" in blocker for blocker in decision.get("details", {}).get("compatibility_blockers", []))
        ),
        "invalid_condition_id_format_count": sum(
            1
            for decision in decisions
            if _decision_has_blocker(decision, "invalid_condition_id_format")
        ),
        "reason_counts": dict(sorted((key, value) for key, value in reason_counts.items() if key)),
        "photo_hosting_states": dict(sorted((key, value) for key, value in photo_states.items() if key)),
    }


def group_bulk_publish_preview_decisions(decisions: list[dict]) -> dict:
    grouped: dict[str, list[str]] = defaultdict(list)
    for decision in decisions:
        label = f"{decision.get('decision') or 'UNKNOWN'}:{decision.get('reason_code') or 'unknown'}"
        grouped[label].append(str(decision.get("sku") or ""))
    return dict(sorted((key, sorted(values)) for key, values in grouped.items()))


def group_bulk_publish_preview_by_next_step(decisions: list[dict]) -> dict:
    grouped: dict[str, list[str]] = defaultdict(list)
    for decision in decisions:
        sku = str(decision.get("sku") or "")
        for label in _actionable_labels(decision):
            grouped[label].append(sku)
    return dict(sorted((key, sorted(set(values))) for key, values in grouped.items()))


def group_bulk_publish_preview_by_next_best_action(decisions: list[dict]) -> dict:
    grouped: dict[str, list[str]] = defaultdict(list)
    for decision in decisions:
        grouped[next_best_action_group(decision)].append(str(decision.get("sku") or ""))
    return dict(sorted((key, sorted(set(values))) for key, values in grouped.items()))


def next_best_action_group(decision: dict) -> str:
    labels = _actionable_labels(decision)
    for candidate in NEXT_BEST_ACTION_PRIORITY:
        if candidate in labels:
            return candidate
    return labels[0] if labels else "Publish-readiness blocked"


def next_action_sequence(decision: dict) -> list[dict]:
    labels = _actionable_labels(decision)
    ordered = [label for label in NEXT_BEST_ACTION_PRIORITY if label in labels]
    ordered.extend(label for label in labels if label not in ordered)
    return [
        {
            "group": label,
            "action": _action_text_for_group(label, decision),
        }
        for label in ordered
    ]


def render_bulk_publish_preview_markdown(
    *,
    summary: dict,
    decisions: list[dict],
    grouped: dict,
    actionable_groups: dict,
    next_best_groups: dict,
    selected_skus: list[str],
    selected_statuses: list[str],
) -> str:
    lines = [
        "# Bulk Publish Preview",
        "",
        f"- Generated: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Selected SKUs: `{', '.join(selected_skus) if selected_skus else 'status-filtered'}`",
        f"- Status Filters: `{', '.join(selected_statuses) if selected_statuses else 'none'}`",
        "- No live eBay mutation performed.",
        "",
        "## Summary",
    ]
    for key in [
        "total",
        "would_publish_count",
        "skip_count",
        "repair_count",
        "review_count",
        "already_listed_count",
        "auth_blocked_count",
        "missing_photo_count",
        "stale_offer_count",
        "invalid_category_condition_count",
        "invalid_condition_id_format_count",
    ]:
        lines.append(f"- {key}: {summary.get(key, 0)}")
    lines.extend(["", "## Grouped Decisions"])
    for key, skus in grouped.items():
        lines.append(f"- {key}: {', '.join(skus)}")
    lines.extend(["", "## Actionable Next Steps"])
    for key, skus in actionable_groups.items():
        lines.append(f"- {key}: {', '.join(skus)}")
    lines.extend(["", "## Next Best Action Plan"])
    for key, skus in next_best_groups.items():
        lines.append(f"- {key}: {', '.join(skus)}")
    lines.extend(["", "## Per SKU"])
    for decision in decisions:
        secondary = decision.get("secondary_blockers") or []
        secondary_text = "; ".join(
            f"{entry.get('code')}: {entry.get('message')}"
            for entry in secondary
        )
        sequence = decision.get("next_action_sequence") or []
        sequence_text = "; ".join(
            f"{entry.get('group')}: {entry.get('action')}"
            for entry in sequence
        )
        lines.extend(
            [
                f"### {decision.get('sku')}",
                f"- Decision: {decision.get('decision')}",
                f"- Reason Code: {decision.get('reason_code')}",
                f"- Primary Reason Code: {decision.get('primary_reason_code') or decision.get('reason_code')}",
                f"- Secondary Blockers: {secondary_text if secondary_text else 'none'}",
                f"- Next Best Action Group: {decision.get('next_best_action_group') or ''}",
                f"- Next Action Sequence: {sequence_text if sequence_text else 'none'}",
                f"- Status: {decision.get('status') or ''}",
                f"- Photo Hosting State: {decision.get('photo_hosting_state') or ''}",
                f"- Condition ID Valid: {decision.get('condition_id_valid')}",
                f"- Category Policy Cached/Known: {decision.get('category_policy_cached')} / {decision.get('category_policy_known')}",
                f"- Needs Review/Status Blocker: {decision.get('needs_review_status_blocker')}",
                f"- Message: {decision.get('message')}",
                f"- Next Action: {decision.get('next_action')}",
                f"- Planned Action: {decision.get('planned_action')}",
                f"- Category/Condition: {decision.get('category_id') or ''} / {decision.get('condition_id') or ''}",
                f"- Inventory Enum: {decision.get('inventory_condition_enum') or ''}",
                f"- Offer/Listing: {decision.get('offer_id') or ''} / {decision.get('listing_id') or ''}",
                f"- Repair Plan: {decision.get('repair_plan_id') or ''}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def persist_bulk_publish_preview_report(markdown: str) -> str:
    reports_dir = _bulk_preview_reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"{PERSISTENT_REPORT_BASENAME}_{timestamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path)


def _normalize_skus(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip().upper()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_statuses(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip().lower()
        if text in ALLOWED_STATUS_FILTERS and text not in normalized:
            normalized.append(text)
    return normalized


def _classify_preflight_blockers(
    blockers: list[str],
    *,
    compatibility: dict,
    diagnostics: dict,
    photo_hosting_state: str,
) -> tuple[str, str, str, str]:
    lower_blockers = [str(blocker).lower() for blocker in blockers]
    if any("not a clean numeric ebay condition id" in blocker or "condition id is missing" in blocker for blocker in lower_blockers):
        return (
            "invalid_condition_id_format",
            DECISION_REVIEW,
            "Condition ID is missing or malformed.",
            "Normalize condition_id to a valid eBay numeric condition ID before publishing.",
        )
    if any("condition id" in blocker and "not allowed" in blocker for blocker in lower_blockers):
        return (
            "invalid_category_condition",
            DECISION_REPAIR,
            "Category and condition are not compatible for publish.",
            "Repair the category/condition pairing before publishing.",
        )
    if any("aspect" in blocker and ("exceed" in blocker or "invalid" in blocker) for blocker in lower_blockers):
        return (
            "invalid_aspect_value",
            DECISION_REVIEW,
            "One or more aspect values are invalid for publish.",
            "Repair invalid or overlong aspect values before publishing.",
        )
    missing_required_aspect_blockers = [
        blocker for blocker in lower_blockers if "missing required category aspects" in blocker
    ]
    if missing_required_aspect_blockers:
        missing_condition = any("condition" in blocker for blocker in missing_required_aspect_blockers)
        if missing_condition:
            return (
                "missing_required_condition_data",
                DECISION_REVIEW,
                "Required condition data is missing.",
                "Add the missing condition data before publishing.",
            )
        return (
            "missing_required_aspects",
            DECISION_REVIEW,
            "Required category aspects are still missing.",
            "Fill the missing required category aspects before publishing.",
        )
    if photo_hosting_state in {"missing", "local_only", "invalid_public_url", "missing_local_files"}:
        return (
            "photo_hosting_required",
            DECISION_SKIP,
            "Item photos are not publish-safe yet.",
            "Host or correct public photo URLs before publishing.",
        )
    if any("seller policy" in blocker for blocker in lower_blockers):
        return (
            "seller_policy_missing_or_invalid",
            DECISION_REVIEW,
            "Seller policy configuration is incomplete or invalid.",
            "Configure valid seller policies before publishing.",
        )
    if any("status" in blocker and "not publishable" in blocker for blocker in lower_blockers):
        return (
            "status_not_publishable",
            DECISION_SKIP,
            "Item status is not publishable.",
            "Move the item to approved or export_ready before publishing.",
        )
    return (
        "publish_readiness_blocked",
        DECISION_SKIP,
        str(diagnostics.get("publish_block_summary") or "Local publish readiness or compatibility checks still block publish."),
        str(diagnostics.get("recommended_next_action") or "Resolve the remaining blockers before publishing."),
    )


def _decision_has_blocker(decision: dict, code: str) -> bool:
    return (
        str(decision.get("reason_code") or "") == code
        or any(str(entry.get("code") or "") == code for entry in decision.get("secondary_blockers") or [])
        or any(str(entry.get("code") or "") == code for entry in decision.get("blocker_entries") or [])
    )


def _actionable_labels(decision: dict) -> list[str]:
    labels: list[str] = []
    if _decision_has_blocker(decision, "invalid_condition_id_format"):
        labels.append("Fix invalid condition ID")
    if _decision_has_blocker(decision, "missing_required_condition_data") or _missing_required_condition_data(decision):
        labels.append("Missing required condition data")
    if _decision_has_blocker(decision, "invalid_aspect_value"):
        labels.append("Fix invalid aspects")
    if _decision_has_blocker(decision, "existing_unpublished_offer") or _decision_has_blocker(decision, "blocked_by_repair_queue"):
        labels.append("Resolve existing-offer / repair queue blockers")
    if _decision_has_blocker(decision, "photo_hosting_required"):
        labels.append("Host photos")
    if str(decision.get("photo_hosting_state") or "") in {"missing", "missing_local_files", "invalid_public_url"}:
        labels.append("Add missing photos / run image review")
    if _decision_has_blocker(decision, "status_not_publishable") or bool(decision.get("needs_review_status_blocker")):
        labels.append("Move out of needs_review / approve item")
    if _decision_has_blocker(decision, "missing_required_fields") or _decision_has_blocker(decision, "missing_required_aspects"):
        labels.append("Fill required fields")
    if _decision_has_blocker(decision, "category_condition_policy_unknown"):
        labels.append("Fetch/confirm category condition policy")
    if _manual_review_needed(decision):
        labels.append("Manual high-value/authenticity review")
    if not labels and str(decision.get("decision") or "") != DECISION_WOULD_PUBLISH:
        labels.append("Publish-readiness blocked")
    return labels or ["Would publish"]


def _manual_review_needed(decision: dict) -> bool:
    text = " ".join(
        [
            str(decision.get("message") or ""),
            str(decision.get("next_action") or ""),
            str(decision.get("details") or ""),
        ]
    ).lower()
    return "authentic" in text or "high-value" in text or "high value" in text


def _action_text_for_group(label: str, decision: dict) -> str:
    if label == "Fix invalid condition ID":
        return "Normalize condition_id to a valid eBay numeric condition ID before publishing."
    if label == "Missing required condition data":
        return "Add the missing condition data before publishing."
    if label == "Fix invalid aspects":
        return "Repair invalid or overlong item specifics before publishing."
    if label == "Resolve existing-offer / repair queue blockers":
        if bool(decision.get("blocked_by_repair_queue")):
            return "Resolve or supersede the active repair plan before publishing."
        return "Confirm the existing unpublished offer is current with read-only diagnostics before publishing."
    if label == "Fill required fields":
        return "Populate missing required publish fields or category specifics."
    if label == "Fetch/confirm category condition policy":
        return "Fetch or confirm category-specific condition compatibility before publishing."
    if label == "Host photos":
        return "Host or correct public photo URLs before publishing."
    if label == "Add missing photos / run image review":
        return "Attach or review item photos before publishing."
    if label == "Move out of needs_review / approve item":
        return "Move the item to approved or export_ready after higher-priority blockers are resolved."
    if label == "Manual high-value/authenticity review":
        return "Complete manual authenticity or high-value review before publishing."
    if label == "Would publish":
        return "Run publish dry-run again before any live publish attempt."
    return str(decision.get("next_action") or "Resolve the remaining publish blockers.")


def _missing_required_condition_data(decision: dict) -> bool:
    if not _decision_has_blocker(decision, "missing_required_fields"):
        return False
    payloads = [decision.get("details") or {}]
    payloads.extend(
        entry.get("context") or {}
        for entry in decision.get("secondary_blockers") or []
        if isinstance(entry, dict)
    )
    text = json.dumps(payloads).lower()
    return "condition" in text


def _next_action_from_repair_blocker(repair_blocker: dict) -> str:
    suggested = list(repair_blocker.get("suggested_actions") or [])
    if suggested:
        return str(suggested[0] or "")
    code = str(repair_blocker.get("classified_error_code") or "")
    if code == "requires_publish_decision_after_refresh":
        return "Review the stale-offer publish decision flow before publishing."
    return "Resolve or supersede the active repair plan before publishing."


def _photo_hosting_state(item, compatibility: dict) -> str:
    paths = [str(path).strip() for path in (item.image_paths or []) if str(path).strip()]
    hosted = extract_public_image_urls(paths)
    malformed_public = []
    for path in paths:
        if looks_like_public_image_url_candidate(path):
            normalized = normalize_public_image_url(path)
            if not is_valid_public_image_url(normalized):
                malformed_public.append(path)
    local_paths = [path for path in paths if not looks_like_public_image_url_candidate(path)]
    missing_local = [path for path in local_paths if not Path(path).is_file()]
    existing_local = [path for path in local_paths if Path(path).is_file()]
    if malformed_public:
        return "invalid_public_url"
    if hosted and existing_local:
        return "mixed"
    if hosted:
        return "hosted_public"
    if existing_local:
        return "local_only"
    if missing_local:
        return "missing_local_files"
    if not paths:
        return "missing"
    if compatibility.get("ready") is False:
        return "missing"
    return "unknown"


def _bulk_preview_reports_dir() -> Path:
    settings = get_settings()
    return settings.db_path.parent / "diagnostics" / "reports"
