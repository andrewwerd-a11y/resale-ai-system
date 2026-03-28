from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository

router = APIRouter()


class ReviewAction(BaseModel):
    action: str  # approve | reject
    notes: Optional[str] = None
    # Optional field overrides
    title: Optional[str] = None
    brand: Optional[str] = None
    condition: Optional[str] = None
    condition_notes: Optional[str] = None
    estimated_price: Optional[float] = None
    list_price: Optional[float] = None
    size: Optional[str] = None
    color: Optional[str] = None
    department: Optional[str] = None
    item_type: Optional[str] = None
    material: Optional[str] = None
    style: Optional[str] = None
    author: Optional[str] = None
    book_format: Optional[str] = None
    franchise: Optional[str] = None
    character: Optional[str] = None


@router.get("/queue")
def review_queue():
    """Return all items in needs_review status."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_status("needs_review")
    return [_summary(i) for i in items]


@router.post("/{sku}")
def review_item(sku: str, body: ReviewAction):
    with get_session() as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku.upper())
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {sku} not found")

        if body.action not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

        new_status = "approved" if body.action == "approve" else "rejected"

        # Apply any field overrides
        field_keys = [
            "title", "brand", "condition", "condition_notes", "estimated_price",
            "list_price", "size", "color", "department", "item_type",
            "material", "style", "author", "book_format", "franchise", "character",
        ]
        overrides = {k: getattr(body, k) for k in field_keys if getattr(body, k) is not None}
        if body.notes is not None:
            overrides["notes"] = body.notes
        if overrides:
            overrides["manual_override"] = True
        overrides["status"] = new_status

        updated = item.model_copy(update=overrides)
        saved = repo.upsert(updated)

    return {"sku": sku, "status": saved.status}


@router.get("/queue/count")
def queue_count():
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_status("needs_review")
    return {"count": len(items)}


def _summary(item) -> dict:
    return {
        "sku": item.sku,
        "title": item.title,
        "category": item.category,
        "brand": item.brand,
        "condition": item.condition,
        "estimated_price": item.estimated_price,
        "list_price": item.list_price,
        "ai_confidence": item.ai_confidence,
        "review_reasons": item.review_reasons,
        "image_paths": item.image_paths,
        "hosted_photo_urls": item.hosted_photo_urls,
        "status": item.status,
        # Full fields for editing
        "size": item.size,
        "color": item.color,
        "department": item.department,
        "item_type": item.item_type,
        "material": item.material,
        "style": item.style,
        "author": item.author,
        "book_format": item.book_format,
        "franchise": item.franchise,
        "character": item.character,
        "condition_notes": item.condition_notes,
        "notes": item.notes,
        "features": item.features,
        "defects": item.defects,
    }
