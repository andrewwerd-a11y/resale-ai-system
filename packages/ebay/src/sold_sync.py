"""
SoldSync — polls the eBay Orders API for completed sales and reconciles
them against the local item database.

Phase 3 stub: returns empty stats if credentials are not configured.
Full implementation fetches orders, matches by listing_id, and updates
item status to SOLD with sold_price, fees, date_sold, net_profit.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src import http_client as ebay_http


class SoldSync:
    def __init__(self):
        self.auth = EbayAuth()
        self.settings = get_settings()

    def reconcile(self, session: Session, allowed_skus: set[str] | None = None) -> dict:
        """
        Fetch recent sold orders from eBay and mark matching items as SOLD.
        Returns stats dict: {synced, skipped, errors, not_configured}.
        """
        if not self.auth.is_configured():
            return {
                "synced": 0,
                "skipped": 0,
                "errors": [],
                "not_configured": True,
                "message": "eBay credentials not configured — set ebay_sandbox_* or ebay_prod_* in .env",
            }

        try:
            orders = self._fetch_sold_orders()
        except Exception as exc:
            return {"synced": 0, "skipped": 0, "errors": [str(exc)], "not_configured": False}

        repo = ItemRepository(session)
        stats = {"synced": 0, "skipped": 0, "errors": [], "not_configured": False}

        for order in orders:
            try:
                self._process_order(order, repo, allowed_skus=allowed_skus)
                stats["synced"] += 1
            except Exception as exc:
                stats["errors"].append(str(exc))
                stats["skipped"] += 1

        return stats

    def _fetch_sold_orders(self) -> list[dict]:
        """Fetch orders with FULFILLED status from the last 90 days."""
        since = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        params = {
            "filter": f"orderfulfillmentstatus:{{FULFILLED}},creationdate:[{since}]",
            "limit": "200",
        }
        resp = ebay_http.get(
            f"{self.auth.api_base}/sell/fulfillment/v1/order",
            headers={
                "Authorization": f"Bearer {self.auth.user_token}",
                "Accept": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self.auth.marketplace_id,
            },
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Sold sync failed: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        return data.get("orders", [])

    def _process_order(
        self,
        order: dict,
        repo: ItemRepository,
        allowed_skus: set[str] | None = None,
    ) -> None:
        """Match order line items to local SKUs and update status."""
        for line in order.get("lineItems", []):
            sku = line.get("sku") or line.get("legacySku")
            if not sku:
                continue
            if allowed_skus is not None and sku not in allowed_skus:
                continue
            item = repo.get_by_sku(sku)
            if not item or item.status == ItemStatus.SOLD:
                continue

            sold_price = float(
                order.get("pricingSummary", {}).get("total", {}).get("value", 0)
            )
            fees = float(
                order.get("pricingSummary", {}).get("fee", {}).get("value", 0)
            )
            shipping_cost = float(
                order.get("pricingSummary", {}).get("deliveryCost", {}).get("value", 0)
            )
            net_profit = sold_price - fees - shipping_cost - (item.cost or 0)

            item.status = ItemStatus.SOLD
            item.date_sold = datetime.utcnow()
            item.sold_price = sold_price
            item.fees = fees
            item.shipping_cost = shipping_cost
            item.net_profit = net_profit
            item.profit_margin = (net_profit / sold_price) if sold_price else None
            repo.upsert(item)
