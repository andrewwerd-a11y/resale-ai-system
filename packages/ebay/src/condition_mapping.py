from __future__ import annotations

import re

CONDITION_ID_TO_ENUM: dict[str, str] = {
    "1000": "NEW",
    "1500": "NEW_OTHER",
    "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED",
    "2010": "EXCELLENT_REFURBISHED",
    "2020": "VERY_GOOD_REFURBISHED",
    "2030": "GOOD_REFURBISHED",
    "2500": "SELLER_REFURBISHED",
    "2750": "LIKE_NEW",
    "2990": "PRE_OWNED_EXCELLENT",
    "3000": "USED_EXCELLENT",
    "3010": "PRE_OWNED_FAIR",
    "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}

CONDITION_ENUM_ALIASES: dict[str, str] = {
    "NEW": "NEW",
    "NEW_OTHER": "NEW_OTHER",
    "NEW_WITH_DEFECTS": "NEW_WITH_DEFECTS",
    "CERTIFIED_REFURBISHED": "CERTIFIED_REFURBISHED",
    "EXCELLENT_REFURBISHED": "EXCELLENT_REFURBISHED",
    "VERY_GOOD_REFURBISHED": "VERY_GOOD_REFURBISHED",
    "GOOD_REFURBISHED": "GOOD_REFURBISHED",
    "SELLER_REFURBISHED": "SELLER_REFURBISHED",
    "LIKE_NEW": "LIKE_NEW",
    "PRE_OWNED_EXCELLENT": "PRE_OWNED_EXCELLENT",
    "PRE_OWNED_FAIR": "PRE_OWNED_FAIR",
    "USED_EXCELLENT": "USED_EXCELLENT",
    "USED_VERY_GOOD": "USED_VERY_GOOD",
    "VERY_GOOD": "USED_VERY_GOOD",
    "USED_GOOD": "USED_GOOD",
    "USED_ACCEPTABLE": "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING": "FOR_PARTS_OR_NOT_WORKING",
}

INVENTORY_ENUM_TO_CONDITION_ID: dict[str, str] = {
    enum_name: condition_id
    for condition_id, enum_name in CONDITION_ID_TO_ENUM.items()
}


def normalize_condition_id(value: object) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))[:4]
    return digits if digits in CONDITION_ID_TO_ENUM else ""


def normalize_inventory_enum(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in CONDITION_ID_TO_ENUM:
        return CONDITION_ID_TO_ENUM[text]
    return CONDITION_ENUM_ALIASES.get(text, "")


def condition_id_to_inventory_enum(condition_id: object, *, default: str = "") -> str:
    normalized = normalize_condition_id(condition_id)
    if not normalized:
        return default
    return CONDITION_ID_TO_ENUM.get(normalized, default)


def inventory_enum_to_condition_id(inventory_enum: object, *, default: str = "") -> str:
    normalized = normalize_inventory_enum(inventory_enum)
    if not normalized:
        return default
    return INVENTORY_ENUM_TO_CONDITION_ID.get(normalized, default)


def validate_condition_id_enum_pair(condition_id: object, inventory_enum: object) -> bool:
    expected_enum = condition_id_to_inventory_enum(condition_id, default="")
    normalized_enum = normalize_inventory_enum(inventory_enum)
    return bool(expected_enum and normalized_enum and expected_enum == normalized_enum)
