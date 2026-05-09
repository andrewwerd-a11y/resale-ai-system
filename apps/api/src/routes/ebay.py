"""
eBay API routes — OAuth flow, publish listings, sync sold orders, check status.
"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session
from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from apps.api.src.services.ebay_auth_diagnostics import classify_ebay_auth_failure, get_ebay_auth_readiness
from apps.api.src.services.operation_diagnostics import (
    classify_exception,
    classify_ebay_error_payload,
    record_failure,
    record_success,
)
from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from apps.api.src.services.publish_repair import (
    bulk_apply_approved_fixes,
    bulk_draft_fixes,
    draft_fix_for_sku,
    get_repair_queue,
    get_repair_queue_detail,
    get_publish_repair_blocker,
    record_publish_blocked,
    record_publish_failure,
    recheck_repair_readiness,
)
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.condition_mapping import condition_id_to_inventory_enum
from packages.ebay.src import http_client as ebay_http
from packages.ebay.src.photo_uploader import PhotoUploader
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
    parse_sku_list,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class BulkDraftFixBody(BaseModel):
    skus: list[str]
    mode: str = "draft_only"
    allow_low_risk_auto_apply: bool = False
    allow_medium_risk_drafts: bool = True
    allow_high_risk_drafts: bool = True


class ApprovedRepairEntry(BaseModel):
    sku: str
    repair_plan_id: str
    approved: bool
    edited_value: dict | list | str | int | float | bool | None = None
    operator_label: str | None = None


class BulkApplyApprovedFixesBody(BaseModel):
    approvals: list[ApprovedRepairEntry]


def _try_update_category_stats(item, sold: bool = False, sold_price: float | None = None) -> None:
    try:
        category_id = str(item.ebay_category_id or "").strip()
        if not category_id:
            return
        from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
        CategorySpreadsheet().update_field_stats(
            category_id=category_id,
            item=item,
            sold=sold,
            sold_price=sold_price,
        )
    except Exception as exc:  # non-fatal
        logger.warning("Category stats update skipped for %s: %s", getattr(item, "sku", "?"), exc)

# ── OAuth 2.0 flow ────────────────────────────────────────────────────────────

@router.get("/oauth/start")
def oauth_start():
    """Redirect browser to eBay OAuth consent page."""
    auth = EbayAuth()
    url = auth.get_auth_url()
    return RedirectResponse(url)


@router.get("/oauth/callback")
def oauth_callback(code: str = "", error: str = "", error_description: str = ""):
    """Exchange authorization code for tokens and save to data/ebay_tokens.json."""
    if error or not code:
        msg = error_description or error or "No authorization code received."
        return HTMLResponse(_oauth_result_html(False, msg))
    auth = EbayAuth()
    try:
        tokens = auth.exchange_code_for_tokens(code)
        expires_at = tokens.get("expires_at", "")
        return HTMLResponse(_oauth_result_html(True, f"Tokens saved. Access token expires: {expires_at[:19].replace('T',' ')} UTC"))
    except Exception as exc:
        return HTMLResponse(_oauth_result_html(False, str(exc)))


@router.get("/oauth/status")
def oauth_status():
    """Return current OAuth token status."""
    auth = EbayAuth()
    return auth.get_token_status()


def _oauth_result_html(success: bool, message: str) -> str:
    color = "#5dcaa5" if success else "#f09595"
    heading = "eBay Connected!" if success else "OAuth Error"
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>eBay OAuth — Resale AI</title>
<style>body{{font-family:system-ui,sans-serif;background:#1a1a18;color:#d4d2c8;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.box{{background:#222220;border:1px solid #2c2c2a;border-radius:10px;padding:32px 40px;max-width:480px;text-align:center}}
h2{{color:{color};margin-bottom:12px}}p{{font-size:13px;color:#888780;margin-bottom:20px}}
a{{color:#7f77dd;text-decoration:none;font-size:13px}}</style></head>
<body><div class="box">
<h2>{heading}</h2><p>{message}</p>
<a href="/export">← Back to Export Center</a>
</div></body></html>"""


# ── Status ─────────────────────────────────────────────────────────────────────

