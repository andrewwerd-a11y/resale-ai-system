"""
Browser UI routes — serves full HTML pages for each view.
All data is fetched from the /api/* endpoints via JavaScript.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from packages.intake.src.pipeline_types import PhotoType

router = APIRouter()


def _photo_type_options_json() -> str:
    return json.dumps(PhotoType.ALL)


@router.get("/listings", response_class=HTMLResponse)
async def listings_page():
    return HTMLResponse(_listings_html())


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


@router.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics_page():
    return HTMLResponse(_diagnostics_html())


@router.get("/sourcing", response_class=HTMLResponse)
async def sourcing_page():
    return HTMLResponse(_sourcing_html())


@router.get("/capture", response_class=HTMLResponse)
async def capture_page():
    return HTMLResponse(_capture_html())


@router.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(_settings_html())


@router.get("/intake-pipeline/{sku}", response_class=HTMLResponse)
async def intake_pipeline_cockpit(sku: str):
    """Operator cockpit for a single item's staged intake pipeline.

    Reads /api/items/{sku}/intake-pipeline-status,
    /api/items/{sku}/correction-report-v2, and exposes buttons for
    platform-drafts and marketplace-recommendations. Read-only / draft-only —
    publish controls remain on the existing review and listings pages.
    """
    return HTMLResponse(_intake_pipeline_cockpit_html(sku))


def _intake_pipeline_cockpit_html(sku: str) -> str:
    safe_sku = sku.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html><head><meta charset='utf-8'>
<title>Intake Pipeline — {safe_sku}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 16px; }}
  h1 {{ margin-top: 0; }}
  section {{ border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin: 12px 0; }}
  section h2 {{ margin: 0 0 8px 0; font-size: 15px; color: #333; }}
  pre {{ background: #f7f7f8; padding: 8px; border-radius: 4px; overflow: auto; max-height: 360px; font-size: 12px; }}
  .row {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  button {{ padding: 6px 10px; cursor: pointer; }}
  .badge {{ display: inline-block; padding: 2px 6px; border-radius: 4px; background: #eef; color: #224; font-size: 12px; margin-left: 8px; }}
  .bad {{ background: #fee; color: #800; }}
  .ok {{ background: #efe; color: #060; }}
  .warn {{ background: #ffd; color: #553; }}
</style></head><body>
<h1>Intake Pipeline — {safe_sku} <span class='badge warn'>read-only / draft-only</span></h1>
<p>This cockpit calls the new <code>/api/items/{safe_sku}/...</code> endpoints. Nothing here publishes or mutates remote state.</p>
<section><h2>Stage status</h2>
  <div class='row'>
    <button id='btn-status'>Run intake pipeline preview</button>
    <button id='btn-report'>View correction report v2</button>
    <button id='btn-readiness'>Recheck publish readiness</button>
    <button id='btn-drafts'>Generate platform drafts</button>
    <button id='btn-recs'>Marketplace recommendations</button>
  </div>
  <pre id='out'>(click a button above)</pre>
</section>
<section id='evidence-panel' style='display:none'>
  <h2>Operator photo evidence</h2>
  <div id='evidence-summary' style='font-size:12px;line-height:1.45'></div>
  <div id='photo-metadata-summary' style='margin-top:14px;font-size:12px;line-height:1.45'></div>
</section>
<script>
const SKU = {sku!r};
const PHOTO_TYPE_OPTIONS = {_photo_type_options_json()};
function escapeHtml(value) {{
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}}
function formatList(items, emptyText) {{
  return items && items.length
    ? `<ul style="margin:6px 0 0 18px;padding:0">${{items.map(item => `<li>${{escapeHtml(item)}}</li>`).join('')}}</ul>`
    : `<div style="color:#666;margin-top:6px">${{escapeHtml(emptyText)}}</div>`;
}}
function renderPhotoTypeOptions(selected) {{
  return PHOTO_TYPE_OPTIONS.map(function(opt) {{
    const picked = opt === selected ? ' selected' : '';
    return `<option value="${{escapeHtml(opt)}}"${{picked}}>${{escapeHtml(opt)}}</option>`;
  }}).join('');
}}
function renderPhotoMetadataSection(sku, photos, missingPhotoTypes) {{
  if (!photos || !photos.length) {{
    return `
      <div style="margin-top:12px">
        <strong>Photo labels</strong>
        <div style="margin-top:6px;color:#666">No photos available to label.</div>
      </div>
    `;
  }}
  return `
    <div style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <strong>Photo labels</strong>
        <span class="badge ok">local-only labels; no publish or approval changes</span>
      </div>
      <div style="margin-top:6px;color:#333">Current missing photo types: ${{escapeHtml((missingPhotoTypes || []).join(', ') || 'none')}}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px">
        ${{photos.map(function(photo) {{
          const thumb = `/api/items/${{sku}}/image?path=${{encodeURIComponent(photo.image_path)}}`;
          return `
            <div style="border:1px solid #ddd;border-radius:6px;padding:8px;background:#fafafa">
              <img src="${{thumb}}" alt="photo" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;border:1px solid #ddd" onerror="this.style.display='none'">
              <div style="margin-top:6px;font-size:11px;word-break:break-word">${{escapeHtml(photo.image_path || '')}}</div>
              <label style="margin-top:8px;font-size:11px;color:#666;display:block">Photo type</label>
              <select data-image-path="${{escapeHtml(photo.image_path || '')}}" style="width:100%;margin-top:4px">
                ${{renderPhotoTypeOptions(photo.photo_type || 'unknown')}}
              </select>
              <div style="margin-top:6px;font-size:11px;color:#666">Source: ${{escapeHtml(photo.label_source || photo.source || 'unknown')}} | Confidence: ${{escapeHtml(photo.confidence ?? '')}}</div>
              <button style="margin-top:8px;padding:5px 8px;cursor:pointer" onclick="savePhotoMetadataLabel('${{sku}}', this)">Save label</button>
            </div>
          `;
        }}).join('')}}
      </div>
    </div>
  `;
}}
async function loadPhotoMetadataAndRender(sku, report) {{
  const target = document.getElementById('photo-metadata-summary');
  try {{
    const resp = await fetch(`/api/items/${{sku}}/photos/metadata`);
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Photo metadata failed.');
    target.innerHTML = renderPhotoMetadataSection(sku, body.photos || [], (report.operator_photo_evidence || {{}}).missing_photo_types || []);
  }} catch (error) {{
    target.innerHTML = `<div style="margin-top:12px;color:#800">Photo metadata unavailable: ${{escapeHtml(error.message)}}</div>`;
  }}
}}
async function savePhotoMetadataLabel(sku, button) {{
  const card = button.closest('div[style*="border:1px solid #ddd"]');
  const select = card ? card.querySelector('select[data-image-path]') : null;
  if (!select) return;
  button.disabled = true;
  try {{
    const resp = await fetch(`/api/items/${{sku}}/photos/metadata`, {{
      method: 'PATCH',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        updates: [{{
          image_path: select.getAttribute('data-image-path'),
          photo_type: select.value
        }}]
      }})
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Photo label save failed.');
    const report = await show(fetch(`/api/items/${{sku}}/correction-report-v2`));
    if (report) {{
      document.getElementById('evidence-summary').innerHTML = renderCorrectionReportSummary(sku, report);
      await loadPhotoMetadataAndRender(sku, report);
    }}
  }} catch (error) {{
    alert(error.message);
  }} finally {{
    button.disabled = false;
  }}
}}
function renderCorrectionReportSummary(sku, report) {{
  const evidence = report.operator_photo_evidence || {{}};
  const nextPhotos = evidence.missing_photo_types || [];
  const selectedTypes = evidence.selected_photo_types || [];
  const skippedReasons = evidence.skipped_image_reasons || [];
  const selectionAvailable = evidence.deep_analysis_image_selection_available;
  const selectedCount = evidence.selected_image_count;
  const skippedCount = evidence.skipped_image_count;
  const qualityStatus = evidence.intake_quality_status || (report.intake_quality || {{}}).intake_quality_status || '-';
  const needsMorePhotos = evidence.needs_more_photos_for_analysis;
  const qualityReason = (report.intake_quality || {{}}).reason || '';
  const selectionSummary = selectionAvailable
    ? `<div style="margin-top:8px"><strong>Analysis image selection:</strong> selected ${{selectedCount ?? 0}} image(s), skipped ${{skippedCount ?? 0}}.</div>`
    : '<div style="margin-top:8px;color:#666">Deep analysis image-selection metadata is not available yet. Intake-quality evidence below is still current.</div>';
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <h3 style="margin:0;font-size:15px">Operator evidence for ${{escapeHtml(sku)}}</h3>
      <span class="badge ${{needsMorePhotos ? 'warn' : 'ok'}}">${{escapeHtml(qualityStatus)}}</span>
    </div>
    <div style="margin-top:8px;color:#333">${{escapeHtml(qualityReason || 'No additional intake-quality note.')}}</div>
    <div style="margin-top:12px">
      <strong>Next photos needed</strong>
      ${{formatList(nextPhotos, needsMorePhotos ? 'No specific photo types listed yet.' : 'No additional photos requested by intake quality.')}}
    </div>
    <div style="margin-top:12px">
      <strong>Evidence needed</strong>
      <div style="margin-top:6px">Intake quality asks for more photos: <strong>${{needsMorePhotos ? 'yes' : 'no'}}</strong></div>
      ${{selectionSummary}}
      <div style="margin-top:8px"><strong>Selected photo types:</strong></div>
      ${{formatList(selectedTypes, selectionAvailable ? 'No selected photo types were reported.' : 'Selection details will appear after deep analysis runs.')}}
      <div style="margin-top:8px"><strong>Skipped image reasons:</strong></div>
      ${{formatList(skippedReasons, selectionAvailable ? 'No skipped-image reasons were reported.' : 'No skipped-image data is available before deep analysis runs.')}}
    </div>
  `;
}}
async function show(promise) {{
  const out = document.getElementById('out');
  out.textContent = 'loading...';
  try {{
    const resp = await promise;
    const body = await resp.json();
    out.textContent = JSON.stringify(body, null, 2);
    return body;
  }} catch (e) {{ out.textContent = String(e); }}
  return null;
}}
document.getElementById('btn-status').onclick = () =>
  show(fetch(`/api/items/${{SKU}}/intake-pipeline-status?run_deep_analysis=true`));
document.getElementById('btn-report').onclick = async () => {{
  const body = await show(fetch(`/api/items/${{SKU}}/correction-report-v2`));
  if (!body) {{
    document.getElementById('evidence-panel').style.display = 'none';
    return;
  }}
  document.getElementById('evidence-panel').style.display = 'block';
  document.getElementById('evidence-summary').innerHTML = renderCorrectionReportSummary(SKU, body);
  await loadPhotoMetadataAndRender(SKU, body);
}};
document.getElementById('btn-readiness').onclick = () =>
  show(fetch(`/api/items/${{SKU}}/correction-report`));
document.getElementById('btn-drafts').onclick = () =>
  show(fetch(`/api/items/${{SKU}}/platform-drafts`, {{method: 'POST', headers: {{'content-type':'application/json'}}, body: '{{}}'}}));
document.getElementById('btn-recs').onclick = () =>
  show(fetch(`/api/items/${{SKU}}/marketplace-recommendations`, {{method: 'POST', headers: {{'content-type':'application/json'}}, body: JSON.stringify({{selection_mode: 'hybrid'}})}}));
</script>
</body></html>"""


