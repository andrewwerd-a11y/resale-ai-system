"""Unit tests for PriceEstimator."""
import pytest
from packages.pricing.src.estimator import PriceEstimator
from tests.fixtures.sample_items import make_clothing_item


@pytest.fixture
def estimator():
    return PriceEstimator()


# ── list_price derived from estimated_price ───────────────────────────────────

def test_list_price_derived_when_missing(estimator):
    item = make_clothing_item(estimated_price=20.0, list_price=None)
    result = estimator.apply(item)
    # List price should be ~10% above estimated
    assert result.list_price == pytest.approx(22.0)


def test_estimated_price_derived_when_list_price_given(estimator):
    item = make_clothing_item(estimated_price=None, list_price=22.0)
    result = estimator.apply(item)
    assert result.estimated_price == pytest.approx(19.80, rel=0.01)


def test_both_prices_given_neither_changed(estimator):
    item = make_clothing_item(estimated_price=20.0, list_price=25.0)
    result = estimator.apply(item)
    assert result.list_price == 25.0
    assert result.estimated_price == 20.0


# ── minimum_price floor ───────────────────────────────────────────────────────

def test_minimum_price_set_from_list_price(estimator):
    item = make_clothing_item(estimated_price=20.0, list_price=24.0, minimum_price=None)
    result = estimator.apply(item)
    # Minimum price = 75% of list price
    assert result.minimum_price == pytest.approx(18.0)


def test_minimum_price_not_overwritten_if_set(estimator):
    item = make_clothing_item(estimated_price=20.0, list_price=24.0, minimum_price=15.0)
    result = estimator.apply(item)
    assert result.minimum_price == 15.0


# ── margin enforcement ────────────────────────────────────────────────────────

def test_list_price_bumped_when_margin_too_low(estimator):
    # cost=18, list=20 → margin=(20-18)/20=10% — below 30% threshold
    item = make_clothing_item(cost=18.0, estimated_price=20.0, list_price=20.0)
    result = estimator.apply(item)
    # Should bump to cost/(1-0.30) ≈ 25.71
    assert result.list_price > 20.0
    if result.cost and result.list_price:
        margin = (result.list_price - result.cost) / result.list_price
        assert margin >= 0.30 - 0.001  # allow small float rounding


def test_list_price_unchanged_when_margin_ok(estimator):
    # cost=10, list=25 → margin=(25-10)/25=60% — above 30%
    item = make_clothing_item(cost=10.0, estimated_price=22.0, list_price=25.0)
    result = estimator.apply(item)
    assert result.list_price == 25.0


# ── profit / margin computation ───────────────────────────────────────────────

def test_net_profit_computed_when_all_fields_set(estimator):
    item = make_clothing_item(
        sold_price=25.0, cost=10.0, fees=2.50, shipping_cost=1.00,
        list_price=25.0,
    )
    result = estimator.apply(item)
    assert result.net_profit == pytest.approx(11.50)


def test_profit_margin_computed(estimator):
    item = make_clothing_item(
        sold_price=20.0, cost=10.0, fees=2.0, shipping_cost=1.0,
        list_price=20.0,
    )
    result = estimator.apply(item)
    # net = 20-10-2-1 = 7; margin = 7/20 = 0.35
    assert result.profit_margin == pytest.approx(0.35)


def test_net_profit_not_computed_when_cost_missing(estimator):
    item = make_clothing_item(
        sold_price=20.0, cost=None, fees=2.0, shipping_cost=1.0,
        list_price=20.0,
    )
    result = estimator.apply(item)
    assert result.net_profit is None


# ── stale detection ───────────────────────────────────────────────────────────

def test_is_stale_when_days_listed_ge_threshold(estimator):
    item = make_clothing_item(days_listed=60)
    assert estimator.is_stale(item) is True


def test_is_stale_when_days_listed_above_threshold(estimator):
    item = make_clothing_item(days_listed=90)
    assert estimator.is_stale(item) is True


def test_not_stale_when_days_below_threshold(estimator):
    item = make_clothing_item(days_listed=30)
    assert estimator.is_stale(item) is False


def test_not_stale_when_days_listed_none(estimator):
    item = make_clothing_item(days_listed=None)
    assert estimator.is_stale(item) is False


# ── suggested stale price ─────────────────────────────────────────────────────

def test_suggested_stale_price_drops_10_percent(estimator):
    item = make_clothing_item(list_price=30.0, minimum_price=20.0)
    new_price = estimator.suggested_stale_price(item)
    assert new_price == pytest.approx(27.0)


def test_suggested_stale_price_not_below_minimum(estimator):
    item = make_clothing_item(list_price=20.0, minimum_price=19.0)
    new_price = estimator.suggested_stale_price(item)
    # 20 * 0.10 = 2 drop → 18.0 but min is 19.0 → should be 19.0
    assert new_price == 19.0


def test_suggested_stale_price_none_when_no_list_price(estimator):
    item = make_clothing_item(list_price=None)
    assert estimator.suggested_stale_price(item) is None


# ── manual override ───────────────────────────────────────────────────────────

def test_manual_override_skips_price_derivation(estimator):
    # With manual_override, list_price should NOT be set from estimated_price
    item = make_clothing_item(
        estimated_price=20.0,
        list_price=None,
        minimum_price=None,
        manual_override=True,
    )
    result = estimator.apply(item)
    # Derived pricing is skipped; list_price stays None
    assert result.list_price is None
