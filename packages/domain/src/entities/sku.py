"""SKU reservation entity — tracks next available number per prefix."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class SKUReservation(BaseModel):
    category_key: str
    prefix: str
    last_number: int
    reserved_number: int | None = None
    reserved_at: datetime | None = None
    confirmed: bool = False

    def format_sku(self, number: int) -> str:
        return f"{self.prefix}-{number:06d}"

    @property
    def next_sku(self) -> str:
        return self.format_sku(self.last_number + 1)
