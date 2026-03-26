"""
Ollama vision provider — sends images to a locally running Ollama model.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.core.src.config import get_settings
from packages.core.src.result import Result
from packages.core.src.types import JsonDict
from packages.vision.src.provider_base import VisionProvider


def _encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?", "", text).strip()
    text = re.sub(r"```", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None
                    depth = 0
    return None


class OllamaProvider(VisionProvider):
    def __init__(self, model_id: str | None = None):
        settings = get_settings()
        self._model_id = model_id or settings.vision_model_default
        self.base_url = settings.ollama_base_url
        self.dry_run = settings.dry_run
        self.timeout = 600

    @property
    def model_id(self) -> str:
        return self._model_id

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def analyze(
        self,
        image_paths: list[Path],
        prompt: str,
        max_tokens: int = 2048,
    ) -> Result[JsonDict]:
        if self.dry_run:
            return Result.success(self._stub_response())

        if not image_paths:
            return Result.failure("no_images_provided")

        # Send up to 3 images: front, back, tag — most informative for extraction
        # Keeping at 3 ensures we stay within context window at num_ctx 8192
        images_b64 = []
        for p in image_paths[:3]:
            try:
                images_b64.append(_encode_image(p))
            except Exception as e:
                return Result.failure(f"image_encode_failed: {p}: {e}")

        messages = [
            {
                "role": "user",
                "content": prompt,
                "images": images_b64,
            }
        ]

        payload = {
            "model": self._model_id,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": max_tokens,
                "num_ctx": 8192,
                "repeat_penalty": 1.15,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                raw_text = data.get("message", {}).get("content", "")
                print("=== RAW OUTPUT ===")
                print(raw_text[:3000])
                print("=== END ===")
        except httpx.HTTPStatusError as e:
            return Result.failure(f"ollama_http_error: {e.response.status_code}")
        except httpx.RequestError as e:
            return Result.failure(f"ollama_connection_error: {e}")

        parsed = _extract_json(raw_text)
        if parsed is None:
            return Result.failure(
                "json_parse_failed",
                raw_response=raw_text[:500],
            )

        return Result.success(parsed)

    def _stub_response(self) -> JsonDict:
        return {
            "title_raw": "Test Item (dry run)",
            "title_final": "Test Item Dry Run",
            "category_key": "clothing",
            "brand": "TestBrand",
            "brand_normalized": "testbrand",
            "type": "jacket",
            "condition_label": "Pre-owned - Good",
            "condition_id": "5000",
            "condition_notes": "Good condition, minor wear.",
            "defects": [],
            "color": "Blue",
            "size": "M",
            "department": "Women",
            "estimated_price": 15.00,
            "list_price": 18.00,
            "confidence_score": 0.95,
            "needs_review": False,
            "review_reasons": [],
            "description_final": "Test item generated in dry run mode.",
            "measurements": {},
            "features": [],
            "item_specifics": {},
        }
