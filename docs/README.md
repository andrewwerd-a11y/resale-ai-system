# Docs

This folder contains the reference documentation for the Resale AI System. The top-level `README.md` covers user-facing setup and daily workflow; everything else lives here.

| File | Audience | When to read |
|---|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Contributors, AI tools | "How is this built?" "Where does X happen?" "What are the rules?" |
| [`ROADMAP.md`](./ROADMAP.md) | Everyone | "What's done?" "What's next?" "What are the known issues?" |
| [`openapi.json`](./openapi.json) | API consumers, integrators | The complete HTTP API surface — paths, parameters, response shapes |
| [`../README.md`](../README.md) | New users | First-time setup, daily workflow, troubleshooting |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | New contributors | How to set up the dev environment, conventions, PR process |

## Regenerating `openapi.json`

The OpenAPI file is a snapshot of the FastAPI app's route definitions. Regenerate after adding or modifying any route:

```powershell
make docs
# or
uv run python scripts/export_openapi.py
```

You can also browse the live spec at `http://localhost:8000/docs` (Swagger) or `http://localhost:8000/redoc` (ReDoc) when the server is running.

## Keeping docs current

- `ARCHITECTURE.md` should change when **architectural decisions** change (a new external service, a shift in the data model's invariants, a refactor of layer boundaries) — *not* on every route or column add.
- `ROADMAP.md` should change when **status or scope** changes — when a phase ships, when a known issue is fixed, when a new direction is committed to.
- `openapi.json` should be regenerated **on every API change** and committed alongside the code change.
