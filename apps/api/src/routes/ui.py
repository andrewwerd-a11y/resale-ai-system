"""
Browser UI routes — serves full HTML pages for each view.
All data is fetched from the /api/* endpoints via JavaScript.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/review-queue", response_class=HTMLResponse)
async def review_queue_page():
    return HTMLResponse(_review_queue_html())


@router.get("/inventory", response_class=HTMLResponse)
async def inventory_page():
    return HTMLResponse(_inventory_html())


@router.get("/export", response_class=HTMLResponse)
async def export_page():
    return HTMLResponse(_export_html())


@router.get("/intake", response_class=HTMLResponse)
async def intake_page():
    return HTMLResponse(_intake_html())


def _nav(active: str) -> str:
    pages = [
        ("Dashboard", "/", "dashboard"),
        ("Intake Queue", "/intake", "intake"),
        ("Review Queue", "/review-queue", "review"),
        ("Inventory", "/inventory", "inventory"),
        ("Export", "/export", "export"),
    ]
    links = "".join(
        f'<a href="{url}" class="{"active" if key == active else ""}">{label}</a>'
        for label, url, key in pages
    )
    return f"""
    <header>
      <h1>Resale AI System</h1>
      <span id="ts"></span>
    </header>
    <nav>{links}</nav>
    <script>document.getElementById('ts').textContent = new Date().toLocaleString();</script>
    """


def _base_style() -> str:
    return """
    <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a18; color: #d4d2c8; min-height: 100vh; }
    header { background: #111110; border-bottom: 1px solid #2c2c2a; padding: 14px 24px;
             display: flex; align-items: center; justify-content: space-between; }
    header h1 { font-size: 16px; font-weight: 500; color: #f1efe8; }
    header span { font-size: 12px; color: #888780; }
    nav { background: #111110; border-bottom: 1px solid #2c2c2a; padding: 0 24px; display: flex; }
    nav a { display: block; padding: 10px 16px; font-size: 13px; color: #888780;
            text-decoration: none; border-bottom: 2px solid transparent; }
    nav a:hover, nav a.active { color: #f1efe8; border-bottom-color: #7f77dd; }
    main { padding: 24px; max-width: 1400px; }
    h2 { font-size: 14px; font-weight: 500; color: #f1efe8; margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 8px 10px; color: #888780; font-weight: 400; font-size: 11px;
         text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid #2c2c2a; }
    td { padding: 8px 10px; border-bottom: 1px solid #1e1e1c; color: #d4d2c8; vertical-align: top; }
    tr:hover td { background: #222220; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
    .badge.pending_intake { background: #2c2c2a; color: #888780; }
    .badge.needs_review   { background: #412402; color: #fac775; }
    .badge.approved       { background: #085041; color: #9fe1cb; }
    .badge.export_ready   { background: #085041; color: #9fe1cb; }
    .badge.exported       { background: #26215c; color: #afa9ec; }
    .badge.rejected       { background: #501313; color: #f09595; }
    .badge.analyzed       { background: #042c53; color: #85b7eb; }
    .btn { display: inline-block; padding: 6px 14px; border-radius: 6px; font-size: 13px;
           cursor: pointer; border: none; font-family: inherit; }
    .btn-green  { background: #085041; color: #9fe1cb; }
    .btn-green:hover  { background: #0f6e56; }
    .btn-red    { background: #501313; color: #f09595; }
    .btn-red:hover    { background: #791f1f; }
    .btn-purple { background: #534ab7; color: #eeedfe; }
    .btn-purple:hover { background: #7f77dd; }
    .btn-gray   { background: #2c2c2a; color: #d4d2c8; }
    .btn-gray:hover   { background: #3a3a38; }
    input, textarea, select { background: #2c2c2a; border: 1px solid #3a3a38; color: #f1efe8;
                              border-radius: 6px; padding: 6px 10px; font-size: 13px;
                              font-family: inherit; width: 100%; }
    input:focus, textarea:focus { outline: none; border-color: #7f77dd; }
    label { font-size: 12px; color: #888780; display: block; margin-bottom: 4px; }
    .field-row { margin-bottom: 12px; }
    .msg { padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-top: 10px; }
    .msg.ok  { background: #085041; color: #9fe1cb; }
    .msg.err { background: #501313; color: #f09595; }
    </style>
    """


def _review_queue_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Review Queue — Resale AI</title>
{_base_style()}
<style>
.review-layout {{ display: grid; grid-template-columns: 1fr 380px; gap: 20px; height: calc(100vh - 120px); }}
.item-list {{ overflow-y: auto; }}
.item-card {{ background: #222220; border: 1px solid #2c2c2a; border-radius: 8px;
              padding: 14px; margin-bottom: 10px; cursor: pointer; }}
.item-card:hover {{ border-color: #534ab7; }}
.item-card.selected {{ border-color: #7f77dd; background: #26215c22; }}
.item-card .sku {{ font-family: monospace; font-size: 13px; color: #f1efe8; }}
.item-card .title {{ font-size: 12px; color: #888780; margin-top: 3px; white-space: nowrap;
                     overflow: hidden; text-overflow: ellipsis; }}
.item-card .meta {{ display: flex; gap: 8px; margin-top: 6px; font-size: 11px; color: #888780; }}
.detail-panel {{ background: #111110; border: 1px solid #2c2c2a; border-radius: 8px;
                 overflow-y: auto; padding: 16px; }}
.images {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 16px; }}
.images img {{ width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 4px;
               border: 1px solid #2c2c2a; cursor: pointer; }}
.images img:hover {{ border-color: #7f77dd; }}
.reason-tag {{ display: inline-block; background: #412402; color: #fac775;
               padding: 2px 8px; border-radius: 4px; font-size: 11px; margin: 2px; }}
.actions {{ display: flex; gap: 8px; margin-top: 16px; }}
.conf-bar {{ height: 4px; background: #2c2c2a; border-radius: 2px; margin-top: 4px; }}
.conf-fill {{ height: 100%; border-radius: 2px; }}
</style>
</head>
<body>
{_nav("review")}
<main>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <h2>Review Queue <span id="review-count" style="color:#888780;font-weight:400"></span></h2>
</div>
<div class="review-layout">
  <div class="item-list" id="item-list">
    <div style="color:#888780;font-size:13px">Loading...</div>
  </div>
  <div class="detail-panel" id="detail-panel">
    <div style="color:#888780;font-size:13px;margin-top:40px;text-align:center">
      Select an item to review
    </div>
  </div>
</div>
</main>
<script>
let items = [];
let selected = null;

async function loadQueue() {{
  const r = await fetch('/api/review');
  items = await r.json();
  document.getElementById('review-count').textContent = `(${{items.length}})`;
  const list = document.getElementById('item-list');
  if (!items.length) {{
    list.innerHTML = '<div style="color:#5dcaa5;font-size:13px;padding:20px;text-align:center">No items need review. All clear!</div>';
    return;
  }}
  list.innerHTML = items.map((it, i) => {{
    const conf = it.confidence_score != null ? (it.confidence_score * 100).toFixed(0) + '%' : '?';
    const reasons = (it.review_reasons || []).join(', ') || 'flagged';
    return `<div class="item-card" id="card-${{i}}" onclick="selectItem(${{i}})">
      <div class="sku">${{it.sku}}</div>
      <div class="title">${{it.title_final || it.title_raw || 'No title yet'}}</div>
      <div class="meta">
        <span>${{it.category_label || it.category_key}}</span>
        <span>·</span>
        <span>Confidence: ${{conf}}</span>
      </div>
      <div style="margin-top:6px;font-size:11px;color:#fac775">${{reasons}}</div>
    </div>`;
  }}).join('');
}}

function selectItem(i) {{
  selected = i;
  document.querySelectorAll('.item-card').forEach(c => c.classList.remove('selected'));
  const card = document.getElementById('card-' + i);
  if (card) card.classList.add('selected');
  renderDetail(items[i]);
}}

function renderDetail(it) {{
  const conf = it.confidence_score != null ? it.confidence_score : 0;
  const confPct = (conf * 100).toFixed(0);
  const confColor = conf >= 0.72 ? '#5dcaa5' : conf >= 0.50 ? '#fac775' : '#f09595';
  const reasons = (it.review_reasons || []).map(r =>
    `<span class="reason-tag">${{r.replace(/_/g,' ')}}</span>`).join('');

  // Build image paths — try to show them
  const imgPaths = (it.image_paths || '').split('|').filter(Boolean);
  const imgs = imgPaths.slice(0,6).map(p =>
    `<img src="/api/items/${{it.sku}}/image?path=${{encodeURIComponent(p)}}"
          onerror="this.style.display='none'"
          alt="item photo">`
  ).join('');

  const fields = [
    ['Title', 'title_final'], ['Brand', 'brand'], ['Type', 'type'],
    ['Department', 'department'], ['Size', 'size'], ['Color', 'color'],
    ['Material', 'material'], ['Pattern', 'pattern'], ['Style', 'style'],
    ['Condition', 'condition_label'], ['Condition notes', 'condition_notes'],
    ['Defects', 'defects'], ['Est. price', 'estimated_price'],
    ['List price', 'list_price'], ['Author', 'author'], ['Format', 'format'],
    ['Franchise', 'franchise'], ['Character', 'character'],
  ];

  const fieldRows = fields.map(([label, key]) => {{
    let val = it[key];
    if (Array.isArray(val)) val = val.join(', ');
    if (val === null || val === undefined || val === '') return '';
    const prefix = key.includes('price') ? '$' : '';
    return `<div class="field-row">
      <label>${{label}}</label>
      <input type="text" id="field-${{key}}" value="${{prefix}}${{val}}"
             onchange="markEdited('${{key}}', this.value)">
    </div>`;
  }}).filter(Boolean).join('');

  document.getElementById('detail-panel').innerHTML = `
    <div style="font-family:monospace;font-size:14px;color:#f1efe8;margin-bottom:4px">${{it.sku}}</div>
    <div style="font-size:12px;color:#888780;margin-bottom:12px">${{it.category_label || it.category_key}}</div>

    <div style="margin-bottom:10px">${{reasons}}</div>

    <div style="margin-bottom:8px">
      <span style="font-size:12px;color:#888780">Confidence: </span>
      <span style="font-size:12px;color:${{confColor}}">${{confPct}}%</span>
      <div class="conf-bar"><div class="conf-fill" style="width:${{confPct}}%;background:${{confColor}}"></div></div>
    </div>

    <div class="images">${{imgs}}</div>

    <div id="edit-fields">${{fieldRows}}</div>

    <div class="field-row">
      <label>Notes</label>
      <textarea id="field-notes" rows="2" onchange="markEdited('notes', this.value)">${{it.notes || ''}}</textarea>
    </div>

    <div class="actions">
      <button class="btn btn-green" onclick="approve('${{it.sku}}')">Approve</button>
      <button class="btn btn-purple" onclick="editAndApprove('${{it.sku}}')">Save edits + approve</button>
      <button class="btn btn-red" onclick="reject('${{it.sku}}')">Reject</button>
    </div>
    <div id="action-msg"></div>
  `;
}}

let edits = {{}};
function markEdited(key, val) {{
  // Strip leading $ for price fields
  edits[key] = val.replace(/^\\$/, '');
}}

async function approve(sku) {{
  const r = await fetch(`/api/review/${{sku}}/approve`, {{method: 'POST'}});
  if (r.ok) {{ showMsg('Approved — moved to export queue.', 'ok'); removeItem(sku); }}
  else showMsg('Error approving.', 'err');
}}

async function reject(sku) {{
  const r = await fetch(`/api/review/${{sku}}/reject`, {{method: 'POST'}});
  if (r.ok) {{ showMsg('Rejected.', 'ok'); removeItem(sku); }}
  else showMsg('Error rejecting.', 'err');
}}

async function editAndApprove(sku) {{
  if (!Object.keys(edits).length) {{ approve(sku); return; }}
  const r = await fetch(`/api/review/${{sku}}/edit`, {{
    method: 'PATCH',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(edits),
  }});
  if (r.ok) {{ showMsg('Saved and approved.', 'ok'); edits = {{}}; removeItem(sku); }}
  else showMsg('Error saving.', 'err');
}}

function showMsg(text, type) {{
  const el = document.getElementById('action-msg');
  if (el) {{ el.className = 'msg ' + type; el.textContent = text; }}
}}

function removeItem(sku) {{
  items = items.filter(i => i.sku !== sku);
  document.getElementById('review-count').textContent = `(${{items.length}})`;
  setTimeout(() => {{
    loadQueue();
    document.getElementById('detail-panel').innerHTML =
      '<div style="color:#888780;font-size:13px;margin-top:40px;text-align:center">Select an item to review</div>';
  }}, 1200);
}}

loadQueue();
</script>
</body></html>"""


def _inventory_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Inventory — Resale AI</title>
{_base_style()}
</head>
<body>
{_nav("inventory")}
<main>
<div style="display:flex;gap:10px;margin-bottom:16px;align-items:center">
  <h2 style="margin:0">Inventory</h2>
  <input type="text" id="search" placeholder="Search SKU, title, brand..."
         style="width:280px" oninput="filterItems()">
  <select id="status-filter" onchange="filterItems()" style="width:160px">
    <option value="">All statuses</option>
    <option value="pending_intake">Pending intake</option>
    <option value="analyzed">Analyzed</option>
    <option value="needs_review">Needs review</option>
    <option value="approved">Approved</option>
    <option value="export_ready">Export ready</option>
    <option value="exported">Exported</option>
    <option value="listed">Listed</option>
    <option value="sold">Sold</option>
    <option value="rejected">Rejected</option>
  </select>
  <span id="count" style="font-size:12px;color:#888780"></span>
</div>
<table>
  <thead><tr>
    <th>SKU</th><th>Title</th><th>Category</th><th>Brand</th>
    <th>Size</th><th>Condition</th><th>Status</th>
    <th>Confidence</th><th>Est. Price</th><th>List Price</th>
  </tr></thead>
  <tbody id="inv-body"><tr><td colspan="10" style="color:#888780">Loading...</td></tr></tbody>
</table>
</main>
<script>
let allItems = [];
async function load() {{
  const r = await fetch('/api/items?limit=500');
  allItems = await r.json();
  filterItems();
}}
function filterItems() {{
  const q = document.getElementById('search').value.toLowerCase();
  const st = document.getElementById('status-filter').value;
  const filtered = allItems.filter(it => {{
    const matchQ = !q || (it.sku||'').toLowerCase().includes(q)
      || (it.title_final||'').toLowerCase().includes(q)
      || (it.brand||'').toLowerCase().includes(q);
    const matchSt = !st || it.status === st;
    return matchQ && matchSt;
  }});
  document.getElementById('count').textContent = filtered.length + ' items';
  document.getElementById('inv-body').innerHTML = filtered.map(it => `
    <tr>
      <td style="font-family:monospace">${{it.sku||'-'}}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${{(it.title_final||it.title_raw||'-').slice(0,60)}}</td>
      <td>${{it.category_label||'-'}}</td>
      <td>${{it.brand||'-'}}</td>
      <td>${{it.size||'-'}}</td>
      <td>${{it.condition_label||'-'}}</td>
      <td><span class="badge ${{it.status||''}}">${{it.status||'-'}}</span></td>
      <td>${{it.confidence_score!=null?(it.confidence_score*100).toFixed(0)+'%':'-'}}</td>
      <td>${{it.estimated_price!=null?'$'+it.estimated_price.toFixed(2):'-'}}</td>
      <td>${{it.list_price!=null?'$'+it.list_price.toFixed(2):'-'}}</td>
    </tr>`).join('');
}}
load();
</script>
</body></html>"""


def _export_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Export — Resale AI</title>
{_base_style()}
</head>
<body>
{_nav("export")}
<main>
<h2>Export Center</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:700px">
  <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px">
    <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:6px">eBay bulk upload CSV</div>
    <div style="font-size:12px;color:#888780;margin-bottom:14px">
      Exports all export-ready items to a CSV you can upload to eBay Seller Hub.
    </div>
    <div style="font-size:24px;font-weight:500;color:#5dcaa5;margin-bottom:14px" id="ready-count">...</div>
    <div style="font-size:12px;color:#888780;margin-bottom:14px">items ready</div>
    <button class="btn btn-purple" onclick="generateCSV()">Generate eBay CSV</button>
    <div id="csv-msg"></div>
  </div>
  <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px">
    <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:6px">Master inventory sheet</div>
    <div style="font-size:12px;color:#888780;margin-bottom:14px">
      Generates an Excel file with all items and their current status.
    </div>
    <button class="btn btn-gray" onclick="generateSheet()">Generate master sheet</button>
    <div id="sheet-msg"></div>
  </div>
</div>
<div style="margin-top:28px">
  <h2>Upload instructions</h2>
  <ol style="font-size:13px;color:#888780;line-height:2;margin-left:20px;margin-top:10px">
    <li>Generate the eBay CSV above</li>
    <li>Open <strong style="color:#d4d2c8">eBay Seller Hub</strong> → Reports → Upload a file</li>
    <li>Select template type: <strong style="color:#d4d2c8">Active listings</strong></li>
    <li>Upload the CSV file from your <code style="color:#7f77dd">data/exports/</code> folder</li>
    <li>Review the upload summary in Seller Hub</li>
  </ol>
</div>
</main>
<script>
async function loadStats() {{
  const r = await fetch('/api/export/stats');
  const d = await r.json();
  document.getElementById('ready-count').textContent = d.export_ready || 0;
}}
async function generateCSV() {{
  document.getElementById('csv-msg').innerHTML = '<div class="msg ok">Generating...</div>';
  const r = await fetch('/api/export/ebay-csv', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('csv-msg').innerHTML =
    `<div class="msg ok">${{d.message}}<br><small style="opacity:.7">${{d.path||''}}</small></div>`;
  loadStats();
}}
async function generateSheet() {{
  document.getElementById('sheet-msg').innerHTML = '<div class="msg ok">Generating...</div>';
  const r = await fetch('/api/export/master-sheet', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('sheet-msg').innerHTML =
    `<div class="msg ok">${{d.message}}<br><small style="opacity:.7">${{d.path||''}}</small></div>`;
}}
loadStats();
</script>
</body></html>"""


def _intake_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Intake Queue — Resale AI</title>
{_base_style()}
</head>
<body>
{_nav("intake")}
<main>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <h2>Intake Queue <span id="intake-count" style="color:#888780;font-weight:400"></span></h2>
  <button class="btn btn-purple" onclick="runWorker()">Run AI analysis on all</button>
</div>
<div id="worker-msg" style="margin-bottom:12px;font-size:13px"></div>
<table>
  <thead><tr>
    <th>SKU</th><th>Category</th><th>Images</th><th>Status</th><th>Action</th>
  </tr></thead>
  <tbody id="intake-body"><tr><td colspan="5" style="color:#888780">Loading...</td></tr></tbody>
</table>
</main>
<script>
async function load() {{
  const r = await fetch('/api/items?status=pending_intake&limit=200');
  const items = await r.json();
  document.getElementById('intake-count').textContent = `(${{items.length}})`;
  document.getElementById('intake-body').innerHTML = items.length
    ? items.map(it => {{
        const imgs = (it.image_paths||'').split('|').filter(Boolean).length;
        return `<tr>
          <td style="font-family:monospace">${{it.sku}}</td>
          <td>${{it.category_label||it.category_key||'-'}}</td>
          <td>${{imgs}}</td>
          <td><span class="badge pending_intake">pending_intake</span></td>
          <td><button class="btn btn-gray" style="font-size:11px;padding:4px 10px"
              onclick="analyzeOne('${{it.sku}}', this)">Analyze</button></td>
        </tr>`;
      }}).join('')
    : '<tr><td colspan="5" style="color:#5dcaa5">No pending items.</td></tr>';
}}
async function runWorker() {{
  document.getElementById('worker-msg').innerHTML = '<span style="color:#fac775">Starting worker...</span>';
  const r = await fetch('/api/items/process', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('worker-msg').innerHTML = `<span style="color:#5dcaa5">${{d.message}}</span>`;
  setTimeout(load, 3000);
}}
async function analyzeOne(sku, btn) {{
  btn.textContent = 'Running...';
  btn.disabled = true;
  const r = await fetch(`/api/items/${{sku}}/analyze`, {{method:'POST'}});
  const d = await r.json();
  btn.textContent = d.status || 'Done';
  load();
}}
load();
</script>
</body></html>"""
