from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from packages.data.src.db.sqlite import init_db
from apps.api.src.routes import health, items, review, export, ui, ebay

app = FastAPI(
    title="Resale AI System",
    description="Local-first resale automation with AI vision and eBay publishing",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(items.router, prefix="/api/items", tags=["items"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
app.include_router(ui.router, tags=["ui"])


@app.on_event("startup")
def on_startup() -> None:
    init_db()
