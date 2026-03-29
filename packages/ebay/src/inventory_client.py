"""
EbayInventoryClient — publishes items to eBay via the Inventory API.

Phase 3 stub: when eBay credentials are not configured this returns a
Result.failure so callers can surface a clear error rather than crashing.
Full OAuth + Inventory API + Offer API implementation goes here in Phase 3.
"""
from __future__ import annotations

import json
from pathlib import Path

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.photo_uploader import PhotoUploader


class EbayInventoryClient:
    def __init__(self):
        self.auth = EbayAuth()
        self.uploader = PhotoUploader()

    def publish_item(self, item: Item) -> Result[dict]:
        """
        Upload photos, create/replace inventory item, create offer, publish.
        Returns Result.success with {"listing_id", "listing_url", "photo_urls"}.

        Currently a stub — returns failure if credentials are absent,
        otherwise performs the full Inventory API flow.
        """
        if not self.auth.is_configured():
            return Result.failure(
                "eBay credentials not configured. Set ebay_sandbox_* or ebay_prod_* in .env",
                error_code="NOT_CONFIGURED",
            )

        # Upload photos
        image_paths = [Path(p) for p in (item.image_paths or [])]
        photo_urls = self.uploader.upload_all(image_paths) if image_paths else []

        try:
            listing_id, listing_url = self._publish_via_api(item, photo_urls)
            return Result.success({
                "listing_id": listing_id,
                "listing_url": listing_url,
                "photo_urls": photo_urls,
            })
        except Exception as exc:
            return Result.failure(str(exc), error_code="API_ERROR")

    def _publish_via_api(self, item: Item, photo_urls: list[str]) -> tuple[str, str]:
        """
        Full eBay Inventory API flow:
          1. PUT /sell/inventory/v1/inventory_item/{sku}
          2. POST /sell/inventory/v1/offer  (or GET + update if exists)
          3. POST /sell/inventory/v1/offer/{offerId}/publish

        Raises on any HTTP error.
        """
        import urllib.request

        base = self.auth.api_base
        token = self.auth.user_token
        sku = item.sku

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": self.auth.marketplace_id,
        }

        # --- Step 1: Create/replace inventory item ---
        inventory_payload = self._build_inventory_payload(item, photo_urls)
        self._put(
            f"{base}/sell/inventory/v1/inventory_item/{sku}",
            headers,
            inventory_payload,
        )

        # --- Step 2: Create offer ---
        offer_payload = self._build_offer_payload(item)
        offer_resp = self._post(
            f"{base}/sell/inventory/v1/offer",
            headers,
            offer_payload,
        )
        offer_id = offer_resp.get("offerId", "")

        # --- Step 3: Publish offer ---
        publish_resp = self._post(
            f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
            headers,
            {},
        )
        listing_id = publish_resp.get("listingId", offer_id)
        env_domain = "sandbox.ebay.com" if self.auth.settings.ebay_environment == "sandbox" else "ebay.com"
        listing_url = f"https://www.{env_domain}/itm/{listing_id}"
        return listing_id, listing_url

    def _build_inventory_payload(self, item: Item, photo_urls: list[str]) -> dict:
        payload: dict = {
            "product": {
                "title": (item.title_final or item.title_raw or "")[:80],
                "description": item.description_final or item.title_final or "",
                "aspects": self._build_aspects(item),
            },
            "condition": item.condition_id or "5000",
            "conditionDescription": item.condition_notes or "",
            "availability": {
                "shipToLocationAvailability": {"quantity": 1}
            },
        }
        if photo_urls:
            payload["product"]["imageUrls"] = photo_urls[:12]
        return payload

    def _build_aspects(self, item: Item) -> dict[str, list[str]]:
        aspects: dict[str, list[str]] = {}
        mapping = {
            "Brand": item.brand,
            "Type": item.type,
            "Color": item.color,
            "Size": item.size,
            "Material": item.material,
            "Style": item.style,
            "Pattern": item.pattern,
            "Department": item.department,
        }
        for k, v in mapping.items():
            if v:
                aspects[k] = [str(v)]
        return aspects

    def _build_offer_payload(self, item: Item) -> dict:
        return {
            "sku": item.sku,
            "marketplaceId": self.auth.marketplace_id,
            "format": "FIXED_PRICE",
            "availableQuantity": 1,
            "categoryId": item.ebay_category_id or "99",
            "pricingSummary": {
                "price": {
                    "value": str(item.list_price or item.estimated_price or 9.99),
                    "currency": "USD",
                }
            },
            "listingPolicies": {},
        }

    def _put(self, url: str, headers: dict, payload: dict) -> dict:
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else {}

    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
