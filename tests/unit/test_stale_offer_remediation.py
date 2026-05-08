from __future__ import annotations

import copy

from apps.api.src.services.stale_offer_remediation import (
    PUBLISH_DECISION_TYPED_CONFIRMATION,
    REQUIRED_TYPED_CONFIRMATION,
    build_stale_offer_publish_decision_preview,
    build_publish_decision_payload_hash,
    build_remediation_payload_hash,
    execute_approved_stale_offer_publish_decision,
    execute_approved_refresh_existing_unpublished_offer,
    execute_refresh_existing_unpublished_offer,
    render_stale_offer_remediation_approval_packet,
)
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord


class FakeRemediationExecutor:
    def __init__(self) -> None:
        self.inventory_calls: list[tuple[str, dict]] = []
        self.offer_calls: list[tuple[str, dict]] = []
        self.publish_calls = 0
        self.delete_calls = 0
        self.withdraw_calls = 0
        self.revise_calls = 0
        self.create_calls = 0

    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        self.inventory_calls.append((sku, payload))
        return {"ok": True, "method": "put_inventory_item"}

    def put_offer(self, offer_id: str, payload: dict) -> dict:
        self.offer_calls.append((offer_id, payload))
        return {"ok": True, "method": "put_offer"}

    def publish_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.publish_calls += 1
        raise AssertionError("mock remediation must not publish")

    def delete_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.delete_calls += 1
        raise AssertionError("mock remediation must not delete offers")

    def withdraw_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.withdraw_calls += 1
        raise AssertionError("mock remediation must not withdraw")

    def revise_listing(self, *_args, **_kwargs):  # pragma: no cover
        self.revise_calls += 1
        raise AssertionError("mock remediation must not revise")

    def create_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.create_calls += 1
        raise AssertionError("mock remediation must not create offers")


class FailingInventoryExecutor(FakeRemediationExecutor):
    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        self.inventory_calls.append((sku, payload))
        return {"ok": False, "error": "inventory failed"}


class FailingOfferExecutor(FakeRemediationExecutor):
    def put_offer(self, offer_id: str, payload: dict) -> dict:
        self.offer_calls.append((offer_id, payload))
        return {"ok": False, "error": "offer failed"}


class FakePublishDecisionExecutor:
    def __init__(self) -> None:
        self.publish_calls: list[tuple[str, str]] = []
        self.inventory_calls = 0
        self.offer_calls = 0
        self.create_calls = 0
        self.batch_calls = 0
        self.generic_publish_calls = 0

    def publish_existing_offer(self, offer_id: str, sku: str) -> dict:
        self.publish_calls.append((offer_id, sku))
        return {
            "ok": True,
            "listing_id": "123456789012",
            "listing_url": "https://www.ebay.com/itm/123456789012",
            "offer_id": offer_id,
            "auth_headers_prepared": True,
            "auth_token_source": "oauth",
            "oauth_token_may_have_been_refreshed": False,
        }

    def publish_item(self, *_args, **_kwargs):  # pragma: no cover
        self.generic_publish_calls += 1
        raise AssertionError("generic publish_item must not be called")

    def put_inventory_item(self, *_args, **_kwargs):  # pragma: no cover
        self.inventory_calls += 1
        raise AssertionError("inventory PUT must not be called")

    def put_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.offer_calls += 1
        raise AssertionError("offer PUT must not be called")

    def create_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.create_calls += 1
        raise AssertionError("offer creation must not be called")

    def publish_batch(self, *_args, **_kwargs):  # pragma: no cover
        self.batch_calls += 1
        raise AssertionError("batch publish must not be called")


class FailingPublishDecisionExecutor(FakePublishDecisionExecutor):
    def publish_existing_offer(self, offer_id: str, sku: str) -> dict:
        self.publish_calls.append((offer_id, sku))
        return {
            "ok": False,
            "error": "publish failed",
            "error_code": "API_ERROR",
            "details": {
                "body": "Error 25001: Listing format not supported.",
                "stage": "publish_offer",
                "offer_id": offer_id,
                "auth_headers_prepared": True,
                "auth_token_source": "oauth",
                "oauth_token_may_have_been_refreshed": False,
            },
        }


