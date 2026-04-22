"""
Two-stage enrichment pipeline.
Stage 1: minicpm-v (already done) — vision extraction from photos
Stage 2: Claude API — description writing, title optimization, pricing context
"""
from __future__ import annotations

import json
import logging

from packages.core.src.config import get_settings
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = """You are an expert eBay reseller with deep knowledge of secondhand market pricing \
and listing optimization. You will receive structured data about a resale item \
extracted from photos by a vision model. Your job is to:

1. Write a compelling, accurate eBay listing description (3-4 paragraphs)
2. Optimize the listing title for eBay search (max 80 chars, keywords first)
3. Fill in any missing item specifics based on your knowledge of the brand/item
4. Suggest a realistic list price based on current eBay sold prices for this item
5. Flag any concerns about authenticity, condition assessment, or pricing

Return ONLY valid JSON matching this schema:
{
  "title_final": "optimized title max 80 chars",
  "description_final": "full listing description",
  "brand_normalized": "standardized brand name",
  "estimated_price": 0.00,
  "list_price": 0.00,
  "minimum_price": 0.00,
  "item_specifics": {},
  "enrichment_notes": "any concerns or flags",
  "enrichment_confidence": 0.00
}"""

# Kept for backwards compatibility — used when no category template is available
SYSTEM_PROMPT = _SYSTEM_PROMPT_BASE


def _build_system_prompt(item) -> str:
    """Build a system prompt that includes category intelligence when available."""
    base = _SYSTEM_PROMPT_BASE
    category_section = _build_category_section(item)
    if category_section:
        return base + "\n\n" + category_section
    return base


def _build_category_section(item) -> str:
    """Build the category intelligence addendum for the system prompt."""
    cat_id = getattr(item, "ebay_category_id", None)
    cat_name = getattr(item, "ebay_category_name", None)
    missing_req = getattr(item, "missing_required_fields", []) or []
    missing_rec = getattr(item, "missing_recommended_fields", []) or []

    if not cat_id:
        return ""

    # Try to load field constraints from the spreadsheet
    field_constraints: dict = {}
    try:
        from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
        sheet = CategorySpreadsheet()
        template = sheet.load_template(cat_id)
        if template:
            field_constraints = template.field_constraints
    except Exception:
        pass

    lines = [
        f"The item belongs to eBay category: {cat_name or cat_id} (ID: {cat_id})",
        "",
    ]
    if missing_req:
        lines.append("Required fields for this category that MUST be in your item_specifics response:")
        for f in missing_req:
            allowed = field_constraints.get(f, [])
            hint = f" (allowed: {', '.join(allowed[:8])}{'...' if len(allowed) > 8 else ''})" if allowed else ""
            lines.append(f"  - {f}{hint}")
        lines.append("")
    if missing_rec:
        lines.append("Recommended fields for this category (fill if determinable):")
        for f in missing_rec:
            lines.append(f"  - {f}")
        lines.append("")
    if field_constraints and not missing_req and not missing_rec:
        lines.append("All required fields are present. Verify values are accurate.")
    lines.append(
        "Fill all required fields in item_specifics. "
        "Use allowed values where constraints exist. "
        "If you cannot determine a required field value from the item data, "
        "use the most common/safe default from the allowed values list."
    )
    return "\n".join(lines)

_PROTECTED = frozenset({
    "sku", "status", "batch_id", "photo_folder", "image_paths",
    "category_key", "category_label", "ebay_category_id",
    "internal_id", "enrichment_done", "cost_manual",
    "created_at", "updated_at",
})

_DROP_FROM_PROMPT = frozenset({
    "internal_id", "photo_folder", "image_paths", "created_at", "updated_at",
})


