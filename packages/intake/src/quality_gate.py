from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from packages.core.src.constants import IntakeQualityStatus, ReviewTrigger
from packages.domain.src.entities.item import Item


PHOTO_REQUIREMENTS: dict[str, list[str]] = {
    "books": [
        "front_cover",
        "back_cover",
        "spine",
        "title_page",
        "copyright_publication_page",
        "condition_flaws",
        "markings_annotations",
    ],
    "clothing": [
        "front",
        "back",
        "brand_tag",
        "size_tag",
        "material_care_tag",
        "measurements",
        "flaws_wear",
    ],
    "shoes": [
        "pair_front_side",
        "soles",
        "size_tag_inside_label",
        "brand_label",
        "heels_toes_wear",
        "material_detail",
    ],
    "plush_toys": [
        "front",
        "back",
        "tag_tush_tag",
        "scale_measurement",
        "defects_wear",
        "copyright_manufacturer_tag",
    ],
    "bags": [
        "front_back",
        "interior",
        "brand_logo",
        "serial_date_code",
        "hardware",
        "corners_wear",
        "strap_handle",
        "authenticity_sensitive_evidence",
    ],
    "collectibles_antiques": [
        "full_object",
        "maker_marks",
        "bottom_back",
        "close_ups",
        "defects",
        "scale",
        "provenance_context",
    ],
}

OPTIONAL_WHEN_NOT_PRESENT = {
    "markings_annotations",
    "serial_date_code",
    "provenance_context",
}

PHOTO_TYPE_LABELS = {
    "front_cover": "front cover",
    "back_cover": "back cover",
    "spine": "spine",
    "title_page": "title page",
    "copyright_publication_page": "copyright/publication page",
    "condition_flaws": "condition/flaws",
    "markings_annotations": "notable markings/annotations if present",
    "front": "front",
    "back": "back",
    "brand_tag": "brand tag",
    "size_tag": "size tag",
    "material_care_tag": "material/care tag",
    "measurements": "measurements",
    "flaws_wear": "flaws/wear",
    "pair_front_side": "pair front/side",
    "soles": "soles",
    "size_tag_inside_label": "size tag/inside label",
    "brand_label": "brand label",
    "heels_toes_wear": "heels/toes/wear",
    "material_detail": "material detail",
    "tag_tush_tag": "tag/tush tag",
    "scale_measurement": "scale/measurement",
    "defects_wear": "defects/wear",
    "copyright_manufacturer_tag": "copyright/manufacturer tag",
    "front_back": "front/back",
    "interior": "interior",
    "brand_logo": "brand/logo",
    "serial_date_code": "serial/date code if applicable",
    "hardware": "hardware",
    "corners_wear": "corners/wear",
    "strap_handle": "strap/handle",
    "authenticity_sensitive_evidence": "authenticity-sensitive evidence",
    "full_object": "full object",
    "maker_marks": "maker marks",
    "bottom_back": "bottom/back",
    "close_ups": "close-ups",
    "defects": "defects",
    "scale": "scale",
    "provenance_context": "provenance/context if available",
}

