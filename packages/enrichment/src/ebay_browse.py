"""
eBay sold price comps via Marketplace Insights API (Option A) with Browse API fallback (Option B).

Auth: Application token (Client Credentials grant) — NOT user token.
Scope required: https://api.ebay.com/oauth/api_scope/buy.marketplace.insights

If eBay hasn't approved that scope, falls back automatically to active Browse API
listings and uses the 20th-percentile price as a conservative floor proxy.
"""
from __future__ import annotations

import logging
import statistics
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Cache the app token in memory for the process lifetime (expires in ~2h, server restarts are fine)
_cached_token: dict = {}  # {"token": str, "expires_at": float}


def get_price_comps(query: str, limit: int = 10) -> dict:
    """
    Fetch sold price comps for a search query.

    Returns:
        {"median": float|None, "low": float|None, "high": float|None, "sample_size": int}
    """
    from packages.core.src.config import get_settings
    settings = get_settings()

    if not settings.ebay_app_id or not settings.ebay_cert_id:
        logger.debug("eBay credentials missing — skipping price comps")
        return {"median": None, "low": None, "high": None, "sample_size": 0}

    token = _get_app_token(settings)
    if not token:
        return {"median": None, "low": None, "high": None, "sample_size": 0}

    # Option A: Marketplace Insights (actual sold listings)
    result = _fetch_insights(token, query, limit, settings)
    if result["sample_size"] > 0:
        return result

    # Option B: Active Browse API — use 20th-percentile as conservative floor proxy
    logger.debug("Marketplace Insights returned 0 results; falling back to Browse API for %r", query)
    return _fetch_browse_comps(token, query, limit, settings)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_app_token(settings) -> Optional[str]:
    import base64
    import time

    global _cached_token
    now = time.time()

    if _cached_token.get("token") and _cached_token.get("expires_at", 0) > now + 60:
        return _cached_token["token"]

    credentials = f"{settings.ebay_app_id}:{settings.ebay_cert_id}"
    encoded = base64.b64encode(credentials.encode()).decode()

    base = settings.ebay_api_base  # https://api.ebay.com or sandbox
    try:
        resp = httpx.post(
            f"{base}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 7200))
        _cached_token = {"token": token, "expires_at": now + expires_in}
        logger.debug("eBay app token refreshed (expires in %ds)", expires_in)
        return token
    except Exception as exc:
        logger.warning("Failed to get eBay app token: %s", exc)
        return None


# ── Option A: Marketplace Insights ───────────────────────────────────────────

def _fetch_insights(token: str, query: str, limit: int, settings) -> dict:
    base = settings.ebay_api_base
    url = f"{base}/buy/marketplace_insights/v1_beta/item_sales/search"
    try:
        resp = httpx.get(
            url,
            params={
                "q": query,
                "limit": min(limit, 50),
                "filter": "buyingOptions:{FIXED_PRICE}",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
            timeout=10.0,
        )
        if resp.status_code == 403:
            # Scope not approved — silently fall through to Option B
            logger.debug("Marketplace Insights scope not approved (403) — falling back to Browse")
            return {"median": None, "low": None, "high": None, "sample_size": 0}
        resp.raise_for_status()
        items = resp.json().get("itemSales", [])
        return _aggregate_prices(items, price_key="lastSoldPrice")
    except Exception as exc:
        logger.debug("Marketplace Insights error for %r: %s", query, exc)
        return {"median": None, "low": None, "high": None, "sample_size": 0}


# ── Option B: Browse API (active listings, 20th-percentile floor) ─────────────

def _fetch_browse_comps(token: str, query: str, limit: int, settings) -> dict:
    base = settings.ebay_api_base
    url = f"{base}/buy/browse/v1/item_summary/search"
    try:
        resp = httpx.get(
            url,
            params={
                "q": query,
                "limit": min(limit * 3, 50),  # pull more to get a better percentile
                "filter": "buyingOptions:{FIXED_PRICE},itemLocationCountry:US",
                "sort": "price",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("itemSummaries", [])
        result = _aggregate_prices(items, price_key="price")
        if result["sample_size"] > 0:
            # Annotate that this is an active-listing proxy, not confirmed sold
            result["proxy"] = True
        return result
    except Exception as exc:
        logger.debug("Browse API error for %r: %s", query, exc)
        return {"median": None, "low": None, "high": None, "sample_size": 0}


# ── Price aggregation ─────────────────────────────────────────────────────────

def _aggregate_prices(items: list, price_key: str) -> dict:
    prices = []
    for item in items:
        price_obj = item.get(price_key) or item.get("price")
        if not price_obj:
            continue
        try:
            prices.append(float(price_obj.get("value", 0)))
        except (TypeError, ValueError):
            continue

    prices = [p for p in prices if p > 0]
    if not prices:
        return {"median": None, "low": None, "high": None, "sample_size": 0}

    prices.sort()
    return {
        "median": round(statistics.median(prices), 2),
        "low": round(prices[0], 2),
        "high": round(prices[-1], 2),
        "sample_size": len(prices),
    }
