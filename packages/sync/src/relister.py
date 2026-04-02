"""
AutoRelister — re-lists ended eBay listings with an optional price adjustment.
Default price reduction on relist: 10%.
"""
from __future__ import annotations

import logging
from datetime import datetime

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item

logger = logging.getLogger(__name__)


class AutoRelister:
    def get_ended_listings(self, session) -> list[Item]:
        """
        Return items with status='listed' whose eBay listing has ended.
        Falls back to returning all listed items if eBay check fails.
        """
        from sqlmodel import select
        from packages.data.src.models.item_record import ItemRecord
        from packages.data.src.repositories.item_repo import _from_record

        stmt = select(ItemRecord).where(
            ItemRecord.status == "listed",
            ItemRecord.listing_id != None,
        )
        records = session.exec(stmt).all()
        items = [_from_record(r) for r in records]

        ended = []
        for item in items:
            if not self._is_listing_active(item):
                ended.append(item)
        return ended

    def _is_listing_active(self, item: Item) -> bool:
        """Returns True if listing is still active on eBay. Assumes active on error."""
        if not item.listing_id:
            return False
        try:
            from packages.ebay.src.inventory_client import EbayInventoryClient
            client = EbayInventoryClient()
            if hasattr(client, "get_listing_status"):
                result = client.get_listing_status(item.listing_id)
                if result.ok:
                    return result.value.get("status") == "ACTIVE"
        except Exception as e:
            logger.debug("Could not check listing %s: %s", item.listing_id, e)
        return True  # Assume active if check fails

    def relist(self, item: Item, price_adjustment: float = -0.10) -> Result[str]:
        """
        Relist an ended item with optional price reduction.
        Returns new listing_id on success.
        """
        try:
            from packages.ebay.src.inventory_client import EbayInventoryClient

            if item.list_price:
                new_price = round(item.list_price * (1 + price_adjustment), 2)
                if item.minimum_price:
                    new_price = max(new_price, float(item.minimum_price))
                item.list_price = new_price

            client = EbayInventoryClient()
            result = client.publish_item(item)
            if not result.ok:
                return Result.failure(result.error or "publish_failed")

            listing_id = result.value.get("listing_id", "")
            logger.info("Relisted %s → listing %s at $%.2f", item.sku, listing_id, item.list_price or 0)
            return Result.success(listing_id)
        except Exception as e:
            return Result.failure(str(e))
