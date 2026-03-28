from __future__ import annotations
from packages.domain.src.entities.item import Item


EBAY_FEE_RATE = 0.1325
SHIPPING_COST = 4.99
DEFAULT_MARGIN = 0.35


def calculate_list_price(estimated_price: float) -> float:
    """
    Back-calculate list price from estimated market value.
    list_price = estimated / (1 - fee_rate)
    Rounded to nearest $0.99.
    """
    if estimated_price <= 0:
        return 0.0
    raw = estimated_price / (1 - EBAY_FEE_RATE)
    # Round to X.99
    floored = int(raw)
    return float(floored) + 0.99


def calculate_net_profit(list_price: float, ebay_fees: float, cogs: float = 0.0) -> float:
    return list_price - ebay_fees - SHIPPING_COST - cogs


def calculate_fees(list_price: float) -> float:
    return round(list_price * EBAY_FEE_RATE, 2)


def enrich_pricing(item: Item) -> dict:
    """Return a dict of pricing fields to apply to an item."""
    est = item.estimated_price or 0.0
    list_price = item.list_price or calculate_list_price(est)
    fees = calculate_fees(list_price)
    net = calculate_net_profit(list_price, fees)
    return {
        "list_price": round(list_price, 2),
        "ebay_fees": round(fees, 2),
        "net_profit": round(net, 2),
    }