def _nav(active: str) -> str:
    pages = [
        ("Dashboard", "/", "dashboard"),
        ("Intake", "/intake", "intake"),
        ("Review Queue", "/review-queue", "review"),
        ("Bulk Approve", "/bulk-approve", "bulk"),
        ("Inventory", "/inventory", "inventory"),
        ("Listings", "/listings", "listings"),
        ("Lots", "/lots", "lots"),
        ("Reports", "/reports", "reports"),
        ("Diagnostics", "/diagnostics", "diagnostics"),
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
      + ' onerror="this.style.display=\\'none\\'"'
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
    + '<button class="btn btn-green"  style="font-size:12px;padding:5px 10px" onclick="detailAction(\\'approve\\',\\'' + it.sku + '\\')">Approve</button>'
    + '<button class="btn btn-gray"   style="font-size:12px;padding:5px 10px" onclick="detailAction(\\'review\\',\\'' + it.sku + '\\')">Send to review</button>'
    + '<button class="btn btn-purple" style="font-size:12px;padding:5px 10px" onclick="detailAction(\\'publish\\',\\'' + it.sku + '\\')">Publish to eBay</button>'
    + '<button class="btn btn-red"    style="font-size:12px;padding:5px 10px" onclick="detailAction(\\'reject\\',\\'' + it.sku + '\\')">Reject</button>'
    + '</div>'
    + '<div id="dp-publish-result" style="margin-top:10px;font-size:12px"></div>';
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

  // ── Category Intelligence section ─────────────────────────────────────────
  let catIntelHtml = '';
  if (it.ebay_category_id || it.category_template_fetched) {{
    const catName = it.ebay_category_name || it.ebay_category_id || '';
    const missing = it.missing_required_fields || [];
    const missingRec = it.missing_recommended_fields || [];
    const publishReady = it.publish_ready;

    const publishBadge = publishReady
      ? '<span style="background:#085041;color:#9fe1cb;padding:1px 7px;border-radius:8px;font-size:11px">Publish Ready</span>'
      : '<span style="background:#501313;color:#f09595;padding:1px 7px;border-radius:8px;font-size:11px">Missing Required Fields</span>';

    const missingReqList = missing.length
      ? missing.map(f => `<div style="color:#f09595;font-size:12px">&#10007; ${{f}}</div>`).join('')
      : '<div style="color:#5dcaa5;font-size:12px">&#10003; All required fields present</div>';

    const missingRecList = missingRec.length
      ? `<div style="margin-top:6px;font-size:11px;color:#888780">Recommended missing: ${{missingRec.slice(0,5).join(', ')}}${{missingRec.length > 5 ? '…' : ''}}</div>`
      : '';

    catIntelHtml = `
      <div style="border:1px solid #2c2c2a;border-radius:6px;padding:10px;margin-bottom:12px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:12px;font-weight:500;color:#f1efe8">Category Intelligence</span>
          ${{publishBadge}}
        </div>
        <div style="font-size:11px;color:#888780;margin-bottom:6px">
          eBay: ${{catName}} (ID: ${{it.ebay_category_id || '—'}})
        </div>
        ${{missingReqList}}
        ${{missingRecList}}
        <button onclick="rerunCatIntel('${{it.sku}}')"
          style="margin-top:8px;background:#2c2c2a;border:none;color:#d4d2c8;font-size:11px;
                 padding:3px 10px;border-radius:4px;cursor:pointer;font-family:inherit">
          Re-run category intelligence
        </button>
        <span id="cat-intel-msg-${{it.sku}}" style="font-size:11px;margin-left:6px;color:#888780"></span>
      </div>`;
  }}

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
    ${{catIntelHtml}}
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

async function rerunCatIntel(sku) {{
  const msgEl = document.getElementById('cat-intel-msg-' + sku);
  if (msgEl) msgEl.textContent = 'Running...';
  try {{
    const r = await fetch(`/api/items/${{sku}}/category-intelligence`, {{method: 'POST'}});
    if (r.ok) {{
      const d = await r.json();
      if (msgEl) msgEl.textContent = d.publish_ready ? 'Done — publish ready!' : `Done — ${{d.missing_required.length}} required fields missing`;
      // Refresh the item in the list
      const idx = items.findIndex(i => i.sku === sku);
      if (idx >= 0) {{
        const itemR = await fetch(`/api/items/${{sku}}`);
        if (itemR.ok) {{
          items[idx] = await itemR.json();
          renderDetail(items[idx]);
        }}
      }}
    }} else {{
      const err = await r.json().catch(() => ({{detail: 'Error'}}));
      if (msgEl) msgEl.textContent = 'Error: ' + (err.detail || r.status);
    }}
  }} catch(e) {{
    if (msgEl) msgEl.textContent = 'Error: ' + e.message;
  }}
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
  try {{
    const r = await fetch('/api/items?limit=500');
    if (!r.ok) {{ throw new Error('API error ' + r.status); }}
    allItems = await r.json();
  }} catch(e) {{
    document.getElementById('inv-body').innerHTML =
      '<tr><td colspan="10" style="color:#f09595">Failed to load items: ' + e.message + '</td></tr>';
    return;
  }}
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
    const res = document.getElementById('dp-publish-result');
    if (res) res.innerHTML = '<span style="color:#fac775">Publishing...</span>';
    try {{
      const r = await fetch(`/api/ebay/publish/${{sku}}`, {{method:'POST'}});
      const d = await r.json();
      if (r.ok) {{
        const url = d.listing_url ? ` — <a href="${{d.listing_url}}" target="_blank" style="color:#5dcaa5">View listing</a>` : '';
        if (res) res.innerHTML = `<span style="color:#5dcaa5">Listed! ID: ${{d.listing_id||'?'}}${{url}}</span>`;
        load();
      }} else {{
        if (res) res.innerHTML = `<span style="color:#f09595">Error: ${{d.detail||d.error||'Unknown error'}}</span>`;
      }}
    }} catch(e) {{
      if (res) res.innerHTML = `<span style="color:#f09595">Error: ${{e.message}}</span>`;
    }}
    return;
  }}
  closePanel(); load();
}}
load();
</script>
</body></html>"""


def _export_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Export Center — Resale AI</title>
{_base_style()}
<style>
.export-card {{
  background:#222220;border:1px solid #2c2c2a;border-radius:8px;padding:20px;
  display:flex;flex-direction:column;
}}
.export-card-title {{ font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:6px; }}
.export-card-desc  {{ font-size:12px;color:#888780;margin-bottom:14px;flex:1; }}
.export-count      {{ font-size:28px;font-weight:500;color:#5dcaa5;margin-bottom:2px; }}
.export-count-label{{ font-size:11px;color:#888780;margin-bottom:14px; }}
.export-msg        {{ margin-top:10px;font-size:12px; }}
.status-panel      {{
  background:#1a1a18;border:1px solid #2c2c2a;border-radius:8px;padding:18px;
  margin-top:24px;max-width:700px;
}}
.status-row        {{ display:flex;gap:10px;align-items:center;margin-bottom:6px;font-size:12px;color:#d4d2c8; }}
.badge-ok          {{ background:#085041;color:#9fe1cb;padding:2px 8px;border-radius:10px;font-size:11px; }}
.badge-warn        {{ background:#3a2a00;color:#fac775;padding:2px 8px;border-radius:10px;font-size:11px; }}
</style>
</head>
<body>
{_nav("export")}
<main>
<h2>Export Center</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:700px">

  <!-- Card 1: Publish to eBay -->
  <div class="export-card">
    <div class="export-card-title">Publish to eBay</div>
    <div class="export-card-desc">
      Directly publish all approved items as live eBay listings with photos uploaded automatically.
    </div>
    <div class="export-count" id="publish-count">...</div>
    <div class="export-count-label">items ready to publish</div>
    <button class="btn btn-green" onclick="publishBatch()">Publish all to eBay</button>
    <div class="export-msg" id="publish-msg"></div>
  </div>

  <!-- Card 2: eBay bulk CSV -->
  <div class="export-card">
    <div class="export-card-title">eBay bulk upload CSV</div>
    <div class="export-card-desc">
      Exports all export-ready items to a CSV you can upload to eBay Seller Hub.
    </div>
    <div class="export-count" id="ready-count">...</div>
    <div class="export-count-label">items export-ready</div>
    <button class="btn btn-purple" onclick="generateCSV()">Generate eBay CSV</button>
    <div class="export-msg" id="csv-msg"></div>
  </div>

  <!-- Card 3: Master inventory sheet -->
  <div class="export-card">
    <div class="export-card-title">Master inventory sheet</div>
    <div class="export-card-desc">
      Generates an Excel file with all items and their current status.
    </div>
    <button class="btn btn-gray" onclick="generateSheet()">Generate Excel</button>
    <div class="export-msg" id="sheet-msg"></div>
  </div>

  <!-- Card 4: Sync sold orders -->
  <div class="export-card">
    <div class="export-card-title">Sync sold orders</div>
    <div class="export-card-desc">
      Fetch sold orders from eBay and update profit records automatically.
    </div>
    <button class="btn btn-gray" onclick="syncSold()">Sync from eBay</button>
    <div class="export-msg" id="sync-msg"></div>
  </div>

</div>

<!-- eBay OAuth + connection status -->
<div class="status-panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <div style="font-size:13px;font-weight:500;color:#f1efe8">eBay account connection</div>
    <a href="/api/ebay/oauth/start"
       style="background:#534ab7;color:#eeedfe;padding:5px 12px;border-radius:6px;font-size:12px;text-decoration:none">
      Connect eBay Account
    </a>
  </div>
  <div id="ebay-oauth-content" style="color:#888780;font-size:12px;margin-bottom:14px">Loading...</div>
  <div style="font-size:13px;font-weight:500;color:#f1efe8;margin-bottom:10px">API credentials</div>
  <div id="ebay-status-content" style="color:#888780;font-size:12px">Loading...</div>
</div>

<div style="margin-top:28px;max-width:700px">
  <h2>CSV upload instructions</h2>
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
  const [sr, er] = await Promise.all([
    fetch('/api/items/stats'),
    fetch('/api/export/stats'),
  ]);
  const s = await sr.json();
  const e = await er.json();
  document.getElementById('publish-count').textContent = s._ready_to_publish || 0;
  document.getElementById('ready-count').textContent   = e.export_ready || 0;
}}

async function loadEbayStatus() {{
  try {{
    const [sr, or_] = await Promise.all([
      fetch('/api/ebay/status'),
      fetch('/api/ebay/oauth/status'),
    ]);
    const d  = await sr.json();
    const od = await or_.json();

    // OAuth panel
    let ohtml = '';
    if (od.has_oauth_tokens) {{
      const tokenBadge = od.access_token_valid
        ? '<span class="badge-ok">OAuth token valid</span>'
        : '<span class="badge-warn">OAuth token expired</span>';
      const refreshBadge = od.refresh_token_valid
        ? '<span class="badge-ok">refresh token valid</span>'
        : '<span class="badge-warn">refresh token expired — reconnect</span>';
      const exp = od.expires_at ? od.expires_at.slice(0,19).replace('T',' ') + ' UTC' : '?';
      const rexp = od.refresh_expires_at ? od.refresh_expires_at.slice(0,10) : '?';
      ohtml = `
        <div class="status-row">${{tokenBadge}} ${{refreshBadge}}</div>
        <div class="status-row"><span style="color:#888780">Access expires:</span><span>${{exp}}</span></div>
        <div class="status-row"><span style="color:#888780">Refresh expires:</span><span>${{rexp}}</span></div>
      `;
    }} else if (od.using_env_token) {{
      ohtml = `<div class="status-row"><span class="badge-warn">Using .env IAF token (not OAuth)</span>
        <span style="color:#888780;margin-left:6px">Click "Connect eBay Account" to upgrade to OAuth</span></div>`;
    }} else {{
      ohtml = `<div class="status-row"><span class="badge-warn">No token configured</span>
        <span style="color:#888780;margin-left:6px">Click "Connect eBay Account" to authorize</span></div>`;
    }}
    document.getElementById('ebay-oauth-content').innerHTML = ohtml;

    // API credentials panel
    const connBadge = d.configured
      ? '<span class="badge-ok">Credentials OK</span>'
      : '<span class="badge-warn">Not configured</span>';
    const photoBadge = d.photo_hosting
      ? `<span class="badge-ok">${{d.photo_host||'cloudinary'}}</span>`
      : '<span class="badge-warn">local paths only</span>';
    let html = `
      <div class="status-row">${{connBadge}}<span style="color:#888780">Environment:</span><span>${{d.environment||'?'}}</span></div>
      <div class="status-row"><span style="color:#888780">Marketplace:</span><span>${{d.marketplace||'?'}}</span></div>
      <div class="status-row"><span style="color:#888780">Photo hosting:</span>${{photoBadge}}</div>
    `;
    if (!d.configured) {{
      html += `<div style="margin-top:10px;padding:10px;background:#2a1a00;border:1px solid #4a3000;border-radius:6px;font-size:12px;color:#fac775">
        Add <code>EBAY_PROD_APP_ID</code>, <code>EBAY_PROD_CERT_ID</code>, and <code>EBAY_RUNAME</code> to your <code>.env</code> file.
      </div>`;
    }}
    document.getElementById('ebay-status-content').innerHTML = html;
  }} catch(e) {{
    document.getElementById('ebay-status-content').textContent = 'Failed to load status.';
  }}
}}

async function publishBatch() {{
  document.getElementById('publish-msg').innerHTML = '<span style="color:#fac775">Publishing... this may take a moment.</span>';
  try {{
    const r = await fetch('/api/ebay/publish/batch', {{method:'POST'}});
    const d = await r.json();
    const msg = d.message || `Published: ${{d.published||0}}, Failed: ${{d.failed||0}}`;
    const color = (d.failed||0) > 0 ? '#fac775' : '#5dcaa5';
    document.getElementById('publish-msg').innerHTML = `<span style="color:${{color}}">${{msg}}</span>`;
    if (d.errors && d.errors.length) {{
      document.getElementById('publish-msg').innerHTML +=
        '<div style="margin-top:6px;color:#f09595;font-size:11px">' + d.errors.join('<br>') + '</div>';
    }}
    loadStats();
  }} catch(e) {{
    document.getElementById('publish-msg').innerHTML = `<span style="color:#f09595">Error: ${{e.message}}</span>`;
  }}
}}

async function generateCSV() {{
  document.getElementById('csv-msg').innerHTML = '<span style="color:#fac775">Generating...</span>';
  const r = await fetch('/api/export/ebay-csv', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('csv-msg').innerHTML =
    `<span style="color:#5dcaa5">${{d.message}}</span><br><small style="color:#888780">${{d.path||''}}</small>`;
  loadStats();
}}

async function generateSheet() {{
  document.getElementById('sheet-msg').innerHTML = '<span style="color:#fac775">Generating...</span>';
  const r = await fetch('/api/export/master-sheet', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('sheet-msg').innerHTML =
    `<span style="color:#5dcaa5">${{d.message}}</span><br><small style="color:#888780">${{d.path||''}}</small>`;
}}

async function syncSold() {{
  document.getElementById('sync-msg').innerHTML = '<span style="color:#fac775">Syncing...</span>';
  try {{
    const r = await fetch('/api/ebay/sync-sold', {{method:'POST'}});
    const d = await r.json();
    const matched = d.matched !== undefined ? d.matched : (d.orders_matched || 0);
    document.getElementById('sync-msg').innerHTML =
      `<span style="color:#5dcaa5">Matched: ${{matched}} orders</span>`;
  }} catch(e) {{
    document.getElementById('sync-msg').innerHTML = `<span style="color:#f09595">Error: ${{e.message}}</span>`;
  }}
}}

loadStats();
loadEbayStatus();
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
    <th>SKU</th><th>Category</th><th>Images</th><th>Intake quality</th><th>Cost ($)</th><th>Status</th><th>Action</th>
  </tr></thead>
  <tbody id="intake-body"><tr><td colspan="7" style="color:#888780">Loading...</td></tr></tbody>
</table>
<section id="correction-report-panel" style="display:none;margin-top:16px;background:#171715;border:1px solid #2c2c2a;border-radius:6px;padding:12px;color:#d4d2c8">
  <div id="correction-report-summary" style="font-size:12px;line-height:1.45"></div>
  <pre id="correction-report" style="margin-top:12px;background:#111110;border:1px solid #2c2c2a;border-radius:6px;padding:12px;color:#d4d2c8;font-size:12px;white-space:pre-wrap;max-height:420px;overflow:auto"></pre>
</section>
</main>
<script>
const PHOTO_TYPE_OPTIONS = {_photo_type_options_json()};
function escapeHtml(value) {{
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}}
function formatList(items, emptyText) {{
  return items && items.length
    ? `<ul style="margin:6px 0 0 18px;padding:0">${{items.map(item => `<li>${{escapeHtml(item)}}</li>`).join('')}}</ul>`
    : `<div style="color:#888780;margin-top:6px">${{escapeHtml(emptyText)}}</div>`;
}}
function renderPhotoTypeOptions(selected) {{
  return PHOTO_TYPE_OPTIONS.map(function(opt) {{
    const picked = opt === selected ? ' selected' : '';
    return `<option value="${{escapeHtml(opt)}}"${{picked}}>${{escapeHtml(opt)}}</option>`;
  }}).join('');
}}
function renderPhotoMetadataSection(sku, photos, missingPhotoTypes) {{
  if (!photos || !photos.length) {{
    return `
      <div style="margin-top:12px">
        <strong>Photo labels</strong>
        <div style="margin-top:6px;color:#888780">No photos available to label.</div>
      </div>
    `;
  }}
  return `
    <div style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <strong>Photo labels</strong>
        <span class="badge approved">local-only labels; no publish or approval changes</span>
      </div>
      <div style="margin-top:6px;color:#c9c6bc">Current missing photo types: ${{escapeHtml((missingPhotoTypes || []).join(', ') || 'none')}}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px">
        ${{photos.map(function(photo) {{
          const thumb = `/api/items/${{sku}}/image?path=${{encodeURIComponent(photo.image_path)}}`;
          return `
            <div style="border:1px solid #2c2c2a;border-radius:6px;padding:8px;background:#111110">
              <img src="${{thumb}}" alt="photo" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;border:1px solid #2c2c2a" onerror="this.style.display='none'">
              <div style="margin-top:6px;font-size:11px;word-break:break-word">${{escapeHtml(photo.image_path || '')}}</div>
              <label style="margin-top:8px">Photo type</label>
              <select data-image-path="${{escapeHtml(photo.image_path || '')}}" style="margin-top:4px">
                ${{renderPhotoTypeOptions(photo.photo_type || 'unknown')}}
              </select>
              <div style="margin-top:6px;font-size:11px;color:#888780">Source: ${{escapeHtml(photo.label_source || photo.source || 'unknown')}} | Confidence: ${{escapeHtml(photo.confidence ?? '')}}</div>
              <button class="btn btn-gray" style="margin-top:8px;font-size:11px;padding:4px 10px" onclick="savePhotoMetadataLabel('${{sku}}', this)">Save label</button>
            </div>
          `;
        }}).join('')}}
      </div>
    </div>
  `;
}}
function renderCorrectionReportSummary(sku, report) {{
  const evidence = report.operator_photo_evidence || {{}};
  const nextPhotos = evidence.missing_photo_types || [];
  const selectedTypes = evidence.selected_photo_types || [];
  const skippedReasons = evidence.skipped_image_reasons || [];
  const selectionAvailable = evidence.deep_analysis_image_selection_available;
  const selectedCount = evidence.selected_image_count;
  const skippedCount = evidence.skipped_image_count;
  const qualityStatus = evidence.intake_quality_status || (report.intake_quality || {{}}).intake_quality_status || '-';
  const needsMorePhotos = evidence.needs_more_photos_for_analysis;
  const qualityReason = (report.intake_quality || {{}}).reason || '';
  const selectionSummary = selectionAvailable
    ? `<div style="margin-top:8px"><strong>Analysis image selection:</strong> selected ${{selectedCount ?? 0}} image(s), skipped ${{skippedCount ?? 0}}.</div>`
    : '<div style="margin-top:8px;color:#888780">Deep analysis image-selection metadata is not available yet. Intake-quality evidence below is still current.</div>';
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <h3 style="margin:0;font-size:15px">Operator evidence for ${{escapeHtml(sku)}}</h3>
      <span class="badge ${{needsMorePhotos ? 'needs_review' : 'approved'}}">${{escapeHtml(qualityStatus)}}</span>
    </div>
    <div style="margin-top:8px;color:#c9c6bc">${{escapeHtml(qualityReason || 'No additional intake-quality note.')}}</div>
    <div style="margin-top:12px">
      <strong>Next photos needed</strong>
      ${{formatList(nextPhotos, needsMorePhotos ? 'No specific photo types listed yet.' : 'No additional photos requested by intake quality.')}}
    </div>
    <div style="margin-top:12px">
      <strong>Evidence needed</strong>
      <div style="margin-top:6px">Intake quality asks for more photos: <strong>${{needsMorePhotos ? 'yes' : 'no'}}</strong></div>
      ${{selectionSummary}}
      <div style="margin-top:8px"><strong>Selected photo types:</strong></div>
      ${{formatList(selectedTypes, selectionAvailable ? 'No selected photo types were reported.' : 'Selection details will appear after deep analysis runs.')}}
      <div style="margin-top:8px"><strong>Skipped image reasons:</strong></div>
      ${{formatList(skippedReasons, selectionAvailable ? 'No skipped-image reasons were reported.' : 'No skipped-image data is available before deep analysis runs.')}}
    </div>
  `;
}}
async function load() {{
  const r = await fetch('/api/items?status=pending_intake&limit=200');
  const items = await r.json();
  const qualityEntries = await Promise.all(items.map(async function(it) {{
    try {{
      const qr = await fetch(`/api/items/${{it.sku}}/intake-quality`);
      return [it.sku, await qr.json()];
    }} catch(e) {{
      return [it.sku, null];
    }}
  }}));
  const qualityBySku = Object.fromEntries(qualityEntries);
  document.getElementById('intake-count').textContent = `(${{items.length}})`;
  document.getElementById('intake-body').innerHTML = items.length
    ? items.map(it => {{
        const paths = Array.isArray(it.image_paths) ? it.image_paths : (it.image_paths||'').split('|').filter(Boolean);
        const imgs = paths.length;
        const q = qualityBySku[it.sku] || {{}};
        const missing = (q.missing_photo_types || []).slice(0, 3).join(', ');
        const qualityText = q.intake_quality_status || '-';
        const warn = q.needs_more_photos_for_analysis
          ? '<div style="color:#fac775;font-size:11px;margin-top:3px">Next photos needed: ' + missing + '</div>'
          : '';
        const margin = it.estimated_price && it.cost
          ? ' (~' + Math.round((it.estimated_price - it.cost) / it.estimated_price * 100) + '% margin)'
          : '';
        return `<tr>
          <td style="font-family:monospace">${{it.sku}}</td>
          <td>${{it.category_label||it.category_key||'-'}}</td>
          <td>${{imgs}}</td>
          <td><span class="badge ${{q.should_run_deep_analysis ? 'approved' : 'needs_review'}}">${{qualityText}}</span>${{warn}}</td>
          <td><input type="number" min="0" step="0.01"
              style="width:72px;padding:3px 6px;background:#2c2c2a;border:1px solid #3a3a38;color:#f1efe8;border-radius:4px;font-size:12px"
              value="${{it.cost||''}}"
              placeholder="0.00"
              onblur="saveCost('${{it.sku}}', this)"
              onkeydown="if(event.key==='Enter')this.blur()">
            <span style="font-size:11px;color:#888780">${{margin}}</span></td>
          <td><span class="badge pending_intake">pending_intake</span></td>
          <td><button class="btn btn-gray" style="font-size:11px;padding:4px 10px"
              onclick="analyzeOne('${{it.sku}}', this)" ${{q.should_run_deep_analysis ? '' : 'disabled'}}>Analyze</button>
              <button class="btn btn-gray" style="font-size:11px;padding:4px 10px;margin-left:4px"
              onclick="showCorrectionReport('${{it.sku}}')">Report</button></td>
        </tr>`;
      }}).join('')
    : '<tr><td colspan="7" style="color:#5dcaa5">No pending items.</td></tr>';
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
  btn.textContent = r.ok ? (d.status || 'Done') : 'Blocked';
  load();
}}
async function showCorrectionReport(sku) {{
  const r = await fetch(`/api/items/${{sku}}/correction-report-v2`);
  const d = await r.json();
  const metadataResp = await fetch(`/api/items/${{sku}}/photos/metadata`);
  const metadataBody = await metadataResp.json();
  const panel = document.getElementById('correction-report-panel');
  const summary = document.getElementById('correction-report-summary');
  const el = document.getElementById('correction-report');
  panel.style.display = 'block';
  const metadataHtml = metadataResp.ok
    ? renderPhotoMetadataSection(sku, metadataBody.photos || [], (d.operator_photo_evidence || {{}}).missing_photo_types || [])
    : `<div style="margin-top:12px;color:#f09595">Photo metadata unavailable: ${{escapeHtml(metadataBody.detail || 'unknown error')}}</div>`;
  summary.innerHTML = renderCorrectionReportSummary(sku, d) + metadataHtml;
  el.textContent = JSON.stringify(d, null, 2);
}}
async function savePhotoMetadataLabel(sku, button) {{
  const card = button.closest('div[style*="border:1px solid"]');
  const select = card ? card.querySelector('select[data-image-path]') : null;
  if (!select) return;
  button.disabled = true;
  try {{
    const resp = await fetch(`/api/items/${{sku}}/photos/metadata`, {{
      method: 'PATCH',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        updates: [{{
          image_path: select.getAttribute('data-image-path'),
          photo_type: select.value
        }}]
      }})
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Photo label save failed.');
    await showCorrectionReport(sku);
  }} catch (error) {{
    alert(error.message);
  }} finally {{
    button.disabled = false;
  }}
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
    <input type="range" id="conf-slider" min="0" max="100" value="0"
           oninput="document.getElementById('conf-val').textContent=this.value+'%'; applyFilters()">
    <span id="conf-val" style="font-size:12px;color:#f1efe8;min-width:38px">0%</span>
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
  try {{
    const r = await fetch('/api/items?limit=500');
    if (!r.ok) {{ throw new Error('API error ' + r.status); }}
    allItems = await r.json();
  }} catch(e) {{
    document.getElementById('bulk-body').innerHTML =
      '<tr><td colspan="8" style="color:#f09595">Failed to load items: ' + e.message + '</td></tr>';
    return;
  }}
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
{_detail_panel_style()}
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
let itemMap = {{}};
let selectedSkus = new Set();
{_detail_panel_js()}

async function load() {{
  try {{
    const r = await fetch('/api/items?limit=500');
    if (!r.ok) {{ throw new Error('API error ' + r.status); }}
    allItems = await r.json();
  }} catch(e) {{
    document.getElementById('item-list').innerHTML =
      '<div style="color:#f09595">Failed to load items: ' + e.message + '</div>';
    return;
  }}
  itemMap = {{}};
  allItems.forEach(function(it) {{ if (it.sku) itemMap[it.sku] = it; }});
  const cats = [...new Set(allItems.map(it => it.category_label).filter(Boolean))].sort();
  const catSel = document.getElementById('cat-filter');
  while (catSel.options.length > 1) catSel.remove(1);
  cats.forEach(c => {{ const o = document.createElement('option'); o.value=c; o.textContent=c; catSel.appendChild(o); }});
  filterItems();
  loadExistingLots();
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
        return `<div class="lot-item${{isSel ? ' selected' : ''}}" onclick="openDetail(itemMap['${{it.sku}}'])">
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
{_detail_panel_html()}
{_lightbox_html()}
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

<div style="margin-top:32px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between">
  <h2 style="margin:0">Category Intelligence</h2>
  <button class="btn btn-gray" style="font-size:12px" onclick="exportCatIntel()">Export CSV</button>
</div>
<table id="cat-intel-table">
  <thead><tr>
    <th>Category ID</th><th>Category Name</th><th>Items</th>
    <th>Sold</th><th>Avg Sold Price</th><th>Last Updated</th>
  </tr></thead>
  <tbody id="cat-intel-body"><tr><td colspan="6" style="color:#888780">Loading...</td></tr></tbody>
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

async function loadCatIntel() {{
  try {{
    const r = await fetch('/api/reports/category-intelligence');
    const rows = await r.json();
    const tbody = document.getElementById('cat-intel-body');
    if (!rows.length) {{
      tbody.innerHTML = '<tr><td colspan="6" style="color:#888780">No category intelligence data yet. Run analyze_all.py to populate.</td></tr>';
      return;
    }}
    tbody.innerHTML = rows.map(row => `<tr>
      <td style="font-family:monospace;font-size:12px">${{row.category_id || '-'}}</td>
      <td>${{row.category_name || '-'}}</td>
      <td>${{row.item_count || 0}}</td>
      <td>${{row.sold_count || 0}}</td>
      <td>${{row.avg_sold_price ? '$' + parseFloat(row.avg_sold_price).toFixed(2) : '-'}}</td>
      <td style="font-size:11px;color:#888780">${{(row.last_updated || '').slice(0,10) || '-'}}</td>
    </tr>`).join('');
  }} catch(e) {{ console.error('cat intel load error', e); }}
}}

async function exportCatIntel() {{
  const r = await fetch('/api/reports/category-intelligence/export');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'category_intelligence.csv';
  a.click();
}}

loadSummary(); loadMonthly(); loadPlatforms(); loadSales(); loadCatIntel();
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
  <div style="display:flex;align-items:center;gap:12px">
    <span id="dirty-indicator" style="font-size:12px;color:#ef9f27;display:none">Unsaved changes</span>
    <button class="btn btn-purple" id="save-btn" onclick="saveAll()">Save Settings</button>
  </div>
</div>
<div id="save-toast" style="display:none;padding:8px 14px;border-radius:6px;font-size:13px;margin-bottom:12px"></div>
<div class="restart-banner" id="restart-banner">
  Changes saved. Restart the server for .env changes to take effect.
</div>

<!-- ── DB-backed settings ─────────────────────────────── -->
<div class="settings-section">
  <h3>Photo Intake</h3>
  <div class="field-row">
    <label>Photo Sort Mode</label>
    <div style="display:flex;gap:0;border:1px solid #3a3a38;border-radius:6px;overflow:hidden;width:fit-content">
      <button id="sort-auto" onclick="setPhotoSort('auto')"
              style="padding:6px 18px;font-size:13px;cursor:pointer;border:none;font-family:inherit;
                     background:#534ab7;color:#eeedfe">Auto (AI)</button>
      <button id="sort-manual" onclick="setPhotoSort('manual')"
              style="padding:6px 18px;font-size:13px;cursor:pointer;border:none;font-family:inherit;
                     background:#2c2c2a;color:#888780">Manual</button>
    </div>
    <input type="hidden" id="photo-sort" value="auto">
  </div>
</div>

<div class="settings-section">
  <h3>Enrichment</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Enrichment Mode</label>
      <select id="enrichment-mode" onchange="markDirty()">
        <option value="local">Local only (Ollama vision, no Claude)</option>
        <option value="claude">Claude only</option>
        <option value="hybrid">Hybrid (Local first, Claude fallback)</option>
      </select>
    </div>
    <div class="field-row">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="enrichment-enabled" style="width:auto" onchange="markDirty()">
        <span style="font-size:13px;color:#d4d2c8">Enable AI enrichment (Claude API)</span>
      </label>
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>Listings</h3>
  <div class="settings-grid">
    <div class="field-row">
      <label>Default Promotion %</label>
      <input type="number" id="default-promo-pct" min="0" max="20" step="0.5"
             style="width:120px" onchange="markDirty()">
      <span style="font-size:11px;color:#888780;margin-top:4px;display:block">
        Applied to new listings unless overridden per item. Set to 0 to disable.
      </span>
    </div>
    <div class="field-row">
      <label>Default Condition</label>
      <select id="intake-condition" onchange="markDirty()">
        <option value="NEW">New</option>
        <option value="LIKE_NEW">Like New</option>
        <option value="USED_EXCELLENT">Used - Excellent</option>
        <option value="USED_VERY_GOOD">Used - Very Good</option>
        <option value="USED_GOOD">Used - Good</option>
        <option value="USED_ACCEPTABLE">Used - Acceptable</option>
        <option value="FOR_PARTS_OR_NOT_WORKING">For Parts / Not Working</option>
      </select>
    </div>
  </div>
</div>

<div class="settings-section">
  <h3>Listing Age Alerts</h3>
  <div style="display:flex;gap:16px;align-items:flex-end">
    <div class="field-row" style="margin:0">
      <label>First alert (days)</label>
      <input type="number" id="alert-days-1" min="1" style="width:100px" onchange="markDirty()">
    </div>
    <div class="field-row" style="margin:0">
      <label>Second alert (days)</label>
      <input type="number" id="alert-days-2" min="1" style="width:100px" onchange="markDirty()">
    </div>
    <div class="field-row" style="margin:0">
      <label>Third alert (days)</label>
      <input type="number" id="alert-days-3" min="1" style="width:100px" onchange="markDirty()">
    </div>
  </div>
</div>
<!-- ── end DB-backed settings ─────────────────────────── -->

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
  <div id="vision-provider-options" style="margin-top:14px"></div>
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
let _dirty = false;

function markDirty() {{
  _dirty = true;
  document.getElementById('dirty-indicator').style.display = 'inline';
}}

function setPhotoSort(val) {{
  document.getElementById('photo-sort').value = val;
  document.getElementById('sort-auto').style.background   = val === 'auto'   ? '#534ab7' : '#2c2c2a';
  document.getElementById('sort-auto').style.color        = val === 'auto'   ? '#eeedfe' : '#888780';
  document.getElementById('sort-manual').style.background = val === 'manual' ? '#534ab7' : '#2c2c2a';
  document.getElementById('sort-manual').style.color      = val === 'manual' ? '#eeedfe' : '#888780';
  markDirty();
}}

function showToast(msg, ok) {{
  const t = document.getElementById('save-toast');
  t.textContent = msg;
  t.style.background = ok ? '#085041' : '#501313';
  t.style.color = ok ? '#9fe1cb' : '#f09595';
  t.style.border = ok ? '1px solid #0d6b57' : '1px solid #7a1f1f';
  t.style.display = 'block';
  setTimeout(() => {{ t.style.display = 'none'; }}, 3000);
}}

async function loadDbSettings() {{
  try {{
    const r = await fetch('/api/settings');
    const d = await r.json();
    setPhotoSort(d.photo_sort || 'auto');
    document.getElementById('enrichment-mode').value = d.enrichment_mode || 'hybrid';
    document.getElementById('default-promo-pct').value = parseFloat(d.default_promotion_pct || '3');
    document.getElementById('intake-condition').value = d.intake_default_condition || 'USED_EXCELLENT';
    const parts = (d.listing_age_alert_days || '30,60,90').split(',');
    document.getElementById('alert-days-1').value = parseInt(parts[0]) || 30;
    document.getElementById('alert-days-2').value = parseInt(parts[1]) || 60;
    document.getElementById('alert-days-3').value = parseInt(parts[2]) || 90;
    _dirty = false;
    document.getElementById('dirty-indicator').style.display = 'none';
  }} catch(e) {{ console.error('loadDbSettings:', e); }}
}}

async function loadSettings() {{
  try {{
    const [rCurrent, rRules, rPlatforms, rVisionProviders] = await Promise.all([
      fetch('/api/settings/current'),
      fetch('/api/settings/rules'),
      fetch('/api/settings/platforms'),
      fetch('/api/settings/vision-providers'),
    ]);
    const current = await rCurrent.json();
    const rules = await rRules.json();
    const platforms = await rPlatforms.json();
    const visionProviders = await rVisionProviders.json();

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

    const vpDiv = document.getElementById('vision-provider-options');
    vpDiv.innerHTML = (visionProviders.providers || []).map((provider) => `
      <div style="padding:10px 12px;border:1px solid #2a2a27;border-radius:10px;background:#151513;margin-bottom:8px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
          <div style="font-size:13px;color:#f3efe6">${{provider.label}}${{provider.default ? ' · Default' : ''}}</div>
          <div style="font-size:11px;color:#888780;text-transform:uppercase">${{provider.tier}} · ${{provider.status}}</div>
        </div>
        <div style="font-size:12px;color:#b8b4aa;margin-top:6px">${{provider.note || ''}}</div>
        <div style="font-size:11px;color:#888780;margin-top:6px">${{provider.selectable ? 'Selectable for intake.' : (provider.selection_block_reason || 'Not selectable yet.')}}</div>
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
  const btn = document.getElementById('save-btn');
  btn.textContent = 'Saving...';
  btn.disabled = true;
  try {{
    // Save DB-backed settings
    const alertDays = [
      parseInt(document.getElementById('alert-days-1').value) || 30,
      parseInt(document.getElementById('alert-days-2').value) || 60,
      parseInt(document.getElementById('alert-days-3').value) || 90,
    ].join(',');
    const dbResp = await fetch('/api/settings', {{
      method:'PATCH', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        photo_sort: document.getElementById('photo-sort').value,
        enrichment_mode: document.getElementById('enrichment-mode').value,
        default_promotion_pct: String(document.getElementById('default-promo-pct').value || '3'),
        intake_default_condition: document.getElementById('intake-condition').value,
        listing_age_alert_days: alertDays,
      }})
    }});
    if (!dbResp.ok) {{
      const err = await dbResp.json();
      showToast('Error: ' + (err.detail || 'save failed'), false);
      return;
    }}

    // Save rules-based settings
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

    _dirty = false;
    document.getElementById('dirty-indicator').style.display = 'none';
    showToast('Settings saved', true);
    document.getElementById('restart-banner').classList.add('show');
  }} catch(e) {{
    showToast('Save failed: ' + e, false);
  }} finally {{
    btn.textContent = 'Save Settings';
    btn.disabled = false;
  }}
}}

loadDbSettings();
loadSettings();
</script>
</body></html>"""


