from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
    parse_sku_list,
)

router = APIRouter()


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    from collections import Counter
    from sqlmodel import select
    from packages.data.src.models.item_record import ItemRecord
    all_items = session.exec(select(ItemRecord)).all()
    counts = Counter(i.status for i in all_items)
    counts["_total"] = sum(v for k, v in counts.items() if not k.startswith("_"))
    counts["_high_confidence_pending"] = sum(
        1 for i in all_items
        if (i.confidence_score or 0) >= 0.85
        and i.status in ("analyzed", "approved", "needs_review")
    )
    counts["_ready_to_publish"] = sum(
        1 for i in all_items
        if i.status in ("approved", "export_ready")
    )
    from packages.core.src.config import get_rules
    rules = get_rules()
    stale_days = int(rules.get("pricing", {}).get("stale_listing_days", 60))
    counts["_stale_count"] = sum(
        1 for i in all_items
        if i.status == "listed" and (i.days_listed or 0) >= stale_days
    )
    return counts


@router.get("/stale")
def get_stale(session: Session = Depends(get_session)):
    """Return all listed items that have been active longer than stale_listing_days."""
    from packages.sync.src.stale_checker import StaleChecker
    checker = StaleChecker()
    records = checker.get_stale_items(session)
    return [
        {
            "sku": r.sku,
            "title": r.title_final or r.title_raw,
            "list_price": r.list_price,
            "days_listed": r.days_listed,
            "suggested_price": checker.suggest_price_drop(r),
        }
        for r in records
    ]


