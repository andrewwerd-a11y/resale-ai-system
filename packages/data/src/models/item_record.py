"""
ItemRecord — the SQLite table for item storage.
Maps 1:1 to the Item domain entity.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ItemRecord(SQLModel, table=True):
    __tablename__ = "items"

    # Identity
    internal_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    sku: Optional[str] = Field(default=None, index=True, unique=True)
    status: str = Field(default="pending_intake", index=True)
    item_mode: str = Field(default="single")
    batch_id: Optional[str] = Field(default=None, index=True)

    # Folder / images
    photo_folder: Optional[str] = None
    image_paths: Optional[str] = None          # pipe-separated: path1|path2|...

    # Category
    category_key: Optional[str] = Field(default=None, index=True)
    category_label: Optional[str] = None
    ebay_category_id: Optional[str] = None
    ebay_category_name: Optional[str] = None
    category_template_fetched: int = Field(default=0)
    category_template_fetched_at: Optional[str] = None
    missing_required_fields: Optional[str] = None    # JSON list
    missing_recommended_fields: Optional[str] = None  # JSON list
    publish_ready: int = Field(default=0)

    # Titles
    title_raw: Optional[str] = None
    title_final: Optional[str] = None
    description_final: Optional[str] = None

    # Brand
    brand: Optional[str] = None
    brand_normalized: Optional[str] = None

    # Clothing fields
    type: Optional[str] = None
    subcategory: Optional[str] = None
    department: Optional[str] = None
    size: Optional[str] = None
    size_type: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    pattern: Optional[str] = None
    style: Optional[str] = None
    fit: Optional[str] = None
    features: Optional[str] = None            # JSON array string
    occasion: Optional[str] = None
    season: Optional[str] = None

    # Book fields
    author: Optional[str] = None
    publisher: Optional[str] = None
    format: Optional[str] = None
    edition: Optional[str] = None
    era: Optional[str] = None
    language: Optional[str] = None
    topic: Optional[str] = None
    isbn: Optional[str] = None

    # Collectible / toy fields
    model: Optional[str] = None
    mpn: Optional[str] = None
    upc: Optional[str] = None
    artist: Optional[str] = None
    franchise: Optional[str] = None
    subject: Optional[str] = None
    country_region: Optional[str] = None
    theme: Optional[str] = None
    character: Optional[str] = None

    # Condition
    condition_label: Optional[str] = None
    condition_id: Optional[str] = None
    condition_notes: Optional[str] = None
    defects: Optional[str] = None             # JSON array string

    # Measurements (JSON string)
    measurements: Optional[str] = None

    # Lot
    bundle_candidate: bool = False
    lot_group_id: Optional[str] = None
    lot_reason: Optional[str] = None

    # Pricing
    cost: Optional[float] = None
    cost_basis: Optional[float] = None          # Phase 5A
    estimated_price: Optional[float] = None
    list_price: Optional[float] = None
    minimum_price: Optional[float] = None
    shipping_weight: Optional[float] = None
    shipping_method: Optional[str] = None
    storage_location: Optional[str] = None

    # Platform / listing
    platform: Optional[str] = None
    listing_id: Optional[str] = None
    offer_id: Optional[str] = None              # Phase 5A — eBay offer ID
    promotion_pct: Optional[float] = None       # Phase 5A — ad rate %
    listing_url: Optional[str] = None
    date_listed: Optional[datetime] = None
    days_listed: Optional[int] = None
    date_sold: Optional[datetime] = None
    sold_price: Optional[float] = None
    fees: Optional[float] = None
    shipping_cost: Optional[float] = None
    net_profit: Optional[float] = None
    profit_margin: Optional[float] = None

    # AI / review
    confidence_score: Optional[float] = None
    intake_quality_status: Optional[str] = None
    missing_photo_types: Optional[str] = None    # JSON array string
    needs_more_photos_for_analysis: bool = False
    needs_review: bool = False
    review_reasons: Optional[str] = None      # JSON array string
    review_reason: Optional[str] = None
    review_sub_queue: Optional[str] = None
    reviewer_notes: Optional[str] = None
    listing_quality_score: Optional[float] = None
    concern_flags: Optional[str] = None       # JSON array string
    manual_override: bool = False
    notes: Optional[str] = None

    # Enrichment (Phase 4 — Claude API second pass)
    enrichment_done: bool = Field(default=False)
    enrichment_notes: Optional[str] = None
    cost_manual: bool = Field(default=False)  # True if cost was manually entered

    # Sourcing (Phase 5)
    sourcing_location: Optional[str] = None
    sourcing_date: Optional[datetime] = None
    sourcing_batch: Optional[str] = None      # batch_id from sourcing_batches table

    # Extra specifics (JSON)
    item_specifics: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_synced_at: Optional[str] = None
