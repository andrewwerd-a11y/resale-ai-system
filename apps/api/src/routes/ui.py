"""
Browser UI routes — server-rendered HTML pages.

/                → Dashboard
/review-queue    → Review queue (two-panel layout)
/inventory       → Full inventory browser
/export          → Export & publish page
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# Shared CSS/JS
# ---------------------------------------------------------------------------

_BASE_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1117; color: #e2e8f0; min-height: 100vh; }
a { color: #60a5fa; text-decoration: none; }
a:hover { text-decoration: underline; }
.nav { background: #1e2330; padding: 0.75rem 1.5rem; display: flex;
       align-items: center; gap: 2rem; border-bottom: 1px solid #2d3748; }
.nav-brand { font-weight: 700; font-size: 1.1rem; color: #fff; }
.nav a { color: #94a3b8; font-size: 0.9rem; }
.nav a:hover { color: #fff; text-decoration: none; }
.container { max-width: 1400px; margin: 0 auto; padding: 1.5rem; }
.card { background: #1e2330; border: 1px solid #2d3748; border-radius: 8px;
        padding: 1.25rem; }
.card-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
              color: #64748b; margin-bottom: 0.5rem; }
.card-value { font-size: 2rem; font-weight: 700; color: #fff; }
.grid { display: grid; gap: 1rem; }
.grid-2 { grid-template-columns: repeat(2, 1fr); }
.grid-3 { grid-template-columns: repeat(3, 1fr); }
.grid-4 { grid-template-columns: repeat(4, 1fr); }
.grid-5 { grid-template-columns: repeat(5, 1fr); }
.btn { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.5rem 1rem;
       border-radius: 6px; font-size: 0.875rem; cursor: pointer; border: none;
       font-weight: 500; transition: opacity 0.15s; }
.btn:hover { opacity: 0.85; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-success { background: #22c55e; color: #fff; }
.btn-danger  { background: #ef4444; color: #fff; }
.btn-warning { background: #f59e0b; color: #fff; }
.btn-secondary { background: #374151; color: #e2e8f0; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 9999px;
         font-size: 0.7rem; font-weight: 600; }
.badge-pending   { background: #374151; color: #94a3b8; }
.badge-review    { background: #92400e; color: #fcd34d; }
.badge-approved  { background: #14532d; color: #86efac; }
.badge-listed    { background: #1e3a5f; color: #93c5fd; }
.badge-sold      { background: #312e81; color: #a5b4fc; }
.badge-rejected  { background: #450a0a; color: #fca5a5; }
.badge-exported  { background: #065f46; color: #6ee7b7; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; padding: 0.6rem 0.75rem; background: #1a1f2e;
     color: #64748b; font-size: 0.75rem; text-transform: uppercase; border-bottom: 1px solid #2d3748; }
td { padding: 0.6rem 0.75rem; border-bottom: 1px solid #1a1f2e; color: #cbd5e1; vertical-align: middle; }
tr:hover td { background: #252d3d; }
input, select, textarea { background: #111827; color: #e2e8f0; border: 1px solid #374151;
                           border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.875rem;
                           width: 100%; }
input:focus, select:focus, textarea:focus { outline: none; border-color: #3b82f6; }
label { display: block; font-size: 0.75rem; color: #64748b; margin-bottom: 0.25rem; }
.section-title { font-size: 1.1rem; font-weight: 600; color: #fff; margin-bottom: 1rem; }
.text-sm { font-size: 0.8rem; color: #64748b; }
.mt-1 { margin-top: 0.5rem; }
.mt-2 { margin-top: 1rem; }
.mb-2 { margin-bottom: 1rem; }
.gap-2 { gap: 1rem; }
.flex { display: flex; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }
.w-full { width: 100%; }
.confidence-bar-wrap { background: #1a1f2e; border-radius: 9999px; height: 6px; overflow: hidden; }
.confidence-bar { height: 100%; border-radius: 9999px; }
.conf-high { background: #22c55e; }
.conf-mid  { background: #f59e0b; }
.conf-low  { background: #ef4444; }
"""