def _eligible_diagnostics() -> dict:
    inventory_payload = {
        "condition": "USED_GOOD",
        "conditionDescription": "Cover creasing.",
        "product": {
            "title": "Rand McNally Atlas",
            "imageUrls": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        },
    }
    offer_payload = {
        "sku": "BK-000008",
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "categoryId": "14056",
        "availableQuantity": 1,
        "pricingSummary": {"price": {"value": "22.00", "currency": "USD"}},
    }
    return {
        "sku": "BK-000008",
        "found": True,
        "live_readonly_requested": True,
        "live_readonly_performed": True,
        "live_readonly_methods_called": ["get_offer", "get_inventory_item", "get_item_condition_policies"],
        "live_readonly_errors": [],
        "local_status": "export_ready",
        "local_category_id": "14056",
        "local_category_name": "Atlases",
        "local_condition_id": "3000",
        "local_inventory_condition_enum": "USED_GOOD",
        "offer_id": "156719395011",
        "listing_id": "",
        "planned_action": "publish_existing_offer",
        "blocked_by_repair_queue": True,
        "retry_allowed": False,
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "existing_offer_diagnostics": {
            "source": "live_readonly",
            "read_available": True,
            "offer_id": "156719395011",
            "offer_exists": True,
            "status": "UNPUBLISHED",
            "category_id": "14056",
            "merchant_location_key": "real-location",
            "listing_policies": {
                "fulfillmentPolicyId": "287672421015",
                "paymentPolicyId": "287672342015",
                "returnPolicyId": "287672344015",
                "countryCode": "US",
            },
            "category_differs_from_local": False,
        },
        "inventory_item_diagnostics": {
            "source": "live_readonly",
            "read_available": True,
            "sku": "BK-000008",
            "inventory_item_exists": True,
            "condition_enum": "USED_GOOD",
            "condition_differs_from_local": False,
        },
        "category_condition_policy_diagnostics": {
            "source": "live_readonly_metadata",
            "read_available": True,
            "category_id": "14056",
            "condition_id": "3000",
            "live_policy_allows_condition": True,
            "live_metadata_supports_changing_condition": False,
        },
        "stale_offer_remediation_draft": {
            "sku": "BK-000008",
            "repair_plan_id": "repair-plan-1",
            "latest_publish_attempt_id": "attempt-1",
            "remediation_type": "refresh_existing_unpublished_offer",
            "live_execution_enabled": False,
            "operator_approval_required": True,
            "publish_after_remediation": False,
            "no_mutation_performed": True,
            "actionable": False,
            "safe_to_execute": False,
            "status": "draft_preview_available",
            "safe_to_preview": True,
            "refusal_reasons": [],
            "offer_id": "156719395011",
            "listing_id": "",
            "offer_status": "UNPUBLISHED",
            "category_id": "14056",
            "category_name": "Atlases",
            "condition_id": "3000",
            "inventory_condition_enum": "USED_GOOD",
            "live_policy_result": {
                "source": "live_readonly_metadata",
                "read_available": True,
                "live_policy_allows_condition": True,
                "allowed_condition_ids": ["1000", "3000"],
                "local_policy_status": "confirmed_by_live_readonly_metadata",
            },
            "stale_offer_reasoning": "Existing unpublished offer may need refresh.",
            "intended_inventory_item_payload_preview": inventory_payload,
            "intended_offer_payload_preview": offer_payload,
            "intended_call_sequence_preview": [
                {
                    "order": 1,
                    "method": "PUT",
                    "endpoint": "/sell/inventory/v1/inventory_item/BK-000008",
                    "preview_only": True,
                    "mutation_performed": False,
                },
                {
                    "order": 2,
                    "method": "PUT",
                    "endpoint": "/sell/inventory/v1/offer/156719395011",
                    "preview_only": True,
                    "mutation_performed": False,
                },
                {
                    "order": 3,
                    "method": "NONE",
                    "endpoint": "",
                    "preview_only": True,
                    "mutation_performed": False,
                    "note": "Do not publish in this phase.",
                },
            ],
        },
    }


def _execute(diagnostics: dict, **overrides) -> tuple[dict, FakeRemediationExecutor]:
    executor = overrides.pop("executor", FakeRemediationExecutor())
    result = execute_refresh_existing_unpublished_offer(
        sku=overrides.pop("sku", "BK-000008"),
        diagnostics=diagnostics,
        operator_approved=overrides.pop("operator_approved", True),
        executor=executor,
        **overrides,
    )
    return result, executor


def _approval(diagnostics: dict) -> dict:
    draft = diagnostics["stale_offer_remediation_draft"]
    return {
        "sku": diagnostics["sku"],
        "remediation_type": "refresh_existing_unpublished_offer",
        "repair_plan_id": draft["repair_plan_id"],
        "latest_publish_attempt_id": draft["latest_publish_attempt_id"],
        "offer_id": draft["offer_id"],
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_listing_id_empty": True,
        "confirm_category_id": diagnostics["local_category_id"],
        "confirm_condition_id": diagnostics["local_condition_id"],
        "confirm_inventory_condition_enum": diagnostics["local_inventory_condition_enum"],
        "confirm_publish_after_remediation": False,
        "operator_approved": True,
        "typed_confirmation": REQUIRED_TYPED_CONFIRMATION,
        "approved_payload_hash": build_remediation_payload_hash(draft),
    }


def _execute_approved(
    diagnostics: dict,
    *,
    approval: dict | None = None,
    executor: FakeRemediationExecutor | None = None,
    live_remediation_enabled: bool = True,
    post_refresh=None,
) -> tuple[dict, FakeRemediationExecutor | None]:
    effective_executor = executor if executor is not None else FakeRemediationExecutor()
    result = execute_approved_refresh_existing_unpublished_offer(
        sku="BK-000008",
        diagnostics=diagnostics,
        approval_request=approval or _approval(diagnostics),
        executor=effective_executor,
        live_remediation_enabled=live_remediation_enabled,
        post_refresh_diagnostics_provider=post_refresh,
    )
    return result, effective_executor