PHOTO_TYPE_KEYWORDS = {
    "front_cover": ["front cover", "front-cover", "cover front", "cover_front"],
    "back_cover": ["back cover", "back-cover", "cover back", "cover_back"],
    "spine": ["spine"],
    "title_page": ["title page", "title-page", "title_page"],
    "copyright_publication_page": ["copyright", "publication", "pub page"],
    "condition_flaws": ["condition", "flaw", "damage", "wear", "defect"],
    "markings_annotations": ["marking", "annotation", "inscription", "highlight", "writing"],
    "front": ["front"],
    "back": ["back"],
    "brand_tag": ["brand tag", "brand-tag", "brand_tag", "label brand"],
    "size_tag": ["size tag", "size-tag", "size_tag"],
    "material_care_tag": ["material", "care tag", "care-tag", "care_tag", "fabric"],
    "measurements": ["measurement", "measurements", "measure", "ruler", "tape"],
    "flaws_wear": ["flaw", "wear", "stain", "hole", "pilling", "defect"],
    "pair_front_side": ["pair", "front", "side"],
    "soles": ["sole", "soles", "bottom"],
    "size_tag_inside_label": ["inside label", "inside-label", "size tag", "size_tag", "inside"],
    "brand_label": ["brand", "label", "logo"],
    "heels_toes_wear": ["heel", "toe", "wear"],
    "material_detail": ["material", "leather", "canvas", "suede", "detail"],
    "tag_tush_tag": ["tush tag", "tush-tag", "tush_tag", "hang tag", "hang-tag", "brand tag", "brand-tag"],
    "scale_measurement": ["scale", "measurement", "ruler", "tape"],
    "defects_wear": ["defect", "wear", "flaw", "damage"],
    "copyright_manufacturer_tag": ["copyright", "manufacturer", "maker", "tag"],
    "front_back": ["front", "back"],
    "interior": ["interior", "inside", "lining"],
    "brand_logo": ["brand", "logo"],
    "serial_date_code": ["serial", "date code", "date-code", "date_code"],
    "hardware": ["hardware", "zipper", "clasp", "buckle", "feet"],
    "corners_wear": ["corner", "corners", "wear"],
    "strap_handle": ["strap", "handle"],
    "authenticity_sensitive_evidence": ["authentic", "authenticity", "serial", "date code", "stamp", "hologram"],
    "full_object": ["full", "object", "front"],
    "maker_marks": ["maker", "mark", "signature", "stamp"],
    "bottom_back": ["bottom", "back", "underside"],
    "close_ups": ["close", "closeup", "close-up", "detail"],
    "defects": ["defect", "damage", "flaw", "chip", "crack"],
    "scale": ["scale", "measurement", "ruler"],
    "provenance_context": ["provenance", "context", "receipt", "certificate"],
}


@dataclass
class IntakeQualityResult:
    intake_quality_status: str
    has_enough_photos: bool
    missing_photo_types: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    suggested_next_uploads: list[str] = field(default_factory=list)
    should_run_deep_analysis: bool = False
    should_block_publish_approval: bool = True
    needs_more_photos_for_analysis: bool = False
    category_family: str = "unknown"
    present_photo_types: list[str] = field(default_factory=list)
    required_photo_types: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "intake_quality_status": self.intake_quality_status,
            "has_enough_photos": self.has_enough_photos,
            "missing_photo_types": self.missing_photo_types,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested_next_uploads": self.suggested_next_uploads,
            "should_run_deep_analysis": self.should_run_deep_analysis,
            "should_block_publish_approval": self.should_block_publish_approval,
            "needs_more_photos_for_analysis": self.needs_more_photos_for_analysis,
            "category_family": self.category_family,
            "present_photo_types": self.present_photo_types,
            "required_photo_types": self.required_photo_types,
        }


def evaluate_intake_quality(
    item: Item,
    photo_meta: list | None = None,
) -> IntakeQualityResult:
    family = category_family_for_item(item)
    required = PHOTO_REQUIREMENTS.get(family, [])
    present = infer_present_photo_types(item, photo_meta=photo_meta)
    missing = [photo_type for photo_type in required if photo_type not in present and not _optional_photo_satisfied(item, photo_type)]
    missing_labels = [_label(photo_type) for photo_type in missing]
    has_enough_photos = bool(required) and not missing
    confidence = _confidence(item, missing, required)
    review_reasons = {str(reason) for reason in (item.review_reasons or [])}

    status = IntakeQualityStatus.READY_FOR_DEEP_ANALYSIS
    reason = "Photo coverage is sufficient for provider-agnostic deep analysis."
    if not family or family == "unknown":
        status = IntakeQualityStatus.NEEDS_CATEGORY_REVIEW
        reason = "Category family is unknown; choose or confirm a category before deep analysis."
    elif _authenticity_sensitive(item):
        status = IntakeQualityStatus.NEEDS_AUTHENTICITY_REVIEW
        reason = "High-value or authenticity-sensitive item requires manual review before deep analysis or approval."
    elif confidence < 0.55 or ReviewTrigger.LOW_CONFIDENCE in review_reasons:
        status = IntakeQualityStatus.LOW_CONFIDENCE_HOLD
        reason = "Existing item confidence is too low; collect stronger photos before deep analysis."
    elif missing:
        status = IntakeQualityStatus.NEEDS_MORE_PHOTOS
        reason = "Missing required category-family photo coverage for reliable deep analysis."
    elif not _has_condition_context(item) and _post_analysis_item(item):
        status = IntakeQualityStatus.NEEDS_CONDITION_REVIEW
        reason = "Condition context is missing; add condition notes or condition data before approval."
    elif _needs_user_context(item):
        status = IntakeQualityStatus.NEEDS_USER_CONTEXT
        reason = "Operator context is required before deep analysis."

    should_run = status == IntakeQualityStatus.READY_FOR_DEEP_ANALYSIS
    return IntakeQualityResult(
        intake_quality_status=status,
        has_enough_photos=has_enough_photos,
        missing_photo_types=missing_labels,
        confidence=confidence,
        reason=reason,
        suggested_next_uploads=missing_labels,
        should_run_deep_analysis=should_run,
        should_block_publish_approval=not should_run,
        needs_more_photos_for_analysis=bool(missing),
        category_family=family,
        present_photo_types=sorted(_label(photo_type) for photo_type in present),
        required_photo_types=[_label(photo_type) for photo_type in required],
    )


