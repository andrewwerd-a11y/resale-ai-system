"""
FastAPI application — local web UI and REST API.
Serves the browser interface at http://localhost:8000
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import init_db, migrate_add_columns
from apps.api.src.routes import (
    items, review, export, health, ui, ebay,
    reports, sourcing, capture, sync, settings, lots, listings, diagnostics,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    cfg.ensure_dirs()
    # Configure file logging once dirs exist
    log_file = cfg.log_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    init_db()
    migrate_add_columns()
    logger.info("Resale AI System started — %s", cfg.ebay_environment)
    yield
    logger.info("Resale AI System shutting down cleanly")


app = FastAPI(
    title="Resale AI System",
    description="Local-first resale automation pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Error handlers ────────────────────────────────────────────────────────────

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;background:#1a1a18;color:#d4d2c8;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        "<div style='text-align:center'>"
        "<div style='font-size:48px;color:#3a3a38;margin-bottom:16px'>404</div>"
        "<div style='font-size:16px;color:#888780'>Page not found</div>"
        "<a href='/' style='display:inline-block;margin-top:20px;color:#7f77dd;"
        "text-decoration:none;font-size:13px'>← Back to dashboard</a>"
        "</div></body></html>",
        status_code=404,
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error("500 error on %s: %s", request.url.path, exc)
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;background:#1a1a18;color:#d4d2c8;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        "<div style='text-align:center'>"
        "<div style='font-size:48px;color:#501313;margin-bottom:16px'>500</div>"
        "<div style='font-size:16px;color:#f09595'>Server error — check data/logs/app.log</div>"
        "<a href='/' style='display:inline-block;margin-top:20px;color:#7f77dd;"
        "text-decoration:none;font-size:13px'>← Back to dashboard</a>"
        "</div></body></html>",
        status_code=500,
    )


# Routers
app.include_router(health.router, prefix="/api")
app.include_router(items.router, prefix="/api/items", tags=["items"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(sourcing.router, prefix="/api/sourcing", tags=["sourcing"])
app.include_router(capture.router, prefix="/api/capture", tags=["capture"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(lots.router, prefix="/api/lots", tags=["lots"])
app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
app.include_router(diagnostics.router, prefix="/api/diagnostics", tags=["diagnostics"])
app.include_router(ui.router, tags=["ui"])


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the minimal operational dashboard."""
    return HTMLResponse(content=_dashboard_html())


