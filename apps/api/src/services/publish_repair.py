from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from apps.api.src.services.publish_compatibility import (
    evaluate_publish_compatibility,
    get_category_condition_policy,
)
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from packages.core.src.config import get_settings
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_decision_record import PublishRepairDecisionRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.ebay.src.condition_mapping import CONDITION_ID_TO_ENUM, normalize_inventory_enum
from packages.ebay.src.inventory_client import CATEGORY_MAP
from packages.ebay.src.public_image_urls import (
    extract_public_image_urls,
    looks_like_public_image_url_candidate,
    normalize_public_image_url,
)
from packages.logging.src.audit_log import AuditLog


ACTIVE_REPAIR_STATUSES = {
    "open",
    "draft_fix_available",
    "needs_manual_review",
    "fixed_pending_recheck",
    "ready_to_retry",
}

CONDITION_ID_LABELS = {
    "1000": "New",
    "1500": "New other",
    "1750": "New with defects",
    "2000": "Certified Refurbished",
    "2010": "Excellent - Refurbished",
    "2020": "Very Good - Refurbished",
    "2030": "Good - Refurbished",
    "2500": "Seller Refurbished",
    "2750": "Like New",
    "2990": "Pre-owned - Excellent",
    "3000": "Used",
    "3010": "Pre-owned - Fair",
    "4000": "Very Good",
    "5000": "Good",
    "6000": "Acceptable",
    "7000": "For parts or not working",
}

CONDITION_LABEL_FALLBACKS = {
    "NEW": ["1000", "1500"],
    "NEW_OTHER": ["1500", "1000"],
    "NEW_WITH_DEFECTS": ["1750", "1500"],
    "CERTIFIED_REFURBISHED": ["2000"],
    "EXCELLENT_REFURBISHED": ["2010"],
    "VERY_GOOD_REFURBISHED": ["2020"],
    "GOOD_REFURBISHED": ["2030"],
    "SELLER_REFURBISHED": ["2500"],
    "USED_EXCELLENT": ["3000"],
    "USED_VERY_GOOD": ["4000", "3000"],
    "LIKE_NEW": ["2750"],
    "PRE_OWNED_EXCELLENT": ["2990", "3000"],
    "PRE_OWNED_FAIR": ["3010"],
    "VERY_GOOD": ["4000", "3000"],
    "USED_GOOD": ["5000", "4000"],
    "USED_ACCEPTABLE": ["6000", "5000"],
    "FOR_PARTS_OR_NOT_WORKING": ["7000"],
}


@dataclass
class RepairSuggestionProvider:
    source: str = "deterministic_provider"

    def draft(self, item: Item, plan: PublishRepairPlanRecord) -> dict | None:
        raise NotImplementedError


class DeterministicRepairSuggestionProvider(RepairSuggestionProvider):
    source = "deterministic_provider"

    def draft(self, item: Item, plan: PublishRepairPlanRecord) -> dict | None:
        current_value = _loads(plan.current_value_json, {})
        expected_value = _loads(plan.expected_value_json, {})
        classified_error_code = str(plan.classified_error_code or "")

        if classified_error_code == "invalid_image_url":
            normalized_urls = []
            for value in item.image_paths or []:
                text = str(value).strip()
                if looks_like_public_image_url_candidate(text):
                    normalized = normalize_public_image_url(text)
                    if normalized not in normalized_urls:
                        normalized_urls.append(normalized)
            if normalized_urls:
                return {
                    "affected_field": "image_paths",
                    "suggested_value": normalized_urls,
                    "suggested_actions": [
                        "Normalize hosted public image URLs to https://... form with forward slashes.",
                        "Keep local file paths stored locally, but exclude them from eBay imageUrls.",
                    ],
                    "confidence": "high",
                    "safe_to_auto_apply": True,
                    "risk_level": "low",
                }

        if classified_error_code == "offer_already_exists":
            recovered_offer_id = str(expected_value.get("offer_id") or "").strip()
            if recovered_offer_id:
                return {
                    "affected_field": "offer_id",
                    "suggested_value": recovered_offer_id,
                    "suggested_actions": [
                        "Persist the recovered offer ID locally.",
                        "Recheck publish readiness after offer recovery.",
                    ],
                    "confidence": "high",
                    "safe_to_auto_apply": True,
                    "risk_level": "low",
                }

        if classified_error_code == "already_published":
            return {
                "affected_field": "listing_sync",
                "suggested_value": None,
                "suggested_actions": [
                    "Run constrained listing sync for this SKU to recover listing identifiers.",
                ],
                "confidence": "high",
                "safe_to_auto_apply": False,
                "risk_level": "low",
            }

        if classified_error_code == "invalid_category_condition":
            allowed_condition_ids = expected_value.get("allowed_condition_ids") or []
            if expected_value.get("local_policy_status") == "suspect_or_stale":
                suggested_value = _empty_condition_suggestion_payload(item) | {
                    "rejected_by_live_validation": {
                        "condition_id": current_value.get("condition_id") or item.condition_id or "",
                        "inventory_condition_enum": current_value.get("inventory_condition_enum")
                        or expected_value.get("rejected_inventory_condition_enum")
                        or _infer_internal_condition_key(item),
                    }
                }
            else:
                suggested_value = _condition_suggestion_payload(item, allowed_condition_ids)
            return {
                "affected_field": "condition_id",
                "suggested_value": suggested_value,
                "suggested_actions": _condition_suggested_actions(
                    item,
                    expected_value,
                    suggested_value,
                ),
                "confidence": "medium" if allowed_condition_ids else "low",
                "safe_to_auto_apply": False,
                "risk_level": "high",
            }

        if classified_error_code in {"missing_required_aspect", "invalid_aspect_value"}:
            return {
                "affected_field": str(plan.affected_field or "item_specifics"),
                "suggested_value": expected_value.get("allowed_values") or current_value,
                "suggested_actions": _loads(plan.suggested_actions_json, []),
                "confidence": "medium",
                "safe_to_auto_apply": False,
                "risk_level": str(plan.risk_level or "medium"),
            }

        return None


def get_repair_queue(
    session: Session,
    *,
    status: str = "",
    risk_level: str = "",
    repair_layer: str = "",
    sku: str = "",
    requires_review: str = "",
) -> list[dict]:
    stmt = select(PublishRepairPlanRecord)
    plans = session.exec(stmt).all()

    filtered = []
    for plan in plans:
        if status and str(plan.status) != status:
            continue
        if not status and str(plan.status) not in ACTIVE_REPAIR_STATUSES:
            continue
        if risk_level and str(plan.risk_level) != risk_level:
            continue
        if repair_layer and str(plan.repair_layer) != repair_layer:
            continue
        if sku and str(plan.sku).upper() != sku.upper():
            continue
        if requires_review:
            expected = requires_review.lower() == "true"
            if bool(plan.requires_review) != expected:
                continue
        filtered.append(plan)

    filtered.sort(key=lambda p: (p.updated_at, p.created_at), reverse=True)
    return [_queue_summary(session, plan) for plan in filtered]


def get_repair_queue_detail(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    latest_attempt = session.exec(
        select(PublishAttemptRecord)
        .where(PublishAttemptRecord.sku == normalized)
        .order_by(PublishAttemptRecord.attempted_at.desc())
    ).first()
    plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
    ).all()
    decisions = session.exec(
        select(PublishRepairDecisionRecord)
        .where(PublishRepairDecisionRecord.sku == normalized)
        .order_by(PublishRepairDecisionRecord.created_at.desc())
    ).all()

    readiness = evaluate_publish_readiness(item).as_dict() if item else None
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True) if item else None
    ready_to_retry = bool(
        item
        and readiness
        and compatibility
        and readiness["ready"]
        and compatibility["ready"]
        and not any(str(plan.status) in ACTIVE_REPAIR_STATUSES - {"ready_to_retry", "resolved", "ignored"} for plan in plans)
    )
    if ready_to_retry and item and _has_live_condition_policy_conflict(session, item):
        ready_to_retry = False

    repair_status = summarize_repair_status(session, normalized)
    repair_blocker = get_publish_repair_blocker(session, normalized)
    if ready_to_retry and plans:
        for plan in plans:
            if plan.status not in {"resolved", "ignored"}:
                plan.status = "ready_to_retry"
                plan.retry_allowed = True
                plan.updated_at = datetime.utcnow()
                session.add(plan)
        session.commit()

    return {
        "sku": normalized,
        "latest_publish_attempt": _serialize_attempt(latest_attempt),
        "repair_plans": [_serialize_plan(plan, repair_blocker=repair_blocker) for plan in plans],
        "repair_decisions": [_serialize_decision(decision) for decision in decisions],
        "readiness_summary": readiness,
        "compatibility_summary": compatibility,
        "repair_status": repair_status,
        "ready_to_retry": ready_to_retry,
    }