def _looks_like_auth_failure(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        marker in lower
        for marker in (
            "invalid access token",
            "error 1001",
            "authorization http request header",
            "insufficient scope",
            "insufficient permissions",
            "refresh failed",
            "auth not ready",
            "missing token",
        )
    )


def _persist_partial_publish_state(item, result, repo) -> bool:
    recovered_offer_id = str(result.details.get("offer_id") or "").strip()
    if not recovered_offer_id:
        return False
    if item.offer_id == recovered_offer_id:
        return False
    item.offer_id = recovered_offer_id
    repo.upsert(item)
    return True


@router.get("/status")
def ebay_status():
    auth = EbayAuth()
    uploader = PhotoUploader()
    settings = get_settings()
    return {
        "configured": auth.is_configured(),
        "environment": settings.ebay_environment,
        "marketplace": settings.ebay_marketplace_id,
        "photo_hosting": uploader.is_configured(),
        "photo_host": "cloudinary" if uploader.is_configured() else "local_paths_only",
    }


@router.get("/auth-readiness")
def ebay_auth_readiness():
    return get_ebay_auth_readiness()


@router.get("/repair-queue")
def repair_queue(
    status: str = "",
    risk_level: str = "",
    repair_layer: str = "",
    sku: str = "",
    requires_review: str = "",
    session: Session = Depends(get_session),
):
    return {
        "entries": get_repair_queue(
            session,
            status=status,
            risk_level=risk_level,
            repair_layer=repair_layer,
            sku=sku,
            requires_review=requires_review,
        )
    }


@router.get("/repair-queue/{sku}")
def repair_queue_detail(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "ebay.repair_queue_detail")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return get_repair_queue_detail(session, sku)


@router.post("/repair-queue/{sku}/recheck-readiness")
def repair_queue_recheck_readiness(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "ebay.repair_queue_recheck")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return recheck_repair_readiness(session, sku)


@router.post("/repair-queue/{sku}/draft-fix")
def repair_queue_draft_fix(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "ebay.repair_queue_draft_fix")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return draft_fix_for_sku(session, sku)


@router.post("/repair-queue/{sku}/apply-draft-fix")
def repair_queue_apply_draft_fix(
    sku: str,
    body: ApprovedRepairEntry,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "ebay.repair_queue_apply_draft_fix")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    result = bulk_apply_approved_fixes(
        session,
        [
            {
                "sku": sku,
                "repair_plan_id": body.repair_plan_id,
                "approved": body.approved,
                "edited_value": body.edited_value,
                "operator_label": body.operator_label,
            }
        ],
    )
    if result["rejected"]:
        raise HTTPException(status_code=400, detail=result["rejected"][0]["detail"])
    return result["applied"][0]


