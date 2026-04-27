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
            return {
                "affected_field": "condition_id",
                "suggested_value": allowed_condition_ids[0] if allowed_condition_ids else None,
                "suggested_actions": [
                    "Choose an allowed condition ID for the exact eBay category.",
                    "If no listed condition matches the item, review the category assignment manually.",
                ],
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
        .order_by(PublishRepairPlanRecord.updated_at.desc())
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

    repair_status = summarize_repair_status(session, normalized)
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
        "repair_plans": [_serialize_plan(plan) for plan in plans],
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
    latest_plan = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc())
    ).first()
    if not latest_plan:
        return {
            "has_open_repair": False,
            "status": "none",
            "last_error_code": "",
            "repair_layer": "",
            "risk_level": "",
            "suggested_fixes": [],
            "ready_to_retry": False,
        }
    return {
        "has_open_repair": str(latest_plan.status) in ACTIVE_REPAIR_STATUSES,
        "status": str(latest_plan.status),
        "last_error_code": str(latest_plan.classified_error_code or (latest_attempt.classified_error_code if latest_attempt else "")),
        "repair_layer": str(latest_plan.repair_layer or ""),
        "risk_level": str(latest_plan.risk_level or ""),
        "suggested_fixes": _loads(latest_plan.suggested_actions_json, []),
        "ready_to_retry": bool(latest_plan.retry_allowed),
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

    provider = DeterministicRepairSuggestionProvider()
    plans = session.exec(
        select(PublishRepairPlanRecord)
        .where(PublishRepairPlanRecord.sku == normalized)
        .order_by(PublishRepairPlanRecord.updated_at.desc())
    ).all()

    drafted = []
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
    return {
        "sku": normalized,
        "status": "draft_fix_available" if drafted else "no_draft_available",
        "drafts": drafted,
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
    if not approved:
        return {"ok": False, "status_code": 400, "detail": "Repair approval is required before applying a draft fix."}
    if str(plan.risk_level) == "high" and edited_value is None and not plan.suggested_value_json:
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
            skipped.append({"sku": sku, "reason": detail.get("status", "no_draft_available")})
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


def _serialize_plan(plan: PublishRepairPlanRecord) -> dict:
    return {
        "id": plan.id,
        "sku": plan.sku,
        "publish_attempt_id": plan.publish_attempt_id or "",
        "status": plan.status,
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
        item.condition_id = str(new_value if new_value is not None else "").strip()
        return {"ok": True, "after_value": {"condition_id": item.condition_id}}

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
