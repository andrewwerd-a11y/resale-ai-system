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


@router.get("/bulk-approve", response_class=HTMLResponse)
async def bulk_approve_page():
    return HTMLResponse(_bulk_approve_html())


@router.get("/lots", response_class=HTMLResponse)
async def lots_page():
    return HTMLResponse(_lots_html())


@router.get("/reports", response_class=HTMLResponse)
async def reports_page():
    return HTMLResponse(_reports_html())


@router.get("/sourcing", response_class=HTMLResponse)
async def sourcing_page():
    return HTMLResponse(_sourcing_html())


@router.get("/capture", response_class=HTMLResponse)
async def capture_page():
    return HTMLResponse(_capture_html())


@router.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(_settings_html())


def _nav(active: str) -> str:
    pages = [
        ("Dashboard", "/", "dashboard"),
        ("Intake", "/intake", "intake"),
        ("Review Queue", "/review-queue", "review"),
        ("Bulk Approve", "/bulk-approve", "bulk"),
        ("Inventory", "/inventory", "inventory"),
        ("Lots", "/lots", "lots"),
        ("Reports", "/reports", "reports"),
        ("Sourcing", "/sourcing", "sourcing"),
        ("Capture", "/capture", "capture"),
        ("Export", "/export", "export"),
        ("Settings", "/settings", "settings"),
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


# ─── Shared detail-panel helpers ──────────────────────────────────────────────

def _detail_panel_style() -> str:
    return """<style>
.dp-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:200; }
.dp-overlay.open { display:block; }
.dp-panel { position:fixed; top:0; right:-440px; width:420px; height:100vh;
            background:#111110; border-left:1px solid #2c2c2a;
            overflow-y:auto; z-index:201; padding:16px;
            transition:right .25s ease; }
.dp-panel.open { right:0; }
.dp-imgs { display:grid; grid-template-columns:repeat(3,1fr); gap:4px; margin-bottom:12px; }
.dp-imgs img { width:100%; aspect-ratio:1; object-fit:cover; border-radius:4px;
               border:1px solid #2c2c2a; cursor:pointer; }
.dp-imgs img:hover { border-color:#7f77dd; }
.dp-field { margin-bottom:8px; }
.dp-field .dp-label { font-size:11px; color:#888780; margin-bottom:2px; }
.dp-field .dp-val   { font-size:13px; color:#f1efe8; }
.reason-tag { display:inline-block; background:#412402; color:#fac775;
              padding:2px 8px; border-radius:4px; font-size:11px; margin:2px; }
#dp-lightbox { display:none; position:fixed; inset:0; background:rgba(0,0,0,.92); z-index:300;
               align-items:center; justify-content:center; }
#dp-lightbox.open { display:flex; }
</style>"""


def _detail_panel_html() -> str:
    return """<div class="dp-overlay" id="dp-overlay" onclick="closePanel()"></div>
<div class="dp-panel" id="dp-panel">
  <div id="dp-inner" style="min-height:200px;color:#888780;font-size:13px">Select an item</div>
</div>
<div id="dp-lightbox" onclick="closeDpLb(event)">
  <div style="position:relative;display:inline-block">
    <img id="dp-lb-img" src="" style="max-width:90vw;max-height:85vh;object-fit:contain;border-radius:4px">
    <div id="dp-lb-ctr" style="position:absolute;top:8px;right:8px;background:rgba(0,0,0,.7);color:#fff;padding:3px 8px;border-radius:8px;font-size:12px"></div>
    <button onclick="event.stopPropagation();dpLbNav(-1)"
      style="position:absolute;top:50%;left:-52px;transform:translateY(-50%);background:rgba(255,255,255,.14);border:none;color:#fff;font-size:20px;padding:8px 12px;border-radius:6px;cursor:pointer">&#8592;</button>
    <button onclick="event.stopPropagation();dpLbNav(1)"
      style="position:absolute;top:50%;right:-52px;transform:translateY(-50%);background:rgba(255,255,255,.14);border:none;color:#fff;font-size:20px;padding:8px 12px;border-radius:6px;cursor:pointer">&#8594;</button>
  </div>
  <button onclick="event.stopPropagation();document.getElementById('dp-lightbox').classList.remove('open')"
    style="position:absolute;top:16px;right:18px;background:rgba(255,255,255,.14);border:none;color:#fff;font-size:16px;padding:5px 10px;border-radius:6px;cursor:pointer">&#10005;</button>
</div>"""


def _lightbox_html() -> str:
    """The lightbox for the detail panel is embedded in _detail_panel_html() — this is a no-op."""
    return ""


def _detail_panel_js() -> str:
    return """
var _dpPaths = [];
var _dpIdx = 0;

function renderDetailPanel(it) {
  const conf = it.confidence_score != null ? it.confidence_score : 0;
  const confPct = (conf * 100).toFixed(0);
  const confColor = conf >= 0.72 ? '#5dcaa5' : conf >= 0.50 ? '#fac775' : '#f09595';
  const reasons = (it.review_reasons || []).map(function(r) {
    return '<span class="reason-tag">' + r.replace(/_/g,' ') + '</span>';
  }).join('');

  const normPaths = Array.isArray(it.image_paths)
    ? it.image_paths
    : (it.image_paths || '').split('|').filter(Boolean);
  _dpPaths = normPaths.slice(0, 6);

  const imgs = _dpPaths.map(function(p, idx) {
    return '<img src="/api/items/' + it.sku + '/image?path=' + encodeURIComponent(p) + '"'
      + ' onerror="this.style.display=\'none\'"'
      + ' onclick="openDpLb(' + idx + ')" title="Click to enlarge" alt="photo">';
  }).join('');

  const enrichBadge = it.enrichment_done
    ? '<span style="background:#085041;color:#9fe1cb;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:6px">AI Enriched</span>'
    : '';
  const enrichNotes = it.enrichment_notes
    ? '<div style="background:#1a2a20;border:1px solid #2c4a30;border-radius:6px;padding:10px;margin:8px 0;font-size:12px;color:#9fe1cb;white-space:pre-wrap">' + it.enrichment_notes + '</div>'
    : '';

  const fieldDefs = [
    ['Title','title_final'],['Brand','brand'],['Type','type'],
    ['Department','department'],['Size','size'],['Color','color'],
    ['Material','material'],['Pattern','pattern'],['Style','style'],
    ['Condition','condition_label'],['Condition notes','condition_notes'],
    ['Defects','defects'],['Est. price','estimated_price'],
    ['List price','list_price'],['Author','author'],['Format','format'],
    ['Franchise','franchise'],['Character','character'],
  ];
  const fieldRows = fieldDefs.map(function(pair) {
    var label = pair[0], key = pair[1];
    var val = it[key];
    if (Array.isArray(val)) val = val.join(', ');
    if (val === null || val === undefined || val === '') return '';
    var disp = key.indexOf('price') >= 0 ? '$' + parseFloat(val).toFixed(2) : val;
    return '<div class="dp-field"><div class="dp-label">' + label + '</div>'
         + '<div class="dp-val">' + disp + '</div></div>';
  }).filter(Boolean).join('');

  document.getElementById('dp-inner').innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">'
    + '<div>'
    + '<div style="font-family:monospace;font-size:14px;color:#f1efe8">' + (it.sku || '-') + '</div>'
    + '<div style="font-size:11px;color:#888780;margin-top:2px">' + (it.category_label || it.category_key || '') + '</div>'
    + '</div>'
    + '<button onclick="closePanel()" style="background:none;border:none;color:#888780;font-size:18px;cursor:pointer;padding:0">&#10005;</button>'
    + '</div>'
    + '<div style="margin-bottom:8px"><span class="badge ' + (it.status || '') + '">' + (it.status || '') + '</span></div>'
    + (reasons ? '<div style="margin-bottom:10px">' + reasons + '</div>' : '')
    + '<div style="margin-bottom:10px">'
    + '<span style="font-size:12px;color:#888780">Confidence: </span>'
    + '<span style="font-size:12px;color:' + confColor + '">' + confPct + '%</span>'
    + enrichBadge + '</div>'
    + enrichNotes
    + (imgs ? '<div class="dp-imgs">' + imgs + '</div>' : '')
    + '<div style="border-top:1px solid #2c2c2a;padding-top:12px;margin-bottom:14px">' + fieldRows + '</div>'
    + '<div style="display:flex;gap:6px;flex-wrap:wrap;border-top:1px solid #2c2c2a;padding-top:12px">'
    + '<button class="btn btn-green"  style="font-size:12px;padding:5px 10px" onclick="detailAction(\'approve\',\'' + it.sku + '\')">Approve</button>'
    + '<button class="btn btn-gray"   style="font-size:12px;padding:5px 10px" onclick="detailAction(\'review\',\'' + it.sku + '\')">Send to review</button>'
    + '<button class="btn btn-purple" style="font-size:12px;padding:5px 10px" onclick="detailAction(\'publish\',\'' + it.sku + '\')">Publish to eBay</button>'
    + '<button class="btn btn-red"    style="font-size:12px;padding:5px 10px" onclick="detailAction(\'reject\',\'' + it.sku + '\')">Reject</button>'
    + '</div>';
}

function openDetail(it) {
  renderDetailPanel(it);
  document.getElementById('dp-overlay').classList.add('open');
  document.getElementById('dp-panel').classList.add('open');
}

function closePanel() {
  document.getElementById('dp-overlay').classList.remove('open');
  document.getElementById('dp-panel').classList.remove('open');
}

function openDpLb(idx) {
  _dpIdx = idx;
  _updateDpLb();
  document.getElementById('dp-lightbox').classList.add('open');
}
function closeDpLb(e) {
  if (e && e.currentTarget !== e.target) return;
  document.getElementById('dp-lightbox').classList.remove('open');
}
function dpLbNav(dir) {
  _dpIdx = (_dpIdx + dir + _dpPaths.length) % _dpPaths.length;
  _updateDpLb();
}
function _updateDpLb() {
  document.getElementById('dp-lb-img').src = '/api/items/x/image?path=' + encodeURIComponent(_dpPaths[_dpIdx]);
  document.getElementById('dp-lb-ctr').textContent = (_dpIdx + 1) + ' / ' + _dpPaths.length;
}
"""


def _review_queue_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Review Queue — Resale AI</title>
{_base_style()}
<style>
body {{ position: relative; }}
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
               border: 1px solid #2c2c2a; cursor: pointer; transition: border-color .15s; }}
.images img:hover {{ border-color: #7f77dd; }}
.reason-tag {{ display: inline-block; background: #412402; color: #fac775;
               padding: 2px 8px; border-radius: 4px; font-size: 11px; margin: 2px; }}
.actions {{ display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }}
.conf-bar {{ height: 4px; background: #2c2c2a; border-radius: 2px; margin-top: 4px; }}
.conf-fill {{ height: 100%; border-radius: 2px; }}
/* Lightbox — position:absolute so it works correctly inside an iframe */
#lightbox {{
  display: none; position: absolute; top: 0; left: 0; width: 100%; min-height: 100vh;
  background: rgba(0,0,0,0.92); z-index: 1000;
  align-items: center; justify-content: center;
}}
#lightbox.open {{ display: flex; }}
.lb-arrow {{
  position: absolute; top: 50%; transform: translateY(-50%);
  background: rgba(255,255,255,0.14); border: none; color: #fff;
  font-size: 22px; padding: 10px 15px; border-radius: 6px; cursor: pointer;
}}
.lb-arrow:hover {{ background: rgba(255,255,255,0.28); }}
#lb-close {{
  position: absolute; top: 18px; right: 20px;
  background: rgba(255,255,255,0.14); border: none; color: #fff;
  font-size: 18px; padding: 6px 12px; border-radius: 6px; cursor: pointer;
}}
#lb-close:hover {{ background: rgba(255,255,255,0.28); }}
</style>
</head>
<body>
{_nav("review")}
<main>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <h2>Review Queue <span id="review-count" style="color:#888780;font-weight:400"></span></h2>
  <button class="btn btn-green" onclick="approveAll()" style="font-size:12px">Approve all</button>
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

<!-- Fullscreen lightbox -->
<div id="lightbox" onclick="closeLightboxBg(event)">
  <div style="position:relative;display:inline-block">
    <img id="lb-img" src="" style="max-width:90vw;max-height:85vh;object-fit:contain;display:block;border-radius:4px">
    <div id="lb-counter" style="position:absolute;top:10px;right:10px;background:rgba(0,0,0,0.7);color:#fff;padding:4px 10px;border-radius:10px;font-size:13px">1 / 1</div>
    <button class="lb-arrow" style="left:-58px" onclick="event.stopPropagation();lbNav(-1)">&#8592;</button>
    <button class="lb-arrow" style="right:-58px" onclick="event.stopPropagation();lbNav(1)">&#8594;</button>
  </div>
  <button id="lb-close" onclick="event.stopPropagation();closeLightbox()">&#10005;</button>
</div>

<script>
let items = [];
let selected = null;
let currentItemPaths = [];
let currentItemSku = '';
let lbIdx = 0;

async function loadQueue() {{
  const r = await fetch('/api/review');
  items = await r.json();
  renderList();
  if (items.length > 0 && selected === null) selectItem(0);
}}

function renderList() {{
  const list = document.getElementById('item-list');
  document.getElementById('review-count').textContent = `(${{items.length}})`;
  if (!items.length) {{
    list.innerHTML = '<div style="color:#5dcaa5;font-size:13px;padding:20px;text-align:center">No items need review. All clear!</div>';
    return;
  }}
  list.innerHTML = items.map((it, i) => {{
    const conf = it.confidence_score != null ? (it.confidence_score * 100).toFixed(0) + '%' : '?';
    const reasons = (it.review_reasons || []).join(', ') || 'flagged';
    const isSel = selected !== null && i === selected;
    return `<div class="item-card${{isSel ? ' selected' : ''}}" id="card-${{i}}" onclick="selectItem(${{i}})">
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
  renderList();
  renderDetail(items[i]);
  setTimeout(() => {{
    const card = document.getElementById('card-' + i);
    if (card) card.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
  }}, 40);
}}

function renderDetail(it) {{
  const conf = it.confidence_score != null ? it.confidence_score : 0;
  const confPct = (conf * 100).toFixed(0);
  const confColor = conf >= 0.72 ? '#5dcaa5' : conf >= 0.50 ? '#fac775' : '#f09595';
  const reasons = (it.review_reasons || []).map(r =>
    `<span class="reason-tag">${{r.replace(/_/g,' ')}}</span>`).join('');

  const normPaths = Array.isArray(it.image_paths)
    ? it.image_paths
    : (it.image_paths || '').split('|').filter(Boolean);

  // Store globally so lightbox can access them
  currentItemPaths = normPaths.slice(0, 6);
  currentItemSku = it.sku;

  const imgs = currentItemPaths.map((p, idx) =>
    `<img src="/api/items/${{it.sku}}/image?path=${{encodeURIComponent(p)}}"
          onerror="this.style.display='none'"
          onclick="openLightbox(${{idx}})"
          title="Click to enlarge"
          alt="item photo">`
  ).join('');

  const enrichBadge = it.enrichment_done
    ? '<span style="background:#085041;color:#9fe1cb;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:8px">AI Enriched</span>'
    : '';
  const enrichNotes = it.enrichment_notes
    ? `<div style="background:#1a2a20;border:1px solid #2c4a30;border-radius:6px;padding:10px;margin-bottom:12px;font-size:12px;color:#9fe1cb;white-space:pre-wrap">${{it.enrichment_notes}}</div>`
    : '';

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
      ${{enrichBadge}}
      <div class="conf-bar"><div class="conf-fill" style="width:${{confPct}}%;background:${{confColor}}"></div></div>
    </div>
    ${{enrichNotes}}
    <div class="images">${{imgs}}</div>
    <div id="edit-fields">${{fieldRows}}</div>
    <div class="field-row">
      <label>Notes</label>
      <textarea id="field-notes" rows="2" onchange="markEdited('notes', this.value)">${{it.notes || ''}}</textarea>
    </div>
    <div class="actions">
      <button class="btn btn-green" onclick="approve('${{it.sku}}')">Approve</button>
      <button class="btn btn-purple" onclick="editAndApprove('${{it.sku}}')">Save + approve</button>
      <button class="btn btn-red" onclick="reject('${{it.sku}}')">Reject</button>
    </div>
    <div id="action-msg"></div>
  `;
}}

let edits = {{}};
function markEdited(key, val) {{
  edits[key] = val.replace(/^\\$/, '');
}}

async function approve(sku) {{
  const r = await fetch(`/api/review/${{sku}}/approve`, {{method: 'POST'}});
  if (r.ok) {{ showMsg('Approved.', 'ok'); removeItem(sku); }}
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

async function approveAll() {{
  if (!items.length) return;
  if (!confirm(`Approve all ${{items.length}} items in the review queue?`)) return;
  for (const it of [...items]) {{
    await fetch(`/api/review/${{it.sku}}/approve`, {{method: 'POST'}});
  }}
  items = [];
  selected = null;
  renderList();
  document.getElementById('detail-panel').innerHTML =
    '<div style="color:#5dcaa5;font-size:13px;margin-top:40px;text-align:center">All items approved!</div>';
}}

function showMsg(text, type) {{
  const el = document.getElementById('action-msg');
  if (el) {{ el.className = 'msg ' + type; el.textContent = text; }}
}}

function removeItem(sku) {{
  const prevIdx = selected || 0;
  items = items.filter(i => i.sku !== sku);
  if (items.length === 0) {{
    selected = null;
    renderList();
    document.getElementById('detail-panel').innerHTML =
      '<div style="color:#5dcaa5;font-size:13px;margin-top:40px;text-align:center">All caught up!</div>';
    return;
  }}
  const nextIdx = Math.min(prevIdx, items.length - 1);
  selected = nextIdx;
  renderList();
  renderDetail(items[nextIdx]);
  setTimeout(() => {{
    const card = document.getElementById('card-' + nextIdx);
    if (card) card.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
  }}, 40);
}}

// ── Lightbox ──────────────────────────────────────────────────────────────────
function openLightbox(idx) {{
  if (!currentItemPaths.length) return;
  lbIdx = idx;
  updateLightbox();
  document.getElementById('lightbox').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeLightbox() {{
  document.getElementById('lightbox').classList.remove('open');
  document.body.style.overflow = '';
}}

function closeLightboxBg(e) {{
  if (e.target === document.getElementById('lightbox')) closeLightbox();
}}

function lbNav(dir) {{
  if (!currentItemPaths.length) return;
  lbIdx = (lbIdx + dir + currentItemPaths.length) % currentItemPaths.length;
  updateLightbox();
}}

function updateLightbox() {{
  const p = currentItemPaths[lbIdx];
  document.getElementById('lb-img').src =
    `/api/items/${{currentItemSku}}/image?path=${{encodeURIComponent(p)}}`;
  document.getElementById('lb-counter').textContent = `${{lbIdx + 1}} / ${{currentItemPaths.length}}`;
}}

document.addEventListener('keydown', e => {{
  if (!document.getElementById('lightbox').classList.contains('open')) return;
  if (e.key === 'ArrowLeft')  lbNav(-1);
  else if (e.key === 'ArrowRight') lbNav(1);
  else if (e.key === 'Escape') closeLightbox();
}});

loadQueue();
</script>
</body></html>"""


def _inventory_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Inventory — Resale AI</title>
{_base_style()}
{_detail_panel_style()}
</head>
<body>
{_nav("inventory")}
<main>
<div style="display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap">
  <h2 style="margin:0">Inventory</h2>
  <input type="text" id="search" placeholder="Search SKU, title, brand..."
         style="width:240px" oninput="filterItems()">
  <select id="status-filter" onchange="filterItems()" style="width:150px">
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
  <select id="conf-filter" onchange="filterItems()" style="width:170px">
    <option value="">All confidence</option>
    <option value="high">High (&ge;85%)</option>
    <option value="medium">Medium (72&#8209;84%)</option>
    <option value="low">Low (&lt;72%)</option>
  </select>
  <button class="btn btn-green" onclick="bulkApproveHighConf()" style="font-size:12px;padding:5px 12px">
    Bulk approve high confidence
  </button>
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
{_detail_panel_html()}
{_lightbox_html()}
<script>
let allItems = [];
let itemMap = {{}};
{_detail_panel_js()}
async function load() {{
  const r = await fetch('/api/items?limit=500');
  allItems = await r.json();
  itemMap = {{}};
  allItems.forEach(function(it) {{ if (it.sku) itemMap[it.sku] = it; }});
  const params = new URLSearchParams(window.location.search);
  const initStatus = params.get('status') || '';
  const initConf = params.get('conf') || '';
  if (initStatus) document.getElementById('status-filter').value = initStatus;
  if (initConf) document.getElementById('conf-filter').value = initConf;
  filterItems();
}}
function filterItems() {{
  const q = document.getElementById('search').value.toLowerCase();
  const st = document.getElementById('status-filter').value;
  const cf = document.getElementById('conf-filter').value;
  const filtered = allItems.filter(it => {{
    const matchQ = !q || (it.sku||'').toLowerCase().includes(q)
      || (it.title_final||'').toLowerCase().includes(q)
      || (it.brand||'').toLowerCase().includes(q);
    const matchSt = !st || it.status === st;
    const conf = it.confidence_score || 0;
    const matchCf = !cf
      || (cf === 'high'   && conf >= 0.85)
      || (cf === 'medium' && conf >= 0.72 && conf < 0.85)
      || (cf === 'low'    && conf < 0.72);
    return matchQ && matchSt && matchCf;
  }});
  document.getElementById('count').textContent = filtered.length + ' items';
  document.getElementById('inv-body').innerHTML = filtered.map(it => {{
    const conf = it.confidence_score;
    const confPct = conf != null ? (conf * 100).toFixed(0) + '%' : '-';
    const confColor = conf == null ? '' : conf >= 0.85 ? 'color:#5dcaa5' : conf >= 0.72 ? 'color:#fac775' : 'color:#f09595';
    const ep = it.estimated_price != null ? '$' + parseFloat(it.estimated_price).toFixed(2) : '-';
    const lp = it.list_price != null ? '$' + parseFloat(it.list_price).toFixed(2) : '-';
    return `<tr style="cursor:pointer" onclick="openDetail(itemMap['${{it.sku}}'])">
      <td style="font-family:monospace">${{it.sku||'-'}}${{it.lot_group_id ? '<span style="background:#412402;color:#fac775;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:4px">LOT</span>' : ''}}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${{(it.title_final||it.title_raw||'-').slice(0,60)}}</td>
      <td>${{it.category_label||'-'}}</td>
      <td>${{it.brand||'-'}}</td>
      <td>${{it.size||'-'}}</td>
      <td>${{it.condition_label||'-'}}</td>
      <td><span class="badge ${{it.status||''}}">${{it.status||'-'}}</span></td>
      <td style="${{confColor}}">${{confPct}}</td>
      <td>${{ep}}</td>
      <td>${{lp}}</td>
    </tr>`;
  }}).join('');
}}
async function bulkApproveHighConf() {{
  const targets = allItems.filter(it =>
    (it.confidence_score || 0) >= 0.85 &&
    ['analyzed','approved','needs_review'].includes(it.status)
  );
  if (!targets.length) {{ alert('No high confidence items found to approve.'); return; }}
  if (!confirm(`Approve ${{targets.length}} high confidence items?`)) return;
  const r = await fetch('/api/items/bulk-approve', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{skus: targets.map(it => it.sku)}})
  }});
  const d = await r.json();
  alert(`Approved ${{d.updated}} items.`);
  load();
}}
async function detailAction(action, sku) {{
  if (action === 'approve') {{
    await fetch('/api/items/bulk-approve', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'review') {{
    await fetch('/api/items/bulk-review', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'reject') {{
    await fetch('/api/items/bulk-reject', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'publish') {{
    await fetch(`/api/ebay/publish/${{sku}}`, {{method:'POST'}});
  }}
  closePanel(); load();
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
    <th>SKU</th><th>Category</th><th>Images</th><th>Cost ($)</th><th>Status</th><th>Action</th>
  </tr></thead>
  <tbody id="intake-body"><tr><td colspan="6" style="color:#888780">Loading...</td></tr></tbody>
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
        const margin = it.estimated_price && it.cost
          ? ' (~' + Math.round((it.estimated_price - it.cost) / it.estimated_price * 100) + '% margin)'
          : '';
        return `<tr>
          <td style="font-family:monospace">${{it.sku}}</td>
          <td>${{it.category_label||it.category_key||'-'}}</td>
          <td>${{imgs}}</td>
          <td><input type="number" min="0" step="0.01"
              style="width:72px;padding:3px 6px;background:#2c2c2a;border:1px solid #3a3a38;color:#f1efe8;border-radius:4px;font-size:12px"
              value="${{it.cost||''}}"
              placeholder="0.00"
              onblur="saveCost('${{it.sku}}', this)"
              onkeydown="if(event.key==='Enter')this.blur()">
            <span style="font-size:11px;color:#888780">${{margin}}</span></td>
          <td><span class="badge pending_intake">pending_intake</span></td>
          <td><button class="btn btn-gray" style="font-size:11px;padding:4px 10px"
              onclick="analyzeOne('${{it.sku}}', this)">Analyze</button></td>
        </tr>`;
      }}).join('')
    : '<tr><td colspan="6" style="color:#5dcaa5">No pending items.</td></tr>';
}}
async function runWorker() {{
  document.getElementById('worker-msg').innerHTML = '<span style="color:#fac775">Starting worker...</span>';
  const r = await fetch('/api/items/process', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('worker-msg').innerHTML = `<span style="color:#5dcaa5">${{d.message}}</span>`;
  setTimeout(load, 3000);
}}
async function saveCost(sku, input) {{
  const val = parseFloat(input.value);
  if (isNaN(val) || val < 0) return;
  input.style.borderColor = '#fac775';
  await fetch(`/api/items/${{sku}}/cost`, {{
    method: 'PATCH',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{cost: val}})
  }});
  input.style.borderColor = '#5dcaa5';
  setTimeout(() => {{ input.style.borderColor = ''; }}, 1500);
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


def _bulk_approve_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Bulk Approve — Resale AI</title>
{_base_style()}
{_detail_panel_style()}
<style>
.filter-bar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }}
.filter-bar label {{ display:inline; margin:0; color:#888780; font-size:12px; }}
.slider-wrap {{ display:flex; align-items:center; gap:6px; }}
input[type=range] {{ width:130px; accent-color:#7f77dd; cursor:pointer; }}
th input[type=checkbox], td input[type=checkbox] {{ width:auto; cursor:pointer; }}
.conf-high   {{ color:#5dcaa5; }}
.conf-medium {{ color:#fac775; }}
.conf-low    {{ color:#f09595; }}
.action-bar {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }}
</style>
</head>
<body>
{_nav("bulk")}
<main>
<h2 style="margin-bottom:14px">Bulk Approve</h2>

<div class="filter-bar">
  <div class="slider-wrap">
    <label>Min confidence:</label>
    <input type="range" id="conf-slider" min="0" max="100" value="72"
           oninput="document.getElementById('conf-val').textContent=this.value+'%'; applyFilters()">
    <span id="conf-val" style="font-size:12px;color:#f1efe8;min-width:38px">72%</span>
  </div>
  <select id="cat-filter" onchange="applyFilters()" style="width:170px">
    <option value="">All categories</option>
  </select>
  <select id="status-filter" onchange="applyFilters()" style="width:160px">
    <option value="">All statuses</option>
    <option value="pending_intake">Pending intake</option>
    <option value="analyzed">Analyzed</option>
    <option value="needs_review">Needs review</option>
    <option value="approved">Approved</option>
    <option value="export_ready">Export ready</option>
    <option value="exported">Exported</option>
    <option value="rejected">Rejected</option>
  </select>
  <span id="filter-count" style="font-size:12px;color:#888780"></span>
</div>

<div class="action-bar">
  <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#d4d2c8;cursor:pointer">
    <input type="checkbox" id="select-all" onchange="toggleSelectAll(this)"> Select all visible
  </label>
  <span id="sel-count" style="font-size:12px;color:#888780"></span>
  <button class="btn btn-green" onclick="bulkAction('bulk-approve')">Approve selected</button>
  <button class="btn btn-gray"  onclick="bulkAction('bulk-review')">Send to review</button>
  <button class="btn btn-red"   onclick="bulkAction('bulk-reject')">Reject selected</button>
  <div id="bulk-msg" style="font-size:13px"></div>
</div>

<table>
  <thead><tr>
    <th style="width:32px"></th>
    <th>SKU</th><th>Title</th><th>Category</th>
    <th>Confidence</th><th>Est. Price</th><th>List Price</th><th>Status</th>
  </tr></thead>
  <tbody id="bulk-body"><tr><td colspan="8" style="color:#888780">Loading...</td></tr></tbody>
</table>
</main>
{_detail_panel_html()}
{_lightbox_html()}
<script>
let allItems = [];
let filteredItems = [];
let itemMap = {{}};
{_detail_panel_js()}

async function load() {{
  const r = await fetch('/api/items?limit=500');
  allItems = await r.json();
  itemMap = {{}};
  allItems.forEach(function(it) {{ if (it.sku) itemMap[it.sku] = it; }});
  const cats = [...new Set(allItems.map(it => it.category_label).filter(Boolean))].sort();
  const catSel = document.getElementById('cat-filter');
  while (catSel.options.length > 1) catSel.remove(1);
  cats.forEach(c => {{
    const o = document.createElement('option');
    o.value = c; o.textContent = c;
    catSel.appendChild(o);
  }});
  applyFilters();
}}

function applyFilters() {{
  const threshold = parseFloat(document.getElementById('conf-slider').value) / 100;
  const cat    = document.getElementById('cat-filter').value;
  const status = document.getElementById('status-filter').value;
  filteredItems = allItems.filter(it => {{
    const conf = it.confidence_score || 0;
    return conf >= threshold
      && (!cat    || it.category_label === cat)
      && (!status || it.status === status);
  }});
  document.getElementById('filter-count').textContent = filteredItems.length + ' items match';
  renderTable();
}}

function confClass(conf) {{
  if (conf >= 0.85) return 'conf-high';
  if (conf >= 0.72) return 'conf-medium';
  return 'conf-low';
}}

function renderTable() {{
  document.getElementById('select-all').checked = false;
  document.getElementById('bulk-body').innerHTML = filteredItems.map(it => {{
    const conf = it.confidence_score || 0;
    const confPct = (conf * 100).toFixed(0) + '%';
    const ep = it.estimated_price != null ? '$' + parseFloat(it.estimated_price).toFixed(2) : '-';
    const lp = it.list_price != null ? '$' + parseFloat(it.list_price).toFixed(2) : '-';
    return `<tr style="cursor:pointer">
      <td onclick="event.stopPropagation()"><input type="checkbox" class="item-cb" value="${{it.sku}}" onchange="updateSelCount()"></td>
      <td style="font-family:monospace;font-size:12px" onclick="openDetail(itemMap['${{it.sku}}'])">${{it.sku||'-'}}${{it.lot_group_id ? '<span style="background:#412402;color:#fac775;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:4px">LOT</span>' : ''}}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" onclick="openDetail(itemMap['${{it.sku}}'])">
        ${{(it.title_final||it.title_raw||'-').slice(0,55)}}</td>
      <td onclick="openDetail(itemMap['${{it.sku}}'])">${{it.category_label||'-'}}</td>
      <td class="${{confClass(conf)}}" onclick="openDetail(itemMap['${{it.sku}}'])">${{confPct}}</td>
      <td onclick="openDetail(itemMap['${{it.sku}}'])">${{ep}}</td>
      <td onclick="openDetail(itemMap['${{it.sku}}'])">${{lp}}</td>
      <td onclick="openDetail(itemMap['${{it.sku}}'])"><span class="badge ${{it.status||''}}">${{it.status||'-'}}</span></td>
    </tr>`;
  }}).join('') || '<tr><td colspan="8" style="color:#888780">No items match filters.</td></tr>';
  updateSelCount();
}}

function updateSelCount() {{
  const checked = document.querySelectorAll('.item-cb:checked').length;
  const total   = filteredItems.length;
  document.getElementById('sel-count').textContent = `${{checked}} of ${{total}} selected`;
}}

function toggleSelectAll(cb) {{
  document.querySelectorAll('.item-cb').forEach(c => c.checked = cb.checked);
  updateSelCount();
}}

async function bulkAction(action) {{
  const skus = [...document.querySelectorAll('.item-cb:checked')].map(c => c.value);
  if (!skus.length) {{ showMsg('No items selected.', 'err'); return; }}
  const r = await fetch(`/api/items/${{action}}`, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{skus}})
  }});
  const d = await r.json();
  showMsg(`Updated ${{d.updated}} items.`, 'ok');
  load();
}}

function showMsg(text, type) {{
  const el = document.getElementById('bulk-msg');
  el.className = 'msg ' + type;
  el.textContent = text;
  setTimeout(() => {{ el.textContent = ''; el.className = ''; }}, 3000);
}}

async function detailAction(action, sku) {{
  if (action === 'approve') {{
    await fetch('/api/items/bulk-approve', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'review') {{
    await fetch('/api/items/bulk-review', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'reject') {{
    await fetch('/api/items/bulk-reject', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{skus:[sku]}})}});
  }} else if (action === 'publish') {{
    await fetch(`/api/ebay/publish/${{sku}}`, {{method:'POST'}});
  }}
  closePanel(); load();
}}

load();
</script>
</body></html>"""


# ─── /lots ────────────────────────────────────────────────────────────────────

def _lots_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Lots — Resale AI</title>
{_base_style()}
<style>
.lot-item {{ background:#222220;border:1px solid #2c2c2a;border-radius:6px;
             padding:10px 14px;margin-bottom:6px;display:flex;align-items:center;gap:10px;cursor:pointer; }}
.lot-item:hover {{ border-color:#534ab7; }}
.lot-item.selected {{ border-color:#7f77dd;background:#26215c22; }}
</style>
</head>
<body>
{_nav("lots")}
<main>
<div style="display:grid;grid-template-columns:1fr 340px;gap:20px;max-width:1200px">
  <div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h2 style="margin:0">Lot Builder</h2>
      <span id="sel-count" style="font-size:12px;color:#888780"></span>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <input type="text" id="search" placeholder="Search SKU or title..." style="width:220px" oninput="filterItems()">
      <select id="cat-filter" onchange="filterItems()" style="width:160px">
        <option value="">All categories</option>
      </select>
      <button class="btn btn-gray" style="font-size:12px" onclick="clearSelection()">Clear selection</button>
    </div>
    <div id="item-list"><div style="color:#888780;font-size:13px">Loading...</div></div>
  </div>

  <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px;height:fit-content">
    <h2 style="margin-bottom:14px">Create Lot</h2>
    <div class="field-row">
      <label>Lot title</label>
      <input type="text" id="lot-title" placeholder="e.g. 3 Men's Dress Shirts Bundle">
    </div>
    <div class="field-row">
      <label>Combined price ($)</label>
      <input type="number" id="lot-price" min="0" step="0.01" placeholder="0.00">
    </div>
    <div class="field-row">
      <label>Selected items (<span id="count-badge">0</span>)</label>
      <div id="selected-list" style="font-size:12px;color:#888780;min-height:40px;background:#1a1a18;border:1px solid #2c2c2a;border-radius:6px;padding:8px">
        None selected
      </div>
    </div>
    <button class="btn btn-purple" style="width:100%;margin-top:4px" onclick="createLot()">Create Lot Listing</button>
    <div id="lot-msg" style="margin-top:10px;font-size:13px"></div>

    <div style="border-top:1px solid #2c2c2a;margin-top:20px;padding-top:16px">
      <h2 style="margin-bottom:12px">Existing Lots</h2>
      <div id="lots-list" style="font-size:12px;color:#888780">Loading...</div>
    </div>
  </div>
</div>
</main>
<script>
let allItems = [];
let selectedSkus = new Set();

async function load() {{
  const r = await fetch('/api/items?limit=500');
  allItems = await r.json();
  const cats = [...new Set(allItems.map(it => it.category_label).filter(Boolean))].sort();
  const catSel = document.getElementById('cat-filter');
  while (catSel.options.length > 1) catSel.remove(1);
  cats.forEach(c => {{ const o = document.createElement('option'); o.value=c; o.textContent=c; catSel.appendChild(o); }});
  filterItems();
  loadExistingLots();
}}

async function loadExistingLots() {{
  const r = await fetch('/api/lots');
  const lots = await r.json();
  const div = document.getElementById('lots-list');
  if (!lots.length) {{ div.textContent = 'No lots yet.'; return; }}
  div.innerHTML = lots.map(lot => `
    <div style="background:#1a1a18;border:1px solid #2c2c2a;border-radius:6px;padding:10px;margin-bottom:8px">
      <div style="font-family:monospace;font-size:11px;color:#f1efe8">${{lot.sku}}</div>
      <div style="color:#d4d2c8;margin-top:2px;font-size:12px">${{lot.title_final||'-'}}</div>
      <div style="color:#5dcaa5;margin-top:2px;font-size:12px">${{lot.list_price ? '$' + parseFloat(lot.list_price).toFixed(2) : '-'}}</div>
      <button class="btn btn-red" style="font-size:11px;padding:3px 8px;margin-top:6px"
        onclick="dissolveLot('${{lot.sku}}')">Dissolve</button>
    </div>`).join('');
}}

function filterItems() {{
  const q = document.getElementById('search').value.toLowerCase();
  const cat = document.getElementById('cat-filter').value;
  // Show ALL items except sold/rejected — lot grouping is metadata, not a location
  const eligible = allItems.filter(it =>
    !['sold','rejected'].includes(it.status) &&
    (!q || (it.sku||'').toLowerCase().includes(q) || (it.title_final||'').toLowerCase().includes(q)) &&
    (!cat || it.category_label === cat)
  );
  document.getElementById('item-list').innerHTML = eligible.length
    ? eligible.map(it => {{
        const isSel = selectedSkus.has(it.sku);
        const lotBadge = it.lot_group_id
          ? `<span style="background:#412402;color:#fac775;padding:1px 5px;border-radius:3px;font-size:10px;margin-left:4px" title="In lot: ${{it.lot_group_id}}">LOT</span>`
          : '';
        return `<div class="lot-item${{isSel ? ' selected' : ''}}" onclick="toggleSelect('${{it.sku}}')">
          <input type="checkbox" style="width:auto" ${{isSel ? 'checked' : ''}} onclick="event.stopPropagation();toggleSelect('${{it.sku}}')">
          <span style="font-family:monospace;font-size:12px;color:#f1efe8;min-width:100px">${{it.sku}}${{lotBadge}}</span>
          <span style="font-size:12px;color:#888780;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{it.title_final||it.title_raw||'-'}}</span>
          <span style="font-size:12px;color:#5dcaa5;min-width:60px;text-align:right">${{it.estimated_price ? '$' + parseFloat(it.estimated_price).toFixed(2) : '-'}}</span>
        </div>`;
      }}).join('')
    : '<div style="color:#888780;font-size:13px">No items found.</div>';
  updateSelectionUI();
}}

function toggleSelect(sku) {{
  if (selectedSkus.has(sku)) selectedSkus.delete(sku); else selectedSkus.add(sku);
  filterItems();
}}

function clearSelection() {{ selectedSkus.clear(); filterItems(); }}

function updateSelectionUI() {{
  const skus = [...selectedSkus];
  document.getElementById('count-badge').textContent = skus.length;
  document.getElementById('sel-count').textContent = skus.length ? skus.length + ' items selected' : '';
  document.getElementById('selected-list').innerHTML = skus.length
    ? skus.map(s => `<span style="display:inline-block;background:#2c2c2a;border-radius:4px;padding:2px 6px;margin:2px;font-family:monospace;font-size:11px">${{s}}</span>`).join('')
    : 'None selected';
}}

async function createLot() {{
  const title = document.getElementById('lot-title').value.trim();
  const price = parseFloat(document.getElementById('lot-price').value) || 0;
  const skus = [...selectedSkus];
  if (!title) {{ showMsg('Enter a lot title.', 'err'); return; }}
  if (skus.length < 2) {{ showMsg('Select at least 2 items.', 'err'); return; }}
  const r = await fetch('/api/lots/create', {{
    method: 'POST', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{skus, title, price}})
  }});
  const d = await r.json();
  if (d.lot_sku) {{
    showMsg('Lot created: ' + d.lot_sku, 'ok');
    selectedSkus.clear();
    document.getElementById('lot-title').value = '';
    document.getElementById('lot-price').value = '';
    load();
  }} else {{ showMsg(d.detail || 'Error creating lot', 'err'); }}
}}

async function dissolveLot(sku) {{
  if (!confirm('Dissolve lot ' + sku + '? Member items will return to approved status.')) return;
  await fetch('/api/lots/dissolve/' + sku, {{method:'POST'}});
  load();
}}

function showMsg(text, type) {{
  const el = document.getElementById('lot-msg');
  el.className = 'msg ' + type; el.textContent = text;
  setTimeout(() => {{ el.textContent=''; el.className=''; }}, 4000);
}}

load();
</script>
</body></html>"""


# ─── /reports ─────────────────────────────────────────────────────────────────

def _reports_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Reports — Resale AI</title>
{_base_style()}
<style>
.s-card {{ background:#2c2c2a;border:1px solid #3a3a38;border-radius:8px;padding:16px; }}
.s-card .num {{ font-size:26px;font-weight:500;color:#f1efe8;margin-bottom:4px; }}
.s-card .lbl {{ font-size:12px;color:#888780; }}
.s-card.green .num {{ color:#5dcaa5; }}
.s-card.purple .num {{ color:#afa9ec; }}
.s-card.orange .num {{ color:#fac775; }}
.bar-chart {{ display:flex; align-items:flex-end; gap:6px; height:100px; }}
.bar-wrap {{ display:flex; flex-direction:column; align-items:center; flex:1; min-width:0; }}
.bar {{ background:#534ab7; border-radius:3px 3px 0 0; min-height:2px; width:100%; }}
.bar-label {{ font-size:9px; color:#888780; margin-top:3px; text-align:center; white-space:nowrap;
              overflow:hidden; text-overflow:ellipsis; max-width:100%; }}
.bar-val {{ font-size:9px; color:#afa9ec; margin-bottom:2px; }}
</style>
</head>
<body>
{_nav("reports")}
<main>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:8px">
  <h2 style="margin:0">Sales Reports</h2>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input type="date" id="date-from" onchange="loadSales()" style="width:140px">
    <span style="color:#888780;font-size:12px">to</span>
    <input type="date" id="date-to" onchange="loadSales()" style="width:140px">
    <select id="platform-filter" onchange="loadSales()" style="width:130px">
      <option value="">All platforms</option>
      <option value="ebay">eBay</option>
      <option value="poshmark">Poshmark</option>
      <option value="mercari">Mercari</option>
    </select>
    <button class="btn btn-gray" style="font-size:12px" onclick="clearFilters()">Clear</button>
    <button class="btn btn-green" style="font-size:12px" onclick="exportCSV()">Export CSV</button>
  </div>
</div>

<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px" id="summary-cards">
  <div class="s-card"><div class="num">...</div><div class="lbl">Loading...</div></div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">
  <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:16px">
    <h2 style="margin-bottom:10px">Monthly Revenue</h2>
    <div class="bar-chart" id="monthly-chart">
      <div style="color:#888780;font-size:12px">No data</div>
    </div>
  </div>
  <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:16px">
    <h2 style="margin-bottom:10px">By Platform</h2>
    <div id="platform-breakdown" style="font-size:13px;color:#888780">Loading...</div>
  </div>
</div>

<h2 style="margin-bottom:12px">Sales History <span id="sale-count" style="color:#888780;font-weight:400"></span></h2>
<table>
  <thead><tr>
    <th>SKU</th><th>Platform</th><th>Sold Price</th>
    <th>Cost</th><th>Fees</th><th>Net Profit</th><th>Margin %</th><th>Date Sold</th>
  </tr></thead>
  <tbody id="sales-body"><tr><td colspan="8" style="color:#888780">Loading...</td></tr></tbody>
</table>
</main>
<script>
async function loadSummary() {{
  const r = await fetch('/api/reports/summary');
  const d = await r.json();
  document.getElementById('summary-cards').innerHTML = `
    <div class="s-card green"><div class="num">$${{(d.total_revenue||0).toFixed(2)}}</div><div class="lbl">Total Revenue</div></div>
    <div class="s-card green"><div class="num">$${{(d.total_gross_profit||0).toFixed(2)}}</div><div class="lbl">Gross Profit</div></div>
    <div class="s-card purple"><div class="num">$${{(d.total_net_profit||0).toFixed(2)}}</div><div class="lbl">Net Profit</div></div>
    <div class="s-card orange"><div class="num">${{((d.avg_net_margin||0)*100).toFixed(1)}}%</div><div class="lbl">Avg Net Margin</div></div>
    <div class="s-card"><div class="num">${{d.total_sales||0}}</div><div class="lbl">Total Sales</div></div>
  `;
}}

async function loadMonthly() {{
  const r = await fetch('/api/reports/by-month');
  const months = await r.json();
  const chart = document.getElementById('monthly-chart');
  if (!months.length) {{ chart.innerHTML = '<div style="color:#888780;font-size:12px">No sales yet</div>'; return; }}
  const recent = months.slice(-12);
  const maxRev = Math.max(...recent.map(m => m.revenue), 1);
  chart.innerHTML = recent.map(m => {{
    const h = Math.max(4, Math.round((m.revenue / maxRev) * 90));
    return `<div class="bar-wrap">
      <div class="bar-val">$${{m.revenue.toFixed(0)}}</div>
      <div class="bar" style="height:${{h}}px"></div>
      <div class="bar-label">${{m.month.slice(5)}}</div>
    </div>`;
  }}).join('');
}}

async function loadPlatforms() {{
  const r = await fetch('/api/reports/by-platform');
  const platforms = await r.json();
  const div = document.getElementById('platform-breakdown');
  if (!platforms.length) {{ div.textContent = 'No sales yet.'; return; }}
  div.innerHTML = platforms.map(p => `
    <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1e1e1c;font-size:12px">
      <span style="color:#f1efe8;min-width:70px">${{p.platform}}</span>
      <span style="color:#5dcaa5">$${{p.revenue.toFixed(2)}}</span>
      <span style="color:#888780">${{p.sales}} sold</span>
      <span style="color:#afa9ec">$${{p.net_profit.toFixed(2)}} net</span>
    </div>`).join('');
}}

async function loadSales() {{
  const from = document.getElementById('date-from').value;
  const to = document.getElementById('date-to').value;
  const platform = document.getElementById('platform-filter').value;
  let url = '/api/reports/sales?limit=500';
  if (from) url += '&date_from=' + from;
  if (to) url += '&date_to=' + to;
  if (platform) url += '&platform=' + platform;
  const r = await fetch(url);
  const sales = await r.json();
  document.getElementById('sale-count').textContent = '(' + sales.length + ')';
  document.getElementById('sales-body').innerHTML = sales.length
    ? sales.map(s => {{
        const ds = s.date_sold ? s.date_sold.slice(0,10) : '-';
        const margin = s.net_margin != null ? (s.net_margin*100).toFixed(1)+'%' : '-';
        const mColor = (s.net_margin||0) >= 0.3 ? 'color:#5dcaa5' : (s.net_margin||0) >= 0.1 ? 'color:#fac775' : 'color:#f09595';
        return `<tr>
          <td style="font-family:monospace;font-size:12px">${{s.sku}}</td>
          <td>${{s.platform}}</td>
          <td style="color:#5dcaa5">$${{s.sold_price.toFixed(2)}}</td>
          <td>${{s.cost!=null ? '$${{s.cost.toFixed(2)}}' : '-'}}</td>
          <td>${{s.fees ? '$${{s.fees.toFixed(2)}}' : '-'}}</td>
          <td style="${{mColor}}">$${{s.net_profit.toFixed(2)}}</td>
          <td style="${{mColor}}">${{margin}}</td>
          <td style="font-size:12px;color:#888780">${{ds}}</td>
        </tr>`;
      }}).join('')
    : '<tr><td colspan="8" style="color:#888780">No sales recorded yet.</td></tr>';
}}

async function exportCSV() {{
  const r = await fetch('/api/reports/export-csv', {{method:'POST'}});
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'sales_report.csv';
  a.click();
}}

function clearFilters() {{
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  document.getElementById('platform-filter').value = '';
  loadSales();
}}

loadSummary(); loadMonthly(); loadPlatforms(); loadSales();
</script>
</body></html>"""


# ─── /sourcing ────────────────────────────────────────────────────────────────

def _sourcing_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Sourcing — Resale AI</title>
{_base_style()}
</head>
<body>
{_nav("sourcing")}
<main>
<div style="display:grid;grid-template-columns:360px 1fr;gap:20px;max-width:1200px">

  <div>
    <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px;margin-bottom:16px">
      <h2 style="margin-bottom:14px">New Sourcing Batch</h2>
      <div class="field-row">
        <label>Label</label>
        <input type="text" id="b-label" placeholder="e.g. Estate sale — Main St — March 2026">
      </div>
      <div class="field-row">
        <label>Total cost ($)</label>
        <input type="number" id="b-cost" min="0" step="0.01" placeholder="0.00" oninput="updateCostPerItem()">
      </div>
      <div class="field-row">
        <label>Item count</label>
        <input type="number" id="b-count" min="1" placeholder="0" oninput="updateCostPerItem()">
      </div>
      <div class="field-row">
        <label>Date</label>
        <input type="date" id="b-date">
      </div>
      <div class="field-row">
        <label>Location (optional)</label>
        <input type="text" id="b-location" placeholder="e.g. 123 Main St">
      </div>
      <div style="margin-bottom:12px;padding:10px;background:#1a1a18;border-radius:6px;border:1px solid #2c2c2a">
        <span style="font-size:12px;color:#888780">Cost per item: </span>
        <span style="font-size:16px;font-weight:500;color:#5dcaa5" id="cost-per-item">-</span>
      </div>
      <button class="btn btn-purple" style="width:100%" onclick="createBatch()">Create Batch</button>
      <div id="batch-msg" style="margin-top:8px;font-size:13px"></div>
    </div>

    <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px">
      <h2 style="margin-bottom:12px">Batches</h2>
      <div id="batch-list" style="font-size:13px;color:#888780">Loading...</div>
    </div>
  </div>

  <div>
    <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px;margin-bottom:16px">
      <h2 style="margin-bottom:14px">Assign Batch Cost to Items</h2>
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
        <select id="assign-batch" style="flex:1;min-width:200px" onchange="updateAssignBatchInfo()">
          <option value="">Select a batch...</option>
        </select>
        <input type="text" id="assign-search" placeholder="Search SKU or title" style="flex:1;min-width:160px" oninput="filterAssignItems()">
      </div>
      <div id="assign-batch-info" style="font-size:12px;color:#5dcaa5;margin-bottom:8px"></div>
      <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#d4d2c8;cursor:pointer;margin:0">
          <input type="checkbox" id="assign-all" style="width:auto" onchange="toggleAssignAll(this)"> Select all
        </label>
        <span id="assign-count" style="font-size:12px;color:#888780"></span>
        <button class="btn btn-green" style="font-size:12px;margin-left:auto" onclick="assignBatch()">Assign to selected</button>
      </div>
      <div id="assign-item-list" style="max-height:300px;overflow-y:auto;border:1px solid #2c2c2a;border-radius:6px;padding:8px">
        <div style="color:#888780;font-size:12px">Select a batch above, then choose items to assign.</div>
      </div>
      <div id="assign-msg" style="margin-top:8px;font-size:13px"></div>
    </div>

    <div style="background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px">
      <h2 style="margin-bottom:14px">Set Individual Item Cost</h2>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
        <div style="flex:1;min-width:120px">
          <label>SKU</label>
          <input type="text" id="ind-sku" placeholder="Enter SKU">
        </div>
        <div style="width:120px">
          <label>Cost ($)</label>
          <input type="number" id="ind-cost" min="0" step="0.01" placeholder="0.00">
        </div>
        <div style="width:180px">
          <label>Location (optional)</label>
          <input type="text" id="ind-location" placeholder="Optional">
        </div>
        <button class="btn btn-purple" style="padding:6px 14px;white-space:nowrap;margin-bottom:0" onclick="setIndividualCost()">Set Cost</button>
      </div>
      <div id="ind-msg" style="margin-top:8px;font-size:13px"></div>
    </div>
  </div>
</div>
</main>
<script>
let allItems = [];
let batches = [];

async function load() {{
  const [itemsR, batchesR] = await Promise.all([
    fetch('/api/items?limit=500'),
    fetch('/api/sourcing/batches'),
  ]);
  allItems = await itemsR.json();
  batches = await batchesR.json();
  renderBatchList();
  populateBatchSelect();
  filterAssignItems();
}}

function renderBatchList() {{
  const div = document.getElementById('batch-list');
  if (!batches.length) {{ div.textContent = 'No batches yet.'; return; }}
  div.innerHTML = batches.map(b => `
    <div style="background:#1a1a18;border:1px solid #2c2c2a;border-radius:6px;padding:12px;margin-bottom:8px">
      <div style="font-size:13px;color:#f1efe8;margin-bottom:4px">${{b.label}}</div>
      <div style="font-size:12px;color:#888780;display:flex;gap:16px;flex-wrap:wrap">
        <span>Total: <span style="color:#f1efe8">$${{b.total_cost.toFixed(2)}}</span></span>
        <span>Items: <span style="color:#f1efe8">${{b.item_count}}</span></span>
        <span>Per item: <span style="color:#5dcaa5">$${{b.cost_per_item.toFixed(2)}}</span></span>
        <span>Date: <span style="color:#f1efe8">${{(b.sourcing_date||'').slice(0,10)||'-'}}</span></span>
        ${{b.location ? '<span>Loc: <span style="color:#f1efe8">'+b.location+'</span></span>' : ''}}
      </div>
    </div>`).join('');
}}

function populateBatchSelect() {{
  const sel = document.getElementById('assign-batch');
  while (sel.options.length > 1) sel.remove(1);
  batches.forEach(b => {{
    const o = document.createElement('option');
    o.value = b.batch_id;
    o.textContent = b.label + ' ($' + b.cost_per_item.toFixed(2) + '/item)';
    sel.appendChild(o);
  }});
}}

function updateAssignBatchInfo() {{
  const batchId = document.getElementById('assign-batch').value;
  const b = batches.find(x => x.batch_id === batchId);
  document.getElementById('assign-batch-info').textContent = b ? 'Cost per item: $' + b.cost_per_item.toFixed(2) : '';
}}

function filterAssignItems() {{
  const q = document.getElementById('assign-search').value.toLowerCase();
  const eligible = allItems.filter(it =>
    !['sold','rejected','lot_member'].includes(it.status) &&
    (!q || (it.sku||'').toLowerCase().includes(q) || (it.title_final||'').toLowerCase().includes(q))
  );
  const div = document.getElementById('assign-item-list');
  div.innerHTML = eligible.length
    ? eligible.map(it => `
      <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #1e1e1c">
        <input type="checkbox" class="assign-cb" value="${{it.sku}}" style="width:auto" onchange="updateAssignCount()">
        <span style="font-family:monospace;font-size:11px;color:#f1efe8;min-width:90px">${{it.sku}}</span>
        <span style="font-size:12px;color:#888780;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{it.title_final||it.title_raw||'-'}}</span>
        <span style="font-size:12px;min-width:60px;text-align:right;color:${{it.cost ? '#5dcaa5' : '#888780'}}">${{it.cost ? '$${{it.cost.toFixed(2)}}' : 'no cost'}}</span>
      </div>`).join('')
    : '<div style="color:#888780;font-size:12px">No items found.</div>';
  updateAssignCount();
}}

function updateAssignCount() {{
  const n = document.querySelectorAll('.assign-cb:checked').length;
  document.getElementById('assign-count').textContent = n + ' selected';
}}

function toggleAssignAll(cb) {{
  document.querySelectorAll('.assign-cb').forEach(c => c.checked = cb.checked);
  updateAssignCount();
}}

function updateCostPerItem() {{
  const cost = parseFloat(document.getElementById('b-cost').value) || 0;
  const count = parseInt(document.getElementById('b-count').value) || 0;
  document.getElementById('cost-per-item').textContent = count > 0 ? '$' + (cost / count).toFixed(2) : '-';
}}

async function createBatch() {{
  const label = document.getElementById('b-label').value.trim();
  const cost  = parseFloat(document.getElementById('b-cost').value) || 0;
  const count = parseInt(document.getElementById('b-count').value) || 0;
  const date  = document.getElementById('b-date').value || new Date().toISOString().slice(0,10);
  const loc   = document.getElementById('b-location').value.trim();
  if (!label || cost <= 0 || count < 1) {{ showMsg('batch-msg', 'Fill in label, cost, and item count.', 'err'); return; }}
  const r = await fetch('/api/sourcing/batch', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{label, total_cost:cost, item_count:count, sourcing_date:date, location:loc||null}})
  }});
  const d = await r.json();
  if (d.batch_id) {{
    showMsg('batch-msg', 'Batch created: ' + d.label, 'ok');
    ['b-label','b-cost','b-count','b-date','b-location'].forEach(id => document.getElementById(id).value='');
    document.getElementById('cost-per-item').textContent='-';
    load();
  }} else {{ showMsg('batch-msg', d.detail || 'Error', 'err'); }}
}}

async function assignBatch() {{
  const batchId = document.getElementById('assign-batch').value;
  if (!batchId) {{ showMsg('assign-msg', 'Select a batch first.', 'err'); return; }}
  const skus = [...document.querySelectorAll('.assign-cb:checked')].map(c => c.value);
  if (!skus.length) {{ showMsg('assign-msg', 'Select at least one item.', 'err'); return; }}
  const r = await fetch('/api/sourcing/assign/'+batchId, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{skus}})
  }});
  const d = await r.json();
  showMsg('assign-msg', 'Assigned cost to ' + d.assigned + ' items.', 'ok');
  load();
}}

async function setIndividualCost() {{
  const sku = document.getElementById('ind-sku').value.trim();
  const cost = parseFloat(document.getElementById('ind-cost').value);
  const loc = document.getElementById('ind-location').value.trim();
  if (!sku || isNaN(cost) || cost < 0) {{ showMsg('ind-msg', 'Enter a valid SKU and cost.', 'err'); return; }}
  const r = await fetch('/api/sourcing/item/'+sku, {{
    method:'PATCH', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{cost, sourcing_location: loc||null}})
  }});
  const d = await r.json();
  if (d.sku) {{ showMsg('ind-msg', sku + ' cost set to $' + d.cost.toFixed(2), 'ok'); }}
  else {{ showMsg('ind-msg', d.detail || 'Item not found', 'err'); }}
}}

