"""Provider-agnostic enums and shared types for the staged intake pipeline.

Pure data definitions. No I/O, no provider calls. Importing this module is safe
from any layer (domain, services, routes, tests).
"""
from __future__ import annotations


class IntakePipelineStage:
    PHOTO_INTAKE = "PHOTO_INTAKE"
    FIRST_PASS_IDENTITY = "FIRST_PASS_IDENTITY"
    PHOTO_SUFFICIENCY = "PHOTO_SUFFICIENCY"
    CATEGORY_RESOLUTION = "CATEGORY_RESOLUTION"
    MARKETPLACE_REQUIREMENTS = "MARKETPLACE_REQUIREMENTS"
    DEEP_ANALYSIS = "DEEP_ANALYSIS"
    CORRECTION_REPORT = "CORRECTION_REPORT"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REANALYSIS_AFTER_EDIT = "REANALYSIS_AFTER_EDIT"
    PLATFORM_TRANSLATION = "PLATFORM_TRANSLATION"
    PLATFORM_READINESS = "PLATFORM_READINESS"
    PUBLISH_APPROVAL = "PUBLISH_APPROVAL"

    ALL = [
        PHOTO_INTAKE,
        FIRST_PASS_IDENTITY,
        PHOTO_SUFFICIENCY,
        CATEGORY_RESOLUTION,
        MARKETPLACE_REQUIREMENTS,
        DEEP_ANALYSIS,
        CORRECTION_REPORT,
        HUMAN_REVIEW,
        REANALYSIS_AFTER_EDIT,
        PLATFORM_TRANSLATION,
        PLATFORM_READINESS,
        PUBLISH_APPROVAL,
    ]


class IntakeDecision:
    READY_FOR_DEEP_ANALYSIS = "READY_FOR_DEEP_ANALYSIS"
    NEEDS_MORE_PHOTOS = "NEEDS_MORE_PHOTOS"
    NEEDS_USER_CONTEXT = "NEEDS_USER_CONTEXT"
    NEEDS_CATEGORY_REVIEW = "NEEDS_CATEGORY_REVIEW"
    NEEDS_AUTHENTICITY_REVIEW = "NEEDS_AUTHENTICITY_REVIEW"
    NEEDS_CONDITION_REVIEW = "NEEDS_CONDITION_REVIEW"
    LOW_CONFIDENCE_HOLD = "LOW_CONFIDENCE_HOLD"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    READY_FOR_PLATFORM_TRANSLATION = "READY_FOR_PLATFORM_TRANSLATION"
    BLOCKED_FOR_PUBLISH = "BLOCKED_FOR_PUBLISH"

    ALL = [
        READY_FOR_DEEP_ANALYSIS,
        NEEDS_MORE_PHOTOS,
        NEEDS_USER_CONTEXT,
        NEEDS_CATEGORY_REVIEW,
        NEEDS_AUTHENTICITY_REVIEW,
        NEEDS_CONDITION_REVIEW,
        LOW_CONFIDENCE_HOLD,
        READY_FOR_HUMAN_REVIEW,
        READY_FOR_PLATFORM_TRANSLATION,
        BLOCKED_FOR_PUBLISH,
    ]


class PhotoType:
    # General
    FRONT = "front"
    BACK = "back"
    SIDE = "side"
    DETAIL = "detail"
    FLAW = "flaw"
    MEASUREMENT = "measurement"
    SCALE = "scale"
    LABEL = "label"
    TAG = "tag"
    SERIAL_OR_DATE_CODE = "serial_or_date_code"
    MAKER_MARK = "maker_mark"
    INTERIOR = "interior"
    SOLE = "sole"
    SPINE = "spine"
    TITLE_PAGE = "title_page"
    COPYRIGHT_PAGE = "copyright_page"
    BRAND_TAG = "brand_tag"
    SIZE_TAG = "size_tag"
    MATERIAL_CARE_TAG = "material_care_tag"
    TUSH_TAG = "tush_tag"
    HARDWARE = "hardware"
    CORNERS_WEAR = "corners_wear"
    UNKNOWN = "unknown"

    ALL = [
        FRONT, BACK, SIDE, DETAIL, FLAW, MEASUREMENT, SCALE, LABEL, TAG,
        SERIAL_OR_DATE_CODE, MAKER_MARK, INTERIOR, SOLE, SPINE, TITLE_PAGE,
        COPYRIGHT_PAGE, BRAND_TAG, SIZE_TAG, MATERIAL_CARE_TAG, TUSH_TAG,
        HARDWARE, CORNERS_WEAR, UNKNOWN,
    ]