def _refusal_codes(result: dict) -> set[str]:
    return {reason["code"] for reason in result["refusal_reasons"]}


def _expected_live_offer_payload(diagnostics: dict) -> dict:
    payload = copy.deepcopy(diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"])
    payload["merchantLocationKey"] = "real-location"
    payload["listingPolicies"] = {
        "fulfillmentPolicyId": "287672421015",
        "paymentPolicyId": "287672342015",
        "returnPolicyId": "287672344015",
    }
    return payload


def _diagnostics_with_placeholder_offer_payload(field: str) -> dict:
    diagnostics = _eligible_diagnostics()
    if field == "merchantLocationKey":
        diagnostics["existing_offer_diagnostics"]["merchant_location_key"] = "preview-location"
        return diagnostics
    key_map = {
        "fulfillmentPolicyId": "preview-fulfillment-policy",
        "paymentPolicyId": "preview-payment-policy",
        "returnPolicyId": "preview-return-policy",
    }
    diagnostics["existing_offer_diagnostics"]["listing_policies"][field] = key_map[field]
    return diagnostics


class _FakePublishDecisionSession:
    def __init__(self, plan: PublishRepairPlanRecord | None) -> None:
        self._plan = plan

    def get(self, model, plan_id: str):
        if model is PublishRepairPlanRecord and self._plan and self._plan.id == plan_id:
            return self._plan
        return None


def _publish_decision_plan(**overrides) -> PublishRepairPlanRecord:
    values = {
        "id": "repair-plan-1",
        "sku": "BK-000008",
        "publish_attempt_id": "attempt-1",
        "status": "needs_manual_review",
        "retry_allowed": False,
        "requires_review": True,
        "repair_layer": "post_refresh_publish_decision",
        "classified_error_code": "requires_publish_decision_after_refresh",
    }
    values.update(overrides)
    return PublishRepairPlanRecord(**values)


def test_publish_decision_preview_builds_template_for_eligible_diagnostics(monkeypatch) -> None:
    diagnostics = _eligible_diagnostics()
    plan = _publish_decision_plan()

    class _Repo:
        def __init__(self, _session) -> None:
            pass

        def get_by_sku(self, _sku: str):
            return type("ItemStub", (), {"offer_id": "156719395011", "image_paths": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"]})()

    class _Client:
        def extract_hosted_photo_urls(self, values: list[str]) -> list[str]:
            return values

    monkeypatch.setattr("apps.api.src.services.stale_offer_remediation.ItemRepository", _Repo)
    monkeypatch.setattr(
        "apps.api.src.services.stale_offer_remediation.get_publish_repair_blocker",
        lambda *_args, **_kwargs: {
            "blocked_by_repair_queue": True,
            "repair_plan_id": "repair-plan-1",
            "latest_publish_attempt_id": "attempt-1",
            "classified_error_code": "requires_publish_decision_after_refresh",
            "retry_allowed": False,
        },
    )
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", _Client)

    preview = build_stale_offer_publish_decision_preview(
        session=_FakePublishDecisionSession(plan),
        sku="BK-000008",
        repair_plan_id="repair-plan-1",
        diagnostics=diagnostics | {"classified_error_code": "requires_publish_decision_after_refresh"},
    )

    assert preview["eligible_for_publish_decision_preview"] is True
    assert preview["read_only"] is True
    assert preview["no_mutation_performed"] is True
    assert preview["no_ebay_mutation_performed"] is True
    assert preview["no_publish_performed"] is True
    assert preview["publish_call_preview"]["method"] == "POST"
    assert preview["publish_call_preview"]["endpoint"] == "/sell/inventory/v1/offer/156719395011/publish"
    assert preview["typed_confirmation_required"] == PUBLISH_DECISION_TYPED_CONFIRMATION
    assert preview["payload_hash"] == build_publish_decision_payload_hash(
        {
            "sku": "BK-000008",
            "repair_plan_id": "repair-plan-1",
            "latest_publish_attempt_id": "attempt-1",
            "publish_call_preview": preview["publish_call_preview"],
            "current_blocking_plan_summary": preview["current_blocking_plan_summary"],
            "live_prerequisites_summary": preview["live_prerequisites_summary"],
        }
    )
    template = preview["required_approval_fields_template"]
    assert template["typed_confirmation"] == PUBLISH_DECISION_TYPED_CONFIRMATION
    assert template["approved_payload_hash"] == preview["payload_hash"]
    assert template["confirm_blocker_classified_error_code"] == "requires_publish_decision_after_refresh"


def test_publish_decision_preview_refuses_placeholder_policy_and_missing_hosted_urls(monkeypatch) -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("fulfillmentPolicyId")
    plan = _publish_decision_plan()

    class _Repo:
        def __init__(self, _session) -> None:
            pass

        def get_by_sku(self, _sku: str):
            return type("ItemStub", (), {"offer_id": "156719395011", "image_paths": ["C:\\photos\\BK-000008\\01.jpg"]})()

    class _Client:
        def extract_hosted_photo_urls(self, values: list[str]) -> list[str]:
            return []

    monkeypatch.setattr("apps.api.src.services.stale_offer_remediation.ItemRepository", _Repo)
    monkeypatch.setattr(
        "apps.api.src.services.stale_offer_remediation.get_publish_repair_blocker",
        lambda *_args, **_kwargs: {
            "blocked_by_repair_queue": True,
            "repair_plan_id": "repair-plan-1",
            "latest_publish_attempt_id": "attempt-1",
            "classified_error_code": "requires_publish_decision_after_refresh",
            "retry_allowed": False,
        },
    )
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", _Client)

    preview = build_stale_offer_publish_decision_preview(
        session=_FakePublishDecisionSession(plan),
        sku="BK-000008",
        repair_plan_id="repair-plan-1",
        diagnostics=diagnostics | {"classified_error_code": "requires_publish_decision_after_refresh"},
    )

    assert preview["eligible_for_publish_decision_preview"] is False
    codes = {reason["code"] for reason in preview["blockers"]}
    assert "missing_hosted_public_image_urls" in codes
    assert "missing_real_listing_policy_ids" in codes
    assert "placeholder_listing_policy_detected" in codes


def test_stale_offer_remediation_approval_preview_builds_template_for_eligible_diagnostics() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    diagnostics = _eligible_diagnostics()
    preview = build_stale_offer_remediation_approval_preview(diagnostics)

    assert preview["eligible_for_approval_preview"] is True
    assert preview["remediation_type"] == "refresh_existing_unpublished_offer"
    assert preview["approval_required"] is True
    assert preview["typed_confirmation_required"] == REQUIRED_TYPED_CONFIRMATION
    assert preview["live_execution_enabled"] is False
    assert preview["no_mutation_performed"] is True
    assert preview["publish_after_remediation"] is False
    assert preview["safe_to_execute_now"] is False
    assert preview["payload_hash"] == build_remediation_payload_hash(diagnostics["stale_offer_remediation_draft"])
    template = preview["required_approval_fields_template"]
    assert template["sku"] == "BK-000008"
    assert template["typed_confirmation"] == REQUIRED_TYPED_CONFIRMATION
    assert template["approved_payload_hash"] == preview["payload_hash"]
    assert preview["next_step_warning"] == "This preview does not publish, does not refresh eBay, and does not clear the repair queue."


def test_stale_offer_remediation_approval_packet_contains_required_safety_statement() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    preview = build_stale_offer_remediation_approval_preview(_eligible_diagnostics())
    packet = render_stale_offer_remediation_approval_packet(preview, generated_at="2026-05-07T00:00:00+00:00")

    assert "# Stale Offer Remediation Approval Packet - BK-000008" in packet
    assert "Read-only approval packet." in packet
    assert "No publish performed." in packet
    assert "No eBay refresh performed." in packet
    assert "No repair queue clear performed." in packet
    assert "No category/condition change performed." in packet


def test_stale_offer_remediation_approval_packet_contains_required_approval_template() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    preview = build_stale_offer_remediation_approval_preview(_eligible_diagnostics())
    packet = render_stale_offer_remediation_approval_packet(preview)

    assert "## Required Approval Template" in packet
    assert '"sku": "BK-000008"' in packet
    assert '"remediation_type": "refresh_existing_unpublished_offer"' in packet
    assert '"typed_confirmation": "REFRESH UNPUBLISHED OFFER ONLY"' in packet
    assert '"confirm_publish_after_remediation": false' in packet


def test_stale_offer_remediation_approval_packet_includes_payload_hash() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    diagnostics = _eligible_diagnostics()
    preview = build_stale_offer_remediation_approval_preview(diagnostics)
    packet = render_stale_offer_remediation_approval_packet(preview)

    assert build_remediation_payload_hash(diagnostics["stale_offer_remediation_draft"]) in packet


def test_stale_offer_remediation_approval_packet_does_not_execute_remediation() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    preview = build_stale_offer_remediation_approval_preview(_eligible_diagnostics())
    packet = render_stale_offer_remediation_approval_packet(preview)

    assert "This packet does not authorize publish" in packet
    assert "preview_only=true" in packet
    assert "mutation_performed=false" in packet


def test_stale_offer_remediation_approval_packet_does_not_publish() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    preview = build_stale_offer_remediation_approval_preview(_eligible_diagnostics())
    packet = render_stale_offer_remediation_approval_packet(preview)

    assert "Publish after remediation: false" in packet
    assert "Live execution enabled: false" in packet
    assert "Safe to execute now: false" in packet


def test_stale_offer_remediation_approval_packet_requires_explicit_sku() -> None:
    try:
        render_stale_offer_remediation_approval_packet({"sku": ""})
    except ValueError as exc:
        assert "sku is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected missing SKU to fail")


def test_refresh_existing_unpublished_offer_mock_executes_previewed_payloads_only() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "mock_executed"
    assert result["mode"] == "mock_only"
    assert result["remediation_type"] == "refresh_existing_unpublished_offer"
    assert result["live_execution_enabled"] is False
    assert result["operator_approval_required"] is True
    assert result["operator_approval_received"] is True
    assert result["publish_after_remediation"] is False
    assert result["mocked_mutation_performed"] is True
    assert result["real_ebay_mutation_performed"] is False
    assert result["no_live_mutation_performed"] is True
    assert result["repair_plan_id"] == "repair-plan-1"
    assert result["latest_publish_attempt_id"] == "attempt-1"
    assert result["offer_id"] == "156719395011"
    assert result["offer_status"] == "UNPUBLISHED"
    assert result["inventory_payload_preview"]["condition"] == "USED_GOOD"
    assert result["offer_payload_preview"]["categoryId"] == "14056"
    assert result["call_sequence"][0]["method"] == "PUT"
    assert result["call_sequence"][1]["endpoint"].endswith("/offer/156719395011")
    assert executor.inventory_calls == [
        ("BK-000008", diagnostics["stale_offer_remediation_draft"]["intended_inventory_item_payload_preview"])
    ]
    assert executor.offer_calls == [
        ("156719395011", diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"])
    ]
    assert executor.publish_calls == 0
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0
    assert executor.create_calls == 0
    assert "separately approved one-SKU publish retry" in result["next_recommended_action"]


def test_refresh_existing_unpublished_offer_blocks_runtime_live_execution_even_when_requested() -> None:
    diagnostics = _eligible_diagnostics()
    executor = FakeRemediationExecutor()

    result, executor = _execute(diagnostics, execute_live=True, executor=executor)

    assert result["execution_status"] == "live_execution_disabled"
    assert result["code"] == "live_execution_disabled"
    assert "live_execution_disabled" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []
    assert result["mocked_mutation_performed"] is False
    assert result["real_ebay_mutation_performed"] is False


def test_refresh_existing_unpublished_offer_blocks_without_mock_executor() -> None:
    diagnostics = _eligible_diagnostics()

    result = execute_refresh_existing_unpublished_offer(
        sku="BK-000008",
        diagnostics=diagnostics,
        operator_approved=True,
    )

    assert result["execution_status"] == "live_execution_disabled"
    assert result["code"] == "live_execution_disabled"
    assert result["mode"] == "live_disabled"
    assert result["no_mutation_performed"] is True
    assert "live_execution_disabled" in _refusal_codes(result)


def test_refresh_existing_unpublished_offer_requires_operator_approval() -> None:
    result, executor = _execute(_eligible_diagnostics(), operator_approved=False)

    assert result["execution_status"] == "blocked"
    assert "operator_approval_required" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_wrong_remediation_type() -> None:
    result, executor = _execute(_eligible_diagnostics(), remediation_type="change_condition")

    assert result["execution_status"] == "blocked"
    assert "wrong_remediation_type" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_publish_after_remediation() -> None:
    result, executor = _execute(_eligible_diagnostics(), publish_after_remediation=True)

    assert result["execution_status"] == "blocked"
    assert "publish_after_remediation_not_allowed" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_sku_mismatch() -> None:
    result, executor = _execute(_eligible_diagnostics(), sku="BK-000009")

    assert result["execution_status"] == "blocked"
    assert "sku_mismatch" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_stale_or_non_previewable_draft() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["status"] = "refused"
    diagnostics["stale_offer_remediation_draft"]["safe_to_preview"] = False

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "draft_not_previewable" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_missing_offer_id() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["offer_id"] = ""
    diagnostics["offer_id"] = ""

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_offer_id" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_listing_id_present() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["listing_id"] = "987654321012"
    diagnostics["listing_id"] = "987654321012"

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "listing_id_present" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_item_already_listed() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["local_status"] = "listed"

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "item_already_listed" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_offer_status_not_unpublished() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["offer_status"] = "PUBLISHED"
    diagnostics["existing_offer_diagnostics"]["status"] = "PUBLISHED"

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "offer_status_not_unpublished" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_when_repair_queue_not_blocking() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["blocked_by_repair_queue"] = False

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "repair_queue_not_blocking" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_missing_latest_repair_plan() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["repair_plan_id"] = ""
    diagnostics["repair_plan_id"] = ""

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_latest_repair_plan" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_policy_rejects_condition() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["live_policy_result"]["live_policy_allows_condition"] = False
    diagnostics["category_condition_policy_diagnostics"]["live_policy_allows_condition"] = False

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "live_policy_does_not_allow_condition" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_inventory_condition_differs() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["inventory_item_diagnostics"]["condition_enum"] = "LIKE_NEW"
    diagnostics["inventory_item_diagnostics"]["condition_differs_from_local"] = True

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "inventory_condition_differs_from_local" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_existing_offer_category_differs() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["existing_offer_diagnostics"]["category_id"] = "12345"
    diagnostics["existing_offer_diagnostics"]["category_differs_from_local"] = True

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "existing_offer_category_differs_from_local" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_when_category_condition_change_appears_needed() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["category_condition_policy_diagnostics"]["live_metadata_supports_changing_condition"] = True

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "category_condition_change_appears_needed" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_refuses_missing_inventory_condition_read() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["inventory_item_diagnostics"].pop("condition_enum")

    result, executor = _execute(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_inventory_condition_read" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_refresh_existing_unpublished_offer_does_not_mutate_payload_previews() -> None:
    diagnostics = _eligible_diagnostics()
    original = copy.deepcopy(diagnostics)

    result, executor = _execute(diagnostics)
    executor.inventory_calls[0][1]["condition"] = "MUTATED_IN_TEST"
    executor.offer_calls[0][1]["categoryId"] = "MUTATED_IN_TEST"

    assert result["execution_status"] == "mock_executed"
    assert diagnostics == original
    assert result["inventory_payload_preview"]["condition"] == "USED_GOOD"
    assert result["offer_payload_preview"]["categoryId"] == "14056"


def test_live_offer_refresh_requires_explicit_sku_and_operator_approval() -> None:
    diagnostics = _eligible_diagnostics()

    result = execute_refresh_existing_unpublished_offer(
        sku="",
        diagnostics=diagnostics,
        operator_approved=False,
        execute_live=True,
        approval_request={},
    )

    codes = _refusal_codes(result)
    assert result["execution_status"] == "live_execution_disabled"
    assert "missing_sku" in codes
    assert "operator_approval_required" in codes
    assert "missing_approval_request" in codes
    assert result["real_ebay_mutation_performed"] is False


def test_live_offer_refresh_blocks_when_feature_flag_disabled() -> None:
    diagnostics = _eligible_diagnostics()
    executor = FakeRemediationExecutor()

    result, executor = _execute(
        diagnostics,
        execute_live=True,
        approval_request=_approval(diagnostics),
        executor=executor,
    )

    assert result["execution_status"] == "live_execution_disabled"
    assert result["code"] == "live_execution_disabled"
    assert result["live_remediation_feature_enabled"] is False
    assert "live_execution_disabled" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_publish_after_remediation() -> None:
    diagnostics = _eligible_diagnostics()
    approval = _approval(diagnostics)
    approval["confirm_publish_after_remediation"] = True

    result, executor = _execute(
        diagnostics,
        publish_after_remediation=True,
        approval_request=approval,
    )

    codes = _refusal_codes(result)
    assert result["execution_status"] == "blocked"
    assert "publish_after_remediation_not_allowed" in codes
    assert "approval_publish_after_remediation_not_false" in codes
    assert "requested_publish_after_remediation_not_allowed" in codes
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_rechecks_readonly_diagnostics_before_mutation() -> None:
    diagnostics = _eligible_diagnostics()
    stale_diagnostics = copy.deepcopy(diagnostics)
    stale_diagnostics["existing_offer_diagnostics"]["status"] = "PUBLISHED"
    stale_diagnostics["stale_offer_remediation_draft"]["offer_status"] = "PUBLISHED"

    result, executor = _execute(
        diagnostics,
        approval_request=_approval(diagnostics),
        preflight_diagnostics=stale_diagnostics,
    )

    assert result["preflight_recheck_performed"] is True
    assert result["execution_status"] == "blocked"
    assert "offer_status_not_unpublished" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_if_offer_status_changed_from_unpublished() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["existing_offer_diagnostics"]["status"] = "PUBLISHED"
    diagnostics["stale_offer_remediation_draft"]["offer_status"] = "PUBLISHED"

    result, executor = _execute(diagnostics, approval_request=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "offer_status_not_unpublished" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_if_listing_id_appears() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["listing_id"] = "987654321012"
    diagnostics["stale_offer_remediation_draft"]["listing_id"] = "987654321012"

    result, executor = _execute(diagnostics, approval_request=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "listing_id_present" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_if_inventory_condition_differs() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["inventory_item_diagnostics"]["condition_enum"] = "LIKE_NEW"

    result, executor = _execute(diagnostics, approval_request=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "inventory_condition_differs_from_local" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_if_live_policy_no_longer_allows_condition() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["live_policy_result"]["live_policy_allows_condition"] = False

    result, executor = _execute(diagnostics, approval_request=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "live_policy_does_not_allow_condition" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_blocks_if_payload_hash_differs_from_approval() -> None:
    diagnostics = _eligible_diagnostics()
    approval = _approval(diagnostics)
    approval["approved_payload_hash"] = "not-the-current-hash"

    result, executor = _execute(diagnostics, approval_request=approval)

    assert result["execution_status"] == "blocked"
    assert "approval_payload_hash_mismatch" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_live_offer_refresh_mock_calls_inventory_put_then_offer_put_only() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute(diagnostics, approval_request=_approval(diagnostics))

    assert result["execution_status"] == "mock_executed"
    assert executor.inventory_calls == [
        ("BK-000008", diagnostics["stale_offer_remediation_draft"]["intended_inventory_item_payload_preview"])
    ]
    assert executor.offer_calls == [
        ("156719395011", diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"])
    ]
    assert executor.publish_calls == 0
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0
    assert executor.create_calls == 0


def test_live_offer_refresh_never_calls_publish_offer() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute(diagnostics, approval_request=_approval(diagnostics))

    assert result["execution_status"] == "mock_executed"
    assert executor.publish_calls == 0
    assert result["publish_after_remediation"] is False


def test_live_offer_refresh_never_deletes_or_recreates_offer() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute(diagnostics, approval_request=_approval(diagnostics))

    assert result["execution_status"] == "mock_executed"
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0
    assert executor.create_calls == 0


def test_live_offer_refresh_runs_post_refresh_readonly_diagnostics_if_mock_executes() -> None:
    diagnostics = _eligible_diagnostics()
    post_refresh = {"sku": "BK-000008", "no_mutation_performed": True, "offer_status": "UNPUBLISHED"}

    result, _executor = _execute(
        diagnostics,
        approval_request=_approval(diagnostics),
        post_refresh_diagnostics_provider=lambda: post_refresh,
    )

    assert result["execution_status"] == "mock_executed"
    assert result["post_refresh_readonly_diagnostics_performed"] is True
    assert result["post_refresh_readonly_diagnostics"] == post_refresh


def test_live_offer_refresh_does_not_clear_repair_queue_or_mark_listed() -> None:
    diagnostics = _eligible_diagnostics()

    result, _executor = _execute(diagnostics, approval_request=_approval(diagnostics))

    assert result["execution_status"] == "mock_executed"
    assert result["audit_log_preview"]["no_publish_performed"] is True
    assert result["audit_log_preview"]["real_ebay_mutation_performed"] is False
    assert result["repair_plan_id"] == "repair-plan-1"
    assert result["next_recommended_action"].startswith("Run publish diagnostics/readiness again")


def test_live_offer_refresh_blocks_wrong_typed_confirmation() -> None:
    diagnostics = _eligible_diagnostics()
    approval = _approval(diagnostics)
    approval["typed_confirmation"] = "refresh"

    result, executor = _execute(diagnostics, approval_request=approval)

    assert result["execution_status"] == "blocked"
    assert "approval_typed_confirmation_mismatch" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_requires_payload_hash_match() -> None:
    diagnostics = _eligible_diagnostics()
    approval = _approval(diagnostics)
    approval["approved_payload_hash"] = "wrong-hash"

    result, executor = _execute_approved(diagnostics, approval=approval)

    assert result["execution_status"] == "blocked"
    assert "approval_payload_hash_mismatch" in _refusal_codes(result)
    assert "preflight_payload_hash_mismatch" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_requires_live_readonly_preflight() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["live_readonly_performed"] = False
    diagnostics["live_readonly_methods_called"] = []

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "live_readonly_preflight_required" in _refusal_codes(result)
    assert "missing_live_readonly_method" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_placeholder_fulfillment_policy_before_mutation() -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("fulfillmentPolicyId")

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_real_listing_policy_ids" in _refusal_codes(result)
    assert "placeholder_listing_policy_detected" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_placeholder_payment_policy_before_mutation() -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("paymentPolicyId")

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_real_listing_policy_ids" in _refusal_codes(result)
    assert "placeholder_listing_policy_detected" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_placeholder_return_policy_before_mutation() -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("returnPolicyId")

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "missing_real_listing_policy_ids" in _refusal_codes(result)
    assert "placeholder_listing_policy_detected" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_placeholder_merchant_location_before_mutation() -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("merchantLocationKey")

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert "merchant_location_key_unresolved" in _refusal_codes(result)
    assert "placeholder_listing_policy_detected" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_when_live_remediation_disabled() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute_approved(diagnostics, live_remediation_enabled=False)

    assert result["execution_status"] == "live_execution_disabled"
    assert result["code"] == "live_execution_disabled"
    assert result["live_execution_enabled"] is False
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_uses_live_existing_offer_policy_ids_when_available() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "refresh_completed"
    assert executor.offer_calls == [("156719395011", _expected_live_offer_payload(diagnostics))]
    assert result["offer_payload_live_executable"] == _expected_live_offer_payload(diagnostics)


def test_execute_approved_refresh_never_puts_offer_with_preview_policy_ids() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"]["merchantLocationKey"] = "preview-location"
    diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"]["listingPolicies"] = {
        "fulfillmentPolicyId": "preview-fulfillment-policy",
        "paymentPolicyId": "preview-payment-policy",
        "returnPolicyId": "preview-return-policy",
        "countryCode": "US",
    }
    approval = _approval(diagnostics)

    result, executor = _execute_approved(diagnostics, approval=approval)

    assert result["execution_status"] == "refresh_completed"
    offer_payload = executor.offer_calls[0][1]
    assert "preview-location" not in str(offer_payload)
    assert "preview-fulfillment-policy" not in str(offer_payload)
    assert "preview-payment-policy" not in str(offer_payload)
    assert "preview-return-policy" not in str(offer_payload)


def test_execute_approved_refresh_inventory_put_not_called_when_policy_ids_are_placeholders() -> None:
    diagnostics = _diagnostics_with_placeholder_offer_payload("fulfillmentPolicyId")

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "blocked"
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_approval_preview_distinguishes_preview_payload_from_live_executable_payload() -> None:
    from apps.api.src.services.stale_offer_remediation import build_stale_offer_remediation_approval_preview

    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"]["merchantLocationKey"] = "preview-location"
    preview = build_stale_offer_remediation_approval_preview(diagnostics)
    result, executor = _execute_approved(diagnostics, approval=preview["required_approval_fields_template"])

    assert preview["no_mutation_performed"] is True
    assert preview["required_approval_fields_template"]["approved_payload_hash"] == preview["payload_hash"]
    assert result["execution_status"] == "refresh_completed"
    assert result["offer_payload_preview"]["merchantLocationKey"] == "preview-location"
    assert result["offer_payload_live_executable"]["merchantLocationKey"] == "real-location"
    assert executor.offer_calls[0][1]["merchantLocationKey"] == "real-location"


def test_execute_approved_refresh_calls_inventory_put_then_offer_put_only() -> None:
    diagnostics = _eligible_diagnostics()
    post_refresh = {"sku": "BK-000008", "read_only": True, "no_mutation_performed": True}

    result, executor = _execute_approved(diagnostics, post_refresh=lambda: post_refresh)

    assert result["execution_status"] == "refresh_completed"
    assert result["calls_performed"] == ["put_inventory_item", "put_offer"]
    assert executor.inventory_calls == [
        ("BK-000008", diagnostics["stale_offer_remediation_draft"]["intended_inventory_item_payload_preview"])
    ]
    assert executor.offer_calls == [
        ("156719395011", _expected_live_offer_payload(diagnostics))
    ]
    assert executor.publish_calls == 0
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0
    assert executor.create_calls == 0
    assert result["no_publish_performed"] is True
    assert result["repair_queue_cleared"] is False
    assert result["item_status_after"] == "export_ready"
    assert result["post_refresh_diagnostics"] == post_refresh


def test_execute_approved_refresh_inventory_failure_skips_offer_put() -> None:
    diagnostics = _eligible_diagnostics()
    executor = FailingInventoryExecutor()

    result, executor = _execute_approved(diagnostics, executor=executor)

    assert result["execution_status"] == "failed_before_offer_refresh"
    assert result["stage"] == "put_inventory_item"
    assert result["calls_performed"] == ["put_inventory_item"]
    assert executor.inventory_calls
    assert executor.offer_calls == []
    assert result["no_publish_performed"] is True
    assert result["repair_queue_cleared"] is False


def test_execute_approved_refresh_offer_failure_reports_partial_failure() -> None:
    diagnostics = _eligible_diagnostics()
    executor = FailingOfferExecutor()
    post_refresh = {"sku": "BK-000008", "read_only": True}

    result, executor = _execute_approved(diagnostics, executor=executor, post_refresh=lambda: post_refresh)

    assert result["execution_status"] == "partial_failure_offer_refresh_failed"
    assert result["stage"] == "put_offer"
    assert result["calls_performed"] == ["put_inventory_item", "put_offer"]
    assert executor.inventory_calls
    assert executor.offer_calls
    assert result["post_refresh_diagnostics"] == post_refresh
    assert result["no_publish_performed"] is True
    assert result["repair_queue_cleared"] is False
    assert result["inventory_refresh_result"]["ok"] is True
    assert result["offer_refresh_result"]["ok"] is False
    assert result["no_mutation_performed"] is False
    assert result["real_ebay_mutation_performed"] is True
    assert result["no_live_mutation_performed"] is False


def test_execute_approved_refresh_blocks_if_offer_not_unpublished() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["existing_offer_diagnostics"]["status"] = "PUBLISHED"
    diagnostics["stale_offer_remediation_draft"]["offer_status"] = "PUBLISHED"

    result, executor = _execute_approved(diagnostics, approval=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "offer_status_not_unpublished" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_if_listing_id_present() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["listing_id"] = "987654321012"
    diagnostics["stale_offer_remediation_draft"]["listing_id"] = "987654321012"

    result, executor = _execute_approved(diagnostics, approval=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "listing_id_present" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_if_inventory_condition_differs() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["inventory_item_diagnostics"]["condition_enum"] = "LIKE_NEW"

    result, executor = _execute_approved(diagnostics, approval=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "inventory_condition_differs_from_local" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_blocks_if_live_policy_rejects_condition() -> None:
    diagnostics = _eligible_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["live_policy_result"]["live_policy_allows_condition"] = False
    diagnostics["category_condition_policy_diagnostics"]["live_policy_allows_condition"] = False

    result, executor = _execute_approved(diagnostics, approval=_approval(_eligible_diagnostics()))

    assert result["execution_status"] == "blocked"
    assert "live_policy_does_not_allow_condition" in _refusal_codes(result)
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_execute_approved_refresh_never_calls_publish_create_delete_withdraw_or_revise() -> None:
    diagnostics = _eligible_diagnostics()

    result, executor = _execute_approved(diagnostics)

    assert result["execution_status"] == "refresh_completed"
    assert executor.publish_calls == 0
    assert executor.create_calls == 0
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0
    assert result["publish_after_remediation"] is False
