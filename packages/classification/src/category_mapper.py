"""
CategoryMapper — infers category_key and eBay category ID from
a folder's SKU prefix, or from AI extraction output.
Prefix is always the authority; AI classification is a fallback
for new items without an assigned prefix.
"""
from __future__ import annotations

from packages.core.src.config import get_sku_prefixes, get_categories
from packages.core.src.result import Result


class CategoryMapper:
    def __init__(self):
        self.prefixes = get_sku_prefixes()
        self.categories = get_categories()

    def from_prefix(self, prefix: str) -> Result[dict]:
        """Derive category from SKU prefix. This is always authoritative."""
        data = self.prefixes.get(prefix)
        if not data:
            return Result.failure(f"Unknown prefix: {prefix}")
        category_key = data["category_key"]
        profile = self.categories.get(category_key, {})
        return Result.success({
            "category_key": category_key,
            "category_label": data.get("label", category_key),
            "ebay_category_id": data.get("ebay_category_id") or profile.get("ebay_category_id"),
            "prefix": prefix,
        })

    def from_sku(self, sku: str) -> Result[dict]:
        """Derive category from a full SKU string like 'CL-000007'."""
        try:
            prefix = sku.split("-")[0]
        except (AttributeError, IndexError):
            return Result.failure(f"Cannot parse SKU: {sku}")
        return self.from_prefix(prefix)

    def get_profile(self, category_key: str) -> dict:
        return self.categories.get(category_key, {})

    def required_fields(self, category_key: str) -> list[str]:
        return self.categories.get(category_key, {}).get("required_fields", [])

    def optional_fields(self, category_key: str) -> list[str]:
        return self.categories.get(category_key, {}).get("optional_fields", [])

    def title_template(self, category_key: str) -> str:
        return self.categories.get(category_key, {}).get("title_template", "{title_final}")

    def build_title(self, category_key: str, fields: dict) -> str:
        template = self.title_template(category_key)
        try:
            title = template.format_map({k: (v or "") for k, v in fields.items()})
            # Clean up extra spaces from empty fields
            return " ".join(title.split())[:80]
        except KeyError:
            return fields.get("title_raw") or fields.get("title_final") or ""