class RiskFlag:
    LOW_CONFIDENCE = "low_confidence"
    HIGH_VALUE_ESTIMATE = "high_value_estimate"
    AUTHENTICITY_SENSITIVE_BRAND = "authenticity_sensitive_brand"
    ANTIQUE = "antique"
    POSSIBLE_COUNTERFEIT_RISK = "possible_counterfeit_risk"
    CONDITION_UNCERTAIN = "condition_uncertain"
    CATEGORY_UNCERTAIN = "category_uncertain"
    MISSING_REQUIRED_PHOTOS = "missing_required_photos"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    MALFORMED_CONDITION_ID = "malformed_condition_id"
    INVALID_ASPECTS = "invalid_aspects"
    EXISTING_OFFER_RISK = "existing_offer_risk"
    MARKETPLACE_POLICY_UNKNOWN = "marketplace_policy_unknown"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"

    ALL = [
        LOW_CONFIDENCE,
        HIGH_VALUE_ESTIMATE,
        AUTHENTICITY_SENSITIVE_BRAND,
        ANTIQUE,
        POSSIBLE_COUNTERFEIT_RISK,
        CONDITION_UNCERTAIN,
        CATEGORY_UNCERTAIN,
        MISSING_REQUIRED_PHOTOS,
        MISSING_REQUIRED_FIELDS,
        MALFORMED_CONDITION_ID,
        INVALID_ASPECTS,
        EXISTING_OFFER_RISK,
        MARKETPLACE_POLICY_UNKNOWN,
        NEEDS_MANUAL_REVIEW,
    ]


class ManualEditTrustLevel:
    FACTUAL_MEASUREMENT = "factual_measurement"
    FACTUAL_OBSERVATION = "factual_observation"
    USER_CLAIM = "user_claim"
    RISKY_CLAIM = "risky_claim"
    OVERRIDE = "override"

    ALL = [
        FACTUAL_MEASUREMENT,
        FACTUAL_OBSERVATION,
        USER_CLAIM,
        RISKY_CLAIM,
        OVERRIDE,
    ]


class PhotoSource:
    LOCAL = "local"
    HOSTED = "hosted"
    UPLOADED = "uploaded"
    GENERATED = "generated"

    ALL = [LOCAL, HOSTED, UPLOADED, GENERATED]


class ProviderKind:
    """How a result was produced."""
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    EXTERNAL_MODEL = "external_model"
    LOCAL_MODEL = "local_model"
    MOCK = "mock"

    ALL = [DETERMINISTIC_FALLBACK, EXTERNAL_MODEL, LOCAL_MODEL, MOCK]


class ConfidenceSource:
    """What evidence backs the confidence score."""
    HEURISTIC = "heuristic"
    VISUAL_MODEL = "visual_model"
    USER_LABELED = "user_labeled"
    CACHED_METADATA = "cached_metadata"
    MIXED = "mixed"

    ALL = [HEURISTIC, VISUAL_MODEL, USER_LABELED, CACHED_METADATA, MIXED]


DETERMINISTIC_FALLBACK_WARNING = (
    "This result was produced by a deterministic heuristic fallback, not a real "
    "vision or language model. All confidence values are conservative estimates. "
    "Human review is required before any publish action."
)
