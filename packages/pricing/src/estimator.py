"""
PriceEstimator — applies pricing rules to AI-extracted data.

The AI already suggests estimated_price and list_price in the extraction prompt.
This module validates, adjusts, and computes derived fields.
Manual overrides always win.
"""
from __future__ import annotations

from packages.core.src.config import get_rules
from packages.domain.src.entities.item import Item


class PriceEstimator:
    def __init__(self):
        rules = get_rules()
        pricing = rules.get("pricing", {})
        self.min_margin = pricing.get("minimum_profit_margin", 0.30)
        self.stale_days = pricing.get("stale_listing_days", 60)
        self.stale_drop = pricing.get("stale_price_drop_percent", 10)

    def apply(self, item: Item) -> Item:
        """
        Apply pricing logic to an item after AI extraction.
        Returns the item with pricing fields filled/validated.
        Never overwrites fields that have manual_override=True.
        """
        if item.manual_override:
            # Only compute derived fields, never touch inputs
            item = self._compute_derived(item)
            return item

        # If AI gave us estimated_price, derive list_price if missing
        if item.estimated_price and not item.list_price:
            # Add 10% buffer for negotiation room
            item.list_price = round(item.estimated_price * 1.10, 2)

        # If AI gave us list_price but not estimated_price
        if item.list_price and not item.estimated_price:
            item.estimated_price = round(item.list_price * 0.90, 2)

        # Set minimum price floor (protect from lowball offers)
        if item.list_price and not item.minimum_price:
            item.minimum_price = round(item.list_price * 0.75, 2)

        # Validate margin if cost is known
        if item.cost and item.list_price:
            margin = (item.list_price - item.cost) / item.list_price
            if margin < self.min_margin:
                # Bump list price to meet minimum margin
                item.list_price = round(item.cost / (1 - self.min_margin), 2)
                item.minimum_price = round(item.list_price * 0.75, 2)

        item = self._compute_derived(item)
        return item

    def _compute_derived(self, item: Item) -> Item:
        """Recompute net_profit and profit_margin from current values."""
        if all(v is not None for v in [item.sold_price, item.cost,
                                        item.fees, item.shipping_cost]):
            item.net_profit = round(
                item.sold_price - item.cost - item.fees - item.shipping_cost, 2
            )
            if item.sold_price > 0:
                item.profit_margin = round(item.net_profit / item.sold_price, 4)
        return item

    def is_stale(self, item: Item) -> bool:
        """Return True if listing has been active longer than stale threshold."""
        if not item.days_listed:
            return False
        return item.days_listed >= self.stale_days

    def suggested_stale_price(self, item: Item) -> float | None:
        """Suggest a reduced price for stale listings."""
        if not item.list_price:
            return None
        drop = item.list_price * (self.stale_drop / 100)
        new_price = round(item.list_price - drop, 2)
        # Never go below minimum price
        if item.minimum_price and new_price < item.minimum_price:
            return item.minimum_price
        return new_price