def summarize_repair_status(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    latest_attempt = session.exec(
        select(PublishAttemptRecord)
        .where(PublishAttemptRecord.sku == normalized)
        .order_by(PublishAttemptRecord.attempted_at.desc())
    ).first()
    plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
    ).all()
    latest_plan = next((plan for plan in plans if str(plan.status or "") in ACTIVE_REPAIR_STATUSES), None)
    if not latest_plan:
        return {
            "has_open_repair": False,
            "status": "none",
            "last_error_code": "",
            "repair_layer": "",
            "risk_level": "",
            "suggested_fixes": [],
            "ready_to_retry": False,
            "latest_blocking_plan_id": "",
            "blocked_by_repair_queue": False,
        }
    repair_blocker = get_publish_repair_blocker(session, normalized)
    return {
        "has_open_repair": str(latest_plan.status) in ACTIVE_REPAIR_STATUSES,
        "status": str(latest_plan.status),
        "last_error_code": str(latest_plan.classified_error_code or (latest_attempt.classified_error_code if latest_attempt else "")),
        "repair_layer": str(latest_plan.repair_layer or ""),
        "risk_level": str(latest_plan.risk_level or ""),
        "suggested_fixes": _loads(latest_plan.suggested_actions_json, []),
        "ready_to_retry": bool(latest_plan.retry_allowed) and not repair_blocker["blocked_by_repair_queue"],
        "latest_blocking_plan_id": repair_blocker["repair_plan_id"] if repair_blocker["blocked_by_repair_queue"] else "",
        "blocked_by_repair_queue": bool(repair_blocker["blocked_by_repair_queue"]),
    }


def get_publish_repair_blocker(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    latest_attempt = session.exec(
        select(PublishAttemptRecord)
        .where(PublishAttemptRecord.sku == normalized)
        .order_by(PublishAttemptRecord.attempted_at.desc())
    ).first()
    plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
    ).all()
    latest_plan = plans[0] if plans else None
    latest_active_plan = next((plan for plan in plans if str(plan.status or "") in ACTIVE_REPAIR_STATUSES), None)

    empty_status = {
        "has_open_repair": False,
        "status": "none",
        "last_error_code": "",
        "repair_layer": "",
        "risk_level": "",
        "suggested_fixes": [],
        "ready_to_retry": False,
    }
    if latest_plan is None:
        return {
            "sku": normalized,
            "blocked_by_repair_queue": False,
            "repair_plan_id": "",
            "latest_publish_attempt_id": latest_attempt.id if latest_attempt else "",
            "repair_status": empty_status,
            "status": "none",
            "retry_allowed": True,
            "requires_review": False,
            "classified_error_code": "",
            "suggested_actions": [],
            "reason": "",
            "policy_conflict": False,
            "condition_diagnostics": {},
        }

    evaluation_plan = latest_active_plan or latest_plan
    status = str(evaluation_plan.status or "")
    is_active = status in ACTIVE_REPAIR_STATUSES
    decisions = session.exec(
        select(PublishRepairDecisionRecord)
        .where(PublishRepairDecisionRecord.repair_plan_id == evaluation_plan.id)
        .order_by(PublishRepairDecisionRecord.created_at.desc())
    ).all()
    has_approval = bool(decisions)
    requires_unapproved_review = bool(evaluation_plan.requires_review) and status not in {"resolved", "ignored"} and not has_approval
    blocked = bool(
        is_active
        and (
            status == "needs_manual_review"
            or not bool(evaluation_plan.retry_allowed)
            or requires_unapproved_review
        )
    )

    expected_value = _loads(evaluation_plan.expected_value_json, {})
    current_value = _loads(evaluation_plan.current_value_json, {})
    raw_error = _loads(latest_attempt.raw_error_json, {}) if latest_attempt else {}
    raw_details = raw_error.get("details") if isinstance(raw_error, dict) else {}
    if not isinstance(raw_details, dict):
        raw_details = {}
    request_summary = _loads(latest_attempt.request_summary_json, {}) if latest_attempt else {}
    current_category_id = str(item.ebay_category_id or "") if item else ""
    current_condition_id = str(item.condition_id or "") if item else ""
    offer_id = str(
        current_value.get("offer_id")
        or raw_details.get("offer_id")
        or (item.offer_id if item else "")
        or ""
    )
    existing_offer_id_detected = bool(
        offer_id
        and item
        and not str(item.listing_id or "").strip()
        and str(item.status or "") != "listed"
    )
    planned_action = "publish_existing_offer" if existing_offer_id_detected else "create_offer_then_publish"
    category_id = str(
        current_value.get("category_id")
        or raw_details.get("category_id")
        or current_category_id
        or ""
    )
    condition_id = str(
        current_value.get("condition_id")
        or current_value.get("local_condition_id")
        or raw_details.get("local_condition_id")
        or current_condition_id
        or ""
    )
    allowed_condition_ids = [str(value or "").strip() for value in expected_value.get("allowed_condition_ids") or expected_value.get("local_policy_allowed_condition_ids") or []]
    policy_conflict = bool(
        expected_value.get("local_policy_status") == "suspect_or_stale"
        or expected_value.get("policy_conflict")
        or expected_value.get("contradicted_by") == "ebay_error"
        or (
            str(evaluation_plan.classified_error_code or "") == "invalid_category_condition"
            and str(latest_attempt.ebay_error_id if latest_attempt else "") == "25021"
            and condition_id
            and condition_id in allowed_condition_ids
        )
    )

    suggested_actions = _loads(evaluation_plan.suggested_actions_json, [])
    reason = ""
    if blocked:
        if status == "needs_manual_review":
            reason = "Latest repair plan requires manual review before publish can be retried."
        elif not bool(evaluation_plan.retry_allowed):
            reason = "Latest repair plan does not allow retry."
        elif requires_unapproved_review:
            reason = "Latest repair plan requires an approved repair decision before publish can be retried."
        else:
            reason = "Latest repair plan blocks publish retry."

    repair_status = {
        "has_open_repair": is_active,
        "status": status,
        "last_error_code": str(evaluation_plan.classified_error_code or (latest_attempt.classified_error_code if latest_attempt else "")),
        "repair_layer": str(evaluation_plan.repair_layer or ""),
        "risk_level": str(evaluation_plan.risk_level or ""),
        "suggested_fixes": suggested_actions,
        "ready_to_retry": bool(evaluation_plan.retry_allowed) and not blocked,
    }
    return {
        "sku": normalized,
        "blocked_by_repair_queue": blocked,
        "repair_plan_id": evaluation_plan.id,
        "latest_publish_attempt_id": str(evaluation_plan.publish_attempt_id or (latest_attempt.id if latest_attempt else "")),
        "repair_status": repair_status,
        "status": status,
        "retry_allowed": bool(evaluation_plan.retry_allowed) and not blocked,
        "requires_review": bool(evaluation_plan.requires_review),
        "classified_error_code": str(evaluation_plan.classified_error_code or ""),
        "suggested_actions": suggested_actions,
        "reason": reason,
        "policy_conflict": policy_conflict,
        "condition_diagnostics": {
            "condition_id": condition_id,
            "local_condition_id": condition_id,
            "current_condition_id": current_condition_id,
            "current_category_id": current_category_id,
            "previous_condition_id": str(
                raw_details.get("previous_condition_id")
                or request_summary.get("previous_condition_id")
                or current_value.get("previous_condition_id")
                or ""
            ),
            "previous_category_id": str(
                raw_details.get("previous_category_id")
                or request_summary.get("previous_category_id")
                or current_value.get("previous_category_id")
                or ""
            ),
            "inventory_condition_enum": str(
                current_value.get("inventory_condition_enum")
                or raw_details.get("inventory_condition_enum")
                or ""
            ),
            "category_id": category_id,
            "offer_id": offer_id,
            "existing_offer_id_detected": existing_offer_id_detected,
            "planned_action": planned_action,
            "failed_stage": str(current_value.get("stage") or raw_details.get("stage") or (latest_attempt.stage if latest_attempt else "")),
            "stale_existing_offer_hypothesis": bool(existing_offer_id_detected and str(current_value.get("stage") or raw_details.get("stage") or (latest_attempt.stage if latest_attempt else "")) == "publish_offer"),
            "stale_existing_offer_note": (
                "Existing unpublished offer may contain stale category or condition state; diagnose before retrying publish."
                if existing_offer_id_detected and str(current_value.get("stage") or raw_details.get("stage") or (latest_attempt.stage if latest_attempt else "")) == "publish_offer"
                else ""
            ),
            "rejected_condition_id": condition_id if policy_conflict else "",
            "rejected_category_id": category_id if policy_conflict else "",
            "contradicted_by": "ebay_error" if policy_conflict else "",
        },
    }