def _dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Resale AI System</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #1a1a18; color: #d4d2c8; min-height: 100vh; }
  header { background: #111110; border-bottom: 1px solid #2c2c2a; padding: 14px 24px;
           display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 16px; font-weight: 500; color: #f1efe8; }
  header span { font-size: 12px; color: #888780; }
  nav { background: #111110; border-bottom: 1px solid #2c2c2a; padding: 0 24px;
        display: flex; gap: 0; }
  nav a { display: block; padding: 10px 16px; font-size: 13px; color: #888780;
          text-decoration: none; border-bottom: 2px solid transparent; }
  nav a:hover, nav a.active { color: #f1efe8; border-bottom-color: #7f77dd; }
  main { padding: 24px; max-width: 1200px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 12px; margin-bottom: 28px; }
  .card { background: #2c2c2a; border: 1px solid #3a3a38; border-radius: 8px; padding: 16px;
          cursor: pointer; text-decoration: none; display: block; transition: border-color .15s; }
  .card:hover { border-color: #7f77dd; }
  .card .num { font-size: 28px; font-weight: 500; color: #f1efe8; }
  .card .label { font-size: 12px; color: #888780; margin-top: 4px; }
  .card.review .num { color: #ef9f27; }
  .card.ready  .num { color: #5dcaa5; }
  .card.sold   .num { color: #7f77dd; }
  .card.hconf  .num { color: #5dcaa5; }
  .card.pub    .num { color: #afa9ec; }
  h2 { font-size: 14px; font-weight: 500; color: #f1efe8; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 10px; color: #888780; font-weight: 400;
       border-bottom: 1px solid #2c2c2a; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
  td { padding: 8px 10px; border-bottom: 1px solid #1e1e1c; color: #d4d2c8; }
  tr:hover td { background: #2c2c2a; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
  .badge.pending_intake { background: #2c2c2a; color: #888780; }
  .badge.needs_review   { background: #412402; color: #fac775; }
  .badge.approved       { background: #085041; color: #9fe1cb; }
  .badge.export_ready   { background: #085041; color: #9fe1cb; }
  .badge.exported       { background: #26215c; color: #afa9ec; }
  .badge.rejected       { background: #501313; color: #f09595; }
  .badge.analyzed       { background: #042c53; color: #85b7eb; }
  .btn { display: inline-block; padding: 7px 16px; border-radius: 6px; font-size: 13px;
         cursor: pointer; border: none; font-family: inherit; }
  .btn-primary { background: #534ab7; color: #eeedfe; }
  .btn-primary:hover { background: #7f77dd; }
  .section { margin-bottom: 32px; }
</style>
</head>
<body>
<header>
  <h1>Resale AI System</h1>
  <span id="ts"></span>
</header>
<nav>
  <a href="/" class="active">Dashboard</a>
  <a href="/intake">Intake</a>
  <a href="/review-queue">Review Queue</a>
  <a href="/bulk-approve">Bulk Approve</a>
  <a href="/inventory">Inventory</a>
  <a href="/listings">Listings</a>
  <a href="/lots">Lots</a>
  <a href="/reports">Reports</a>
  <a href="/sourcing">Sourcing</a>
  <a href="/capture">Capture</a>
  <a href="/export">Export</a>
  <a href="/settings">Settings</a>
</nav>
<main>
  <div class="cards" id="stats-cards">
    <a class="card" href="/inventory?status=pending_intake">
      <div class="num" id="stat-pending">...</div><div class="label">Pending intake</div></a>
    <a class="card review" href="/review-queue">
      <div class="num" id="stat-review">...</div><div class="label">Needs review</div></a>
    <a class="card ready" href="/inventory?status=export_ready">
      <div class="num" id="stat-ready">...</div><div class="label">Export ready</div></a>
    <a class="card" href="/inventory?status=listed">
      <div class="num" id="stat-listed">...</div><div class="label">Listed</div></a>
    <a class="card sold" href="/inventory?status=sold">
      <div class="num" id="stat-sold">...</div><div class="label">Sold</div></a>
    <a class="card" href="/inventory">
      <div class="num" id="stat-total">...</div><div class="label">Total items</div></a>
    <a class="card hconf" href="/bulk-approve">
      <div class="num" id="stat-hconf">...</div><div class="label">High confidence pending</div></a>
    <a class="card pub" href="/inventory?status=approved">
      <div class="num" id="stat-pub">...</div><div class="label">Ready to publish</div></a>
    <a class="card" href="/inventory?status=listed" style="border-color:#501313">
      <div class="num" id="stat-stale" style="color:#f09595">...</div><div class="label">Stale listings (60d+)</div></a>
  </div>

  <div class="section">
    <h2>Recent items</h2>
    <table id="recent-table">
      <thead><tr><th>SKU</th><th>Title</th><th>Category</th><th>Status</th><th>Confidence</th><th>Est. Price</th></tr></thead>
      <tbody id="recent-body"><tr><td colspan="6" style="color:#888780">Loading...</td></tr></tbody>
    </table>
  </div>

  <div style="display:flex;gap:10px;margin-top:16px">
    <button class="btn btn-primary" onclick="runWorker()">Run intake worker</button>
    <button class="btn btn-primary" onclick="location.href='/export'">Generate eBay CSV</button>
  </div>
  <div id="worker-msg" style="margin-top:10px;font-size:13px;color:#5dcaa5"></div>
</main>
<script>
document.getElementById('ts').textContent = new Date().toLocaleString();

async function loadStats() {
  try {
    const r = await fetch('/api/items/stats');
    const d = await r.json();
    document.getElementById('stat-pending').textContent = d.pending_intake  || 0;
    document.getElementById('stat-review').textContent  = d.needs_review    || 0;
    document.getElementById('stat-ready').textContent   = d.export_ready    || 0;
    document.getElementById('stat-listed').textContent  = d.listed          || 0;
    document.getElementById('stat-sold').textContent    = d.sold            || 0;
    document.getElementById('stat-total').textContent   = d._total          || 0;
    document.getElementById('stat-hconf').textContent   = d._high_confidence_pending || 0;
    document.getElementById('stat-pub').textContent     = d._ready_to_publish || 0;
    document.getElementById('stat-stale').textContent   = d._stale_count || 0;
  } catch(e) { console.error(e); }
}

async function loadRecent() {
  try {
    const r = await fetch('/api/items?limit=20');
    const items = await r.json();
    const tbody = document.getElementById('recent-body');
    if (!items.length) { tbody.innerHTML = '<tr><td colspan="6" style="color:#888780">No items yet.</td></tr>'; return; }
    tbody.innerHTML = items.map(it => `
      <tr>
        <td style="font-family:monospace">${it.sku||'-'}</td>
        <td>${(it.title_final||it.title_raw||'-').slice(0,60)}</td>
        <td>${it.category_label||'-'}</td>
        <td><span class="badge ${it.status||''}">${it.status||'-'}</span></td>
        <td>${it.confidence_score!=null ? (it.confidence_score*100).toFixed(0)+'%' : '-'}</td>
        <td>${it.estimated_price!=null ? '$'+it.estimated_price.toFixed(2) : '-'}</td>
      </tr>`).join('');
  } catch(e) { console.error(e); }
}

async function runWorker() {
  document.getElementById('worker-msg').textContent = 'Starting worker...';
  try {
    const r = await fetch('/api/items/process', {method:'POST'});
    const d = await r.json();
    document.getElementById('worker-msg').textContent = d.message || 'Done';
    loadStats(); loadRecent();
  } catch(e) { document.getElementById('worker-msg').textContent = 'Error: ' + e; }
}

loadStats(); loadRecent();
// Auto-refresh every 30 seconds
setInterval(() => { loadStats(); loadRecent(); }, 30000);
</script>
</body>
</html>"""
