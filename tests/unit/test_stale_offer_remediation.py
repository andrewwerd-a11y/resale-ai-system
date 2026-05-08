from __future__ import annotations

import copy

from apps.api.src.services.stale_offer_remediation import (
    REQUIRED_TYPED_CONFIRMATION,
    build_remediation_payload_hash,
    execute_refresh_existing_unpublished_offer,
)


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
        "local_status": "export_ready",
        "local_category_id": "14056",
        "local_category_name": "Atlases",
        "local_condition_id": "3000",
        "local_inventory_condition_enum": "USED_GOOD",
        "offer_id": "156719395011",
        "listing_id": "",
        "planned_action": "publish_existing_offer",
        "blocked_by_repair_queue": True,
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "existing_offer_diagnostics": {
            "source": "live_readonly",
            "read_available": True,
            "offer_id": "156719395011",
            "offer_exists": True,
            "status": "UNPUBLISHED",
            "category_id": "14056",
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


def _refusal_codes(result: dict) -> set[str]:
    return {reason["code"] for reason in result["refusal_reasons"]}


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
