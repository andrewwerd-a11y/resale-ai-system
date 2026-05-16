"""Durable per-photo metadata for items.

Sidecar table keyed to a SKU + image path. This preserves the existing
`items.image_paths` contract while allowing operator or model labels to be
stored durably and reused across intake/reporting flows.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ItemPhotoMetadataRecord(SQLModel, table=True):
    __tablename__ = "item_photo_metadata"
    __table_args__ = (
        UniqueConstraint("sku", "image_path", name="uq_item_photo_metadata_sku_path"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    image_path: str = Field(index=True)
    photo_type: str = Field(default="unknown", index=True)
    label_source: str = Field(default="unknown", index=True)
    confidence: float | None = Field(default=None)
    is_cover: bool = Field(default=False)
    sort_order: int | None = Field(default=None)
    notes: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