def record_publish_blocked(
    session: Session,
    item: Item,
    *,
    blockers: list[str],
    readiness: dict,
    compatibility: dict,
) -> dict:
    classification = {
        "classified_error_code": "publish_readiness_blocked",
        "repair_layer": "compatibility",
        "requires_review": True,
        "retry_allowed": False,
        "ebay_error_id": "",
        "ebay_error_message": "Publish was blocked before any eBay mutation because local readiness or compatibility checks failed.",
        "plans": _plans_from_blockers(item, blockers, readiness, compatibility),
    }
    return _record_attempt_and_plans(
        session,
        item,
        stage="preflight",
        status="blocked",
        request_summary={"sku": item.sku, "mode": "single_publish_preflight"},
        raw_error={"blockers": blockers, "readiness": readiness, "compatibility": compatibility},
        classification=classification,
    )


def record_publish_failure(
    session: Session,
    item: Item,
    *,
    result,
    request_summary: dict | None = None,
) -> dict:
    classification = classify_publish_failure(item, result=result)
    stage = str(result.details.get("stage") or _infer_stage_from_error(str(result.error or "")))
    raw_error = {
        "error": str(result.error or ""),
        "error_code": str(result.error_code or ""),
        "body": str(result.details.get("body") or ""),
        "details": dict(result.details or {}),
    }
    return _record_attempt_and_plans(
        session,
        item,
        stage=stage or "unknown",
        status="failed",
        request_summary=request_summary or {"sku": item.sku, "mode": "single_publish"},
        raw_error=raw_error,
        classification=classification,
    )


def classify_publish_failure(item: Item, *, result) -> dict:
    error_text = str(result.error or "")
    body = str(result.details.get("body") or "")
    combined = f"{error_text}\n{body}".lower()
    error_code = str(result.error_code or "")

    if error_code == "INVALID_IMAGE_URL" or "invalid picture url" in combined or "invalid value for imageurl" in combined:
        invalid_urls = result.details.get("invalid_image_urls") or _extract_image_urls_from_text(body)
        return {
            "classified_error_code": "invalid_image_url",
            "repair_layer": "photo_hosting",
            "requires_review": False,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "Hosted image URLs must be public http/https URLs with forward slashes.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="imageUrls",
                    risk_level="low",
                    safe_to_auto_apply=True,
                    requires_review=False,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="photo_hosting",
                    classified_error_code="invalid_image_url",
                    current_value={"image_paths": list(item.image_paths or [])},
                    expected_value={"image_urls_format": "https://public-host/path.jpg"},
                    suggested_value={"normalized_urls": [normalize_public_image_url(u) for u in extract_public_image_urls(item.image_paths or [])]},
                    suggested_actions=[
                        "Normalize hosted image URLs to https://... form with forward slashes.",
                        "Do not send local filesystem paths to eBay imageUrls.",
                    ],
                )
            ],
        }

    if "offer entity already exists" in combined:
        recovered_offer_id = str(result.details.get("offer_id") or "")
        return {
            "classified_error_code": "offer_already_exists",
            "repair_layer": "offer_recovery",
            "requires_review": False,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "eBay already has an unpublished offer for this SKU.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="offer_id",
                    risk_level="low",
                    safe_to_auto_apply=bool(recovered_offer_id),
                    requires_review=False,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="offer_recovery",
                    classified_error_code="offer_already_exists",
                    current_value={"offer_id": item.offer_id or ""},
                    expected_value={"offer_id": recovered_offer_id},
                    suggested_value={"offer_id": recovered_offer_id},
                    suggested_actions=[
                        "Persist the recovered offer ID locally.",
                        "Recheck readiness before manually retrying publish.",
                    ],
                )
            ],
        }

    if "already published" in combined:
        return {
            "classified_error_code": "already_published",
            "repair_layer": "listing_sync",
            "requires_review": False,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "eBay reports the offer is already published.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="listing_sync",
                    risk_level="low",
                    safe_to_auto_apply=False,
                    requires_review=False,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="listing_sync",
                    classified_error_code="already_published",
                    current_value={"listing_id": item.listing_id or "", "offer_id": item.offer_id or ""},
                    expected_value={"next_action": "Run constrained listing sync for the SKU."},
                    suggested_value={},
                    suggested_actions=["Run constrained listing sync for the SKU to recover listing identifiers."],
                )
            ],
        }

    if "25021" in combined or "invalid item condition information" in combined or "invalid categoryid or condition" in combined:
        policy = get_category_condition_policy(str(item.ebay_category_id or ""))
        if _local_policy_is_contradicted_by_live_error(item, policy):
            return _classify_live_condition_policy_conflict(item, policy, result=result)
        return {
            "classified_error_code": "invalid_category_condition",
            "repair_layer": "category_compatibility",
            "requires_review": True,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body) or "25021",
            "ebay_error_message": "The selected condition ID is invalid for the exact eBay category.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="condition_id",
                    risk_level="high",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="category_compatibility",
                    classified_error_code="invalid_category_condition",
                    current_value={"category_id": item.ebay_category_id or "", "condition_id": item.condition_id or ""},
                    expected_value=policy,
                    suggested_value={"condition_id": ""},
                    suggested_actions=[
                        "Check allowed condition IDs for the exact category.",
                        "Choose a valid condition ID or review whether the category is wrong.",
                    ],
                )
            ],
        }

    if "missing required" in combined and "aspect" in combined:
        aspect_name = _extract_aspect_name(body) or "item_specifics"
        return {
            "classified_error_code": "missing_required_aspect",
            "repair_layer": "category_template",
            "requires_review": True,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "A required category aspect is missing.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field=aspect_name,
                    risk_level="medium",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="category_template",
                    classified_error_code="missing_required_aspect",
                    current_value={"aspect": aspect_name, "value": _current_item_specific_value(item, aspect_name)},
                    expected_value={"aspect": aspect_name, "required": True},
                    suggested_value={},
                    suggested_actions=[f"Provide a valid value for required aspect '{aspect_name}'."],
                )
            ],
        }

    if "invalid aspect" in combined or "invalid value" in combined and "aspect" in combined:
        aspect_name = _extract_aspect_name(body) or "item_specifics"
        return {
            "classified_error_code": "invalid_aspect_value",
            "repair_layer": "category_template",
            "requires_review": True,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "An aspect value is invalid for the selected category.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field=aspect_name,
                    risk_level="medium",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="category_template",
                    classified_error_code="invalid_aspect_value",
                    current_value={"aspect": aspect_name, "value": _current_item_specific_value(item, aspect_name)},
                    expected_value={"aspect": aspect_name},
                    suggested_value={},
                    suggested_actions=[f"Correct the invalid value for aspect '{aspect_name}'."],
                )
            ],
        }

    if "policy" in combined and "invalid" in combined:
        return {
            "classified_error_code": "invalid_seller_policy",
            "repair_layer": "seller_policy",
            "requires_review": True,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "A seller policy ID is invalid or incompatible.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="seller_policy_id",
                    risk_level="high",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="seller_policy",
                    classified_error_code="invalid_seller_policy",
                    current_value={},
                    expected_value={},
                    suggested_value={},
                    suggested_actions=["Review configured seller policy IDs before retrying publish."],
                )
            ],
        }

    if "invalid access token" in combined or "authorization http request header" in combined or error_code == "AUTH_NOT_READY":
        return {
            "classified_error_code": "auth_failure",
            "repair_layer": "auth",
            "requires_review": True,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "eBay auth is not ready for publish.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="auth",
                    risk_level="medium",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="auth",
                    classified_error_code="auth_failure",
                    current_value={},
                    expected_value={},
                    suggested_value={},
                    suggested_actions=["Check eBay auth readiness and reconnect OAuth before retrying publish."],
                )
            ],
        }

    if "rate limit" in combined or "too many requests" in combined or "429" in combined:
        return {
            "classified_error_code": "ebay_rate_limited",
            "repair_layer": "ebay_transient",
            "requires_review": False,
            "retry_allowed": False,
            "ebay_error_id": _extract_ebay_error_id(body),
            "ebay_error_message": "eBay rate limited the request.",
            "plans": [
                _plan_payload(
                    item,
                    affected_field="ebay_transient",
                    risk_level="low",
                    safe_to_auto_apply=False,
                    requires_review=False,
                    retry_allowed=False,
                    source="ebay_error",
                    repair_layer="ebay_transient",
                    classified_error_code="ebay_rate_limited",
                    current_value={},
                    expected_value={"retry": "manual_later"},
                    suggested_value={},
                    suggested_actions=["Wait and retry manually later. Do not auto-loop on transient failures."],
                )
            ],
        }

    return {
        "classified_error_code": "unknown_publish_error",
        "repair_layer": "ebay_unknown",
        "requires_review": True,
        "retry_allowed": False,
        "ebay_error_id": _extract_ebay_error_id(body),
        "ebay_error_message": str(result.error or "Unknown eBay publish failure"),
        "plans": [
            _plan_payload(
                item,
                affected_field="unknown",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="ebay_error",
                repair_layer="ebay_unknown",
                classified_error_code="unknown_publish_error",
                current_value={},
                expected_value={},
                suggested_value={},
                suggested_actions=["Review the raw eBay error and repair the listing manually before retrying."],
            )
        ],
    }


