"""
SourcingBatch — represents a bulk purchase event (estate sale, thrift run, etc.).
Cost per item is auto-calculated: total_cost / item_count.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SourcingBatch(SQLModel, table=True):
    __tablename__ = "sourcing_batches"

    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    label: str                              # e.g. "Estate sale - Main St - March 2026"
    total_cost: float
    item_count: int
    cost_per_item: float                    # total_cost / item_count
    sourcing_date: datetime
    location: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
