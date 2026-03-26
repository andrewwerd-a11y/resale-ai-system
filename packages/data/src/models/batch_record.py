"""Batch record table."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class BatchRecord(SQLModel, table=True):
    __tablename__ = "batches"

    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    batch_name: Optional[str] = None
    source_path: Optional[str] = None
    item_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    status: str = "pending"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    notes: Optional[str] = None
