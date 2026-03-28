from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class ReviewRecord(SQLModel, table=True):
    __tablename__ = "review_cases"

    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str
    trigger: str
    detail: str = ""
    resolved: bool = False
    resolution: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
