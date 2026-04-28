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
    "3000": "USED_GOOD",
    "4000": "VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.aspect_validation import validate_aspects
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src import http_client as ebay_http
from packages.ebay.src.photo_uploader import PhotoUploader
from packages.ebay.src.public_image_urls import (
    extract_public_image_urls,
    looks_like_public_image_url_candidate,
    normalize_public_image_urls,
)

logger = logging.getLogger(__name__)


class EbayInventoryClient:
    def __init__(self):
        self.auth = EbayAuth()
        self.uploader = PhotoUploader()
        self._policies_cache: dict | None = None  # {fulfillment_id, payment_id, return_id}
        self._location_key_cache: str | None = None

    # ── Public ────────────────────────────────────────────────────────────────

    def publish_item(self, item: Item) -> Result[dict]:
        """
        Upload photos, create/replace inventory item, create offer, publish.
        Returns Result with {"listing_id", "listing_url", "photo_urls", "offer_id"}.
        """
        if hasattr(self.auth, "is_configured") and not self.auth.is_configured():
            return Result.failure(
                "eBay credentials not configured.",
                error_code="NOT_CONFIGURED",
            )

        token_state = self.auth.resolve_user_token()
        settings = self.auth.settings
        if not (settings.ebay_app_id and settings.ebay_cert_id and token_state["token"]):
            return Result.failure(
                "eBay credentials are not ready for authenticated requests.",
                error_code="AUTH_NOT_READY",
                auth_issue_code=token_state["issue_code"] or "missing_token",
            )

        aspect_validation = validate_aspects(self._build_item_specifics(item))
        if not aspect_validation["ok"]:
            return Result.failure(
                "eBay aspect validation failed before publish.",
                error_code="ASPECT_VALIDATION",
                blockers=aspect_validation["blockers"],
                issues=aspect_validation["issues"],
            )

        raw_image_values = [str(path).strip() for path in (item.image_paths or []) if str(path).strip()]
        hosted_photo_urls, invalid_hosted_urls = normalize_public_image_urls(
            [value for value in raw_image_values if looks_like_public_image_url_candidate(value)]
        )
        if invalid_hosted_urls:
            return Result.failure(
                "eBay image URL validation failed before publish.",
                error_code="INVALID_IMAGE_URL",
                blockers=["Invalid public image URL(s) detected before eBay publish."],
                invalid_image_urls=invalid_hosted_urls,
            )

        image_paths = [Path(p) for p in raw_image_values if not looks_like_public_image_url_candidate(p)]

        # Sort photos by quality before upload if photo_sort == "auto"
        if image_paths:
            try:
                from packages.core.src.settings import get_setting
                sort_mode = get_setting("photo_sort") or "auto"
                if sort_mode == "auto":
                    from packages.enrichment.src.photo_scorer import rank_photos
                    image_paths = rank_photos(image_paths)
                    logger.debug("Photos ranked for %s; cover=%s", item.sku, image_paths[0].name if image_paths else "none")
            except Exception as _exc:
                logger.warning("Photo ranking skipped for %s: %s", item.sku, _exc)

        if hosted_photo_urls:
            photo_urls = hosted_photo_urls
        else:
            uploaded_photo_urls = self.uploader.upload_all(image_paths) if image_paths else []
            photo_urls, invalid_uploaded_urls = normalize_public_image_urls([str(url) for url in uploaded_photo_urls])
            if invalid_uploaded_urls:
                return Result.failure(
                    "eBay image URL validation failed before publish.",
                    error_code="INVALID_IMAGE_URL",
                    blockers=["Invalid public image URL(s) detected before eBay publish."],
                    invalid_image_urls=invalid_uploaded_urls,
                )

        try:
            publish_result = self._publish_via_api(item, photo_urls)
            recovered_existing_offer = False
            used_existing_offer = False
            if isinstance(publish_result, dict):
                listing_id = str(publish_result.get("listing_id") or "")
                listing_url = str(publish_result.get("listing_url") or "")
                offer_id = str(publish_result.get("offer_id") or "")
                recovered_existing_offer = bool(publish_result.get("recovered_existing_offer"))
                used_existing_offer = bool(publish_result.get("used_existing_offer"))
            elif isinstance(publish_result, tuple) and len(publish_result) == 3:
                listing_id, listing_url, offer_id = publish_result
            elif isinstance(publish_result, tuple) and len(publish_result) == 2:
                listing_id, listing_url = publish_result
                offer_id = ""
            else:
                raise ValueError("Unexpected publish result shape")

            # Resolve promotion percentage: item-level overrides DB default
            promo_pct = 0.0
            try:
                from packages.core.src.settings import get_setting as _gs
                promo_pct = float(item.promotion_pct or 0) or float(_gs("default_promotion_pct") or 0)
            except Exception:
                pass
            if promo_pct > 0:
                logger.info("Promotion pct %.1f%% set for %s — Marketing API call pending", promo_pct, item.sku)
                # Marketing API calls (bulk_create_ads_by_listing_id) implemented in Phase 5B

            return Result.success({
                "listing_id": listing_id,
                "listing_url": listing_url,
                "photo_urls": photo_urls,
                "offer_id": offer_id,
                "recovered_existing_offer": recovered_existing_offer,
                "used_existing_offer": used_existing_offer,
            })
        except _EbayApiError as exc:
            logger.error("eBay API %s for %s: %s", exc.status_code, item.sku, exc.body)
            return Result.failure(
                f"eBay API error {exc.status_code}: {exc.message}",
                error_code="API_ERROR",
                body=exc.body,
                stage=str(exc.context.get("stage") or exc.step or ""),
                offer_id=str((exc.context or {}).get("offer_id") or ""),
                recovered_existing_offer=bool((exc.context or {}).get("recovered_existing_offer")),
                used_existing_offer=bool((exc.context or {}).get("used_existing_offer")),
                already_published=bool((exc.context or {}).get("already_published")),
                next_action=(exc.context or {}).get("next_action"),
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
        settings = self.auth.settings
        policies: dict[str, str] = {
            "fulfillment_id": str(settings.ebay_fulfillment_policy_id or "").strip(),
            "payment_id": str(settings.ebay_payment_policy_id or "").strip(),
            "return_id": str(settings.ebay_return_policy_id or "").strip(),
        }

        policy_endpoints = [
            ("fulfillment_id", f"{base}/sell/account/v1/fulfillment_policy?marketplace_id={self.auth.marketplace_id}", "fulfillmentPolicies"),
            ("payment_id",     f"{base}/sell/account/v1/payment_policy?marketplace_id={self.auth.marketplace_id}",     "paymentPolicies"),
            ("return_id",      f"{base}/sell/account/v1/return_policy?marketplace_id={self.auth.marketplace_id}",      "returnPolicies"),
        ]

        for key, url, list_key in policy_endpoints:
            if policies.get(key):
                logger.info("Using configured %s = %s", key, policies[key])
                continue
            try:
                resp = ebay_http.get(url, headers=headers, timeout=15)
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

    def get_listing_status(self, listing_id: str) -> Result[dict]:
        """
        Resolve listing status from Inventory offers by listing ID.
        Returns Result.success({"status": "<STATUS>", "offer_id": "<id>"}) on success.
        """
        if not listing_id:
            return Result.failure("listing_id_required", error_code="INVALID_INPUT")
        if not self.auth.is_configured():
            return Result.failure("eBay credentials not configured.", error_code="NOT_CONFIGURED")

        try:
            resp = ebay_http.get(
                f"{self.auth.api_base}/sell/inventory/v1/offer",
                headers=self._headers(),
                params={"listing_id": listing_id, "limit": "1"},
                timeout=20,
            )
        except Exception as exc:
            return Result.failure(f"listing_status_request_failed: {exc}", error_code="REQUEST_FAILED")

        if resp.status_code != 200:
            return Result.failure(
                f"listing_status_http_{resp.status_code}",
                error_code="API_ERROR",
                body=resp.text[:500],
            )

        try:
            payload = resp.json() if resp.content else {}
        except Exception as exc:
            return Result.failure(f"listing_status_parse_failed: {exc}", error_code="PARSE_ERROR")

        offers = payload.get("offers") or []
        if not offers:
            return Result.failure("listing_not_found", error_code="NOT_FOUND", body=payload)

        offer = offers[0] or {}
        status = str(offer.get("status") or "UNKNOWN").upper()
        return Result.success(
            {
                "status": status,
                "offer_id": str(offer.get("offerId") or ""),
            }
        )

    # ── Internal flow ─────────────────────────────────────────────────────────

    def _publish_via_api(self, item: Item, photo_urls: list[str]) -> dict:
        base = self.auth.api_base
        headers = self._headers()
        sku = item.sku
        recovered_existing_offer = False
        used_existing_offer = False

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

        offer_id = ""
        if self._should_publish_existing_offer(item):
            offer_id = str(item.offer_id or "").strip()
            used_existing_offer = True
        else:
            # Step 2: Create offer
            policies = self.get_seller_policies()
            offer_payload = self._build_offer_payload(item, policies)
            logger.info("POST offer for %s payload: %s", sku, json.dumps(offer_payload))
            try:
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
            except _EbayApiError as exc:
                if self._is_existing_offer_error(exc.body):
                    offer_id = self._extract_offer_id(exc.body)
                    if not offer_id:
                        raise _EbayApiError(
                            exc.status_code,
                            "create_offer failed: existing offer reported but no offerId was returned",
                            exc.body,
                        )
                    recovered_existing_offer = True
                else:
                    raise

        # Step 3: Publish offer
        logger.info("POST offer/%s/publish for %s", offer_id, sku)
        try:
            publish_resp = self._post(
                f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
                headers,
                {},
                sku=sku,
                step="publish_offer",
            )
        except _EbayApiError as exc:
            exc.context.setdefault("offer_id", offer_id)
            exc.context.setdefault("recovered_existing_offer", recovered_existing_offer)
            exc.context.setdefault("used_existing_offer", used_existing_offer)
            if self._is_already_published_error(exc.body):
                exc.context.setdefault("already_published", True)
                exc.context.setdefault(
                    "next_action",
                    "Run constrained listings sync for this SKU to recover listing identifiers from eBay.",
                )
            raise

        listing_id = publish_resp.get("listingId", offer_id)
        env_domain = "sandbox.ebay.com" if self.auth.settings.ebay_environment == "sandbox" else "ebay.com"
        return {
            "listing_id": listing_id,
            "listing_url": f"https://www.{env_domain}/itm/{listing_id}",
            "offer_id": offer_id,
            "recovered_existing_offer": recovered_existing_offer,
            "used_existing_offer": used_existing_offer,
        }

    # ── Payload builders ──────────────────────────────────────────────────────

    def _build_inventory_payload(self, item: Item, photo_urls: list[str]) -> dict:
        raw = str(item.condition_id or "5000")
        digits_only = re.sub(r"[^0-9]", "", raw)[:4]
        condition = CONDITION_MAP.get(digits_only, "USED_GOOD")

        # Load category template from disk cache if available
        template = None
        cat_id = item.ebay_category_id
        if cat_id:
            try:
                from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
                template = CategorySpreadsheet().load_template(cat_id)
            except Exception:
                pass

        product: dict = {
            "title": (item.title_final or item.title_raw or "")[:80],
            "description": item.description_final or item.title_final or "",
            "aspects": self._build_item_specifics(item, template),
        }
        normalized_photo_urls, invalid_photo_urls = normalize_public_image_urls([str(url) for url in photo_urls])
        if invalid_photo_urls:
            raise ValueError(
                f"Invalid public image URL(s): {', '.join(str(url) for url in invalid_photo_urls)}"
            )
        if normalized_photo_urls:
            product["imageUrls"] = normalized_photo_urls[:12]

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

    def _collect_item_specifics(self, item: Item, template=None) -> dict[str, list[str]]:
        """
        Collect raw item specifics before eBay-specific normalization/validation.
        Priority: manually set values > AI extracted values > template defaults.
        All values must be lists of strings as eBay requires.
        """
        specifics: dict[str, list[str]] = {}

        # Load stored item_specifics
        stored: dict = {}
        if isinstance(item.item_specifics, dict):
            stored = item.item_specifics
        elif isinstance(item.item_specifics, str):
            try:
                stored = json.loads(item.item_specifics)
            except Exception:
                stored = {}

        # Standard field mapping (attr → eBay field name)
        field_map = {
            "Brand":      item.brand,
            "Type":       item.type,
            "Color":      item.color,
            "Size":       item.size,
            "Material":   item.material,
            "Style":      item.style,
            "Pattern":    item.pattern,
            "Department": item.department,
        }
        for ebay_field, val in field_map.items():
            if val:
                specifics[ebay_field] = [str(val)]

        # Apply stored item_specifics (override standard fields)
        for k, v in stored.items():
            if v:
                specifics[k] = [str(v)] if not isinstance(v, list) else [str(x) for x in v]

        # Apply required template fields if still missing
        if template is not None:
            required_fields = getattr(template, "required_fields", [])
            field_constraints = getattr(template, "field_constraints", {})
            for field_name in required_fields:
                if field_name not in specifics:
                    # Use first allowed value as safe default
                    defaults = field_constraints.get(field_name, [])
                    if defaults:
                        specifics[field_name] = [defaults[0]]

        return specifics

    def _build_item_specifics(self, item: Item, template=None) -> dict[str, list[str]]:
        specifics = self._collect_item_specifics(item, template)
        aspect_validation = validate_aspects(specifics)
        return aspect_validation["normalized_aspects"]

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

    def extract_hosted_photo_urls(self, values: list[str]) -> list[str]:
        return extract_public_image_urls([str(value) for value in values])

    def _build_offer_payload(
        self,
        item: Item,
        policies: dict,
        merchant_location_key: str | None = None,
    ) -> dict:
        price = round(float(item.list_price or item.estimated_price or 9.99), 2)
        category_id = str(
            item.ebay_category_id
            or CATEGORY_MAP.get(item.category_key or "", "")
            or "99"
        )
        # Derive country code from marketplace ID (e.g. "EBAY_US" → "US")
        marketplace_id = self.auth.marketplace_id
        country_code = marketplace_id.split("_", 1)[-1] if "_" in marketplace_id else "US"
        location_key = merchant_location_key or self.get_merchant_location_key()
        return {
            "sku": item.sku,
            "marketplaceId": marketplace_id,
            "format": "FIXED_PRICE",
            "availableQuantity": 1,
            "categoryId": category_id,
            "listingDescription": item.description_final or item.title_final or "",
            "merchantLocationKey": location_key,
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

    def get_merchant_location_key(self) -> str:
        if self._location_key_cache:
            return self._location_key_cache

        headers = self._headers()
        base = self.auth.api_base
        list_resp = ebay_http.get(f"{base}/sell/inventory/v1/location", headers=headers, timeout=20)
        if list_resp.status_code == 200:
            locations = (list_resp.json() or {}).get("locations", [])
            if locations:
                key = (locations[0] or {}).get("merchantLocationKey")
                if key:
                    self._location_key_cache = key
                    return key
        elif list_resp.status_code not in (200, 404):
            raise _EbayApiError(list_resp.status_code, "list_location failed", list_resp.text)

        create_payload = {
            "location": {
                "address": {
                    "addressLine1": "123 Main St",
                    "city": "Rome",
                    "stateOrProvince": "NY",
                    "postalCode": "13440",
                    "country": "US",
                }
            },
            "locationTypes": ["WAREHOUSE"],
            "name": "Default Location",
            "merchantLocationStatus": "ENABLED",
        }
        create_resp = ebay_http.post(
            f"{base}/sell/inventory/v1/location/default",
            headers=headers,
            json=create_payload,
            timeout=20,
        )
        if create_resp.status_code not in (200, 201, 204):
            raise _EbayApiError(create_resp.status_code, "create_location failed", create_resp.text)

        self._location_key_cache = "default"
        return "default"

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
        resp = ebay_http.put(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 204):
            logger.error("eBay %s error %s for %s: %s", step, resp.status_code, sku, resp.text)
            raise _EbayApiError(resp.status_code, f"{step} failed", resp.text, context={"stage": step}, step=step)
        return resp.json() if resp.content else {}

    def _post(self, url: str, headers: dict, payload: dict, sku: str = "", step: str = "") -> dict:
        resp = ebay_http.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            logger.error("eBay %s error %s for %s: %s", step, resp.status_code, sku, resp.text)
            raise _EbayApiError(resp.status_code, f"{step} failed", resp.text, context={"stage": step}, step=step)
        return resp.json() if resp.content else {}

    @staticmethod
    def _is_existing_offer_error(body: str) -> bool:
        return "offer entity already exists" in str(body or "").lower()

    @staticmethod
    def _is_already_published_error(body: str) -> bool:
        return "already published" in str(body or "").lower()

    @staticmethod
    def _should_publish_existing_offer(item: Item) -> bool:
        offer_id = str(item.offer_id or "").strip()
        listing_id = str(item.listing_id or "").strip()
        status = str(item.status or "").strip().lower()
        return bool(offer_id) and not listing_id and status != "listed"

    @staticmethod
    def _extract_offer_id(body: str) -> str:
        text = str(body or "")
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

        if isinstance(payload, dict):
            direct = str(payload.get("offerId") or "").strip()
            if direct:
                return direct
            for error in payload.get("errors", []) or []:
                for parameter in error.get("parameters", []) or []:
                    name = str(parameter.get("name") or "").strip().lower()
                    value = str(parameter.get("value") or "").strip()
                    if name == "offerid" and value:
                        return value

        match = re.search(r'"offerId"\s*:\s*"?(?P<offer_id>\d+)"?', text)
        if match:
            return str(match.group("offer_id") or "")
        match = re.search(r"offerid[^0-9]*(?P<offer_id>\d{6,})", text, flags=re.IGNORECASE)
        if match:
            return str(match.group("offer_id") or "")
        return ""


class _EbayApiError(Exception):
    def __init__(self, status_code: int, message: str, body: str = "", context: dict | None = None, step: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = body
        self.context = context or {}
        self.step = step
