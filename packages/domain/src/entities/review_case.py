from __future__ import annotations
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class ReviewCase(BaseModel):
    id: Optional[int] = None
    sku: str
    trigger: str
    detail: str = ""
    resolved: bool = False
    resolution: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
