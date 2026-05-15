from __future__ import annotations

from apps.api.src.services.operation_diagnostics import classify_ebay_error_payload
from apps.api.src.services.publish_repair import classify_publish_failure
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item


def _item(**overrides) -> Item:
    base = dict(
        sku="BK-000008",
        status="export_ready",
        title_final="Atlas",
        description_final="Atlas description",
        list_price=20.0,
        ebay_category_id="14056",
        condition_id="3000",
        offer_id="156719395011",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        item_specifics={"Region": "North America"},
    )
    base.update(overrides)
    return Item(**base)


def _failure(body: str, **details):
    return Result.failure(
        "eBay API error 400: publish_offer failed",
        error_code=details.pop("error_code", "API_ERROR"),
        body=body,
        **details,
    )


def test_25021_invalid_category_condition_creates_review_repair_plan():
    result = classify_publish_failure(
        _item(),
        result=_failure(
            "Error 25021: invalid item condition information.",
            stage="publish_offer",
            offer_id="156719395011",
            category_id="14056",
            local_condition_id="3000",
            inventory_condition_enum="USED_GOOD",
        ),
    )

    assert result["classified_error_code"] == "invalid_category_condition"
    assert result["repair_layer"] == "category_compatibility"
    assert result["requires_review"] is True
    assert result["retry_allowed"] is False
    plan = result["plans"][0]
    assert plan["current_value"]["category_id"] == "14056"
    assert plan["current_value"]["condition_id"] == "3000"
    assert plan["current_value"]["inventory_condition_enum"] == "USED_GOOD"
    assert plan["current_value"]["offer_id"] == "156719395011"
    assert plan["current_value"]["stage"] == "publish_offer"


def test_existing_offer_conflict_routes_to_offer_recovery_without_duplicate_publish():
    result = classify_publish_failure(
        _item(offer_id=""),
        result=_failure(
            "Offer entity already exists for inventory item.",
            stage="create_offer",
            offer_id="recovered-offer-1",
        ),
    )

    assert result["classified_error_code"] == "offer_already_exists"
    assert result["repair_layer"] == "offer_recovery"
    assert result["retry_allowed"] is False
    plan = result["plans"][0]
    assert plan["affected_field"] == "offer_id"
    assert plan["expected_value"]["offer_id"] == "recovered-offer-1"


def test_already_published_offer_is_duplicate_publish_risk():
    result = classify_publish_failure(
        _item(),
        result=_failure("This offer is already published.", stage="publish_offer"),
    )

    assert result["classified_error_code"] == "already_published"
    assert result["repair_layer"] == "listing_sync"
    assert result["requires_review"] is False
    assert result["retry_allowed"] is False


def test_invalid_image_url_classifies_photo_repair():
    result = classify_publish_failure(
        _item(image_paths=["https://"]),
        result=_failure("Invalid value for imageUrl: https://", stage="create_inventory_item"),
    )

    assert result["classified_error_code"] == "invalid_image_url"
    assert result["repair_layer"] == "photo_hosting"
    assert result["plans"][0]["affected_field"] == "imageUrls"


def test_required_and_invalid_aspect_errors_are_category_aspect_repairs():
    missing = classify_publish_failure(
        _item(),
        result=_failure("Missing required aspect: Brand.", stage="create_inventory_item"),
    )
    invalid = classify_publish_failure(
        _item(item_specifics={"Theme": "x" * 80}),
        result=_failure("Invalid aspect value for aspect Theme.", stage="create_inventory_item"),
    )

    assert missing["classified_error_code"] == "missing_required_aspect"
    assert missing["repair_layer"] == "category_template"
    assert missing["plans"][0]["affected_field"] == "Brand"
    assert invalid["classified_error_code"] == "invalid_aspect_value"
    assert invalid["repair_layer"] == "category_template"


def test_operation_classifier_maps_auth_policy_location_offer_inventory_and_aspect_payloads():
    cases = [
        ("Invalid access token in Authorization header", "expired_or_invalid_access_token", "auth"),
        ("Insufficient scope for sell.inventory", "insufficient_scope", "auth"),
        ("Fulfillment policy is missing", "seller_policy_missing_or_invalid", "seller_policy"),
        ("Invalid merchantLocationKey value", "merchant_location_invalid", "merchant_location"),
        ("Invalid picture URL in imageUrl", "invalid_image_url", "photo_hosting"),
        ("Inventory item not found for SKU", "inventory_item_not_found", "missing_inventory_item"),
        ("Offer not found for offerId", "offer_not_found", "stale_offer"),
        ("Offer is already published", "already_published", "duplicate_publish_risk"),
        ("Missing required aspect Brand", "missing_required_aspects", "category_aspects"),
        ("Aspect value exceeds maximum length", "invalid_aspect_value", "category_aspects"),
    ]

    for payload, code, family in cases:
        classified = classify_ebay_error_payload(payload)
        assert classified["error_code"] == code
        assert classified["error_family"] == family
        assert classified["recommended_next_action"]
        assert "secret-token" not in str(classified)


def test_known_ebay_error_ids_still_map_to_stable_codes():
    assert classify_ebay_error_payload({"errors": [{"errorId": 25021}]})["error_code"] == "invalid_category_condition"
    assert classify_ebay_error_payload({"errors": [{"errorId": 25002}]})["error_code"] == "offer_already_exists"
    assert classify_ebay_error_payload({"errors": [{"errorId": 25013}]})["error_code"] == "inventory_item_not_found"
