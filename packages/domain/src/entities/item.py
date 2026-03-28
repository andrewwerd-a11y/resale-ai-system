from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class Item(BaseModel):
    """Domain entity for a resale item. Immutable value object."""

    # Identity
    sku: str
    batch_id: Optional[str] = None

    # Media
    image_paths: list[str] = Field(default_factory=list)
    hosted_photo_urls: list[str] = Field(default_factory=list)

    # AI extraction
    title: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    item_type: Optional[str] = None
    department: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    style: Optional[str] = None
    condition: Optional[str] = None
    condition_id: Optional[str] = None
    condition_notes: Optional[str] = None

    # Book-specific
    author: Optional[str] = None
    book_format: Optional[str] = None
    isbn: Optional[str] = None
    publisher: Optional[str] = None
    publication_year: Optional[str] = None

    # Collectibles/Toys
    franchise: Optional[str] = None
    character: Optional[str] = None

    # Lists (always stored as list[str])
    features: list[str] = Field(default_factory=list)
    defects: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    # Pricing
    estimated_price: Optional[float] = None
    list_price: Optional[float] = None
    sold_price: Optional[float] = None
    ebay_fees: Optional[float] = None
    net_profit: Optional[float] = None

    # AI meta
    ai_confidence: Optional[float] = None
    ai_model: Optional[str] = None
    raw_ai_response: Optional[str] = None

    # Status & workflow
    status: str = "pending_intake"
    review_reasons: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    manual_override: bool = False

    # eBay
    ebay_listing_id: Optional[str] = None
    ebay_listing_url: Optional[str] = None
    ebay_offer_id: Optional[str] = None
    date_listed: Optional[datetime] = None
    date_sold: Optional[datetime] = None

    # Timestamps
    date_created: Optional[datetime] = None
    date_updated: Optional[datetime] = None
