"""
eBay sold order sync.

Fetches recent orders from eBay Fulfillment API and reconciles them
with items in the local database by matching SKU via custom label.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from packages.core.src.result import Result
from packages.ebay.src import auth
from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository


def _base() -> str:
    from packages.core.src.config import get_settings
    return get_settings().ebay_api_base


def _headers() -> dict[str, str]:
    return auth.user_token_headers()


def fetch_recent_orders(days_back: int = 30) -> Result[list[dict]]:
    """
    GET /sell/fulfillment/v1/order
    Returns list of raw order dicts.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    url = f"{_base()}/sell/fulfillment/v1/order"
    params = {
        "filter": f"creationdate:[{since}..]",
        "limit": 200,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=_headers(), params=params)
            if resp.status_code == 200:
                data = resp.json()
                orders = data.get("orders", [])
                return Result.success(orders)
            return Result.failure(
                f"fetch_recent_orders HTTP {resp.status_code}: {resp.text[:400]}"
            )
    except Exception as e:
        return Result.failure(f"fetch_recent_orders exception: {e}")


def _extract_sku_from_order(order: dict) -> Optional[str]:
    """Pull custom label (SKU) from order line items."""
    for line in order.get("lineItems", []):
        label = line.get("sku") or line.get("legacyItemId", "")
        if label and "-" in label and len(label) <= 12:
            return label.upper()
        # Try properties
        for prop in line.get("lineItemProperties", []):
            if prop.get("name", "").lower() in ("sku", "custom label"):
                return str(prop.get("value", "")).upper()
    return None


def _extract_sold_price(order: dict) -> Optional[float]:
    try:
        amount = order.get("pricingSummary", {}).get("total", {}).get("value")
        if amount:
            return float(amount)
    except Exception:
        pass
    return None


def _extract_fees(order: dict) -> Optional[float]:
    """Estimate eBay fees from order total (13.25%)."""
    price = _extract_sold_price(order)
    if price:
        return round(price * 0.1325, 2)
    return None


def _extract_order_date(order: dict) -> Optional[datetime]:
    raw = order.get("creationDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def reconcile(days_back: int = 30) -> dict:
    """
    Fetch orders and match to DB items by SKU.
    Updates matched items: sold_price, ebay_fees, net_profit, date_sold, status=sold.
    Returns stats: {matched, not_found, failed, total_orders}
    """
    orders_result = fetch_recent_orders(days_back)
    if orders_result.is_err:
        return {"error": orders_result.error, "matched": 0, "not_found": 0, "failed": 0, "total_orders": 0}

    orders = orders_result.value
    matched = 0
    not_found = 0
    failed = 0

    for order in orders:
        sku = _extract_sku_from_order(order)
        if not sku:
            not_found += 1
            continue

        sold_price = _extract_sold_price(order)
        fees = _extract_fees(order)
        order_date = _extract_order_date(order)

        try:
            with get_session() as session:
                repo = ItemRepository(session)
                item = repo.get_by_sku(sku)
                if item is None:
                    not_found += 1
                    continue

                net_profit = None
                if sold_price and fees:
                    from packages.pricing.src.estimator import SHIPPING_COST
                    net_profit = round(sold_price - fees - SHIPPING_COST, 2)

                # Build updated item
                updated = item.model_copy(update={
                    "sold_price": sold_price,
                    "ebay_fees": fees,
                    "net_profit": net_profit,
                    "date_sold": order_date,
                    "status": "sold",
                })
                repo.upsert(updated)
                matched += 1

        except Exception:
            failed += 1

    return {
        "matched": matched,
        "not_found": not_found,
        "failed": failed,
        "total_orders": len(orders),
    }
