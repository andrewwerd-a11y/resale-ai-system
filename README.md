# Resale AI System

Local-first resale automation pipeline. Turns photo folders into structured eBay-ready listings with minimal typing.

---

## What it does

1. Watches your `intake/pending/` folder for new item folders
2. Preserves or suggests SKUs (e.g. `CL-000007`)
3. Analyzes photos with a local AI vision model (Ollama — no internet required after setup)
4. Extracts brand, size, color, condition, defects, measurements, and more
5. Routes each item to: **single listing / lot / review queue / reject**
6. Stores everything in a local SQLite database
7. Publishes directly to eBay via API, or exports eBay-ready bulk upload CSVs
8. Tracks sold items, sale prices, and profit margins
9. Keeps a live master inventory spreadsheet
10. Runs fully automated on Windows startup via Task Scheduler

---

## Features by phase

| Phase | What shipped |
|-------|-------------|
| 1 | Folder scanner, SKU registry, intake pipeline, SQLite database |
| 2 | Ollama vision integration, category-aware field extraction, confidence scoring |
| 3 | Triage router (single/lot/review/reject), pricing estimator, review queue UI |
| 4 | AI enrichment (Claude), eBay price comp via sold listings, cost tracking, stale detection |
| 5 | eBay API publishing, Cloudinary photo hosting, sold sync, direct listing flow |
| 6 | Full test suite (173 tests), production hardening, Windows auto-start, backup/restore |

---

## Requirements

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | https://www.python.org/downloads/ |
| uv | latest | https://docs.astral.sh/uv/getting-started/installation/ |
| Ollama | latest | https://ollama.com/download |

> **Windows note:** All commands below run in PowerShell or Windows Terminal.
> If you have Git Bash, that also works.

---

## First-time setup

### 1. Clone or download this project

```powershell
cd C:\Users\YourName\Projects
git clone <repo-url> resale-ai-system
cd resale-ai-system
```

### 2. Install Python dependencies

```powershell
uv sync --all-extras
```

### 3. Copy and configure environment variables

```powershell
copy .env.example .env
```

Open `.env` in Notepad or VS Code and set:
- `INTAKE_ROOT` — path to your intake folder (or leave as `./intake`)
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` — for photo hosting
- `EBAY_APP_ID`, `EBAY_CERT_ID`, `EBAY_DEV_ID`, `EBAY_USER_TOKEN` — for eBay API publishing
- `ANTHROPIC_API_KEY` — for Claude AI enrichment (optional, offline works without it)

### 4. Install Ollama and pull the vision model

```powershell
# Download Ollama from https://ollama.com/download and install it
# Then pull the default vision model:
ollama pull minicpm-v
```

> This downloads ~3GB. Do this once while you have a good connection.
> After this, everything runs offline.

For a larger/more accurate model (requires 6GB+ VRAM):
```powershell
ollama pull qwen2.5vl:7b
```
Then set `VISION_MODEL_DEFAULT=qwen2.5vl:7b` in your `.env`.

### 5. Initialize the database

```powershell
uv run alembic upgrade head
```

### 6. Start the system

```powershell
uv run uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser to `http://localhost:8000`.

---

## Windows auto-start

Install as a Windows Task Scheduler task (runs automatically on login):

```powershell
uv run python scripts/install_service.py
```

To uninstall:
```powershell
uv run python scripts/install_service.py --uninstall
```

---

## Daily workflow

### Processing new items

1. Put your item's photo folder into `intake/pending/`
   ```
   intake/pending/CL-000036/
       01.jpg   ← front
       02.jpg   ← back
       03.jpg   ← tag/label
       04.jpg   ← detail
       05.jpg   ← flaw (if any)
   ```

2. Run the intake worker:
   ```powershell
   uv run python apps/worker/src/main.py
   ```
   Or use the browser UI at `http://localhost:8000` → Intake Queue

3. AI analyzes photos and fills all fields automatically

4. Items route to review queue if confidence is low or item needs attention

5. Approve items in the browser at `http://localhost:8000` → Review Queue

6. Publish directly to eBay:
   ```powershell
   uv run python scripts/publish_ebay.py
   ```
   Or export a CSV for manual upload:
   ```powershell
   uv run python scripts/export_ebay_csv.py
   ```
   Output appears in `data/exports/`

---

## Migrating existing items

Your existing `Inventory_Photos/` folder can be imported in one command:

```powershell
uv run python scripts/backfill.py --source "C:\path\to\your\Inventory_Photos"
```

This will:
- Scan all existing SKU folders (BK-000001, CL-000001, etc.)
- Preserve every existing SKU — nothing gets renamed or overwritten
- Create item records in the database for each folder
- Queue them for AI analysis

