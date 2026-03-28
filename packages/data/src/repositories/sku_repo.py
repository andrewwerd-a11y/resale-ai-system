from __future__ import annotations
import json
from typing import Optional
from sqlmodel import Session, select

from packages.data.src.models.sku_record import SKURecord


class SKURepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, sku: str) -> Optional[SKURecord]:
        return self._session.get(SKURecord, sku)

    def next_sequence(self, prefix: str) -> int:
        stmt = select(SKURecord).where(SKURecord.prefix == prefix)
        records = self._session.exec(stmt).all()
        if not records:
            return 1
        return max(r.sequence for r in records) + 1

    def create(self, prefix: str, category: str) -> str:
        seq = self.next_sequence(prefix)
        sku = f"{prefix}-{seq:06d}"
        record = SKURecord(sku=sku, prefix=prefix, sequence=seq, category=category)
        self._session.add(record)
        self._session.commit()
        return sku

    def assign_to_item(self, sku: str, item_sku: str) -> None:
        record = self._session.get(SKURecord, sku)
        if record:
            record.item_sku = item_sku
            self._session.add(record)
            self._session.commit()
