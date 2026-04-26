"""
StaleChecker — identifies listings that have been active too long
and suggests price adjustments to improve sell-through.
"""
from __future__ import annotations

import logging
from datetime import datetime

from packages.core.src.config import get_rules
from packages.data.src.models.item_record import ItemRecord

logger = logging.getLogger(__name__)


class StaleChecker:
    def __init__(self):
        rules = get_rules()
        pricing = rules.get("pricing", {})
        self.stale_days: int = int(pricing.get("stale_listing_days", 60))
        self.stale_drop: float = float(pricing.get("stale_price_drop_percent", 10))

    def get_stale_items(self, session) -> list[ItemRecord]:
        """Return items with status='listed' listed longer than stale_days."""
        from sqlmodel import select

        self.refresh_days_listed(session)
        stmt = select(ItemRecord).where(
            ItemRecord.status == "listed",
            ItemRecord.days_listed >= self.stale_days,
        )
        return list(session.exec(stmt).all())

    def refresh_days_listed(self, session) -> int:
        """
        Recalculate days_listed for currently listed items using date_listed.
        Returns number of rows changed.
        """
        from sqlmodel import select

        now = datetime.utcnow()
        changed = 0
        listed_items = session.exec(
            select(ItemRecord).where(ItemRecord.status == "listed")
        ).all()

        for item in listed_items:
            new_days = self._compute_days_listed(item.date_listed, now)
            if item.days_listed != new_days:
                item.days_listed = new_days
                item.updated_at = now
                session.add(item)
                changed += 1

        if changed:
            session.commit()
        return changed

    @staticmethod
    def _compute_days_listed(date_listed: datetime | None, now: datetime) -> int | None:
        if not date_listed:
            return None
        return max((now.date() - date_listed.date()).days, 0)

    def suggest_price_drop(self, item: ItemRecord) -> float | None:
        """Return suggested new price after applying the stale drop percentage."""
        if not item.list_price:
            return None
        drop_amount = item.list_price * (self.stale_drop / 100)
        new_price = round(item.list_price - drop_amount, 2)
        if item.minimum_price and new_price < item.minimum_price:
            return float(item.minimum_price)
        return new_price

    def apply_price_drops(self, session) -> int:
        """Apply stale price drops to all stale items. Returns count of items updated."""
        stale = self.get_stale_items(session)
        updated = 0
        for item in stale:
            new_price = self.suggest_price_drop(item)
            if new_price is not None and new_price != item.list_price:
                logger.info(
                    "Stale drop: %s $%.2f → $%.2f",
                    item.sku, item.list_price or 0, new_price,
                )
                item.list_price = new_price
                item.updated_at = datetime.utcnow()
                session.add(item)
                updated += 1
        if updated:
            session.commit()
        return updated
