from __future__ import annotations
from abc import ABC, abstractmethod
from packages.core.src.result import Result


class VisionProvider(ABC):
    @abstractmethod
    def analyze(self, image_paths: list[str], prompt: str) -> Result[str]:
        """Analyze images and return the raw text response."""
        ...
