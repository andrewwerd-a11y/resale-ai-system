"""Unit tests for TriageRouter."""
import pytest

# Importing the router applies the Item monkey-patch (image_count_from_paths)
from packages.triage.src.router import TriageRouter
from packages.core.src.constants import ItemMode, ReviewTrigger
from tests.fixtures.sample_items import make_clothing_item, make_book_item


@pytest.fixture
def router():
    return TriageRouter()


# ── Single mode ───────────────────────────────────────────────────────────────

def test_high_value_item_goes_to_single(router):
    item = make_clothing_item(
        estimated_price=30.0,   # above lot_max (12), below high_val (75)
        confidence_score=0.90,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.SINGLE


def test_single_mode_for_moderate_price(router):
    item = make_clothing_item(
        estimated_price=20.0,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    assert router.route(item).item_mode == ItemMode.SINGLE


# ── Lot mode ──────────────────────────────────────────────────────────────────

def test_low_value_clothing_goes_to_lot(router):
    # clothing is lot_eligible; price <= lot_max (12)
    item = make_clothing_item(
        estimated_price=8.0,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.LOT


def test_lot_threshold_boundary(router):
    # At exactly lot_max — goes to lot
    item = make_clothing_item(
        estimated_price=12.0,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    assert router.route(item).item_mode == ItemMode.LOT


def test_above_lot_threshold_goes_to_single(router):
    item = make_clothing_item(
        estimated_price=12.01,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    assert router.route(item).item_mode == ItemMode.SINGLE


# ── Reject mode ───────────────────────────────────────────────────────────────

def test_below_reject_threshold_non_lot_eligible_goes_to_reject(router):
    # shoes is NOT lot_eligible; price below reject_max (2)
    item = make_clothing_item(
        category_key="shoes",
        estimated_price=1.50,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REJECT


def test_below_reject_threshold_lot_eligible_goes_to_lot(router):
    # books is lot_eligible; below reject_max but eligible for lot
    item = make_book_item(
        estimated_price=1.00,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.LOT


# ── Review mode ───────────────────────────────────────────────────────────────

def test_needs_review_true_goes_to_review(router):
    item = make_clothing_item(
        estimated_price=20.0,
        confidence_score=0.85,
        needs_review=True,
        review_reasons=["unclear_brand"],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REVIEW
    assert result.needs_review is True


def test_low_confidence_goes_to_review(router):
    item = make_clothing_item(
        estimated_price=20.0,
        confidence_score=0.50,   # below 0.72
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REVIEW
    assert ReviewTrigger.LOW_CONFIDENCE in result.review_reasons


def test_high_value_estimate_goes_to_review(router):
    item = make_clothing_item(
        estimated_price=100.0,   # above 75 threshold
        confidence_score=0.90,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REVIEW
    assert ReviewTrigger.HIGH_VALUE_ESTIMATE in result.review_reasons


def test_review_reasons_already_set_goes_to_review(router):
    item = make_clothing_item(
        estimated_price=20.0,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=["missing_required_fields"],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REVIEW


# ── Manual override ───────────────────────────────────────────────────────────

def test_manual_override_preserves_single_mode(router):
    item = make_clothing_item(
        estimated_price=8.0,    # would normally be lot
        item_mode=ItemMode.SINGLE,
        manual_override=True,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.SINGLE
    assert "manual_override" in result.reasons


def test_manual_override_preserves_lot_mode(router):
    item = make_clothing_item(
        estimated_price=50.0,   # would normally be single
        item_mode=ItemMode.LOT,
        manual_override=True,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.LOT


def test_manual_override_preserves_reject_mode(router):
    item = make_clothing_item(
        estimated_price=50.0,
        item_mode=ItemMode.REJECT,
        manual_override=True,
        confidence_score=0.85,
        needs_review=False,
        review_reasons=[],
        image_paths=["p/01.jpg"],
    )
    result = router.route(item)
    assert result.item_mode == ItemMode.REJECT