function showMsg(id, text, type) {{
  const el = document.getElementById(id);
  el.className = 'msg ' + type; el.textContent = text;
  setTimeout(() => {{ el.textContent=''; el.className=''; }}, 4000);
}}

load();
</script>
</body></html>"""


# ─── /capture ─────────────────────────────────────────────────────────────────

def _capture_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Capture Station — Resale AI</title>
{_base_style()}
<style>
.status-dot {{ display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px; }}
.status-dot.ok {{ background:#5dcaa5; }}
.status-dot.err {{ background:#f09595; }}
.status-dot.warn {{ background:#fac775; }}
.hw-card {{ background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px; }}
</style>
</head>
<body>
{_nav("capture")}
<main>
<h2 style="margin-bottom:20px">Capture Station</h2>

<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin-bottom:24px">
  <div class="hw-card">
    <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:10px">Camera</div>
    <div id="camera-status" style="font-size:13px"><span class="status-dot err"></span>Checking...</div>
    <div style="font-size:12px;color:#888780;margin-top:8px">Hardware stub — ready for gphoto2 / DigiCamControl</div>
  </div>
  <div class="hw-card">
    <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:10px">Label Printer</div>
    <div id="printer-status" style="font-size:13px"><span class="status-dot err"></span>Checking...</div>
    <div style="font-size:12px;color:#888780;margin-top:8px">Hardware stub — ready for Dymo / Zebra / brother_ql</div>
  </div>
  <div class="hw-card">
    <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:10px">File Watcher</div>
    <div id="watcher-status" style="font-size:13px"><span class="status-dot warn"></span>Checking...</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-green" style="font-size:12px" onclick="startWatcher()">Start</button>
      <button class="btn btn-red" style="font-size:12px" onclick="stopWatcher()">Stop</button>
    </div>
  </div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:860px">
  <div class="hw-card">
    <h2 style="margin-bottom:14px">Watch Folder</h2>
    <div class="field-row">
      <label>Folder path</label>
      <input type="text" id="watch-folder" placeholder="./intake/pending">
    </div>
    <button class="btn btn-purple" style="font-size:12px" onclick="startWatcherWithFolder()">Start watching this folder</button>
    <div id="folder-msg" style="margin-top:8px;font-size:13px"></div>
  </div>

  <div class="hw-card">
    <h2 style="margin-bottom:14px">Quality Thresholds</h2>
    <div class="field-row">
      <label>Blur threshold (min: <span id="blur-val">100</span>)</label>
      <input type="range" id="blur-slider" min="10" max="500" value="100" style="width:100%;padding:0"
             oninput="document.getElementById('blur-val').textContent=this.value">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      <div class="field-row">
        <label>Min photos / item</label>
        <input type="number" id="min-photos" min="1" max="20" value="3">
      </div>
      <div class="field-row">
        <label>Ideal photos / item</label>
        <input type="number" id="ideal-photos" min="1" max="20" value="5">
      </div>
    </div>
    <div style="font-size:11px;color:#888780;margin-top:6px">Applied when hardware is connected.</div>
  </div>

  <div class="hw-card">
    <h2 style="margin-bottom:14px">Print Test Label</h2>
    <div class="field-row">
      <label>SKU</label>
      <input type="text" id="test-sku" placeholder="Enter SKU">
    </div>
    <div class="field-row">
      <label>Title (optional)</label>
      <input type="text" id="test-title" placeholder="Item title">
    </div>
    <button class="btn btn-gray" style="font-size:12px" onclick="printTestLabel()">Print Label</button>
    <div id="print-msg" style="margin-top:8px;font-size:13px"></div>
  </div>
</div>
</main>
<script>
async function loadStatus() {{
  try {{
    const r = await fetch('/api/capture/status');
    const d = await r.json();
    document.getElementById('camera-status').innerHTML = d.camera_connected
      ? '<span class="status-dot ok"></span>Connected'
      : '<span class="status-dot err"></span>Not connected (stub)';
    document.getElementById('printer-status').innerHTML = d.printer_connected
      ? '<span class="status-dot ok"></span>Connected'
      : '<span class="status-dot err"></span>Not connected (stub)';
    document.getElementById('watcher-status').innerHTML = d.watcher_running
      ? '<span class="status-dot ok"></span>Running'
      : '<span class="status-dot warn"></span>Stopped';
  }} catch(e) {{}}
}}

async function startWatcher() {{
  const r = await fetch('/api/capture/watcher/start', {{method:'POST'}});
  const d = await r.json();
  loadStatus();
  showMsg('folder-msg', d.ok ? 'Watcher started on ' + (d.watching||'default folder') : (d.message||'Error'), d.ok ? 'ok' : 'err');
}}

async function startWatcherWithFolder() {{
  const folder = document.getElementById('watch-folder').value.trim();
  const url = '/api/capture/watcher/start' + (folder ? '?watch_folder='+encodeURIComponent(folder) : '');
  const r = await fetch(url, {{method:'POST'}});
  const d = await r.json();
  loadStatus();
  showMsg('folder-msg', d.ok ? 'Watching: '+(d.watching||folder) : (d.message||'Error'), d.ok ? 'ok' : 'err');
}}

async function stopWatcher() {{
  await fetch('/api/capture/watcher/stop', {{method:'POST'}});
  loadStatus();
}}

async function printTestLabel() {{
  const sku = document.getElementById('test-sku').value.trim();
  const title = document.getElementById('test-title').value.trim();
  if (!sku) {{ showMsg('print-msg', 'Enter a SKU', 'err'); return; }}
  const r = await fetch('/api/capture/print-label/'+sku+'?title='+encodeURIComponent(title), {{method:'POST'}});
  const d = await r.json();
  showMsg('print-msg', d.ok ? 'Label sent to printer' : (d.error||'Printer not connected'), d.ok ? 'ok' : 'err');
}}

function showMsg(id, text, type) {{
  const el = document.getElementById(id);
  el.className = 'msg ' + type; el.textContent = text;
  setTimeout(() => {{ el.textContent=''; el.className=''; }}, 4000);
}}

loadStatus();
setInterval(loadStatus, 10000);
</script>
</body></html>"""


