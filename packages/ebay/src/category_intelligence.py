"""
Category Intelligence Layer.

Queries eBay's Taxonomy API to fetch required and recommended
item specifics for any given category. Returns a field template
that drives the review queue display and Claude enrichment.

eBay Taxonomy API endpoint:
GET /commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category
    ?category_id={id}

Uses App token (not user token) — no user auth required.
Results cached per category_id to avoid repeated API calls.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime

import httpx

from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.auth import EbayAuth

logger = logging.getLogger(__name__)

CATEGORY_MAP: dict[str, str] = {
    # Books
    "books":          "29223",   # Books > Antiquarian & Collectible
    "books_general":  "29223",   # Books > Antiquarian & Collectible
    # Clothing
    "clothing":       "53159",   # Clothing > Women > Tops
    "clothing_women": "53159",   # Clothing > Women > Tops
    "clothing_men":   "57990",   # Clothing > Men > Shirts
    # Shoes
    "shoes":          "95672",   # Shoes > Women > Flats
    "shoes_women":    "95672",   # Shoes > Women > Flats
    "shoes_men":      "93427",   # Shoes > Men > Boots
    # Collectibles
    "collectibles":   "40143",   # Collectibles > Decorative Collectibles
    # Toys — leaf categories (sandbox-safe)
    "toys":           "40143",   # Collectibles > Decorative Collectibles (sandbox fallback)
    "dolls":          "40143",   # Collectibles > Decorative Collectibles (sandbox fallback)
    "plush":          "40143",   # Collectibles > Decorative Collectibles (sandbox fallback)
    "bears":          "40143",   # Collectibles > Decorative Collectibles (sandbox fallback)
    # Handbags
    "handbags":       "169291",  # Handbags & Purses > Handbags
}

# Ordered list of known-good fallback leaf category IDs tried when the primary fails.
# In production the primary IDs work; these cover sandbox limitations.
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
        self._cache: dict[str, CategoryTemplate] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_template(self, category_id: str) -> Result[CategoryTemplate]:
        """
        Fetch required and recommended item specifics for a category.
        Returns CategoryTemplate with required_fields, recommended_fields,
        and field_constraints (allowed values per field).
        Cached per category_id for the session.
        """
        if category_id in self._cache:
            return Result.success(self._cache[category_id])

        s = self.auth.settings
        token = self._get_app_token() or self.auth.get_user_token()
        if not token:
            return Result.failure("No eBay token available", error_code="NO_TOKEN")

        url = (
            f"{s.ebay_api_base}/commerce/taxonomy/v1/category_tree/0"
            f"/get_item_aspects_for_category?category_id={category_id}"
        )
        try:
            resp = httpx.get(
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
            return Result.failure(str(exc), error_code="FETCH_ERROR")

        if resp.status_code != 200:
            logger.warning(
                "Category taxonomy fetch failed for %s: %s %s",
                category_id, resp.status_code, resp.text[:300],
            )
            # Try fallback chain before giving up (covers sandbox limitations)
            for fallback_id in _FALLBACK_CHAIN:
                if fallback_id == category_id:
                    continue
                fallback_result = self._fetch_raw(fallback_id, token, s)
                if fallback_result is not None:
                    logger.info(
                        "Using fallback category %s for %s", fallback_id, category_id
                    )
                    template = self._parse_template(category_id, fallback_result)
                    # Keep the original category_id so DB stores the right value
                    self._cache[category_id] = template
                    return Result.success(template)
            return Result.failure(
                f"eBay API {resp.status_code}: {resp.text[:200]}",
                error_code="API_ERROR",
            )

        raw = resp.json()
        template = self._parse_template(category_id, raw)
        self._cache[category_id] = template
        logger.info(
            "Fetched category template for %s (%s): %d required, %d recommended",
            category_id, template.category_name,
            len(template.required_fields), len(template.recommended_fields),
        )
        return Result.success(template)

    def get_category_id(self, item: Item) -> str:
        """
        Return best leaf category_id for item based on category_key, type, and title.
        All returned IDs are verified eBay leaf categories.
        """
        if item.ebay_category_id:
            return str(item.ebay_category_id)

        category = item.category_key or ""
        item_type = (item.type or "").lower()
        title = (item.title_final or item.title_raw or "").lower()

        if category == "toys":
            if any(w in title or w in item_type for w in ["doll", "porcelain", "victorian", "raggedy"]):
                return "238"    # Toys > Dolls > Porcelain Dolls
            if any(w in title or w in item_type for w in ["bear", "plush", "stuffed", "care bear"]):
                return "19009"  # Toys > Stuffed Animals
            return "19009"      # default toys → stuffed animals

        if category == "books":
            # All book sub-types use the same leaf (11092 not valid in sandbox)
            return "29223"  # Books > Antiquarian & Collectible

        if category == "clothing":
            dept = (item.department or "").lower()
            if "men" in dept and "women" not in dept:
                return "57990"  # Clothing > Men > Shirts
            return "53159"      # Clothing > Women > Tops

        if category == "shoes":
            dept = (item.department or "").lower()
            if "men" in dept and "women" not in dept:
                return "93427"  # Shoes > Men > Boots
            return "95672"      # Shoes > Women > Flats

        if category == "collectibles":
            return "40143"      # Collectibles > Decorative Collectibles

        if category == "handbags":
            return "169291"     # Handbags & Purses > Handbags

        # Exact CATEGORY_MAP lookup for any other key
        if category in CATEGORY_MAP:
            return CATEGORY_MAP[category]

        return "29223"          # safe fallback — Books > Antiquarian

    def validate_item_specifics(
        self, item: Item, template: CategoryTemplate
    ) -> ValidationResult:
        """
        Check which required fields are missing or invalid.
        Returns list of missing required fields and list of
        missing recommended fields. Never mutates the item.
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
        Return a dict of field suggestions based on template +
        existing item data. Does NOT write to DB — returns suggestions
        only. Caller decides whether to apply.
        """
        suggestions: dict[str, str] = {}
        for field_name in template.required_fields + template.recommended_fields:
            constraints = template.field_constraints.get(field_name, [])
            if len(constraints) == 1:
                suggestions[field_name] = constraints[0]
        return suggestions

    # ── Internal ────────────────────────────────────────────────────────────────

    def _fetch_raw(self, category_id: str, token: str, s) -> dict | None:
        """Fetch raw taxonomy response for a category. Returns None on any failure."""
        url = (
            f"{s.ebay_api_base}/commerce/taxonomy/v1/category_tree/0"
            f"/get_item_aspects_for_category?category_id={category_id}"
        )
        try:
            resp = httpx.get(
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
            resp = httpx.post(
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
            usage = (
                aspect.get("aspectConstraint", {})
                .get("aspectUsage", "OPTIONAL")
            )
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

        # Try to pull category name from the response
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

    def _flatten_item_values(self, item: Item) -> dict[str, str]:
        """Build a flat case-normalised dict of all item field values."""
        vals: dict[str, str] = {}
        # Known scalar attributes
        for attr in [
            "brand", "type", "color", "size", "material", "style", "pattern",
            "department", "author", "publisher", "format", "edition", "era",
            "language", "topic", "isbn", "character", "franchise", "subject",
            "theme", "model", "mpn", "upc", "artist", "country_region",
            "subcategory", "size_type", "fit", "occasion", "season",
        ]:
            val = getattr(item, attr, None)
            if val:
                # Store under both the raw attr name and the eBay-style title-case key
                vals[attr] = str(val)
                vals[attr.replace("_", " ").title()] = str(val)
                vals[attr.lower()] = str(val)
        # item_specifics dict
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
        """Try several normalisations of field_name against vals."""
        return (
            vals.get(field_name)
            or vals.get(field_name.lower())
            or vals.get(field_name.lower().replace(" ", "_"))
            or ""
        )
