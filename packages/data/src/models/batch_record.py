from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class BatchRecord(SQLModel, table=True):
    __tablename__ = "batches"

    batch_id: str = Field(primary_key=True)
    source_dir: str
    item_count: int = 0
    status: str = "pending"
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
