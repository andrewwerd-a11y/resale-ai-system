"""Shared type aliases used across all packages."""
from __future__ import annotations

from typing import Any

SKU = str              # e.g. "CL-000007"
CategoryKey = str      # e.g. "clothing"
Prefix = str           # e.g. "CL"
FilePath = str         # absolute or relative path string
JsonDict = dict[str, Any]
ImagePaths = list[str]