@router.post("/repair-queue/bulk-draft-fixes")
def repair_queue_bulk_draft_fixes(
    body: BulkDraftFixBody,
    session: Session = Depends(get_session),
):
    selected = parse_sku_list(",".join(body.skus))
    if is_route_guard_enabled():
        try:
            assert_route_skus_allowed(selected, "ebay.repair_queue_bulk_draft_fixes", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    return bulk_draft_fixes(
        session,
        skus=body.skus,
        allow_low_risk_auto_apply=body.allow_low_risk_auto_apply,
        allow_medium_risk_drafts=body.allow_medium_risk_drafts,
        allow_high_risk_drafts=body.allow_high_risk_drafts,
    )


@router.post("/repair-queue/bulk-apply-approved-fixes")
def repair_queue_bulk_apply_approved_fixes(
    body: BulkApplyApprovedFixesBody,
    session: Session = Depends(get_session),
):
    selected = parse_sku_list(",".join([entry.sku for entry in body.approvals]))
    if is_route_guard_enabled():
        try:
            assert_route_skus_allowed(selected, "ebay.repair_queue_bulk_apply_approved_fixes", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    return bulk_apply_approved_fixes(session, [entry.model_dump() for entry in body.approvals])

@router.post("/publish/batch")
def publish_batch(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    from packages.ebay.src.inventory_client import EbayInventoryClient
    import datetime
    import uuid
    selected = parse_sku_list(skus)
    batch_id = f"publish-batch-{uuid.uuid4().hex[:12]}"
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "ebay.publish_batch", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    repo = ItemRepository(session)
    items = repo.list_by_status(ItemStatus.EXPORT_READY) + repo.list_by_status(ItemStatus.APPROVED)
    if selected:
        allowed = set(selected)
        items = [item for item in items if (item.sku or "").upper() in allowed]
    if not items:
        record_success(
            session,
            operation_name="ebay_publish_batch",
            route="/api/ebay/publish/batch",
            batch_id=batch_id,
            safe_message="No items were ready to publish.",
            mutation_attempted=False,
            mutation_succeeded=False,
            ebay_mutation_attempted=False,
            ebay_mutation_succeeded=False,
            external_service="local",
            result_context={"selected_skus": selected, "count": 0},
        )
        return {"message": "No items ready to publish", "count": 0}
    client = EbayInventoryClient()
    results = {"published": 0, "failed": 0, "skipped": 0, "errors": [], "skipped_skus": []}
    for item in items:
        repair_blocker = get_publish_repair_blocker(session, item.sku or "")
        if repair_blocker["blocked_by_repair_queue"]:
            record_failure(
                session,
                operation_name="ebay_publish_batch_item",
                route="/api/ebay/publish/batch",
                sku=item.sku,
                batch_id=batch_id,
                status="blocked",
                safe_message="Publish skipped because repair queue blocks retry.",
                external_service="local",
                stage="preflight_repair_queue",
                error_family="publish_repair_queue",
                error_code=repair_blocker["classified_error_code"] or "blocked_by_repair_queue",
                recommended_next_action="Resolve or supersede the repair queue blocker before publishing.",
                result_context={"repair_plan_id": repair_blocker["repair_plan_id"], "retry_allowed": repair_blocker["retry_allowed"]},
            )
            results["skipped"] += 1
            results["skipped_skus"].append(
                {
                    "sku": item.sku,
                    "code": "blocked_by_repair_queue",
                    "reason": repair_blocker["reason"],
                    "repair_plan_id": repair_blocker["repair_plan_id"],
                    "latest_publish_attempt_id": repair_blocker["latest_publish_attempt_id"],
                    "repair_status": repair_blocker["repair_status"],
                    "retry_allowed": repair_blocker["retry_allowed"],
                    "classified_error_code": repair_blocker["classified_error_code"],
                    "suggested_actions": repair_blocker["suggested_actions"],
                }
            )
            continue
        result = client.publish_item(item)
        if result.ok:
            data = result.value
            item.listing_id = data["listing_id"]
            item.listing_url = data["listing_url"]
            item.offer_id = data.get("offer_id") or ""
            item.status = ItemStatus.LISTED
            item.platform = "ebay"
            item.date_listed = datetime.datetime.utcnow()
            if data["photo_urls"]:
                item.image_paths = data["photo_urls"]
            repo.upsert(item)
            _try_update_category_stats(item, sold=False)
            record_success(
                session,
                operation_name="ebay_publish_batch_item",
                route="/api/ebay/publish/batch",
                sku=item.sku,
                batch_id=batch_id,
                safe_message="Batch publish item succeeded.",
                mutation_attempted=True,
                mutation_succeeded=True,
                ebay_mutation_attempted=True,
                ebay_mutation_succeeded=True,
                external_service="ebay",
                stage="publish_offer",
                result_context={"listing_id": data.get("listing_id"), "offer_id": data.get("offer_id"), "photos_uploaded": len(data.get("photo_urls") or [])},
            )
            results["published"] += 1
        else:
            results["failed"] += 1
            recovered_offer_saved = _persist_partial_publish_state(item, result, repo)
            ebay_classification = classify_ebay_error_payload(result.details.get("body") or result.error or "")
            stage = str(result.details.get("stage") or "")
            ebay_attempted = bool(stage or result.error_code == "API_ERROR")
            record_failure(
                session,
                operation_name="ebay_publish_batch_item",
                route="/api/ebay/publish/batch",
                sku=item.sku,
                batch_id=batch_id,
                safe_message=result.error or "Batch publish item failed.",
                mutation_attempted=ebay_attempted,
                mutation_succeeded=False,
                ebay_mutation_attempted=ebay_attempted,
                ebay_mutation_succeeded=False,
                external_service=ebay_classification["external_service"],
                stage=stage or "publish_item",
                error_family=ebay_classification["error_family"],
                error_code=ebay_classification["error_code"],
                raw_error_summary=ebay_classification["raw_error_summary"],
                raw_error_payload=ebay_classification["raw_error_payload"],
                recommended_next_action=result.details.get("next_action") or ebay_classification["recommended_next_action"],
                result_context={"recovered_offer_saved": recovered_offer_saved, "error_code": result.error_code},
            )
            error_detail = result.error or "unknown error"
            if result.details.get("body"):
                error_detail += f" | eBay: {result.details['body']}"
            if recovered_offer_saved:
                error_detail += f" | recovered offer_id: {item.offer_id}"
            results["errors"].append(f"{item.sku}: {error_detail}")
    return results

@router.post("/publish/{sku}")
def publish_item(sku: str, session: Session = Depends(get_session)):
    from packages.ebay.src.inventory_client import EbayInventoryClient
    import datetime
    try:
        assert_route_sku_allowed(sku, "ebay.publish_item")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku=sku,
            status="blocked",
            safe_message=f"Item {sku} not found.",
            external_service="local",
            stage="local_lookup",
            error_family="missing_local_item",
            error_code="not_found",
            recommended_next_action="Create or import the item before publishing.",
        )
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if item.status not in (ItemStatus.APPROVED, ItemStatus.EXPORT_READY):
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku=sku,
            status="blocked",
            safe_message=f"Item must be approved. Current status: {item.status}",
            external_service="local",
            stage="preflight_status",
            error_family="publish_readiness",
            error_code="status_not_publishable",
            recommended_next_action="Move the item to approved or export_ready before publishing.",
            result_context={"status": item.status},
        )
        raise HTTPException(status_code=400, detail=f"Item must be approved. Current status: {item.status}")

    repair_blocker = get_publish_repair_blocker(session, item.sku or sku)
    if repair_blocker["blocked_by_repair_queue"]:
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku=item.sku or sku,
            status="blocked",
            safe_message="Publish blocked by repair queue before mutation.",
            external_service="local",
            stage="preflight_repair_queue",
            error_family="publish_repair_queue",
            error_code=repair_blocker["classified_error_code"] or "blocked_by_repair_queue",
            recommended_next_action="Resolve or supersede the repair queue blocker before publishing.",
            result_context={"repair_plan_id": repair_blocker["repair_plan_id"], "retry_allowed": repair_blocker["retry_allowed"]},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "blocked_by_repair_queue",
                "sku": (item.sku or sku).upper(),
                "blocked_by_repair_queue": True,
                "repair_plan_id": repair_blocker["repair_plan_id"],
                "latest_publish_attempt_id": repair_blocker["latest_publish_attempt_id"],
                "repair_status": repair_blocker["repair_status"],
                "retry_allowed": repair_blocker["retry_allowed"],
                "classified_error_code": repair_blocker["classified_error_code"],
                "suggested_actions": repair_blocker["suggested_actions"],
                "reason": repair_blocker["reason"],
            },
        )

    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)
    preflight_blockers = list(dict.fromkeys(readiness["blockers"] + compatibility["blockers"]))
    if preflight_blockers:
        repair_record = record_publish_blocked(
            session,
            item,
            blockers=preflight_blockers,
            readiness=readiness,
            compatibility=compatibility,
        )
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku=sku,
            status="blocked",
            safe_message="Publish readiness blocked before mutation.",
            external_service="local",
            stage="preflight_readiness",
            error_family="publish_readiness",
            error_code="publish_readiness_blocked",
            raw_error_payload={"blockers": preflight_blockers},
            recommended_next_action="Resolve publish readiness blockers before publishing.",
            result_context={"repair_plan_ids": [plan.get("id") for plan in repair_record.get("repair_plan", [])]},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "publish_readiness_blocked",
                "sku": sku,
                "blockers": preflight_blockers,
                "repair_queue_entry_created": True,
                "repair_plan": repair_record["repair_plan"],
            },
        )

    client = EbayInventoryClient()
    result = client.publish_item(item)
    if not result.ok:
        repair_record = record_publish_failure(
            session,
            item,
            result=result,
            request_summary={"sku": sku, "mode": "single_publish"},
        )
        recovered_offer_saved = _persist_partial_publish_state(item, result, repo)
        auth_issue = result.details.get("auth_issue_code")
        body = str(result.details.get("body") or "")
        ebay_classification = classify_ebay_error_payload(body or str(result.error or ""))
        stage = str(result.details.get("stage") or "")
        ebay_attempted = bool(stage or result.error_code == "API_ERROR")
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku=sku,
            safe_message=result.error or "eBay publish failed.",
            mutation_attempted=ebay_attempted,
            mutation_succeeded=False,
            ebay_mutation_attempted=ebay_attempted,
            ebay_mutation_succeeded=False,
            external_service=ebay_classification["external_service"],
            stage=stage or "publish_item",
            error_family=ebay_classification["error_family"],
            error_code=ebay_classification["error_code"],
            raw_error_summary=ebay_classification["raw_error_summary"],
            raw_error_payload=ebay_classification["raw_error_payload"],
            recommended_next_action=result.details.get("next_action") or ebay_classification["recommended_next_action"],
            result_context={
                "repair_plan_ids": [plan.get("id") for plan in repair_record.get("repair_plan", [])],
                "recovered_offer_saved": recovered_offer_saved,
                "result_error_code": result.error_code,
            },
        )
        if result.error_code == "INVALID_IMAGE_URL":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_image_url",
                    "message": result.error or "Invalid public image URL detected before eBay publish.",
                    "invalid_image_urls": result.details.get("invalid_image_urls") or [],
                    "blockers": result.details.get("blockers") or [],
                    "next_action": "Host photos again or correct the stored hosted photo URLs before retrying publish.",
                },
            )
        if result.error_code == "AUTH_NOT_READY" or _looks_like_auth_failure(body) or _looks_like_auth_failure(str(result.error or "")):
            detail = classify_ebay_auth_failure(
                status_code=401,
                text=body or str(result.error or ""),
                auth_readiness=get_ebay_auth_readiness(),
                token_issue_code=str(auth_issue or ""),
            )
            raise HTTPException(
                status_code=503 if result.error_code == "AUTH_NOT_READY" else 502,
                detail={
                    "code": "ebay_publish_failed",
                    "sku": sku,
                    "stage": str(result.details.get("stage") or ""),
                    "raw_ebay_errors": [body] if body else [str(result.error or "")],
                    "classified_error": detail,
                    "repair_plan": repair_record["repair_plan"],
                    "retry_allowed": False,
                    "requires_review": True,
                },
            )
        raw_errors = [body] if body else [str(result.error or "unknown error")]
        error_detail = {
            "code": "ebay_publish_failed",
            "sku": sku,
            "stage": str(result.details.get("stage") or ""),
            "raw_ebay_errors": raw_errors,
            "classified_error": repair_record["classified_error"],
            "repair_plan": repair_record["repair_plan"],
            "retry_allowed": False,
            "requires_review": bool(repair_record["classified_error"]["requires_review"]),
        }
        if any(
            result.details.get(key)
            for key in ("local_condition_id", "inventory_condition_enum", "category_id", "offer_id")
        ):
            offer_id = str(result.details.get("offer_id") or item.offer_id or "")
            existing_offer_id_detected = bool(
                offer_id
                and not str(item.listing_id or "").strip()
                and str(item.status or "") != "listed"
            )
            stage = str(result.details.get("stage") or "")
            stale_existing_offer_hypothesis = bool(existing_offer_id_detected and stage == "publish_offer")
            error_detail["condition_diagnostics"] = {
                "local_condition_id": str(result.details.get("local_condition_id") or ""),
                "inventory_condition_enum": str(result.details.get("inventory_condition_enum") or ""),
                "category_id": str(result.details.get("category_id") or ""),
                "current_category_id": str(item.ebay_category_id or ""),
                "current_condition_id": str(item.condition_id or ""),
                "previous_category_id": str(result.details.get("previous_category_id") or ""),
                "previous_condition_id": str(result.details.get("previous_condition_id") or ""),
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
            }
        if recovered_offer_saved:
            error_detail["recovered_offer_id"] = item.offer_id
        if result.details.get("next_action"):
            error_detail["next_action"] = result.details["next_action"]
        raise HTTPException(status_code=500, detail=error_detail)
    data = result.value
    item.listing_id = data["listing_id"]
    item.listing_url = data["listing_url"]
    item.offer_id = data.get("offer_id") or ""
    item.status = ItemStatus.LISTED
    item.platform = "ebay"
    item.date_listed = datetime.datetime.utcnow()
    if data["photo_urls"]:
        item.image_paths = data["photo_urls"]
    repo.upsert(item)
    _try_update_category_stats(item, sold=False)
    record_success(
        session,
        operation_name="ebay_publish",
        route="/api/ebay/publish/{sku}",
        sku=sku,
        safe_message="eBay publish succeeded.",
        mutation_attempted=True,
        mutation_succeeded=True,
        ebay_mutation_attempted=True,
        ebay_mutation_succeeded=True,
        external_service="ebay",
        stage="publish_offer",
        result_context={"listing_id": data.get("listing_id"), "offer_id": data.get("offer_id"), "photos_uploaded": len(data.get("photo_urls") or [])},
    )
    return {
        "sku": sku,
        "listing_id": data["listing_id"],
        "listing_url": data["listing_url"],
        "offer_id": data.get("offer_id"),
        "recovered_existing_offer": bool(data.get("recovered_existing_offer")),
        "used_existing_offer": bool(data.get("used_existing_offer")),
        "status": "listed",
        "photos_uploaded": len(data["photo_urls"]),
    }

