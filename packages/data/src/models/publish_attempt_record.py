from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class PublishAttemptRecord(SQLModel, table=True):
    __tablename__ = "publish_attempts"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    stage: str = Field(default="unknown", index=True)
    status: str = Field(default="failed", index=True)
    attempted_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    ebay_environment: Optional[str] = None
    marketplace_id: Optional[str] = None
    request_summary_json: Optional[str] = None
    raw_error_json: Optional[str] = None
    ebay_error_id: Optional[str] = None
    ebay_error_message: Optional[str] = None
    classified_error_code: Optional[str] = Field(default=None, index=True)
    repair_layer: Optional[str] = Field(default=None, index=True)
    requires_review: bool = Field(default=True, index=True)
    retry_allowed: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
