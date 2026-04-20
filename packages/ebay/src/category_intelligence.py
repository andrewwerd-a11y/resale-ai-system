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
    "books":           "29223",   # Books > Antiquarian & Collectible
    "books_general":   "267",     # Books
    # Clothing
    "clothing_women":  "15724",   # Women's Clothing
    "clothing_men":    "1059",    # Men's Clothing
    "clothing":        "11450",   # Clothing, Shoes & Accessories
    # Shoes
    "shoes_women":     "3034",    # Women's Shoes
    "shoes_men":       "93427",   # Men's Shoes
    "shoes":           "62107",   # Shoes
    # Collectibles
    "collectibles":    "1",       # Collectibles
    "dolls":           "238",     # Dolls & Bears > Dolls
    "bears":           "13753",   # Dolls & Bears > Bears
    # Toys
    "toys":            "220",     # Toys & Hobbies
    "plush":           "19009",   # Stuffed Animals
    # Handbags
    "handbags":        "169291",  # Handbags & Purses
}


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
        Return best category_id for item based on category_key and type.
        Uses CATEGORY_MAP with sensible leaf-level defaults.
        """
        if item.ebay_category_id:
            return str(item.ebay_category_id)

        cat_key = (item.category_key or "").lower()

        # Exact match
        if cat_key in CATEGORY_MAP:
            return CATEGORY_MAP[cat_key]

        # Broad keyword match
        for keyword, cat_id in [
            ("book",       CATEGORY_MAP["books"]),
            ("shoe",       CATEGORY_MAP["shoes"]),
            ("footwear",   CATEGORY_MAP["shoes"]),
            ("doll",       CATEGORY_MAP["dolls"]),
            ("bear",       CATEGORY_MAP["bears"]),
            ("plush",      CATEGORY_MAP["plush"]),
            ("toy",        CATEGORY_MAP["toys"]),
            ("bag",        CATEGORY_MAP["handbags"]),
            ("purse",      CATEGORY_MAP["handbags"]),
            ("handbag",    CATEGORY_MAP["handbags"]),
            ("cloth",      CATEGORY_MAP["clothing"]),
            ("shirt",      CATEGORY_MAP["clothing"]),
            ("jean",       CATEGORY_MAP["clothing"]),
            ("pant",       CATEGORY_MAP["clothing"]),
            ("dress",      CATEGORY_MAP["clothing_women"]),
            ("collect",    CATEGORY_MAP["collectibles"]),
        ]:
            if keyword in cat_key:
                return cat_id

        return CATEGORY_MAP["clothing"]  # safe default

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
