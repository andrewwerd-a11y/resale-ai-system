"""Review case table."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ReviewRecord(SQLModel, table=True):
    __tablename__ = "review_cases"

    review_case_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    trigger_reason: Optional[str] = None     # JSON array
    confidence_score: Optional[float] = None
    missing_fields: Optional[str] = None     # JSON array
    field_conflicts: Optional[str] = None    # JSON object
    high_value_flag: bool = False
    override_notes: Optional[str] = None
    resolution_status: str = Field(default="pending", index=True)
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