def recheck_repair_readiness(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    if not item:
        return {
            "sku": normalized,
            "status": "missing_item",
            "ready_to_retry": False,
            "blockers": [f"Item {normalized} not found."],
        }

    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)
    ready_to_retry = readiness["ready"] and compatibility["ready"]
    if ready_to_retry and _has_live_condition_policy_conflict(session, item):
        ready_to_retry = False

    plans = session.exec(
        select(PublishRepairPlanRecord).where(PublishRepairPlanRecord.sku == normalized)
    ).all()
    for plan in plans:
        if ready_to_retry:
            plan.status = "ready_to_retry"
            plan.retry_allowed = True
        elif plan.safe_to_auto_apply:
            plan.status = "fixed_pending_recheck"
            plan.retry_allowed = False
        else:
            plan.status = "needs_manual_review"
            plan.retry_allowed = False
        plan.updated_at = datetime.utcnow()
        session.add(plan)
    session.commit()

    AuditLog()._write(
        {
            "event": "publish_repair_recheck",
            "sku": normalized,
            "ready_to_retry": ready_to_retry,
            "blockers": readiness["blockers"] + compatibility["blockers"],
        }
    )

    return {
        "sku": normalized,
        "status": "ready_to_retry" if ready_to_retry else "needs_manual_review",
        "ready_to_retry": ready_to_retry,
        "readiness": readiness,
        "compatibility": compatibility,
    }


def draft_fix_for_sku(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    if not item:
        return {"sku": normalized, "drafts": [], "status": "missing_item"}

    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)
    current_blockers = list(dict.fromkeys(readiness["blockers"] + compatibility["blockers"]))
    generated_plans = _upsert_current_blocker_plans(
        session,
        item,
        readiness=readiness,
        compatibility=compatibility,
    )

    drafted = []
    if generated_plans:
        for plan in generated_plans:
            plan.status = "draft_fix_available"
            plan.updated_at = datetime.utcnow()
            session.add(plan)
            drafted.append(_serialize_plan(plan) | {"confidence": "medium"})
    else:
        provider = DeterministicRepairSuggestionProvider()
        plans = session.exec(
            select(PublishRepairPlanRecord)
            .where(PublishRepairPlanRecord.sku == normalized)
            .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
        ).all()
        for plan in plans:
            suggestion = provider.draft(item, plan)
            if suggestion is None:
                continue
            plan.suggested_value_json = _dumps(suggestion.get("suggested_value"))
            plan.suggested_actions_json = _dumps(suggestion.get("suggested_actions"))
            plan.safe_to_auto_apply = bool(suggestion.get("safe_to_auto_apply"))
            plan.risk_level = str(suggestion.get("risk_level") or plan.risk_level or "medium")
            plan.source = "model_draft" if suggestion.get("source") == "model_draft" else provider.source
            plan.status = "draft_fix_available"
            plan.updated_at = datetime.utcnow()
            session.add(plan)
            drafted.append(_serialize_plan(plan) | {"confidence": suggestion.get("confidence", "medium")})

    session.commit()
    if drafted:
        status = "draft_fix_available"
    elif not current_blockers:
        status = "no_blockers"
    else:
        status = "unresolved_blockers"

    return {
        "sku": normalized,
        "status": status,
        "drafts": drafted,
        "readiness": readiness,
        "compatibility": compatibility,
        "blockers": current_blockers,
    }


def apply_draft_fix(
    session: Session,
    sku: str,
    repair_plan_id: str,
    *,
    approved: bool,
    edited_value: Any = None,
    operator_label: str | None = None,
) -> dict:
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    if not item:
        return {"ok": False, "status_code": 404, "detail": f"Item {normalized} not found"}

    plan = session.get(PublishRepairPlanRecord, repair_plan_id)
    if not plan or str(plan.sku).upper() != normalized:
        return {"ok": False, "status_code": 404, "detail": f"Repair plan {repair_plan_id} not found for {normalized}"}
    repair_blocker = get_publish_repair_blocker(session, normalized)
    if (
        repair_blocker["blocked_by_repair_queue"]
        and repair_blocker["repair_plan_id"]
        and repair_blocker["repair_plan_id"] != plan.id
        and str(plan.status or "") in ACTIVE_REPAIR_STATUSES
    ):
        return {
            "ok": False,
            "status_code": 409,
            "detail": "Repair plan is superseded by a newer blocking repair plan and is not actionable.",
            "superseded_by_repair_plan_id": repair_blocker["repair_plan_id"],
            "latest_publish_attempt_id": repair_blocker["latest_publish_attempt_id"],
            "classified_error_code": repair_blocker["classified_error_code"],
        }
    if not approved:
        return {"ok": False, "status_code": 400, "detail": "Repair approval is required before applying a draft fix."}
    if str(plan.risk_level) == "high" and edited_value is None:
        return {"ok": False, "status_code": 400, "detail": "High-risk repair plans require an explicit approved value before apply."}

    before_value = _field_snapshot(item, str(plan.affected_field or ""))
    new_value = edited_value if edited_value is not None else _loads(plan.suggested_value_json, None)

    apply_result = _apply_plan_to_item(item, plan, new_value)
    if not apply_result["ok"]:
        return apply_result

    ItemRepository(session).upsert(item)
    decision = PublishRepairDecisionRecord(
        sku=normalized,
        repair_plan_id=plan.id,
        action="apply" if edited_value is None else "edit_before_apply",
        before_value_json=_dumps(before_value),
        after_value_json=_dumps(apply_result["after_value"]),
        operator_label=operator_label,
        approved_at=datetime.utcnow(),
    )
    session.add(decision)
    plan.status = "fixed_pending_recheck"
    plan.updated_at = datetime.utcnow()
    session.add(plan)
    session.commit()

    AuditLog()._write(
        {
            "event": "publish_repair_applied",
            "sku": normalized,
            "repair_plan_id": plan.id,
            "risk_level": plan.risk_level,
            "affected_field": plan.affected_field,
        }
    )

    recheck = recheck_repair_readiness(session, normalized)
    return {
        "ok": True,
        "sku": normalized,
        "repair_plan_id": plan.id,
        "before_value": before_value,
        "after_value": apply_result["after_value"],
        "recheck": recheck,
    }


def bulk_draft_fixes(
    session: Session,
    *,
    skus: list[str],
    allow_low_risk_auto_apply: bool,
    allow_medium_risk_drafts: bool,
    allow_high_risk_drafts: bool,
) -> dict:
    safe_low_risk_fixes = []
    medium_risk_review_fixes = []
    high_risk_manual_review_fixes = []
    unresolved_errors = []
    skipped = []

    for sku in [str(s or "").strip().upper() for s in skus if str(s or "").strip()]:
        detail = draft_fix_for_sku(session, sku)
        drafts = detail.get("drafts", [])
        if not drafts:
            status = str(detail.get("status") or "no_draft_available")
            if status == "unresolved_blockers":
                unresolved_errors.append(
                    {
                        "sku": sku,
                        "reason": "unresolved_blockers",
                        "blockers": detail.get("blockers", []),
                    }
                )
            else:
                skipped.append({"sku": sku, "reason": status})
            continue
        for draft in drafts:
            entry = {
                "sku": sku,
                "repair_plan_id": draft["id"],
                "risk_level": draft["risk_level"],
                "affected_field": draft["affected_field"],
                "before": draft["current_value"],
                "after": draft["suggested_value"],
                "suggested_actions": draft["suggested_actions"],
                "source": draft["source"],
                "safe_to_auto_apply": draft["safe_to_auto_apply"],
            }
            if draft["risk_level"] == "low":
                safe_low_risk_fixes.append(entry)
                if allow_low_risk_auto_apply and draft["safe_to_auto_apply"]:
                    apply_draft_fix(session, sku, draft["id"], approved=True, operator_label="bulk_auto_low_risk")
            elif draft["risk_level"] == "medium":
                if allow_medium_risk_drafts:
                    medium_risk_review_fixes.append(entry)
                else:
                    unresolved_errors.append(entry | {"reason": "medium_risk_drafts_disabled"})
            elif draft["risk_level"] == "high":
                if allow_high_risk_drafts:
                    high_risk_manual_review_fixes.append(entry)
                else:
                    unresolved_errors.append(entry | {"reason": "high_risk_drafts_disabled"})
            else:
                unresolved_errors.append(entry | {"reason": "unknown_risk"})

    return {
        "safe_low_risk_fixes": safe_low_risk_fixes,
        "medium_risk_review_fixes": medium_risk_review_fixes,
        "high_risk_manual_review_fixes": high_risk_manual_review_fixes,
        "unresolved_errors": unresolved_errors,
        "skipped": skipped,
    }


