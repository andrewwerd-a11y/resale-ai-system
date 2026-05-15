"""
Item entity — one resale unit or defined lot.
This is the central record in the system.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from packages.core.src.constants import ItemStatus, ItemMode


class Measurements(BaseModel):
    chest_in: float | None = None
    length_in: float | None = None
    waist_in: float | None = None
    hips_in: float | None = None
    sleeve_in: float | None = None
    inseam_in: float | None = None
    shoulder_in: float | None = None
    rise_in: float | None = None
    notes: str | None = None


class Item(BaseModel):
    # Identity
    internal_id: str | None = None
    sku: str | None = None
    status: str = ItemStatus.PENDING_INTAKE
    item_mode: str = ItemMode.SINGLE
    batch_id: str | None = None

    # Photo / folder
    photo_folder: str | None = None
    image_paths: list[str] = Field(default_factory=list)

    # Category
    category_key: str | None = None
    category_label: str | None = None
    ebay_category_id: str | None = None
    ebay_category_name: str | None = None
    category_template_fetched: bool = False
    category_template_fetched_at: str | None = None
    missing_required_fields: list[str] = Field(default_factory=list)
    missing_recommended_fields: list[str] = Field(default_factory=list)
    publish_ready: bool = False

    # Titles / description
    title_raw: str | None = None
    title_final: str | None = None
    description_final: str | None = None

    # Brand
    brand: str | None = None
    brand_normalized: str | None = None

    # Item specifics — clothing
    type: str | None = None
    subcategory: str | None = None
    department: str | None = None
    size: str | None = None
    size_type: str | None = None
    color: str | None = None
    material: str | None = None
    pattern: str | None = None
    style: str | None = None
    fit: str | None = None
    features: list[str] = Field(default_factory=list)
    occasion: str | None = None
    season: str | None = None

    # Item specifics — books
    author: str | None = None
    publisher: str | None = None
    format: str | None = None
    edition: str | None = None
    era: str | None = None
    language: str | None = None
    topic: str | None = None
    isbn: str | None = None

    # Item specifics — collectibles / toys
    model: str | None = None
    mpn: str | None = None
    upc: str | None = None
    artist: str | None = None
    franchise: str | None = None
    subject: str | None = None
    country_region: str | None = None
    theme: str | None = None
    character: str | None = None

    # Condition
    condition_label: str | None = None
    condition_id: str | None = None
    condition_notes: str | None = None
    defects: list[str] = Field(default_factory=list)

    # Measurements
    measurements: Measurements = Field(default_factory=Measurements)

    # Lot
    bundle_candidate: bool = False
    lot_group_id: str | None = None
    lot_reason: str | None = None

    # Pricing
    cost: float | None = None
    cost_basis: float | None = None             # Phase 5A
    estimated_price: float | None = None
    list_price: float | None = None
    minimum_price: float | None = None
    shipping_weight: float | None = None
    shipping_method: str | None = None

    # Storage
    storage_location: str | None = None

    # Platform / listing
    platform: str | None = None
    listing_id: str | None = None
    offer_id: str | None = None                 # Phase 5A — eBay offer ID
    promotion_pct: float | None = None          # Phase 5A — ad rate %
    listing_url: str | None = None
    date_listed: datetime | None = None
    days_listed: int | None = None
    date_sold: datetime | None = None
    sold_price: float | None = None
    fees: float | None = None
    shipping_cost: float | None = None
    net_profit: float | None = None
    profit_margin: float | None = None

    # AI / review
    confidence_score: float | None = None
    intake_quality_status: str | None = None
    missing_photo_types: list[str] = Field(default_factory=list)
    needs_more_photos_for_analysis: bool = False
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    manual_override: bool = False
    notes: str | None = None

    # Enrichment (Phase 4 — Claude API second pass)
    enrichment_done: bool = False
    enrichment_notes: str | None = None
    cost_manual: bool = False  # True if cost was manually entered

    # Sourcing (Phase 5)
    sourcing_location: str | None = None
    sourcing_date: datetime | None = None
    sourcing_batch: str | None = None

    # Extra item specifics (catch-all for category-specific fields)
    item_specifics: dict[str, Any] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def image_paths_str(self) -> str:
        """Pipe-separated image paths for spreadsheet storage."""
        return "|".join(self.image_paths)

    def computed_net_profit(self) -> float | None:
        if all(v is not None for v in [self.sold_price, self.cost, self.fees, self.shipping_cost]):
            return self.sold_price - self.cost - self.fees - self.shipping_cost  # type: ignore
        return None

    def computed_profit_margin(self) -> float | None:
        profit = self.computed_net_profit()
        if profit is not None and self.sold_price and self.sold_price > 0:
            return profit / self.sold_price
        return None