---

## Backup and restore

### Create a backup

```powershell
uv run python scripts/backup_db.py
```

Output: `data/exports/backup_YYYYMMDD_HHMMSS.json` — all items, SKU registry, and sale records.

### Restore from a backup

```powershell
uv run python scripts/restore_db.py --file data/exports/backup_20260401.json
```

> WARNING: Restore overwrites all current data. Take a fresh backup first.

---

## Running the test suite

```powershell
uv run pytest tests/ -v
```

Run only unit tests:
```powershell
uv run pytest tests/unit/ -v
```

Run only integration tests:
```powershell
uv run pytest tests/integration/ -v
```

Run with short failure summaries:
```powershell
uv run pytest tests/ -v --tb=short
```

All 173 tests run against an in-memory SQLite database — the production `data/app.db` is never touched.

---

## Folder structure

```
resale-ai-system/
├── apps/
│   ├── api/          ← FastAPI local web server + browser UI
│   └── worker/       ← Background processing jobs
├── packages/
│   ├── core/         ← Settings, types, constants, Result[T] pattern
│   ├── domain/       ← Entities (Item, Batch, SKU...)
│   ├── intake/       ← Folder scanner, manifest builder
│   ├── sku/          ← SKU registry and generator
│   ├── vision/       ← Ollama vision provider + prompt builder
│   ├── classification/ ← Category and field mapping
│   ├── triage/       ← Single/lot/review/reject router
│   ├── pricing/      ← Price estimation rules
│   ├── data/         ← SQLite models and repositories
│   ├── spreadsheet/  ← Master inventory sheet
│   ├── ebay/         ← eBay CSV export
│   ├── sync/         ← eBay API publishing + sold sync
│   ├── capture/      ← Photo capture pipeline
│   ├── notifications/ ← Alerts and notifications
│   └── logging/      ← Audit log (JSONL + Python logger)
├── config/           ← All configuration files (JSON/YAML)
├── data/             ← Database, exports, imports, logs
├── intake/           ← Your item folders go here
├── scripts/          ← Utility scripts (backup, restore, export, service)
├── tests/
│   ├── unit/         ← 8 unit test modules (no I/O)
│   ├── integration/  ← 4 integration test modules (in-memory DB)
│   └── fixtures/     ← Shared test data and mock helpers
└── docs/             ← Documentation
```

---

## Configuration

All behavior is controlled by files in `config/`:

| File | Purpose |
|------|---------|
| `sku_prefixes.json` | SKU category prefixes (BK, CL, CO, SH, TO...) |
| `categories.json` | Category profiles, required fields, eBay category IDs |
| `rules.json` | Triage rules, review triggers, pricing rules |
| `settings.yaml` | Runtime settings (thresholds, paths, model names) |
| `ebay_fields.json` | eBay bulk upload field mapping |
| `model_profiles.json` | AI model profiles and Ollama pull commands |
| `platforms.json` | Platform-specific listing settings |

**Adding a new category** — edit `sku_prefixes.json` and `categories.json` only. No code changes needed.

---

## Item status lifecycle

```
pending_intake → sku_suggested → sku_confirmed → analyzed
    → needs_review  (human checks it)
    → approved → export_ready → exported → listed → sold → archived
    → rejected
```

---

## Phase roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | Done | Intake pipeline, SKU registry, SQLite |
| 2 | Done | Ollama vision, field extraction, confidence scoring |
| 3 | Done | Triage router, pricing, review queue UI |
| 4 | Done | Claude enrichment, eBay price comps, cost tracking |
| 5 | Done | eBay API publishing, Cloudinary photos, sold sync |
| 6 | Done | Full test suite, production hardening, backup/restore |
| 7 | Planned | Hardware integration (barcode scanner, label printer) |
| 8 | Planned | Cross-platform APIs (Mercari, Poshmark, Facebook Marketplace) |

---

## Offline operation

The only times internet is required:
- First-time Ollama install and model pulls
- Cloudinary photo uploads (for eBay listings)
- eBay API calls (publishing, sold sync)
- Claude enrichment (optional)

Everything else — scanning, AI analysis, database writes, CSV generation, the browser UI — runs entirely on your local machine.

---

## Troubleshooting

**Ollama not responding**
```powershell
ollama serve
# Then in a separate terminal:
ollama list
```

**Model not found**
```powershell
ollama pull minicpm-v
```

**Database errors**
```powershell
uv run alembic upgrade head
```

**Port 8000 already in use**
Edit `.env` and change `API_PORT=8001`, then open `http://localhost:8001`.

**Check system health**
```
GET http://localhost:8000/api/health
```
Returns database status, Ollama connectivity, model loaded, and version info.
