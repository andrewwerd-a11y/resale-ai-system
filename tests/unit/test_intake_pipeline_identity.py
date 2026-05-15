from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.identity_scan import (
    DeterministicIdentityProvider,
    run_first_pass_identity,
)
from packages.intake.src.pipeline_types import IntakeDecision, RiskFlag


def _item(**overrides) -> Item:
    base = dict(
        sku="BK-ID",
        category_key="books",
        category_label="Books",
        title_final="A Book",
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


def test_identity_ready_when_photos_sufficient():
    result = run_first_pass_identity(_item())
    assert result.decision == IntakeDecision.READY_FOR_DEEP_ANALYSIS
    assert result.should_continue_to_category_resolution is True
    assert result.provider == "deterministic-fallback"
    assert "books" in result.category_family_candidates


def test_identity_needs_more_photos_when_missing_book_pages():
    result = run_first_pass_identity(_item(image_paths=["front-cover.jpg"]))
    assert result.decision == IntakeDecision.NEEDS_MORE_PHOTOS
    assert result.needs_more_photos is True
    assert result.should_continue_to_category_resolution is False
    assert RiskFlag.MISSING_REQUIRED_PHOTOS in result.risk_flags


def test_identity_unknown_category_blocks():
    result = run_first_pass_identity(
        _item(category_key=None, category_label=None, title_final="mystery thing")
    )
    assert result.decision in {
        IntakeDecision.NEEDS_CATEGORY_REVIEW,
        IntakeDecision.NEEDS_MORE_PHOTOS,
    }
    assert RiskFlag.CATEGORY_UNCERTAIN in result.risk_flags or result.needs_more_photos


def test_identity_high_value_flagged():
    result = run_first_pass_identity(_item(estimated_price=120.0))
    assert RiskFlag.HIGH_VALUE_ESTIMATE in result.risk_flags


def test_provider_protocol_used_directly():
    provider = DeterministicIdentityProvider()
    item = _item()
    from packages.intake.src.identity_scan import IdentityScanRequest
    result = provider.analyze(IdentityScanRequest(sku=item.sku, item=item))
    assert result.sku == item.sku
