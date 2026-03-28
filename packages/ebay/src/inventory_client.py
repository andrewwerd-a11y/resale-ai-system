"""
eBay Inventory API client.

Covers the full publish flow:
  1. Upload photos via Imgur
  2. PUT /sell/inventory/v1/inventory_item/{sku}
  3. POST /sell/inventory/v1/offer
  4. POST /sell/inventory/v1/offer/{offerId}/publish

All methods return Result[T] — never raise.
Environment (sandbox vs production) is selected automatically from .env.
"""
from __future__ import annotations
import json
from typing import Optional

import httpx

from packages.core.src.config import get_settings
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src import auth
from packages.ebay.src.photo_uploader import upload_item_photos, photos_already_hosted
from packages.classification.src.category_mapper import get_ebay_category_id


def _base() -> str:
    return get_settings().ebay_api_base


def _headers() -> dict[str, str]:
    return auth.user_token_headers()


# ---------------------------------------------------------------------------
# Photo preparation
# ---------------------------------------------------------------------------

def prepare_photos(item: Item) -> list[str]:
    """
    Return a list of photo URLs ready for eBay.
    - If item already has hosted URLs → use them
    - Otherwise upload via Imgur
    - Falls back to empty list (eBay requires at least 1 for most categories)
    """
    if item.hosted_photo_urls and photos_already_hosted(item.hosted_photo_urls):
        return item.hosted_photo_urls[:12]

    if item.image_paths:
        uploaded = upload_item_photos(item.image_paths, max_photos=12)
        if uploaded:
            return uploaded

    return []


# ---------------------------------------------------------------------------
# Inventory Item
# ---------------------------------------------------------------------------

def create_or_replace_inventory_item(item: Item, photo_urls: list[str]) -> Result[None]:
    """
    PUT /sell/inventory/v1/inventory_item/{sku}
    Creates or updates an inventory item record on eBay.
    """
    settings = get_settings()
    sku = item.sku
    url = f"{_base()}/sell/inventory/v1/inventory_item/{sku}"

    condition_id = item.condition_id or "3000"
    condition_desc = item.condition_notes or item.condition or ""

    # Build product aspects
    aspects: dict[str, list[str]] = {}
    _add_aspect(aspects, "Brand", item.brand)
    _add_aspect(aspects, "Type", item.item_type)
    _add_aspect(aspects, "Department", item.department)
    _add_aspect(aspects, "Size", item.size)
    _add_aspect(aspects, "Color", item.color)
    _add_aspect(aspects, "Material", item.material)
    _add_aspect(aspects, "Style", item.style)
    _add_aspect(aspects, "Author", item.author)
    _add_aspect(aspects, "Format", item.book_format)
    _add_aspect(aspects, "Franchise", item.franchise)
    _add_aspect(aspects, "Character", item.character)

    description_parts = []
    if item.title:
        description_parts.append(item.title)
    if item.condition_notes:
        description_parts.append(f"Condition: {item.condition_notes}")
    if item.features:
        description_parts.append("Features: " + ", ".join(item.features))
    if item.defects:
        description_parts.append("Defects noted: " + ", ".join(item.defects))
    if item.notes:
        description_parts.append(item.notes)
    description = "\n".join(description_parts) or (item.title or sku)

    payload: dict = {
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1,
            }
        },
        "condition": condition_id,
        "conditionDescription": condition_desc[:1000],
        "product": {
            "title": (item.title or sku)[:80],
            "description": description[:4000],
            "imageUrls": photo_urls[:24],
            "aspects": aspects,
        },
    }

    if item.isbn:
        payload["product"]["isbn"] = [item.isbn]

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.put(url, headers=_headers(), json=payload)
            if resp.status_code in (200, 201, 204):
                return Result.success(None)
            return Result.failure(
                f"create_inventory_item HTTP {resp.status_code}: {resp.text[:400]}"
            )
    except Exception as e:
        return Result.failure(f"create_inventory_item exception: {e}")


# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------

