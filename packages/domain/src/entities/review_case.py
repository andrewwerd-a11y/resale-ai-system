"""ReviewCase entity — items flagged for manual inspection."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ReviewCase(BaseModel):
    review_case_id: str
    sku: str
    trigger_reason: list[str] = Field(default_factory=list)
    confidence_score: float | None = None
    missing_fields: list[str] = Field(default_factory=list)
    field_conflicts: dict = Field(default_factory=dict)
    high_value_flag: bool = False
    override_notes: str | None = None
    resolution_status: str = "pending"   # pending | approved | rejected | edited
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
