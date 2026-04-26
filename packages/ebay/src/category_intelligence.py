"""
Category Intelligence Layer.

Category resolution order:
  1. eBay Category Suggestions API — title-based, always returns valid leaf IDs
  2. CATEGORY_MAP — static fallback when Suggestions API is unavailable

Item aspects fetched via:
  GET https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category
      ?category_id={leaf_id}

Uses App token (not user token) — no user auth required.
Templates cached per category_id; suggestions cached per title hash.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src import http_client as ebay_http

logger = logging.getLogger(__name__)

# Taxonomy API is always production — it is not sandboxed.
_TAXONOMY_HOST = "https://api.ebay.com"

# Static fallback map — used only when Suggestions API is unavailable.
CATEGORY_MAP: dict[str, str] = {
    # Books
    "books":          "29223",   # Books > Antiquarian & Collectible
    "books_general":  "29223",
    # Clothing
    "clothing":       "53159",   # Clothing > Women > Tops
    "clothing_women": "53159",
    "clothing_men":   "57990",   # Clothing > Men > Shirts
    # Shoes
    "shoes":          "95672",   # Shoes > Women > Flats
    "shoes_women":    "95672",
    "shoes_men":      "93427",   # Shoes > Men > Boots
    # Collectibles
    "collectibles":   "40143",   # Collectibles > Decorative Collectibles
    # Toys
    "toys":           "48084",   # Toys & Hobbies > Stuffed Animals > Bears
    "dolls":          "44201",   # Dolls & Bears > Dolls > Porcelain & China
    "plush":          "48084",
    "bears":          "48084",
    # Handbags
    "handbags":       "169291",  # Handbags & Purses > Handbags
}

# Generic fallback chain for get_template when the resolved category ID fails.
_FALLBACK_CHAIN = ["29223", "53159", "40143", "95672", "57990"]


@dataclass
class CategoryTemplate:
    category_id: str
    category_name: str
    required_fields: list[str]
    recommended_fields: list[str]
    field_constraints: dict[str, list[str]]   # field → allowed values
    fetched_at: datetime
    raw_response: dict                         # full eBay response for debugging


@dataclass
class ValidationResult:
    missing_required: list[str]
    missing_recommended: list[str]
    invalid_fields: list[str]   # fields with values not in allowed list
    is_publish_ready: bool      # True only if missing_required is empty


class CategoryIntelligence:
    def __init__(self) -> None:
        self.auth = EbayAuth()
        self._template_cache: dict[str, CategoryTemplate] = {}
        self._suggestion_cache: dict[str, tuple[str, str]] = {}  # title_hash → (id, name)

    # ── Public API ─────────────────────────────────────────────────────────────

    def suggest_category(self, item: Item) -> Result[tuple[str, str]]:
        """
        Ask eBay's Taxonomy API to suggest the correct leaf category
        based on the item title. Returns (category_id, category_name).
        Uses App token. Results cached by title hash.
        """
        title = item.title_final or item.title_raw or ""
        if not title:
            return Result.failure("no_title_for_suggestion")

        title_hash = hashlib.md5(title[:80].encode()).hexdigest()
        if title_hash in self._suggestion_cache:
            return Result.success(self._suggestion_cache[title_hash])

        token = self._get_app_token() or self.auth.get_user_token()
        if not token:
            return Result.failure("No eBay token available")

        url = (
            f"{_TAXONOMY_HOST}/commerce/taxonomy/v1/category_tree/0"
            f"/get_category_suggestions"
        )
        print(f"[CategoryIntelligence] SUGGEST {url}?q={title[:60]!r}")
        try:
            resp = ebay_http.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                params={"q": title[:80]},
                timeout=15,
            )
        except Exception as exc:
            code = self._classify_exception(exc)
            return Result.failure(f"suggestion_error: {exc}", error_code=code)

        if resp.status_code != 200:
            code = "AUTH_FAILED" if resp.status_code in (401, 403) else "SUGGESTION_API_ERROR"
            return Result.failure(
                f"suggestion_failed: {resp.status_code} {resp.text[:200]}",
                error_code=code,
            )

        try:
            payload = resp.json()
        except Exception as exc:
            return Result.failure(
                f"suggestion_malformed_response: {exc}",
                error_code="MALFORMED_RESPONSE",
            )
        suggestions = payload.get("categorySuggestions", [])
        if not suggestions:
            return Result.failure("suggestion_empty: no results returned")

        cat = suggestions[0]["category"]
        category_id = cat["categoryId"]
        category_name = cat["categoryName"]
        self._suggestion_cache[title_hash] = (category_id, category_name)
        logger.info(
            "Suggested category for %r: %s (%s)", title[:40], category_id, category_name
        )
        return Result.success((category_id, category_name))

    def get_category_id(self, item: Item) -> tuple[str, str]:
        """
        Return (category_id, category_name) for an item.
        Tries eBay Category Suggestions API first (title-based, always leaf IDs).
        Falls back to CATEGORY_MAP when the API is unavailable.
        """
        suggestion = self.suggest_category(item)
        if suggestion.ok:
            return suggestion.value

        logger.debug(
            "Suggestion unavailable for %s (%s), using CATEGORY_MAP",
            item.sku, suggestion.error,
        )
        fallback_id = CATEGORY_MAP.get(item.category_key or "", "29223")
        return (fallback_id, item.category_key or "unknown")

    def get_template(self, category_id: str) -> Result[CategoryTemplate]:
        """
        Fetch required and recommended item specifics for a category.
        Returns CategoryTemplate with required_fields, recommended_fields,
        and field_constraints (allowed values per field).
        Cached per category_id for the session.
        """
        if category_id in self._template_cache:
            return Result.success(self._template_cache[category_id])

        s = self.auth.settings
        token = self._get_app_token() or self.auth.get_user_token()
        if not token:
            return Result.failure("No eBay token available", error_code="NO_TOKEN")

        url = (
            f"{_TAXONOMY_HOST}/commerce/taxonomy/v1/category_tree/0"
            f"/get_item_aspects_for_category?category_id={category_id}"
        )
        print(f"[CategoryIntelligence] ASPECTS {url}")
        try:
            resp = ebay_http.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "X-EBAY-C-MARKETPLACE-ID": s.ebay_marketplace_id,
                },
                timeout=20,
            )
        except Exception as exc:
            logger.error("Category template fetch error for %s: %s", category_id, exc)
            return Result.failure(
                f"template_fetch_error: {exc}",
                error_code=self._classify_exception(exc),
            )

        if resp.status_code != 200:
            logger.warning(
                "Category aspects fetch failed for %s: %s %s",
                category_id, resp.status_code, resp.text[:300],
            )
            # Try generic fallback chain — category IDs from suggestions should
            # never hit this path, but static CATEGORY_MAP fallbacks might.
            for fallback_id in _FALLBACK_CHAIN:
                if fallback_id == category_id:
                    continue
                raw = self._fetch_aspects_raw(fallback_id, token, s)
                if raw is not None:
                    logger.info("Using fallback aspects from %s for %s", fallback_id, category_id)
                    template = self._parse_template(category_id, raw)
                    self._template_cache[category_id] = template
                    return Result.success(template)
            return Result.failure(
                f"eBay API {resp.status_code}: {resp.text[:200]}",
                error_code="AUTH_FAILED" if resp.status_code in (401, 403) else "API_ERROR",
            )

        try:
            raw = resp.json()
        except Exception as exc:
            return Result.failure(
                f"template_malformed_response: {exc}",
                error_code="MALFORMED_RESPONSE",
            )
        template = self._parse_template(category_id, raw)
        self._template_cache[category_id] = template
        logger.info(
            "Fetched aspects for %s (%s): %d required, %d recommended",
            category_id, template.category_name,
            len(template.required_fields), len(template.recommended_fields),
        )
        return Result.success(template)

    def validate_item_specifics(
        self, item: Item, template: CategoryTemplate
    ) -> ValidationResult:
        """
        Check which required fields are missing or invalid.
        Never mutates the item.
        """
        item_vals = self._flatten_item_values(item)

        missing_required: list[str] = []
        missing_recommended: list[str] = []
        invalid_fields: list[str] = []

        for field_name in template.required_fields:
            val = self._lookup(item_vals, field_name)
            if not val:
                missing_required.append(field_name)
            else:
                allowed = template.field_constraints.get(field_name, [])
                if allowed and val not in allowed:
                    invalid_fields.append(field_name)

        for field_name in template.recommended_fields:
            val = self._lookup(item_vals, field_name)
            if not val:
                missing_recommended.append(field_name)

        return ValidationResult(
            missing_required=missing_required,
            missing_recommended=missing_recommended,
            invalid_fields=invalid_fields,
            is_publish_ready=len(missing_required) == 0,
        )

    def apply_template_to_item(
        self, item: Item, template: CategoryTemplate
    ) -> dict:
        """
        Return field suggestions from template constraints.
        Does NOT write to DB — caller decides whether to apply.
        """
        suggestions: dict[str, str] = {}
        for field_name in template.required_fields + template.recommended_fields:
            constraints = template.field_constraints.get(field_name, [])
            if len(constraints) == 1:
                suggestions[field_name] = constraints[0]
        return suggestions

    # ── Internal ────────────────────────────────────────────────────────────────

    def _fetch_aspects_raw(self, category_id: str, token: str, s) -> dict | None:
        """Fetch raw aspects response for a category. Returns None on failure."""
        url = (
            f"{_TAXONOMY_HOST}/commerce/taxonomy/v1/category_tree/0"
            f"/get_item_aspects_for_category?category_id={category_id}"
        )
        print(f"[CategoryIntelligence] ASPECTS {url}  (fallback)")
        try:
            resp = ebay_http.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "X-EBAY-C-MARKETPLACE-ID": s.ebay_marketplace_id,
                },
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.debug("Fallback aspects %s → %s", category_id, resp.status_code)
        except Exception:
            pass
        return None

    def _get_app_token(self) -> str:
        """Fetch a client-credentials (app-only) token. Read-only scope."""
        s = self.auth.settings
        if not s.ebay_app_id or not s.ebay_cert_id:
            return ""
        credentials = base64.b64encode(
            f"{s.ebay_app_id}:{s.ebay_cert_id}".encode()
        ).decode()
        try:
            resp = ebay_http.post(
                f"{s.ebay_api_base}/identity/v1/oauth2/token",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("access_token", "")
        except Exception as exc:
            logger.debug("App token fetch failed: %s", exc)
        return ""

    def _parse_template(self, category_id: str, raw: dict) -> CategoryTemplate:
        """Parse eBay Taxonomy API response into a CategoryTemplate."""
        aspects = raw.get("aspects", [])
        required_fields: list[str] = []
        recommended_fields: list[str] = []
        field_constraints: dict[str, list[str]] = {}

        for aspect in aspects:
            name = aspect.get("localizedAspectName", "")
            if not name:
                continue
            usage = aspect.get("aspectConstraint", {}).get("aspectUsage", "OPTIONAL")
            allowed: list[str] = [
                v.get("localizedValue", "")
                for v in aspect.get("aspectValues", [])
                if v.get("localizedValue")
            ]
            if allowed:
                field_constraints[name] = allowed
            if usage == "REQUIRED":
                required_fields.append(name)
            elif usage == "RECOMMENDED":
                recommended_fields.append(name)

        ancestors = raw.get("categoryTreeNodeAncestors", [])
        cat_name = ancestors[-1].get("categoryName", "") if ancestors else ""
        if not cat_name:
            cat_name = f"Category {category_id}"

        return CategoryTemplate(
            category_id=category_id,
            category_name=cat_name,
            required_fields=required_fields,
            recommended_fields=recommended_fields,
            field_constraints=field_constraints,
            fetched_at=datetime.utcnow(),
            raw_response=raw,
        )

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        text = f"{type(exc).__name__}: {exc}".lower()
        if "timeout" in text:
            return "UPSTREAM_TIMEOUT"
        if (
            "connect" in text
            or "connection" in text
            or "dns" in text
            or "name or service not known" in text
            or "winerror 10061" in text
        ):
            return "UPSTREAM_CONNECTION"
        if "proxy" in text:
            return "UPSTREAM_PROXY"
        return "UPSTREAM_ERROR"

    def _flatten_item_values(self, item: Item) -> dict[str, str]:
        """Build a flat case-normalised dict of all item field values."""
        vals: dict[str, str] = {}
        for attr in [
            "brand", "type", "color", "size", "material", "style", "pattern",
            "department", "author", "publisher", "format", "edition", "era",
            "language", "topic", "isbn", "character", "franchise", "subject",
            "theme", "model", "mpn", "upc", "artist", "country_region",
            "subcategory", "size_type", "fit", "occasion", "season",
        ]:
            val = getattr(item, attr, None)
            if val:
                vals[attr] = str(val)
                vals[attr.replace("_", " ").title()] = str(val)
                vals[attr.lower()] = str(val)
        if isinstance(item.item_specifics, dict):
            for k, v in item.item_specifics.items():
                if v:
                    s = str(v)
                    vals[k] = s
                    vals[k.lower()] = s
                    vals[k.lower().replace(" ", "_")] = s
        return vals

    @staticmethod
    def _lookup(vals: dict[str, str], field_name: str) -> str:
        return (
            vals.get(field_name)
            or vals.get(field_name.lower())
            or vals.get(field_name.lower().replace(" ", "_"))
            or ""
        )
