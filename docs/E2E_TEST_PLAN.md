# E2E Test Plan (Safe Harness)

## Purpose
This plan defines a controlled end-to-end harness for the Resale AI System that validates workflows without risking real inventory. The harness is API-driven, DB-verified, and uses strict SKU allowlisting.

## Approved SKU Strategy
Only these SKUs are mutation-eligible in E2E:
- `BK-000005`
- `BK-000008`
- `BK-000009`

Any mutation attempt outside this allowlist is blocked by the central E2E guard.

## Test Item Role Mapping
- `BK-000005`: main happy path E2E item
- `BK-000008`: review/edit/photo-repair test item
- `BK-000009`: CSV/sold/reporting/revision test item

## Test Modes
- `mock`: default; no live marketplace mutation
- `sandbox`: allows real sandbox marketplace mutation for approved SKUs
- `live-gated`: allows production mutation only when `ALLOW_LIVE_E2E=true` and SKU is approved

## Backup and Restore Behavior
- Before DB mutation steps, create a timestamped backup of `data/app.db`.
- Snapshot approved SKU state before/after execution.
- Prefer reversible API updates with E2E markers.
- If full restoration is not available for a workflow, mark mutation explicitly and rely on DB backup.

## Workflows Covered
- Health and startup endpoints
- Approved SKU baseline capture
- Missing `missing_cloudinary_upload` investigation
- Item read/edit/cost mutation (approved SKUs only)
- Review queue flow for `BK-000008`
- Category intelligence reads/writes (approved SKUs)
- Photo/path validation and Cloudinary status checks
- Safe CSV generation path for approved SKUs only
- eBay connectivity diagnostics
- eBay push/publish/revision behavior by mode and gate
- Reports and settings read checks
- Capture status checks
- Sync/ended listings read checks
- Constrained intake processing for approved SKUs via `POST /api/items/process?skus=...&e2e_only=true`

## Skipped Workflows and Why
Skipped when unconstrained/global:
- `POST /api/ebay/publish/batch` (global publish)
- `POST /api/export/ebay-csv` and `POST /api/export/master-sheet` (global exports)
- `POST /api/ebay/sync-sold` (global sold sync)
- `POST /api/items/apply-stale-drops` (global stale drop mutation)
- `POST /api/sync/relist-all` (global relist)

These are marked `SKIP` unless safely constrained. Intake has a constrained path and is tested through it.

## External Integration Rules
- Ollama: optional; if unavailable, skip or run dry checks.
- Anthropic: optional; skipped by default.
- Premium intake vision provider state:
  - `local_ollama`: implemented (default)
  - `claude_vision`: planned, not implemented in intake
  - `openai_vision` / `chatgpt_vision`: placeholder only, not implemented
- Cloudinary: never log credentials; upload tests limited to approved SKUs and mode gating.
- eBay: read-only calls allowed; mutation requires mode + allowlist guard + live gate.

## eBay Mutation Safety Rules
- SKU must be allowlisted.
- `live-gated` requires `ALLOW_LIVE_E2E=true`.
- `production` plus no live gate means eBay mutations are skipped/mocked.
- `sandbox` mode can mutate only allowlisted SKUs in sandbox.

## Cloudinary Safety Rules
- No secret values printed.
- Investigate `missing_cloudinary_upload` with redacted diagnostics.
- In mock mode, avoid uploads by default.

## DB Safety Rules
- Backup before mutation.
- Never mutate non-approved SKUs.
- Avoid destructive operations.
- Prefer API mutation plus DB verification.
- Intake safety:
  - In `E2E_ROUTE_GUARD_ENABLED=true`, global intake without explicit `skus` is blocked.
  - Constrained intake only processes pending folders whose folder name matches requested approved SKUs.
  - Missing requested SKU folders are reported explicitly and do not cause broad fallback processing.

## Investigating `missing_cloudinary_upload`
Harness traces:
- Current DB fields (`review_reasons`, `missing_required_fields`, `notes`)
- Code references where reason is generated or consumed
- `PhotoUploader.is_configured()` and upload fallback behavior
- Whether `image_paths` are local file paths vs hosted URLs

## Risks and Mitigations
- Risk: global mutation routes touching real inventory.
  Mitigation: explicit skip + report safety gap.
- Risk: secret leakage.
  Mitigation: central redaction helpers for logs/report payloads.
- Risk: production marketplace mutation.
  Mitigation: explicit live gate + allowlist + mode checks.

## How to Run
```powershell
uv run python scripts/run_e2e.py --mode mock
uv run python scripts/run_e2e.py --mode sandbox
uv run python scripts/run_e2e.py --mode live-gated
```

Optional:
```powershell
uv run python scripts/run_e2e.py --mode mock --sku BK-000005 --base-url http://127.0.0.1:8000
```

## How to Interpret the Report
- `PASS`: step executed and validated
- `FAIL`: step executed but failed expectations
- `SKIP`: intentionally not executed for safety or missing preconditions

## Expected Report Path
- `data/e2e_reports/e2e_report_<timestamp>.md`

