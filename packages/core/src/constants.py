"""System-wide constants. Import from here, never hardcode strings."""

# ── Item status lifecycle ────────────────────────────────────────────────────
class ItemStatus:
    PENDING_INTAKE  = "pending_intake"
    SKU_SUGGESTED   = "sku_suggested"
    SKU_CONFIRMED   = "sku_confirmed"
    ANALYZED        = "analyzed"
    NEEDS_REVIEW    = "needs_review"
    APPROVED        = "approved"
    EXPORT_READY    = "export_ready"
    EXPORTED        = "exported"
    LISTED          = "listed"
    SOLD            = "sold"
    ARCHIVED        = "archived"
    REJECTED        = "rejected"

    ALL = [
        PENDING_INTAKE, SKU_SUGGESTED, SKU_CONFIRMED, ANALYZED,
        NEEDS_REVIEW, APPROVED, EXPORT_READY, EXPORTED,
        LISTED, SOLD, ARCHIVED, REJECTED,
    ]


# ── Item mode ────────────────────────────────────────────────────────────────
class ItemMode:
    SINGLE  = "single"
    LOT     = "lot"
    REVIEW  = "review"
    REJECT  = "reject"

    ALL = [SINGLE, LOT, REVIEW, REJECT]


# ── Triage outcomes ──────────────────────────────────────────────────────────
class TriageOutcome:
    SINGLE  = "single"
    LOT     = "lot"
    REVIEW  = "review"
    REJECT  = "reject"


# ── Review trigger keys ──────────────────────────────────────────────────────
class ReviewTrigger:
    LOW_CONFIDENCE          = "low_confidence"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    CONFLICTING_VALUES      = "conflicting_extracted_values"
    IMAGE_INSUFFICIENCY     = "image_insufficiency"
    HIGH_VALUE_ESTIMATE     = "high_value_estimate"
    UNUSUAL_DEFECTS         = "unusual_defects"
    ANTIQUE                 = "antique"
    SIGNED                  = "signed"
    INSCRIBED               = "inscribed"
    FIRST_EDITION           = "first_edition"
    RARE_BINDING            = "rare_binding"
    COLLECTIBLE_EDITION     = "collectible_edition"
    LUXURY_BRAND            = "luxury_brand"
    UNCLEAR_BRAND           = "unclear_brand"
    UNCLEAR_AUTHENTICITY    = "unclear_authenticity"
    POSSIBLE_COUNTERFEIT    = "possible_counterfeit"
    PRICE_AMBIGUITY         = "price_ambiguity"
    RARE                    = "rare"
    VINTAGE                 = "vintage"


# ── Image filename convention ────────────────────────────────────────────────
IMAGE_FILENAME_PATTERN = "{n:02d}.jpg"   # 01.jpg, 02.jpg, ...
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

# ── Extraction schema version ────────────────────────────────────────────────
EXTRACTION_SCHEMA_VERSION = "v1"

# ── Platform identifiers ─────────────────────────────────────────────────────
class Platform:
    EBAY     = "ebay"
    POSHMARK = "poshmark"   # V3
    MERCARI  = "mercari"    # V3
