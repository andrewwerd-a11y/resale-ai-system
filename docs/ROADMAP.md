# Roadmap

This document tracks what's built, what's actively being worked on, and what's planned. It's organized by phase. Each phase has a status, a scope, and (for in-progress phases) a checklist.

The phase numbering reflects how the system has actually evolved, which doesn't perfectly match the README's table — the README treats Phase 4 as "done" because the *first iteration* of Claude enrichment shipped, but several Phase 4 items are still open. This roadmap is the authoritative status.

---

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Shipped and stable |
| 🟡 | Partial / first iteration done, more work planned |
| 🔵 | Actively in progress |
| ⚪ | Planned, not started |
| ⏸ | Parked / deferred |
| 🐛 | Known issue, scheduled fix |

---

## Phase 1 — Intake foundation ✅

The plumbing: scan folders, reserve SKUs, store records.

- ✅ Folder scanner (`packages/intake/folder_scanner.py`) detects SKU-named folders and validates against prefix registry
- ✅ Image normalizer renames to `NN.jpg`, resizes to 1600px, backs up originals
- ✅ SKU registry with atomic reservation per prefix (`sku_registry` table)
- ✅ SQLite database setup with idempotent `init_db` and `migrate_add_columns`
- ✅ Backfill script (`scripts/backfill.py`) for migrating existing inventory without renaming SKUs
- ✅ Backup/restore (`scripts/backup_db.py`, `scripts/restore_db.py`)

---

## Phase 2 — Vision extraction ✅

Local AI vision pipeline. No internet required after Ollama install.

- ✅ Ollama provider (`packages/vision/ollama_provider.py`) with retry + JSON-mode + brace-balancing fallback parser
- ✅ Provider abstraction (`VisionProvider` ABC) for future provider swaps
- ✅ Versioned extraction prompt (`extraction_v1.txt`) with category-specific field schemas
- ✅ Response parser with type coercion, confidence clamping, review trigger detection
- ✅ Configurable thresholds in `config/rules.json` and `.env`
- ✅ Dry-run mode that bypasses Ollama for testing

---

## Phase 3 — Triage and review ✅

Decision-making layer + operator UI.

- ✅ Triage router with single/lot/review/reject decision tree
- ✅ Review queue UI at `/review-queue`
- ✅ Inventory page with filtering (`/inventory?status=…`)
- ✅ Bulk approve/review/reject endpoints
- ✅ Lot creation + dissolution (`LotBuilder`)
- ✅ Manual override semantics (sticky once set)
- ✅ Master inventory spreadsheet export (openpyxl)

---

## Phase 3.5 — eBay CSV path ✅

Bulk-upload path for sellers not using the API.

- ✅ `EbayCSVWriter` with per-category column filtering (clothing-only fields stripped from non-clothing)
- ✅ Configurable column map and defaults in `config/ebay_fields.json`
- ✅ Title cleaner strips known AI-added suffixes
- ✅ Photo URL columns 1-6 with local-file fallback

---

## Phase 3.6 — Category Intelligence ✅

Phase 3.6 was added when we discovered that eBay's required item specifics vary by category and the static `CATEGORY_MAP` was producing rejected listings. The Taxonomy API gives us authoritative leaf category IDs and their required/recommended aspects.

- ✅ `CategoryIntelligence.suggest_category` — eBay Suggestions API (title-based, always leaf IDs)
- ✅ `CategoryIntelligence.get_template` — eBay Item Aspects API with fallback chain
- ✅ `CategoryIntelligence.validate_item_specifics` — flags missing required + recommended fields, invalid values
- ✅ `CategorySpreadsheet` persistent cache at `data/category_intelligence/`
- ✅ App-token auth (client_credentials) for read-only taxonomy access
- ✅ Per-item endpoint (`POST /api/items/{sku}/category-intelligence`)
- ✅ Bulk script (`scripts/run_category_intelligence.py`) with `--reset`, `--limit`, `--sku`
- ✅ Review reason `missing_required_specifics` flows into existing review queue
- ✅ Reports endpoint + CSV export
- ✅ Category templates included in enrichment system prompt

---

## Phase 3.7 — eBay API publishing ✅

Direct publish replacing the CSV upload path.

- ✅ OAuth 2.0 authorization code flow with auto-refresh
- ✅ Token persistence at `data/ebay_tokens.json` (gitignored)
- ✅ `EbayInventoryClient.publish_item` — full 3-step flow (PUT inventory_item → POST offer → POST publish)
- ✅ Cloudinary photo upload with local-path fallback
- ✅ Seller policy fetching with caching
- ✅ Condition map (eBay condition_id → enum)
- ✅ Country code derivation from marketplace_id
- ✅ Item specifics builder merging stored values + standard fields + template defaults
- ✅ Single-item publish (`POST /api/ebay/publish/{sku}`) and batch publish
- ✅ Listing update (`PATCH /api/ebay/listing/{sku}`) for title/description/price/aspects
- ✅ Sold sync (`POST /api/ebay/sync-sold`) via Fulfillment API
- ✅ Browse API price researcher (`packages/pricing/price_researcher.py`)

