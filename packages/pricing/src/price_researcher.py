"""
PriceResearcher — looks up real eBay sold prices for comparable items.
Uses the eBay Browse API with a client_credentials OAuth application token.
"""
from __future__ import annotations

import base64
import logging

import httpx

from packages.core.src.config import get_settings
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item

logger = logging.getLogger(__name__)


def _get_app_token(settings) -> str | None:
    """Obtain an OAuth application token via client_credentials grant."""
    if settings.ebay_environment == "production":
        token_url = "https://api.ebay.com/identity/v1/oauth2/token"
        app_id = settings.ebay_prod_app_id
        cert_id = settings.ebay_prod_cert_id
    else:
        token_url = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
        app_id = settings.ebay_sandbox_app_id
        cert_id = settings.ebay_sandbox_cert_id

    if not (app_id and cert_id):
        logger.warning("eBay app_id / cert_id not configured — skipping price research")
        return None

    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    try:
        r = httpx.post(
            token_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
        logger.warning(
            "App token request failed: %s %s", r.status_code, r.text[:200]
        )
    except Exception as e:
        logger.warning("App token error: %s", e)
    return None


class PriceResearcher:
    """Looks up comparable eBay active listings to inform pricing."""

    def __init__(self):
        self.settings = get_settings()

    def _build_query(self, item: Item) -> str:
        parts = [
            item.brand or "",
            item.type or "",
            item.size or "",
            item.condition_label or "",
        ]
        return " ".join(p for p in parts if p).strip()

    def research(self, item: Item) -> Result[dict]:
        """
        Search eBay Browse API for comparable fixed-price listings.

        Returns:
            {
                "suggested_price": float,
                "avg_sold_price": float,
                "min_sold_price": float,
                "max_sold_price": float,
                "sample_count": int,
                "search_query": str,
                "needs_manual_pricing": bool,
            }
        """
        token = _get_app_token(self.settings)
        if not token:
            return Result.failure(
                "Could not obtain eBay app token — check ebay_*_app_id and ebay_*_cert_id"
            )

        query = self._build_query(item)
        if not query:
            return Result.failure(
                "Insufficient item data to build search query (need brand, type, or size)"
            )

        url = f"{self.settings.ebay_api_base}/buy/browse/v1/item_summary/search"
        params = {
            "q": query,
            "filter": "buyingOptions:{FIXED_PRICE}",
            "sort": "NEWLY_LISTED",
            "limit": "20",
        }

        try:
            r = httpx.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        except httpx.TimeoutException:
            return Result.failure("eBay Browse API request timed out")
        except Exception as e:
            return Result.failure(f"eBay Browse API request failed: {e}")

        if r.status_code != 200:
            return Result.failure(
                f"eBay Browse API error {r.status_code}: {r.text[:300]}"
            )

        summaries = r.json().get("itemSummaries", [])
        prices: list[float] = []
        for it in summaries:
            try:
                val = float(it.get("price", {}).get("value", 0) or 0)
                if val > 0:
                    prices.append(val)
            except (ValueError, TypeError):
                continue

        if not prices:
            return Result.failure(f"No priced results found for query: {query!r}")

        avg = round(sum(prices) / len(prices), 2)
        low = round(min(prices), 2)
        high = round(max(prices), 2)
        # Suggest slightly below average to be competitive
        suggested = round(avg * 0.95, 2)
        needs_manual = len(prices) < 3

        logger.info(
            "Price research for %s: avg=$%.2f n=%d query=%r",
            item.sku, avg, len(prices), query,
        )

        return Result.success({
            "suggested_price": suggested,
            "avg_sold_price": avg,
            "min_sold_price": low,
            "max_sold_price": high,
            "sample_count": len(prices),
            "search_query": query,
            "needs_manual_pricing": needs_manual,
        })
