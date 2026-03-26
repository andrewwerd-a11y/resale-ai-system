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
7. Exports eBay-ready bulk upload CSVs
8. Keeps a live master inventory spreadsheet

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
# If using git:
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
- `INTAKE_ROOT` — path to your intake folder (or leave as `./intake` to use the folder inside this project)
- Everything else can stay as defaults for now

### 4. Install Ollama and pull the vision model

```powershell
# Download Ollama from https://ollama.com/download and install it
# Then pull the default vision model:
ollama pull qwen2.5vl:7b
```

> This downloads ~5GB. Do this once while you have a good connection.
> After this, everything runs offline.

If your machine has less than 6GB VRAM, use the lighter model instead:
```powershell
ollama pull minicpm-v
```
Then set `VISION_MODEL_DEFAULT=minicpm-v` in your `.env`.

### 5. Initialize the database

```powershell
uv run alembic upgrade head
```

### 6. Start the system

```powershell
# Start the local API server (keeps running in this terminal):
uv run uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000 --reload

# Open your browser to:
# http://localhost:8000
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

3. Confirm or override the suggested SKU

4. AI analyzes photos and fills all fields automatically

5. Items route to review queue if confidence is low or item needs attention

6. Approve items in the browser at `http://localhost:8000` → Review Queue

7. Generate eBay CSV:
   ```powershell
   uv run python scripts/export_ebay_csv.py
   ```
   Output appears in `data/exports/`

8. Upload CSV to eBay Seller Hub → Reports → Upload

---

## Migrating your existing 86 items

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

## Folder structure

```
resale-ai-system/
├── apps/
│   ├── api/          ← FastAPI local web server
│   └── worker/       ← Background processing jobs
├── packages/
│   ├── core/         ← Settings, types, constants
│   ├── domain/       ← Entities (Item, Batch, SKU...)
│   ├── intake/       ← Folder scanner, manifest builder
│   ├── sku/          ← SKU registry and generator
│   ├── vision/       ← AI provider + prompt builder
│   ├── classification/ ← Category and field mapping
│   ├── triage/       ← Single/lot/review/reject router
│   ├── pricing/      ← Price estimation rules
│   ├── data/         ← SQLite models and repositories
│   ├── spreadsheet/  ← Master inventory sheet
│   └── ebay/         ← eBay CSV export
├── config/           ← All configuration files (JSON/YAML)
├── data/             ← Database, exports, imports, logs
├── intake/           ← Your item folders go here
├── docs/             ← Documentation
└── scripts/          ← One-off utility scripts
```

---

## Configuration

All behavior is controlled by files in `config/`:

| File | Purpose |
|------|---------|
| `sku_prefixes.json` | SKU category prefixes (BK, CL, CO, SH, TO...) |
| `categories.json` | Category profiles, required fields, eBay IDs |
| `rules.json` | Triage rules, review triggers, pricing rules |
| `settings.yaml` | Runtime settings (thresholds, paths, model) |
| `ebay_fields.json` | eBay bulk upload field mapping |
| `model_profiles.json` | AI model profiles and Ollama pull commands |

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

## Offline operation

The only times internet is required:
- First-time Ollama install
- Pulling vision models (`ollama pull ...`)
- Uploading the final CSV to eBay Seller Hub

Everything else — scanning, AI analysis, database writes, CSV generation, the browser UI — runs entirely on your local machine.

---

## Troubleshooting

**Ollama not responding**
```powershell
# Make sure Ollama is running:
ollama serve
# Then in a separate terminal, verify:
ollama list
```

**Model not found**
```powershell
ollama pull qwen2.5vl:7b
```

**Database errors**
```powershell
uv run alembic upgrade head
```

**Port 8000 already in use**
Edit `.env` and change `API_PORT=8001`, then open `http://localhost:8001`.
