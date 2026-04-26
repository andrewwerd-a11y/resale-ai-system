"""
export_openapi.py — write the FastAPI app's OpenAPI schema to docs/openapi.json.

Run with:
    uv run python scripts/export_openapi.py

The generated file is the source of truth for what HTTP endpoints exist —
regenerate after adding or changing a route. Commit the file so collaborators
can read the API surface without running the server.

Compatible viewers:
  - Browser via FastAPI's runtime /docs (Swagger) and /redoc
  - Insomnia / Postman / Bruno — import as OpenAPI 3
  - VS Code with the OpenAPI (Swagger) Editor extension
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def export_openapi(output_path: Path | None = None) -> Path:
    """Generate docs/openapi.json from the FastAPI app's OpenAPI schema."""
    # Import after sys.path is set so the FastAPI app can resolve packages/
    from apps.api.src.main import app  # noqa: E402

    if output_path is None:
        output_path = ROOT / "docs" / "openapi.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()
    output_path.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    routes = schema.get("paths", {})
    operation_count = sum(
        1
        for methods in routes.values()
        for method in methods
        if method in ("get", "post", "put", "patch", "delete")
    )
    print(
        f"OpenAPI schema written to {output_path.relative_to(ROOT)}\n"
        f"  Paths: {len(routes)}\n"
        f"  Operations: {operation_count}\n"
        f"  Title: {schema.get('info', {}).get('title')}\n"
        f"  Version: {schema.get('info', {}).get('version')}"
    )
    return output_path


if __name__ == "__main__":
    out: Path | None = None
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        out = Path(args[0])
    export_openapi(out)
