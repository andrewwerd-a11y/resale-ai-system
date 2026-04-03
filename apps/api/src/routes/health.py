from fastapi import APIRouter
from packages.core.src.config import get_settings
from packages.vision.src.ollama_provider import OllamaProvider

router = APIRouter()


@router.get("/health")
def health():
    settings = get_settings()
    provider = OllamaProvider()

    # Check DB connectivity
    db_ok = False
    try:
        from packages.data.src.db.sqlite import engine
        from sqlmodel import Session, text
        with Session(engine) as s:
            s.exec(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "ollama": provider.is_available(),
        "model": provider.model_id,
        "environment": settings.ebay_environment,
        "enrichment_enabled": settings.enrichment_enabled,
        "version": "0.6.0",
    }
