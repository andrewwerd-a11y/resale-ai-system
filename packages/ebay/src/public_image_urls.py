from __future__ import annotations

import re
from urllib.parse import urlparse


def looks_like_public_image_url_candidate(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered.startswith(("http://", "https://", "http:\\", "https:\\"))


def normalize_public_image_url(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    normalized = re.sub(r"^(https?):/+", lambda m: f"{m.group(1).lower()}://", normalized, count=1, flags=re.IGNORECASE)
    return normalized


def is_valid_public_image_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return "\\" not in value


def normalize_public_image_urls(values: list[str]) -> tuple[list[str], list[str]]:
    normalized_urls: list[str] = []
    invalid_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = normalize_public_image_url(value)
        if not is_valid_public_image_url(normalized):
            invalid_values.append(str(value))
            continue
        if normalized not in seen:
            seen.add(normalized)
            normalized_urls.append(normalized)

    return normalized_urls, invalid_values


def extract_public_image_urls(values: list[str]) -> list[str]:
    candidates = [str(value).strip() for value in values if looks_like_public_image_url_candidate(str(value))]
    normalized_urls, _invalid_values = normalize_public_image_urls(candidates)
    return normalized_urls
