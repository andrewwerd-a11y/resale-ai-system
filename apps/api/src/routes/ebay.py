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


@router.get("/listings")
def get_active_listings(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    items = repo.list_by_status(ItemStatus.LISTED)
    return [{"sku": i.sku, "title": i.title_final, "listing_id": i.listing_id, "listing_url": i.listing_url, "list_price": i.list_price, "date_listed": i.date_listed} for i in items]