_NAV = """
<nav class="nav">
  <span class="nav-brand">Resale AI</span>
  <a href="/">Dashboard</a>
  <a href="/review-queue">Review Queue</a>
  <a href="/inventory">Inventory</a>
  <a href="/export">Export & Publish</a>
</nav>
"""


def _page(title: str, body: str, extra_css: str = "", extra_js: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Resale AI</title>
<style>{_BASE_CSS}{extra_css}</style>
</head>
<body>
{_NAV}
{body}
<script>{extra_js}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard():
    body = """
<div class="container">
  <div class="flex items-center justify-between mb-2" style="margin-bottom:1.5rem">
    <h1 style="font-size:1.5rem;font-weight:700">Dashboard</h1>
    <div class="flex gap-2" style="gap:0.75rem">
      <button class="btn btn-primary" onclick="runAnalysis()">Run AI Analysis</button>
      <button class="btn btn-warning" onclick="window.location='/review-queue'">Review Queue</button>
      <button class="btn btn-success" onclick="publishAll()">Publish All to eBay</button>
      <button class="btn btn-secondary" onclick="syncSold()">Sync Sold Orders</button>
    </div>
  </div>

  <div class="grid grid-5 mb-2" id="stat-cards" style="margin-bottom:1.5rem">
    <div class="card"><div class="card-title">Pending</div><div class="card-value" id="cnt-pending">—</div></div>
    <div class="card"><div class="card-title">Review Queue</div><div class="card-value" id="cnt-review">—</div></div>
    <div class="card"><div class="card-title">Approved / Ready</div><div class="card-value" id="cnt-ready">—</div></div>
    <div class="card"><div class="card-title">Listed on eBay</div><div class="card-value" id="cnt-listed">—</div></div>
    <div class="card"><div class="card-title">Sold</div><div class="card-value" id="cnt-sold">—</div></div>
  </div>

  <div class="card">
    <div class="section-title">Recent Items</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>SKU</th><th>Title</th><th>Category</th><th>Status</th>
            <th>Est. Price</th><th>Confidence</th><th>eBay</th>
          </tr>
        </thead>
        <tbody id="recent-items"></tbody>
      </table>
    </div>
  </div>

  <div id="toast" style="position:fixed;bottom:2rem;right:2rem;background:#1e2330;
       border:1px solid #374151;padding:1rem 1.5rem;border-radius:8px;
       display:none;z-index:999;color:#e2e8f0;min-width:250px;"></div>
</div>
"""

    js = """
function toast(msg, ok=true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = ok ? '#22c55e' : '#ef4444';
  el.style.display = 'block';
  setTimeout(() => el.style.display='none', 4000);
}

function statusBadge(s) {
  const map = {
    pending_intake:'badge-pending', sku_suggested:'badge-pending',
    sku_confirmed:'badge-pending', analyzed:'badge-pending',
    needs_review:'badge-review', approved:'badge-approved',
    export_ready:'badge-approved', exported:'badge-exported',
    listed:'badge-listed', sold:'badge-sold', rejected:'badge-rejected', archived:'badge-pending'
  };
  return `<span class="badge ${map[s]||'badge-pending'}">${s}</span>`;
}

async function loadStats() {
  const r = await fetch('/api/items/counts');
  const d = await r.json();
  const pending = (d.pending_intake||0)+(d.sku_suggested||0)+(d.sku_confirmed||0)+(d.analyzed||0);
  const ready = (d.approved||0)+(d.export_ready||0);
  document.getElementById('cnt-pending').textContent = pending;
  document.getElementById('cnt-review').textContent = d.needs_review||0;
  document.getElementById('cnt-ready').textContent = ready;
  document.getElementById('cnt-listed').textContent = d.listed||0;
  document.getElementById('cnt-sold').textContent = d.sold||0;
}

async function loadRecent() {
  const r = await fetch('/api/items?status=listed');
  const listed = await r.json();
  const r2 = await fetch('/api/items?status=approved');
  const approved = await r2.json();
  const r3 = await fetch('/api/items?status=needs_review');
  const review = await r3.json();
  const items = [...listed.slice(0,5), ...approved.slice(0,5), ...review.slice(0,5)].slice(0,20);

  const tbody = document.getElementById('recent-items');
  tbody.innerHTML = items.map(i => `
    <tr>
      <td><a href="/inventory#${i.sku}">${i.sku}</a></td>
      <td>${i.title||'—'}</td>
      <td>${i.category||'—'}</td>
      <td>${statusBadge(i.status)}</td>
      <td>${i.estimated_price ? '$'+i.estimated_price.toFixed(2) : '—'}</td>
      <td>${i.ai_confidence ? (i.ai_confidence*100).toFixed(0)+'%' : '—'}</td>
      <td>${i.ebay_listing_url ? `<a href="${i.ebay_listing_url}" target="_blank">View</a>` : '—'}</td>
    </tr>
  `).join('');
}

async function runAnalysis() {
  toast('AI analysis is run via: uv run python scripts/analyze_all.py');
}

async function publishAll() {
  if (!confirm('Publish all approved items to eBay?')) return;
  const r = await fetch('/api/ebay/publish/batch', {method:'POST'});
  const d = await r.json();
  toast(`Published: ${d.published}, Failed: ${d.failed}`, d.failed===0);
  loadStats(); loadRecent();
}

async function syncSold() {
  const r = await fetch('/api/ebay/sync-sold', {method:'POST'});
  const d = await r.json();
  if (d.error) { toast('Sync error: '+d.error, false); return; }
  toast(`Matched: ${d.matched}, Not found: ${d.not_found}`);
  loadStats();
}

loadStats();
loadRecent();
setInterval(() => { loadStats(); loadRecent(); }, 30000);
"""
    return HTMLResponse(_page("Dashboard", body, extra_js=js))


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------

_REVIEW_CSS = """
.rq-layout { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 52px); }
.rq-sidebar { background: #161b27; border-right: 1px solid #2d3748; overflow-y: auto; }
.rq-main { overflow-y: auto; padding: 1.5rem; }
.item-card { padding: 0.875rem 1rem; border-bottom: 1px solid #1a1f2e; cursor: pointer;
             transition: background 0.1s; }
.item-card:hover, .item-card.active { background: #1e2a3d; }
.item-card .sku { font-weight: 600; font-size: 0.85rem; color: #60a5fa; }
.item-card .ititle { font-size: 0.8rem; color: #94a3b8; margin-top: 0.2rem;
                     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.item-card .meta { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.35rem; }
.photo-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; margin-bottom: 1rem; }
.photo-grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 6px; cursor: pointer;
                   border: 2px solid transparent; transition: border-color 0.1s; }
.photo-grid img:hover { border-color: #3b82f6; }
.modal-bg { position:fixed; inset:0; background:rgba(0,0,0,0.85); z-index:1000;
            display:none; align-items:center; justify-content:center; }
.modal-bg.open { display:flex; }
.modal-img { max-width:90vw; max-height:90vh; border-radius:8px; }
.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.reason-tag { display:inline-block; background:#1a2744; color:#93c5fd;
              padding:0.2rem 0.5rem; border-radius:4px; font-size:0.7rem; margin:0.15rem; }
.action-row { display:flex; gap:0.75rem; margin-top:1.5rem; }
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center;
               height:100%; color:#4b5563; }
"""

_REVIEW_JS = """
let items = [];
let current = null;
let currentPhotos = [];

async function load() {
  const r = await fetch('/api/review/queue');
  items = await r.json();
  renderSidebar();
  if (items.length > 0) selectItem(0);
  else document.getElementById('detail').innerHTML = `
    <div class="empty-state">
      <div style="font-size:3rem">✓</div>
      <div style="margin-top:1rem;font-size:1.1rem">Review queue is empty</div>
    </div>`;
}

function confClass(c) {
  if (!c) return 'conf-low';
  if (c >= 0.72) return 'conf-high';
  if (c >= 0.5) return 'conf-mid';
  return 'conf-low';
}

function renderSidebar() {
  const list = document.getElementById('item-list');
  list.innerHTML = items.map((item, idx) => `
    <div class="item-card" id="card-${idx}" onclick="selectItem(${idx})">
      <div class="sku">${item.sku}</div>
      <div class="ititle">${item.title || 'No title'}</div>
      <div class="meta">
        <span style="font-size:0.7rem;color:#64748b">${item.category||'—'}</span>
        <span style="margin-left:auto;font-size:0.7rem;color:#94a3b8">
          ${item.ai_confidence ? (item.ai_confidence*100).toFixed(0)+'%' : '?'}
        </span>
      </div>
      <div class="confidence-bar-wrap mt-1">
        <div class="confidence-bar ${confClass(item.ai_confidence)}"
             style="width:${((item.ai_confidence||0)*100).toFixed(0)}%"></div>
      </div>
    </div>
  `).join('');
}

function selectItem(idx) {
  current = idx;
  document.querySelectorAll('.item-card').forEach(el => el.classList.remove('active'));
  const card = document.getElementById('card-'+idx);
  if (card) card.classList.add('active');
  renderDetail(items[idx]);
}

function renderDetail(item) {
  if (!item) return;
  const conf = item.ai_confidence || 0;
  const confPct = (conf*100).toFixed(0);
  const confCls = confClass(conf);

  const rawPaths = (item.hosted_photo_urls?.length ? item.hosted_photo_urls : item.image_paths) || [];
  // Build resolved src URLs and store in module-level array so openModal can reference by index
  currentPhotos = rawPaths.map(p =>
    p.startsWith('http') ? p : `/api/items/${item.sku}/image?path=${encodeURIComponent(p)}`
  );
  const photoHtml = currentPhotos.map((src, idx) =>
    `<img src="${src}" onclick="openModal(${idx})" alt="" loading="lazy">`
  ).join('');

  const reasons = (item.review_reasons||[]).map(r =>
    `<span class="reason-tag">${r}</span>`).join('');

  document.getElementById('detail').innerHTML = `
    <div>
      <div class="flex items-center justify-between mb-2">
        <div>
          <span style="font-size:1.2rem;font-weight:700">${item.sku}</span>
          <span style="color:#64748b;margin-left:0.5rem;font-size:0.9rem">${item.category||''}</span>
        </div>
        <div style="text-align:right">
          <div style="font-size:0.75rem;color:#64748b">AI Confidence</div>
          <div style="font-size:1.2rem;font-weight:700;color:${conf>=0.72?'#22c55e':conf>=0.5?'#f59e0b':'#ef4444'}">${confPct}%</div>
          <div class="confidence-bar-wrap" style="width:120px"><div class="confidence-bar ${confCls}" style="width:${confPct}%"></div></div>
        </div>
      </div>

      ${reasons ? `<div style="margin-bottom:1rem">${reasons}</div>` : ''}

      ${photos.length ? `<div class="photo-grid">${photoHtml}</div>` : '<div style="color:#4b5563;margin-bottom:1rem">No photos</div>'}

      <div class="field-grid" id="edit-fields">
        ${field('title','Title',item.title)}
        ${field('brand','Brand',item.brand)}
        ${field('item_type','Type',item.item_type)}
        ${field('department','Department',item.department)}
        ${field('size','Size',item.size)}
        ${field('color','Color',item.color)}
        ${field('material','Material',item.material)}
        ${field('style','Style',item.style)}
        ${conditionField(item.condition)}
        ${field('author','Author',item.author)}
        ${field('book_format','Format (books)',item.book_format)}
        ${field('franchise','Franchise',item.franchise)}
        ${field('character','Character',item.character)}
        ${numField('estimated_price','Est. Price ($)',item.estimated_price)}
        ${numField('list_price','List Price ($)',item.list_price)}
      </div>
      <div style="margin-top:0.75rem">
        ${field('condition_notes','Condition Notes',item.condition_notes)}
      </div>
      <div style="margin-top:0.75rem">
        ${field('notes','Notes',item.notes)}
      </div>

      <div class="action-row">
        <button class="btn btn-success" onclick="doAction('approve')">Approve</button>
        <button class="btn btn-primary" onclick="doAction('approve', true)">Save + Approve</button>
        <button class="btn btn-warning" onclick="publishItem()">Publish to eBay</button>
        <button class="btn btn-danger" onclick="doAction('reject')">Reject</button>
      </div>
    </div>
  `;
}

function field(name, label, val) {
  return `<div><label>${label}</label><input id="f-${name}" value="${escHtml(val||'')}" placeholder="${label}"></div>`;
}
function numField(name, label, val) {
  return `<div><label>${label}</label><input id="f-${name}" type="number" step="0.01" value="${val||''}" placeholder="${label}"></div>`;
}
function conditionField(val) {
  const opts = ['New','Like New','Excellent','Very Good','Good','Acceptable','Fair','Poor','For Parts'];
  const options = opts.map(o => `<option${o===val?' selected':''}>${o}</option>`).join('');
  return `<div><label>Condition</label><select id="f-condition">${options}</select></div>`;
}
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function getEdits() {
  const fields = ['title','brand','item_type','department','size','color','material','style',
                  'condition','author','book_format','franchise','character','condition_notes','notes'];
  const nums = ['estimated_price','list_price'];
  const body = {};
  fields.forEach(f => {
    const el = document.getElementById('f-'+f);
    if (el && el.value.trim()) body[f] = el.value.trim();
  });
  nums.forEach(f => {
    const el = document.getElementById('f-'+f);
    if (el && el.value) body[f] = parseFloat(el.value);
  });
  return body;
}

async function doAction(action, saveEdits=false) {
  const item = items[current];
  if (!item) return;
  const body = { action, ...(saveEdits ? getEdits() : {}) };
  const r = await fetch('/api/review/'+item.sku, { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (r.ok) {
    items.splice(current, 1);
    renderSidebar();
    if (items.length > 0) selectItem(Math.min(current, items.length-1));
    else document.getElementById('detail').innerHTML = `<div class="empty-state"><div style="font-size:3rem">✓</div><div style="margin-top:1rem">Queue empty!</div></div>`;
  } else {
    alert('Error: '+(await r.text()));
  }
}

async function publishItem() {
  const item = items[current];
  if (!item) return;
  // Save edits first, then publish
  const edits = getEdits();
  if (Object.keys(edits).length > 0) {
    await fetch('/api/items/'+item.sku, { method:'PATCH',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(edits) });
  }
  const r = await fetch('/api/ebay/publish/'+item.sku, {method:'POST'});
  const d = await r.json();
  if (r.ok) {
    alert('Published! Listing: '+d.listing_url);
    items.splice(current, 1);
    renderSidebar();
    if (items.length > 0) selectItem(Math.min(current, items.length-1));
  } else {
    alert('Publish failed: '+(d.detail||JSON.stringify(d)));
  }
}

function openModal(idx) {
  const src = typeof idx === 'number' ? currentPhotos[idx] : idx;
  document.getElementById('modal-img').src = src;
  document.getElementById('modal').classList.add('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal(); });
function closeModal() { document.getElementById('modal').classList.remove('open'); }

load();
"""


@router.get("/review-queue", response_class=HTMLResponse)
def review_queue_page():
    body = """
<div class="rq-layout">
  <div class="rq-sidebar">
    <div style="padding:0.75rem 1rem;border-bottom:1px solid #2d3748;font-size:0.8rem;color:#64748b">
      Review Queue — <span id="q-count">loading...</span>
    </div>
    <div id="item-list"></div>
  </div>
  <div class="rq-main" id="detail">
    <div class="empty-state" style="height:100%">
      <div style="color:#4b5563">Select an item to review</div>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal" onclick="closeModal()">
  <img class="modal-img" id="modal-img" src="" alt="">
</div>
"""
    extra_js = _REVIEW_JS + "\ndocument.getElementById('q-count').textContent = items.length + ' items';"
    return HTMLResponse(_page("Review Queue", body, extra_css=_REVIEW_CSS, extra_js=extra_js))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

_INV_CSS = """
.filter-bar { background: #1e2330; padding: 0.875rem 1.5rem; border-bottom: 1px solid #2d3748;
              display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
.filter-bar input, .filter-bar select { width: auto; min-width: 160px; }
"""

_INV_JS = """
let allItems = [];

async function load() {
  const r = await fetch('/api/items');
  allItems = await r.json();
  render(allItems);
}

function statusBadge(s) {
  const map = {
    pending_intake:'badge-pending', sku_suggested:'badge-pending',
    sku_confirmed:'badge-pending', analyzed:'badge-pending',
    needs_review:'badge-review', approved:'badge-approved',
    export_ready:'badge-approved', exported:'badge-exported',
    listed:'badge-listed', sold:'badge-sold', rejected:'badge-rejected'
  };
  return `<span class="badge ${map[s]||'badge-pending'}">${s.replace(/_/g,' ')}</span>`;
}

function render(items) {
  const tbody = document.getElementById('inv-tbody');
  tbody.innerHTML = items.map(i => `
    <tr id="${i.sku}">
      <td><strong>${i.sku}</strong></td>
      <td>${i.title ? i.title.substring(0,50)+(i.title.length>50?'…':'') : '—'}</td>
      <td>${i.category||'—'}</td>
      <td>${i.brand||'—'}</td>
      <td>${i.size||'—'}</td>
      <td>${i.condition||'—'}</td>
      <td>${statusBadge(i.status)}</td>
      <td>${i.ai_confidence ? (i.ai_confidence*100).toFixed(0)+'%' : '—'}</td>
      <td>${i.estimated_price ? '$'+i.estimated_price.toFixed(2) : '—'}</td>
      <td>${i.list_price ? '$'+i.list_price.toFixed(2) : '—'}</td>
      <td>${i.ebay_listing_url ? `<a href="${i.ebay_listing_url}" target="_blank">View ↗</a>` : '—'}</td>
    </tr>
  `).join('');
  document.getElementById('item-count').textContent = items.length + ' items';
}

function applyFilters() {
  const q = document.getElementById('search').value.toLowerCase();
  const status = document.getElementById('status-filter').value;
  const cat = document.getElementById('cat-filter').value;
  let filtered = allItems;
  if (q) filtered = filtered.filter(i =>
    (i.sku||'').toLowerCase().includes(q) ||
    (i.title||'').toLowerCase().includes(q) ||
    (i.brand||'').toLowerCase().includes(q));
  if (status) filtered = filtered.filter(i => i.status === status);
  if (cat) filtered = filtered.filter(i => i.category === cat);
  render(filtered);
}

document.getElementById('search').addEventListener('input', applyFilters);
document.getElementById('status-filter').addEventListener('change', applyFilters);
document.getElementById('cat-filter').addEventListener('change', applyFilters);
load();
"""


@router.get("/inventory", response_class=HTMLResponse)
def inventory_page():
    body = """
<div class="filter-bar">
  <input id="search" type="text" placeholder="Search SKU, title, brand...">
  <select id="status-filter">
    <option value="">All Statuses</option>
    <option value="pending_intake">Pending Intake</option>
    <option value="needs_review">Needs Review</option>
    <option value="approved">Approved</option>
    <option value="listed">Listed</option>
    <option value="sold">Sold</option>
    <option value="rejected">Rejected</option>
  </select>
  <select id="cat-filter">
    <option value="">All Categories</option>
    <option value="Books">Books</option>
    <option value="Clothing">Clothing</option>
    <option value="Collectibles">Collectibles</option>
    <option value="Shoes">Shoes</option>
    <option value="Toys">Toys</option>
  </select>
  <span id="item-count" class="text-sm"></span>
</div>
<div class="container" style="padding-top:1rem">
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>SKU</th><th>Title</th><th>Category</th><th>Brand</th>
          <th>Size</th><th>Condition</th><th>Status</th>
          <th>Confidence</th><th>Est. Price</th><th>List Price</th><th>eBay</th>
        </tr>
      </thead>
      <tbody id="inv-tbody"></tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Inventory", body, extra_css=_INV_CSS, extra_js=_INV_JS))


# ---------------------------------------------------------------------------
# Export & Publish
# ---------------------------------------------------------------------------

_EXPORT_JS = """
async function loadStatus() {
  const r = await fetch('/api/ebay/status');
  const d = await r.json();
  document.getElementById('ebay-env').textContent = d.environment;
  document.getElementById('ebay-conf').textContent = d.configured ? 'Configured' : 'Not configured';
  document.getElementById('ebay-conf').style.color = d.configured ? '#22c55e' : '#ef4444';
  document.getElementById('photo-host').textContent = d.photo_hosting_configured ? 'Imgur' : 'Not set';
  document.getElementById('photo-host').style.color = d.photo_hosting_configured ? '#22c55e' : '#f59e0b';
  const r2 = await fetch('/api/export/ready-count');
  const d2 = await r2.json();
  document.getElementById('ready-count').textContent = d2.count + ' items ready';
}

async function publishAll() {
  if (!confirm('Publish all approved items to eBay?')) return;
  document.getElementById('pub-btn').disabled = true;
  document.getElementById('pub-btn').textContent = 'Publishing...';
  const r = await fetch('/api/ebay/publish/batch', {method:'POST'});
  const d = await r.json();
  document.getElementById('pub-btn').disabled = false;
  document.getElementById('pub-btn').textContent = 'Publish to eBay';
  alert(`Done! Published: ${d.published}, Failed: ${d.failed}`);
}

async function syncSold() {
  document.getElementById('sync-btn').disabled = true;
  const r = await fetch('/api/ebay/sync-sold', {method:'POST'});
  const d = await r.json();
  document.getElementById('sync-btn').disabled = false;
  if (d.error) alert('Error: '+d.error);
  else alert(`Matched: ${d.matched} orders, Not found: ${d.not_found}`);
}

loadStatus();
"""


@router.get("/export", response_class=HTMLResponse)
def export_page():
    body = """
<div class="container">
  <h1 style="font-size:1.5rem;font-weight:700;margin-bottom:1.5rem">Export & Publish</h1>

  <div class="card mb-2" style="margin-bottom:1.5rem">
    <div class="section-title">eBay Connection</div>
    <div class="grid grid-4" style="gap:1rem;margin-top:0.75rem">
      <div>
        <div class="text-sm">Environment</div>
        <div style="font-weight:600;margin-top:0.25rem" id="ebay-env">—</div>
      </div>
      <div>
        <div class="text-sm">Status</div>
        <div style="font-weight:600;margin-top:0.25rem" id="ebay-conf">—</div>
      </div>
      <div>
        <div class="text-sm">Photo Hosting</div>
        <div style="font-weight:600;margin-top:0.25rem" id="photo-host">—</div>
      </div>
      <div>
        <div class="text-sm">Ready to Export</div>
        <div style="font-weight:600;margin-top:0.25rem" id="ready-count">—</div>
      </div>
    </div>
  </div>

  <div class="grid grid-2" style="gap:1rem">
    <div class="card">
      <div class="section-title">Publish to eBay</div>
      <p class="text-sm" style="margin-bottom:1rem">Publish all approved items directly to eBay via API. Photos are uploaded to Imgur automatically.</p>
      <button class="btn btn-success w-full" id="pub-btn" onclick="publishAll()">Publish to eBay</button>
    </div>

    <div class="card">
      <div class="section-title">eBay Bulk CSV</div>
      <p class="text-sm" style="margin-bottom:1rem">Download a CSV file formatted for eBay bulk listing upload tool.</p>
      <a class="btn btn-primary w-full" href="/api/export/ebay-csv">Download eBay CSV</a>
    </div>

    <div class="card">
      <div class="section-title">Master Inventory Sheet</div>
      <p class="text-sm" style="margin-bottom:1rem">Full inventory export with all fields. Available as CSV or Excel.</p>
      <div class="flex gap-2" style="gap:0.5rem">
        <a class="btn btn-secondary" style="flex:1" href="/api/export/master-csv">CSV</a>
        <a class="btn btn-secondary" style="flex:1" href="/api/export/master-excel">Excel</a>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Sync Sold Orders</div>
      <p class="text-sm" style="margin-bottom:1rem">Fetch recent eBay orders and update sold status, prices, and profits in the database.</p>
      <button class="btn btn-warning w-full" id="sync-btn" onclick="syncSold()">Sync Sold Orders</button>
    </div>
  </div>
</div>
"""
    return HTMLResponse(_page("Export & Publish", body, extra_js=_EXPORT_JS))
