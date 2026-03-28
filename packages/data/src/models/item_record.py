from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class ItemRecord(SQLModel, table=True):
    __tablename__ = "items"

    sku: str = Field(primary_key=True)
    batch_id: Optional[str] = None

    # Media — pipe-separated paths
    image_paths: Optional[str] = None
    hosted_photo_urls: Optional[str] = None

    # AI extraction fields
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

    # Collectibles / Toys
    franchise: Optional[str] = None
    character: Optional[str] = None

    # JSON lists stored as strings
    features: Optional[str] = None        # JSON array of str
    defects: Optional[str] = None         # JSON array of str
    keywords: Optional[str] = None        # JSON array of str
    review_reasons: Optional[str] = None  # JSON array of str

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

    # Status & flags
    status: str = "pending_intake"
    notes: Optional[str] = None
    manual_override: bool = False

    # eBay
    ebay_listing_id: Optional[str] = None
    ebay_listing_url: Optional[str] = None
    ebay_offer_id: Optional[str] = None
    date_listed: Optional[datetime] = None
    date_sold: Optional[datetime] = None

    # Timestamps
    date_created: Optional[datetime] = Field(default_factory=datetime.utcnow)
    date_updated: Optional[datetime] = Field(default_factory=datetime.utcnow)
