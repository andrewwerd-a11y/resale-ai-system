from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.intake.src.quality_gate import apply_intake_quality_to_item, evaluate_intake_quality
from packages.testing.src.e2e_guard import E2ESafetyError, assert_route_sku_allowed

router = APIRouter()


def _approval_block_detail(item) -> dict | None:
    quality = evaluate_intake_quality(item)
    apply_intake_quality_to_item(item, quality)
    if not quality.should_block_publish_approval:
        return None
    return {
        "code": "intake_quality_blocked",
        "sku": item.sku,
        "message": "Item cannot be approved until intake quality blockers are resolved.",
        "next_action": quality.suggested_next_uploads[0] if quality.suggested_next_uploads else quality.reason,
        "intake_quality": quality.as_dict(),
    }


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
    try:
        assert_route_sku_allowed(sku, "review.approve_item")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    block = _approval_block_detail(item)
    if block:
        repo.upsert(item)
        raise HTTPException(status_code=409, detail=block)
    item.needs_review = False
    item.status = ItemStatus.EXPORT_READY
    repo.upsert(item)
    return {"sku": sku, "status": ItemStatus.EXPORT_READY}


@router.post("/{sku}/reject")
def reject_item(sku: str, session: Session = Depends(get_session)):
    try:
        assert_route_sku_allowed(sku, "review.reject_item")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    ok = repo.update_status(sku, ItemStatus.REJECTED)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    return {"sku": sku, "status": ItemStatus.REJECTED}


@router.patch("/{sku}/edit")
def edit_and_approve(sku: str, updates: dict, session: Session = Depends(get_session)):
    """Edit fields and immediately approve."""
    try:
        assert_route_sku_allowed(sku, "review.edit_and_approve")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    for k, v in updates.items():
        if hasattr(item, k):
            setattr(item, k, v)
    block = _approval_block_detail(item)
    if block:
        item.manual_override = True
        repo.upsert(item)
        raise HTTPException(status_code=409, detail=block)
    item.manual_override = True
    item.needs_review = False
    item.status = ItemStatus.EXPORT_READY
    saved = repo.upsert(item)
    return saved.model_dump()