def bulk_apply_approved_fixes(session: Session, approvals: list[dict]) -> dict:
    applied = []
    rejected = []
    for approval in approvals:
        sku = str(approval.get("sku") or "").strip().upper()
        plan_id = str(approval.get("repair_plan_id") or "").strip()
        approved = bool(approval.get("approved"))
        edited_value = approval.get("edited_value")
        result = apply_draft_fix(
            session,
            sku,
            plan_id,
            approved=approved,
            edited_value=edited_value,
            operator_label=str(approval.get("operator_label") or "bulk_apply"),
        )
        if result.get("ok"):
            applied.append(result)
        else:
            rejected.append({"sku": sku, "repair_plan_id": plan_id, "detail": result.get("detail")})
    return {"applied": applied, "rejected": rejected}


def _record_attempt_and_plans(
    session: Session,
    item: Item,
    *,
    stage: str,
    status: str,
    request_summary: dict,
    raw_error: dict,
    classification: dict,
) -> dict:
    settings = get_settings()
    attempt = PublishAttemptRecord(
        sku=str(item.sku or "").upper(),
        stage=stage,
        status=status,
        ebay_environment=str(settings.ebay_environment or ""),
        marketplace_id=str(settings.ebay_marketplace_id or ""),
        request_summary_json=_dumps(request_summary),
        raw_error_json=_dumps(raw_error),
        ebay_error_id=str(classification.get("ebay_error_id") or ""),
        ebay_error_message=str(classification.get("ebay_error_message") or ""),
        classified_error_code=str(classification.get("classified_error_code") or ""),
        repair_layer=str(classification.get("repair_layer") or ""),
        requires_review=bool(classification.get("requires_review", True)),
        retry_allowed=bool(classification.get("retry_allowed", False)),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    created_plans = []
    for plan_payload in classification.get("plans", []):
        plan = PublishRepairPlanRecord(
            sku=str(item.sku or "").upper(),
            publish_attempt_id=attempt.id,
            status="needs_manual_review" if plan_payload["requires_review"] else "open",
            affected_field=str(plan_payload.get("affected_field") or ""),
            current_value_json=_dumps(plan_payload.get("current_value")),
            expected_value_json=_dumps(plan_payload.get("expected_value")),
            suggested_value_json=_dumps(plan_payload.get("suggested_value")),
            suggested_actions_json=_dumps(plan_payload.get("suggested_actions")),
            risk_level=str(plan_payload.get("risk_level") or "medium"),
            safe_to_auto_apply=bool(plan_payload.get("safe_to_auto_apply")),
            requires_review=bool(plan_payload.get("requires_review", True)),
            retry_allowed=bool(plan_payload.get("retry_allowed", False)),
            source=str(plan_payload.get("source") or "ebay_error"),
            repair_layer=str(plan_payload.get("repair_layer") or classification.get("repair_layer") or ""),
            classified_error_code=str(plan_payload.get("classified_error_code") or classification.get("classified_error_code") or ""),
        )
        session.add(plan)
        created_plans.append(plan)

    session.commit()
    AuditLog()._write(
        {
            "event": "publish_repair_created",
            "sku": item.sku,
            "attempt_id": attempt.id,
            "classified_error_code": classification.get("classified_error_code"),
            "repair_layer": classification.get("repair_layer"),
            "plan_count": len(created_plans),
        }
    )
    return {
        "attempt": _serialize_attempt(attempt),
        "repair_plan": [_serialize_plan(plan) for plan in created_plans],
        "repair_queue_entry_created": True,
        "classified_error": {
            "code": classification.get("classified_error_code"),
            "repair_layer": classification.get("repair_layer"),
            "requires_review": classification.get("requires_review"),
            "retry_allowed": classification.get("retry_allowed"),
            "message": classification.get("ebay_error_message"),
        },
    }


def _plans_from_blockers(item: Item, blockers: list[str], readiness: dict, compatibility: dict) -> list[dict]:
    plans = []
    for blocker in blockers:
        lower = blocker.lower()
        if "condition id" in lower and "category" in lower:
            policy = get_category_condition_policy(str(item.ebay_category_id or ""))
            plans.append(
                _plan_payload(
                    item,
                    affected_field="condition_id",
                    risk_level="high",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="cached_category_policy",
                    repair_layer="category_compatibility",
                    classified_error_code="invalid_category_condition",
                    current_value={"category_id": item.ebay_category_id or "", "condition_id": item.condition_id or ""},
                    expected_value=policy,
                    suggested_value={},
                    suggested_actions=["Choose an allowed category-specific condition ID before retrying publish."],
                )
            )
        elif "image url" in lower or "hosted image" in lower or "photo" in lower:
            plans.append(
                _plan_payload(
                    item,
                    affected_field="imageUrls",
                    risk_level="low",
                    safe_to_auto_apply=True,
                    requires_review=False,
                    retry_allowed=False,
                    source="cached_template",
                    repair_layer="photo_hosting",
                    classified_error_code="invalid_image_url",
                    current_value={"image_paths": list(item.image_paths or [])},
                    expected_value={"image_urls_format": "https://public-host/path.jpg"},
                    suggested_value={"normalized_urls": [normalize_public_image_url(u) for u in extract_public_image_urls(item.image_paths or [])]},
                    suggested_actions=["Normalize hosted image URLs and ensure only public URLs are sent to eBay."],
                )
            )
        elif "required category aspects" in lower or "required aspect" in lower:
            plans.append(
                _plan_payload(
                    item,
                    affected_field="item_specifics",
                    risk_level="medium",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="cached_template",
                    repair_layer="category_template",
                    classified_error_code="missing_required_aspect",
                    current_value={"missing_required": compatibility["checks"]},
                    expected_value={},
                    suggested_value={},
                    suggested_actions=["Fill the missing required category-specific aspects before retrying publish."],
                )
            )

    if not plans:
        plans.append(
            _plan_payload(
                item,
                affected_field="publish_readiness",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="operator_manual",
                repair_layer="compatibility",
                classified_error_code="publish_readiness_blocked",
                current_value={"readiness": readiness, "compatibility": compatibility},
                expected_value={},
                suggested_value={},
                suggested_actions=["Resolve the remaining readiness blockers before retrying publish."],
            )
        )
    return plans


def _plans_from_current_state(item: Item, readiness: dict, compatibility: dict) -> list[dict]:
    plans: list[dict] = []
    compatibility_checks = {
        str(check.get("name") or ""): check for check in compatibility.get("checks", [])
    }
    readiness_checks = {
        str(check.get("name") or ""): check for check in readiness.get("checks", [])
    }

    condition_check = compatibility_checks.get("category_condition_policy")
    if condition_check and not condition_check.get("ok"):
        context = dict(condition_check.get("context") or {})
        allowed_condition_ids = [str(value) for value in context.get("allowed_condition_ids") or []]
        suggested_value = _condition_suggestion_payload(item, allowed_condition_ids)
        plans.append(
            _plan_payload(
                item,
                affected_field="condition_id",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="readiness_category_policy",
                repair_layer="category_compatibility",
                classified_error_code="invalid_category_condition",
                current_value={
                    "category_id": context.get("category_id") or item.ebay_category_id or "",
                    "condition_id": context.get("condition_id") or item.condition_id or "",
                },
                expected_value={
                    "category_id": context.get("category_id") or item.ebay_category_id or "",
                    "allowed_condition_ids": allowed_condition_ids,
                    "allowed_condition_options": _condition_options(allowed_condition_ids),
                    "policy_source": str(context.get("source") or ""),
                    "internal_condition_key": _infer_internal_condition_key(item),
                },
                suggested_value=suggested_value,
                suggested_actions=_condition_suggested_actions(item, context, suggested_value),
            )
        )

    public_image_check = compatibility_checks.get("public_image_urls")
    if public_image_check and not public_image_check.get("ok") and public_image_check.get("blocking"):
        context = dict(public_image_check.get("context") or {})
        malformed_candidates = list(context.get("malformed_public_candidates") or [])
        if malformed_candidates:
            plans.append(
                _plan_payload(
                    item,
                    affected_field="image_paths",
                    risk_level="low",
                    safe_to_auto_apply=True,
                    requires_review=False,
                    retry_allowed=False,
                    source="readiness_image_validation",
                    repair_layer="photo_hosting",
                    classified_error_code="invalid_image_url",
                    current_value={"image_paths": list(item.image_paths or [])},
                    expected_value={"image_urls_format": "https://public-host/path.jpg"},
                    suggested_value={
                        "normalized_urls": [normalize_public_image_url(u) for u in extract_public_image_urls(item.image_paths or [])]
                    },
                    suggested_actions=[
                        "Repair malformed hosted public image URLs.",
                        "Keep local file paths stored locally, but exclude them from eBay imageUrls.",
                    ],
                )
            )

    missing_required = _missing_required_fields(readiness_checks)
    for field_name in missing_required:
        plan_payload = _plan_for_missing_required_field(item, field_name)
        if plan_payload is not None:
            plans.append(plan_payload)

    category_template_check = readiness_checks.get("category_template_validation")
    if category_template_check and not category_template_check.get("ok"):
        context = dict(category_template_check.get("context") or {})
        for field_name in context.get("missing_required") or []:
            plans.append(
                _plan_payload(
                    item,
                    affected_field="item_specifics",
                    risk_level="high",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="readiness_category_template",
                    repair_layer="category_template",
                    classified_error_code="missing_required_aspect",
                    current_value={"aspect": str(field_name), "value": _current_item_specific_value(item, str(field_name))},
                    expected_value={"aspect": str(field_name), "required": True},
                    suggested_value={"aspect": str(field_name), "allowed_options": []},
                    suggested_actions=[f"Choose a value for required aspect '{field_name}' before retrying publish."],
                )
            )
        for field_name in context.get("invalid_fields") or []:
            plans.append(
                _plan_payload(
                    item,
                    affected_field="item_specifics",
                    risk_level="medium",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="readiness_category_template",
                    repair_layer="category_template",
                    classified_error_code="invalid_aspect_value",
                    current_value={"aspect": str(field_name), "value": _current_item_specific_value(item, str(field_name))},
                    expected_value={"aspect": str(field_name)},
                    suggested_value={"aspect": str(field_name)},
                    suggested_actions=[f"Repair invalid value for aspect '{field_name}' before retrying publish."],
                )
            )

    aspect_check = readiness_checks.get("aspect_value_lengths")
    if aspect_check and not aspect_check.get("ok"):
        for issue in dict(aspect_check.get("context") or {}).get("issues") or []:
            aspect_name = str(issue.get("aspect") or "item_specifics")
            plans.append(
                _plan_payload(
                    item,
                    affected_field="item_specifics",
                    risk_level="high",
                    safe_to_auto_apply=False,
                    requires_review=True,
                    retry_allowed=False,
                    source="readiness_aspect_validation",
                    repair_layer="category_template",
                    classified_error_code="invalid_aspect_value",
                    current_value={"aspect": aspect_name, "value": issue.get("value")},
                    expected_value={"aspect": aspect_name, "max_length": issue.get("max_length")},
                    suggested_value={"aspect": aspect_name},
                    suggested_actions=[f"Shorten or repair aspect '{aspect_name}' before retrying publish."],
                )
            )

    return _dedupe_plan_payloads(plans)


def _upsert_current_blocker_plans(
    session: Session,
    item: Item,
    *,
    readiness: dict,
    compatibility: dict,
) -> list[PublishRepairPlanRecord]:
    payloads = _plans_from_current_state(item, readiness, compatibility)
    if not payloads:
        return []

    existing_plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == str(item.sku or "").upper())
        .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
    ).all()
    updated_plans: list[PublishRepairPlanRecord] = []

    for payload in payloads:
        current_value = payload.get("current_value")
        matching_plan = next(
            (
                plan
                for plan in existing_plans
                if str(plan.affected_field or "") == str(payload.get("affected_field") or "")
                and str(plan.classified_error_code or "") == str(payload.get("classified_error_code") or "")
                and str(plan.repair_layer or "") == str(payload.get("repair_layer") or "")
                and _loads(plan.current_value_json, {}) == current_value
                and str(plan.status or "") in ACTIVE_REPAIR_STATUSES
            ),
            None,
        )

        if matching_plan is None:
            matching_plan = PublishRepairPlanRecord(
                sku=str(item.sku or "").upper(),
                publish_attempt_id=None,
                status="needs_manual_review" if payload["requires_review"] else "open",
                affected_field=str(payload.get("affected_field") or ""),
                current_value_json=_dumps(payload.get("current_value")),
                expected_value_json=_dumps(payload.get("expected_value")),
                suggested_value_json=_dumps(payload.get("suggested_value")),
                suggested_actions_json=_dumps(payload.get("suggested_actions")),
                risk_level=str(payload.get("risk_level") or "medium"),
                safe_to_auto_apply=bool(payload.get("safe_to_auto_apply")),
                requires_review=bool(payload.get("requires_review", True)),
                retry_allowed=bool(payload.get("retry_allowed", False)),
                source=str(payload.get("source") or "readiness_blocker"),
                repair_layer=str(payload.get("repair_layer") or ""),
                classified_error_code=str(payload.get("classified_error_code") or ""),
            )
            session.add(matching_plan)
            existing_plans.append(matching_plan)
        else:
            matching_plan.status = "needs_manual_review" if payload["requires_review"] else "open"
            matching_plan.expected_value_json = _dumps(payload.get("expected_value"))
            matching_plan.suggested_value_json = _dumps(payload.get("suggested_value"))
            matching_plan.suggested_actions_json = _dumps(payload.get("suggested_actions"))
            matching_plan.risk_level = str(payload.get("risk_level") or matching_plan.risk_level or "medium")
            matching_plan.safe_to_auto_apply = bool(payload.get("safe_to_auto_apply"))
            matching_plan.requires_review = bool(payload.get("requires_review", True))
            matching_plan.retry_allowed = bool(payload.get("retry_allowed", False))
            matching_plan.source = str(payload.get("source") or matching_plan.source or "readiness_blocker")
            matching_plan.repair_layer = str(payload.get("repair_layer") or matching_plan.repair_layer or "")
            matching_plan.classified_error_code = str(payload.get("classified_error_code") or matching_plan.classified_error_code or "")

        matching_plan.updated_at = datetime.utcnow()
        updated_plans.append(matching_plan)

    session.commit()
    return updated_plans


