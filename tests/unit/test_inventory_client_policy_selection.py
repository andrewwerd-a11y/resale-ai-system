from __future__ import annotations

from packages.core.src import config as core_config
from packages.ebay.src.inventory_client import EbayInventoryClient


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _set_base_env(monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    core_config.get_settings.cache_clear()


def test_configured_policy_ids_override_api_selection(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "cfg-fulfill")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "cfg-payment")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "cfg-return")
    core_config.get_settings.cache_clear()

    client = EbayInventoryClient()

    def fail_if_called(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Policy API should not be called when all IDs are configured")

    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fail_if_called)

    policies = client.get_seller_policies()
    assert policies == {
        "fulfillment_id": "cfg-fulfill",
        "payment_id": "cfg-payment",
        "return_id": "cfg-return",
    }


def test_missing_configured_ids_fall_back_to_first_policy(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "")
    core_config.get_settings.cache_clear()

    client = EbayInventoryClient()

    def fake_get(url, **_kwargs):
        if "fulfillment_policy" in url:
            return _Resp(200, {"fulfillmentPolicies": [{"fulfillmentPolicyId": "f1"}]})
        if "payment_policy" in url:
            return _Resp(200, {"paymentPolicies": [{"paymentPolicyId": "p1"}]})
        return _Resp(200, {"returnPolicies": [{"returnPolicyId": "r1"}]})

    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fake_get)

    policies = client.get_seller_policies()
    assert policies == {
        "fulfillment_id": "f1",
        "payment_id": "p1",
        "return_id": "r1",
    }


def test_partial_configured_policy_ids_mix_with_fallback(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "cfg-payment")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "")
    core_config.get_settings.cache_clear()

    client = EbayInventoryClient()

    called_urls: list[str] = []

    def fake_get(url, **_kwargs):
        called_urls.append(url)
        if "fulfillment_policy" in url:
            return _Resp(200, {"fulfillmentPolicies": [{"fulfillmentPolicyId": "f2"}]})
        if "return_policy" in url:
            return _Resp(200, {"returnPolicies": [{"returnPolicyId": "r2"}]})
        return _Resp(500, {})

    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fake_get)

    policies = client.get_seller_policies()
    assert policies == {
        "fulfillment_id": "f2",
        "payment_id": "cfg-payment",
        "return_id": "r2",
    }
    assert all("payment_policy" not in url for url in called_urls)
