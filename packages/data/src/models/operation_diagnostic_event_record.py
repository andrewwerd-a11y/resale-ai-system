from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class OperationDiagnosticEventRecord(SQLModel, table=True):
    __tablename__ = "operation_diagnostic_events"

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    operation_name: str = Field(index=True)
    route: Optional[str] = Field(default=None, index=True)
    sku: Optional[str] = Field(default=None, index=True)
    batch_id: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(index=True)
    mutation_attempted: bool = Field(default=False, index=True)
    mutation_succeeded: bool = Field(default=False, index=True)
    ebay_mutation_attempted: bool = Field(default=False, index=True)
    ebay_mutation_succeeded: bool = Field(default=False, index=True)
    external_service: Optional[str] = Field(default=None, index=True)
    stage: Optional[str] = Field(default=None, index=True)
    error_family: Optional[str] = Field(default=None, index=True)
    error_code: Optional[str] = Field(default=None, index=True)
    raw_error_summary: Optional[str] = None
    raw_error_payload_json: Optional[str] = None
    safe_message: str
    recommended_next_action: Optional[str] = None
    related_files_services_json: Optional[str] = None
    request_context_json: Optional[str] = None
    result_context_json: Optional[str] = None
