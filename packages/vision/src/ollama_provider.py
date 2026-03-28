from __future__ import annotations
import base64
import json
from pathlib import Path
import httpx

from packages.core.src.result import Result
from packages.vision.src.provider_base import VisionProvider
from packages.core.src.config import get_settings


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


class OllamaProvider(VisionProvider):
    def __init__(
        self,
        model: str | None = None,
        num_ctx: int = 8192,
        timeout: int = 600,
    ) -> None:
        settings = get_settings()
        self._base_url = settings.ollama_base_url
        self._model = model or settings.vision_model_default
        self._num_ctx = num_ctx
        self._timeout = timeout

    def analyze(self, image_paths: list[str], prompt: str) -> Result[str]:
        images = []
        for path in image_paths:
            p = Path(path)
            if not p.exists():
                continue
            try:
                images.append(_encode_image(path))
            except Exception as e:
                return Result.failure(f"Failed to encode image {path}: {e}")

        if not images:
            return Result.failure("No valid images to analyze")

        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": images,
            "stream": False,
            "options": {
                "num_ctx": self._num_ctx,
                "temperature": 0.1,
            },
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return Result.success(data.get("response", ""))
        except httpx.TimeoutException:
            return Result.failure(f"Ollama request timed out after {self._timeout}s")
        except Exception as e:
            return Result.failure(f"Ollama request failed: {e}")
