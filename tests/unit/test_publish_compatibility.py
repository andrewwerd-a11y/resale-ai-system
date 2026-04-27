from __future__ import annotations

from packages.core.src.constants import ItemStatus
from packages.domain.src.entities.item import Item
from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility


def _item(**overrides) -> Item:
    base = dict(
        sku="BK-000008",
        status=ItemStatus.EXPORT_READY,
        title_final="Preview title",
        description_final="Preview description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        item_specifics={},
    )
    base.update(overrides)
    return Item(**base)


def test_hosted_cloudinary_urls_pass_image_url_validation():
    result = evaluate_publish_compatibility(_item(), strict_condition_policy=True)

    assert result["ready"] is True
    image_check = next(check for check in result["checks"] if check["name"] == "public_image_urls")
    assert image_check["ok"] is True


def test_local_windows_paths_fail_image_url_validation():
    result = evaluate_publish_compatibility(
        _item(image_paths=[r"C:\Users\Andrew\Desktop\photo.jpg"]),
        strict_condition_policy=True,
    )

    assert result["ready"] is False
    assert any("Hosted image URLs are missing or malformed" in blocker for blocker in result["blockers"])


def test_category_specific_condition_policy_blocks_invalid_condition():
    result = evaluate_publish_compatibility(
        _item(ebay_category_id="14056", condition_id="5000"),
        strict_condition_policy=True,
    )

    assert result["ready"] is False
    condition_check = next(check for check in result["checks"] if check["name"] == "category_condition_policy")
    assert condition_check["ok"] is False
    assert condition_check["context"]["allowed_condition_ids"]


def test_valid_category_specific_condition_passes():
    result = evaluate_publish_compatibility(
        _item(ebay_category_id="14056", condition_id="3000"),
        strict_condition_policy=True,
    )

    assert result["ready"] is True


def test_unknown_category_condition_policy_blocks_strict_live_publish():
    result = evaluate_publish_compatibility(
        _item(ebay_category_id="999999", condition_id="5000"),
        strict_condition_policy=True,
    )

    assert result["ready"] is False
    assert any("Condition policy for the selected category is not cached locally." in blocker for blocker in result["blockers"])