# ── Listings page (Phase 5B) ───────────────────────────────────────────────────

def _listings_html() -> str:  # noqa: PLR0915
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Listings — Resale AI System</title>
{_base_style()}
<style>
.listing-card {{
  display:flex; align-items:flex-start; background:#222220;
  border:1px solid #2c2c2a; border-radius:8px; padding:10px;
  cursor:pointer; position:relative; transition:border-color .15s;
  user-select:none;
}}
.listing-card:hover {{ border-color:#534ab7; }}
.listing-card.selected {{ border-color:#7f77dd; background:#26215c22; }}
.card-check {{
  position:absolute; top:8px; left:8px;
  width:16px; height:16px; cursor:pointer; z-index:2;
}}
.listing-card .card-thumb {{
  width:80px; height:80px; object-fit:cover; border-radius:4px;
  flex-shrink:0; margin-left:20px;
}}
.card-thumb-placeholder {{
  width:80px; height:80px; background:#2c2c2a; border-radius:4px;
  flex-shrink:0; margin-left:20px; display:flex;
  align-items:center; justify-content:center;
  color:#3a3a38; font-size:22px;
}}
#drawer-overlay {{
  display:none; position:fixed; inset:0;
  background:rgba(0,0,0,.5); z-index:200;
}}
#drawer-overlay.open {{ display:block; }}
#drawer {{
  position:fixed; top:0; right:-500px; width:480px; height:100vh;
  background:#111110; border-left:1px solid #2c2c2a;
  overflow-y:auto; z-index:201; transition:right .25s ease;
  display:flex; flex-direction:column;
}}
#drawer.open {{ right:0; }}
.drawer-section {{
  border-bottom:1px solid #2c2c2a; padding:14px 16px;
}}
.drawer-section-title {{
  font-size:11px; color:#888780; text-transform:uppercase;
  letter-spacing:.05em; margin-bottom:10px;
}}
.field-dirty {{ border-left:3px solid #fac775 !important; padding-left:8px; }}
.field-row {{ margin-bottom:12px; position:relative; }}
.field-row label {{ font-size:11px; color:#888780; margin-bottom:3px; display:block; }}
.revert-link {{
  font-size:10px; color:#7f77dd; cursor:pointer;
  position:absolute; right:0; top:0; text-decoration:underline;
}}
.char-counter {{ font-size:10px; color:#888780; text-align:right; margin-top:2px; }}
.suggest-panel {{
  background:#1a2030; border:1px solid #2c3a5c; border-radius:6px;
  padding:10px; margin-top:6px; font-size:12px; color:#d4d2c8;
  display:none;
}}
.suggest-panel.open {{ display:block; }}
.suggest-text {{ color:#9fe1cb; margin-bottom:8px; line-height:1.5; }}
.price-note {{ font-size:11px; color:#888780; margin-top:4px; }}
.step-row {{
  display:flex; align-items:center; gap:8px;
  font-size:12px; color:#d4d2c8; padding:4px 0;
}}
.step-icon {{ font-size:14px; width:18px; text-align:center; }}
.photo-scroll {{
  display:flex; gap:8px; overflow-x:auto; padding-bottom:6px;
}}
.photo-thumb {{
  flex-shrink:0; width:100px; height:100px; position:relative;
}}
.photo-thumb img {{
  width:100px; height:100px; object-fit:cover; border-radius:4px;
  border:1px solid #2c2c2a;
}}
.photo-cover-badge {{
  position:absolute; top:4px; left:4px;
  background:#fac775; color:#1a1a18; font-size:9px;
  padding:1px 5px; border-radius:3px; font-weight:600;
}}
.photo-actions {{
  position:absolute; bottom:4px; left:0; right:0;
  display:flex; gap:4px; justify-content:center;
}}
.photo-btn {{
  background:rgba(17,17,16,.85); border:1px solid #3a3a38;
  color:#d4d2c8; font-size:9px; padding:2px 5px; border-radius:3px;
  cursor:pointer; font-family:inherit;
}}
.photo-btn:hover {{ background:#2c2c2a; }}
#ctx-menu {{
  display:none; position:fixed; background:#1a1a18;
  border:1px solid #3a3a38; border-radius:6px;
  z-index:500; min-width:180px; padding:4px 0;
  box-shadow:0 4px 16px rgba(0,0,0,.5);
}}
#ctx-menu .ctx-item {{
  padding:7px 14px; font-size:13px; color:#d4d2c8;
  cursor:pointer;
}}
#ctx-menu .ctx-item:hover {{ background:#2c2c2a; }}
#ctx-menu .ctx-sep {{ border-top:1px solid #2c2c2a; margin:4px 0; }}
#toast {{
  position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
  background:#2c2c2a; color:#f1efe8; padding:10px 20px;
  border-radius:8px; font-size:13px; z-index:600;
  box-shadow:0 4px 16px rgba(0,0,0,.5);
  opacity:0; transition:opacity .3s; pointer-events:none;
}}
#toast.show {{ opacity:1; }}
#toast.ok {{ background:#085041; color:#9fe1cb; }}
#toast.err {{ background:#501313; color:#f09595; }}
#dialog-overlay {{
  display:none; position:fixed; inset:0;
  background:rgba(0,0,0,.6); z-index:400;
  align-items:center; justify-content:center;
}}
#dialog-overlay.open {{ display:flex; }}
#dialog-box {{
  background:#1a1a18; border:1px solid #3a3a38; border-radius:10px;
  padding:24px; min-width:320px; max-width:460px;
}}
#dialog-box h3 {{ font-size:14px; color:#f1efe8; margin-bottom:12px; }}
#dialog-box p {{ font-size:13px; color:#888780; margin-bottom:16px; }}
.grid-container {{
  display:grid;
  grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));
  gap:10px; padding:16px 24px;
}}
</style>
</head>
<body>
{_nav("listings")}
<div style="background:#111110;border-bottom:1px solid #2c2c2a;padding:10px 24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
  <select id="status-filter" onchange="loadItems()"
    style="background:#2c2c2a;border:1px solid #3a3a38;color:#f1efe8;border-radius:6px;padding:5px 10px;font-size:13px;font-family:inherit">
    <option value="all">All Active</option>
    <option value="listed">Listed</option>
    <option value="exported">Exported</option>
  </select>
  <input id="search-input" type="text" placeholder="Search SKU or title..."
    oninput="debounceSearch()"
    style="background:#2c2c2a;border:1px solid #3a3a38;color:#f1efe8;border-radius:6px;padding:5px 10px;font-size:13px;font-family:inherit;width:220px">
  <span id="item-count" style="font-size:12px;color:#888780">Loading...</span>
  <div style="margin-left:auto;display:flex;gap:8px">
    <button class="btn btn-gray" onclick="syncFromEbay()" id="sync-btn">Sync from eBay</button>
    <button class="btn btn-gray" onclick="window.open('https://www.ebay.com/sh/lst/active','_blank')">eBay Seller Hub ↗</button>
  </div>
