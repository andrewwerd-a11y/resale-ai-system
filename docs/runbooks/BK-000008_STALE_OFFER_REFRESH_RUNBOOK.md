# BK-000008 Stale Offer Refresh Runbook

## Purpose

This runbook describes the future operator sequence for refreshing the existing unpublished eBay offer for SKU `BK-000008` after the eBay `25021 invalid_category_condition` publish rejection. It is a controlled stale-offer refresh only. The refresh is intended to update the existing inventory item and existing unpublished offer so a later, separately approved publish decision can be evaluated.

## Current Known SKU State

- SKU: `BK-000008`
- Title: `Rand McNally Atlas World Hardcover Geography Maps Cities Population Stats`
- Local status: `export_ready`
- Category ID: `14056`
- Category name: `Atlases`
- Condition ID: `3000`
- Inventory condition enum: `USED_EXCELLENT`
- Offer ID: `156719395011`
- Listing ID: empty
- Planned action: `publish_existing_offer`
- Existing offer status: `UNPUBLISHED`
- eBay inventory item exists.
- eBay inventory condition is `USED_GOOD`.
- Live eBay condition policy for category `14056` allows condition ID `3000`.
- Repair plan ID: `178a3bc0-7679-42b4-b1f4-898d3e818d2b`
- Latest publish attempt ID: `1c909979-22f6-4f2f-96ab-30083986f0f3`

## This Runbook Does Not Authorize

- No publish.
- No batch publish.
- No category change.
- No condition change.
- No repair queue clear or resolution.
- No offer create, recreate, delete, withdraw, or active listing revise.

## Required Pre-Checks

- Confirm the repo is clean with `git status -sb`.
- Regenerate the latest approval packet locally.
- Copy the fresh payload hash from the local approval packet.
- Confirm the offer status is still `UNPUBLISHED`.
- Confirm the listing ID is still empty.
- Confirm category ID is still `14056`.
- Confirm condition remains `3000` / `USED_EXCELLENT`.
- Confirm the repair queue is still blocking.
- Confirm `retry_allowed` is still `false`.
- Confirm the payload hash matches the current live-read-only approval preview.

## Required Env Flags

Set only the flags needed for this one approved refresh:

```powershell
$env:ALLOW_LIVE_E2E = "true"
$env:ALLOW_EBAY_STALE_OFFER_REFRESH = "true"
```

Do not enable publish or batch publish flags. Do not enable any route or script that would publish, batch publish, create offers, recreate offers, delete offers, withdraw offers, revise active listings, change category, change condition, or clear the repair queue.

## Request Body Template

Replace `<FRESH_PAYLOAD_HASH_FROM_LOCAL_PACKET>` with the hash copied from the latest locally regenerated approval packet.

```json
{
  "sku": "BK-000008",
  "remediation_type": "refresh_existing_unpublished_offer",
  "repair_plan_id": "178a3bc0-7679-42b4-b1f4-898d3e818d2b",
  "latest_publish_attempt_id": "1c909979-22f6-4f2f-96ab-30083986f0f3",
  "offer_id": "156719395011",
  "confirm_offer_status": "UNPUBLISHED",
  "confirm_listing_id_empty": true,
  "confirm_category_id": "14056",
  "confirm_condition_id": "3000",
  "confirm_inventory_condition_enum": "USED_EXCELLENT",
  "confirm_publish_after_remediation": false,
  "operator_approved": true,
  "typed_confirmation": "REFRESH UNPUBLISHED OFFER ONLY",
  "approved_payload_hash": "<FRESH_PAYLOAD_HASH_FROM_LOCAL_PACKET>"
}
```

## Endpoint

```text
POST /api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh
```

## Expected Successful Response

- `execution_status=refresh_completed`
- `no_publish_performed=true`
- `repair_queue_cleared=false`
- `item_status_after=export_ready`
- `calls_performed=["put_inventory_item", "put_offer"]`

## Required Post-Refresh Checks

- Rerun the approval preview with `allow_live_readonly=true`.
- Rerun publish diagnostics.
- Confirm the offer is still `UNPUBLISHED`.
- Confirm no listing ID appeared.
- Confirm the repair queue was not auto-cleared.
- Confirm `retry_allowed` remains `false`.
- Confirm local category and condition did not change.

## Stop Conditions

Stop and do not retry automatically if any of these occur:

- Inventory PUT fails.
- Offer PUT fails.
- Payload hash mismatch.
- Offer is not `UNPUBLISHED`.
- Listing ID appears.
- Category mismatch.
- Condition mismatch.
- Repair queue is no longer blocking.
- `retry_allowed` is not `false`.

## Next Step After Successful Refresh

A successful refresh does not authorize publish. The next step is a separate future publish decision with its own preview, diagnostics, approval, and safety gates.
