"""
SoldSync — polls the eBay Orders API for completed sales and reconciles
them against the local item database.

Phase 3 stub: returns empty stats if credentials are not configured.
Full implementation fetches orders, matches by listing_id, and updates
item status to SOLD with sold_price, fees, date_sold, net_profit.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.data.src.models.sale_record import SaleRecord
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
        stats = {
            "synced": 0,
            "skipped": 0,
            "errors": [],
            "not_configured": False,
            "synced_items": 0,
            "skipped_items": 0,
            "duplicate_items": 0,
            "unknown_skus": 0,
            "blocked_skus": 0,
        }

        for order in orders:
            try:
                order_stats = self._process_order(order, repo, session, allowed_skus=allowed_skus)
                stats["synced_items"] += order_stats["synced_items"]
                stats["skipped_items"] += order_stats["skipped_items"]
                stats["duplicate_items"] += order_stats["duplicate_items"]
                stats["unknown_skus"] += order_stats["unknown_skus"]
                stats["blocked_skus"] += order_stats["blocked_skus"]
            except Exception as exc:
                stats["errors"].append(str(exc))
                stats["skipped_items"] += 1

        # Preserve legacy keys for callers that expect old response shape.
        stats["synced"] = stats["synced_items"]
        stats["skipped"] = stats["skipped_items"]

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
        session: Session,
        allowed_skus: set[str] | None = None,
    ) -> dict[str, int]:
        """Match order line items to local SKUs and update status."""
        result = {
            "synced_items": 0,
            "skipped_items": 0,
            "duplicate_items": 0,
            "unknown_skus": 0,
            "blocked_skus": 0,
        }
        order_id = str(order.get("orderId") or order.get("legacyOrderId") or "")

        for line in order.get("lineItems", []):
            sku = str(line.get("sku") or line.get("legacySku") or "").strip()
            if not sku:
                result["skipped_items"] += 1
                continue

            source_key = self._source_key(order_id, line, sku)
            if source_key:
                existing = session.exec(
                    select(SaleRecord).where(
                        SaleRecord.sku == sku,
                        SaleRecord.source_report == source_key,
                    )
                ).first()
                if existing:
                    result["duplicate_items"] += 1
                    result["skipped_items"] += 1
                    continue

            item = repo.get_by_sku(sku)
            if not item:
                result["unknown_skus"] += 1
                result["skipped_items"] += 1
                continue
            if allowed_skus is not None and sku not in allowed_skus:
                result["blocked_skus"] += 1
                result["skipped_items"] += 1
                continue
            if not source_key and item.status == ItemStatus.SOLD:
                result["duplicate_items"] += 1
                result["skipped_items"] += 1
                continue

            sold_price = self._money(line, "lineItemCost", "total", default=0.0)
            if sold_price <= 0:
                sold_price = self._money(order, "pricingSummary", "total", default=0.0)
            fees = self._money(order, "pricingSummary", "fee", default=0.0)
            shipping_cost = self._money(order, "pricingSummary", "deliveryCost", default=0.0)
            net_profit = sold_price - fees - shipping_cost - (item.cost or 0)
            gross_profit = sold_price - (item.cost or 0)
            gross_margin = (gross_profit / sold_price) if sold_price else 0.0
            net_margin = (net_profit / sold_price) if sold_price else 0.0
            sold_at = datetime.utcnow()

            item.status = ItemStatus.SOLD
            item.date_sold = sold_at
            item.sold_price = sold_price
            item.fees = fees
            item.shipping_cost = shipping_cost
            item.net_profit = net_profit
            item.profit_margin = net_margin if sold_price else None
            item.platform = "ebay"
            repo.upsert(item)

            sale = SaleRecord(
                sku=sku,
                platform="ebay",
                listing_id=str(line.get("legacyItemId") or item.listing_id or ""),
                sold_price=float(sold_price),
                cost=item.cost,
                fees=float(fees),
                shipping_cost=float(shipping_cost),
                gross_profit=round(gross_profit, 2),
                net_profit=round(net_profit, 2),
                gross_margin=round(gross_margin, 4),
                net_margin=round(net_margin, 4),
                date_sold=sold_at,
                source_report=source_key or None,
            )
            session.add(sale)
            session.commit()
            result["synced_items"] += 1

        return result

    @staticmethod
    def _source_key(order_id: str, line: dict, sku: str) -> str:
        line_id = str(line.get("lineItemId") or line.get("legacyTransactionId") or "")
        if order_id and line_id:
            return f"ebay_order:{order_id}|line:{line_id}|sku:{sku}"
        if order_id:
            return f"ebay_order:{order_id}|sku:{sku}"
        return ""

    @staticmethod
    def _money(payload: dict, *keys: str, default: float = 0.0) -> float:
        node = payload
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key, {})
        if isinstance(node, dict):
            node = node.get("value", default)
        try:
            return float(node or 0)
        except (TypeError, ValueError):
            return default