</div>

<div id="bulk-bar" style="display:none;background:#26215c;border-bottom:1px solid #3a3a38;padding:8px 24px;display:flex;align-items:center;gap:8px">
  <span id="bulk-count" style="font-size:12px;color:#afa9ec;margin-right:4px"></span>
  <button class="btn btn-gray" style="font-size:12px;padding:4px 10px" onclick="dlgBulkPrice()">Set Price</button>
  <button class="btn btn-gray" style="font-size:12px;padding:4px 10px" onclick="dlgBulkPromo()">Set Promo %</button>
  <button class="btn btn-purple" style="font-size:12px;padding:4px 10px" onclick="bulkPushAll()">Push All</button>
  <button class="btn btn-red" style="font-size:12px;padding:4px 10px" onclick="bulkEndListings()">End Listings</button>
  <button class="btn btn-gray" style="font-size:12px;padding:4px 10px" onclick="deselectAll()">Deselect All</button>
</div>

<div id="grid-wrapper">
  <div id="items-grid" class="grid-container">
    <div style="color:#888780;padding:24px;grid-column:1/-1;text-align:center">Loading...</div>
  </div>
</div>

<!-- Drawer -->
<div id="drawer-overlay" onclick="closeDrawer()"></div>
<div id="drawer">
  <div id="drawer-inner" style="flex:1"></div>
</div>

<!-- Context menu -->
<div id="ctx-menu">
  <div class="ctx-item" onclick="ctxOpenEbay()">Open on eBay ↗</div>
  <div class="ctx-item" onclick="ctxOpenSellerHub()">Open in Seller Hub ↗</div>
  <div class="ctx-sep"></div>
  <div class="ctx-item" onclick="ctxImproveTitle()">Improve Title with Claude</div>
  <div class="ctx-item" onclick="ctxPriceSuggest()">Suggest Market Price</div>
  <div class="ctx-sep"></div>
  <div class="ctx-item" style="color:#f09595" onclick="ctxEndListing()">End This Listing</div>
  <div class="ctx-item" onclick="ctxSendToReview()">Send to Review Queue</div>
</div>

<!-- Toast -->
<div id="toast"></div>

<!-- Dialog -->
<div id="dialog-overlay">
  <div id="dialog-box">
    <h3 id="dlg-title"></h3>
    <p id="dlg-body"></p>
    <div id="dlg-content"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
      <button class="btn btn-gray" onclick="closeDlg()" id="dlg-cancel">Cancel</button>
      <button class="btn btn-purple" onclick="dlgConfirm()" id="dlg-ok">OK</button>
    </div>
  </div>
</div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────
var allItems = [];
var selectedSkus = new Set();
var lastClickedIdx = null;
var drawerSku = null;
var drawerItem = null;
var dirtyFields = {{}};   // {{fieldName: {{value, original}}}}
var drawerPhotos = [];
var originalPhotos = [];
var alertDays = [30, 60, 90];
var defaultPromoPct = 3;
var ctxItem = null;
var dlgCallback = null;
var searchTimer = null;

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {{
  try {{
    const r = await fetch('/api/settings');
    const s = await r.json();
    const ad = s.listing_age_alert_days || '30,60,90';
    alertDays = ad.split(',').map(Number).filter(Boolean);
    defaultPromoPct = parseFloat(s.default_promotion_pct || '3') || 3;
  }} catch(e) {{}}
  loadItems();
}}

function debounceSearch() {{
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadItems, 300);
}}

async function loadItems() {{
  const st = document.getElementById('status-filter').value;
  const q = document.getElementById('search-input').value;
  try {{
    const r = await fetch('/api/listings?status=' + st + '&search=' + encodeURIComponent(q));
    allItems = await r.json();
  }} catch(e) {{
    showToast('Failed to load listings', false);
    allItems = [];
  }}
  selectedSkus = new Set();
  updateBulkBar();
  renderGrid();
}}

// ── Grid ──────────────────────────────────────────────────────────────────────
function renderGrid() {{
  const grid = document.getElementById('items-grid');
  document.getElementById('item-count').textContent = allItems.length + ' items';
  if (!allItems.length) {{
    grid.innerHTML = '<div style="color:#888780;padding:40px;grid-column:1/-1;text-align:center">No active listings found.</div>';
    return;
  }}
  grid.innerHTML = allItems.map((item, idx) => renderCard(item, idx)).join('');
}}

function renderCard(item, idx) {{
  const days = item.days_listed;
  let daysBadge = '';
  if (days != null) {{
    let color = '#888780', bg = '#2c2c2a';
    if (days > (alertDays[2] || 90)) {{ color = '#f09595'; bg = '#501313'; }}
    else if (days > (alertDays[1] || 60)) {{ color = '#fac775'; bg = '#412402'; }}
    else if (days > (alertDays[0] || 30)) {{ color = '#fac775'; bg = '#2c1a00'; }}
    daysBadge = '<span style="background:' + bg + ';color:' + color + ';padding:2px 6px;border-radius:4px;font-size:10px">' + days + 'd</span> ';
  }}
  const statusBadge = item.status === 'listed'
    ? '<span style="background:#085041;color:#9fe1cb;padding:2px 6px;border-radius:4px;font-size:10px">listed</span>'
    : '<span style="background:#26215c;color:#afa9ec;padding:2px 6px;border-radius:4px;font-size:10px">exported</span>';
  const flags = item.concern_flags;
  const concernDot = flags && flags !== '[]' && flags !== 'null' && flags !== ''
    ? '<span style="width:7px;height:7px;background:#f09595;border-radius:50%;display:inline-block;margin-left:4px;vertical-align:middle" title="Has concern flags"></span>'
    : '';
  const price = item.list_price != null ? '$' + parseFloat(item.list_price).toFixed(2) : '-';
  const sel = selectedSkus.has(item.sku);
  const thumb = item.cover_photo
    ? '<img class="card-thumb" src="' + esc(item.cover_photo) + '" onerror="this.style.display=\\'none\\'">'
    : '<div class="card-thumb-placeholder">📷</div>';
  return '<div class="listing-card' + (sel ? ' selected' : '') + '" data-idx="' + idx + '" data-sku="' + esc(item.sku) + '"'
    + ' onclick="cardClick(' + idx + ', event)" oncontextmenu="showCtxMenu(event,' + idx + ');return false;">'
    + '<input type="checkbox" class="card-check" ' + (sel ? 'checked' : '') + ' onclick="event.stopPropagation();toggleSelect(' + idx + ',event)">'
    + thumb
    + '<div style="flex:1;min-width:0;margin-left:10px">'
    + '<div style="font-family:monospace;font-size:11px;color:#888780">' + esc(item.sku) + '</div>'
    + '<div style="font-size:12px;color:#f1efe8;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin:3px 0;line-height:1.4">' + esc(item.title || '-') + '</div>'
    + '<div style="font-size:13px;color:#5dcaa5;font-weight:500">' + price + '</div>'
    + '<div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap;align-items:center">'
    + statusBadge + ' ' + daysBadge + concernDot
    + '</div></div></div>';
}}

function cardClick(idx, event) {{
  if (event.target.classList.contains('card-check')) return;
  if (event.shiftKey && lastClickedIdx !== null) {{
    toggleSelect(idx, event);
    return;
  }}
  openDrawer(allItems[idx]);
  lastClickedIdx = idx;
}}

// ── Multi-select ──────────────────────────────────────────────────────────────
function toggleSelect(idx, event) {{
  const item = allItems[idx];
  if (!item) return;
  if (event && event.shiftKey && lastClickedIdx !== null) {{
    const lo = Math.min(lastClickedIdx, idx);
    const hi = Math.max(lastClickedIdx, idx);
    for (let i = lo; i <= hi; i++) {{
      selectedSkus.add(allItems[i].sku);
    }}
  }} else {{
    if (selectedSkus.has(item.sku)) selectedSkus.delete(item.sku);
    else selectedSkus.add(item.sku);
  }}
  lastClickedIdx = idx;
  renderGrid();
  updateBulkBar();
}}

function updateBulkBar() {{
  const bar = document.getElementById('bulk-bar');
  if (selectedSkus.size >= 2) {{
    bar.style.display = 'flex';
    document.getElementById('bulk-count').textContent = selectedSkus.size + ' selected';
  }} else {{
    bar.style.display = 'none';
  }}
}}

function deselectAll() {{
  selectedSkus = new Set();
  renderGrid();
  updateBulkBar();
}}

// ── Drawer ────────────────────────────────────────────────────────────────────
function openDrawer(item) {{
  drawerSku = item.sku;
  drawerItem = item;
  dirtyFields = {{}};
  drawerPhotos = (item.image_paths || '').split('|').filter(Boolean);
  originalPhotos = [...drawerPhotos];
  renderDrawer(item);
  document.getElementById('drawer-overlay').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}}

function closeDrawer() {{
  document.getElementById('drawer-overlay').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
  drawerSku = null;
  drawerItem = null;
  dirtyFields = {{}};
}}

function renderDrawer(item) {{
  const hasListingId = item.listing_id && item.listing_id !== '';
  const hasOfferId = item.offer_id && item.offer_id !== '';
  const ebayLink = hasListingId
    ? '<a href="https://www.ebay.com/itm/' + item.listing_id + '" target="_blank" style="color:#7f77dd;text-decoration:none;font-size:12px">Open on eBay ↗</a>'
    : '<span style="color:#3a3a38;font-size:12px" title="No listing ID">Open on eBay ↗</span>';
  const statusBadge = item.status === 'listed'
    ? '<span class="badge" style="background:#085041;color:#9fe1cb">listed</span>'
    : '<span class="badge" style="background:#26215c;color:#afa9ec">exported</span>';

  const condOptions = ['NEW','LIKE_NEW','VERY_GOOD','USED_GOOD','USED_ACCEPTABLE','FOR_PARTS_OR_NOT_WORKING']
    .map(c => '<option value="' + c + '"' + (item.condition === c ? ' selected' : '') + '>' + c.replace(/_/g,' ') + '</option>').join('');

  const promoPct = item.promotion_pct || defaultPromoPct;

  document.getElementById('drawer-inner').innerHTML = `
<div class="drawer-section" style="position:sticky;top:0;background:#111110;z-index:10">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <div style="font-family:monospace;font-size:15px;color:#f1efe8;font-weight:500">${{esc(item.sku)}}</div>
      <div style="margin-top:4px;display:flex;gap:8px;align-items:center">
        ${{statusBadge}}
        ${{ebayLink}}
        <a href="https://www.ebay.com/sh/lst/active" target="_blank" style="color:#888780;text-decoration:none;font-size:11px">Seller Hub ↗</a>
      </div>
    </div>
    <button onclick="closeDrawer()" style="background:none;border:none;color:#888780;font-size:20px;cursor:pointer;padding:0;line-height:1">&#10005;</button>
  </div>
</div>

<div class="drawer-section" id="drawer-photos">
  <div class="drawer-section-title">Photos</div>
  <div class="photo-scroll" id="photo-row"></div>
  <div style="margin-top:8px;display:flex;align-items:center;gap:8px">
    <button class="btn btn-gray" style="font-size:12px;padding:4px 10px" onclick="document.getElementById('photo-upload').click()">+ Add Photos</button>
    <input type="file" id="photo-upload" multiple accept="image/*" style="display:none" onchange="handlePhotoUpload(this.files)">
    <span style="font-size:10px;color:#888780">Use Set Cover to change cover photo.</span>
  </div>
</div>

<div class="drawer-section">
  <div class="drawer-section-title">Fields</div>

  <div class="field-row" id="field-row-title">
    <label>Title <span id="revert-title" class="revert-link" style="display:none" onclick="revertField('title')">Revert</span></label>
    <input type="text" id="field-title" value="${{esc(item.title||'')}}" maxlength="80"
      oninput="markDirty('title',this.value); updateCharCounter()">
    <div class="char-counter"><span id="char-counter">0</span>/80</div>
    <button class="btn btn-gray" style="font-size:11px;padding:3px 8px;margin-top:4px" onclick="claudeSuggest('title')">Improve with Claude</button>
    <div class="suggest-panel" id="suggest-panel-title">
      <div class="drawer-section-title" style="margin-bottom:6px">Claude Suggestion</div>
      <div class="suggest-text" id="suggest-text-title"></div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-green" style="font-size:11px;padding:3px 8px" onclick="acceptSuggestion('title')">Accept</button>
        <button class="btn btn-gray" style="font-size:11px;padding:3px 8px" onclick="editSuggestion('title')">Edit</button>
        <button class="btn btn-gray" style="font-size:11px;padding:3px 8px" onclick="dismissSuggestion('title')">Dismiss</button>
      </div>
    </div>
  </div>

  <div class="field-row" id="field-row-description">
    <label>Description <span id="revert-description" class="revert-link" style="display:none" onclick="revertField('description')">Revert</span></label>
    <textarea id="field-description" rows="5" style="resize:vertical" oninput="markDirty('description',this.value)">${{esc(item.description||'')}}</textarea>
    <button class="btn btn-gray" style="font-size:11px;padding:3px 8px;margin-top:4px" onclick="claudeSuggest('description')">Improve with Claude</button>
    <div class="suggest-panel" id="suggest-panel-description">
      <div class="drawer-section-title" style="margin-bottom:6px">Claude Suggestion</div>
      <div class="suggest-text" id="suggest-text-description"></div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-green" style="font-size:11px;padding:3px 8px" onclick="acceptSuggestion('description')">Accept</button>
        <button class="btn btn-gray" style="font-size:11px;padding:3px 8px" onclick="editSuggestion('description')">Edit</button>
        <button class="btn btn-gray" style="font-size:11px;padding:3px 8px" onclick="dismissSuggestion('description')">Dismiss</button>
      </div>
    </div>
  </div>

  <div class="field-row" id="field-row-price">
    <label>Price ($) <span id="revert-price" class="revert-link" style="display:none" onclick="revertField('price')">Revert</span></label>
    <input type="number" id="field-price" value="${{item.list_price||''}}" step="0.01" min="0"
      oninput="markDirty('price',parseFloat(this.value))">
    <button class="btn btn-gray" style="font-size:11px;padding:3px 8px;margin-top:4px" onclick="suggestPrice()">Suggest Market Price</button>
    <div id="price-note" class="price-note"></div>
  </div>

  <div class="field-row" id="field-row-condition">
    <label>Condition <span id="revert-condition" class="revert-link" style="display:none" onclick="revertField('condition')">Revert</span></label>
    <select id="field-condition" onchange="markDirty('condition',this.value)">
      ${{condOptions}}
    </select>
  </div>

  <div class="field-row">
    <label>Category</label>
    <div style="display:flex;align-items:center;gap:8px">
      <span id="category-display" style="font-size:13px;color:#f1efe8">${{esc(item.ebay_category_name||'Unknown')}}</span>
      <button class="btn btn-gray" style="font-size:11px;padding:3px 8px" onclick="recategorize()">Recategorize</button>
    </div>
  </div>
</div>

<div class="drawer-section">
  <div class="drawer-section-title">Promotion</div>
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin:0">
      <input type="checkbox" id="promo-toggle" onchange="togglePromo(this.checked)"
        ${{item.promotion_pct ? 'checked' : ''}}>
      <span style="font-size:13px;color:#d4d2c8">Promote this listing</span>
    </label>
  </div>
  <div id="promo-pct-row" style="${{item.promotion_pct ? '' : 'display:none'}}">
    <label>Promotion % (2–20)</label>
    <input type="number" id="field-promo-pct" value="${{promoPct}}"
      min="2" max="20" step="0.5" style="width:100px">
  </div>
  <div style="font-size:11px;color:#888780;margin-top:6px">Changes applied when you push to eBay.</div>
</div>

<div class="drawer-section">
  <div class="drawer-section-title">Push to eBay</div>
  ${{!hasOfferId ? '<div style="background:#412402;color:#fac775;padding:8px 10px;border-radius:6px;font-size:12px;margin-bottom:10px">No offer ID stored. Publish this item first via the Export tab.</div>' : ''}}
  <div id="push-steps" style="margin-bottom:10px"></div>
  <button class="btn btn-purple" style="width:100%;padding:9px" onclick="pushToEbay()" id="push-btn" ${{!hasOfferId ? 'disabled style="opacity:.5;cursor:default"' : ''}}>Push to eBay</button>
  <button class="btn btn-red" style="width:100%;padding:7px;margin-top:8px" onclick="confirmEndListing()">End Listing</button>
</div>
`;

  renderPhotoRow();
  updateCharCounter();
}}

