# Architecture

This document describes how the Resale AI System is built — its design principles, structural decisions, data flow, and key invariants. It is intentionally separate from `README.md` (which is user-facing setup) and `ROADMAP.md` (which tracks status and future work).

If you're reading code and asking "*why is it like this?*" or "*where does X happen?*" — this is the doc.

---

## Design principles

These are non-negotiable and inform almost every other decision in the codebase.

### 1. Local-first

The system runs entirely on a single machine. SQLite, local file system, local Ollama. No cloud database, no message queue, no Redis, no Docker required. Internet is needed only for:
- Initial Ollama model pull
- Cloudinary photo uploads (optional — falls back to local paths)
- eBay API calls (publishing, sold sync, taxonomy lookup)
- Claude API enrichment (optional)

This shapes the stack: SQLModel over Postgres, FastAPI background tasks over Celery, file-based config over a settings service.

### 2. Prefix is authoritative for category

The two-letter SKU prefix (`BK`, `CL`, `CO`, `SH`, `TO`) determines an item's category — not AI classification, not the eBay Suggestions API. Prefix is a deliberate human input via folder naming. AI extraction fills in *fields within* a category but never overrides which category an item belongs to.

This means: to add a new category, edit `config/sku_prefixes.json` + `config/categories.json`. No code changes.

### 3. Manual override wins, always

Once a human edits a field through the review UI (or any `PATCH /api/items/{sku}` call), `manual_override=True` is set. From that point forward, the AI pipeline will not write to a defined set of "manual-protected" fields (`cost`, `list_price`, `minimum_price`, `notes`, `storage_location`). Reprocessing the item — even with better photos or a smarter model — leaves human edits intact.

`cost_manual` and `enrichment_done` work the same way: once True, never reset.

### 4. Idempotent upserts

Every write to the items table goes through `ItemRepository.upsert()`. Re-running the worker on the same SKU updates fields in place. Re-running the backfill on the same source folder is safe. There are no "partial write then crash" recovery paths because there are no two-phase commits — the upsert is the unit of work.

### 5. `Result[T]` everywhere

Cross-module calls return `Result[T]` (`packages/core/src/result.py`) instead of raising. This forces callers to acknowledge failure paths explicitly, and lets us aggregate errors without scattering try/except. Exceptions are reserved for genuinely exceptional situations (network errors, programming bugs).

```python
result = provider.analyze(images, prompt)
if not result.ok:
    return Result.failure(f"vision_failed: {result.error}")
return process(result.value)
```

### 6. Configuration over code

Behaviour-shaping decisions live in `config/*.json` and `config/settings.yaml`. Triage thresholds, review trigger keywords, luxury brand lists, eBay column mappings, model profiles — all editable without a code change. The Python code reads them through `@lru_cache`'d accessors in `packages/core/src/config.py`.

### 7. Two layers of "missing" detection