class ItemEnricher:
    """Uses Claude API to enrich item data after vision extraction."""

    def __init__(self):
        self.settings = get_settings()
        self._client = None

    def is_available(self) -> bool:
        return bool(
            self.settings.anthropic_api_key
            and self.settings.enrichment_enabled
        )

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=self.settings.anthropic_api_key
                )
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed — run: uv sync"
                )
        return self._client

    def enrich(self, item: Item) -> Result[dict]:
        """
        Call Claude API to enrich an item's extracted data.
        Returns Result containing a dict of enriched fields plus
        details["estimated_cost"] in USD.
        """
        if not self.is_available():
            return Result.failure(
                "Enrichment not available: check ANTHROPIC_API_KEY and ENRICHMENT_ENABLED"
            )

        # Respect enrichment_mode from DB settings
        try:
            from packages.core.src.settings import get_setting
            mode = get_setting("enrichment_mode") or "hybrid"
        except Exception:
            mode = "hybrid"
        if mode == "local":
            return Result.failure("Enrichment mode is 'local' — Claude enrichment disabled in settings")

        # Build the user-message payload — exclude noise fields
        item_data = {
            k: v for k, v in item.model_dump().items()
            if k not in _DROP_FROM_PROMPT
            and v is not None
            and v != []
            and v != {}
        }
        user_message = json.dumps(item_data, indent=2, default=str)

        try:
            import anthropic

            client = self._get_client()
            response = client.messages.create(
                model=self.settings.enrichment_model,
                max_tokens=2048,
                system=_build_system_prompt(item),
                messages=[{"role": "user", "content": user_message}],
            )

            raw_text = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                inner = []
                for line in lines[1:]:
                    if line.strip() == "```":
                        break
                    inner.append(line)
                raw_text = "\n".join(inner)

            enriched = json.loads(raw_text)

            # Rough cost estimate: Sonnet ~$3/M input, ~$15/M output tokens
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost_usd = (input_tokens * 3 + output_tokens * 15) / 1_000_000

            logger.info(
                "Enriched %s — %d+%d tokens, ~$%.4f",
                item.sku, input_tokens, output_tokens, cost_usd,
            )

            # ── Price comp research ──────────────────────────────────────────
            # Uses ebay_browse.get_price_comps: Marketplace Insights (actual sold)
            # with Browse API fallback. Uses median, not avg of active listings.
            try:
                from packages.enrichment.src.ebay_browse import get_price_comps
                search_query = (
                    enriched.get("ebay_search_query")
                    or enriched.get("title_final")
                    or item.title_final
                    or item.title_raw
                    or ""
                ).strip()[:80]

                if search_query:
                    comp = get_price_comps(search_query, limit=10)
                    comp_median = comp.get("median")
                    claude_est = float(enriched.get("estimated_price") or item.estimated_price or 0)

                    if comp_median and claude_est > 0:
                        concern_flags = list(enriched.get("concern_flags") or [])
                        if comp_median < claude_est * 0.5:
                            # Anomalously low comp — keep Claude's estimate, flag item
                            concern_flags.append("comp_price_anomaly")
                            enriched["concern_flags"] = concern_flags
                            enriched["needs_review"] = True
                            logger.warning(
                                "Comp anomaly for %s: median=$%.2f << est=$%.2f — keeping Claude estimate",
                                item.sku, comp_median, claude_est,
                            )
                        elif comp_median > claude_est * 1.5:
                            # Comp much higher — use it but flag for human review
                            concern_flags.append("comp_price_high")
                            enriched["concern_flags"] = concern_flags
                            enriched["needs_review"] = True
                            enriched["list_price"] = comp_median
                            logger.info(
                                "Comp median $%.2f >> Claude est $%.2f for %s — using comp, flagging review",
                                comp_median, claude_est, item.sku,
                            )
                        else:
                            enriched["list_price"] = comp_median
                            logger.info(
                                "Price comps for %s: median=$%.2f sample=%d",
                                item.sku, comp_median, comp.get("sample_size", 0),
                            )
                    elif comp_median:
                        # No claude_est to compare against — use comp median directly
                        enriched["list_price"] = comp_median

            except Exception as _price_exc:
                logger.debug("Price comp lookup failed for %s: %s", item.sku, _price_exc)

            # ── Floor guard: list_price must be >= estimated_price * 0.6 ─────
            _claude_est = float(enriched.get("estimated_price") or item.estimated_price or 0)
            _final_list = float(enriched.get("list_price") or 0)
            if _claude_est > 0 and _final_list > 0:
                _floor = round(_claude_est * 0.6, 2)
                if _final_list < _floor:
                    _flags = list(enriched.get("concern_flags") or [])
                    _flags.append("list_price_below_floor")
                    enriched["concern_flags"] = _flags
                    enriched["needs_review"] = True
                    enriched["list_price"] = _floor
                    logger.warning(
                        "list_price $%.2f below floor $%.2f for %s — clamped",
                        _final_list, _floor, item.sku,
                    )

            return Result.success(enriched, estimated_cost=round(cost_usd, 4))

        except json.JSONDecodeError as e:
            return Result.failure(f"Failed to parse Claude response as JSON: {e}")
        except Exception as e:
            module = getattr(type(e), "__module__", "")
            if "anthropic" in module:
                return Result.failure(f"Anthropic API error: {e}")
            return Result.failure(f"Enrichment error: {e}")

    def apply_to_item(self, item: Item, enriched: dict) -> Item:
        """
        Apply enriched fields to an Item entity.
        Respects manual_override. Never touches protected identity fields.
        """
        if not item.manual_override:
            for key, val in enriched.items():
                if key in _PROTECTED or not hasattr(item, key):
                    continue
                if key == "item_specifics" and isinstance(val, dict):
                    # Merge: existing non-null values win over Claude suggestions
                    existing = item.item_specifics or {}
                    merged = {**val, **{k: v for k, v in existing.items() if v is not None}}
                    item.item_specifics = merged
                else:
                    setattr(item, key, val)

        # Enrichment metadata — always written, even on manual_override
        item.enrichment_done = True
        item.enrichment_notes = enriched.get("enrichment_notes")
        return item