# ─── /settings ────────────────────────────────────────────────────────────────

def _settings_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Settings — Resale AI</title>
{_base_style()}
<style>
.settings-section {{ background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px;margin-bottom:16px; }}
.settings-section h3 {{ font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:14px;
                        padding-bottom:8px;border-bottom:1px solid #2c2c2a; }}
.settings-grid {{ display:grid;grid-template-columns:1fr 1fr;gap:12px 24px; }}
.restart-banner {{ display:none;background:#412402;color:#fac775;border:1px solid #8b5e0a;
                   border-radius:6px;padding:10px 16px;margin-bottom:16px;font-size:13px; }}
.restart-banner.show {{ display:block; }}
input[type=range] {{ accent-color:#7f77dd;cursor:pointer; }}
</style>
</head>
<body>
{_nav("settings")}
<main style="max-width:900px">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <h2 style="margin:0">Settings</h2>
  <button class="btn btn-purple" onclick="saveAll()">Save Changes</button>
</div>
<div class="restart-banner" id="restart-banner">
  Changes saved. Restart the server for .env changes to take effect.
</div>

<div class="settings-section">
  <h3>Vision Model</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Default vision model</label>
      <select id="vision-model">
        <option value="minicpm-v">minicpm-v (recommended)</option>
        <option value="qwen2.5vl:7b">qwen2.5vl:7b</option>
        <option value="llama3.2-vision:11b">llama3.2-vision:11b</option>
      </select>
    </div>
    <div class="field-row">
      <label>Enrichment model</label>
      <input type="text" value="claude-sonnet-4-20250514" disabled style="opacity:.5">
      <span style="font-size:11px;color:#888780">Fixed — do not change</span>
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>Enrichment</h3>
  <div class="field-row">
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
      <input type="checkbox" id="enrichment-enabled" style="width:auto">
      <span style="font-size:13px;color:#d4d2c8">Enable AI enrichment (Claude API)</span>
    </label>
  </div>
</div>

<div class="settings-section">
  <h3>Triage Thresholds</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Confidence review threshold (<span id="conf-threshold-val">72</span>%)</label>
      <input type="range" id="conf-threshold" min="0" max="100" value="72" style="width:100%;padding:0"
             oninput="document.getElementById('conf-threshold-val').textContent=this.value">
    </div>
    <div class="field-row">
      <label>High value threshold ($)</label>
      <input type="number" id="high-value-threshold" min="0" step="1" style="width:120px">
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>Pricing Rules</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Stale listing days</label>
      <input type="number" id="stale-days" min="1" step="1" style="width:120px">
    </div>
    <div class="field-row">
      <label>Price drop % on stale</label>
      <input type="number" id="stale-drop" min="0" max="100" step="1" style="width:120px">
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>eBay Policies</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Shipping policy name</label>
      <input type="text" id="ebay-shipping">
    </div>
    <div class="field-row">
      <label>Return policy name</label>
      <input type="text" id="ebay-return">
    </div>
    <div class="field-row">
      <label>Payment policy name</label>
      <input type="text" id="ebay-payment">
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>Notifications</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="notif-enabled" style="width:auto">
        <span style="font-size:13px;color:#d4d2c8">Enable email notifications</span>
      </label>
    </div>
    <div class="field-row">
      <label>Notify email</label>
      <input type="email" id="notif-email" placeholder="you@example.com">
    </div>
    <div class="field-row">
      <label>SMTP host</label>
      <input type="text" id="smtp-host" placeholder="smtp.gmail.com">
    </div>
    <div class="field-row">
      <label>SMTP port</label>
      <input type="number" id="smtp-port" value="587" style="width:120px">
    </div>
  </div>
  <div style="font-size:12px;color:#888780;margin-top:8px">SMTP credentials are set in .env — restart required after changes.</div>
</div>

<div class="settings-section">
  <h3>Platform Toggles</h3>
  <div id="platform-toggles" style="color:#888780;font-size:13px">Loading...</div>
</div>
</main>
<script>
async function loadSettings() {{
  try {{
    const [rCurrent, rRules, rPlatforms] = await Promise.all([
      fetch('/api/settings/current'),
      fetch('/api/settings/rules'),
      fetch('/api/settings/platforms'),
    ]);
    const current = await rCurrent.json();
    const rules = await rRules.json();
    const platforms = await rPlatforms.json();

    document.getElementById('vision-model').value = current.vision_model_default || 'minicpm-v';
    document.getElementById('enrichment-enabled').checked = !!current.enrichment_enabled;
    document.getElementById('notif-enabled').checked = !!current.notifications_enabled;
    document.getElementById('notif-email').value = current.notify_email || '';
    document.getElementById('smtp-host').value = current.smtp_host || '';
    document.getElementById('smtp-port').value = current.smtp_port || 587;

    const confPct = Math.round((current.confidence_review_threshold || 0.72) * 100);
    document.getElementById('conf-threshold').value = confPct;
    document.getElementById('conf-threshold-val').textContent = confPct;
    document.getElementById('high-value-threshold').value = current.high_value_review_threshold || 75;

    const pricing = rules.pricing || {{}};
    document.getElementById('stale-days').value = pricing.stale_listing_days || 60;
    document.getElementById('stale-drop').value = pricing.price_drop_percent || 10;

    const ebay = rules.ebay || {{}};
    document.getElementById('ebay-shipping').value = ebay.default_shipping_policy || '';
    document.getElementById('ebay-return').value = ebay.default_return_policy || '';
    document.getElementById('ebay-payment').value = ebay.default_payment_policy || '';

    const ptDiv = document.getElementById('platform-toggles');
    ptDiv.innerHTML = Object.entries(platforms).map(([key, cfg]) => `
      <div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #1e1e1c">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin:0;flex:1">
          <input type="checkbox" style="width:auto" ${{cfg.active ? 'checked' : ''}}
                 onchange="togglePlatform('${{key}}', this.checked)">
          <span style="font-size:13px;color:#d4d2c8">${{cfg.label}}</span>
        </label>
        <span style="font-size:11px;color:#888780">${{cfg.note || (cfg.end_listing_supported ? 'API supported' : 'Manual takedown')}}</span>
      </div>`).join('');
  }} catch(e) {{ console.error(e); }}
}}

async function togglePlatform(key, active) {{
  await fetch('/api/settings/platforms/'+key, {{
    method:'PATCH', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{active}})
  }});
}}

async function saveAll() {{
  const confVal = parseInt(document.getElementById('conf-threshold').value) / 100;
  await fetch('/api/settings/rules', {{
    method:'PATCH', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      triage: {{
        confidence_review_threshold: confVal,
        high_value_threshold: parseFloat(document.getElementById('high-value-threshold').value)||75,
      }},
      pricing: {{
        stale_listing_days: parseInt(document.getElementById('stale-days').value)||60,
        price_drop_percent: parseInt(document.getElementById('stale-drop').value)||10,
      }},
      ebay: {{
        default_shipping_policy: document.getElementById('ebay-shipping').value,
        default_return_policy: document.getElementById('ebay-return').value,
        default_payment_policy: document.getElementById('ebay-payment').value,
      }},
    }})
  }});
  document.getElementById('restart-banner').classList.add('show');
}}

loadSettings();
</script>
</body></html>"""