A field can be missing in two ways: missing from our schema (i.e. the AI didn't extract it), or missing from eBay's category requirements (i.e. eBay needs an aspect we don't have). The first is caught by the response parser against `categories.json`. The second is caught by the Category Intelligence layer against eBay's Taxonomy API. Both paths flow into the same `review_reasons` field but are driven by independent rules.

---

## System topology

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (operator UI)                              │
│            http://localhost:8000  → server-rendered HTML                   │
└─────────────────────────────────┬──────────────────────────────────────────┘
                                  │ fetch(/api/...)
┌─────────────────────────────────▼──────────────────────────────────────────┐
│                              FastAPI                                       │
│         apps/api/src/main.py + routes/  (single uvicorn process)           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ /api/items, /api/review, /api/lots, /api/export, /api/ebay,        │   │
│  │ /api/sourcing, /api/sync, /api/settings, /api/reports, /api/capture │   │
│  │ /api/health                                                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└──────┬───────────────────────────────────────────────────────────┬─────────┘
       │                                                           │
       │ uses                                                      │ launches
       ▼                                                           ▼
┌────────────────────────────────────┐         ┌─────────────────────────────────┐
│      packages/ (the engine)        │         │   apps/worker/src/main.py       │
│  intake → vision → triage → eBay   │         │   (single-shot batch process)   │
│  Result[T] is the lingua franca    │         │   spawned as subprocess via API │
└────────────────────────┬───────────┘         └────────────────┬────────────────┘
                         │                                       │
                         │             both write to             │
                         ▼                                       ▼
                  ┌──────────────────────────────────────────────────┐
                  │              SQLite — data/app.db                │
                  │  items, review_cases, sale_records,              │
                  │  sku_registry, sourcing_batches, batches         │
                  └────────────────────┬─────────────────────────────┘
                                       │
                                       │ derived artifacts (never source of truth)
                                       ▼
                  ┌──────────────────────────────────────────────────┐
                  │  data/exports/                                   │
                  │  ├─ ebay_upload_*.csv (Seller Hub bulk)          │
                  │  ├─ master_inventory_*.xlsx                      │
                  │  ├─ backup_*.json                                │
                  │  └─ sales_*.csv                                  │
                  │  data/category_intelligence/                     │
                  │  ├─ {cat_id}_template.json (per-category cache)  │
                  │  └─ category_summary.csv                         │
                  │  data/logs/{app.log, audit.jsonl}                │
                  │  data/ebay_tokens.json (OAuth state)             │
                  └──────────────────────────────────────────────────┘

External services (called from packages/):
┌──────────────┐  vision      ┌─────────────────┐
│   Ollama     │◄─────────────┤ packages/vision │
│  (local)     │   :11434     └─────────────────┘
└──────────────┘

┌──────────────┐  enrichment  ┌────────────────────┐
│ Anthropic    │◄─────────────┤ packages/enrichment │
└──────────────┘              └────────────────────┘

┌──────────────┐  photos      ┌─────────────────────┐
│ Cloudinary   │◄─────────────┤ packages/ebay       │
└──────────────┘              │  photo_uploader     │

┌──────────────┐  taxonomy    │  category_intel.    │
│ eBay APIs    │◄─────────────┤  inventory_client   │
│ - Taxonomy   │  publish     │  sold_sync          │
│ - Inventory  │  orders      │  auth (OAuth 2.0)   │
│ - Account    │  policies    └─────────────────────┘
│ - Browse     │  comps       ┌─────────────────────┐
│ - Fulfillment│              │ packages/pricing    │
└──────────────┘              │  price_researcher   │
                              └─────────────────────┘
```

There is no separate API server, no worker queue, no separate processes for vision vs triage vs publishing. Everything is one Python process (the FastAPI server) plus an occasional subprocess (the worker) that writes to the same SQLite file.

---

## Layered architecture

The codebase follows a loose hexagonal/clean-architecture split:

```
apps/                  ← I/O layer: HTTP, CLI scripts, background worker
  api/src/             ← FastAPI routes (thin — just call into packages/)
  worker/src/          ← Batch processing entrypoint

packages/              ← Business logic (no I/O at this layer except where
                         explicitly an integration adapter)
  domain/              ← Pure entities (Item, Batch, ReviewCase, SKU)
  core/                ← Cross-cutting: config, constants, Result[T], types
  data/                ← Persistence: SQLModel tables + repositories
  intake/              ← Folder scanning, image normalization
  sku/                 ← SKU registry (atomic reservation)
  vision/              ← Vision provider abstraction + Ollama implementation
  classification/      ← Category derivation from prefix
  triage/              ← Single/lot/review/reject decision tree
  pricing/             ← Rule-based price estimator + Browse API researcher
  enrichment/          ← Claude API enrichment
  ebay/                ← All eBay integration (auth, inventory, taxonomy, sold sync, CSV)
  sync/                ← Cross-platform takedowns, relisting, stale checks
  capture/             ← Camera/printer hardware (mostly stubs) + watchdog file watcher
  spreadsheet/         ← openpyxl-based master sheet writer
  notifications/       ← SMTP email stubs
  logging/             ← JSONL audit log

config/                ← Behavior dials (categories, rules, prefixes, fields, models, platforms)
tests/                 ← Unit + integration (in-memory SQLite, never touches data/app.db)
scripts/               ← One-off CLI tools (analyze, enrich, backfill, backup, etc.)
```

### Direction of dependency

`apps/` depends on `packages/`. `packages/` does not depend on `apps/`. Within `packages/`, `domain/` and `core/` are leaves — they don't import from any other package. Other packages may import from each other when there's a clear orchestration relationship (e.g. `enrichment/` imports `pricing/` to layer Browse API research over Claude's pricing).

### What lives where

If you're adding code:
- **A new HTTP endpoint** → `apps/api/src/routes/` (and probably no logic; delegate to a package)
- **A new business rule (e.g. a triage trigger)** → `config/rules.json` first; `packages/triage/` if it requires code
- **A new external integration** → new file in `packages/<area>/src/` with a class that returns `Result[T]`
- **A new vision provider** → new file in `packages/vision/src/` implementing `VisionProvider` ABC
- **A new platform (Mercari, Poshmark)** → new file in `packages/<area>/src/` paralleling `ebay/`, plus an entry in `config/platforms.json`

---

## The pipeline, end to end

A new item travels through these stages. Each stage is a separate module; transitions are explicit.

```
intake/pending/CL-000099/01.jpg 02.jpg 03.jpg
                  │
                  ▼  packages/intake/folder_scanner.py
                  FolderScanner.scan_pending() → FolderManifest(detected_sku, image_paths, ...)
                  │
                  ▼  packages/intake/image_normalizer.py
                  ImageNormalizer.normalize_folder() → renames to NN.jpg, resizes to 1600px
                  Originals backed up to _original_backup/ if rename needed
                  │
                  ▼  packages/sku/registry.py (only for new items without prefix-named folders)
                  SKURegistry.reserve(prefix) → atomic increment via SKURepository
                  │
                  ▼  packages/classification/category_mapper.py
                  CategoryMapper.from_prefix("CL") → {category_key, label, ebay_category_id}
                  │
                  ▼  packages/vision/prompt_builder.py
                  build_extraction_prompt(category_key) → loads extraction_v1.txt + substitutes
                  │
                  ▼  packages/vision/ollama_provider.py
                  OllamaProvider.analyze(images[:3], prompt) → POST /api/chat with format=json
                  Returns Result[JsonDict] with all extracted fields + confidence_score
                  │
                  ▼  packages/vision/response_parser.py
                  ResponseParser.parse(extracted, category_key) → coerces types,
                  validates required fields, detects review triggers (low confidence,
                  high value, signed/first edition/luxury brand text scan)
                  │
                  ▼  Build Item entity from extracted fields
                  │
                  ▼  packages/pricing/estimator.py
                  PriceEstimator.apply(item) → derives list/min from estimated, enforces
                  minimum_profit_margin, recomputes net_profit if sold data present
                  │
                  ▼  packages/triage/router.py
                  TriageRouter.route(item) → SINGLE | LOT | REVIEW | REJECT
                  │
                  ▼  packages/data/repositories/item_repo.py
                  ItemRepository.upsert(item) → INSERT or UPDATE in items table
                  Manual-override-protected fields are preserved on update
                  │
                  ▼  If needs_review, create ReviewRecord
                  │
                  ▼  Item now visible in /review-queue or /inventory pages

  ─── operator action ───────────────────────────────────────────────────────

                  ▼  Review UI: approve / edit / reject
                  POST /api/review/{sku}/approve → status = export_ready
                  PATCH /api/review/{sku}/edit → manual_override=True + status = export_ready

                  ▼  [optional] enrichment pass
                  scripts/enrich_all.py → ItemEnricher.enrich(item)
                  Sends item JSON to Claude (text only, not images currently)
                  Layers PriceResearcher.research(item) on top → Browse API avg overrides Claude's list_price

                  ▼  [optional] category intelligence pass
                  scripts/run_category_intelligence.py OR
                  POST /api/items/{sku}/category-intelligence
                  Suggestions API → leaf category_id
                  Item Aspects API → required/recommended fields + allowed values
                  CategorySpreadsheet caches template at data/category_intelligence/

                  ▼  publish
                  POST /api/ebay/publish/{sku}
                  PhotoUploader → Cloudinary URLs (or local paths if not configured)
                  PUT /sell/inventory/v1/inventory_item/{sku}
                  POST /sell/inventory/v1/offer
                  POST /sell/inventory/v1/offer/{offerId}/publish
                  Item.status = listed, listing_id + listing_url stored

  ─── post-listing automation ────────────────────────────────────────────────

                  ▼  /api/ebay/sync-sold (cron-able)
                  SoldSync.reconcile() → /sell/fulfillment/v1/order
                  Match by SKU → status = sold, fees + net_profit computed

                  ▼  /api/items/apply-stale-drops (cron-able)
                  StaleChecker → drops list_price by 10% on listings ≥60 days old
                  Floored at minimum_price

                  ▼  cross-platform takedowns
                  CrossPlatformSync.mark_sold() → ends listings on other platforms
                  (Currently only logs warnings — no other platform APIs implemented)
```

---

## Data model

### Tables

Six tables, all defined in `packages/data/src/models/`. SQLite single file at `data/app.db`. Schema evolution happens through `migrate_add_columns()` in `packages/data/src/db/sqlite.py` (raw `ALTER TABLE ADD COLUMN` calls, idempotent — Alembic config exists but no migration files are checked in yet).

| Table | Model | PK | Purpose |
|---|---|---|---|
| `items` | `ItemRecord` | `internal_id` (uuid), `sku` unique | Central item record. ~70 columns. |
| `review_cases` | `ReviewRecord` | `review_case_id` (uuid) | One row per review event with trigger reasons + confidence. |
| `sale_records` | `SaleRecord` | `id` (uuid) | One row per sale with computed gross/net profit + margins. |
| `sku_registry` | `SKURecord` | `prefix` | Tracks last reserved number per prefix for atomic SKU generation. |
| `sourcing_batches` | `SourcingBatch` | `batch_id` (uuid) | Bulk purchase events. Drives cost-per-item assignment. |
| `batches` | `BatchRecord` | `batch_id` (uuid) | One row per worker run. Tracks processed/failed counts. |

### Item lifecycle

Status transitions are not enforced by code — any handler can set any status. The convention is:

```
pending_intake
   │   (worker / analyze runs vision pipeline)
   ▼
analyzed ──┬─→ needs_review ──┬─→ approved ──→ export_ready ──┬─→ exported (CSV path)
           │                  │                                │
           │                  │                                └─→ listed ──→ sold ──→ archived
           │                  │
           │                  └─→ rejected
           │
           └─→ rejected (auto-reject from triage)

Lot wrappers:
   approved item ──→ LotBuilder.create_lot → wrapper has item_mode="lot",
                                              members get status="lot_member"
```

Manual override layered on top of any state — sets `manual_override=True` and protects `cost`, `list_price`, `minimum_price`, `notes`, `storage_location` from future AI writes.

### Field categories on `ItemRecord`

The 70 columns group naturally into:

- **Identity** — `internal_id`, `sku`, `status`, `item_mode`, `batch_id`
- **Photos** — `photo_folder`, `image_paths` (pipe-separated string)
- **Category** — `category_key`, `category_label`, `ebay_category_id`, `ebay_category_name`, plus Phase 3.6 fields (`category_template_fetched`, `missing_required_fields`, `missing_recommended_fields`, `publish_ready`)
- **Titles** — `title_raw`, `title_final`, `description_final`
- **Brand** — `brand`, `brand_normalized`
- **Category-specific item specifics** — clothing fields (size, color, material, ...), book fields (author, publisher, isbn, ...), collectible/toy fields (mpn, upc, character, franchise, ...)
- **Condition** — `condition_label`, `condition_id`, `condition_notes`, `defects`
- **Measurements** — JSON dict
- **Lot** — `bundle_candidate`, `lot_group_id`, `lot_reason`
- **Pricing** — `cost`, `estimated_price`, `list_price`, `minimum_price`, `shipping_*`, `storage_location`
- **Listing** — `platform`, `listing_id`, `listing_url`, `date_listed`, `days_listed`, `date_sold`, `sold_price`, `fees`, `shipping_cost`, `net_profit`, `profit_margin`
- **AI/review** — `confidence_score`, `needs_review`, `review_reasons`, `manual_override`, `notes`
- **Enrichment** — `enrichment_done`, `enrichment_notes`
- **Sourcing** — `sourcing_location`, `sourcing_date`, `sourcing_batch`
- **Catch-all** — `item_specifics` (JSON dict — used for category-specific aspects beyond the typed columns)
- **Timestamps** — `created_at`, `updated_at`

### JSON fields and serialization

Several fields are typed as `list` or `dict` on the `Item` Pydantic entity but stored as JSON strings in SQLite. The conversion happens in `_to_record()` and `_from_record()` in `packages/data/src/repositories/item_repo.py`:
- Stored as JSON string: `features`, `defects`, `review_reasons`, `measurements`, `item_specifics`, `missing_required_fields`, `missing_recommended_fields`
- Stored as pipe-separated string: `image_paths`

If you add a list/dict field, add it to the serializer's known-JSON-field set — otherwise it gets coerced to comma-joined string and breaks deserialization.

---

## Invariants

These are properties the code maintains. Violating them is a bug.

1. **Once `manual_override=True`, never overwrite** these fields from AI/automation: `cost`, `list_price`, `minimum_price`, `notes`, `storage_location`. (Enforced in `ItemRepository.upsert()`.)
2. **Once `cost_manual=True`, never reset to False.** (Enforced in `ItemRepository.upsert()`.)
3. **Once `enrichment_done=True`, never reset to False.** (Same.)
4. **`internal_id` and `created_at` are immutable.** Updates are filtered to skip them. (Same.)
5. **SKU prefix is authoritative for category.** No code path overrides category from AI extraction. (Enforced in worker and `analyze_single` route by deriving category from manifest's prefix, not from extracted JSON.)
6. **The same SKU never has two rows.** Upsert keys on `sku`. (Enforced by unique index + upsert logic.)
7. **The SKU registry never goes backwards.** `reserve_next` only increments; `preserve_existing` raises the floor without ever lowering it.
8. **The bulk-upload CSV never duplicates lot members.** `EbayCSVWriter.write()` filters out items with `status="lot_member"` or non-wrapper lot members.
9. **Vision model receives at most 3 images.** Hardcoded in `OllamaProvider.analyze` for context-window stability.
10. **Image filenames are normalized to `NN.jpg`.** Folder scanner assumes this; image normalizer enforces it (with backup).

---

## Configuration

### File-driven behaviour

| File | Mutability | Purpose |
|---|---|---|
| `config/sku_prefixes.json` | Edit to add categories | Maps prefix → `{label, category_key, ebay_category_id, lot_eligible, active}` |
| `config/categories.json` | Edit to tune category rules | Required/optional fields, allowed values, title/description templates, lot grouping keys |
| `config/rules.json` | Hot-editable via `/api/settings/rules` | Triage thresholds, review trigger keywords, luxury brand list, pricing rules |
| `config/platforms.json` | Hot-editable via `/api/settings/platforms` | Platform on/off + end-listing-supported flag |
| `config/ebay_fields.json` | Edit if eBay changes column names | Bulk upload column list + internal→eBay field map + defaults |
| `config/model_profiles.json` | Informational reference | Vision model profiles (default/lightweight/premium) — runtime reads `VISION_MODEL_DEFAULT` from `.env`, not this file |
| `config/settings.yaml` | Reference defaults | Mostly superseded by `.env`/`Settings`. Kept for documentation. |

### `Settings` (pydantic-settings)

Defined in `packages/core/src/config.py`. Loaded once at startup, accessible via `get_settings()`. Reads `.env`. All keys documented in `.env.example`.

Most-relevant blocks:
- **Paths** — `intake_root`, `db_path`, `export_dir`, `import_dir`, `log_dir`
- **Vision** — `ollama_base_url`, `vision_model_default/fallback/premium`
- **Thresholds** — `confidence_review_threshold` (0.72), `high_value_review_threshold` (75.00)
- **eBay** — split between `ebay_sandbox_*` and `ebay_prod_*` fields. Properties on `Settings` (`ebay_app_id`, `ebay_cert_id`, `ebay_user_token`, `ebay_api_base`) auto-switch based on `ebay_environment`.
- **Photo hosting** — Cloudinary creds
- **Enrichment** — `anthropic_api_key`, `enrichment_model`, `enrichment_enabled`
- **Notifications** — SMTP creds + `notify_email`
- **Dev** — `dry_run` (bypasses Ollama, returns stub item)

### Cache invalidation

Config accessors are `@lru_cache(1)` decorated. The `PATCH /api/settings/rules` endpoint manually clears `get_rules.cache_clear()` so changes take effect mid-process. If you add a new config accessor and a write endpoint, do the same.

---

## External integrations

### Ollama (vision)

- **Where:** `packages/vision/src/ollama_provider.py`
- **Auth:** None (local HTTP)
- **Model:** `minicpm-v` default; `qwen2.5vl:7b` as default in `settings.yaml` but overridden by `.env`'s `VISION_MODEL_DEFAULT`
- **Endpoint:** `POST {ollama_base_url}/api/chat` with `format=json`, `temperature=0.1`, `num_ctx=8192`, `repeat_penalty=1.15`
- **Image limit:** First 3 images sent (front/back/tag)
- **Retries:** 3, exponential backoff via `tenacity`
- **Failure modes:** connection error, HTTP error, JSON parse failure (handled by `_extract_json` brace-balancing fallback)
- **Dry-run mode:** `Settings.dry_run=True` returns stub response without calling Ollama

### Anthropic (enrichment)

- **Where:** `packages/enrichment/src/enricher.py`
- **Auth:** API key via `Settings.anthropic_api_key`
- **Model:** `claude-sonnet-4-20250514` default
- **Mode:** Text-only currently (item JSON in, enriched JSON out — does *not* send images)
- **Cost tracking:** ~$3/M input + ~$15/M output tokens (Sonnet pricing); estimated cost returned in `Result.details["estimated_cost"]`
- **Price layering:** After Claude responds, `PriceResearcher.research()` is called; if Browse API returns an avg, it overrides Claude's `list_price`
- **Protected fields:** `sku`, `status`, `batch_id`, `photo_folder`, `image_paths`, `category_key`, `category_label`, `ebay_category_id`, `internal_id`, `enrichment_done`, `cost_manual`, `created_at`, `updated_at` are never written by enrichment

### Cloudinary (photo hosting)

- **Where:** `packages/ebay/src/photo_uploader.py`
- **Auth:** `cloud_name`, `api_key`, `api_secret` from `Settings`
- **Folder:** `resale/`
- **Failure mode:** Falls back to local file paths (works for sandbox, breaks production listings)

### eBay (multiple APIs)

All eBay code lives in `packages/ebay/src/`. The `EbayAuth` class is the single source of truth for tokens.

| API | Module | Auth | When called |
|---|---|---|---|
| OAuth 2.0 | `auth.py` | basic auth (app_id + cert_id) | `/api/ebay/oauth/*` flow + auto-refresh |
| Inventory | `inventory_client.py` | OAuth user token | Each publish |
| Account | `inventory_client.py` (`get_seller_policies`) | OAuth user token | First publish per process (cached) |
| Browse | `pricing/price_researcher.py` | App token (client_credentials) | Inside enrichment |
| Taxonomy | `category_intelligence.py` | App token, falls back to user | Per item suggestions + aspects |
| Fulfillment | `sold_sync.py` | OAuth user token | `/api/ebay/sync-sold` |

**Token lifecycle:**
1. User clicks Connect eBay in UI → `/api/ebay/oauth/start` → 302 to eBay
2. eBay calls back to `/api/ebay/oauth/callback?code=…`
3. `EbayAuth.exchange_code_for_tokens()` saves `data/ebay_tokens.json`
4. Subsequent calls to `EbayAuth.get_user_token()` check expiry; if within 5 min, silent refresh via `_refresh_access_token`
5. If file is missing/expired-without-refresh, falls back to `EBAY_*_USER_TOKEN` from `.env` (Identity & Access Framework token)

**Production vs sandbox:** controlled by `EBAY_ENVIRONMENT`. Auth properties on `Settings` (e.g. `ebay_app_id`) auto-route. The Taxonomy host is **always production** (`https://api.ebay.com`) regardless — sandbox doesn't have meaningful catalog data.

**Country code derivation:** `marketplaceId.split("_")[-1]` → `EBAY_US` becomes `US`. Used in `listingPolicies.countryCode` of the offer payload.

**Seller policies:** First active policy of each type (fulfillment, payment, return) is selected and cached. There's no UI for selecting between multiple policies.

### SMTP (notifications)

- **Where:** `packages/notifications/src/notifier.py`
- **Auth:** `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `notify_email`
- **Activation:** `notifications_enabled=true` in `.env`
- **Failure mode:** Logs warning, never raises (notification failure must never break pipeline)

### Watchdog (file watcher)

- **Where:** `packages/capture/src/file_watcher.py`
- **Activation:** `POST /api/capture/watcher/start`
- **Behaviour:** Logs new image files in watched folder. Does NOT trigger the worker — observation only.

---

## Error model

### `Result[T]`

```python
@dataclass
class Result(Generic[T]):
    ok: bool
    value: T | None = None
    error: str | None = None
    error_code: str | None = None
    details: dict = field(default_factory=dict)

    @classmethod
    def success(cls, value: T, **details) -> "Result[T]"
    @classmethod
    def failure(cls, error: str, error_code: str | None = None, **details) -> "Result[T]"
    def unwrap(self) -> T   # raises RuntimeError if not ok
```

Conventions:
- Every public package method that can fail returns `Result[T]`.
- `error_code` is for machine-readable tagging (e.g. `"NOT_CONFIGURED"`, `"API_ERROR"`).
- `error` is for human-readable display.
- `details` carries diagnostic data (e.g. eBay's response body, the failed query string).

### Where exceptions are still thrown

- Programming bugs (KeyError on a missing field that should be there)
- Unrecoverable infrastructure errors at startup (DB can't be created)
- Inside the FastAPI route layer when translating to `HTTPException` (the route catches a Result and raises 4xx/5xx)

### eBay error propagation

`EbayInventoryClient` catches HTTP errors as `_EbayApiError(status_code, message, body)` internally, then converts to `Result.failure(error, error_code="API_ERROR", body=resp.text)`. The route layer surfaces `result.details["body"]` to the UI so the operator sees the actual eBay error message.

---

## Concurrency model

There isn't one, deliberately.

- The FastAPI server is a single uvicorn process (sync or async — most routes are sync).
- The worker is a single subprocess that reads + writes serially.
- SQLite uses `check_same_thread=False` to allow background tasks but is still effectively serialized.
- There's no row-level locking, no transaction isolation handling, no optimistic concurrency.

If you start running multiple workers in parallel, expect:
- SKU registry collisions (atomic increment in SQLite is fine, but two concurrent reservations are still a race against each other and the file system folder rename)
- Photo upload duplication (no idempotency on Cloudinary side beyond the unique-filename setting)
- Last-write-wins on item updates

For now, "run one worker at a time" is part of the contract. The future "live capture studio" Phase 5+ work will need to revisit this.

---

## Persistence and exports

### Source of truth: SQLite

`data/app.db`. Everything else is derived.

### Derived artifacts

- `data/exports/ebay_upload_*.csv` — generated by `EbayCSVWriter.write()` on demand. Never read back.
- `data/exports/master_inventory_*.xlsx` — `MasterSheetWriter.write()`. Never read back.
- `data/exports/backup_*.json` — full DB dump via `scripts/backup_db.py`. Reversible via `scripts/restore_db.py`.
- `data/exports/sales_*.csv` — sales report export. Never read back.
- `data/category_intelligence/{cat_id}_template.json` — cached category templates. Read by `CategorySpreadsheet.load_template()` and used as input for the enrichment system prompt.
- `data/category_intelligence/category_summary.csv` — per-category stats. Updated by `update_field_stats()`.
- `data/logs/app.log` — Python logger output (rotated... but not really; rotation isn't configured at runtime).
- `data/logs/audit.jsonl` — JSONL audit events. Append-only.
- `data/ebay_tokens.json` — OAuth tokens. Auto-managed by `EbayAuth`. Don't commit (already in `.gitignore`).

### Backup/restore

`scripts/backup_db.py` exports items + sku_registry + sale_records as JSON. `scripts/restore_db.py` is destructive — it overwrites all current data. The README warns about this; it's intentional. Always take a fresh backup before restoring.

---

## Observability

Three signals:

1. **`data/logs/app.log`** — standard Python logging output. Configured in the FastAPI lifespan hook. INFO level by default.
2. **`data/logs/audit.jsonl`** — structured business events via `AuditLog`. Currently the class is fully implemented but only invoked from a few places. **This is a known gap — see ROADMAP.md.**
3. **`/api/health`** — endpoint returns DB connectivity, Ollama availability, model name, environment, enrichment-enabled flag.

There's no metrics endpoint, no Prometheus, no distributed tracing. Local-first means we don't need it (yet).

---

## Testing

`tests/` is split into:

- **`unit/`** — 8 modules, no I/O. Tests pure functions: category mapper, CSV writer, folder scanner, pricing estimator, response parser, Result, SKU registry, triage router.
- **`integration/`** — 4 modules. Use in-memory SQLite (never `data/app.db`). Tests pipeline composition: analyze_flow, ebay_publish, intake_pipeline, item_repo.
- **`fixtures/`** — `mock_ebay.py`, `mock_extraction.py`, `sample_items.py` — shared test data.

Run with `make test` or `uv run pytest tests/ -v`. Configured for `asyncio_mode = "auto"`.

What's NOT covered:
- Live HTTP routes (no FastAPI TestClient harness)
- OAuth flow
- Cloudinary uploads
- Anthropic API calls
- Real Ollama integration (uses dry-run / stub)
- Real eBay calls (uses `mock_ebay.py`)

These are integration-environment-only tests; running them requires real credentials and is intentionally excluded from CI scope.

---

## Extension points

### Add a new category

1. Edit `config/sku_prefixes.json` — add `{label, category_key, ebay_category_id, lot_eligible, active}`.
2. Edit `config/categories.json` — add a profile with `required_fields`, `optional_fields`, `allowed_values`, `review_triggers`, `title_template`, `description_template`, `lot_grouping_keys`.
3. (Optional) Add category-specific review triggers in `config/rules.json` under `review_triggers.<category_key>`.
4. (Optional) Add luxury brand list under `rules.luxury_brands.<category_key>`.
5. Restart server (config is `lru_cache`'d).

No code changes required.

### Add a new vision provider

1. Create `packages/vision/src/<provider>_provider.py` implementing `VisionProvider` ABC.
2. Add a profile entry in `config/model_profiles.json` (informational).
3. Wire it in via a constructor param in the worker / `/api/items/{sku}/analyze` route, or add a factory to `packages/vision/`.
4. Tests: add a fixture that returns a stub response.

### Add a new platform (Mercari, Poshmark, ...)

1. Add an entry in `config/platforms.json` with `active=false` initially.
2. Create `packages/<platform>/src/<platform>_client.py` implementing publish + sold-sync.
3. Wire it into `CrossPlatformSync.end_other_platform_listings` so cross-platform takedowns work.
4. Add UI surfaces (probably new routes in `apps/api/src/routes/<platform>.py`).

### Add a new review trigger

1. Add the constant to `packages/core/src/constants.py:ReviewTrigger`.
2. Add it to `config/rules.json` under `review_triggers.global` or `review_triggers.<category>`.
3. If it's text-detected, add the keyword(s) in `ResponseParser.parse()` `trigger_words` dict.
4. If it's value-detected (like high_value), add a comparison in `ResponseParser.parse()` or `TriageRouter.route()`.

### Add a new HTTP endpoint

1. Pick the right router file in `apps/api/src/routes/` (or create one).
2. Add the handler. Keep it thin — delegate to a package.
3. Register the router in `apps/api/src/main.py` if it's a new file.
4. (Optional) Add a UI page in `apps/api/src/routes/ui.py`.
5. Re-run `make docs` to regenerate `docs/openapi.json`.

---

## Known architectural debt

These are tracked in detail in `ROADMAP.md`; flagged here because they shape architectural decisions:

- **`days_listed` not maintained.** No background job updates it after `date_listed` is set. Stale checks depend on it.
- **`AutoRelister._is_listing_active` always returns True.** Defensive fallback because `EbayInventoryClient.get_listing_status` doesn't exist yet.
- **Seller policies pick first-only.** No UI to select among multiple policies.
- **Alembic configured but unused.** Schema evolution happens via `migrate_add_columns()` raw `ALTER TABLE` calls. Migration files exist as templates only.
- **`AuditLog` partially wired.** Class is complete, but not invoked from most callsites.
- **Cross-platform takedowns are stubs.** Only logging today; no API integration for Poshmark/Mercari/Depop/Facebook.
- **No row-level locking.** Single-writer assumption.
- **Title cleaner is reactive.** Strips known AI-added suffixes; new ones require code changes.
