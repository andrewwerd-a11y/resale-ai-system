"""
eBay API routes — publish listings, sync sold orders, check status.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.photo_uploader import PhotoUploader

router = APIRouter()

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
            results["errors"].append(f"{item.sku}: {result.error}")
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
        raise HTTPException(status_code=500, detail=result.error)
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