// ── Photos ────────────────────────────────────────────────────────────────────
function renderPhotoRow() {{
  const row = document.getElementById('photo-row');
  if (!row) return;
  if (!drawerPhotos.length) {{
    row.innerHTML = '<span style="color:#888780;font-size:12px">No photos</span>';
    return;
  }}
  row.innerHTML = drawerPhotos.map((url, i) => `
<div class="photo-thumb">
  <img src="${{esc(url)}}" onerror="this.src=''" title="${{esc(url)}}">
  ${{i === 0 ? '<div class="photo-cover-badge">Cover</div>' : ''}}
  <div class="photo-actions">
    ${{i !== 0 ? `<button class="photo-btn" onclick="setCover('${{esc(url)}}')">Set Cover</button>` : ''}}
    <button class="photo-btn" style="color:#f09595" onclick="deletePhoto('${{esc(url)}}')">Del</button>
  </div>
</div>`).join('');
}}

async function handlePhotoUpload(files) {{
  if (!files.length || !drawerSku) return;
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/photos', {{method:'POST', body:fd}});
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    drawerPhotos = d.image_paths;
    renderPhotoRow();
    // Update cover photo in grid
    const idx = allItems.findIndex(i => i.sku === drawerSku);
    if (idx >= 0) {{ allItems[idx].image_paths = drawerPhotos.join('|'); allItems[idx].cover_photo = drawerPhotos[0] || null; renderGrid(); }}
    showToast('Photos uploaded', true);
  }} catch(e) {{
    showToast('Upload failed: ' + e, false);
  }}
  document.getElementById('photo-upload').value = '';
}}

async function deletePhoto(url) {{
  if (!drawerSku) return;
  if (!confirm('Remove this photo from the listing?')) return;
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/photos', {{
      method:'DELETE', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{url}})
    }});
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    drawerPhotos = d.image_paths;
    renderPhotoRow();
    showToast('Photo removed', true);
  }} catch(e) {{
    showToast('Failed: ' + e, false);
  }}
}}

async function setCover(url) {{
  if (!drawerSku) return;
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/photos/set-cover', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{url}})
    }});
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    drawerPhotos = d.image_paths;
    renderPhotoRow();
    // Update grid
    const idx = allItems.findIndex(i => i.sku === drawerSku);
    if (idx >= 0) {{ allItems[idx].cover_photo = drawerPhotos[0] || null; allItems[idx].image_paths = drawerPhotos.join('|'); renderGrid(); }}
    showToast('Cover photo updated', true);
  }} catch(e) {{
    showToast('Failed: ' + e, false);
  }}
}}

// ── Dirty fields ──────────────────────────────────────────────────────────────
function markDirty(field, value) {{
  if (!(field in dirtyFields)) {{
    let original;
    if (field === 'title') original = drawerItem.title || '';
    else if (field === 'description') original = drawerItem.description || '';
    else if (field === 'price') original = drawerItem.list_price;
    else if (field === 'condition') original = drawerItem.condition || '';
    dirtyFields[field] = {{value, original}};
  }} else {{
    dirtyFields[field].value = value;
  }}
  const row = document.getElementById('field-row-' + field);
  const inp = document.getElementById('field-' + field);
  const rev = document.getElementById('revert-' + field);
  if (row) row.classList.add('field-dirty');
  if (rev) rev.style.display = 'inline';
}}

function revertField(field) {{
  if (!(field in dirtyFields)) return;
  const orig = dirtyFields[field].original;
  delete dirtyFields[field];
  if (field === 'title') document.getElementById('field-title').value = orig || '';
  if (field === 'description') document.getElementById('field-description').value = orig || '';
  if (field === 'price') document.getElementById('field-price').value = orig != null ? orig : '';
  if (field === 'condition') document.getElementById('field-condition').value = orig || '';
  const row = document.getElementById('field-row-' + field);
  const rev = document.getElementById('revert-' + field);
  if (row) row.classList.remove('field-dirty');
  if (rev) rev.style.display = 'none';
  updateCharCounter();
}}

function updateCharCounter() {{
  const el = document.getElementById('field-title');
  const ctr = document.getElementById('char-counter');
  if (!el || !ctr) return;
  const n = (el.value || '').length;
  ctr.textContent = n;
  ctr.style.color = n > 75 ? '#f09595' : n > 59 ? '#fac775' : '#888780';
}}

// ── Claude suggest ────────────────────────────────────────────────────────────
async function claudeSuggest(type) {{
  if (!drawerSku) return;
  const panel = document.getElementById('suggest-panel-' + type);
  const textEl = document.getElementById('suggest-text-' + type);
  panel.classList.add('open');
  textEl.textContent = 'Generating suggestion...';
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/claude-suggest', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{type}})
    }});
    if (!r.ok) {{
      const e = await r.json();
      const detail = e.detail;
      const message = typeof detail === 'string' ? detail : (detail && detail.message) || 'API error';
      throw new Error(message);
    }}
    const d = await r.json();
    textEl.textContent = d.suggestion;
    panel.dataset.suggestion = d.suggestion;
  }} catch(e) {{
    textEl.textContent = 'Error: ' + e.message;
    panel.dataset.suggestion = '';
  }}
}}

function acceptSuggestion(type) {{
  const panel = document.getElementById('suggest-panel-' + type);
  const suggestion = panel.dataset.suggestion || '';
  if (!suggestion) return;
  const field = type === 'title' ? 'field-title' : 'field-description';
  document.getElementById(field).value = suggestion;
  markDirty(type === 'title' ? 'title' : 'description', suggestion);
  updateCharCounter();
  panel.classList.remove('open');
}}

function editSuggestion(type) {{
  const panel = document.getElementById('suggest-panel-' + type);
  const suggestion = panel.dataset.suggestion || '';
  const field = type === 'title' ? 'field-title' : 'field-description';
  document.getElementById(field).value = suggestion;
  markDirty(type === 'title' ? 'title' : 'description', suggestion);
  updateCharCounter();
  panel.classList.remove('open');
  document.getElementById(field).focus();
}}

function dismissSuggestion(type) {{
  document.getElementById('suggest-panel-' + type).classList.remove('open');
}}

// ── Price suggest ─────────────────────────────────────────────────────────────
async function suggestPrice() {{
  if (!drawerSku) return;
  const btn = event.target;
  btn.textContent = 'Looking up comps...';
  btn.disabled = true;
  const noteEl = document.getElementById('price-note');
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/price-suggest');
    if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail || 'API error'); }}
    const d = await r.json();
    if (d.suggested_price != null) {{
      const inp = document.getElementById('field-price');
      inp.value = d.suggested_price.toFixed(2);
      markDirty('price', d.suggested_price);
      noteEl.textContent = 'Based on ' + d.sample_size + ' sold comps: median $'
        + (d.median||0).toFixed(2) + ', range $' + (d.low||0).toFixed(2) + '–$' + (d.high||0).toFixed(2);
    }} else {{
      noteEl.textContent = 'No comps found for this item.';
    }}
  }} catch(e) {{
    noteEl.textContent = 'Error: ' + e.message;
  }} finally {{
    btn.textContent = 'Suggest Market Price';
    btn.disabled = false;
  }}
}}

// ── Promotion ─────────────────────────────────────────────────────────────────
function togglePromo(enabled) {{
  document.getElementById('promo-pct-row').style.display = enabled ? '' : 'none';
}}

// ── Recategorize ──────────────────────────────────────────────────────────────
async function recategorize() {{
  if (!drawerSku) return;
  const btn = event.target;
  btn.textContent = 'Working...';
  btn.disabled = true;
  try {{
    const r = await fetch('/api/items/' + drawerSku + '/recategorize', {{method:'POST'}});
    if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail||'error'); }}
    const d = await r.json();
    document.getElementById('category-display').textContent = d.ebay_category_name || 'Unknown';
    showToast('Category updated to: ' + (d.ebay_category_name||'Unknown'), true);
  }} catch(e) {{
    showToast('Recategorize failed: ' + e.message, false);
  }} finally {{
    btn.textContent = 'Recategorize';
    btn.disabled = false;
  }}
}}

// ── Push to eBay ──────────────────────────────────────────────────────────────
async function pushToEbay() {{
  if (!drawerSku) return;
  const btn = document.getElementById('push-btn');
  const stepsEl = document.getElementById('push-steps');
  btn.disabled = true;
  btn.textContent = 'Pushing...';

  const payload = {{
    title: document.getElementById('field-title')?.value || null,
    description: document.getElementById('field-description')?.value || null,
    list_price: parseFloat(document.getElementById('field-price')?.value) || null,
    condition: document.getElementById('field-condition')?.value || null,
    promotion_enabled: document.getElementById('promo-toggle')?.checked || false,
    promotion_pct: parseFloat(document.getElementById('field-promo-pct')?.value) || null,
    photos_changed: JSON.stringify(drawerPhotos) !== JSON.stringify(originalPhotos),
  }};

  stepsEl.innerHTML = '<div class="step-row"><span class="step-icon">⏳</span> Pushing to eBay...</div>';
  try {{
    const r = await fetch('/api/listings/push/' + drawerSku, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(payload)
    }});
    const d = await r.json();
    if (!r.ok) {{
      stepsEl.innerHTML = '<div class="step-row"><span class="step-icon">❌</span><span style="color:#f09595">' + esc(d.detail||'Error') + '</span></div>';
      showToast('Push failed', false);
      return;
    }}

    stepsEl.innerHTML = (d.steps||[]).map(s =>
      '<div class="step-row"><span class="step-icon">' + (s.ok ? '✅' : '❌') + '</span>'
      + '<span style="color:' + (s.ok ? '#9fe1cb' : '#f09595') + '">' + esc(s.msg) + '</span></div>'
    ).join('');

    if (d.ok) {{
      // Clear dirty borders
      ['title','description','price','condition'].forEach(f => {{
        const row = document.getElementById('field-row-' + f);
        const rev = document.getElementById('revert-' + f);
        if (row) row.classList.remove('field-dirty');
        if (rev) rev.style.display = 'none';
      }});
      dirtyFields = {{}};
      originalPhotos = [...drawerPhotos];
      // Update card in grid
      const idx = allItems.findIndex(i => i.sku === drawerSku);
      if (idx >= 0 && d.item) {{
        if (d.item.title) allItems[idx].title = d.item.title;
        if (d.item.list_price) allItems[idx].list_price = d.item.list_price;
        renderGrid();
      }}
      showToast('Pushed successfully', true);
    }} else {{
      showToast('Push completed with errors', false);
    }}
  }} catch(e) {{
    stepsEl.innerHTML = '<div class="step-row"><span class="step-icon">❌</span><span style="color:#f09595">' + esc(e.message) + '</span></div>';
    showToast('Push failed: ' + e.message, false);
  }} finally {{
    btn.disabled = false;
    btn.textContent = 'Push to eBay';
  }}
}}

// ── End listing ───────────────────────────────────────────────────────────────
function confirmEndListing() {{
  if (!drawerSku) return;
  showDialog(
    'End This Listing',
    'End this listing on eBay? This removes it from active listings. The item will remain in your inventory and can be relisted from the Export tab.',
    null,
    async () => {{
      try {{
        const r = await fetch('/api/listings/end/' + drawerSku, {{method:'DELETE'}});
        if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail||'Error'); }}
        showToast('Listing ended', true);
        closeDrawer();
        loadItems();
      }} catch(e) {{
        showToast('End listing failed: ' + e.message, false);
      }}
    }},
    'End Listing',
    'btn-red'
  );
}}

// ── Sync ──────────────────────────────────────────────────────────────────────
async function syncFromEbay() {{
  const btn = document.getElementById('sync-btn');
  btn.textContent = 'Syncing...';
  btn.disabled = true;
  try {{
    const r = await fetch('/api/listings/sync');
    if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail||'error'); }}
    const d = await r.json();
    showToast('Synced ' + d.synced + ' items, updated ' + d.updated, true);
    loadItems();
  }} catch(e) {{
    showToast('Sync failed: ' + e.message, false);
  }} finally {{
    btn.textContent = 'Sync from eBay';
    btn.disabled = false;
  }}
}}

// ── Context menu ──────────────────────────────────────────────────────────────
function showCtxMenu(event, idx) {{
  ctxItem = allItems[idx];
  const menu = document.getElementById('ctx-menu');
  menu.style.display = 'block';
  menu.style.left = event.clientX + 'px';
  menu.style.top = event.clientY + 'px';
  // Clamp to viewport
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = (event.clientX - rect.width) + 'px';
  if (rect.bottom > window.innerHeight) menu.style.top = (event.clientY - rect.height) + 'px';
}}

function hideCtxMenu() {{
  document.getElementById('ctx-menu').style.display = 'none';
  ctxItem = null;
}}

function ctxOpenEbay() {{
  if (!ctxItem) return;
  if (ctxItem.listing_id) window.open('https://www.ebay.com/itm/' + ctxItem.listing_id, '_blank');
  hideCtxMenu();
}}

function ctxOpenSellerHub() {{
  window.open('https://www.ebay.com/sh/lst/active', '_blank');
  hideCtxMenu();
}}

async function ctxImproveTitle() {{
  if (!ctxItem) return;
  openDrawer(ctxItem);
  hideCtxMenu();
  await new Promise(r => setTimeout(r, 100));
  claudeSuggest('title');
}}

async function ctxPriceSuggest() {{
  if (!ctxItem) return;
  openDrawer(ctxItem);
  hideCtxMenu();
  await new Promise(r => setTimeout(r, 100));
  suggestPrice();
}}

async function ctxEndListing() {{
  if (!ctxItem) return;
  const sku = ctxItem.sku;
  hideCtxMenu();
  showDialog(
    'End This Listing',
    'End listing for ' + sku + '? This removes it from active eBay listings.',
    null,
    async () => {{
      try {{
        const r = await fetch('/api/listings/end/' + sku, {{method:'DELETE'}});
        if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail||'Error'); }}
        showToast('Listing ended: ' + sku, true);
        loadItems();
      }} catch(e) {{
        showToast('Failed: ' + e.message, false);
      }}
    }},
    'End Listing', 'btn-red'
  );
}}

async function ctxSendToReview() {{
  if (!ctxItem) return;
  const sku = ctxItem.sku;
  hideCtxMenu();
  try {{
    const r = await fetch('/api/items/' + sku, {{
      method:'PATCH', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        status: 'needs_review',
        review_sub_queue: 'awaiting_approval',
        reviewer_notes: 'Manually sent from Listings page'
      }})
    }});
    if (!r.ok) {{ const e = await r.json(); throw new Error(e.detail||'Error'); }}
    showToast('Sent to Review Queue: ' + sku, true);
    loadItems();
  }} catch(e) {{
    showToast('Failed: ' + e.message, false);
  }}
}}

// ── Bulk actions ──────────────────────────────────────────────────────────────
function dlgBulkPrice() {{
  showDialog('Set Price', 'Enter the new price for ' + selectedSkus.size + ' items:',
    '<input type="number" id="dlg-input" step="0.01" min="0" placeholder="0.00" style="width:100%;margin-top:4px">',
    async () => {{
      const price = parseFloat(document.getElementById('dlg-input').value);
      if (!price || price <= 0) {{ showToast('Invalid price', false); return; }}
      const r = await fetch('/api/listings/bulk/price', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{skus: [...selectedSkus], price}})
      }});
      const d = await r.json();
      showToast('Updated ' + d.updated.length + ' items', true);
      loadItems();
    }}
  );
}}

