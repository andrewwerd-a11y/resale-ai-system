"""
CrossPlatformSync — marks items sold and ends listings on all other platforms.
When an item sells anywhere, it becomes unavailable everywhere else immediately.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item

logger = logging.getLogger(__name__)

_PLATFORMS_PATH = Path(__file__).resolve().parents[4] / "config" / "platforms.json"


def _load_platforms() -> dict:
    with open(_PLATFORMS_PATH, encoding="utf-8") as f:
        return json.load(f)


class CrossPlatformSync:
    def mark_sold(
        self,
        sku: str,
        platform: str,
        sold_price: float,
        fees: float,
        session,
    ) -> Result[dict]:
        """
        Mark item as sold on one platform and end listings on all others.
        Creates a SaleRecord. Returns summary of actions taken.
        """
        from packages.data.src.models.sale_record import SaleRecord
        from packages.data.src.repositories.item_repo import ItemRepository

        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            return Result.failure(f"Item {sku} not found")

        fees = fees or 0.0
        cost = item.cost or 0.0
        shipping = item.shipping_cost or 0.0
        gross_profit = sold_price - cost
        net_profit = sold_price - cost - fees - shipping
        gross_margin = gross_profit / sold_price if sold_price > 0 else 0.0
        net_margin = net_profit / sold_price if sold_price > 0 else 0.0

        item.status = "sold"
        item.date_sold = datetime.utcnow()
        item.sold_price = sold_price
        item.fees = fees
        item.platform = platform
        item.net_profit = net_profit
        item.profit_margin = net_margin
        repo.upsert(item)

        sale = SaleRecord(
            sku=sku,
            platform=platform,
            listing_id=item.listing_id,
            sold_price=sold_price,
            cost=item.cost,
            fees=fees,
            shipping_cost=shipping,
            gross_profit=round(gross_profit, 2),
            net_profit=round(net_profit, 2),
            gross_margin=round(gross_margin, 4),
            net_margin=round(net_margin, 4),
            date_sold=datetime.utcnow(),
        )
        session.add(sale)
        session.commit()

        takedowns = self.end_other_platform_listings(item, platform)

        return Result.success({
            "sku": sku,
            "sold_price": sold_price,
            "net_profit": round(net_profit, 2),
            "gross_margin_pct": round(gross_margin * 100, 1),
            "platform": platform,
            "takedowns": takedowns,
        })

    def end_other_platform_listings(self, item: Item, sold_on: str) -> dict:
        """
        End active listings on all platforms except the one it sold on.
        Returns {platform: result_string} for each attempted takedown.
        """
        platforms = _load_platforms()
        results: dict[str, str] = {}

        for key, cfg in platforms.items():
            if key == sold_on:
                continue
            if not cfg.get("active", False):
                continue

            if cfg.get("end_listing_supported", False):
                # Future Phase 7: call platform API
                results[key] = "api_not_implemented"
            else:
                logger.warning(
                    "MANUAL TAKEDOWN REQUIRED — %s (%s): %s. %s",
                    item.sku,
                    cfg["label"],
                    key,
                    cfg.get("note", ""),
                )
                results[key] = "manual_takedown_required"

        return results