def category_family_for_item(item: Item) -> str:
    category_text = " ".join(
        str(value or "").lower()
        for value in [item.category_key, item.category_label, item.ebay_category_name]
    )
    title_text = " ".join(str(value or "").lower() for value in [item.title_raw, item.title_final])
    raw = f"{category_text} {title_text}"
    if any(token in category_text for token in ["book", "atlas", "magazine"]):
        return "books"
    if any(token in category_text for token in ["shoe", "sneaker", "boot", "sandal"]):
        return "shoes"
    if any(token in category_text for token in ["bag", "purse", "handbag", "wallet", "tote"]):
        return "bags"
    if any(token in category_text for token in ["plush", "toy", "doll", "action figure", "lego"]):
        return "plush_toys"
    if any(token in category_text for token in ["collectible", "antique", "vintage object", "ceramic", "figurine"]):
        return "collectibles_antiques"
    if any(token in category_text for token in ["cloth", "shirt", "jacket", "pants", "dress", "sweater"]):
        return "clothing"
    if any(token in raw for token in ["book", "atlas", "magazine"]):
        return "books"
    if any(token in raw for token in ["shoe", "sneaker", "boot", "sandal"]):
        return "shoes"
    if any(token in raw for token in ["bag", "purse", "handbag", "wallet", "tote"]):
        return "bags"
    if any(token in raw for token in ["plush", "toy", "doll", "action figure", "lego"]):
        return "plush_toys"
    if any(token in raw for token in ["collectible", "antique", "vintage object", "ceramic", "figurine"]):
        return "collectibles_antiques"
    if any(token in raw for token in ["cloth", "shirt", "jacket", "pants", "dress", "sweater"]):
        return "clothing"
    return "unknown"


def infer_present_photo_types(
    item: Item,
    photo_meta: list | None = None,
) -> set[str]:
    """Infer which quality-gate photo-type tokens are present.

    When ``photo_meta`` is supplied (list of PhotoMeta from photo_types.py),
    user-labeled and model-labeled entries are consulted first via the
    QUALITY_GATE_TO_PHOTO_TYPE inverse map — they outrank filename inference.
    Filename inference runs for any path not covered by explicit metadata.
    """
    present: set[str] = set()

    if photo_meta:
        # Import here to avoid circular import at module load time.
        from packages.intake.src.photo_types import (
            QUALITY_GATE_TO_PHOTO_TYPE,
            PhotoType,
        )
        # Build inverse map: PhotoType value → quality_gate token(s).
        _inverse: dict[str, list[str]] = {}
        for qt_token, pt_value in QUALITY_GATE_TO_PHOTO_TYPE.items():
            _inverse.setdefault(pt_value, []).append(qt_token)

        covered_paths: set[str] = set()
        for meta in photo_meta:
            if meta.photo_type and meta.photo_type != PhotoType.UNKNOWN:
                for qt_token in _inverse.get(meta.photo_type, []):
                    present.add(qt_token)
                covered_paths.add(meta.path)

        # Filename inference for paths not covered by explicit meta.
        uncovered = [
            p for p in (item.image_paths or []) if str(p) not in covered_paths
        ]
        haystacks = [_photo_text(p) for p in uncovered]
    else:
        haystacks = [_photo_text(path) for path in item.image_paths or []]

    for photo_type, keywords in PHOTO_TYPE_KEYWORDS.items():
        if any(_matches_keywords(text, keywords) for text in haystacks):
            present.add(photo_type)
    return present