function dlgBulkPromo() {{
  showDialog('Set Promo %', 'Enter promotion % for ' + selectedSkus.size + ' items (2–20):',
    '<input type="number" id="dlg-input" step="0.5" min="2" max="20" placeholder="3" style="width:100%;margin-top:4px">',
    async () => {{
      const pct = parseFloat(document.getElementById('dlg-input').value);
      if (!pct || pct < 2 || pct > 20) {{ showToast('Invalid %', false); return; }}
      const r = await fetch('/api/listings/bulk/promo', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{skus: [...selectedSkus], promotion_pct: pct}})
      }});
      const d = await r.json();
      showToast('Updated ' + d.updated.length + ' items', true);
      loadItems();
    }}
  );
}}

async function bulkPushAll() {{
  const skus = [...selectedSkus];
  if (!skus.length) return;
  if (!confirm('Push ' + skus.length + ' listings to eBay?')) return;

  const bar = document.getElementById('bulk-bar');
  bar.innerHTML = '<span style="color:#afa9ec;font-size:12px">Pushing 0/' + skus.length + '...</span>';
  let pass = 0, fail = 0;

  for (const sku of skus) {{
    try {{
      const r = await fetch('/api/listings/push/' + sku, {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{promotion_enabled:false}})
      }});
      const d = await r.json();
      if (d.ok) pass++; else fail++;
    }} catch(e) {{ fail++; }}
    bar.innerHTML = '<span style="color:#afa9ec;font-size:12px">Pushing ' + (pass+fail) + '/' + skus.length + '...</span>';
  }}

  showToast('Bulk push done: ' + pass + ' ok, ' + fail + ' failed', pass > 0);
  deselectAll();
  loadItems();
}}

async function bulkEndListings() {{
  const skus = [...selectedSkus];
  showDialog(
    'End ' + skus.length + ' Listings',
    'End all ' + skus.length + ' selected listings on eBay? Items will move to Export Ready.',
    null,
    async () => {{
      let done = 0;
      for (const sku of skus) {{
        try {{
          await fetch('/api/listings/end/' + sku, {{method:'DELETE'}});
          done++;
        }} catch(e) {{}}
      }}
      showToast('Ended ' + done + ' listings', true);
      deselectAll();
      loadItems();
    }},
    'End All', 'btn-red'
  );
}}

// ── Dialog ────────────────────────────────────────────────────────────────────
function showDialog(title, body, contentHtml, onOk, okLabel, okClass) {{
  document.getElementById('dlg-title').textContent = title;
  document.getElementById('dlg-body').textContent = body;
  document.getElementById('dlg-content').innerHTML = contentHtml || '';
  const okBtn = document.getElementById('dlg-ok');
  okBtn.textContent = okLabel || 'OK';
  okBtn.className = 'btn ' + (okClass || 'btn-purple');
  dlgCallback = onOk;
  document.getElementById('dialog-overlay').classList.add('open');
}}

async function dlgConfirm() {{
  closeDlg();
  if (dlgCallback) await dlgCallback();
}}

function closeDlg() {{
  document.getElementById('dialog-overlay').classList.remove('open');
  dlgCallback = null;
}}

// ── Toast ─────────────────────────────────────────────────────────────────────
var toastTimer = null;
function showToast(msg, ok) {{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + (ok ? 'ok' : 'err');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = '', 3500);
}}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

// ── Close on outside click ────────────────────────────────────────────────────
document.addEventListener('click', function(e) {{
  const menu = document.getElementById('ctx-menu');
  if (menu.style.display === 'block' && !menu.contains(e.target)) hideCtxMenu();
}});
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    hideCtxMenu();
    closeDrawer();
    closeDlg();
  }}
}});

