from __future__ import annotations

from datetime import datetime

from packages.core.src import config as core_config
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.category_intelligence import CategoryTemplate
from apps.api.src.services.publish_readiness import apply_publish_repair_blocker, evaluate_publish_readiness


def _make_item(photo_path: str, **overrides) -> Item:
    base = dict(
        sku="BK-000005",
        status="approved",
        title_final="Ready title",
        description_final="Ready description",
        list_price=24.0,
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=[photo_path],
        item_specifics={},
    )
    base.update(overrides)
    return Item(**base)


def _hosted_photo_url() -> str:
    return "https://res.cloudinary.com/demo/image/upload/v1/BK-000005-01.jpg"


def _template(required_fields=None, recommended_fields=None, field_constraints=None) -> CategoryTemplate:
    return CategoryTemplate(
        category_id="29223",
        category_name="Books",
        required_fields=required_fields or [],
        recommended_fields=recommended_fields or [],
        field_constraints=field_constraints or {},
        fetched_at=datetime.utcnow(),
        raw_response={"aspects": []},
    )


def _block_network(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("unexpected eBay network call during readiness evaluation")

    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.post", _fail)
    monkeypatch.setattr("packages.ebay.src.category_intelligence.ebay_http.get", _fail)
    monkeypatch.setattr("packages.ebay.src.category_intelligence.ebay_http.post", _fail)


def test_valid_category_condition_and_policy_config_pass_readiness(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()

    result = evaluate_publish_readiness(
        _make_item(_hosted_photo_url()),
        category_template_provider=lambda _item: Result.success(_template()),
    )

    assert result.ready is True
    assert result.blockers == []
    checks = {check["name"]: check for check in result.checks}
    assert checks["condition_id_supported"]["ok"] is True
    assert checks["category_template_validation"]["ok"] is True
    assert checks["seller_policy_readiness"]["ok"] is True


def test_missing_category_blocks_readiness(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    core_config.get_settings.cache_clear()

    photo = tmp_path / "ready.jpg"
    photo.write_bytes(b"ready")
    result = evaluate_publish_readiness(_make_item(str(photo), ebay_category_id=""))

    assert result.ready is False
    assert "Missing required field: category_id." in result.blockers


def test_missing_condition_blocks_readiness(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    core_config.get_settings.cache_clear()

    photo = tmp_path / "ready.jpg"
    photo.write_bytes(b"ready")
    result = evaluate_publish_readiness(_make_item(str(photo), condition_id=""))

    assert result.ready is False
    assert "Missing required field: condition_id." in result.blockers


def test_missing_seller_policy_config_is_surfaced_clearly(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    monkeypatch.setenv("EBAY_PROD_APP_ID", "")
    monkeypatch.setenv("EBAY_PROD_CERT_ID", "")
    monkeypatch.setenv("EBAY_PROD_USER_TOKEN", "")
    core_config.get_settings.cache_clear()

    photo = tmp_path / "ready.jpg"
    photo.write_bytes(b"ready")
    result = evaluate_publish_readiness(
        _make_item(str(photo)),
        category_template_provider=lambda _item: Result.success(_template()),
    )

    assert result.ready is False
    assert any("Seller policy IDs are missing" in blocker for blocker in result.blockers)


def test_taxonomy_upstream_failure_is_classified_without_crashing(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()

    result = evaluate_publish_readiness(
        _make_item(_hosted_photo_url()),
        category_template_provider=lambda _item: Result.failure(
            "template_fetch_error: timeout",
            error_code="UPSTREAM_TIMEOUT",
        ),
    )

    assert result.ready is True
    assert any("UPSTREAM_TIMEOUT" in warning for warning in result.warnings)
    check = next(check for check in result.checks if check["name"] == "category_template_validation")
    assert check["ok"] is True


def test_overlong_color_is_normalized_under_limit(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()

    result = evaluate_publish_readiness(
        _make_item(
            _hosted_photo_url(),
            color="blue and white dress on a woman, various colors in the illustration background",
        ),
        category_template_provider=lambda _item: Result.success(_template()),
    )

    aspect_check = next(check for check in result.checks if check["name"] == "aspect_value_lengths")
    assert result.ready is True
    assert aspect_check["ok"] is True
    assert aspect_check["context"]["normalized_aspects"]["Color"] == ["Blue/White"]
    assert any("Normalized Color" in warning for warning in result.warnings)


def test_overlong_non_normalizable_aspect_blocks_readiness(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()

    photo = tmp_path / "ready.jpg"
    photo.write_bytes(b"ready")
    result = evaluate_publish_readiness(
        _make_item(
            str(photo),
            item_specifics={
                "Theme": "x" * 70,
            },
        ),
        category_template_provider=lambda _item: Result.success(_template()),
    )

    aspect_check = next(check for check in result.checks if check["name"] == "aspect_value_lengths")
    assert result.ready is False
    assert aspect_check["ok"] is False
    assert any("Aspect 'Theme' value exceeds eBay's 65-character limit" in blocker for blocker in result.blockers)


def test_active_repair_queue_blocker_forces_readiness_false(monkeypatch, tmp_path):
    _block_network(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()

    readiness = evaluate_publish_readiness(
        _make_item(_hosted_photo_url(), condition_id="3000"),
        category_template_provider=lambda _item: Result.success(_template()),
    )
    repair_blocker = {
        "blocked_by_repair_queue": True,
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "repair_status": {"status": "needs_manual_review"},
        "retry_allowed": False,
        "classified_error_code": "requires_publish_decision_after_refresh",
        "reason": "Latest repair plan requires manual review before publish can be retried.",
        "suggested_actions": ["Review the active repair plan before publishing."],
    }

    result = apply_publish_repair_blocker(readiness, repair_blocker)

    assert result.ready is False
    assert result.blocked_by_repair_queue is True
    assert "blocked_by_repair_queue" in result.blockers
    assert result.repair_plan_id == "repair-plan-1"
    assert result.retry_allowed is False
    assert result.classified_error_code == "requires_publish_decision_after_refresh"
    assert "Resolve or supersede the active repair plan in the repair queue before publishing." in result.required_actions
    not_blocked_check = next(check for check in result.checks if check["name"] == "not_blocked_from_publish")
    assert not_blocked_check["ok"] is False
    assert not_blocked_check["context"]["repair_plan_id"] == "repair-plan-1"
