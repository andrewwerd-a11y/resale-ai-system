"""
Factory functions for test Item entities.
All fields have sensible defaults so callers only override what matters.
"""
from __future__ import annotations

from packages.domain.src.entities.item import Item


def make_clothing_item(**kwargs) -> Item:
    defaults = dict(
        sku="CL-000001",
        status="approved",
        item_mode="single",
        category_key="clothing",
        category_label="Clothing",
        title_raw="Nike Dri-FIT Men's Running Jacket Blue L",
        title_final="Nike Dri-FIT Men's Running Jacket Blue L",
        brand="Nike",
        type="Jacket",
        department="Men",
        size="L",
        color="Blue",
        material="Polyester",
        condition_label="Good",
        condition_id="3000",
        estimated_price=22.00,
        list_price=24.00,
        minimum_price=18.00,
        confidence_score=0.88,
        needs_review=False,
        manual_override=False,
        image_paths=["intake/CL-000001/01.jpg", "intake/CL-000001/02.jpg"],
    )
    defaults.update(kwargs)
    return Item(**defaults)


def make_book_item(**kwargs) -> Item:
    defaults = dict(
        sku="BK-000001",
        status="approved",
        item_mode="single",
        category_key="books",
        category_label="Books",
        title_raw="The Great Gatsby",
        title_final="The Great Gatsby",
        author="F. Scott Fitzgerald",
        format="Paperback",
        condition_label="Good",
        condition_id="3000",
        estimated_price=8.00,
        list_price=9.00,
        minimum_price=6.75,
        confidence_score=0.85,
        needs_review=False,
        manual_override=False,
        image_paths=["intake/BK-000001/01.jpg"],
    )
    defaults.update(kwargs)
    return Item(**defaults)


def make_toy_item(**kwargs) -> Item:
    defaults = dict(
        sku="TO-000001",
        status="approved",
        item_mode="single",
        category_key="toys",
        category_label="Toys",
        title_final="LEGO Star Wars Millennium Falcon Set",
        title_raw="LEGO Star Wars Millennium Falcon Set",
        brand="LEGO",
        type="Building Set",
        franchise="Star Wars",
        character="Millennium Falcon",
        condition_label="Very Good",
        condition_id="4000",
        estimated_price=45.00,
        list_price=49.00,
        confidence_score=0.90,
        needs_review=False,
        manual_override=False,
        image_paths=["intake/TO-000001/01.jpg"],
    )
    defaults.update(kwargs)
    return Item(**defaults)


def make_reviewed_item(**kwargs) -> Item:
    defaults = dict(
        sku="CL-000099",
        status="needs_review",
        item_mode="review",
        category_key="clothing",
        category_label="Clothing",
        title_raw="Unknown Brand Jacket",
        brand="Unknown",
        type="Jacket",
        confidence_score=0.55,
        needs_review=True,
        review_reasons=["low_confidence", "unclear_brand"],
        manual_override=False,
        image_paths=["intake/CL-000099/01.jpg"],
    )
    defaults.update(kwargs)
    return Item(**defaults)
