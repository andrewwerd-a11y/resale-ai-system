"""
EbayInventoryClient — publishes items to eBay via the Inventory API.

Flow per item:
  1. Upload photos (Cloudinary / local fallback)
  2. PUT  /sell/inventory/v1/inventory_item/{sku}
  3. POST /sell/inventory/v1/offer
  4. POST /sell/inventory/v1/offer/{offerId}/publish
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

CATEGORY_MAP = {
    "books":        "29223",   # Books > Antiquarian & Collectible
    "clothing":     "11450",   # Clothing, Shoes & Accessories > Women > Clothing
    "shoes":        "93427",   # Shoes
    "collectibles": "1",       # Collectibles
    "toys":         "19009",   # Toys & Hobbies > Dolls & Bears
}

CONDITION_MAP = {
    "1000": "NEW",
    "1500": "NEW_OTHER",
    "2000": "NEW_WITH_DEFECTS",
    "2500": "NEW_OTHER",
    "3000": "LIKE_NEW",
    "4000": "VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.photo_uploader import PhotoUploader

logger = logging.getLogger(__name__)


class EbayInventoryClient:
    def __init__(self):
        self.auth = EbayAuth()
        self.uploader = PhotoUploader()
        self._policies_cache: dict | None = None  # {fulfillment_id, payment_id, return_id}

    # ── Public ────────────────────────────────────────────────────────────────

    def publish_item(self, item: Item) -> Result[dict]:
        """
        Upload photos, create/replace inventory item, create offer, publish.
        Returns Result with {"listing_id", "listing_url", "photo_urls"}.
        """
        if not self.auth.is_configured():
            return Result.failure(
                "eBay credentials not configured.",
                error_code="NOT_CONFIGURED",
            )

        image_paths = [Path(p) for p in (item.image_paths or [])]
        photo_urls = self.uploader.upload_all(image_paths) if image_paths else []

        try:
            listing_id, listing_url = self._publish_via_api(item, photo_urls)
            return Result.success({
                "listing_id": listing_id,
                "listing_url": listing_url,
                "photo_urls": photo_urls,
            })
        except _EbayApiError as exc:
            logger.error("eBay API %s for %s: %s", exc.status_code, item.sku, exc.body)
            return Result.failure(
                f"eBay API error {exc.status_code}: {exc.message}",
                error_code="API_ERROR",
                body=exc.body,
            )
        except Exception as exc:
            logger.exception("Unexpected error publishing %s", item.sku)
            return Result.failure(str(exc), error_code="API_ERROR")

    def get_seller_policies(self) -> dict:
        """
        Fetch and cache the seller's first active fulfillment, payment, and
        return policy IDs from the eBay Account API.
        Returns {"fulfillment_id": "...", "payment_id": "...", "return_id": "..."}.
        """
        if self._policies_cache is not None:
            return self._policies_cache

        base = self.auth.api_base
        headers = self._headers()
        policies: dict[str, str] = {
            "fulfillment_id": "",
            "payment_id": "",
            "return_id": "",
        }

        policy_endpoints = [
            ("fulfillment_id", f"{base}/sell/account/v1/fulfillment_policy?marketplace_id={self.auth.marketplace_id}", "fulfillmentPolicies"),
            ("payment_id",     f"{base}/sell/account/v1/payment_policy?marketplace_id={self.auth.marketplace_id}",     "paymentPolicies"),
            ("return_id",      f"{base}/sell/account/v1/return_policy?marketplace_id={self.auth.marketplace_id}",      "returnPolicies"),
        ]

        for key, url, list_key in policy_endpoints:
            try:
                resp = httpx.get(url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    items = resp.json().get(list_key, [])
                    if items:
                        policies[key] = items[0].get("fulfillmentPolicyId") \
                            or items[0].get("paymentPolicyId") \
                            or items[0].get("returnPolicyId") \
                            or ""
                        logger.info("eBay %s = %s", key, policies[key])
                else:
                    logger.warning("Failed to fetch %s: %s %s", key, resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning("Error fetching %s: %s", key, exc)

        self._policies_cache = policies
        return policies

    # ── Internal flow ─────────────────────────────────────────────────────────

    def _publish_via_api(self, item: Item, photo_urls: list[str]) -> tuple[str, str]:
        base = self.auth.api_base
        headers = self._headers()
        sku = item.sku

        # Step 1: Create/replace inventory item
        inventory_payload = self._build_inventory_payload(item, photo_urls)
        logger.info("PUT inventory_item/%s payload: %s", sku, json.dumps(inventory_payload))
        self._put(
            f"{base}/sell/inventory/v1/inventory_item/{sku}",
            headers,
            inventory_payload,
            sku=sku,
            step="create_inventory_item",
        )

        # Step 2: Create offer
        policies = self.get_seller_policies()
        offer_payload = self._build_offer_payload(item, policies)
        logger.info("POST offer for %s payload: %s", sku, json.dumps(offer_payload))
        offer_resp = self._post(
            f"{base}/sell/inventory/v1/offer",
            headers,
            offer_payload,
            sku=sku,
            step="create_offer",
        )
        offer_id = offer_resp.get("offerId", "")
        if not offer_id:
            raise _EbayApiError(0, "No offerId in response", str(offer_resp))

        # Step 3: Publish offer
        logger.info("POST offer/%s/publish for %s", offer_id, sku)
        publish_resp = self._post(
            f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
            headers,
            {},
            sku=sku,
            step="publish_offer",
        )

        listing_id = publish_resp.get("listingId", offer_id)
        env_domain = "sandbox.ebay.com" if self.auth.settings.ebay_environment == "sandbox" else "ebay.com"
        return listing_id, f"https://www.{env_domain}/itm/{listing_id}"

    # ── Payload builders ──────────────────────────────────────────────────────

    def _build_inventory_payload(self, item: Item, photo_urls: list[str]) -> dict:
        raw = str(item.condition_id or "5000")
        digits_only = re.sub(r"[^0-9]", "", raw)[:4]
        condition = CONDITION_MAP.get(digits_only, "USED_GOOD")

        product: dict = {
            "title": (item.title_final or item.title_raw or "")[:80],
            "description": item.description_final or item.title_final or "",
            "aspects": self._build_aspects(item),
        }
        if photo_urls:
            product["imageUrls"] = photo_urls[:12]

        payload: dict = {
            "product": product,
            "condition": condition,
            "availability": {
                "shipToLocationAvailability": {"quantity": 1}
            },
        }
        if item.condition_notes:
            payload["conditionDescription"] = item.condition_notes[:1000]
        return payload

    def _build_aspects(self, item: Item) -> dict[str, list[str]]:
        aspects: dict[str, list[str]] = {}
        mapping = {
            "Brand":      item.brand,
            "Type":       item.type,
            "Color":      item.color,
            "Size":       item.size,
            "Material":   item.material,
            "Style":      item.style,
            "Pattern":    item.pattern,
            "Department": item.department,
        }
        for k, v in mapping.items():
            if v:
                aspects[k] = [str(v)]
        return aspects

    def _build_offer_payload(self, item: Item, policies: dict) -> dict:
        price = round(float(item.list_price or item.estimated_price or 9.99), 2)
        category_id = str(
            item.ebay_category_id
            or CATEGORY_MAP.get(item.category_key or "", "")
            or "99"
        )
        # Derive country code from marketplace ID (e.g. "EBAY_US" → "US")
        marketplace_id = self.auth.marketplace_id
        country_code = marketplace_id.split("_", 1)[-1] if "_" in marketplace_id else "US"
        return {
            "sku": item.sku,
            "marketplaceId": marketplace_id,
            "format": "FIXED_PRICE",
            "availableQuantity": 1,
            "categoryId": category_id,
            "listingDescription": item.description_final or item.title_final or "",
            "merchantLocationKey": "default",
            "listingPolicies": {
                "fulfillmentPolicyId": policies.get("fulfillment_id", ""),
                "paymentPolicyId":     policies.get("payment_id", ""),
                "returnPolicyId":      policies.get("return_id", ""),
                "countryCode":         country_code,
            },
            "pricingSummary": {
                "price": {
                    "currency": "USD",
                    "value": f"{price:.2f}",
                }
            },
            "includeCatalogProductDetails": False,
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth.user_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Language": "en-US",
            "X-EBAY-C-MARKETPLACE-ID": self.auth.marketplace_id,
        }

    def _put(self, url: str, headers: dict, payload: dict, sku: str = "", step: str = "") -> dict:
        resp = httpx.put(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 204):
            logger.error("eBay %s error %s for %s: %s", step, resp.status_code, sku, resp.text)
            raise _EbayApiError(resp.status_code, f"{step} failed", resp.text)
        return resp.json() if resp.content else {}

    def _post(self, url: str, headers: dict, payload: dict, sku: str = "", step: str = "") -> dict:
        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            logger.error("eBay %s error %s for %s: %s", step, resp.status_code, sku, resp.text)
            raise _EbayApiError(resp.status_code, f"{step} failed", resp.text)
        return resp.json() if resp.content else {}


class _EbayApiError(Exception):
    def __init__(self, status_code: int, message: str, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = body
