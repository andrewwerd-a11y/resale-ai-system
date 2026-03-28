"""
Phase 3 eBay API routes.

GET  /api/ebay/status          → config check, environment, photo hosting status
POST /api/ebay/publish/{sku}   → publish single item, update DB status to listed
POST /api/ebay/publish/batch   → publish all approved + export_ready items
POST /api/ebay/sync-sold       → run sold sync reconciliation
GET  /api/ebay/listings        → return all listed items
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src import auth
from packages.ebay.src.inventory_client import publish_item
from packages.ebay.src.sold_sync import reconcile
from packages.core.src.config import get_settings

router = APIRouter()


@router.get("/status")
def ebay_status():
    """Return eBay configuration and connectivity status."""
    settings = get_settings()
    configured = auth.is_configured()
    imgur_configured = bool(settings.imgur_client_id)

    return {
        "configured": configured,
        "environment": auth.environment_name(),
        "marketplace_id": settings.ebay_marketplace_id,
        "photo_hosting": "imgur" if imgur_configured else "none",
        "photo_hosting_configured": imgur_configured,
        "sandbox": settings.is_sandbox,
        "app_id_set": bool(settings.ebay_app_id),
        "user_token_set": bool(settings.ebay_user_token),
    }


@router.post("/publish/{sku}")
def publish_single(sku: str):
    """Publish a single item to eBay."""
    sku = sku.upper()

    with get_session() as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)

    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    if item.status not in ("approved", "export_ready", "exported"):
        raise HTTPException(
            status_code=400,
            detail=f"Item {sku} status is '{item.status}' — must be approved, export_ready, or exported to publish",
        )

    result = publish_item(item)
    if result.is_err:
        raise HTTPException(status_code=502, detail=result.error)

    data = result.value

    with get_session() as session:
        repo = ItemRepository(session)
        repo.update_ebay(
            sku=sku,
            listing_id=data["listing_id"],
            offer_id=data["offer_id"],
            listing_url=data["listing_url"],
            status="listed",
        )
        # Also save hosted photo URLs if available
        if data.get("photo_urls"):
            item_refreshed = repo.get_by_sku(sku)
            if item_refreshed:
                updated = item_refreshed.model_copy(update={"hosted_photo_urls": data["photo_urls"]})
                repo.upsert(updated)

    return {
        "sku": sku,
        "status": "listed",
        "listing_id": data["listing_id"],
        "offer_id": data["offer_id"],
        "listing_url": data["listing_url"],
    }


@router.post("/publish/batch")
def publish_batch():
    """Publish all approved and export_ready items to eBay."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_statuses(["approved", "export_ready"])

    if not items:
        return {"message": "No items ready to publish", "published": 0, "failed": 0}

    published = 0
    failed = 0
    errors: list[dict] = []

    for item in items:
        result = publish_item(item)
        if result.is_err:
            failed += 1
            errors.append({"sku": item.sku, "error": result.error})
            continue

        data = result.value
        with get_session() as session:
            repo = ItemRepository(session)
            repo.update_ebay(
                sku=item.sku,
                listing_id=data["listing_id"],
                offer_id=data["offer_id"],
                listing_url=data["listing_url"],
                status="listed",
            )
        published += 1

    return {
        "published": published,
        "failed": failed,
        "total": len(items),
        "errors": errors[:20],  # Cap error list
    }


@router.post("/sync-sold")
def sync_sold():
    """Fetch recent eBay orders and reconcile sold status in DB."""
    stats = reconcile(days_back=30)
    return stats


@router.get("/listings")
def get_listings():
    """Return all currently listed items."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_status("listed")

    return [
        {
            "sku": i.sku,
            "title": i.title,
            "category": i.category,
            "list_price": i.list_price,
            "ebay_listing_id": i.ebay_listing_id,
            "ebay_listing_url": i.ebay_listing_url,
            "date_listed": i.date_listed.isoformat() if i.date_listed else None,
        }
        for i in items
    ]
