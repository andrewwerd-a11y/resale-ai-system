"""
SaleRecord — one record per item sold.
Created automatically whenever mark_sold() is called.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SaleRecord(SQLModel, table=True):
    __tablename__ = "sale_records"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: str = Field(index=True)
    platform: str
    listing_id: Optional[str] = None
    sold_price: float
    cost: Optional[float] = None
    fees: float = 0.0
    shipping_cost: float = 0.0
    gross_profit: float = 0.0       # sold_price - cost
    net_profit: float = 0.0         # sold_price - cost - fees - shipping
    gross_margin: float = 0.0       # gross_profit / sold_price
    net_margin: float = 0.0         # net_profit / sold_price
    date_sold: datetime = Field(default_factory=datetime.utcnow)
    source_report: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