def _plan_payload(
    item: Item,
    *,
    affected_field: str,
    risk_level: str,
    safe_to_auto_apply: bool,
    requires_review: bool,
    retry_allowed: bool,
    source: str,
    repair_layer: str,
    classified_error_code: str,
    current_value: Any,
    expected_value: Any,
    suggested_value: Any,
    suggested_actions: list[str],
) -> dict:
    return {
        "sku": str(item.sku or "").upper(),
        "affected_field": affected_field,
        "risk_level": risk_level,
        "safe_to_auto_apply": safe_to_auto_apply,
        "requires_review": requires_review,
        "retry_allowed": retry_allowed,
        "source": source,
        "repair_layer": repair_layer,
        "classified_error_code": classified_error_code,
        "current_value": current_value,
        "expected_value": expected_value,
        "suggested_value": suggested_value,
        "suggested_actions": suggested_actions,
    }


def _queue_summary(session: Session, plan: PublishRepairPlanRecord) -> dict:
    latest_attempt = session.exec(
        select(PublishAttemptRecord)
        .where(PublishAttemptRecord.sku == plan.sku)
        .order_by(PublishAttemptRecord.attempted_at.desc())
    ).first()
    return {
        "sku": plan.sku,
        "repair_plan_id": plan.id,
        "status": plan.status,
        "risk_level": plan.risk_level,
        "repair_layer": plan.repair_layer or "",
        "requires_review": bool(plan.requires_review),
        "retry_allowed": bool(plan.retry_allowed),
        "classified_error_code": plan.classified_error_code or "",
        "latest_error_message": latest_attempt.ebay_error_message if latest_attempt else "",
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else "",
    }


def _serialize_attempt(attempt: PublishAttemptRecord | None) -> dict | None:
    if attempt is None:
        return None
    return {
        "id": attempt.id,
        "sku": attempt.sku,
        "stage": attempt.stage,
        "status": attempt.status,
        "attempted_at": attempt.attempted_at.isoformat() if attempt.attempted_at else "",
        "ebay_environment": attempt.ebay_environment or "",
        "marketplace_id": attempt.marketplace_id or "",
        "request_summary": _loads(attempt.request_summary_json, {}),
        "raw_error": _loads(attempt.raw_error_json, {}),
        "ebay_error_id": attempt.ebay_error_id or "",
        "ebay_error_message": attempt.ebay_error_message or "",
        "classified_error_code": attempt.classified_error_code or "",
        "repair_layer": attempt.repair_layer or "",
        "requires_review": bool(attempt.requires_review),
        "retry_allowed": bool(attempt.retry_allowed),
    }


