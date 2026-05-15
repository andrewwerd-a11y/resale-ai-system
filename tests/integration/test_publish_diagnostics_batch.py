from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "publish_diagnostics_batch.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(**overrides) -> None:
    base = dict(
        sku="BK-000008",
        status=ItemStatus.EXPORT_READY,
        title_raw="Debug raw title",
        title_final="Debug title",
        description_final="Debug description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="14056",
        ebay_category_name="Atlases",
        condition_id="3000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        offer_id="156719395011",
        listing_id="",
        item_specifics={},
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_mutations(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("batch diagnostics must not call eBay mutation methods")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_inventory_item", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_offer", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload_all", fail)


def _allow_readonly_auth(monkeypatch):
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_readonly_auth_diagnostics",
        lambda _self: {
            "auth_readonly_available": True,
            "token_source_used": "env",
            "reason": "",
            "suggested_action": "",
            "refresh_allowed": False,
            "no_token_refresh_performed": True,
            "marketplace_id": "EBAY_US",
        },
    )


def _stub_live_reads(monkeypatch, *, inventory_condition: str = "USED_EXCELLENT", policy_ids: list[str] | None = None):
    policy_ids = policy_ids or ["1000", "3000"]

    def fake_offer(_self, offer_id):
        return Result.success(
            {
                "offerId": offer_id,
                "sku": "BK-000008",
                "status": "UNPUBLISHED",
                "categoryId": "14056",
                "marketplaceId": "EBAY_US",
                "merchantLocationKey": "warehouse-1",
                "listingPolicies": {
                    "fulfillmentPolicyId": "fulfillment-1",
                    "paymentPolicyId": "payment-1",
                    "returnPolicyId": "return-1",
                },
            }
        )

    def fake_inventory(_self, sku):
        return Result.success(
            {
                "sku": sku,
                "condition": inventory_condition,
                "product": {
                    "title": "Debug title",
                    "imageUrls": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
                },
            }
        )

    def fake_policy(_self, category_id):
        return Result.success(
            {
                "itemConditionPolicies": [
                    {
                        "categoryId": category_id,
                        "itemConditions": [{"conditionId": condition_id} for condition_id in policy_ids],
                    }
                ]
            }
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_offer", fake_offer)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item", fake_inventory)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies", fake_policy)


def _post_batch(skus: list[str], *, allow_live_readonly: bool = True):
    with _client() as client:
        return client.post(
            "/api/listings/publish-diagnostics/batch",
            json={"skus": skus, "allow_live_readonly": allow_live_readonly},
        )


def test_batch_diagnostics_blocks_stale_live_used_good_without_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _allow_readonly_auth(monkeypatch)
    _stub_live_reads(monkeypatch, inventory_condition="USED_GOOD", policy_ids=["1000", "3000"])
    _seed_item()

    resp = _post_batch(["BK-000008", "MISSING-SKU"])

    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnostic_version"] == "publish-debug-diagnostics.v1"
    assert body["report_type"] == "publish_diagnostics_batch"
    assert body["project"] == "resale-ai-system"
    assert body["environment"] == "local"
    assert body["persistable"] is True
    assert body["no_mutation_performed"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["session_warning"]
    assert body["copyable_report_markdown"]
    assert body["copyable_codex_prompt"]
    assert "workflow_hint=" in body["copyable_report_markdown"]
    assert "primary_blocker_family=condition" in body["copyable_report_markdown"]
    assert "Read-only analysis only." in body["copyable_codex_prompt"]
    assert "No eBay mutation." in body["copyable_codex_prompt"]
    assert "Do not publish through the normal flow." in body["copyable_codex_prompt"]

    stale = body["per_sku_results"][0]
    assert stale["sku"] == "BK-000008"
    assert stale["expected_inventory_enum"] == "USED_EXCELLENT"
    assert stale["live_inventory_condition_enum"] == "USED_GOOD"
    assert stale["live_inventory_condition_id"] == "5000"
    assert stale["ready_for_publish_preview"] is False
    assert stale["workflow_lane"] == "live_state_remediation_required"
    assert stale["primary_blocker_family"] == "condition"
    assert "ready_for_publish_preview" not in stale["blocker_codes"]
    assert "local_live_condition_mismatch" in stale["blocker_codes"]
    assert "condition_id_enum_mapping_mismatch" in stale["blocker_codes"]
    assert "live_inventory_condition_not_allowed_by_policy" in stale["blocker_codes"]
    assert "stale_live_inventory_condition_suspected" in stale["blocker_codes"]
    assert stale["recommended_next_action"].startswith("Do not publish.")
    assert stale["related_files_services"]

    missing = body["per_sku_results"][1]
    assert missing["sku"] == "MISSING-SKU"
    assert missing["found"] is False
    assert missing["blocker_codes"] == ["missing_local_item"]

    assert body["summary"]["total"] == 2
    assert body["summary"]["found"] == 1
    assert body["summary"]["missing"] == 1
    assert body["summary"]["blocked"] == 2
    assert "condition" in body["grouped_blocker_families"]
    assert "missing_local_item" in body["grouped_blocker_families"]
    assert "live_state_remediation_required" in body["grouped_workflow_lanes"]
    assert "ready_for_publish_preview" not in body["grouped_blocker_families"]


def test_batch_diagnostics_accepts_clean_live_used_excellent(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _allow_readonly_auth(monkeypatch)
    _stub_live_reads(monkeypatch, inventory_condition="USED_EXCELLENT", policy_ids=["1000", "3000"])
    _seed_item()

    resp = _post_batch(["BK-000008"])

    assert resp.status_code == 200
    result = resp.json()["per_sku_results"][0]
    assert result["blocker_codes"] == []
    assert result["ready_for_publish_preview"] is True
    assert "ready_for_publish_preview" in result["status_codes"]
    assert "ready_for_publish_preview" in result["success_checks"]
    assert "ready_for_publish_preview" not in resp.json()["grouped_blocker_families"]


def test_batch_diagnostics_classifies_image_hosting_failures(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(image_paths=[str(tmp_path / "local-only.jpg")], offer_id="")

    resp = _post_batch(["BK-000008"], allow_live_readonly=False)

    assert resp.status_code == 200
    result = resp.json()["per_sku_results"][0]
    assert result["workflow_lane"] == "image_hosting_candidate"
    assert result["workflow_hint"] == "image hosting needed before publish preview"
    assert result["primary_blocker_family"] == "images"
    assert "missing_hosted_images" in result["blocker_codes"]
    assert result["image_hosting_readiness"]["status"] == "missing"
    assert result["recommended_next_action"] == "Host item images to public URLs before publish preview or publish."
    prompt = resp.json()["copyable_codex_prompt"]
    assert "workflow_lane: image_hosting_candidate" in prompt
    assert "Investigate image hosting readiness only after confirming there are no higher-risk live-state, category, or condition blockers." in prompt
    assert result["raw_details"]
    serialized = str(result["raw_details"]).lower()
    assert "authorization" not in serialized
    assert "bearer" not in serialized
    assert "secret" not in serialized
    assert len(serialized) < 12000


def test_batch_diagnostics_classifies_already_listed_items_separately(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(status=ItemStatus.LISTED, listing_id="1234567890")

    resp = _post_batch(["BK-000008"], allow_live_readonly=False)

    assert resp.status_code == 200
    result = resp.json()["per_sku_results"][0]
    assert result["ready_for_publish_preview"] is False
    assert result["workflow_lane"] == "already_listed_or_sync_review"
    assert result["workflow_hint"] == "already listed / sync review"
    assert result["primary_blocker_family"] == "sync_review"
    assert result["blocker_codes"] == []
    assert "listed/sync-review item" in result["recommended_next_action"]
    assert resp.json()["summary"]["ready_for_publish_preview"] == 0
    prompt = resp.json()["copyable_codex_prompt"]
    assert "workflow_lane: already_listed_or_sync_review" in prompt
    assert "Review sync, revise readiness, and listed-state consistency only." in prompt
    assert "workflow_hint: fresh publish candidate" not in prompt
    assert "Do not treat this SKU as a fresh publish candidate" in prompt


def test_batch_diagnostics_live_state_blockers_outrank_image_hosting(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _allow_readonly_auth(monkeypatch)
    _stub_live_reads(monkeypatch, inventory_condition="USED_GOOD", policy_ids=["1000", "3000"])
    local_only = tmp_path / "local-only.jpg"
    local_only.write_bytes(b"fake-image")
    _seed_item(image_paths=[str(local_only)])

    resp = _post_batch(["BK-000008"], allow_live_readonly=True)

    assert resp.status_code == 200
    result = resp.json()["per_sku_results"][0]
    assert result["workflow_lane"] == "live_state_remediation_required"
    assert result["primary_blocker_family"] == "condition"
    assert "local_image_path_only" in result["blocker_codes"]
    assert result["blocker_codes"].index("stale_live_inventory_condition_suspected") < result["blocker_codes"].index("local_image_path_only")
    assert result["recommended_next_action"].startswith("Do not publish.")
    assert "Host item images" not in result["recommended_next_action"]
    prompt = resp.json()["copyable_codex_prompt"]
    assert "workflow_lane: live_state_remediation_required" in prompt
    assert "Do not publish through the normal flow." in prompt
    assert "Host item images to public URLs before publish preview or publish." not in prompt


def test_batch_diagnostics_unknown_manual_review_mixed_image_and_live_gap_avoids_host_images_first(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    local_only = tmp_path / "local-only.jpg"
    local_only.write_bytes(b"fake-image")
    _seed_item(
        status=ItemStatus.NEEDS_REVIEW,
        image_paths=[str(local_only)],
        offer_id="",
        listing_id="",
    )

    resp = _post_batch(["BK-000008"], allow_live_readonly=True)

    assert resp.status_code == 200
    body = resp.json()
    result = body["per_sku_results"][0]
    assert result["workflow_lane"] == "unknown_manual_review"
    assert result["workflow_hint"] == "status not publish candidate"
    assert "missing_live_inventory_item" in result["blocker_codes"]
    assert "local_image_path_only" in result["blocker_codes"]
    assert "Host item images" not in result["recommended_next_action"]
    assert "classification is clear" in result["recommended_next_action"]
    assert "status=needs_review; lane=unknown_manual_review; workflow_hint=status not publish candidate" in body["copyable_report_markdown"]
    prompt = body["copyable_codex_prompt"]
    assert "workflow_lane: unknown_manual_review" in prompt
    assert "Investigate why live inventory/readiness state is missing or unclear before treating this as normal publish prep." in prompt
    assert "Confirm whether this is a fresh publish-prep item, an already-listed/sync item, or a remediation item;" in prompt
    assert "Host item images to public URLs before publish preview or publish." not in prompt


def test_batch_diagnostics_honors_route_guard(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000008")
    _block_mutations(monkeypatch)

    resp = _post_batch(["BK-000008", "BK-999999"], allow_live_readonly=False)

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json()["detail"]


def test_batch_diagnostics_rejects_oversized_requests(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    skus = [f"SKU-{idx:06d}" for idx in range(51)]

    resp = _post_batch(skus, allow_live_readonly=False)

    assert resp.status_code == 400
    assert "50 SKUs" in resp.json()["detail"]
