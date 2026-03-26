"""
SKU Registry — the single authority for SKU generation and reservation.

Rules:
- Existing SKUs are ALWAYS preserved, never regenerated.
- New SKUs are suggested from config, confirmed by the user, then reserved.
- Reservation is atomic (SQLite write) before any folder rename happens.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlmodel import Session

from packages.core.src.config import get_sku_prefixes
from packages.core.src.result import Result
from packages.data.src.repositories.sku_repo import SKURepository


SKU_PATTERN = re.compile(r"^([A-Z]{2})-(\d{6})$")


class SKURegistry:
    def __init__(self, session: Session):
        self.repo = SKURepository(session)
        self.prefixes = get_sku_prefixes()

    def is_valid_sku(self, sku: str) -> bool:
        match = SKU_PATTERN.match(sku)
        if not match:
            return False
        prefix = match.group(1)
        return prefix in self.prefixes

    def suggest_next(self, prefix: str) -> Result[str]:
        """Suggest the next SKU for a prefix without reserving it."""
        if prefix not in self.prefixes:
            return Result.failure(f"Unknown prefix: {prefix}")
        last = self.repo.get_last_number(prefix)
        suggested = f"{prefix}-{last + 1:06d}"
        return Result.success(suggested)

    def reserve(self, prefix: str, override_number: int | None = None) -> Result[str]:
        """
        Reserve the next SKU atomically.
        If override_number is given, reserves that specific number (for migration).
        Returns the reserved SKU string.
        """
        if prefix not in self.prefixes:
            return Result.failure(f"Unknown prefix: {prefix}")

        if override_number is not None:
            # Used during migration to preserve exact existing numbers
            sku = f"{prefix}-{override_number:06d}"
            self.repo.preserve_existing(prefix, override_number)
            return Result.success(sku)

        sku = self.repo.reserve_next(prefix)
        return Result.success(sku)

    def preserve_existing_sku(self, sku: str) -> Result[str]:
        """
        Called during migration. Registers an existing SKU so the
        registry never issues a duplicate.
        """
        parsed = self.repo.parse_sku(sku)
        if not parsed:
            return Result.failure(f"Cannot parse SKU: {sku}")
        prefix, number = parsed
        self.repo.preserve_existing(prefix, number)
        return Result.success(sku)

    def prefix_for_category(self, category_key: str) -> str | None:
        """Return the prefix for a given category_key."""
        for prefix, data in self.prefixes.items():
            if data.get("category_key") == category_key:
                return prefix
        return None

    def initialize_from_scan(self, sku_list: list[str]) -> dict[str, int]:
        """Bulk-initialize registry from a list of existing SKUs."""
        return self.repo.initialize_from_existing_folders(sku_list)