@router.post("/sync-sold")
def sync_sold(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    from packages.ebay.src.sold_sync import SoldSync
    selected = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "ebay.sync_sold", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    sync = SoldSync()
    try:
        stats = sync.reconcile(session, allowed_skus=set(selected) if selected else None)
    except Exception as exc:
        classification = classify_exception(exc)
        record_failure(
            session,
            operation_name="ebay_sync_sold",
            route="/api/ebay/sync-sold",
            status="failed",
            safe_message=classification["safe_message"],
            mutation_attempted=True,
            mutation_succeeded=False,
            ebay_mutation_attempted=False,
            ebay_mutation_succeeded=False,
            external_service=classification["external_service"],
            stage="sync_sold",
            error_family=classification["error_family"],
            error_code=classification["error_code"],
            raw_error_summary=classification["raw_error_summary"],
            raw_error_payload=classification,
            recommended_next_action=classification["recommended_next_action"],
            request_context={"selected_skus": selected, "e2e_only": e2e_only},
        )
        raise
    record_success(
        session,
        operation_name="ebay_sync_sold",
        route="/api/ebay/sync-sold",
        safe_message="Sold sync completed.",
        mutation_attempted=True,
        mutation_succeeded=True,
        ebay_mutation_attempted=False,
        ebay_mutation_succeeded=False,
        external_service="database",
        stage="sync_sold",
        request_context={"selected_skus": selected, "e2e_only": e2e_only},
        result_context=stats,
    )
    return stats

@router.post("/mark-sold/{sku}")
def mark_sold_manual(
    sku: str,
    sold_price: float,
    fees: float = 0.0,
    platform: str = "ebay",
    session: Session = Depends(get_session),
):
    """Manually mark an item as sold with price and fees. Creates a SaleRecord."""
    try:
        assert_route_sku_allowed(sku, "ebay.mark_sold")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    from packages.sync.src.cross_platform_sync import CrossPlatformSync
    sync = CrossPlatformSync()
    result = sync.mark_sold(sku, platform, sold_price, fees, session)
    if not result.ok:
        status = 404 if "not found" in (result.error or "") else 500
        raise HTTPException(status_code=status, detail=result.error)
    item = ItemRepository(session).get_by_sku(sku)
    if item:
        _try_update_category_stats(item, sold=True, sold_price=sold_price)
    # Notify
    try:
        from packages.notifications.src.notifier import Notifier
        Notifier().notify_sale(sku, sold_price, platform)
    except Exception:
        pass
    return result.value


@router.patch("/listing/{sku}")
def update_listing(sku: str, updates: dict, session: Session = Depends(get_session)):
    """
    Update a live eBay listing after publish.
    Uses PUT /sell/inventory/v1/inventory_item/{sku} to update fields.
    Allowed updates: title, description, price, item_specifics.
    Does NOT update: category_id, condition (these require relisting).
    Returns success or eBay error details.
    """
    import json
    from packages.ebay.src.inventory_client import EbayInventoryClient
    try:
        assert_route_sku_allowed(sku, "ebay.update_listing")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if item.status != ItemStatus.LISTED:
        raise HTTPException(status_code=400, detail=f"Item {sku} is not listed (status: {item.status})")

    # Apply allowed updates to the item
    allowed_fields = {"title_final", "description_final", "list_price", "item_specifics"}
    for k, v in updates.items():
        if k in allowed_fields and hasattr(item, k):
            setattr(item, k, v)

    client = EbayInventoryClient()
    token_state = client.auth.resolve_user_token()
    if not (client.auth.settings.ebay_app_id and client.auth.settings.ebay_cert_id and token_state["token"]):
        readiness = get_ebay_auth_readiness()
        if token_state["issue_code"] == "refresh_failed":
            readiness["code"] = "refresh_failed"
            readiness["message"] = token_state["issue_message"] or readiness["message"]
            readiness["next_action"] = "Reconnect eBay OAuth or replace the expired token, then retry."
        raise HTTPException(status_code=503, detail=readiness)

    auth = client.auth
    base = auth.api_base
    headers = {
        "Authorization": f"Bearer {token_state['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": auth.marketplace_id,
    }

    # Build minimal inventory item payload with updated fields
    import re
    raw_cond = str(item.condition_id or "5000")
    digits_only = re.sub(r"[^0-9]", "", raw_cond)[:4]
    condition = condition_id_to_inventory_enum(digits_only, default="USED_GOOD")

    aspects: dict = {}
    if isinstance(item.item_specifics, dict):
        for k, v in item.item_specifics.items():
            if v:
                aspects[k] = [str(v)]

    product: dict = {
        "title": (item.title_final or item.title_raw or "")[:80],
        "description": item.description_final or item.title_final or "",
        "aspects": aspects,
    }
    inventory_payload = {
        "product": product,
        "condition": condition,
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }

    resp = ebay_http.put(
        f"{base}/sell/inventory/v1/inventory_item/{sku}",
        headers=headers,
        json=inventory_payload,
        timeout=30,
    )
    if resp.status_code not in (200, 204):
        detail = classify_ebay_auth_failure(
            status_code=resp.status_code,
            text=resp.text[:500],
            auth_readiness=get_ebay_auth_readiness(),
            token_issue_code=str(token_state["issue_code"] or ""),
        )
        if detail["code"] == "unknown_auth_error" and resp.status_code not in (401, 403):
            detail = {
                "code": "revise_failed",
                "category": "ebay",
                "message": f"eBay API {resp.status_code}: {resp.text[:300]}",
                "next_action": "Review the eBay response and retry after fixing the listing data or auth state.",
            }
        raise HTTPException(status_code=502, detail=detail)

    # Persist updated fields
    from datetime import datetime
    item.updated_at = datetime.utcnow()
    repo.upsert(item)
    return {"sku": sku, "updated": True, "fields": list(updates.keys())}


@router.get("/listings")
def get_active_listings(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    items = repo.list_by_status(ItemStatus.LISTED)
    return [{"sku": i.sku, "title": i.title_final, "listing_id": i.listing_id, "listing_url": i.listing_url, "list_price": i.list_price, "date_listed": i.date_listed} for i in items]
