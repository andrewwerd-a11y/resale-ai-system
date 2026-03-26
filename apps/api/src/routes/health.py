from fastapi import APIRouter
from packages.vision.src.ollama_provider import OllamaProvider

router = APIRouter()

@router.get("/health")
def health():
    provider = OllamaProvider()
    return {
        "status": "ok",
        "ollama": provider.is_available(),
        "model": provider.model_id,
    }