**Production milestone:** 19 of 23 items pushed live to eBay production via this flow.

---

## Phase 4 — Advanced enrichment 🟡 / 🔵

The current state: Claude API enrichment exists in **text-only** mode. The next iteration adds vision and improves price comping.

### Already shipped under "Phase 4" 🟡

- ✅ `ItemEnricher` class with Claude Sonnet integration
- ✅ Cost tracking (~$0.02/item)
- ✅ Price layering (Browse API average overrides Claude's `list_price`)
- ✅ Category template injection into system prompt
- ✅ Manual-override protection
- ✅ `scripts/enrich_all.py` with skip-already-enriched logic
- ✅ Toggle via `ENRICHMENT_ENABLED` env var

### In progress / planned for next Phase 4 iteration 🔵

- ⚪ **Claude Sonnet vision enrichment** — replace minicpm-v with Sonnet vision for cases where local model confidence is low. Requires:
  - Image upload to Anthropic API in `ItemEnricher.enrich`
  - New `ENRICHMENT_MODE` toggle: `text` / `vision` / `hybrid`
  - Cost reporting updates (vision is more expensive)
  - Decision logic: when to escalate from local Ollama to Claude vision
- ⚪ **eBay Browse API for sold comps** — current `PriceResearcher` queries *active* listings. Need to query the sold/completed listings endpoint for true price discovery. Note: the standard Browse API doesn't expose sold listings — this requires either the Marketplace Insights API (limited access) or scraping. Decision pending.
- ⚪ **Cover photo auto-selection** — currently photos are uploaded in folder order (`01.jpg` becomes the cover). Vision-based selection of the most representative photo would improve listing quality. Likely uses Claude vision with a "rank these photos for eBay listing" prompt.
- ⚪ **UI cost estimation** — show estimated enrichment cost before running on a batch. Read pricing from `model_profiles.json` (which is currently informational only).

---

## Phase 5 — Operations and resilience 🟡

Most pieces shipped, several gaps remaining.

### Shipped ✅

- ✅ Sourcing batch tracking (`/api/sourcing/*`) with auto-cost-per-item
- ✅ Stale listing detection (`StaleChecker`)
- ✅ Auto-relister (`AutoRelister`)
- ✅ Cross-platform sync wrapper (logs only — see Phase 7 for actual API integrations)
- ✅ Notifier (SMTP) — sale, stale, review queue notifications
- ✅ Audit log class (`AuditLog`) — JSONL writes
- ✅ Windows Task Scheduler installer (`scripts/install_service.py`)

### Open issues 🐛

- 🐛 **`days_listed` not maintained.** No background job recomputes this after `date_listed` is set. The stale-checker depends on it. Fix: cron-able endpoint or worker pass that updates `days_listed = (now - date_listed).days` for all listed items.
- 🐛 **`AutoRelister._is_listing_active` always returns True.** Because `EbayInventoryClient.get_listing_status` doesn't exist yet. Fix: implement the method using `GET /sell/inventory/v1/inventory_item/{sku}/getListingStatus` (or equivalent).
- 🐛 **`AuditLog` is built but rarely called.** Wire calls into:
  - Worker (`item_created`, `item_analyzed`, `item_triaged`, `worker_started`, `worker_finished`)
  - Review routes (`item_approved`, `item_rejected`, `manual_override`)
  - Inventory client (`item_exported`)
  - Sold sync (`item_sold`)
- 🐛 **`CategorySpreadsheet.update_field_stats` not called on publish.** Only invoked from `run_category_intelligence.py`. Should also fire after `publish_item` succeeds, and after `sync-sold`.
- 🐛 **Seller policies pick first-only.** If multiple fulfillment/payment/return policies exist, there's no way to choose. Fix: settings UI for selecting active policy IDs, persisted in `.env` or a new `config/ebay_policies.json`.
- 🐛 **Title cleaner is reactive.** New AI-added suffixes break titles until manually added to the strip list. Fix: stronger prompt constraints + a generic regex for `" - .+ eBay Listing.*"` patterns.

---

## Phase 6 — Test suite & hardening 🟡

### Shipped ✅

- ✅ 12 test modules (8 unit + 4 integration)
- ✅ In-memory SQLite for tests (production DB never touched)
- ✅ Fixtures for mock eBay + mock extraction
- ✅ Coverage reporting via `make test` (with `pytest-cov`)
- ✅ Backup/restore scripts

### Open ⚪

- ⚪ **HTTP route tests.** No FastAPI `TestClient` coverage. Add a `tests/api/` module that exercises every route with a transactional in-memory DB.
- ⚪ **OAuth flow integration test.** Mock the eBay token endpoint and verify the full callback → save → refresh cycle.
- ⚪ **Property-based tests for triage router.** Hypothesis-style randomized item generation to find edge cases in the decision tree.
- ⚪ **Snapshot tests for prompt builder.** Lock the exact prompt sent to Ollama so prompt-engineering changes are visible in diffs.
- ⚪ **Migration safety tests.** Verify `migrate_add_columns` is genuinely idempotent across multiple runs and across pre-/post-Phase-3.6 schemas.

---

## Phase 7 — Hardware and capture studio ⚪

Vision: a "live capture studio" workflow where photos auto-flow into the system as they're taken.

- ⚪ Camera trigger via gphoto2 (Linux/Mac) or DigiCamControl (Windows). The `CameraController` stub exists.
- ⚪ Live-view positioning hints (auto-detect when subject is in frame)
- ⚪ Quality checker integration (`QualityChecker` already exists; runs on each captured photo and rejects blurry shots before they reach intake)
- ⚪ Label printer integration (`LabelPrinter` stub exists; brother_ql or dymoprint)
- ⚪ `IntakeWatcher` triggers analysis automatically when a folder reaches the configured photo count
- ⚪ Barcode scanner input for SKU confirmation

The watchdog file watcher already works — it just doesn't trigger downstream actions yet.

---

## Phase 8 — Multi-platform expansion ⚪

Currently `config/platforms.json` lists Poshmark, Mercari, Depop, Facebook Marketplace as inactive. None have API integrations.

- ⚪ Mercari API integration (publish + sold sync)
- ⚪ Poshmark — no public API; would require browser automation (Playwright) or third-party service
- ⚪ Depop — same as Poshmark
- ⚪ Facebook Marketplace — same; browser automation only
- ⚪ Cross-platform takedown actually works (currently `CrossPlatformSync.end_other_platform_listings` only logs warnings)
- ⚪ Per-platform pricing rules (e.g. Poshmark adds 20% fee, price accordingly)
- ⚪ Per-platform photo requirements (square crop, etc.)

---

## Phase 9 — SaaS productization ⏸

Long-term aspiration: a freemium/premium product where other resellers run their own instances or use a hosted version.

- ⏸ Multi-tenant data model (currently single-tenant, single-user)
- ⏸ Auth/authorization layer
- ⏸ Hosted Ollama or cloud vision provider option
- ⏸ Stripe billing
- ⏸ Onboarding flow for new users
- ⏸ Public landing page

This is parked until the personal use case is rock-solid and the toolchain stabilizes.

---

## Cross-cutting tech debt

These don't fit cleanly into a phase but should be addressed.

### Schema management

- ⚪ **Adopt Alembic properly or remove it.** Currently `alembic.ini` and `env.py` exist but no migration files are checked in. Schema evolution happens via `migrate_add_columns()` raw `ALTER TABLE` calls. Either:
  - Generate Alembic migrations going forward (run `alembic revision --autogenerate`) and remove `migrate_add_columns`, OR
  - Delete the alembic artifacts and document `migrate_add_columns` as the official schema mechanism

### Logging configuration

- ⚪ **Log rotation.** `app.log` grows forever currently. Add `RotatingFileHandler` with `max_bytes` and `backup_count` (already configured in `settings.yaml` but not wired in `main.py:lifespan`).

### Concurrency

- ⏸ **Multi-writer support.** Currently single-writer assumption. Future Phase 7 capture studio may need to revisit. Solutions: SQLite WAL mode (already implicitly enabled by some access patterns), or migrate to Postgres for the database tier.

### Code quality

- ⚪ **Stricter mypy config.** Currently `strict = false`. Tighten gradually as type annotations stabilize.
- ⚪ **Pre-commit hooks.** Ruff + mypy run via `make lint` but aren't enforced on commit.
- ⚪ **Reduce inline HTML in `ui.py`.** 2300-line file with all server-rendered pages. Either extract Jinja2 templates or migrate the UI to a separate frontend (HTMX would be a good fit given the local-first constraint).

### API surface

- ⚪ **Rate limiting on external API calls.** Currently no client-side rate limiting on eBay or Anthropic. Heavy batches could hit limits.
- ⚪ **Retry/backoff on eBay calls.** Tenacity is used for Ollama but not for eBay. A 429 response would propagate as a hard failure.
- ⚪ **Idempotency keys on publish.** If a network blip causes an unclear state mid-publish, re-running could create duplicate offers. eBay's offer creation is keyed on SKU which provides some protection, but not full idempotency.

---

## Out of scope (deliberate non-goals)

These have been considered and rejected, at least for the foreseeable future:

- **Real-time synchronization across devices.** Single-machine architecture is intentional. If you need multi-device, run a syncthing/dropbox layer underneath.
- **Mobile app.** The browser UI works on mobile (basically). A native app is a Phase 9 concern.
- **Inventory forecasting / demand prediction.** Out of scope; this is a listing tool, not a market intelligence platform.
- **Automated repricing competitor analysis.** Browse API is used for one-shot pricing. We don't track competitor listings over time.
- **Customer messaging.** eBay Messages are out of scope; handled in eBay Seller Hub directly.
- **Shipping label generation.** Pirate Ship / Easyship / eBay-integrated labels are out of scope; handled outside the system.
