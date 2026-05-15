from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import (
    DeterministicDeepAnalysisProvider,
    run_deep_analysis_preview,
)
from packages.intake.src.category_resolver import resolve_categories
from packages.intake.src.identity_scan import run_first_pass_identity
from packages.intake.src.marketplace_requirements import get_marketplace_requirements
from packages.intake.src.pipeline_types import RiskFlag


def _ready_item(**overrides) -> Item:
    base = dict(
        sku="BK-DEEP",
        category_key="books",
        category_label="Books",
        title_final="A Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[
            "front-cover.jpg", "back-cover.jpg", "spine.jpg",
            "title-page.jpg", "copyright.jpg", "condition-flaws.jpg",
        ],
    )
    base.update(overrides)
    return Item(**base)


def test_deep_analysis_never_invents_fields():
    item = _ready_item(brand=None, material=None)
    result = run_deep_analysis_preview(item)
    assert "brand" in result.uncertain_fields
    assert "brand" not in result.suggested_field_updates


def test_deep_analysis_echoes_existing_fields_with_low_confidence():
    item = _ready_item()
    result = run_deep_analysis_preview(item)
    assert result.suggested_field_updates.get("brand") == "Penguin"
    assert result.confidence_by_field["brand"] == 0.4


def test_deep_analysis_flags_high_value_item():
    item = _ready_item(estimated_price=200.0)
    result = run_deep_analysis_preview(item)
    assert RiskFlag.HIGH_VALUE_ESTIMATE in result.high_value_flags
    assert result.should_block_publish_approval is True


def test_deep_analysis_flags_authenticity_sensitive_brand():
    item = _ready_item(brand="Coach", title_final="Coach bag")
    result = run_deep_analysis_preview(item)
    assert RiskFlag.AUTHENTICITY_SENSITIVE_BRAND in result.authenticity_flags


def test_deep_analysis_includes_missing_photos_when_blocked():
    item = _ready_item(image_paths=["front-cover.jpg"])
    result = run_deep_analysis_preview(item)
    assert result.needs_more_photos is True
    assert RiskFlag.MISSING_REQUIRED_PHOTOS in result.publish_risk_flags


def test_deep_analysis_flags_malformed_condition_id_against_allowed_list():
    item = _ready_item(condition_id="abc-bad")
    identity = run_first_pass_identity(item)
    resolution = resolve_categories(item, identity=identity)
    requirements = get_marketplace_requirements(item)
    result = run_deep_analysis_preview(
        item,
        identity=identity,
        selected_category=resolution.marketplace_candidates[0] if resolution.marketplace_candidates else None,
        marketplace_requirements=requirements,
    )
    assert RiskFlag.MALFORMED_CONDITION_ID in result.publish_risk_flags


def test_provider_is_conservative_by_default():
    provider = DeterministicDeepAnalysisProvider()
    item = _ready_item()
    from packages.intake.src.analysis_contract import DeepAnalysisRequest
    result = provider.analyze(DeepAnalysisRequest(
        sku=item.sku,
        canonical_schema_version="v1",
        item=item,
    ))
    assert result.should_require_manual_review is True
