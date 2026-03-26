"""Batch entity — tracks one processing run."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class Batch(BaseModel):
    batch_id: str
    batch_name: str | None = None
    source_path: str | None = None
    item_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    status: str = "pending"   # pending | running | complete | failed
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    notes: str | None = None
