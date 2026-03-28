from __future__ import annotations
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class Batch(BaseModel):
    batch_id: str
    source_dir: str
    item_count: int = 0
    status: str = "pending"
    created_at: Optional[datetime] = None
