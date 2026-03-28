from enum import Enum


class ItemStatus(str, Enum):
    PENDING_INTAKE = "pending_intake"
    SKU_SUGGESTED = "sku_suggested"
    SKU_CONFIRMED = "sku_confirmed"
    ANALYZED = "analyzed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    EXPORT_READY = "export_ready"
    EXPORTED = "exported"
    LISTED = "listed"
    SOLD = "sold"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class ItemMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


class ReviewTrigger(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    HIGH_VALUE = "high_value"
    MISSING_FIELDS = "missing_fields"
    MANUAL_FLAG = "manual_flag"
    AI_UNCERTAIN = "ai_uncertain"
    CONDITION_CONCERN = "condition_concern"
    PRICE_OUTLIER = "price_outlier"
