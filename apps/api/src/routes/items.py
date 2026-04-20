from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository

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
def apply_stale_drops(session: Session = Depends(get_session)):
    """Apply configured price drop to all stale listings."""
    from packages.sync.src.stale_checker import StaleChecker
    checker = StaleChecker()
    count = checker.apply_price_drops(session)
    return {"updated": count, "drop_percent": checker.stale_drop}


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
def trigger_worker(background_tasks: BackgroundTasks):
    """Trigger the intake worker in the background."""
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
    repo = ItemRepository(session)
    updated = []
    for sku in body.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.status = ItemStatus.REJECTED
            repo.upsert(item)
            updated.append(sku)
    return {"updated": len(updated), "skus": updated}