def create_offer(item: Item) -> Result[str]:
    """
    POST /sell/inventory/v1/offer
    Returns the offerId on success.
    """
    settings = get_settings()
    url = f"{_base()}/sell/inventory/v1/offer"

    category_id = get_ebay_category_id(item.category or "Collectibles")
    list_price = item.list_price or item.estimated_price or 9.99

    payload = {
        "sku": item.sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": category_id,
        "listingDescription": item.title or item.sku,
        "listingDuration": "GTC",
        "listingPolicies": {
            "fulfillmentPolicyId": "",  # Set via eBay account policies
            "paymentPolicyId": "",
            "returnPolicyId": "",
        },
        "pricingSummary": {
            "price": {
                "currency": "USD",
                "value": f"{list_price:.2f}",
            }
        },
        "merchantLocationKey": "default",
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=_headers(), json=payload)
            data = resp.json() if resp.content else {}
            if resp.status_code in (200, 201):
                offer_id = data.get("offerId", "")
                if offer_id:
                    return Result.success(offer_id)
                return Result.failure(f"create_offer: no offerId in response: {data}")
            return Result.failure(
                f"create_offer HTTP {resp.status_code}: {resp.text[:400]}"
            )
    except Exception as e:
        return Result.failure(f"create_offer exception: {e}")


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish_offer(offer_id: str) -> Result[str]:
    """
    POST /sell/inventory/v1/offer/{offerId}/publish
    Returns the listingId on success.
    """
    url = f"{_base()}/sell/inventory/v1/offer/{offer_id}/publish"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=_headers(), json={})
            data = resp.json() if resp.content else {}
            if resp.status_code in (200, 201):
                listing_id = data.get("listingId", "")
                if listing_id:
                    return Result.success(listing_id)
                return Result.failure(f"publish_offer: no listingId in response: {data}")
            return Result.failure(
                f"publish_offer HTTP {resp.status_code}: {resp.text[:400]}"
            )
    except Exception as e:
        return Result.failure(f"publish_offer exception: {e}")


def end_listing(listing_id: str) -> Result[None]:
    """
    DELETE /sell/inventory/v1/offer/{offerId} — end an active listing.
    Pass the offer_id (not listing_id) to properly close via API.
    """
    url = f"{_base()}/sell/inventory/v1/offer/{listing_id}"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.delete(url, headers=_headers())
            if resp.status_code in (200, 204):
                return Result.success(None)
            return Result.failure(
                f"end_listing HTTP {resp.status_code}: {resp.text[:400]}"
            )
    except Exception as e:
        return Result.failure(f"end_listing exception: {e}")


# ---------------------------------------------------------------------------
# Full publish flow
# ---------------------------------------------------------------------------

def publish_item(item: Item) -> Result[dict]:
    """
    Full publish flow:
      1. Upload photos
      2. Create/replace inventory item
      3. Create offer
      4. Publish offer
    Returns dict with listing_id, offer_id, listing_url.
    Never raises.
    """
    settings = get_settings()

    # Step 1: photos
    photo_urls = prepare_photos(item)

    # Step 2: inventory item
    inv_result = create_or_replace_inventory_item(item, photo_urls)
    if inv_result.is_err:
        return Result.failure(f"Inventory item failed: {inv_result.error}")

    # Step 3: offer
    offer_result = create_offer(item)
    if offer_result.is_err:
        return Result.failure(f"Create offer failed: {offer_result.error}")
    offer_id = offer_result.value

    # Step 4: publish
    publish_result = publish_offer(offer_id)
    if publish_result.is_err:
        return Result.failure(f"Publish offer failed: {publish_result.error}")
    listing_id = publish_result.value

    # Build listing URL
    if settings.is_sandbox:
        listing_url = f"https://www.sandbox.ebay.com/itm/{listing_id}"
    else:
        listing_url = f"https://www.ebay.com/itm/{listing_id}"

    return Result.success({
        "listing_id": listing_id,
        "offer_id": offer_id,
        "listing_url": listing_url,
        "photo_urls": photo_urls,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_aspect(aspects: dict, key: str, val: str | None) -> None:
    if val:
        aspects[key] = [val]
