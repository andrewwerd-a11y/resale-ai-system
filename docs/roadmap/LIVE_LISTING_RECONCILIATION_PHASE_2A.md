# Live Listing Reconciliation Phase 2A

## Purpose

Phase 2A defines the next major system after the BK-000008 stale-offer recovery: read-only live listing reconciliation for already-uploaded listings. The goal is to map local database state against eBay state before any future mutation workflow is designed or enabled.

## Why This Matters

- Some listings were uploaded before the latest route guards, repair queue gates, and stale-offer remediation safety checks existed.
- Local database state and eBay state may differ for listing IDs, offer IDs, inventory items, publish status, sold/ended status, and repair blockers.
- The system needs a read-only state map before future mutation. Without that map, retries, sync actions, and repair workflows risk acting on stale or incomplete assumptions.

## Read-Only First Principle

Phase 2A must not mutate local listing state or eBay state by default. The first implementation should only read local records, read eBay state when explicitly allowed, classify mismatches, and produce operator-facing reports.

Every reconciliation output should include `no_mutation_performed=true`. Any action that could change local records or eBay state must start as preview-only, one SKU at a time, with explicit approval and audit logging in a later phase.

## Classification Buckets

Each SKU should land in one primary bucket, with secondary flags where useful:

- `active_listed`: local and eBay state both indicate an active listing.
- `unpublished_offer_exists`: eBay has an existing offer that is not published.
- `inventory_item_exists_no_offer`: eBay inventory item exists, but no offer is found.
- `local_offer_id_no_listing_id`: local state has an offer ID but no listing ID.
- `local_listed_ebay_not_active`: local state is listed, but eBay does not report an active listing.
- `ebay_active_missing_locally`: eBay reports an active listing that is missing or incomplete locally.
- `repair_blocked`: latest repair queue state blocks retry or publish decisions.
- `sold_ended`: listing appears sold, ended, or otherwise no longer active.
- `unknown_auth_unavailable`: reconciliation cannot complete because auth, eBay reads, or required local state are unavailable.

## Proposed API And Report Outputs

Initial API/report responses should be explicit, stable, and easy to diff:

- Per-SKU reconciliation result.
- Source-of-truth markers for local DB, eBay offer, eBay inventory item, eBay listing, and repair queue state.
- Mismatch reason, including the exact local and live fields that differ.
- Suggested next action, limited to preview or read-only follow-up steps.
- `no_mutation_performed=true`.
- `live_readonly_requested`, `live_readonly_performed`, and `live_readonly_errors` fields where live reads are attempted.
- A batch report mode that reads and classifies many SKUs, but never mutates them.

## Preview-Only Operator Actions

These actions should exist as preview-only recommendations before any mutation path is added:

- Sync listing ID from eBay to local state.
- Mark active locally when eBay is active and local state is stale.
- Mark ended or sold candidate when eBay is not active.
- Generate a repair plan for mismatched or unsafe states.
- Refresh an unpublished offer.
- Revise an active listing.

Preview output should explain what would change, why it is suggested, which source supports it, and which approval gate would be required later.

## Future UI

The UI should make reconciliation useful for daily operations without encouraging bulk mutation:

- Reconciliation dashboard with per-SKU status, bucket, confidence, and suggested next action.
- Repair queue UI that links reconciliation findings to open repair plans.
- Retry/recheck mechanism for one SKU at a time.
- Listing state filters for active, unpublished, repair-blocked, missing-local, ended/sold, and unknown/auth-unavailable states.

## Safety Gates

Phase 2A should preserve the same safety posture as the BK-000008 remediation ladder:

- No batch mutation.
- No publish from reconciliation.
- One-SKU approval first for any later mutation workflow.
- Audit logs for previews, approvals, and any eventual state changes.
- Route guards for operator-triggered actions.
- Explicit live-read-only enablement for eBay reads.
- Failure-closed handling when auth or live reads are unavailable.

## Relationship To Future Modules

Live listing reconciliation should become the shared state foundation for:

- Repair queue UI.
- Dedicated category retry mechanism.
- AI intake routing with ChatGPT/Claude.
- Contact-sheet analysis experiments.
- Inventory research and sourcing intelligence.
- Mobile/offline field sourcing mode.
- Source/opportunity scoring module.
- Worker-assisted acquisition tools.

The reconciliation layer should not own those workflows. It should provide the read-only state map, mismatch explanations, and safe next-action previews that those modules can consume.