def _serialize_plan(plan: PublishRepairPlanRecord, *, repair_blocker: dict | None = None) -> dict:
    blocker = repair_blocker or {}
    blocked_plan_id = str(blocker.get("repair_plan_id") or "")
    blocked = bool(blocker.get("blocked_by_repair_queue"))
    status = str(plan.status or "")
    active = status in ACTIVE_REPAIR_STATUSES
    superseded = bool(blocked and active and blocked_plan_id and plan.id != blocked_plan_id)
    actionable = bool(active and plan.retry_allowed and not blocked and not superseded)
    return {
        "id": plan.id,
        "sku": plan.sku,
        "publish_attempt_id": plan.publish_attempt_id or "",
        "status": plan.status,
        "active": active and not superseded,
        "actionable": actionable,
        "superseded": superseded,
        "superseded_by_repair_plan_id": blocked_plan_id if superseded else "",
        "non_actionable_reason": (
            "Superseded by a newer repair plan that blocks publish retry."
            if superseded
            else (
                "Latest repair plan blocks publish retry."
                if blocked and plan.id == blocked_plan_id
                else ""
            )
        ),
        "affected_field": plan.affected_field or "",
        "current_value": _loads(plan.current_value_json, {}),
        "expected_value": _loads(plan.expected_value_json, {}),
        "suggested_value": _loads(plan.suggested_value_json, {}),
        "suggested_actions": _loads(plan.suggested_actions_json, []),
        "risk_level": plan.risk_level,
        "safe_to_auto_apply": bool(plan.safe_to_auto_apply),
        "requires_review": bool(plan.requires_review),
        "retry_allowed": bool(plan.retry_allowed),
        "source": plan.source,
        "repair_layer": plan.repair_layer or "",
        "classified_error_code": plan.classified_error_code or "",
        "created_at": plan.created_at.isoformat() if plan.created_at else "",
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else "",
    }


def _serialize_decision(decision: PublishRepairDecisionRecord) -> dict:
    return {
        "id": decision.id,
        "sku": decision.sku,
        "repair_plan_id": decision.repair_plan_id,
        "action": decision.action,
        "before_value": _loads(decision.before_value_json, {}),
        "after_value": _loads(decision.after_value_json, {}),
        "operator_label": decision.operator_label or "",
        "approved_at": decision.approved_at.isoformat() if decision.approved_at else "",
        "created_at": decision.created_at.isoformat() if decision.created_at else "",
    }


def _apply_plan_to_item(item: Item, plan: PublishRepairPlanRecord, new_value: Any) -> dict:
    field = str(plan.affected_field or "")
    if str(plan.risk_level) == "high" and new_value in (None, "", {}):
        return {"ok": False, "status_code": 400, "detail": "High-risk repair plans require an explicit replacement value."}

    if field == "condition_id":
        if isinstance(new_value, dict):
            new_value = new_value.get("condition_id")
        item.condition_id = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"condition_id": item.condition_id}}

    if field == "title_final":
        item.title_final = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"title_final": item.title_final}}

    if field == "description_final":
        item.description_final = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"description_final": item.description_final}}

    if field == "list_price":
        item.list_price = float(new_value) if new_value not in (None, "") else None
        return {"ok": True, "after_value": {"list_price": item.list_price}}

    if field == "ebay_category_id":
        if isinstance(new_value, dict):
            new_value = new_value.get("ebay_category_id")
        item.ebay_category_id = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"ebay_category_id": item.ebay_category_id}}

    if field == "offer_id":
        item.offer_id = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"offer_id": item.offer_id}}

    if field == "imageUrls" or field == "image_paths":
        normalized_urls = []
        for value in item.image_paths or []:
            text = str(value).strip()
            if looks_like_public_image_url_candidate(text):
                normalized = normalize_public_image_url(text)
                if normalized not in normalized_urls:
                    normalized_urls.append(normalized)
        local_paths = [str(value).strip() for value in (item.image_paths or []) if not looks_like_public_image_url_candidate(str(value))]
        item.image_paths = local_paths + normalized_urls
        return {"ok": True, "after_value": {"image_paths": item.image_paths}}

    if field == "listing_sync":
        return {"ok": False, "status_code": 400, "detail": "Listing sync repairs are guidance-only in this phase; run constrained sync manually."}

    if field == "item_specifics":
        item.item_specifics = _dumps(new_value)
        return {"ok": True, "after_value": {"item_specifics": new_value}}

    return {"ok": False, "status_code": 400, "detail": f"Repair plan field '{field}' is not applicable in this phase."}


def _field_snapshot(item: Item, field: str) -> dict:
    if field == "condition_id":
        return {"condition_id": item.condition_id or ""}
    if field == "title_final":
        return {"title_final": item.title_final or ""}
    if field == "description_final":
        return {"description_final": item.description_final or ""}
    if field == "list_price":
        return {"list_price": item.list_price}
    if field == "ebay_category_id":
        return {"ebay_category_id": item.ebay_category_id or ""}
    if field == "offer_id":
        return {"offer_id": item.offer_id or ""}
    if field in {"imageUrls", "image_paths"}:
        return {"image_paths": list(item.image_paths or [])}
    if field == "item_specifics":
        return {"item_specifics": _loads(item.item_specifics, {})}
    return {}


def _current_item_specific_value(item: Item, aspect_name: str) -> Any:
    values = _loads(item.item_specifics, {})
    if isinstance(values, dict):
        return values.get(aspect_name)
    return None


def _missing_required_fields(readiness_checks: dict[str, dict]) -> list[str]:
    fields = []
    for name, check in readiness_checks.items():
        if name.startswith("required_") and not check.get("ok"):
            fields.append(name.removeprefix("required_"))
    return fields


def _plan_for_missing_required_field(item: Item, field_name: str) -> dict | None:
    if field_name == "title" and (item.title_raw or "").strip():
        return _plan_payload(
            item,
            affected_field="title_final",
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=False,
            source="readiness_required_field",
            repair_layer="content_completeness",
            classified_error_code="missing_required_title",
            current_value={"title_final": item.title_final or ""},
            expected_value={"title_source": "title_raw"},
            suggested_value={"title_final": str(item.title_raw or "").strip()[:80]},
            suggested_actions=["Review the suggested title and approve it before retrying publish."],
        )
    if field_name == "description" and ((item.title_final or item.title_raw or "").strip()):
        return _plan_payload(
            item,
            affected_field="description_final",
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=False,
            source="readiness_required_field",
            repair_layer="content_completeness",
            classified_error_code="missing_required_description",
            current_value={"description_final": item.description_final or ""},
            expected_value={"description_source": "title_fallback"},
            suggested_value={"description_final": str(item.title_final or item.title_raw or "").strip()},
            suggested_actions=["Review the fallback description and approve it before retrying publish."],
        )
    if field_name == "price" and item.estimated_price:
        return _plan_payload(
            item,
            affected_field="list_price",
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=False,
            source="readiness_required_field",
            repair_layer="offer_basics",
            classified_error_code="missing_required_price",
            current_value={"list_price": item.list_price},
            expected_value={"price_source": "estimated_price"},
            suggested_value={"list_price": round(float(item.estimated_price), 2)},
            suggested_actions=["Review the suggested listing price and approve it before retrying publish."],
        )
    if field_name == "category_id":
        suggested_category_id = str(CATEGORY_MAP.get(str(item.category_key or "").lower(), "") or "")
        if suggested_category_id:
            return _plan_payload(
                item,
                affected_field="ebay_category_id",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="readiness_required_field",
                repair_layer="category_compatibility",
                classified_error_code="missing_required_category_id",
                current_value={"ebay_category_id": item.ebay_category_id or "", "category_key": item.category_key or ""},
                expected_value={"category_key": item.category_key or "", "suggested_category_id": suggested_category_id},
                suggested_value={"ebay_category_id": suggested_category_id},
                suggested_actions=["Review the suggested category and approve it before retrying publish."],
            )
    if field_name == "condition_id":
        condition_payload = _condition_suggestion_payload(item, [])
        if condition_payload.get("allowed_options") or condition_payload.get("recommended_value"):
            return _plan_payload(
                item,
                affected_field="condition_id",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="readiness_required_field",
                repair_layer="category_compatibility",
                classified_error_code="missing_required_condition_id",
                current_value={"condition_id": item.condition_id or "", "condition_label": item.condition_label or ""},
                expected_value={"condition_label": item.condition_label or "", "internal_condition_key": _infer_internal_condition_key(item)},
                suggested_value=condition_payload,
                suggested_actions=["Choose and approve an eBay condition ID before retrying publish."],
            )
    return None


def _infer_internal_condition_key(item: Item) -> str:
    condition_id = str(item.condition_id or "").strip()
    if condition_id in CONDITION_ID_TO_ENUM:
        return str(CONDITION_ID_TO_ENUM[condition_id] or "").strip().upper()

    condition_label = str(item.condition_label or "").strip().lower()
    normalized_label = normalize_inventory_enum(condition_label)
    if normalized_label:
        return normalized_label
    if "very good" in condition_label:
        return "USED_VERY_GOOD"
    if "like new" in condition_label or "excellent" in condition_label:
        return "USED_EXCELLENT"
    if "acceptable" in condition_label or "fair" in condition_label:
        return "USED_ACCEPTABLE"
    if "parts" in condition_label:
        return "FOR_PARTS_OR_NOT_WORKING"
    if "new" in condition_label:
        return "NEW"
    if "good" in condition_label or condition_label:
        return "USED_GOOD"
    return ""


