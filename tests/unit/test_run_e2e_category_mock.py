from __future__ import annotations

from scripts.run_e2e import _classify_mock_category_failure


def test_classify_mock_category_failure_accepts_structured_upstream_codes():
    allowed, code = _classify_mock_category_failure(
        502, {"detail": {"code": "UPSTREAM_CONNECTION", "message": "connect failed"}}
    )
    assert allowed is True
    assert code == "UPSTREAM_CONNECTION"


def test_classify_mock_category_failure_accepts_legacy_winerror_detail():
    allowed, code = _classify_mock_category_failure(
        502,
        {"detail": "[WinError 10061] No connection could be made because the target machine actively refused it"},
    )
    assert allowed is True
    assert code == "UPSTREAM_CONNECTION"


def test_classify_mock_category_failure_rejects_non_upstream_4xx():
    allowed, code = _classify_mock_category_failure(400, {"detail": "bad input"})
    assert allowed is False
    assert code == ""
