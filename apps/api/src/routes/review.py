from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository

router = APIRouter()


@router.get("")
def list_review_queue(session: Session = Depends(get_session)):
    from sqlmodel import select
    from packages.data.src.models.item_record import ItemRecord
    from packages.data.src.repositories.item_repo import _from_record
    stmt = select(ItemRecord).where(
        (ItemRecord.status == "needs_review") | (ItemRecord.needs_review == True)
    )
    records = session.exec(stmt).all()
    return [_from_record(r).model_dump() for r in records]


@router.post("/{sku}/approve")
def approve_item(sku: str, session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    item.needs_review = False
    item.status = ItemStatus.EXPORT_READY
    repo.upsert(item)
    return {"sku": sku, "status": ItemStatus.EXPORT_READY}


@router.post("/{sku}/reject")
def reject_item(sku: str, session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    ok = repo.update_status(sku, ItemStatus.REJECTED)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    return {"sku": sku, "status": ItemStatus.REJECTED}


@router.patch("/{sku}/edit")
def edit_and_approve(sku: str, updates: dict, session: Session = Depends(get_session)):
    """Edit fields and immediately approve."""
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    for k, v in updates.items():
        if hasattr(item, k):
            setattr(item, k, v)
    item.manual_override = True
    item.needs_review = False
    item.status = ItemStatus.EXPORT_READY
    saved = repo.upsert(item)
    return saved.model_dump()
