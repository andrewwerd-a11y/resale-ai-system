"""
eBay API routes — OAuth flow, publish listings, sync sold orders, check status.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.photo_uploader import PhotoUploader

router = APIRouter()

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

@router.post("/publish/batch")
def publish_batch(session: Session = Depends(get_session)):
    from packages.ebay.src.inventory_client import EbayInventoryClient
    import datetime
    repo = ItemRepository(session)
    items = repo.list_by_status(ItemStatus.EXPORT_READY) + repo.list_by_status(ItemStatus.APPROVED)
    if not items:
        return {"message": "No items ready to publish", "count": 0}
    client = EbayInventoryClient()
    results = {"published": 0, "failed": 0, "errors": []}
    for item in items:
        result = client.publish_item(item)
        if result.ok:
            data = result.value
            item.listing_id = data["listing_id"]
            item.listing_url = data["listing_url"]
            item.status = ItemStatus.LISTED
            item.platform = "ebay"
            item.date_listed = datetime.datetime.utcnow()
            if data["photo_urls"]:
                item.image_paths = data["photo_urls"]
            repo.upsert(item)
            results["published"] += 1
        else:
            results["failed"] += 1
            error_detail = result.error or "unknown error"
            if result.details.get("body"):
                error_detail += f" | eBay: {result.details['body']}"
            results["errors"].append(f"{item.sku}: {error_detail}")
    return results

@router.post("/publish/{sku}")
def publish_item(sku: str, session: Session = Depends(get_session)):
    from packages.ebay.src.inventory_client import EbayInventoryClient
    import datetime
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if item.status not in (ItemStatus.APPROVED, ItemStatus.EXPORT_READY):
        raise HTTPException(status_code=400, detail=f"Item must be approved. Current status: {item.status}")
    client = EbayInventoryClient()
    result = client.publish_item(item)
    if not result.ok:
        detail = result.error or "unknown error"
        if result.details.get("body"):
            detail += f" | eBay response: {result.details['body']}"
        raise HTTPException(status_code=500, detail=detail)
    data = result.value
    item.listing_id = data["listing_id"]
    item.listing_url = data["listing_url"]
    item.status = ItemStatus.LISTED
    item.platform = "ebay"
    item.date_listed = datetime.datetime.utcnow()
    if data["photo_urls"]:
        item.image_paths = data["photo_urls"]
    repo.upsert(item)
    return {"sku": sku, "listing_id": data["listing_id"], "listing_url": data["listing_url"], "status": "listed", "photos_uploaded": len(data["photo_urls"])}

@router.post("/sync-sold")
def sync_sold(session: Session = Depends(get_session)):
    from packages.ebay.src.sold_sync import SoldSync
    sync = SoldSync()
    stats = sync.reconcile(session)
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
    from packages.sync.src.cross_platform_sync import CrossPlatformSync
    sync = CrossPlatformSync()
    result = sync.mark_sold(sku, platform, sold_price, fees, session)
    if not result.ok:
        status = 404 if "not found" in (result.error or "") else 500
        raise HTTPException(status_code=status, detail=result.error)
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
    if not client.auth.is_configured():
        raise HTTPException(status_code=503, detail="eBay credentials not configured")

    auth = client.auth
    base = auth.api_base
    headers = {
        "Authorization": f"Bearer {auth.user_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": auth.marketplace_id,
    }

    # Build minimal inventory item payload with updated fields
    import re
    CONDITION_MAP = {
        "1000": "NEW", "1500": "NEW_OTHER", "2000": "NEW_WITH_DEFECTS",
        "2500": "NEW_OTHER", "3000": "LIKE_NEW", "4000": "VERY_GOOD",
        "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE",
        "7000": "FOR_PARTS_OR_NOT_WORKING",
    }
    raw_cond = str(item.condition_id or "5000")
    digits_only = re.sub(r"[^0-9]", "", raw_cond)[:4]
    condition = CONDITION_MAP.get(digits_only, "USED_GOOD")

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

    import httpx
    resp = httpx.put(
        f"{base}/sell/inventory/v1/inventory_item/{sku}",
        headers=headers,
        json=inventory_payload,
        timeout=30,
    )
    if resp.status_code not in (200, 204):
        detail = f"eBay API {resp.status_code}: {resp.text[:300]}"
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
