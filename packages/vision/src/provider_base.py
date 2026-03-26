"""
Abstract base class for vision model providers.
All providers (Ollama, OpenAI-compatible, etc.) implement this interface.
Business logic never imports a concrete provider directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from packages.core.src.result import Result
from packages.core.src.types import JsonDict


class VisionProvider(ABC):
    """
    Contract: given a list of image paths and a text prompt,
    return a structured JSON dict or a failure Result.
    """

    @abstractmethod
    def analyze(
        self,
        image_paths: list[Path],
        prompt: str,
        max_tokens: int = 2048,
    ) -> Result[JsonDict]:
        """Send images + prompt to the model. Return parsed JSON dict."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider/model is reachable."""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """The model identifier string (e.g. 'qwen2.5vl:7b')."""
        ...
