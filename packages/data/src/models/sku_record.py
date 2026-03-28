from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class SKURecord(SQLModel, table=True):
    __tablename__ = "skus"

    sku: str = Field(primary_key=True)
    prefix: str
    sequence: int
    category: str
    item_sku: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
