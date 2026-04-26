"""
Sync endpoints — automatic relisting of ended listings.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
    parse_sku_list,
)

router = APIRouter()


@router.get("/ended-listings")
def get_ended_listings(session: Session = Depends(get_session)):
    from packages.sync.src.relister import AutoRelister
    relister = AutoRelister()
    items = relister.get_ended_listings(session)
    return [
        {
            "sku": i.sku,
            "title": i.title_final,
            "listing_id": i.listing_id,
            "list_price": i.list_price,
            "days_listed": i.days_listed,
        }
        for i in items
    ]


@router.post("/relist/{sku}")
def relist_item(
    sku: str,
    price_adjustment: float = -0.10,
    session: Session = Depends(get_session),
):
    from packages.data.src.repositories.item_repo import ItemRepository
    from packages.sync.src.relister import AutoRelister

    try:
        assert_route_sku_allowed(sku, "sync.relist")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    relister = AutoRelister()
    result = relister.relist(item, price_adjustment)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)

    item.listing_id = result.value
    item.status = "listed"
    item.date_listed = datetime.datetime.utcnow()
    repo.upsert(item)
    return {"sku": sku, "listing_id": result.value, "new_price": item.list_price}


@router.post("/relist-all")
def relist_all(
    skus: str = "",
    e2e_only: bool = False,
    price_adjustment: float = -0.10,
    session: Session = Depends(get_session),
):
    from packages.data.src.repositories.item_repo import ItemRepository
    from packages.sync.src.relister import AutoRelister

    selected = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "sync.relist_all", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    relister = AutoRelister()
    repo = ItemRepository(session)
    ended = relister.get_ended_listings(session)
    if selected:
        allowed = set(selected)
        ended = [item for item in ended if (item.sku or "").upper() in allowed]

    results: dict = {"relisted": 0, "failed": 0, "errors": []}
    for item in ended:
        result = relister.relist(item, price_adjustment)
        if result.ok:
            item.listing_id = result.value
            item.status = "listed"
            item.date_listed = datetime.datetime.utcnow()
            repo.upsert(item)
            results["relisted"] += 1
        else:
            results["failed"] += 1
            results["errors"].append(f"{item.sku}: {result.error}")

    return results
