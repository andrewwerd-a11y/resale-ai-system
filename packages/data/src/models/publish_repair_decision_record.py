from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class PublishRepairDecisionRecord(SQLModel, table=True):
    __tablename__ = "publish_repair_decisions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    repair_plan_id: str = Field(index=True)
    action: str = Field(default="apply", index=True)
    before_value_json: Optional[str] = None
    after_value_json: Optional[str] = None
    operator_label: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
