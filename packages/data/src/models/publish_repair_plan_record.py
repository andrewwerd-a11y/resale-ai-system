from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class PublishRepairPlanRecord(SQLModel, table=True):
    __tablename__ = "publish_repair_plans"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    publish_attempt_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="open", index=True)
    affected_field: Optional[str] = None
    current_value_json: Optional[str] = None
    expected_value_json: Optional[str] = None
    suggested_value_json: Optional[str] = None
    suggested_actions_json: Optional[str] = None
    risk_level: str = Field(default="medium", index=True)
    safe_to_auto_apply: bool = Field(default=False)
    requires_review: bool = Field(default=True, index=True)
    retry_allowed: bool = Field(default=False, index=True)
    source: str = Field(default="ebay_error", index=True)
    repair_layer: Optional[str] = Field(default=None, index=True)
    classified_error_code: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
