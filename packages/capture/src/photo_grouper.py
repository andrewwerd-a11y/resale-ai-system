"""
PhotoGrouper — groups photos into per-item sets based on time proximity.
Photos taken within time_window_seconds of each other belong to the same item.
"""
from __future__ import annotations

from pathlib import Path


class PhotoGrouper:
    def __init__(self, time_window_seconds: int = 90):
        """Photos within time_window of each other are treated as one item."""
        self.time_window_seconds = time_window_seconds

    def group(self, photo_paths: list[Path]) -> list[list[Path]]:
        """
        Returns list of groups. Each group is one item's photos,
        sorted by capture time within the group.
        """
        if not photo_paths:
            return []

        sorted_paths = sorted(photo_paths, key=lambda p: p.stat().st_mtime)

        groups: list[list[Path]] = []
        current_group: list[Path] = [sorted_paths[0]]

        for prev, curr in zip(sorted_paths, sorted_paths[1:]):
            prev_mtime = prev.stat().st_mtime
            curr_mtime = curr.stat().st_mtime
            if curr_mtime - prev_mtime <= self.time_window_seconds:
                current_group.append(curr)
            else:
                groups.append(current_group)
                current_group = [curr]

        if current_group:
            groups.append(current_group)

        return groups
