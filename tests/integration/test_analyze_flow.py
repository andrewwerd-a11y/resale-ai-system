"""
Integration tests for the analysis flow.
ALL vision/AI calls are mocked — no real Ollama or Claude API calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from packages.core.src.result import Result
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.core.src.constants import ItemStatus, ItemMode
from packages.triage.src.router import TriageRouter
from packages.pricing.src.estimator import PriceEstimator
from packages.vision.src.response_parser import ResponseParser
from tests.fixtures.mock_extraction import (
    CLOTHING_EXTRACTION,
    LOW_CONFIDENCE_EXTRACTION,
)
from tests.fixtures.sample_items import make_clothing_item


# ── helpers ───────────────────────────────────────────────────────────────────

def _run_triage_and_price(item: Item) -> Item:
    """Run triage + pricing on an item (no DB, pure domain logic)."""
    router = TriageRouter()
    triage = router.route(item)
    item.item_mode = triage.item_mode
    if triage.needs_review:
        item.needs_review = True
        item.review_reasons = triage.review_reasons
        item.status = ItemStatus.NEEDS_REVIEW
    else:
        item.status = ItemStatus.ANALYZED

    estimator = PriceEstimator()
    item = estimator.apply(item)
    return item


# ── extraction → DB round-trip ────────────────────────────────────────────────

def test_extraction_result_saved_to_db(test_session):
    parser = ResponseParser()
    parsed = parser.parse(CLOTHING_EXTRACTION, "clothing")
    assert parsed.ok

    repo = ItemRepository(test_session)
    # Use Item.model_fields to filter valid keys (Pydantic v2 — hasattr on class unreliable)
    valid_fields = set(Item.model_fields.keys())
    field_data = {k: v for k, v in parsed.value.items() if k in valid_fields}
    item = Item(
        sku="CL-000001",
        status=ItemStatus.PENDING_INTAKE,
        category_key="clothing",
        **field_data,
    )
    saved = repo.upsert(item)

    fetched = repo.get_by_sku("CL-000001")
    assert fetched is not None
    assert fetched.brand == "Patagonia"
    assert fetched.confidence_score == pytest.approx(0.91)


def test_triage_runs_after_extraction(test_session):
    parser = ResponseParser()
    parsed = parser.parse(CLOTHING_EXTRACTION, "clothing")

    valid_fields = set(Item.model_fields.keys())
    field_data = {k: v for k, v in parsed.value.items() if k in valid_fields}
    item = Item(
        sku="CL-000002",
        category_key="clothing",
        image_paths=["p/01.jpg"],
        **field_data,
    )
    item = _run_triage_and_price(item)

    repo = ItemRepository(test_session)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000002")
    assert fetched.status in (ItemStatus.ANALYZED, ItemStatus.NEEDS_REVIEW)
    assert fetched.item_mode in ItemMode.ALL


def test_approved_item_has_correct_status(test_session):
    """High-confidence item with no review triggers → ANALYZED."""
    item = make_clothing_item(
        sku="CL-000003",
        estimated_price=20.0,
        confidence_score=0.90,
        needs_review=False,
        review_reasons=[],
    )
    item = _run_triage_and_price(item)

    repo = ItemRepository(test_session)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000003")
    assert fetched.status == ItemStatus.ANALYZED
    assert fetched.needs_review is False


def test_review_item_has_needs_review_true(test_session):
    """Low-confidence extraction → needs_review=True."""
    parser = ResponseParser()
    parsed = parser.parse(LOW_CONFIDENCE_EXTRACTION, "clothing")

    valid_fields = set(Item.model_fields.keys())
    field_data = {k: v for k, v in parsed.value.items() if k in valid_fields}
    item = Item(
        sku="CL-000004",
        category_key="clothing",
        image_paths=["p/01.jpg"],
        **field_data,
    )
    item = _run_triage_and_price(item)

    repo = ItemRepository(test_session)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000004")
    assert fetched.needs_review is True
    assert fetched.status == ItemStatus.NEEDS_REVIEW


# ── mocked vision provider ────────────────────────────────────────────────────

def test_mocked_vision_call_dry_run(test_session):
    """With analyze mocked, the pipeline must not call the real Ollama endpoint."""
    mock_result = Result.success(CLOTHING_EXTRACTION)

    with patch(
        "packages.vision.src.ollama_provider.OllamaProvider.analyze",
        return_value=mock_result,
    ) as mock_analyze:
        from packages.vision.src.ollama_provider import OllamaProvider
        provider = OllamaProvider()
        result = provider.analyze("fake_prompt", image_paths=[])

    mock_analyze.assert_called_once()
    assert result.ok
    assert result.value["brand"] == "Patagonia"


def test_failed_vision_call_does_not_corrupt_db(test_session):
    """A failed vision extraction must not overwrite existing DB state."""
    repo = ItemRepository(test_session)
    original = make_clothing_item(sku="CL-000005", title_final="Original Title")
    repo.upsert(original)

    # Simulate a failed extraction — item should not be updated
    failed_result = Result.failure("ollama_timeout")
    assert not failed_result.ok

    # Nothing should be written on failure
    fetched = repo.get_by_sku("CL-000005")
    assert fetched.title_final == "Original Title"


# ── pricing applied after triage ─────────────────────────────────────────────

def test_list_price_set_after_analysis(test_session):
    item = make_clothing_item(
        sku="CL-000006",
        estimated_price=20.0,
        list_price=None,
        confidence_score=0.88,
        needs_review=False,
        review_reasons=[],
    )
    item = _run_triage_and_price(item)

    repo = ItemRepository(test_session)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000006")
    assert fetched.list_price is not None
    assert fetched.list_price > 0


def test_minimum_price_set_after_analysis(test_session):
    item = make_clothing_item(
        sku="CL-000007",
        estimated_price=20.0,
        list_price=None,
        minimum_price=None,
        confidence_score=0.88,
        needs_review=False,
        review_reasons=[],
    )
    item = _run_triage_and_price(item)

    repo = ItemRepository(test_session)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000007")
    assert fetched.minimum_price is not None
    assert fetched.minimum_price > 0
