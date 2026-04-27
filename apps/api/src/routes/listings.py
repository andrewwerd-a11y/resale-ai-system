"""
Listings API — active eBay listings management (Phase 5B).
Revision, sync, push-to-eBay, and takedown for listed/exported items.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.src.services.ebay_auth_diagnostics import get_ebay_auth_readiness
from apps.api.src.services.publish_readiness import evaluate_publish_readiness, not_found_publish_readiness
from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import get_session
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src import http_client as ebay_http
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
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
    "2500": "NEW_OTHER", "3000": "LIKE_NEW", "4000": "VERY_GOOD",
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

    mutation_allowed = False
    mutation_reasons = ["Publish preview is read-only in this phase; no sandbox or live mutation is performed."]
    if not is_live_e2e_enabled():
        mutation_reasons.append("ALLOW_LIVE_E2E is false, so live mutation remains blocked.")

    return {
        "sku": (item.sku or "").upper(),
        "readiness": readiness,
        "would_publish": readiness["ready"],
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
    not_found = []
    now = datetime.utcnow().isoformat()
    cfg = get_settings()

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

        _touch_synced_at(cfg.db_path, sku, now)

    result: dict = {"synced": synced, "updated": updated, "not_found": not_found}
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
    image_urls = _image_paths_to_urls(item.image_paths)
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
    return [url for url in _image_paths_to_urls(value) if url.startswith("http://") or url.startswith("https://")]


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