def apply_intake_quality_to_item(item: Item, result: IntakeQualityResult | None = None) -> Item:
    quality = result or evaluate_intake_quality(item)
    item.intake_quality_status = quality.intake_quality_status
    item.missing_photo_types = list(quality.missing_photo_types)
    item.needs_more_photos_for_analysis = quality.needs_more_photos_for_analysis
    review_reasons = list(item.review_reasons or [])
    if quality.needs_more_photos_for_analysis and "needs_more_photos_for_analysis" not in review_reasons:
        review_reasons.append("needs_more_photos_for_analysis")
    if quality.should_block_publish_approval and quality.intake_quality_status not in review_reasons:
        review_reasons.append(quality.intake_quality_status)
    item.review_reasons = review_reasons
    return item


def _photo_text(path: object) -> str:
    raw = str(path or "").lower()
    stem = Path(raw).stem.lower()
    return " ".join([raw, stem]).replace("_", " ").replace("-", " ")


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    return all(part in text for part in str(keywords[0]).replace("_", " ").split()) if len(keywords) == 1 else any(
        keyword.replace("_", " ").replace("-", " ") in text for keyword in keywords
    )


def _label(photo_type: str) -> str:
    return PHOTO_TYPE_LABELS.get(photo_type, photo_type.replace("_", " "))


def _optional_photo_satisfied(item: Item, photo_type: str) -> bool:
    if photo_type not in OPTIONAL_WHEN_NOT_PRESENT:
        return False
    text = _item_text(item)
    if photo_type == "markings_annotations":
        return not any(token in text for token in ["marking", "annotation", "inscribed", "signed", "writing"])
    if photo_type == "serial_date_code":
        return not any(token in text for token in ["luxury", "designer", "coach", "gucci", "prada", "louis vuitton", "serial"])
    if photo_type == "provenance_context":
        return not any(token in text for token in ["antique", "rare", "provenance", "certificate"])
    return False


def _confidence(item: Item, missing: list[str], required: list[str]) -> float:
    base = item.confidence_score if item.confidence_score is not None else 0.75
    if required:
        coverage = max(0.0, (len(required) - len(missing)) / len(required))
        base = min(float(base), 0.35 + coverage * 0.65)
    return round(max(0.0, min(1.0, float(base))), 3)


def _has_condition_context(item: Item) -> bool:
    return bool(
        str(item.condition_label or "").strip()
        or str(item.condition_id or "").strip()
        or str(item.condition_notes or "").strip()
        or item.defects
    )


def _post_analysis_item(item: Item) -> bool:
    return bool(item.title_final or item.description_final or item.confidence_score is not None)


def _needs_user_context(item: Item) -> bool:
    return any(
        token in _item_text(item)
        for token in ["unknown", "not sure", "needs context", "research needed"]
    )


def _authenticity_sensitive(item: Item) -> bool:
    text = _item_text(item)
    if (item.estimated_price or 0) >= 75:
        return True
    return any(
        token in text
        for token in [
            "authentic",
            "luxury",
            "designer",
            "signed",
            "rare",
            "antique",
            "first edition",
            "coach",
            "gucci",
            "prada",
            "louis vuitton",
        ]
    )


def _item_text(item: Item) -> str:
    values = [
        item.category_key,
        item.category_label,
        item.ebay_category_name,
        item.title_raw,
        item.title_final,
        item.brand,
        item.type,
        item.condition_label,
        item.condition_notes,
        item.notes,
        " ".join(str(reason) for reason in item.review_reasons or []),
    ]
    return " ".join(str(value or "").lower() for value in values)
