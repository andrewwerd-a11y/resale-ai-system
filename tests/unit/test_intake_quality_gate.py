from __future__ import annotations

from packages.core.src.constants import IntakeQualityStatus
from packages.domain.src.entities.item import Item
from packages.intake.src.quality_gate import evaluate_intake_quality


def _item(**overrides) -> Item:
    base = dict(
        sku="BK-QUALITY",
        category_key="books",
        category_label="Books",
        title_final="Reference Book",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[],
    )
    base.update(overrides)
    return Item(**base)


def test_book_missing_copyright_page_blocks_deep_analysis():
    result = evaluate_intake_quality(
        _item(
            image_paths=[
                "front-cover.jpg",
                "back-cover.jpg",
                "spine.jpg",
                "title-page.jpg",
                "condition-flaws.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.NEEDS_MORE_PHOTOS
    assert "copyright/publication page" in result.missing_photo_types
    assert result.should_run_deep_analysis is False
    assert result.needs_more_photos_for_analysis is True


def test_clothing_missing_size_and_material_tags_blocks():
    result = evaluate_intake_quality(
        _item(
            sku="CL-QUALITY",
            category_key="clothing",
            category_label="Clothing",
            image_paths=[
                "front.jpg",
                "back.jpg",
                "brand-tag.jpg",
                "measurements.jpg",
                "flaws-wear.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.NEEDS_MORE_PHOTOS
    assert "size tag" in result.missing_photo_types
    assert "material/care tag" in result.missing_photo_types


def test_plush_missing_tag_photo_blocks():
    result = evaluate_intake_quality(
        _item(
            sku="TO-PLUSH",
            category_key="toys",
            category_label="Plush/Toys",
            title_final="Vintage plush bear",
            image_paths=[
                "front.jpg",
                "back.jpg",
                "scale-measurement.jpg",
                "defects-wear.jpg",
                "copyright-manufacturer-tag.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.NEEDS_MORE_PHOTOS
    assert "tag/tush tag" in result.missing_photo_types


def test_bag_missing_authenticity_detail_photos_requires_review():
    result = evaluate_intake_quality(
        _item(
            sku="BG-QUALITY",
            category_key="bags",
            category_label="Bags",
            title_final="Designer leather bag",
            estimated_price=120.0,
            image_paths=[
                "front-back.jpg",
                "interior.jpg",
                "brand-logo.jpg",
                "hardware.jpg",
                "corners-wear.jpg",
                "strap-handle.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.NEEDS_AUTHENTICITY_REVIEW
    assert "authenticity-sensitive evidence" in result.missing_photo_types
    assert result.should_block_publish_approval is True


def test_low_confidence_toy_is_held_for_more_photos():
    result = evaluate_intake_quality(
        _item(
            sku="TO-LOWCONF",
            category_key="toys",
            category_label="Toys",
            title_final="Action figure",
            confidence_score=0.4,
            image_paths=[
                "front.jpg",
                "back.jpg",
                "tag-tush-tag.jpg",
                "scale-measurement.jpg",
                "defects-wear.jpg",
                "copyright-manufacturer-tag.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.LOW_CONFIDENCE_HOLD
    assert result.should_run_deep_analysis is False


def test_high_value_item_requires_manual_review():
    result = evaluate_intake_quality(
        _item(
            estimated_price=150.0,
            image_paths=[
                "front-cover.jpg",
                "back-cover.jpg",
                "spine.jpg",
                "title-page.jpg",
                "copyright-page.jpg",
                "condition-flaws.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.NEEDS_AUTHENTICITY_REVIEW
    assert result.should_block_publish_approval is True


def test_item_with_enough_photos_can_enter_deep_analysis():
    result = evaluate_intake_quality(
        _item(
            image_paths=[
                "front-cover.jpg",
                "back-cover.jpg",
                "spine.jpg",
                "title-page.jpg",
                "copyright-page.jpg",
                "condition-flaws.jpg",
            ],
        )
    )

    assert result.intake_quality_status == IntakeQualityStatus.READY_FOR_DEEP_ANALYSIS
    assert result.has_enough_photos is True
    assert result.should_run_deep_analysis is True
    assert result.should_block_publish_approval is False
