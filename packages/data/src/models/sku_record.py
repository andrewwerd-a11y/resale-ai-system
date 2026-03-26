"""SKU registry table — one row per prefix, tracks last used number."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SKURecord(SQLModel, table=True):
    __tablename__ = "sku_registry"

    prefix: str = Field(primary_key=True)
    category_key: str
    last_number: int = Field(default=0)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