@router.post("/apply-stale-drops")
def apply_stale_drops(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    """Apply configured price drop to all stale listings."""
    from packages.sync.src.stale_checker import StaleChecker

    filtered_skus = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            filtered_skus = assert_route_skus_allowed(
                filtered_skus,
                "items.apply_stale_drops",
                require_non_empty=True,
            )
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not filtered_skus:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    checker = StaleChecker()
    if filtered_skus:
        count = 0
        stale = checker.get_stale_items(session)
        allowed = set(filtered_skus)
        for item in stale:
            if (item.sku or "").upper() not in allowed:
                continue
            new_price = checker.suggest_price_drop(item)
            if new_price is not None and new_price != item.list_price:
                item.list_price = new_price
                item.updated_at = datetime.utcnow()
                session.add(item)
                count += 1
        if count:
            session.commit()
    else:
        count = checker.apply_price_drops(session)
    return {"updated": count, "drop_percent": checker.stale_drop}


@router.get("/enrich/estimate")
def enrich_estimate(
    skus: str = "",
    batch_size: int = 10,
    session: Session = Depends(get_session),
):
    """
    Estimate Claude API cost for enriching a set of items.
    ?skus=SKU1,SKU2  — target specific items
    ?batch_size=N    — target next N unenriched items (default 10)
    """
    from sqlmodel import select
    from packages.data.src.models.item_record import ItemRecord

    TOKENS_PER_IMAGE = 1200
    INPUT_PRICE_PER_TOKEN = 3.00 / 1_000_000  # claude-sonnet-4-5 input pricing

    if skus:
        sku_list = [s.strip() for s in skus.split(",") if s.strip()]
        records = [
            session.exec(select(ItemRecord).where(ItemRecord.sku == s)).first()
            for s in sku_list
        ]
        records = [r for r in records if r]
    else:
        records = list(
            session.exec(
                select(ItemRecord).where(ItemRecord.enrichment_done == False)
            ).all()[:batch_size]
        )

    item_summaries = []
    total_images = 0
    for rec in records:
        paths = [p for p in (rec.image_paths or "").split("|") if p.strip()]
        n = len(paths)
        total_images += n
        item_summaries.append({"sku": rec.sku, "image_count": n})

    estimated_input_tokens = total_images * TOKENS_PER_IMAGE
    estimated_cost = round(estimated_input_tokens * INPUT_PRICE_PER_TOKEN, 4)

    return {
        "item_count": len(records),
        "image_count": total_images,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_cost_usd": estimated_cost,
        "items": item_summaries,
    }


@router.get("")
def list_items(limit: int = 50, status: str | None = None, session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    if status:
        items = repo.list_by_status(status)
    else:
        items = repo.get_all()
    return [i.model_dump() for i in items[:limit]]


@router.get("/{sku}")
def get_item(sku: str, session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    return item.model_dump()


@router.patch("/{sku}")
def update_item(sku: str, updates: dict, session: Session = Depends(get_session)):
    """Manual field override — sets manual_override=True to protect from AI reprocessing."""
    try:
        assert_route_sku_allowed(sku, "items.update_item")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    for k, v in updates.items():
        if hasattr(item, k):
            setattr(item, k, v)
    item.manual_override = True
    saved = repo.upsert(item)
    return saved.model_dump()


@router.patch("/{sku}/cost")
def update_cost(sku: str, updates: dict, session: Session = Depends(get_session)):
    """Set cost for an item. Sets cost_manual=True but does NOT set manual_override."""
    try:
        assert_route_sku_allowed(sku, "items.update_cost")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if "cost" not in updates:
        raise HTTPException(status_code=422, detail="Request body must contain 'cost'")
    item.cost = updates["cost"]
    item.cost_manual = True
    saved = repo.upsert(item)
    return {"sku": sku, "cost": saved.cost, "cost_manual": saved.cost_manual}


@router.get("/{sku}/image")
def serve_image(sku: str, path: str, session: Session = Depends(get_session)):
    """Serve a local image file for the review UI."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(p), media_type="image/jpeg")


@router.post("/{sku}/analyze")
def analyze_single(sku: str, session: Session = Depends(get_session)):
    """Run AI analysis on a single item via the API."""
    import json
    from packages.core.src.constants import ItemStatus, ItemMode
    from packages.data.src.models.review_record import ReviewRecord
    from packages.classification.src.category_mapper import CategoryMapper
    from packages.intake.src.image_normalizer import ImageNormalizer
    from packages.pricing.src.estimator import PriceEstimator
    from packages.triage.src.router import TriageRouter
    from packages.vision.src.ollama_provider import OllamaProvider
    from packages.vision.src.prompt_builder import build_extraction_prompt
    from packages.vision.src.response_parser import ResponseParser

    try:
        assert_route_sku_allowed(sku, "items.analyze")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    provider = OllamaProvider()
    if not provider.is_available():
        raise HTTPException(status_code=503, detail="Ollama not running")

    normalizer = ImageNormalizer()
    folder = Path(item.photo_folder)
    norm_result = normalizer.normalize_folder(folder)
    image_paths = norm_result.value if norm_result.ok else [Path(p) for p in item.image_paths]

    prompt = build_extraction_prompt(item.category_key or "clothing")
    vision_result = provider.analyze(image_paths=image_paths, prompt=prompt)
    if not vision_result.ok:
        raise HTTPException(status_code=500, detail=vision_result.error)

    parser = ResponseParser()
    parse_result = parser.parse(vision_result.value, item.category_key or "clothing")
    if not parse_result.ok:
        raise HTTPException(status_code=500, detail=parse_result.error)

    extracted = parse_result.value
    for k, v in extracted.items():
        if hasattr(item, k) and not item.manual_override:
            setattr(item, k, v)

    estimator = PriceEstimator()
    item = estimator.apply(item)

    router_t = TriageRouter()
    triage = router_t.route(item)
    item.item_mode = triage.item_mode
    item.needs_review = triage.needs_review
    item.review_reasons = triage.review_reasons or []

    if triage.needs_review or triage.item_mode == ItemMode.REVIEW:
        item.status = ItemStatus.NEEDS_REVIEW
    elif triage.item_mode == ItemMode.REJECT:
        item.status = ItemStatus.REJECTED
    else:
        item.status = ItemStatus.APPROVED

    repo.upsert(item)

    if item.needs_review and item.review_reasons:
        review = ReviewRecord(
            sku=sku,
            trigger_reason=json.dumps(item.review_reasons),
            confidence_score=item.confidence_score,
            high_value_flag=(item.estimated_price or 0) >= 75.0,
        )
        session.add(review)
        session.commit()

    return {"sku": sku, "status": item.status, "mode": item.item_mode,
            "confidence": item.confidence_score}


@router.post("/{sku}/enrich")
def enrich_item(sku: str, session: Session = Depends(get_session)):
    """Run Claude enrichment pipeline on a single item."""
    try:
        assert_route_sku_allowed(sku, "items.enrich")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    from packages.enrichment.src.enricher import ItemEnricher
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    enricher = ItemEnricher()
    if not enricher.is_available():
        raise HTTPException(
            status_code=503,
            detail="Enrichment not available — check ANTHROPIC_API_KEY and ENRICHMENT_ENABLED",
        )
    result = enricher.enrich(item)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)
    enricher.apply_to_item(item, result.value)
    repo.upsert(item)
    return {
        "sku": sku,
        "status": item.status,
        "enrichment_done": item.enrichment_done,
        "estimated_cost_usd": result.details.get("estimated_cost"),
        "title_final": item.title_final,
        "list_price": item.list_price,
        "estimated_price": item.estimated_price,
    }


@router.post("/{sku}/category-intelligence")
def run_category_intelligence(sku: str, session: Session = Depends(get_session)):
    """
    Re-run category intelligence for a single item.
    Triggered by title or category changes in review queue.
    Never triggered by item specifics changes.
    Returns updated template and validation result.
    """
    from datetime import datetime
    from packages.ebay.src.category_intelligence import CategoryIntelligence
    from packages.ebay.src.category_spreadsheet import CategorySpreadsheet

    try:
        assert_route_sku_allowed(sku, "items.category_intelligence")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    cat_intel = CategoryIntelligence()
    cat_sheet = CategorySpreadsheet()
    cat_id, cat_name = cat_intel.get_category_id(item)
    result = cat_intel.get_template(cat_id)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)

    template = result.value
    item.ebay_category_id = cat_id
    item.ebay_category_name = cat_name or template.category_name
    item.category_template_fetched = True
    item.category_template_fetched_at = datetime.utcnow().isoformat()
    cat_sheet.save_template(template)

    validation = cat_intel.validate_item_specifics(item, template)
    item.missing_required_fields = validation.missing_required
    item.missing_recommended_fields = validation.missing_recommended
    item.publish_ready = validation.is_publish_ready

    review_reasons = list(item.review_reasons or [])
    if validation.missing_required and "missing_required_specifics" not in review_reasons:
        review_reasons.append("missing_required_specifics")
    elif not validation.missing_required and "missing_required_specifics" in review_reasons:
        review_reasons.remove("missing_required_specifics")
    item.review_reasons = review_reasons
    item.updated_at = datetime.utcnow()
    repo.upsert(item)

    return {
        "sku": sku,
        "category_id": cat_id,
        "category_name": template.category_name,
        "required_fields": template.required_fields,
        "recommended_fields": template.recommended_fields,
        "field_constraints": template.field_constraints,
        "missing_required": validation.missing_required,
        "missing_recommended": validation.missing_recommended,
        "invalid_fields": validation.invalid_fields,
        "publish_ready": validation.is_publish_ready,
    }


@router.get("/{sku}/category-template")
def get_category_template(sku: str, session: Session = Depends(get_session)):
    """Return current category template and validation status for item."""
    from packages.ebay.src.category_intelligence import CategoryIntelligence
    from packages.ebay.src.category_spreadsheet import CategorySpreadsheet

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    cat_id = item.ebay_category_id
    if not cat_id:
        return {
            "sku": sku,
            "category_id": None,
            "category_name": None,
            "required_fields": [],
            "recommended_fields": [],
            "field_constraints": {},
            "missing_required": list(item.missing_required_fields or []),
            "missing_recommended": list(item.missing_recommended_fields or []),
            "publish_ready": item.publish_ready,
            "template_fetched": item.category_template_fetched,
        }

    # Try disk cache first, then in-memory fetch
    sheet = CategorySpreadsheet()
    template = sheet.load_template(cat_id)
    if not template:
        ci = CategoryIntelligence()
        result = ci.get_template(cat_id)
        if result.ok:
            template = result.value

    if not template:
        return {
            "sku": sku,
            "category_id": cat_id,
            "category_name": item.ebay_category_name,
            "required_fields": [],
            "recommended_fields": [],
            "field_constraints": {},
            "missing_required": list(item.missing_required_fields or []),
            "missing_recommended": list(item.missing_recommended_fields or []),
            "publish_ready": item.publish_ready,
            "template_fetched": item.category_template_fetched,
        }

    ci = CategoryIntelligence()
    validation = ci.validate_item_specifics(item, template)
    return {
        "sku": sku,
        "category_id": cat_id,
        "category_name": template.category_name,
        "required_fields": template.required_fields,
        "recommended_fields": template.recommended_fields,
        "field_constraints": template.field_constraints,
        "missing_required": validation.missing_required,
        "missing_recommended": validation.missing_recommended,
        "invalid_fields": validation.invalid_fields,
        "publish_ready": validation.is_publish_ready,
        "template_fetched": item.category_template_fetched,
        "template_fetched_at": item.category_template_fetched_at,
    }


@router.post("/process")
def trigger_worker(
    background_tasks: BackgroundTasks,
    skus: str = "",
    e2e_only: bool = False,
):
    """Trigger the intake worker in the background."""
    filtered_skus = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            filtered_skus = assert_route_skus_allowed(
                filtered_skus,
                "items.process",
                require_non_empty=True,
            )
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not filtered_skus:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    if filtered_skus:
        from apps.worker.src.main import run_worker_for_skus

        result = run_worker_for_skus(filtered_skus)
        if not result.get("ok", False):
            error = str(result.get("error", "intake_processing_failed"))
            status_code = 503 if error == "ollama_unavailable" else 500
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or error,
            )
        return result

    def _run():
        import subprocess, sys
        subprocess.run([sys.executable, "apps/worker/src/main.py"])
    background_tasks.add_task(_run)
    return {"message": "Worker started — check console for progress."}


class BulkSkuRequest(BaseModel):
    skus: list[str]


@router.post("/bulk-approve")
def bulk_approve(body: BulkSkuRequest, session: Session = Depends(get_session)):
    """Approve multiple items at once."""
    from packages.core.src.constants import ItemStatus
    try:
        body.skus = assert_route_skus_allowed(body.skus, "items.bulk_approve", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    updated = []
    for sku in body.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.status = ItemStatus.APPROVED
            repo.upsert(item)
            updated.append(sku)
    return {"updated": len(updated), "skus": updated}


@router.post("/bulk-review")
def bulk_review(body: BulkSkuRequest, session: Session = Depends(get_session)):
    """Send multiple items back to review queue."""
    from packages.core.src.constants import ItemStatus
    try:
        body.skus = assert_route_skus_allowed(body.skus, "items.bulk_review", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    updated = []
    for sku in body.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.status = ItemStatus.NEEDS_REVIEW
            item.needs_review = True
            repo.upsert(item)
            updated.append(sku)
    return {"updated": len(updated), "skus": updated}


@router.post("/bulk-reject")
def bulk_reject(body: BulkSkuRequest, session: Session = Depends(get_session)):
    """Reject multiple items at once."""
    from packages.core.src.constants import ItemStatus
    try:
        body.skus = assert_route_skus_allowed(body.skus, "items.bulk_reject", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    repo = ItemRepository(session)
    updated = []
    for sku in body.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.status = ItemStatus.REJECTED
            repo.upsert(item)
            updated.append(sku)
    return {"updated": len(updated), "skus": updated}


# ── Phase 5B — Photos ──────────────────────────────────────────────────────────

@router.post("/{sku}/photos")
async def upload_photos(
    sku: str,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
):
    """Upload one or more photos, append their URLs to image_paths."""
    try:
        assert_route_sku_allowed(sku, "items.upload_photos")
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {sku} not found")

        from packages.ebay.src.photo_uploader import PhotoUploader
        uploader = PhotoUploader()
        new_urls = []

        for upload in files:
            suffix = Path(upload.filename or "photo.jpg").suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await upload.read())
                tmp_path = Path(tmp.name)
            try:
                result = uploader.upload(tmp_path)
                if result.ok:
                    new_urls.append(result.value)
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        paths = _image_paths_to_list(item.image_paths)
        if new_urls:
            item.image_paths = paths + new_urls
            repo.upsert(item)
            paths = _image_paths_to_list(item.image_paths)
        return {"image_paths": paths}
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})


class PhotoUrlBody(BaseModel):
    url: str


def _image_paths_to_list(value) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split("|") if p.strip()]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


@router.delete("/{sku}/photos")
def delete_photo(sku: str, body: PhotoUrlBody, session: Session = Depends(get_session)):
    """Remove a photo URL from image_paths (does not delete from Cloudinary)."""
    try:
        assert_route_sku_allowed(sku, "items.delete_photo")
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {sku} not found")

        paths = [p for p in _image_paths_to_list(item.image_paths) if p != body.url]
        item.image_paths = paths
        repo.upsert(item)
        return {"image_paths": paths}
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})


@router.post("/{sku}/photos/set-cover")
def set_cover_photo(sku: str, body: PhotoUrlBody, session: Session = Depends(get_session)):
    """Move a photo URL to index 0 in image_paths."""
    try:
        assert_route_sku_allowed(sku, "items.set_cover_photo")
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {sku} not found")

        paths = _image_paths_to_list(item.image_paths)
        if body.url not in paths:
            raise HTTPException(status_code=404, detail="URL not found in image_paths")

        paths.remove(body.url)
        paths.insert(0, body.url)
        item.image_paths = paths
        repo.upsert(item)
        return {"image_paths": paths}
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})


# ── Phase 5B — Claude Suggest ──────────────────────────────────────────────────

class ClaudeSuggestBody(BaseModel):
    type: str  # "title" or "description"


@router.post("/{sku}/claude-suggest")
def claude_suggest(sku: str, body: ClaudeSuggestBody, session: Session = Depends(get_session)):
    """
    Generate a Claude title or description suggestion.
    Never auto-applies — returns suggestion for user review only.
    """
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    if body.type not in ("title", "description"):
        raise HTTPException(status_code=422, detail="type must be 'title' or 'description'")

    from packages.core.src.config import get_settings
    cfg = get_settings()
    if not cfg.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    try:
        import anthropic
    except ImportError:
        raise HTTPException(status_code=503, detail="anthropic package not installed")

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    model = cfg.enrichment_model or "claude-sonnet-4-20250514"

    # Build item context
    title_raw = item.title_final or item.title_raw or ""
    brand = item.brand or ""
    category = item.ebay_category_name or item.category_label or ""
    condition = item.condition_label or ""
    condition_notes = item.condition_notes or ""
    description_existing = item.description_final or ""

    if body.type == "title":
        prompt = f"""You are an expert eBay listing optimizer. Generate a single eBay title for this item.

Item data:
- Current title: {title_raw}
- Brand: {brand}
- Category: {category}
- Condition: {condition}
- Condition notes: {condition_notes}

STRICT RULES — enforce exactly:
1. Start with Brand then item Type
2. Include specific identifiers (author, model, year if known)
3. Maximum 80 characters total
4. No punctuation
5. No subjective words (beautiful, amazing, stunning, rare unless objectively verifiable)
6. Do NOT use "Vintage" unless condition notes confirm pre-1990
7. Format: [Brand] [Type] [Specific Identifiers] [Format/Size]

Return ONLY the title text, nothing else."""

    else:  # description
        prompt = f"""You are an expert eBay listing writer. Write a short description for this item.

Item data:
- Title: {title_raw}
- Brand: {brand}
- Category: {category}
- Condition: {condition}
- Condition notes: {condition_notes}
- Existing description: {description_existing}

STRICT RULES — enforce exactly:
1. Maximum 4 sentences
2. Condition facts only — what you can observe
3. No subjective adjectives (beautiful, charming, lovely)
4. No flowery prose
5. Lead with condition, note any defects honestly, end with format/key specs
6. Example: "Hardcover in good used condition. Spine intact, pages clean with minor yellowing. Cover shows light shelf wear. 342 pages, first edition."

Return ONLY the description text, nothing else."""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = response.content[0].text.strip()

        # For descriptions: truncate to first 4 sentences server-side
        if body.type == "description":
            import re
            sentences = re.split(r'(?<=[.!?])\s+', suggestion)
            suggestion = " ".join(sentences[:4])

        # For titles: enforce 80-char max
        if body.type == "title":
            suggestion = suggestion[:80]

        return {"suggestion": suggestion, "type": body.type}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Claude API error: {exc}")


# ── Phase 5B — Price Suggest ───────────────────────────────────────────────────

@router.get("/{sku}/price-suggest")
def price_suggest(sku: str, session: Session = Depends(get_session)):
    """Get price suggestion from eBay sold comps."""
    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    query = item.title_final or item.title_raw or f"{item.brand or ''} {item.type or ''}".strip()
    if not query:
        raise HTTPException(status_code=400, detail="Item has no title for price lookup")

    try:
        from packages.enrichment.src.ebay_browse import get_price_comps
        comps = get_price_comps(query, limit=15)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    median = comps.get("median")
    suggested = median or item.estimated_price or item.list_price
    return {
        "median": median,
        "low": comps.get("low"),
        "high": comps.get("high"),
        "sample_size": comps.get("sample_size", 0),
        "suggested_price": round(float(suggested), 2) if suggested else None,
    }


# ── Phase 5B — Recategorize ────────────────────────────────────────────────────

@router.post("/{sku}/recategorize")
def recategorize_item(sku: str, session: Session = Depends(get_session)):
    """Re-run category detection and update local ebay_category_id/name."""
    from packages.ebay.src.category_intelligence import CategoryIntelligence
    from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
    from datetime import datetime

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    cat_intel = CategoryIntelligence()
    cat_id, cat_name = cat_intel.get_category_id(item)
    result = cat_intel.get_template(cat_id)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)

    template = result.value
    item.ebay_category_id = cat_id
    item.ebay_category_name = cat_name or template.category_name
    item.category_template_fetched = True
    item.category_template_fetched_at = datetime.utcnow().isoformat()
    CategorySpreadsheet().save_template(template)
    repo.upsert(item)

    return {
        "sku": sku,
        "ebay_category_id": cat_id,
        "ebay_category_name": item.ebay_category_name,
    }