init();
</script>
</body></html>"""


def _diagnostics_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Diagnostic Cockpit v1 - Resale AI System</title>
{_base_style()}
<style>
.cockpit-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; }}
.panel {{ background:#181816; border:1px solid #2c2c2a; border-radius:10px; padding:16px; }}
.panel h2 {{ margin-bottom:10px; }}
.panel p {{ font-size:13px; color:#b4b0a6; }}
.banner {{ background:linear-gradient(135deg,#312e08,#1f1b06); border:1px solid #655d19; border-radius:10px; padding:16px; margin-bottom:18px; }}
.banner h2 {{ margin-bottom:8px; }}
.banner-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; margin-top:10px; }}
.banner-card {{ background:rgba(0,0,0,.18); border:1px solid rgba(250,199,117,.18); border-radius:8px; padding:10px; }}
.banner-card strong {{ display:block; color:#fac775; font-size:12px; margin-bottom:4px; }}
.controls {{ display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end; margin-bottom:12px; }}
.controls > div {{ flex:1 1 180px; min-width:180px; }}
.controls .btn {{ margin-top:18px; }}
.meta-row {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }}
.tag {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:11px; background:#2c2c2a; color:#d4d2c8; border:1px solid #3a3a38; }}
.tag.ok {{ background:#12382f; color:#9fe1cb; border-color:#1b6d55; }}
.tag.warn {{ background:#412402; color:#fac775; border-color:#6d4308; }}
.tag.err {{ background:#501313; color:#f09595; border-color:#7f2323; }}
.tag.info {{ background:#1e2347; color:#b7b6f8; border-color:#3c4191; }}
.tag.mono {{ font-family:Consolas, monospace; }}
.summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px; margin:12px 0; }}
.summary-card {{ background:#111110; border:1px solid #2c2c2a; border-radius:8px; padding:10px; }}
.summary-card .label {{ display:block; color:#888780; font-size:11px; margin-bottom:4px; }}
.summary-card .value {{ display:block; color:#f1efe8; font-size:20px; }}
.section-note {{ color:#888780; font-size:12px; margin-bottom:12px; }}
.status-line {{ min-height:20px; font-size:12px; margin-bottom:10px; color:#888780; }}
.status-line.ok {{ color:#9fe1cb; }}
.status-line.err {{ color:#f09595; }}
.text-output {{ width:100%; min-height:120px; font-family:Consolas, monospace; font-size:12px; resize:vertical; }}
.copy-row {{ display:flex; gap:8px; align-items:center; margin:8px 0 12px; }}
.copy-row span {{ font-size:12px; color:#888780; }}
.data-list {{ display:flex; flex-wrap:wrap; gap:6px; }}
.result-cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; margin-top:12px; }}
.result-card {{ background:#111110; border:1px solid #2c2c2a; border-radius:10px; padding:14px; }}
.result-card h3 {{ font-size:14px; color:#f1efe8; margin-bottom:8px; }}
.result-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; margin-bottom:10px; }}
.kv .k {{ color:#888780; font-size:11px; margin-bottom:2px; }}
.kv .v {{ color:#f1efe8; font-size:12px; word-break:break-word; }}
.compact-list {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }}
.compact-list.empty {{ color:#888780; font-size:12px; }}
.safety-box {{ background:#151d18; border:1px solid #235b3d; border-radius:8px; padding:10px; margin-top:10px; }}
.safety-box.report {{ background:#171926; border-color:#3847a0; }}
.safety-box.warn {{ background:#231e11; border-color:#7a5f14; }}
.table-wrap {{ overflow:auto; border:1px solid #2c2c2a; border-radius:8px; }}
.timeline {{ display:flex; flex-direction:column; gap:10px; }}
.timeline-item {{ background:#111110; border:1px solid #2c2c2a; border-radius:8px; padding:12px; }}
.timeline-item .top {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; flex-wrap:wrap; }}
.timeline-item .when {{ color:#888780; font-size:12px; }}
.timeline-item .msg {{ margin:0; background:#1c1c1a; }}
.report-block {{ background:#111110; border:1px solid #2c2c2a; border-radius:10px; padding:14px; margin-top:12px; }}
.report-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; margin-top:12px; }}
.report-box {{ background:#181816; border:1px solid #2c2c2a; border-radius:8px; padding:10px; }}
.report-box h3 {{ font-size:12px; margin-bottom:8px; color:#f1efe8; }}
details {{ margin-top:8px; }}
details summary {{ cursor:pointer; color:#afa9ec; font-size:12px; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:#0f0f0e; border:1px solid #222220; border-radius:8px; padding:10px; font-size:12px; overflow:auto; }}
.empty {{ color:#888780; font-size:13px; padding:12px 4px; }}
@media (max-width: 900px) {{
  .result-grid {{ grid-template-columns:1fr; }}
  .controls > div {{ min-width:100%; }}
}}
</style>
</head>
<body>
{_nav("diagnostics")}
<main>
  <div class="banner">
    <h2>Diagnostic Cockpit v1</h2>
    <p>Local-only operator cockpit for current-state publish diagnostics, historical runtime outcomes, and local generated diagnostic reports.</p>
    <div class="banner-grid">
      <div class="banner-card"><strong>Local-only</strong><span>Runs in the local app UI and does not send reports externally.</span></div>
      <div class="banner-card"><strong>No live eBay mutation</strong><span>This cockpit is for read-only diagnostics and local report generation only.</span></div>
      <div class="banner-card"><strong>Readiness diagnostics</strong><span>Preflight/current-state checks for whether a SKU looks publish-safe.</span></div>
      <div class="banner-card"><strong>Operation events</strong><span>Historical runtime outcomes recorded by the operation diagnostics ledger.</span></div>
      <div class="banner-card"><strong>Reports</strong><span>Local generated summaries for weekly trends, sessions, SKUs, and root-cause analysis.</span></div>
    </div>
  </div>

  <div class="cockpit-grid">
    <section class="panel" style="grid-column:1 / -1">
      <h2>SKU Batch Diagnostics</h2>
      <div class="section-note">Run read-only publish diagnostics for multiple SKUs. One SKU per line or comma-separated. This does not publish and does not mutate eBay.</div>
      <div class="controls">
        <div style="flex:2 1 320px">
          <label for="batch-skus">SKUs</label>
          <textarea id="batch-skus" rows="5" placeholder="BK-000008&#10;CL-000019&#10;TO-000016">BK-000008
CL-000019
TO-000016</textarea>
        </div>
        <div>
          <label for="allow-live-readonly">Live read-only checks</label>
          <select id="allow-live-readonly">
            <option value="true" selected>Allow live read-only GETs</option>
            <option value="false">Local-only diagnostics</option>
          </select>
        </div>
        <div>
          <label for="batch-preview-statuses">Preview status filters</label>
          <input id="batch-preview-statuses" value="approved, export_ready" placeholder="approved, export_ready">
        </div>
        <div style="flex:0 0 auto">
          <button class="btn btn-purple" onclick="runBatchDiagnostics()">Run Read-Only Publish Diagnostics</button>
          <button class="btn btn-green" onclick="runBulkPublishPreview()" style="margin-top:8px">Preview Bulk Publish</button>
        </div>
      </div>
      <div id="batch-status" class="status-line">No batch diagnostics run yet.</div>
      <div id="batch-summary" class="summary-grid"></div>
      <div id="batch-families" class="data-list"></div>
      <div id="batch-lanes" class="data-list"></div>
      <div id="batch-safety"></div>
      <div id="batch-results" class="result-cards"><div class="empty">No per-SKU diagnostics loaded.</div></div>
      <div class="report-grid">
        <div class="report-box">
          <h3>Copyable Report Markdown</h3>
          <div class="copy-row"><button class="btn btn-gray" onclick="copyField('batch-markdown')">Copy</button><span>Shareable local debug summary.</span></div>
          <textarea id="batch-markdown" class="text-output" readonly placeholder="Batch diagnostics markdown will appear here."></textarea>
        </div>
        <div class="report-box">
          <h3>Copyable Codex Prompt</h3>
          <div class="copy-row"><button class="btn btn-gray" onclick="copyField('batch-prompt')">Copy</button><span>Paste into Codex/GPT for deeper analysis.</span></div>
          <textarea id="batch-prompt" class="text-output" readonly placeholder="Batch diagnostics Codex prompt will appear here."></textarea>
        </div>
      </div>
      <div class="report-block">
        <h2>Bulk Publish Preview</h2>
        <div class="section-note">Read-only batch publish dry-run. Uses the same item-level safety gate as batch publish, returns operator decisions, and never calls eBay mutation methods.</div>
        <div id="batch-preview-status" class="status-line">No bulk publish preview run yet.</div>
        <div id="batch-preview-summary" class="summary-grid"></div>
        <div id="batch-preview-groups" class="data-list"></div>
        <div id="batch-preview-safety"></div>
        <div id="batch-preview-results" class="result-cards"><div class="empty">No bulk publish preview loaded.</div></div>
        <div class="report-grid">
          <div class="report-box">
            <h3>Preview Report Markdown</h3>
            <div class="copy-row"><button class="btn btn-gray" onclick="copyField('batch-preview-markdown')">Copy</button><span>Operator-ready dry-run summary.</span></div>
            <textarea id="batch-preview-markdown" class="text-output" readonly placeholder="Bulk publish preview markdown will appear here."></textarea>
          </div>
          <div class="report-box">
            <h3>Persisted Report Path</h3>
            <div class="copy-row"><button class="btn btn-gray" onclick="copyField('batch-preview-report-path')">Copy</button><span>Local markdown path, if report persistence is enabled.</span></div>
            <textarea id="batch-preview-report-path" class="text-output" readonly placeholder="No bulk publish preview report written yet."></textarea>
          </div>
        </div>
      </div>
      <div class="report-block">
        <h2>Bulk Reintake Preview</h2>
        <div class="section-note">Read-only intake evidence and publish-readiness report. No publish controls, no eBay mutation, no approval mutation, and no external provider call by default.</div>
        <div class="controls">
          <div>
            <label for="bulk-reintake-statuses">Status filters</label>
            <input id="bulk-reintake-statuses" value="needs_review, export_ready, listed" placeholder="needs_review, export_ready, listed">
          </div>
          <div>
            <label for="bulk-reintake-live-readonly">Live read-only checks</label>
            <select id="bulk-reintake-live-readonly">
              <option value="false" selected>Local-only diagnostics</option>
              <option value="true">Allow live read-only GETs</option>
            </select>
          </div>
          <div style="flex:0 0 auto"><button class="btn btn-purple" onclick="runBulkReintakePreview()">Run Bulk Reintake Preview</button></div>
        </div>
        <div id="bulk-reintake-status" class="status-line">No bulk reintake preview run yet.</div>
        <div id="bulk-reintake-summary" class="summary-grid"></div>
        <div id="bulk-reintake-lanes" class="data-list"></div>
        <div id="bulk-reintake-safety"></div>
        <div id="bulk-reintake-results" class="result-cards"><div class="empty">No bulk reintake preview loaded.</div></div>
        <div class="report-grid">
          <div class="report-box">
            <h3>Reintake Report Markdown</h3>
            <div class="copy-row"><button class="btn btn-gray" onclick="copyField('bulk-reintake-markdown')">Copy</button><span>Read-only operator report. Do not publish automatically.</span></div>
            <textarea id="bulk-reintake-markdown" class="text-output" readonly placeholder="Bulk reintake preview markdown will appear here."></textarea>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Recent Operation Events</h2>
      <div class="section-note">Historical runtime outcomes recorded by the operation diagnostics event ledger.</div>
      <div class="controls">
        <div><label for="events-filter-sku">SKU</label><input id="events-filter-sku" placeholder="BK-000008"></div>
        <div><label for="events-filter-status">Status</label><input id="events-filter-status" placeholder="failed"></div>
        <div><label for="events-filter-operation">Operation</label><input id="events-filter-operation" placeholder="ebay_publish"></div>
        <div><label for="events-filter-family">Error family</label><input id="events-filter-family" placeholder="invalid_category_condition"></div>
      </div>
      <div class="controls">
        <div style="flex:0 0 auto"><button class="btn btn-purple" onclick="loadRecentEvents()">Load Recent Events</button></div>
      </div>
      <div id="events-status" class="status-line">Recent events not loaded yet.</div>
      <div id="events-safety"></div>
      <div id="events-table" class="table-wrap"><div class="empty">No recent events loaded.</div></div>
    </section>

    <section class="panel">
      <h2>Per-SKU Diagnostic History</h2>
      <div class="section-note">Answer “what has happened to this SKU recently?” with a focused event timeline.</div>
      <div class="controls">
        <div><label for="sku-history-input">SKU</label><input id="sku-history-input" placeholder="BK-000008"></div>
        <div style="flex:0 0 auto"><button class="btn btn-purple" onclick="loadSkuHistory()">Load SKU Events</button></div>
      </div>
      <div id="sku-history-status" class="status-line">SKU history not loaded yet.</div>
      <div id="sku-history-safety"></div>
      <div id="sku-history-results" class="timeline"><div class="empty">No SKU event history loaded.</div></div>
    </section>

    <section class="panel" style="grid-column:1 / -1">
      <h2>Diagnostic Reports</h2>
      <div class="section-note">Local generated summaries from the diagnostics ledger. Reports are local-only and include redaction notices.</div>
      <div class="controls">
        <div><label for="report-type">Report type</label>
          <select id="report-type">
            <option value="critical_error_report">critical_error_report</option>
            <option value="weekly_report" selected>weekly_report</option>
            <option value="session_report">session_report</option>
            <option value="sku_report">sku_report</option>
            <option value="root_cause_analysis_package">root_cause_analysis_package</option>
          </select>
        </div>
        <div><label for="report-session-id">Session ID</label><input id="report-session-id" placeholder="session-1"></div>
        <div><label for="report-sku">SKU</label><input id="report-sku" placeholder="BK-000008"></div>
        <div><label for="report-days">Days</label><input id="report-days" type="number" min="1" max="90" value="7"></div>
      </div>
      <div class="controls">
        <div style="flex:0 0 auto"><button class="btn btn-gray" onclick="loadRecentReports()">Load Recent Reports</button></div>
        <div style="flex:0 0 auto"><button class="btn btn-purple" onclick="loadWeeklyReport()">Load Weekly Report</button></div>
        <div style="flex:0 0 auto"><button class="btn btn-green" onclick="generateReport()">Generate Report</button></div>
      </div>
      <div id="reports-status" class="status-line">No report loaded yet.</div>
      <div id="reports-safety"></div>
      <div id="recent-reports-list" class="result-cards"><div class="empty">No recent reports loaded.</div></div>
      <div id="report-detail" class="report-block"><div class="empty">No report detail loaded.</div></div>
    </section>
  </div>
</main>

<script>
let recentEventsCache = [];

function esc(value) {{
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

function parseSkus(raw) {{
  return Array.from(new Set(
    String(raw || '')
      .split(/[\\n,]+/)
      .map(value => value.trim().toUpperCase())
      .filter(Boolean)
  ));
}}

function parseStatuses(raw) {{
  return Array.from(new Set(
    String(raw || '')
      .split(/[\\n,]+/)
      .map(value => value.trim().toLowerCase())
      .filter(Boolean)
  ));
}}

function setStatus(id, message, tone) {{
  const el = document.getElementById(id);
  el.textContent = message;
  el.className = 'status-line' + (tone ? ' ' + tone : '');
}}

function renderSummary(summary) {{
  const target = document.getElementById('batch-summary');
  if (!summary) {{
    target.innerHTML = '';
    return;
  }}
  const keys = ['total', 'found', 'missing', 'ready_for_publish_preview', 'blocked', 'warnings'];
  target.innerHTML = keys.map(key => `
    <div class="summary-card">
      <span class="label">${{esc(key.replaceAll('_', ' '))}}</span>
      <span class="value">${{esc(summary[key] ?? 0)}}</span>
    </div>
  `).join('');
}}

function renderTagList(values, tone) {{
  if (!values || !values.length) return '<span class="compact-list empty">none</span>';
  return values.map(value => `<span class="tag ${{tone || ''}} mono">${{esc(value)}}</span>`).join('');
}}

function renderFamilies(grouped) {{
  const target = document.getElementById('batch-families');
  const entries = Object.entries(grouped || {{}});
  if (!entries.length) {{
    target.innerHTML = '<span class="tag ok">No grouped blocker families</span>';
    return;
  }}
  target.innerHTML = entries.map(([family, skus]) => `<span class="tag warn">${{esc(family)}}: ${{esc((skus || []).join(', '))}}</span>`).join('');
}}

function renderLanes(grouped) {{
  const target = document.getElementById('batch-lanes');
  const entries = Object.entries(grouped || {{}});
  if (!entries.length) {{
    target.innerHTML = '<span class="tag ok">No grouped workflow lanes</span>';
    return;
  }}
  target.innerHTML = entries.map(([lane, skus]) => `<span class="tag info">${{esc(lane)}}: ${{esc((skus || []).join(', '))}}</span>`).join('');
}}

function renderSafetyFlags(targetId, payload, extra = []) {{
  const flags = [];
  if (payload && payload.no_mutation_performed !== undefined) {{
    flags.push(payload.no_mutation_performed ? 'No live eBay mutation performed' : 'Mutation status not confirmed');
  }}
  if (payload && payload.no_ebay_mutation_performed !== undefined) {{
    flags.push(payload.no_ebay_mutation_performed ? 'No eBay mutation performed' : 'eBay mutation status not confirmed');
  }}
  if (payload && payload.no_external_send !== undefined) {{
    flags.push(payload.no_external_send ? 'No external report sending' : 'External send status not confirmed');
  }}
  extra.forEach(value => {{ if (value) flags.push(value); }});
  document.getElementById(targetId).innerHTML = flags.length
    ? `<div class="safety-box${{payload && payload.no_external_send ? ' report' : ''}}">${{flags.map(value => `<div class="tag ok">${{esc(value)}}</div>`).join(' ')}}</div>`
    : '';
}}

function formatReadinessBox(title, data) {{
  const status = data && data.status ? data.status : 'unknown';
  const tone = status === 'ready' ? 'ok' : (status === 'missing' || status === 'blocked' ? 'err' : 'warn');
  const details = [];
  Object.entries(data || {{}}).forEach(([key, value]) => {{
    if (key === 'status' || value === '' || value === null || value === undefined) return;
    if (Array.isArray(value)) details.push(`${{key}}: ${{value.join(', ')}}`);
    else if (typeof value === 'object') details.push(`${{key}}: ${{JSON.stringify(value)}}`);
    else details.push(`${{key}}: ${{value}}`);
  }});
  return `
    <div class="kv">
      <div class="k">${{esc(title)}}</div>
      <div class="v"><span class="tag ${{tone}}">${{esc(status)}}</span></div>
      ${{details.length ? `<div class="v" style="margin-top:4px;color:#b4b0a6">${{esc(details.slice(0, 3).join(' | '))}}</div>` : ''}}
    </div>
  `;
}}

function formatLocalItemState(state, fallbackPlannedAction) {{
  const itemState = state || {{}};
  const parts = [
    `status=${{itemState.status || ''}}`,
    `offer_id=${{itemState.offer_id || ''}}`,
    `listing_id=${{itemState.listing_id || ''}}`,
    `planned_action=${{itemState.planned_action || fallbackPlannedAction || ''}}`,
    `blocked_by_repair_queue=${{String(Boolean(itemState.blocked_by_repair_queue))}}`,
  ];
  return parts.map(value => `<span class="tag mono">${{esc(value)}}</span>`).join('');
}}

function renderBatchResults(results, response) {{
  const target = document.getElementById('batch-results');
  if (!results || !results.length) {{
    target.innerHTML = '<div class="empty">No per-SKU diagnostics loaded.</div>';
    return;
  }}
  target.innerHTML = results.map(result => {{
    const tone = result.ready_for_publish_preview ? 'ok' : (result.found ? 'warn' : 'err');
    return `
      <div class="result-card">
        <h3>${{esc(result.sku || 'Unknown SKU')}} <span class="tag ${{tone}}">${{result.ready_for_publish_preview ? 'ready_for_publish_preview' : (result.found ? 'blocked' : 'missing')}}</span></h3>
        <div class="result-grid">
          <div class="kv"><div class="k">Found</div><div class="v">${{esc(String(result.found))}}</div></div>
          <div class="kv"><div class="k">Current local status</div><div class="v">${{esc((result.local_item_state && result.local_item_state.status) || '')}}</div></div>
          <div class="kv"><div class="k">Workflow lane</div><div class="v">${{esc(result.workflow_lane || '')}}</div></div>
          <div class="kv"><div class="k">Workflow hint</div><div class="v">${{esc(result.workflow_hint || '')}}</div></div>
          <div class="kv"><div class="k">Primary blocker family</div><div class="v">${{esc(result.primary_blocker_family || result.likely_root_cause_family || '')}}</div></div>
          <div class="kv"><div class="k">Local item state</div><div class="compact-list">${{formatLocalItemState(result.local_item_state, result.planned_action)}}</div></div>
          <div class="kv"><div class="k">Planned action</div><div class="v">${{esc((result.local_item_state && result.local_item_state.planned_action) || result.planned_action || '')}}</div></div>
          <div class="kv"><div class="k">Category ID</div><div class="v">${{esc(result.local_category_id || '')}}</div></div>
          <div class="kv"><div class="k">Condition ID</div><div class="v">${{esc(result.local_condition_id || '')}}</div></div>
          <div class="kv"><div class="k">Expected inventory enum</div><div class="v">${{esc(result.expected_inventory_enum || '')}}</div></div>
          <div class="kv"><div class="k">Root cause family</div><div class="v">${{esc(result.likely_root_cause_family || '')}}</div></div>
          <div class="kv"><div class="k">Next safest action</div><div class="v">${{esc(result.recommended_next_action || '')}}</div></div>
          ${{formatReadinessBox('Image hosting readiness', result.image_hosting_readiness || {{}})}}
          ${{formatReadinessBox('Seller policy readiness', result.seller_policy_readiness || {{}})}}
          ${{formatReadinessBox('Merchant location readiness', result.merchant_location_readiness || {{}})}}
        </div>
        <div class="kv"><div class="k">Blockers</div><div class="compact-list">${{renderTagList(result.blocker_codes, 'err')}}</div></div>
        <div class="kv"><div class="k">Warning codes</div><div class="compact-list">${{renderTagList(result.warning_codes, 'warn')}}</div></div>
        <div class="kv"><div class="k">Success checks</div><div class="compact-list">${{renderTagList(result.success_checks, 'ok')}}</div></div>
        <div class="kv"><div class="k">Related files/services</div><div class="compact-list">${{renderTagList(result.related_files_services, 'info')}}</div></div>
        <details>
          <summary>Safe raw details</summary>
          <pre>${{esc(JSON.stringify(result.raw_details || {{}}, null, 2))}}</pre>
        </details>
        <div class="meta-row" style="margin-top:10px">
          <span class="tag ok">${{esc(response.no_mutation_performed ? 'no_mutation_performed=true' : 'no_mutation_performed=false')}}</span>
          <span class="tag ok">${{esc(response.no_ebay_mutation_performed ? 'no_ebay_mutation_performed=true' : 'no_ebay_mutation_performed=false')}}</span>
        </div>
      </div>
    `;
  }}).join('');
}}

async function runBatchDiagnostics() {{
  const skus = parseSkus(document.getElementById('batch-skus').value);
  const allowLiveReadonly = document.getElementById('allow-live-readonly').value === 'true';
  if (!skus.length) {{
    setStatus('batch-status', 'Enter at least one SKU before running diagnostics.', 'err');
    return;
  }}
  setStatus('batch-status', 'Running read-only publish diagnostics...', '');
  document.getElementById('batch-results').innerHTML = '<div class="empty">Loading batch diagnostics...</div>';
  try {{
    const resp = await fetch('/api/listings/publish-diagnostics/batch', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ skus, allow_live_readonly: allowLiveReadonly }}),
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Batch diagnostics failed.');
    renderSummary(body.summary || {{}});
    renderFamilies(body.grouped_blocker_families || {{}});
    renderLanes(body.grouped_workflow_lanes || {{}});
    renderSafetyFlags('batch-safety', body, [
      body.persistable ? 'Persistable local diagnostics response' : '',
      body.report_type ? 'Report type: ' + body.report_type : '',
      body.diagnostic_version ? 'Diagnostic version: ' + body.diagnostic_version : '',
    ]);
    renderBatchResults(body.per_sku_results || [], body);
    document.getElementById('batch-markdown').value = body.copyable_report_markdown || '';
    document.getElementById('batch-prompt').value = body.copyable_codex_prompt || '';
    setStatus('batch-status', `Loaded diagnostics for ${{body.summary?.total ?? skus.length}} SKU(s).`, 'ok');
  }} catch (error) {{
    renderSummary(null);
    document.getElementById('batch-families').innerHTML = '';
    document.getElementById('batch-lanes').innerHTML = '';
    document.getElementById('batch-safety').innerHTML = '';
    document.getElementById('batch-results').innerHTML = `<div class="empty">Diagnostics failed: ${{esc(error.message)}}</div>`;
    setStatus('batch-status', error.message, 'err');
  }}
}}

function renderPreviewSummary(summary) {{
  const target = document.getElementById('batch-preview-summary');
  if (!summary) {{
    target.innerHTML = '';
    return;
  }}
  const keys = [
    'total',
    'would_publish_count',
    'skip_count',
    'repair_count',
    'review_count',
    'already_listed_count',
    'auth_blocked_count',
    'missing_photo_count',
    'stale_offer_count',
    'invalid_category_condition_count',
  ];
  target.innerHTML = keys.map(key => `
    <div class="summary-card">
      <span class="label">${{esc(key.replaceAll('_', ' '))}}</span>
      <span class="value">${{esc(summary[key] ?? 0)}}</span>
    </div>
  `).join('');
}}

function renderPreviewGroups(grouped) {{
  const target = document.getElementById('batch-preview-groups');
  const entries = Object.entries(grouped || {{}});
  if (!entries.length) {{
    target.innerHTML = '<span class="tag ok">No grouped preview decisions</span>';
    return;
  }}
  target.innerHTML = entries.map(([label, skus]) => `<span class="tag warn">${{esc(label)}}: ${{esc((skus || []).join(', '))}}</span>`).join('');
}}

function renderPreviewResults(results) {{
  const target = document.getElementById('batch-preview-results');
  if (!results || !results.length) {{
    target.innerHTML = '<div class="empty">No bulk publish preview loaded.</div>';
    return;
  }}
  target.innerHTML = results.map(result => {{
    const decision = String(result.decision || 'SKIP');
    const tone = decision === 'WOULD_PUBLISH' ? 'ok' : (decision === 'AUTH_BLOCKED' ? 'err' : 'warn');
    return `
      <div class="result-card">
        <h3>${{esc(result.sku || 'Unknown SKU')}} <span class="tag ${{tone}}">${{esc(decision)}}</span></h3>
        <div class="result-grid">
          <div class="kv"><div class="k">Reason code</div><div class="v">${{esc(result.reason_code || '')}}</div></div>
          <div class="kv"><div class="k">Classified error</div><div class="v">${{esc(result.classified_error_code || '')}}</div></div>
          <div class="kv"><div class="k">Planned action</div><div class="v">${{esc(result.planned_action || '')}}</div></div>
          <div class="kv"><div class="k">Photo hosting state</div><div class="v">${{esc(result.photo_hosting_state || '')}}</div></div>
          <div class="kv"><div class="k">Local publish ready</div><div class="v">${{esc(String(Boolean(result.local_publish_ready)))}}</div></div>
          <div class="kv"><div class="k">Effective publish ready</div><div class="v">${{esc(String(Boolean(result.effective_publish_ready)))}}</div></div>
          <div class="kv"><div class="k">Category / condition</div><div class="v">${{esc((result.category_id || '') + ' / ' + (result.condition_id || ''))}}</div></div>
          <div class="kv"><div class="k">Inventory enum</div><div class="v">${{esc(result.inventory_condition_enum || '')}}</div></div>
          <div class="kv"><div class="k">Offer / listing</div><div class="v">${{esc((result.offer_id || '') + ' / ' + (result.listing_id || ''))}}</div></div>
          <div class="kv"><div class="k">Repair plan</div><div class="v">${{esc(result.repair_plan_id || '')}}</div></div>
          <div class="kv"><div class="k">Message</div><div class="v">${{esc(result.message || '')}}</div></div>
          <div class="kv"><div class="k">Next action</div><div class="v">${{esc(result.next_action || '')}}</div></div>
        </div>
        <div class="meta-row" style="margin-top:10px">
          <span class="tag mono">${{esc('blocked_by_repair_queue=' + String(Boolean(result.blocked_by_repair_queue)))}}</span>
          <span class="tag mono">${{esc('retry_allowed=' + String(Boolean(result.retry_allowed)))}}</span>
          <span class="tag mono">${{esc('requires_review=' + String(Boolean(result.requires_review)))}}</span>
        </div>
      </div>
    `;
  }}).join('');
}}

async function runBulkPublishPreview() {{
  const skus = parseSkus(document.getElementById('batch-skus').value);
  const statuses = parseStatuses(document.getElementById('batch-preview-statuses').value);
  if (!skus.length && !statuses.length) {{
    setStatus('batch-preview-status', 'Enter explicit SKUs or at least one status filter before preview.', 'err');
    return;
  }}
  setStatus('batch-preview-status', 'Running bulk publish preview...', '');
  document.getElementById('batch-preview-results').innerHTML = '<div class="empty">Loading bulk publish preview...</div>';
  try {{
    const resp = await fetch('/api/ebay/publish/batch-preview', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ skus, statuses, persist_report: true }}),
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Bulk publish preview failed.');
    renderPreviewSummary(body.summary || {{}});
    renderPreviewGroups(body.grouped_decisions || {{}});
    renderSafetyFlags('batch-preview-safety', body, [
      body.report_type ? 'Report type: ' + body.report_type : '',
      body.persisted_report_path ? 'Report saved locally' : 'Report persistence disabled',
    ]);
    renderPreviewResults(body.decisions || []);
    document.getElementById('batch-preview-markdown').value = body.report_markdown || '';
    document.getElementById('batch-preview-report-path').value = body.persisted_report_path || '';
    setStatus('batch-preview-status', `Loaded bulk publish preview for ${{body.summary?.total ?? 0}} SKU(s).`, 'ok');
  }} catch (error) {{
    renderPreviewSummary(null);
    document.getElementById('batch-preview-groups').innerHTML = '';
    document.getElementById('batch-preview-safety').innerHTML = '';
    document.getElementById('batch-preview-results').innerHTML = `<div class="empty">Bulk publish preview failed: ${{esc(error.message)}}</div>`;
    document.getElementById('batch-preview-markdown').value = '';
    document.getElementById('batch-preview-report-path').value = '';
    setStatus('batch-preview-status', error.message, 'err');
  }}
}}

function renderReintakeSummary(summary) {{
  const target = document.getElementById('bulk-reintake-summary');
  if (!summary) {{
    target.innerHTML = '';
    return;
  }}
  const keys = [
    'total_skus',
    'ready_for_publish_preview_count',
    'blocked_count',
    'already_listed_or_sync_review_count',
    'live_state_remediation_required_count',
    'image_hosting_candidate_count',
    'unknown_manual_review_count',
  ];
  target.innerHTML = keys.map(key => `
    <div class="summary-card">
      <span class="label">${{esc(key.replaceAll('_', ' '))}}</span>
      <span class="value">${{esc(summary[key] ?? 0)}}</span>
    </div>
  `).join('');
}}

function renderReintakeLanes(summary) {{
  const target = document.getElementById('bulk-reintake-lanes');
  const lanes = Object.entries((summary || {{}}).by_workflow_lane || {{}});
  if (!lanes.length) {{
    target.innerHTML = '<span class="tag ok">No workflow lanes loaded</span>';
    return;
  }}
  target.innerHTML = lanes.map(([lane, count]) => `<span class="tag warn">${{esc(lane)}}: ${{esc(count)}}</span>`).join('');
}}

function renderReintakeResults(results) {{
  const target = document.getElementById('bulk-reintake-results');
  if (!results || !results.length) {{
    target.innerHTML = '<div class="empty">No bulk reintake preview loaded.</div>';
    return;
  }}
  target.innerHTML = results.slice(0, 24).map(result => `
    <div class="result-card">
      <h3>${{esc(result.sku || 'Unknown SKU')}} <span class="tag warn">${{esc(result.workflow_lane || '')}}</span></h3>
      <div class="result-grid">
        <div class="kv"><div class="k">Current local status</div><div class="v">${{esc(result.current_local_status || '')}}</div></div>
        <div class="kv"><div class="k">Intake quality</div><div class="v">${{esc(result.intake_quality_status || '')}}</div></div>
        <div class="kv"><div class="k">Missing photos</div><div class="v">${{esc((result.missing_photo_types || []).join(', ') || 'none')}}</div></div>
        <div class="kv"><div class="k">Primary blocker family</div><div class="v">${{esc(result.primary_blocker_family || '')}}</div></div>
        <div class="kv"><div class="k">Blockers</div><div class="v">${{esc((result.blockers || []).join(', ') || 'none')}}</div></div>
        <div class="kv"><div class="k">Next safest action</div><div class="v">${{esc(result.next_safest_action || '')}}</div></div>
      </div>
    </div>
  `).join('');
}}

async function runBulkReintakePreview() {{
  const skus = parseSkus(document.getElementById('batch-skus').value);
  const statuses = parseStatuses(document.getElementById('bulk-reintake-statuses').value);
  const allowLiveReadonly = document.getElementById('bulk-reintake-live-readonly').value === 'true';
  if (!skus.length && !statuses.length) {{
    setStatus('bulk-reintake-status', 'Enter explicit SKUs or at least one status filter before preview.', 'err');
    return;
  }}
  setStatus('bulk-reintake-status', 'Running read-only bulk reintake preview...', '');
  document.getElementById('bulk-reintake-results').innerHTML = '<div class="empty">Loading bulk reintake preview...</div>';
  try {{
    const resp = await fetch('/api/items/bulk-reintake-preview', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ skus, statuses, allow_live_readonly: allowLiveReadonly, persist_report: false }}),
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Bulk reintake preview failed.');
    renderReintakeSummary(body.summary || {{}});
    renderReintakeLanes(body.summary || {{}});
    renderSafetyFlags('bulk-reintake-safety', body, [
      body.report_type ? 'Report type: ' + body.report_type : '',
      body.generated_artifact_warning || '',
      body.no_external_provider_called ? 'No external provider call' : 'External provider was called',
    ]);
    renderReintakeResults(body.per_sku_results || []);
    document.getElementById('bulk-reintake-markdown').value = body.report_markdown || '';
    setStatus('bulk-reintake-status', `Loaded bulk reintake preview for ${{body.summary?.total_skus ?? 0}} SKU(s).`, 'ok');
  }} catch (error) {{
    renderReintakeSummary(null);
    document.getElementById('bulk-reintake-lanes').innerHTML = '';
    document.getElementById('bulk-reintake-safety').innerHTML = '';
    document.getElementById('bulk-reintake-results').innerHTML = `<div class="empty">Bulk reintake preview failed: ${{esc(error.message)}}</div>`;
    document.getElementById('bulk-reintake-markdown').value = '';
    setStatus('bulk-reintake-status', error.message, 'err');
  }}
}}

function eventMatchesFilters(event) {{
  const sku = document.getElementById('events-filter-sku').value.trim().toUpperCase();
  const status = document.getElementById('events-filter-status').value.trim().toLowerCase();
  const operation = document.getElementById('events-filter-operation').value.trim().toLowerCase();
  const family = document.getElementById('events-filter-family').value.trim().toLowerCase();
  if (sku && String(event.sku || '').toUpperCase() !== sku) return false;
  if (status && String(event.status || '').toLowerCase() !== status) return false;
  if (operation && !String(event.operation_name || '').toLowerCase().includes(operation)) return false;
  if (family && !String(event.error_family || '').toLowerCase().includes(family)) return false;
  return true;
}}

function renderEvents(events, targetId) {{
  const target = document.getElementById(targetId);
  if (!events.length) {{
    target.innerHTML = '<div class="empty">No events match the current filters.</div>';
    return;
  }}
  target.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Created</th><th>Operation</th><th>Route</th><th>SKU</th><th>Status</th><th>Mutation</th><th>eBay Mutation</th><th>Service</th><th>Stage</th><th>Error Family</th><th>Error Code</th><th>Message</th><th>Recommended Next Action</th>
        </tr>
      </thead>
      <tbody>
        ${{events.map(event => `
          <tr>
            <td>${{esc(event.created_at || '')}}</td>
            <td>${{esc(event.operation_name || '')}}</td>
            <td>${{esc(event.route || '')}}</td>
            <td>${{esc(event.sku || '')}}</td>
            <td><span class="tag ${{event.status === 'success' ? 'ok' : (event.status === 'failed' ? 'err' : 'warn')}}">${{esc(event.status || '')}}</span></td>
            <td>${{esc(String(event.mutation_attempted))}} / ${{esc(String(event.mutation_succeeded))}}</td>
            <td>${{esc(String(event.ebay_mutation_attempted))}} / ${{esc(String(event.ebay_mutation_succeeded))}}</td>
            <td>${{esc(event.external_service || '')}}</td>
            <td>${{esc(event.stage || '')}}</td>
            <td>${{esc(event.error_family || '')}}</td>
            <td>${{esc(event.error_code || '')}}</td>
            <td>${{esc(event.safe_message || '')}}</td>
            <td>${{esc(event.recommended_next_action || '')}}</td>
          </tr>
        `).join('')}}
      </tbody>
    </table>
  `;
}}

async function loadRecentEvents() {{
  setStatus('events-status', 'Loading recent operation events...', '');
  document.getElementById('events-table').innerHTML = '<div class="empty">Loading recent events...</div>';
  try {{
    const resp = await fetch('/api/diagnostics/events/recent');
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Failed to load recent events.');
    recentEventsCache = body.events || [];
    const filtered = recentEventsCache.filter(eventMatchesFilters);
    renderSafetyFlags('events-safety', body);
    renderEvents(filtered, 'events-table');
    setStatus('events-status', `Loaded ${{filtered.length}} recent event(s).`, 'ok');
  }} catch (error) {{
    document.getElementById('events-safety').innerHTML = '';
    document.getElementById('events-table').innerHTML = `<div class="empty">Recent events failed: ${{esc(error.message)}}</div>`;
    setStatus('events-status', error.message, 'err');
  }}
}}

async function loadSkuHistory() {{
  const sku = document.getElementById('sku-history-input').value.trim().toUpperCase();
  if (!sku) {{
    setStatus('sku-history-status', 'Enter a SKU to load its event timeline.', 'err');
    return;
  }}
  setStatus('sku-history-status', `Loading event history for ${{sku}}...`, '');
  document.getElementById('sku-history-results').innerHTML = '<div class="empty">Loading SKU event history...</div>';
  try {{
    const resp = await fetch('/api/diagnostics/events/sku/' + encodeURIComponent(sku));
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Failed to load SKU event history.');
    renderSafetyFlags('sku-history-safety', body);
    const events = body.events || [];
    if (!events.length) {{
      document.getElementById('sku-history-results').innerHTML = '<div class="empty">No events recorded for this SKU yet.</div>';
    }} else {{
      document.getElementById('sku-history-results').innerHTML = events.map(event => `
        <div class="timeline-item">
          <div class="top">
            <div><strong>${{esc(event.operation_name || '')}}</strong> <span class="tag ${{event.status === 'success' ? 'ok' : (event.status === 'failed' ? 'err' : 'warn')}}">${{esc(event.status || '')}}</span></div>
            <div class="when">${{esc(event.created_at || '')}}</div>
          </div>
          <div class="meta-row">
            <span class="tag mono">${{esc(event.route || '')}}</span>
            <span class="tag">${{esc(event.external_service || '')}}</span>
            <span class="tag">${{esc(event.stage || '')}}</span>
            <span class="tag">${{esc(event.error_family || '')}}</span>
            <span class="tag">${{esc(event.error_code || '')}}</span>
          </div>
          <div class="msg">${{esc(event.safe_message || '')}}</div>
          <div class="section-note" style="margin-top:8px">${{esc(event.recommended_next_action || '')}}</div>
        </div>
      `).join('');
    }}
    setStatus('sku-history-status', `Loaded ${{events.length}} event(s) for ${{sku}}.`, 'ok');
  }} catch (error) {{
    document.getElementById('sku-history-safety').innerHTML = '';
    document.getElementById('sku-history-results').innerHTML = `<div class="empty">SKU history failed: ${{esc(error.message)}}</div>`;
    setStatus('sku-history-status', error.message, 'err');
  }}
}}

function renderRecentReports(reports) {{
  const target = document.getElementById('recent-reports-list');
  if (!reports || !reports.length) {{
    target.innerHTML = '<div class="empty">No recent local reports found.</div>';
    return;
  }}
  target.innerHTML = reports.map(report => `
    <div class="result-card">
      <h3>${{esc(report.report_type || report.report_id || 'report')}}</h3>
      <div class="result-grid">
        <div class="kv"><div class="k">Report ID</div><div class="v">${{esc(report.report_id || '')}}</div></div>
        <div class="kv"><div class="k">Generated</div><div class="v">${{esc(report.generated_at || '')}}</div></div>
        <div class="kv"><div class="k">Title</div><div class="v">${{esc(report.title || '')}}</div></div>
        <div class="kv"><div class="k">JSON Path</div><div class="v">${{esc(report.json_path || '')}}</div></div>
      </div>
      <div class="kv"><div class="k">Severity Breakdown</div><div class="compact-list">${{renderTagList(Object.entries(report.severity_breakdown || {{}}).map(([k,v]) => `${{k}}=${{v}}`), 'info')}}</div></div>
    </div>
  `).join('');
}}

function renderReport(report, responseMeta) {{
  const target = document.getElementById('report-detail');
  if (!report) {{
    target.innerHTML = '<div class="empty">No report detail loaded.</div>';
    return;
  }}
  renderSafetyFlags('reports-safety', responseMeta || {{}}, [
    report.no_external_send ? 'No external report sending' : '',
    responseMeta && responseMeta.local_persistence_only ? 'Local-only report generation/persistence' : '',
    report.redaction_notice || '',
  ]);
  const topFamilies = (report.top_error_families || []).map(entry => `${{entry.error_family}}=${{entry.count}}`);
  const repeated = report.repeated_failures || [];
  const git = report.git_context || {{}};
  target.innerHTML = `
    <div class="meta-row">
      <span class="tag info">${{esc(report.report_type || '')}}</span>
      <span class="tag info">${{esc(report.diagnostic_version || '')}}</span>
      <span class="tag ok">${{esc(report.no_external_send ? 'local_only=true' : 'local_only=false')}}</span>
    </div>
    <div class="summary-grid">
      ${{
        Object.entries(report.summary_counts || {{}}).map(([key, value]) => `
          <div class="summary-card">
            <span class="label">${{esc(key.replaceAll('_', ' '))}}</span>
            <span class="value">${{esc(value)}}</span>
          </div>
        `).join('')
      }}
    </div>
    <div class="report-grid">
      <div class="report-box">
        <h3>Severity breakdown</h3>
        <div class="compact-list">${{renderTagList(Object.entries(report.severity_breakdown || {{}}).map(([k,v]) => `${{k}}=${{v}}`), 'info')}}</div>
      </div>
      <div class="report-box">
        <h3>Top error families</h3>
        <div class="compact-list">${{renderTagList(topFamilies, 'warn')}}</div>
      </div>
      <div class="report-box">
        <h3>Affected SKUs</h3>
        <div class="compact-list">${{renderTagList(report.affected_skus || [], 'mono')}}</div>
      </div>
      <div class="report-box">
        <h3>Git / commit context</h3>
        <div class="kv"><div class="k">Commit</div><div class="v">${{esc(git.current_commit_hash || '')}}</div></div>
        <div class="kv"><div class="k">Branch</div><div class="v">${{esc(git.branch || '')}}</div></div>
        <div class="kv"><div class="k">Dirty tree</div><div class="v">${{esc(String(git.dirty_working_tree))}}</div></div>
        <div class="kv"><div class="k">Latest subject</div><div class="v">${{esc(git.latest_commit_subject || '')}}</div></div>
      </div>
    </div>
    <div class="report-grid">
      <div class="report-box">
        <h3>Repeated failures</h3>
        ${{repeated.length ? repeated.map(entry => `<div class="kv" style="margin-bottom:8px"><div class="k">${{esc(entry.group_key)}}</div><div class="v">count=${{esc(entry.count)}}, sku_count=${{esc(entry.sku_count)}}, severity=${{esc(entry.severity)}}</div></div>`).join('') : '<div class="empty">No repeated failures detected.</div>'}}
      </div>
      <div class="report-box">
        <h3>Suspected root causes</h3>
        <div class="compact-list">${{renderTagList(report.suspected_root_causes || [], 'warn')}}</div>
      </div>
      <div class="report-box">
        <h3>Recommended next actions</h3>
        <div class="compact-list">${{renderTagList(report.recommended_next_actions || [], 'ok')}}</div>
      </div>
      <div class="report-box">
        <h3>Related files/services</h3>
        <div class="compact-list">${{renderTagList(report.related_files_services || [], 'info')}}</div>
      </div>
    </div>
    <details>
      <summary>Sanitized raw examples</summary>
      <pre>${{esc(JSON.stringify(report.sanitized_raw_examples || [], null, 2))}}</pre>
    </details>
    <details>
      <summary>Markdown</summary>
      <div class="copy-row"><button class="btn btn-gray" onclick="copyField('report-markdown')">Copy</button><span>Copyable local report markdown.</span></div>
      <textarea id="report-markdown" class="text-output" readonly>${{esc(report.report_markdown || '')}}</textarea>
    </details>
    <details>
      <summary>Copyable Codex prompt</summary>
      <div class="copy-row"><button class="btn btn-gray" onclick="copyField('report-prompt')">Copy</button><span>Paste into Codex for deeper root-cause analysis.</span></div>
      <textarea id="report-prompt" class="text-output" readonly>${{esc(report.copyable_codex_prompt || '')}}</textarea>
    </details>
  `;
}}

async function loadRecentReports() {{
  setStatus('reports-status', 'Loading recent local diagnostic reports...', '');
  try {{
    const resp = await fetch('/api/diagnostics/reports/recent');
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Failed to load recent reports.');
    renderSafetyFlags('reports-safety', body, ['Recent report index only.']);
    renderRecentReports(body.reports || []);
    setStatus('reports-status', `Loaded ${{(body.reports || []).length}} recent report(s).`, 'ok');
  }} catch (error) {{
    document.getElementById('recent-reports-list').innerHTML = `<div class="empty">Recent reports failed: ${{esc(error.message)}}</div>`;
    setStatus('reports-status', error.message, 'err');
  }}
}}

async function loadWeeklyReport() {{
  setStatus('reports-status', 'Loading weekly report...', '');
  try {{
    const days = Number(document.getElementById('report-days').value || 7);
    const resp = await fetch('/api/diagnostics/reports/weekly?days=' + encodeURIComponent(days));
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Failed to load weekly report.');
    renderReport(body.report, body);
    setStatus('reports-status', `Loaded weekly report for the last ${{days}} day(s).`, 'ok');
  }} catch (error) {{
    setStatus('reports-status', error.message, 'err');
    document.getElementById('report-detail').innerHTML = `<div class="empty">Weekly report failed: ${{esc(error.message)}}</div>`;
  }}
}}

async function generateReport() {{
  setStatus('reports-status', 'Generating local diagnostic report...', '');
  try {{
    const payload = {{
      report_type: document.getElementById('report-type').value,
      session_id: document.getElementById('report-session-id').value.trim() || null,
      sku: document.getElementById('report-sku').value.trim().toUpperCase() || null,
      days: Number(document.getElementById('report-days').value || 7),
      persist: true,
    }};
    const resp = await fetch('/api/diagnostics/reports/generate', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload),
    }});
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'Failed to generate diagnostic report.');
    renderReport(body.report, body);
    setStatus('reports-status', `Generated local ${{payload.report_type}}.`, 'ok');
  }} catch (error) {{
    setStatus('reports-status', error.message, 'err');
    document.getElementById('report-detail').innerHTML = `<div class="empty">Report generation failed: ${{esc(error.message)}}</div>`;
  }}
}}

async function copyField(id) {{
  const el = document.getElementById(id);
  await copyTextValue(el);
}}

async function copyTextValue(el) {{
  if (!el) return;
  try {{
    await navigator.clipboard.writeText(el.value || el.textContent || '');
    const previous = document.title;
    document.title = 'Copied';
    setTimeout(() => {{ document.title = previous; }}, 800);
  }} catch (_error) {{
    el.focus();
    el.select();
  }}
}}
</script>
</body>
</html>"""
