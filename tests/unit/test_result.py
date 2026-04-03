"""Unit tests for the Result[T] container."""
import pytest
from packages.core.src.result import Result


def test_success_is_ok():
    r = Result.success("hello")
    assert r.ok is True


def test_success_holds_value():
    r = Result.success(42)
    assert r.value == 42


def test_failure_is_not_ok():
    r = Result.failure("something went wrong")
    assert r.ok is False


def test_failure_holds_error():
    r = Result.failure("bad input", error_code="BAD_INPUT")
    assert r.error == "bad input"
    assert r.error_code == "BAD_INPUT"


def test_failure_value_is_none():
    r = Result.failure("error")
    assert r.value is None


def test_unwrap_returns_value_on_success():
    r = Result.success("data")
    assert r.unwrap() == "data"


def test_unwrap_raises_on_failure():
    r = Result.failure("oops")
    with pytest.raises(RuntimeError, match="oops"):
        r.unwrap()


def test_bool_true_on_success():
    assert bool(Result.success("x")) is True


def test_bool_false_on_failure():
    assert bool(Result.failure("x")) is False


def test_success_in_if_branch():
    r = Result.success([1, 2, 3])
    if r:
        assert r.value == [1, 2, 3]
    else:
        pytest.fail("Should enter success branch")


def test_success_with_details():
    r = Result.success("val", count=5, source="db")
    assert r.details["count"] == 5
    assert r.details["source"] == "db"


def test_failure_with_details():
    r = Result.failure("err", code=404)
    assert r.details["code"] == 404


def test_result_generic_with_dict():
    r: Result[dict] = Result.success({"key": "value"})
    assert r.value["key"] == "value"


def test_result_generic_with_none_value():
    r: Result[None] = Result.success(None)
    assert r.ok is True
    assert r.value is None
