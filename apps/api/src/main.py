"""
FastAPI application — local web UI and REST API.
Serves the browser interface at http://localhost:8000
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import init_db
from apps.api.src.routes import items, review, export, health, ui, ebay


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    yield


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

# Routers
app.include_router(health.router, prefix="/api")
app.include_router(items.router, prefix="/api/items", tags=["items"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
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
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin-bottom: 28px; }
  .card { background: #2c2c2a; border: 1px solid #3a3a38; border-radius: 8px; padding: 16px; }
  .card .num { font-size: 28px; font-weight: 500; color: #f1efe8; }
  .card .label { font-size: 12px; color: #888780; margin-top: 4px; }
  .card.review .num { color: #ef9f27; }
  .card.ready .num { color: #5dcaa5; }
  .card.sold .num { color: #7f77dd; }
  h2 { font-size: 14px; font-weight: 500; color: #f1efe8; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 10px; color: #888780; font-weight: 400;
       border-bottom: 1px solid #2c2c2a; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
  td { padding: 8px 10px; border-bottom: 1px solid #1e1e1c; color: #d4d2c8; }
  tr:hover td { background: #2c2c2a; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
  .badge.pending   { background: #2c2c2a; color: #888780; }
  .badge.review    { background: #412402; color: #fac775; }
  .badge.approved  { background: #085041; color: #9fe1cb; }
  .badge.exported  { background: #26215c; color: #afa9ec; }
  .badge.rejected  { background: #501313; color: #f09595; }
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
  <a href="/intake">Intake Queue</a>
  <a href="/review-queue">Review Queue</a>
  <a href="/inventory">Inventory</a>
  <a href="/export">Export</a>
</nav>
<main>
  <div class="cards" id="stats-cards">
    <div class="card"><div class="num" id="stat-pending">...</div><div class="label">Pending intake</div></div>
    <div class="card review"><div class="num" id="stat-review">...</div><div class="label">Needs review</div></div>
    <div class="card ready"><div class="num" id="stat-ready">...</div><div class="label">Export ready</div></div>
    <div class="card"><div class="num" id="stat-listed">...</div><div class="label">Listed</div></div>
    <div class="card sold"><div class="num" id="stat-sold">...</div><div class="label">Sold</div></div>
    <div class="card"><div class="num" id="stat-total">...</div><div class="label">Total items</div></div>
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
    document.getElementById('stat-pending').textContent  = d.pending_intake  || 0;
    document.getElementById('stat-review').textContent   = d.needs_review    || 0;
    document.getElementById('stat-ready').textContent    = d.export_ready    || 0;
    document.getElementById('stat-listed').textContent   = d.listed          || 0;
    document.getElementById('stat-sold').textContent     = d.sold            || 0;
    document.getElementById('stat-total').textContent    = d._total          || 0;
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
</script>
</body>
</html>"""