def _condition_options(condition_ids: list[str]) -> list[dict]:
    seen: set[str] = set()
    options = []
    for condition_id in condition_ids:
        normalized = str(condition_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        options.append(
            {
                "id": normalized,
                "name": CONDITION_ID_LABELS.get(normalized, "Unknown"),
            }
        )
    return options


def _condition_suggestion_payload(item: Item, allowed_condition_ids: list[str]) -> dict:
    normalized_allowed_ids = [str(value or "").strip() for value in allowed_condition_ids if str(value or "").strip()]
    internal_condition_key = _infer_internal_condition_key(item)
    likely_ids = [
        condition_id
        for condition_id in CONDITION_LABEL_FALLBACKS.get(internal_condition_key, [])
        if not normalized_allowed_ids or condition_id in normalized_allowed_ids
    ]
    payload = {
        "allowed_options": _condition_options(normalized_allowed_ids),
        "likely_options": _condition_options(likely_ids),
        "recommended_value": None,
        "internal_condition_key": internal_condition_key,
    }
    if not normalized_allowed_ids and likely_ids:
        payload["allowed_options"] = _condition_options(likely_ids)
    return payload


def _empty_condition_suggestion_payload(item: Item) -> dict:
    return {
        "allowed_options": [],
        "likely_options": [],
        "recommended_value": None,
        "internal_condition_key": _infer_internal_condition_key(item),
    }


def _condition_suggested_actions(item: Item, policy_context: dict, suggestion_payload: dict) -> list[str]:
    if policy_context.get("local_policy_status") == "suspect_or_stale":
        return [
            "Review whether the selected category is wrong for this item's actual condition.",
            "Fetch live item-condition policy metadata before choosing another condition.",
            "Do not blindly cycle to another local fallback condition ID until the live category policy is confirmed.",
        ]
    actions = [
        "Choose one allowed condition ID for the exact eBay category.",
        "If the item does not fit any allowed condition, review whether the category is wrong.",
    ]
    likely_ids = [option["id"] for option in suggestion_payload.get("likely_options") or []]
    if _infer_internal_condition_key(item) == "USED_GOOD" and "5000" in likely_ids:
        actions.append("Use 5000 when the item clearly fits Good and the live category policy allows it.")
    if _infer_internal_condition_key(item) == "USED_GOOD" and "4000" in likely_ids:
        actions.append("Choose 4000 only if the item truly qualifies as Very Good.")
    if not policy_context.get("source"):
        actions.append("TODO: add a category condition metadata lookup seam if richer condition labels become available.")
    return actions


def _dedupe_plan_payloads(plans: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for plan in plans:
        key = (
            str(plan.get("affected_field") or ""),
            str(plan.get("classified_error_code") or ""),
            str(plan.get("repair_layer") or ""),
            _dumps(plan.get("current_value")) or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(plan)
    return deduped


def _has_draftable_current_blockers(readiness: dict, compatibility: dict) -> bool:
    return bool(readiness.get("blockers") or compatibility.get("blockers"))


def _extract_ebay_error_id(text: str) -> str:
    match = re.search(r"\b(25\d{3}|10\d{2})\b", str(text or ""))
    return str(match.group(1)) if match else ""


def _extract_aspect_name(text: str) -> str:
    match = re.search(r"aspect[^A-Za-z0-9]+([A-Za-z][A-Za-z0-9 _-]+)", str(text or ""), flags=re.IGNORECASE)
    return str(match.group(1)).strip() if match else ""


def _extract_image_urls_from_text(text: str) -> list[str]:
    return [normalize_public_image_url(match) for match in re.findall(r"https?:[\\/]{2}[^\s\"']+", str(text or ""))]


def _infer_stage_from_error(error_text: str) -> str:
    match = re.search(r"(create_inventory_item|create_offer|publish_offer|revise_listing|preflight)", error_text or "")
    return str(match.group(1)) if match else ""


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _local_policy_is_contradicted_by_live_error(item: Item, policy: dict) -> bool:
    allowed_condition_ids = [str(value or "").strip() for value in policy.get("allowed_condition_ids") or []]
    condition_id = str(item.condition_id or "").strip()
    return bool(condition_id and allowed_condition_ids and condition_id in allowed_condition_ids)


def _classify_live_condition_policy_conflict(item: Item, policy: dict, *, result) -> dict:
    allowed_condition_ids = [str(value or "").strip() for value in policy.get("allowed_condition_ids") or []]
    diagnostics = _condition_error_diagnostics(item, result=result)
    expected_value = {
        "category_id": str(item.ebay_category_id or ""),
        "policy_source": str(policy.get("source") or ""),
        "local_policy_status": "suspect_or_stale",
        "local_policy_allowed_condition_ids": allowed_condition_ids,
        "local_policy_allowed_condition_options": _condition_options(allowed_condition_ids),
        "policy_conflict_reason": "Live eBay rejected a condition that the local built-in category policy marked as allowed.",
        "review_required": True,
        "next_action": "Review category selection or fetch live item-condition policy metadata before retrying.",
    }
    suggested_value = _empty_condition_suggestion_payload(item) | {
        "rejected_by_live_validation": {
            "condition_id": diagnostics["local_condition_id"],
            "inventory_condition_enum": diagnostics["inventory_condition_enum"],
        }
    }
    return {
        "classified_error_code": "invalid_category_condition",
        "repair_layer": "category_compatibility",
        "requires_review": True,
        "retry_allowed": False,
        "ebay_error_id": _extract_ebay_error_id(str(result.details.get("body") or "")) or "25021",
        "ebay_error_message": "Live eBay rejected the condition/category pairing even though the local built-in policy marked it as allowed.",
        "plans": [
            _plan_payload(
                item,
                affected_field="condition_id",
                risk_level="high",
                safe_to_auto_apply=False,
                requires_review=True,
                retry_allowed=False,
                source="ebay_error_live_policy_conflict",
                repair_layer="category_compatibility",
                classified_error_code="invalid_category_condition",
                current_value=diagnostics,
                expected_value=expected_value,
                suggested_value=suggested_value,
                suggested_actions=_condition_suggested_actions(item, expected_value, suggested_value),
            )
        ],
    }


def _has_live_condition_policy_conflict(session: Session, item: Item) -> bool:
    normalized = str(item.sku or "").strip().upper()
    condition_id = str(item.condition_id or "").strip()
    category_id = str(item.ebay_category_id or "").strip()
    plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc(), PublishRepairPlanRecord.created_at.desc())
    ).all()
    for plan in plans:
        if str(plan.classified_error_code or "") != "invalid_category_condition":
            continue
        if str(plan.repair_layer or "") != "category_compatibility":
            continue
        expected_value = _loads(plan.expected_value_json, {})
        if str(expected_value.get("local_policy_status") or "") != "suspect_or_stale":
            continue
        current_value = _loads(plan.current_value_json, {})
        if str(current_value.get("category_id") or "") != category_id:
            continue
        if str(current_value.get("condition_id") or "") != condition_id:
            continue
        if str(plan.status or "") in ACTIVE_REPAIR_STATUSES:
            return True
    return False


def _condition_error_diagnostics(item: Item, *, result) -> dict:
    details = dict(result.details or {})
    offer_id = str(details.get("offer_id") or item.offer_id or "")
    existing_offer_id_detected = bool(
        offer_id
        and not str(item.listing_id or "").strip()
        and str(item.status or "") != "listed"
    )
    stage = str(details.get("stage") or _infer_stage_from_error(str(result.error or "")) or "")
    stale_existing_offer_hypothesis = bool(existing_offer_id_detected and stage == "publish_offer")
    return {
        "category_id": str(details.get("category_id") or item.ebay_category_id or ""),
        "condition_id": str(details.get("local_condition_id") or item.condition_id or ""),
        "local_condition_id": str(details.get("local_condition_id") or item.condition_id or ""),
        "current_category_id": str(item.ebay_category_id or ""),
        "current_condition_id": str(item.condition_id or ""),
        "previous_category_id": str(details.get("previous_category_id") or ""),
        "previous_condition_id": str(details.get("previous_condition_id") or ""),
        "inventory_condition_enum": str(details.get("inventory_condition_enum") or _infer_internal_condition_key(item) or ""),
        "offer_id": offer_id,
        "existing_offer_id_detected": existing_offer_id_detected,
        "planned_action": "publish_existing_offer" if existing_offer_id_detected else "create_offer_then_publish",
        "stage": stage,
        "stale_existing_offer_hypothesis": stale_existing_offer_hypothesis,
        "stale_existing_offer_note": (
            "Existing unpublished offer may contain stale category or condition state; diagnose before retrying publish."
            if stale_existing_offer_hypothesis
            else ""
        ),
        "raw_ebay_error": str(details.get("body") or result.error or ""),
    }
