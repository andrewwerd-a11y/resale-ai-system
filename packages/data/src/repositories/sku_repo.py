"""SKU registry repository — tracks and reserves SKU numbers per prefix."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from packages.data.src.models.sku_record import SKURecord
from packages.core.src.config import get_sku_prefixes


class SKURepository:
    def __init__(self, session: Session):
        self.session = session

    def _get_or_create(self, prefix: str) -> SKURecord:
        record = self.session.get(SKURecord, prefix)
        if not record:
            prefixes = get_sku_prefixes()
            category_key = prefixes.get(prefix, {}).get("category_key", prefix.lower())
            record = SKURecord(prefix=prefix, category_key=category_key, last_number=0)
            self.session.add(record)
            self.session.commit()
            self.session.refresh(record)
        return record

    def get_last_number(self, prefix: str) -> int:
        record = self._get_or_create(prefix)
        return record.last_number

    def reserve_next(self, prefix: str) -> str:
        """Atomically reserve the next SKU for a prefix. Returns formatted SKU."""
        record = self._get_or_create(prefix)
        record.last_number += 1
        record.last_updated = datetime.utcnow()
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return f"{prefix}-{record.last_number:06d}"

    def preserve_existing(self, prefix: str, number: int) -> None:
        """
        Called during migration — ensures the registry never goes below
        the highest existing number so new items don't collide.
        """
        record = self._get_or_create(prefix)
        if number > record.last_number:
            record.last_number = number
            record.last_updated = datetime.utcnow()
            self.session.add(record)
            self.session.commit()

    def sku_exists(self, sku: str) -> bool:
        from packages.data.src.models.item_record import ItemRecord
        stmt = select(ItemRecord).where(ItemRecord.sku == sku)
        return self.session.exec(stmt).first() is not None

    def parse_sku(self, sku: str) -> tuple[str, int] | None:
        """Parse 'CL-000007' → ('CL', 7). Returns None if invalid."""
        try:
            prefix, num_str = sku.split("-")
            return prefix, int(num_str)
        except (ValueError, AttributeError):
            return None

    def initialize_from_existing_folders(self, sku_list: list[str]) -> dict[str, int]:
        """
        Scan a list of existing SKUs and set the registry to the
        highest number found per prefix. Used by the migration script.
        Returns {prefix: highest_number}.
        """
        highest: dict[str, int] = {}
        for sku in sku_list:
            parsed = self.parse_sku(sku)
            if parsed:
                prefix, number = parsed
                if number > highest.get(prefix, 0):
                    highest[prefix] = number
        for prefix, number in highest.items():
            self.preserve_existing(prefix, number)
        return highest
