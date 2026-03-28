from __future__ import annotations
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SKU(BaseModel):
    sku: str
    prefix: str
    sequence: int
    category: str
    item_sku: Optional[str] = None  # FK to item
    created_at: Optional[datetime] = None
