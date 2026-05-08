"""
Listings API — active eBay listings management (Phase 5B).
Revision, sync, push-to-eBay, and takedown for listed/exported items.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.src.services.ebay_auth_diagnostics import get_ebay_auth_readiness
from apps.api.src.services.publish_diagnostics import build_publish_diagnostics
from apps.api.src.services.publish_repair import get_publish_repair_blocker
from apps.api.src.services.publish_readiness import evaluate_publish_readiness, not_found_publish_readiness
from apps.api.src.services.stale_offer_remediation import (
    PUBLISH_DECISION_TYPED_CONFIRMATION,
    REQUIRED_TYPED_CONFIRMATION,
    SUPERSEDE_TYPED_CONFIRMATION,
    build_stale_offer_publish_decision_preview,
    build_stale_offer_remediation_approval_preview,
    build_stale_offer_refresh_supersede_preview,
    execute_approved_stale_offer_publish_decision,
    execute_approved_refresh_existing_unpublished_offer,
    execute_approved_stale_offer_refresh_supersede,
)
from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import get_session
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src import http_client as ebay_http
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_live_e2e_allowed,
    is_live_e2e_enabled,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
    parse_sku_list,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_LOCATION_KEY_CACHE: dict[str, str] = {}
_LISTINGS_SYNC_MAX_PAGES = 20
_LISTINGS_SYNC_MAX_SECONDS = 20.0

CONDITION_MAP = {
    "NEW": "NEW", "NEW_OTHER": "NEW_OTHER", "NEW_WITH_DEFECTS": "NEW_WITH_DEFECTS",
    "LIKE_NEW": "LIKE_NEW", "VERY_GOOD": "VERY_GOOD", "USED_GOOD": "USED_GOOD",
    "USED_ACCEPTABLE": "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING": "FOR_PARTS_OR_NOT_WORKING",
    "1000": "NEW", "1500": "NEW_OTHER", "2000": "NEW_WITH_DEFECTS",
    "2500": "NEW_OTHER", "3000": "USED_GOOD", "4000": "VERY_GOOD",
    "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE", "7000": "FOR_PARTS_OR_NOT_WORKING",
}

EBAY_ERROR_HINTS = {
    25002: "Offer already exists for this SKU — the existing offer was reused automatically.",
    25013: "Inventory item not found — re-publish via the Export tab first.",
    21919188: "Price is below the minimum threshold for this category.",
    25001: "Listing format not supported in this category.",
    21916587: "Title contains prohibited words.",
    25004: "Category ID is invalid or not supported.",
}


class StaleOfferApprovedRefreshPayload(BaseModel):
    sku: str
    remediation_type: str
    repair_plan_id: str
    latest_publish_attempt_id: str
    offer_id: str
    confirm_offer_status: str
    confirm_listing_id_empty: bool
    confirm_category_id: str
    confirm_condition_id: str
    confirm_inventory_condition_enum: str
    confirm_publish_after_remediation: bool
    operator_approved: bool
    typed_confirmation: str
    approved_payload_hash: str


class StaleOfferSupersedeApprovalPayload(BaseModel):
    sku: str
    action_type: str
    repair_plan_id: str
    latest_publish_attempt_id: str
    previous_classified_error_code: str
    confirm_listing_id_empty: bool
    confirm_offer_status: str
    confirm_category_id: str
    confirm_condition_id: str
    confirm_inventory_condition_enum: str
    confirm_publish_remains_blocked: bool
    confirm_replacement_classified_error_code: str
    operator_approved: bool
    operator_label: str | None = None
    typed_confirmation: str
    approved_payload_hash: str


class StaleOfferPublishDecisionApprovalPayload(BaseModel):
    sku: str
    action_type: str
    repair_plan_id: str
    latest_publish_attempt_id: str
    offer_id: str
    confirm_offer_status: str
    confirm_listing_id_empty: bool
    confirm_category_id: str
    confirm_condition_id: str
    confirm_inventory_condition_enum: str
    confirm_blocker_classified_error_code: str
    confirm_merchant_location_key: str
    confirm_fulfillment_policy_id: str
    confirm_payment_policy_id: str
    confirm_return_policy_id: str
    confirm_publish_existing_offer_only: bool
    confirm_publish_after_decision: bool
    operator_approved: bool
    operator_label: str | None = None
    typed_confirmation: str
    approved_payload_hash: str


def _repair_queue_blocked_detail(sku: str, repair_blocker: dict) -> dict:
    return {
        "code": "blocked_by_repair_queue",
        "sku": (sku or repair_blocker.get("sku") or "").upper(),
        "blocked_by_repair_queue": True,
        "repair_plan_id": repair_blocker["repair_plan_id"],
        "latest_publish_attempt_id": repair_blocker["latest_publish_attempt_id"],
        "repair_status": repair_blocker["repair_status"],
        "retry_allowed": repair_blocker["retry_allowed"],
        "classified_error_code": repair_blocker["classified_error_code"],
        "reason": repair_blocker["reason"],
        "suggested_actions": repair_blocker["suggested_actions"],
    }


def _is_listed_on_ebay(item) -> bool:
    return bool(str(item.listing_id or "").strip()) or str(item.status or "") == "listed"


def _is_stale_offer_refresh_live_enabled() -> bool:
    return os.getenv("ALLOW_EBAY_STALE_OFFER_REFRESH") == "true"


def _build_stale_offer_refresh_executor():
    from packages.ebay.src.inventory_client import EbayInventoryClient

    return EbayInventoryClient()


def _build_publish_decision_executor():
    from packages.ebay.src.inventory_client import EbayInventoryClient

    return EbayInventoryClient()


# ── GET /api/listings ──────────────────────────────────────────────────────────

@router.get("")
def get_listings(
    status: str = "all",
    search: str = "",
    session: Session = Depends(get_session),
):
    """Return active listings (status = listed or exported)."""
    stmt = select(ItemRecord).where(
        ItemRecord.status.in_(["listed", "exported"])
    )
    records = session.exec(stmt).all()

    result = []
    for r in records:
        if status in ("listed", "exported") and r.status != status:
            continue
        if search:
            q = search.lower()
            title = (r.title_final or r.title_raw or "").lower()
            sku = (r.sku or "").lower()
            if q not in title and q not in sku:
                continue

        days_listed = _compute_days_listed(r)
        paths = [p for p in (r.image_paths or "").split("|") if p.strip()]
        cover_photo = paths[0] if paths else None

        result.append({
            "sku": r.sku,
            "title": r.title_final or r.title_raw or "",
            "list_price": r.list_price,
            "condition": r.condition_label,
            "listing_id": r.listing_id,
            "offer_id": r.offer_id,
            "image_paths": r.image_paths or "",
            "status": r.status,
            "published_at": r.date_listed.isoformat() if r.date_listed else None,
            "promotion_pct": r.promotion_pct,
            "concern_flags": r.concern_flags,
            "listing_quality_score": r.listing_quality_score,
            "ebay_category_name": r.ebay_category_name,
            "days_listed": days_listed,
            "cover_photo": cover_photo,
        })

    return result


# ── GET /api/listings/sync ─────────────────────────────────────────────────────

@router.get("/{sku}/publish-readiness")
def get_publish_readiness(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "listings.publish_readiness")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    item = ItemRepository(session).get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_publish_readiness(sku).as_dict())
    return evaluate_publish_readiness(item).as_dict()


@router.get("/{sku}/publish-preview")
def get_publish_preview(sku: str, session: Session = Depends(get_session)):
    from packages.ebay.src.inventory_client import EbayInventoryClient

    try:
        assert_route_sku_allowed(sku, "listings.publish_preview")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    item = ItemRepository(session).get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_publish_readiness(sku).as_dict())

    readiness = evaluate_publish_readiness(item).as_dict()
    client = EbayInventoryClient()
    hosted_photo_urls = _hosted_photo_urls(item.image_paths)
    inventory_payload = client._build_inventory_payload(item, hosted_photo_urls)

    seller_policy_check = next(
        (check for check in readiness["checks"] if check["name"] == "seller_policy_readiness"),
        None,
    )
    offer_payload = client._build_offer_payload(
        item,
        _preview_policy_ids(seller_policy_check),
        merchant_location_key="preview-location",
    )

    revision_payload = None
    if item.offer_id or item.status == "listed":
        revision_payload = {
            "offer_id": item.offer_id or "",
            "inventory_item": inventory_payload,
            "offer": offer_payload,
        }

    existing_offer_id_detected = bool(str(item.offer_id or "").strip()) and not bool(str(item.listing_id or "").strip()) and (item.status or "") != "listed"
    planned_action = "publish_existing_offer" if existing_offer_id_detected else "create_offer_then_publish"

    mutation_allowed = False
    mutation_reasons = ["Publish preview is read-only in this phase; no sandbox or live mutation is performed."]
    if not is_live_e2e_enabled():
        mutation_reasons.append("ALLOW_LIVE_E2E is false, so live mutation remains blocked.")

    repair_blocker = get_publish_repair_blocker(session, item.sku or sku)
    repair_status = repair_blocker["repair_status"]

    return {
        "sku": (item.sku or "").upper(),
        "readiness": readiness,
        "repair_status": repair_status,
        "blocked_by_repair_queue": bool(repair_blocker["blocked_by_repair_queue"]),
        "would_publish": bool(readiness["ready"] and not repair_blocker["blocked_by_repair_queue"]),
        "repair_plan_id": repair_blocker["repair_plan_id"],
        "latest_publish_attempt_id": repair_blocker["latest_publish_attempt_id"],
        "retry_allowed": repair_blocker["retry_allowed"],
        "classified_error_code": repair_blocker["classified_error_code"],
        "policy_conflict": repair_blocker["policy_conflict"],
        "repair_queue_blocker": repair_blocker,
        "condition_id": str(item.condition_id or ""),
        "inventory_condition_enum": str(inventory_payload.get("condition") or ""),
        "category_id": str(item.ebay_category_id or ""),
        "offer_id": str(item.offer_id or ""),
        "existing_offer_id_detected": existing_offer_id_detected,
        "existing_offer_stale_state_diagnostic": repair_blocker["condition_diagnostics"].get("stale_existing_offer_note", ""),
        "stale_existing_offer_hypothesis": bool(
            repair_blocker["condition_diagnostics"].get("stale_existing_offer_hypothesis")
        ),
        "planned_action": planned_action,
        "listing_id_missing": not bool(str(item.listing_id or "").strip()),
        "mutation_allowed": mutation_allowed,
        "mutation_blockers": mutation_reasons,
        "inventory_item_payload_preview": inventory_payload,
        "offer_payload_preview": offer_payload,
        "revision_payload_preview": revision_payload,
        "photo_input_summary": {
            "hosted_photo_urls": hosted_photo_urls,
            "total_image_paths": len(item.image_paths or []),
        },
        "environment": {
            "ebay_environment": get_settings().ebay_environment,
            "allow_live_e2e": is_live_e2e_enabled(),
        },
    }


@router.get("/{sku}/publish-diagnostics")
def get_publish_diagnostics(
    sku: str,
    allow_live_readonly: bool = False,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "listings.publish_diagnostics")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    diagnostics = build_publish_diagnostics(
        session,
        sku,
        allow_live_readonly=allow_live_readonly,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)
    return diagnostics


@router.get("/{sku}/stale-offer-remediation/approval-preview")
def get_stale_offer_remediation_approval_preview(
    sku: str,
    allow_live_readonly: bool = False,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "listings.stale_offer_remediation.approval_preview")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    diagnostics = build_publish_diagnostics(
        session,
        sku,
        allow_live_readonly=allow_live_readonly,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)
    return build_stale_offer_remediation_approval_preview(diagnostics)


@router.get("/{sku}/stale-offer-remediation/supersede-preview")
def get_stale_offer_remediation_supersede_preview(
    sku: str,
    repair_plan_id: str,
    allow_live_readonly: bool = False,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "listings.stale_offer_remediation.supersede_preview")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    diagnostics = build_publish_diagnostics(
        session,
        sku,
        allow_live_readonly=allow_live_readonly,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)
    return build_stale_offer_refresh_supersede_preview(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )


@router.get("/{sku}/stale-offer-remediation/publish-decision-preview")
def get_stale_offer_remediation_publish_decision_preview(
    sku: str,
    repair_plan_id: str,
    allow_live_readonly: bool = False,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "listings.stale_offer_remediation.publish_decision_preview")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    diagnostics = build_publish_diagnostics(
        session,
        sku,
        allow_live_readonly=allow_live_readonly,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)
    return build_stale_offer_publish_decision_preview(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )


@router.post("/{sku}/stale-offer-remediation/execute-approved-refresh")
def execute_stale_offer_remediation_approved_refresh(
    sku: str,
    payload: StaleOfferApprovedRefreshPayload,
    session: Session = Depends(get_session),
):
    normalized_sku = (sku or "").strip().upper()
    try:
        assert_route_sku_allowed(normalized_sku, "listings.stale_offer_remediation.execute_approved_refresh")
        assert_live_e2e_allowed(normalized_sku)
    except E2ESafetyError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "live_execution_disabled",
                "sku": normalized_sku,
                "execution_status": "live_execution_disabled",
                "live_execution_enabled": False,
                "no_publish_performed": True,
                "repair_queue_cleared": False,
                "reason": str(exc),
            },
        )

    if not _is_stale_offer_refresh_live_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "live_execution_disabled",
                "sku": normalized_sku,
                "execution_status": "live_execution_disabled",
                "live_execution_enabled": False,
                "required_env_flag": "ALLOW_EBAY_STALE_OFFER_REFRESH=true",
                "no_publish_performed": True,
                "repair_queue_cleared": False,
                "reason": "Dedicated stale-offer refresh feature flag is disabled.",
            },
        )

    approval_request = payload.model_dump()
    if payload.typed_confirmation != REQUIRED_TYPED_CONFIRMATION:
        # Fail before live-read-only preflight for clearly malformed approval.
        result = {
            "code": "approval_typed_confirmation_mismatch",
            "sku": normalized_sku,
            "execution_status": "blocked",
            "live_execution_enabled": True,
            "no_publish_performed": True,
            "repair_queue_cleared": False,
            "refusal_reasons": [
                {
                    "code": "approval_typed_confirmation_mismatch",
                    "message": "typed_confirmation must exactly equal REFRESH UNPUBLISHED OFFER ONLY.",
                }
            ],
        }
        raise HTTPException(status_code=409, detail=result)

    diagnostics = build_publish_diagnostics(
        session,
        normalized_sku,
        allow_live_readonly=True,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)

    executor = _build_stale_offer_refresh_executor()
    result = execute_approved_refresh_existing_unpublished_offer(
        sku=normalized_sku,
        diagnostics=diagnostics,
        approval_request=approval_request,
        executor=executor,
        live_remediation_enabled=True,
        post_refresh_diagnostics_provider=lambda: build_publish_diagnostics(
            session,
            normalized_sku,
            allow_live_readonly=True,
        ),
    )
    status = result.get("execution_status")
    if status in {"live_execution_disabled", "blocked"}:
        raise HTTPException(status_code=409, detail=result)
    if status in {"failed_before_offer_refresh", "partial_failure_offer_refresh_failed"}:
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/{sku}/stale-offer-remediation/execute-approved-supersede")
def execute_stale_offer_remediation_approved_supersede(
    sku: str,
    payload: StaleOfferSupersedeApprovalPayload,
    session: Session = Depends(get_session),
):
    normalized_sku = (sku or "").strip().upper()
    try:
        assert_route_sku_allowed(normalized_sku, "listings.stale_offer_remediation.execute_approved_supersede")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if payload.typed_confirmation != SUPERSEDE_TYPED_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approval_typed_confirmation_mismatch",
                "sku": normalized_sku,
                "action_type": payload.action_type,
                "execution_status": "blocked",
                "no_publish_performed": True,
                "no_ebay_mutation_performed": True,
                "repair_queue_cleared": False,
                "refusal_reasons": [
                    {
                        "code": "approval_typed_confirmation_mismatch",
                        "message": f"typed_confirmation must exactly equal {SUPERSEDE_TYPED_CONFIRMATION}.",
                    }
                ],
            },
        )

    diagnostics = build_publish_diagnostics(
        session,
        normalized_sku,
        allow_live_readonly=True,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)

    result = execute_approved_stale_offer_refresh_supersede(
        session=session,
        sku=normalized_sku,
        repair_plan_id=payload.repair_plan_id,
        diagnostics=diagnostics,
        approval_request=payload.model_dump(),
    )
    if result.get("execution_status") == "blocked":
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/{sku}/stale-offer-remediation/execute-approved-publish-decision")
def execute_stale_offer_remediation_approved_publish_decision(
    sku: str,
    payload: StaleOfferPublishDecisionApprovalPayload,
    session: Session = Depends(get_session),
):
    normalized_sku = (sku or "").strip().upper()
    try:
        assert_route_sku_allowed(normalized_sku, "listings.stale_offer_remediation.execute_approved_publish_decision")
        assert_live_e2e_allowed(normalized_sku)
    except E2ESafetyError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "live_execution_disabled",
                "sku": normalized_sku,
                "execution_status": "live_execution_disabled",
                "no_publish_performed": True,
                "reason": str(exc),
            },
        )

    if payload.typed_confirmation != PUBLISH_DECISION_TYPED_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approval_typed_confirmation_mismatch",
                "sku": normalized_sku,
                "action_type": payload.action_type,
                "execution_status": "blocked",
                "no_publish_performed": True,
                "refusal_reasons": [
                    {
                        "code": "approval_typed_confirmation_mismatch",
                        "message": f"typed_confirmation must exactly equal {PUBLISH_DECISION_TYPED_CONFIRMATION}.",
                    }
                ],
            },
        )

    diagnostics = build_publish_diagnostics(
        session,
        normalized_sku,
        allow_live_readonly=True,
    )
    if not diagnostics.get("found"):
        raise HTTPException(status_code=404, detail=diagnostics)

    result = execute_approved_stale_offer_publish_decision(
        session=session,
        sku=normalized_sku,
        repair_plan_id=payload.repair_plan_id,
        diagnostics=diagnostics,
        approval_request=payload.model_dump(),
        publisher=_build_publish_decision_executor(),
    )
    if result.get("execution_status") == "blocked":
        raise HTTPException(status_code=409, detail=result)
    if result.get("execution_status") == "publish_failed":
        raise HTTPException(status_code=502, detail=result)
    return result


@router.get("/{sku}/revise-readiness")
def get_revise_readiness(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "listings.revise_readiness")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    item = ItemRepository(session).get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_publish_readiness(sku).as_dict())
    return _build_revise_readiness(item)


@router.get("/{sku}/revise-preview")
def get_revise_preview(sku: str, session: Session = Depends(get_session)):
    from packages.ebay.src.inventory_client import EbayInventoryClient

    try:
        assert_route_sku_allowed(sku, "listings.revise_preview")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    item = ItemRepository(session).get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_publish_readiness(sku).as_dict())

    readiness = _build_revise_readiness(item)
    client = EbayInventoryClient()
    hosted_photo_urls = _hosted_photo_urls(item.image_paths)
    inventory_payload = client._build_inventory_payload(item, hosted_photo_urls)

    seller_policy_check = next(
        (check for check in readiness["publish_readiness"]["checks"] if check["name"] == "seller_policy_readiness"),
        None,
    )
    offer_payload = client._build_offer_payload(
        item,
        _preview_policy_ids(seller_policy_check),
        merchant_location_key="preview-location",
    )

    return {
        "sku": (item.sku or "").upper(),
        "revise_readiness": readiness,
        "inventory_item_payload_preview": inventory_payload,
        "offer_payload_preview": offer_payload,
        "listing_identifiers": {
            "listing_id": item.listing_id or "",
            "offer_id": item.offer_id or "",
            "listing_url": item.listing_url or "",
        },
        "mutation_allowed": False,
        "would_revise": readiness["ready"],
        "mutation_blockers": readiness["blockers"] + ["Revise preview is read-only in this phase; no eBay mutation is performed."],
        "warnings": readiness["warnings"],
        "photo_input_summary": {
            "hosted_photo_urls": hosted_photo_urls,
            "total_image_paths": len(item.image_paths or []),
        },
    }


@router.get("/sync")
def sync_listings(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    """Sync active listings from eBay inventory API (paginated)."""
    selected = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "listings.sync", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    auth = EbayAuth()
    if not auth.is_configured():
        raise HTTPException(status_code=503, detail="eBay not configured")

    headers = _ebay_headers(auth)
    base = auth.api_base

    sync_errors: list[str] = []
    pages_fetched = 0
    if selected:
        all_ebay_items, sync_errors = _fetch_inventory_items_for_skus(base, headers, selected)
    else:
        all_ebay_items, sync_errors, pages_fetched = _fetch_inventory_items_paginated(base, headers)

    repo = ItemRepository(session)
    synced = 0
    updated = 0
    updated_offer_ids = 0
    updated_listing_ids = 0
    already_current = 0
    not_found = []
    now = datetime.utcnow().isoformat()
    cfg = get_settings()
    listing_id_available_from_sync = True

    allowed = set(selected)
    for ebay_item in all_ebay_items:
        sku = ebay_item.get("sku", "")
        if not sku:
            continue
        if allowed and sku.upper() not in allowed:
            continue
        synced += 1
        local = repo.get_by_sku(sku)
        if not local:
            not_found.append(sku)
            _touch_synced_at(cfg.db_path, sku, now)
            continue

        changed = False
        # Fetch offer data separately (inventory_item endpoint does not include offers)
        try:
            offer_resp = ebay_http.get(
                f"{base}/sell/inventory/v1/offer",
                headers=headers,
                params={"sku": sku},
                timeout=15,
            )
            if offer_resp.status_code == 200:
                offers = offer_resp.json().get("offers", [])
                if offers:
                    offer = offers[0]
                    offer_id = offer.get("offerId", "")
                    if offer_id and offer_id != local.offer_id:
                        local.offer_id = offer_id
                        changed = True
                        updated_offer_ids += 1
                    listing_id = str(
                        offer.get("listingId")
                        or ((offer.get("listing") or {}).get("listingId") if isinstance(offer.get("listing"), dict) else "")
                        or ""
                    ).strip()
                    listing_url = str(
                        offer.get("listingUrl")
                        or ((offer.get("listing") or {}).get("listingUrl") if isinstance(offer.get("listing"), dict) else "")
                        or ""
                    ).strip()
                    if listing_id:
                        if listing_id != (local.listing_id or ""):
                            local.listing_id = listing_id
                            changed = True
                            updated_listing_ids += 1
                        if listing_url and listing_url != (local.listing_url or ""):
                            local.listing_url = listing_url
                            changed = True
                        elif not listing_url and not (local.listing_url or ""):
                            env_domain = "sandbox.ebay.com" if auth.settings.ebay_environment == "sandbox" else "ebay.com"
                            local.listing_url = f"https://www.{env_domain}/itm/{listing_id}"
                            changed = True
                    else:
                        listing_id_available_from_sync = False
                    ebay_price_str = (offer.get("pricingSummary") or {}).get("price") or {}
                    ebay_price = float(ebay_price_str.get("value", 0) or 0)
                    if ebay_price and ebay_price != local.list_price:
                        local.list_price = ebay_price
                        changed = True
            else:
                sync_errors.append(
                    f"{sku}: offer lookup failed {offer_resp.status_code}: {offer_resp.text[:200]}"
                )
        except Exception as exc:
            sync_errors.append(f"{sku}: offer lookup error: {exc}")
            logger.warning("Failed to fetch offer for SKU %s: %s", sku, exc)

        if changed:
            repo.upsert(local)
            updated += 1
        else:
            already_current += 1

        _touch_synced_at(cfg.db_path, sku, now)

    result: dict = {"synced": synced, "updated": updated, "not_found": not_found}
    result["updated_offer_ids"] = updated_offer_ids
    result["updated_listing_ids"] = updated_listing_ids
    result["already_current"] = already_current
    result["listing_id_available_from_sync"] = listing_id_available_from_sync
    if not listing_id_available_from_sync:
        result["next_action"] = "Publish the existing offer or use a listing lookup flow to recover listing identifiers."
    if selected:
        result["constrained"] = True
        result["requested_skus"] = selected
    else:
        result["pages_fetched"] = pages_fetched
    if sync_errors:
        result["errors"] = sync_errors
    return result


@router.get("/ebay-connectivity")
def ebay_connectivity():
    """
    Diagnose eBay connectivity and auth wiring from the API process.
    """
    auth = EbayAuth()
    token = auth.user_token or ""
    token_prefix = token[:20]
    result = {
        "api_base": auth.api_base,
        "marketplace_id": auth.marketplace_id,
        "token_present": bool(token),
        "token_length": len(token),
        "token_prefix": token_prefix,
        "ebay_status": None,
        "ebay_response": None,
        "error": None,
    }
    try:
        resp = ebay_http.get(
            f"{auth.api_base}/sell/inventory/v1/location",
            headers=_ebay_headers(auth),
            timeout=20,
        )
        result["ebay_status"] = resp.status_code
        try:
            result["ebay_response"] = resp.json()
        except Exception:
            result["ebay_response"] = resp.text[:1000]
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ── POST /api/listings/push/{sku} ─────────────────────────────────────────────

class PushPayload(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    list_price: Optional[float] = None
    condition: Optional[str] = None
    condition_notes: Optional[str] = None
    promotion_pct: Optional[float] = None
    promotion_enabled: bool = False
    photos_changed: bool = False


@router.post("/push/{sku}")
def push_to_ebay(sku: str, payload: PushPayload, session: Session = Depends(get_session)):
    """
    Multi-step push of field updates to eBay.
    Returns per-step results so the UI can show progress.
    Steps: inventory_item, offer, promotion.
    """
    try:
        assert_route_sku_allowed(sku, "listings.push")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    repair_blocker = get_publish_repair_blocker(session, item.sku or sku)
    if repair_blocker["blocked_by_repair_queue"]:
        raise HTTPException(
            status_code=409,
            detail=_repair_queue_blocked_detail(item.sku or sku, repair_blocker),
        )

    if not item.offer_id:
        raise HTTPException(
            status_code=400,
            detail="No offer ID stored. Publish this item first via the Export tab.",
        )

    auth = EbayAuth()
    if not auth.is_configured():
        raise HTTPException(status_code=503, detail="eBay not configured")

    # Apply payload to item
    if payload.title is not None:
        item.title_final = payload.title
    if payload.description is not None:
        item.description_final = payload.description
    if payload.list_price is not None:
        item.list_price = payload.list_price
    if payload.condition is not None:
        item.condition_label = payload.condition
    if payload.condition_notes is not None:
        item.condition_notes = payload.condition_notes
    if payload.promotion_pct is not None:
        item.promotion_pct = payload.promotion_pct

    headers = _ebay_headers(auth)
    base = auth.api_base
    steps = []

    # Step 1: PUT inventory item (title, description, condition, aspects)
    condition = _resolve_condition(item.condition_id, item.condition_label)
    aspects = _build_aspects(item)

    inv_payload: dict = {
        "product": {
            "title": (item.title_final or item.title_raw or "")[:80],
            "description": item.description_final or "",
            "aspects": aspects,
        },
        "condition": condition,
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }
    image_urls = _hosted_photo_urls(item.image_paths)
    if image_urls:
        inv_payload["product"]["imageUrls"] = image_urls[:12]
    if item.condition_notes:
        inv_payload["conditionDescription"] = item.condition_notes[:1000]

    try:
        r1 = ebay_http.put(
            f"{base}/sell/inventory/v1/inventory_item/{sku}",
            headers=headers,
            json=inv_payload,
            timeout=30,
        )
        if r1.status_code in (200, 204):
            steps.append({"step": "inventory_item", "ok": True, "msg": "Inventory item updated"})
        else:
            steps.append({"step": "inventory_item", "ok": False, "msg": _parse_ebay_error(r1)})
    except Exception as exc:
        steps.append({"step": "inventory_item", "ok": False, "msg": str(exc)})

    # Step 2: PUT offer (price)
    price = round(float(item.list_price or 9.99), 2)
    marketplace_id = auth.marketplace_id
    country_code = marketplace_id.split("_", 1)[-1] if "_" in marketplace_id else "US"

    # Build full offer payload so eBay accepts it
    from packages.ebay.src.inventory_client import EbayInventoryClient
    client = EbayInventoryClient()
    try:
        policies = client.get_seller_policies()
    except Exception:
        policies = {"fulfillment_id": "", "payment_id": "", "return_id": ""}

    category_id = str(item.ebay_category_id or "99")
    try:
        merchant_location_key = _get_or_create_merchant_location_key(base, headers)
        offer_payload = {
            "sku": sku,
            "marketplaceId": marketplace_id,
            "format": "FIXED_PRICE",
            "availableQuantity": 1,
            "categoryId": category_id,
            "listingDescription": item.description_final or item.title_final or "",
            "merchantLocationKey": merchant_location_key,
            "listingPolicies": {
                "fulfillmentPolicyId": policies.get("fulfillment_id", ""),
                "paymentPolicyId": policies.get("payment_id", ""),
                "returnPolicyId": policies.get("return_id", ""),
                "countryCode": country_code,
            },
            "pricingSummary": {
                "price": {"currency": "USD", "value": f"{price:.2f}"}
            },
            "includeCatalogProductDetails": False,
        }

        r2 = ebay_http.put(
            f"{base}/sell/inventory/v1/offer/{item.offer_id}",
            headers=headers,
            json=offer_payload,
            timeout=30,
        )
        if r2.status_code in (200, 204):
            steps.append({"step": "offer", "ok": True, "msg": "Offer updated"})
        else:
            steps.append({"step": "offer", "ok": False, "msg": _parse_ebay_error(r2)})
    except Exception as exc:
        steps.append({"step": "offer", "ok": False, "msg": str(exc)})

    # Step 3: Promotion
    if payload.promotion_enabled and (payload.promotion_pct or 0) > 0:
        pct = payload.promotion_pct or item.promotion_pct or 3.0
        steps.append({
            "step": "promotion",
            "ok": True,
            "msg": f"Promotion {pct}% noted — Marketing API integration pending",
        })
    elif not payload.promotion_enabled:
        steps.append({"step": "promotion", "ok": True, "msg": "No promotion changes"})

    # Save updated item to DB
    try:
        repo.upsert(item)
    except Exception as exc:
        logger.error("Error saving %s after push: %s", sku, exc)

    all_ok = all(s["ok"] for s in steps)
    return {
        "sku": sku,
        "ok": all_ok,
        "steps": steps,
        "item": {
            "title": item.title_final,
            "list_price": item.list_price,
            "condition": item.condition_label,
        },
    }


# ── DELETE /api/listings/end/{sku} ────────────────────────────────────────────

@router.delete("/end/{sku}")
def end_listing(sku: str, session: Session = Depends(get_session)):
    """Withdraw offer from eBay (ends listing, keeps inventory item)."""
    try:
        assert_route_sku_allowed(sku, "listings.end")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if not item.offer_id:
        raise HTTPException(status_code=400, detail="No offer ID — cannot withdraw")

    repair_blocker = get_publish_repair_blocker(session, item.sku or sku)
    if repair_blocker["blocked_by_repair_queue"] and not _is_listed_on_ebay(item):
        raise HTTPException(
            status_code=409,
            detail=_repair_queue_blocked_detail(item.sku or sku, repair_blocker),
        )

    auth = EbayAuth()
    if not auth.is_configured():
        raise HTTPException(status_code=503, detail="eBay not configured")

    headers = _ebay_headers(auth)
    base = auth.api_base

    try:
        resp = ebay_http.delete(
            f"{base}/sell/inventory/v1/offer/{item.offer_id}/withdraw",
            headers=headers,
            timeout=30,
        )
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=502, detail=_parse_ebay_error(resp))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    item.status = "export_ready"
    repo.upsert(item)
    return {"sku": sku, "status": "export_ready", "message": "Listing ended successfully"}


# ── Bulk operations ────────────────────────────────────────────────────────────

class BulkPricePayload(BaseModel):
    skus: list[str]
    price: float


@router.post("/bulk/price")
def bulk_set_price(payload: BulkPricePayload, session: Session = Depends(get_session)):
    try:
        payload.skus = assert_route_skus_allowed(payload.skus, "listings.bulk_price", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    updated = []
    for sku in payload.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.list_price = payload.price
            repo.upsert(item)
            updated.append(sku)
    return {"updated": updated}


class BulkPromoPayload(BaseModel):
    skus: list[str]
    promotion_pct: float


@router.post("/bulk/promo")
def bulk_set_promo(payload: BulkPromoPayload, session: Session = Depends(get_session)):
    try:
        payload.skus = assert_route_skus_allowed(payload.skus, "listings.bulk_promo", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    updated = []
    for sku in payload.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.promotion_pct = payload.promotion_pct
            repo.upsert(item)
            updated.append(sku)
    return {"updated": updated}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ebay_headers(auth: EbayAuth) -> dict:
    return {
        "Authorization": f"Bearer {auth.user_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": auth.marketplace_id,
    }


def _compute_days_listed(r: ItemRecord) -> Optional[int]:
    if r.date_listed:
        try:
            if isinstance(r.date_listed, datetime):
                return (datetime.utcnow() - r.date_listed).days
            elif isinstance(r.date_listed, str):
                dt = datetime.fromisoformat(r.date_listed.replace("Z", ""))
                return (datetime.utcnow() - dt).days
        except Exception:
            pass
    return r.days_listed


def _resolve_condition(condition_id, condition_label) -> str:
    raw = str(condition_id or condition_label or "USED_GOOD")
    if raw in CONDITION_MAP:
        return CONDITION_MAP[raw]
    digits = re.sub(r"[^0-9]", "", raw)[:4]
    return CONDITION_MAP.get(digits, "USED_GOOD")


def _build_aspects(item) -> dict:
    import json as _json
    aspects: dict = {}
    if isinstance(item.item_specifics, dict):
        for k, v in item.item_specifics.items():
            if v:
                aspects[k] = [str(v)]
    elif isinstance(item.item_specifics, str):
        try:
            sp = _json.loads(item.item_specifics)
            for k, v in sp.items():
                if v:
                    aspects[k] = [str(v)]
        except Exception:
            pass
    # Standard fields
    for ebay_field, val in [
        ("Brand", item.brand), ("Type", item.type), ("Color", item.color),
        ("Size", item.size), ("Material", item.material), ("Style", item.style),
    ]:
        if val and ebay_field not in aspects:
            aspects[ebay_field] = [str(val)]
    return aspects


def _parse_ebay_error(resp) -> str:
    try:
        body = resp.json()
        errors = body.get("errors", [])
        if errors:
            e = errors[0]
            code = e.get("errorId", "")
            msg = e.get("longMessage") or e.get("message", "Unknown error")
            hint = EBAY_ERROR_HINTS.get(int(code) if code else 0, "")
            return f"Error {code}: {msg}" + (f" — {hint}" if hint else "")
        return f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"


def _build_revise_readiness(item) -> dict:
    publish_readiness = evaluate_publish_readiness(item).as_dict()
    auth_readiness = get_ebay_auth_readiness()
    publish_checks = list(publish_readiness.get("checks") or [])
    revise_ignored_check_names = {"publishable_status"}
    revise_ignored_actions = {
        "Move the item to approved or export_ready status before publishing.",
    }
    retained_publish_checks = [
        check for check in publish_checks if check.get("name") not in revise_ignored_check_names
    ]
    blockers = [
        check.get("detail", "")
        for check in retained_publish_checks
        if check.get("blocking") and not check.get("ok") and check.get("detail")
    ]
    warnings = list(dict.fromkeys(publish_readiness["warnings"]))
    required_actions = [
        action
        for action in publish_readiness["required_actions"]
        if action not in revise_ignored_actions
    ]
    checks = []

    def add_check(name: str, ok: bool, detail: str, *, blocking: bool = False, action: str | None = None, warning: str | None = None, context: dict | None = None) -> None:
        payload = {"name": name, "ok": ok, "blocking": blocking, "detail": detail}
        if context is not None:
            payload["context"] = context
        checks.append(payload)
        if blocking and not ok and detail not in blockers:
            blockers.append(detail)
        if warning and warning not in warnings:
            warnings.append(warning)
        if action and action not in required_actions:
            required_actions.append(action)

    listed_status_ok = (item.status or "") == "listed"
    add_check(
        "listed_status",
        listed_status_ok,
        "Item is in listed status." if listed_status_ok else f"Item status '{item.status}' is not listed.",
        blocking=True,
        action="Publish the item first so it has an active listing before revising.",
    )
    add_check(
        "listing_id_present",
        bool(str(item.listing_id or "").strip()),
        "Listing ID is present." if str(item.listing_id or "").strip() else "Listing ID is missing.",
        blocking=True,
        action="Sync or republish the item so a listing ID is stored locally.",
    )
    add_check(
        "offer_id_present",
        bool(str(item.offer_id or "").strip()),
        "Offer ID is present." if str(item.offer_id or "").strip() else "Offer ID is missing.",
        blocking=True,
        action="Sync the listing or republish the item so an offer ID is stored locally.",
    )
    auth_ok = bool(auth_readiness.get("checks", {}).get("access_token_present")) and not auth_readiness.get("blockers")
    add_check(
        "auth_readiness",
        auth_ok,
        auth_readiness.get("message", "eBay auth readiness evaluated."),
        blocking=not auth_ok,
        action=auth_readiness.get("next_action"),
        warning=(auth_readiness.get("warnings") or [None])[0],
        context={
            "code": auth_readiness.get("code"),
            "environment": auth_readiness.get("checks", {}).get("environment"),
            "token_source": auth_readiness.get("checks", {}).get("token_source"),
            "mutation_allowed": auth_readiness.get("checks", {}).get("mutation_allowed"),
        },
    )
    if auth_readiness.get("next_actions"):
        for action in auth_readiness["next_actions"]:
            if action not in required_actions:
                required_actions.append(action)

    return {
        "sku": (item.sku or "").upper(),
        "ready": len(blockers) == 0,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "required_actions": required_actions,
        "publish_readiness": publish_readiness,
        "auth_readiness": auth_readiness,
        "mutation_allowed": False,
    }


def _hosted_photo_urls(value) -> list[str]:
    from packages.ebay.src.public_image_urls import extract_public_image_urls

    return extract_public_image_urls(_image_paths_to_urls(value))


def _preview_policy_ids(seller_policy_check: dict | None) -> dict[str, str]:
    context = seller_policy_check.get("context", {}) if isinstance(seller_policy_check, dict) else {}
    configured = context.get("configured_policy_ids", {}) if isinstance(context, dict) else {}
    discovered = context.get("discovered_policy_ids", {}) if isinstance(context, dict) else {}
    return {
        "fulfillment_id": str(configured.get("fulfillment") or discovered.get("fulfillment") or ""),
        "payment_id": str(configured.get("payment") or discovered.get("payment") or ""),
        "return_id": str(configured.get("return") or discovered.get("return") or ""),
    }


def _image_paths_to_urls(value) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split("|") if p.strip()]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _get_or_create_merchant_location_key(base: str, headers: dict) -> str:
    cached = _LOCATION_KEY_CACHE.get(base)
    if cached:
        return cached

    list_resp = ebay_http.get(f"{base}/sell/inventory/v1/location", headers=headers, timeout=20)
    if list_resp.status_code == 200:
        locations = (list_resp.json() or {}).get("locations", [])
        if locations:
            first = locations[0] or {}
            key = first.get("merchantLocationKey")
            if key:
                _LOCATION_KEY_CACHE[base] = key
                return key
    elif list_resp.status_code not in (200, 404):
        raise RuntimeError(_parse_ebay_error(list_resp))

    create_payload = {
        "location": {
            "address": {
                "addressLine1": "123 Main St",
                "city": "Rome",
                "stateOrProvince": "NY",
                "postalCode": "13440",
                "country": "US",
            }
        },
        "locationTypes": ["WAREHOUSE"],
        "name": "Default Location",
        "merchantLocationStatus": "ENABLED",
    }
    create_resp = ebay_http.post(
        f"{base}/sell/inventory/v1/location/default",
        headers=headers,
        json=create_payload,
        timeout=20,
    )
    if create_resp.status_code not in (200, 201, 204):
        raise RuntimeError(_parse_ebay_error(create_resp))

    _LOCATION_KEY_CACHE[base] = "default"
    return "default"


def _touch_synced_at(db_path, sku: str, now: str) -> None:
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE items SET last_synced_at = ? WHERE sku = ?", (now, sku))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _fetch_inventory_items_for_skus(base: str, headers: dict, skus: list[str]) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []
    for sku in skus:
        try:
            resp = ebay_http.get(
                f"{base}/sell/inventory/v1/inventory_item/{sku}",
                headers=headers,
                timeout=15,
            )
        except Exception as exc:
            errors.append(f"{sku}: inventory lookup error: {exc}")
            continue
        if resp.status_code == 200:
            try:
                payload = resp.json() or {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["sku"] = payload.get("sku") or sku
            items.append(payload)
            continue
        if resp.status_code == 404:
            continue
        errors.append(f"{sku}: inventory lookup failed {resp.status_code}: {resp.text[:200]}")
    return items, errors


def _fetch_inventory_items_paginated(base: str, headers: dict) -> tuple[list[dict], list[str], int]:
    all_items: list[dict] = []
    errors: list[str] = []
    offset = 0
    limit = 25
    pages = 0
    started = time.monotonic()
    while True:
        if pages >= _LISTINGS_SYNC_MAX_PAGES:
            errors.append(f"sync pagination capped at {_LISTINGS_SYNC_MAX_PAGES} pages")
            break
        if time.monotonic() - started >= _LISTINGS_SYNC_MAX_SECONDS:
            errors.append(f"sync pagination timed out after {_LISTINGS_SYNC_MAX_SECONDS:.1f}s")
            break
        try:
            resp = ebay_http.get(
                f"{base}/sell/inventory/v1/inventory_item",
                headers=headers,
                params={"limit": limit, "offset": offset},
                timeout=20,
            )
        except Exception as exc:
            errors.append(f"inventory pagination error: {exc}")
            logger.warning("eBay inventory sync error: %s", exc)
            break
        if resp.status_code not in (200, 204):
            errors.append(f"inventory pagination failed {resp.status_code}: {resp.text[:200]}")
            logger.warning("eBay sync error %s: %s", resp.status_code, resp.text[:200])
            break
        pages += 1
        data = resp.json()
        batch = data.get("inventoryItems", [])
        all_items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_items, errors, pages
