"""
ItemRepository — all database operations for items.
Idempotent writes: reprocessing an existing SKU updates fields,
never creates a duplicate.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from packages.data.src.models.item_record import ItemRecord
from packages.domain.src.entities.item import Item


def _to_record(item: Item) -> dict:
    """Convert Item entity to flat dict for DB storage."""
    d = item.model_dump()
    # Serialize known list/dict fields to JSON strings
    for field in ["features", "defects", "review_reasons",
                  "missing_required_fields", "missing_recommended_fields"]:
        if isinstance(d.get(field), list):
            d[field] = json.dumps(d[field])
    if isinstance(d.get("measurements"), dict):
        d["measurements"] = json.dumps(d["measurements"])
    if isinstance(d.get("item_specifics"), dict):
        d["item_specifics"] = json.dumps(d["item_specifics"])
    # image_paths stored as pipe-separated (never JSON array)
    d["image_paths"] = "|".join(item.image_paths or [])
    # Coerce any remaining list/dict values on string fields
    known_json_fields = {
        "features", "defects", "review_reasons", "image_paths", "measurements",
        "item_specifics", "missing_required_fields", "missing_recommended_fields",
    }
    for k, v in d.items():
        if k in known_json_fields:
            continue
        if isinstance(v, list):
            d[k] = ", ".join(str(i) for i in v)
        elif isinstance(v, dict):
            d[k] = json.dumps(v)
    return d


def _from_record(record: ItemRecord) -> Item:
    """Convert ItemRecord DB row back to Item entity."""
    d = record.model_dump()
    # Deserialize JSON string fields
    for field in ["features", "defects", "review_reasons",
                  "missing_required_fields", "missing_recommended_fields"]:
        if d.get(field) is None:
            d[field] = []
        elif isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    if not d.get("measurements"):
        d["measurements"] = {}
    elif isinstance(d["measurements"], str):
        try:
            d["measurements"] = json.loads(d["measurements"])
        except (json.JSONDecodeError, TypeError):
            d["measurements"] = {}
    if isinstance(d.get("item_specifics"), str):
        try:
            d["item_specifics"] = json.loads(d["item_specifics"])
        except (json.JSONDecodeError, TypeError):
            d["item_specifics"] = {}
    if isinstance(d.get("image_paths"), str) and d["image_paths"]:
        d["image_paths"] = d["image_paths"].split("|")
    elif not d.get("image_paths"):
        d["image_paths"] = []
    return Item(**d)


class ItemRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_sku(self, sku: str) -> Item | None:
        stmt = select(ItemRecord).where(ItemRecord.sku == sku)
        record = self.session.exec(stmt).first()
        return _from_record(record) if record else None

    def get_by_id(self, internal_id: str) -> Item | None:
        record = self.session.get(ItemRecord, internal_id)
        return _from_record(record) if record else None

    def list_by_status(self, status: str) -> list[Item]:
        stmt = select(ItemRecord).where(ItemRecord.status == status)
        return [_from_record(r) for r in self.session.exec(stmt).all()]

    def list_needs_review(self) -> list[Item]:
        stmt = select(ItemRecord).where(ItemRecord.needs_review == True)
        return [_from_record(r) for r in self.session.exec(stmt).all()]

    def list_export_ready(self) -> list[Item]:
        stmt = select(ItemRecord).where(ItemRecord.status == "export_ready")
        return [_from_record(r) for r in self.session.exec(stmt).all()]

    def upsert(self, item: Item) -> Item:
        """
        Insert or update. Never duplicates.
        If a record with the same SKU exists, updates derived fields only
        unless manual_override is True.
        """
        existing = None
        if item.sku:
            stmt = select(ItemRecord).where(ItemRecord.sku == item.sku)
            existing = self.session.exec(stmt).first()

        if existing:
            # Respect manual overrides — don't clobber human-entered data
            data = _to_record(item)
            protected = ["cost", "list_price", "minimum_price", "notes", "storage_location"]
            for field in protected:
                if existing.manual_override and data.get(field) is None:
                    data[field] = getattr(existing, field)
            # Never reset once-set enrichment and cost flags
            for flag in ("enrichment_done", "cost_manual"):
                if getattr(existing, flag, False):
                    data[flag] = True
            if getattr(existing, "enrichment_notes", None) and not data.get("enrichment_notes"):
                data["enrichment_notes"] = existing.enrichment_notes
            data["updated_at"] = datetime.utcnow()
            # Immutable fields — never overwrite PK or creation timestamp
            _immutable = {"internal_id", "created_at"}
            for k, v in data.items():
                if k in _immutable:
                    continue
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return _from_record(existing)
        else:
            data = _to_record(item)
            record = ItemRecord(**{k: v for k, v in data.items() if hasattr(ItemRecord, k)})
            self.session.add(record)
            self.session.commit()
            self.session.refresh(record)
            return _from_record(record)

    def update_status(self, sku: str, status: str) -> bool:
        stmt = select(ItemRecord).where(ItemRecord.sku == sku)
        record = self.session.exec(stmt).first()
        if not record:
            return False
        record.status = status
        record.updated_at = datetime.utcnow()
        self.session.add(record)
        self.session.commit()
        return True

    def count_by_status(self) -> dict[str, int]:
        from sqlalchemy import func
        stmt = select(ItemRecord.status, func.count()).group_by(ItemRecord.status)
        return {status: count for status, count in self.session.exec(stmt).all()}

    def get_all(self) -> list[Item]:
        return [_from_record(r) for r in self.session.exec(select(ItemRecord)).all()]
