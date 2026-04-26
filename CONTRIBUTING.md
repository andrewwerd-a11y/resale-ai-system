# Contributing

Thanks for considering a contribution. This guide covers the bare essentials — for the deeper "how is this built" questions, read [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) first.

---

## Setup

You need:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Ollama (for vision pipeline) — optional if you set `DRY_RUN=true`

```bash
git clone https://github.com/andrewwerd-a11y/resale-ai-system.git
cd resale-ai-system
uv sync --all-extras
cp .env.example .env
# edit .env — at minimum set DRY_RUN=true if you don't have Ollama running
uv run alembic upgrade head      # creates data/app.db (or just start the server)
make dev                          # http://localhost:8000
```

For the worker:

```bash
make worker
```

---

## Day-to-day commands

| Task | Command |
|---|---|
| Start dev server | `make dev` |
| Run worker once | `make worker` |
| Run all tests | `make test` |
| Run unit tests only | `uv run pytest tests/unit/ -v` |
| Run integration tests only | `uv run pytest tests/integration/ -v` |
| Lint | `make lint` (runs ruff + mypy) |
| Format | `uv run ruff format .` |
| Regenerate OpenAPI | `make docs` |
| Backup DB | `uv run python scripts/backup_db.py` |

---

## Code conventions

### Style

- **Formatting:** Ruff handles formatting and most linting. Run `make lint` before submitting.
- **Line length:** 100 characters (configured in `pyproject.toml`).
- **Type hints:** Use them on public function signatures. Mypy is configured non-strict — tighten as you go.
- **Imports:** `from __future__ import annotations` at the top of any module that uses generics or unions. Ruff's `I` rules sort imports automatically.

### Patterns

These are documented in detail in `docs/ARCHITECTURE.md`. Quick summary:

- Cross-module calls return `Result[T]` instead of raising. Use `result.ok` / `result.error` / `result.value`.
- Behavior dials live in `config/*.json`, not in code. If your change adds a tunable threshold or a new category trigger, add it to a config file.
- New SQLite columns are added via raw `ALTER TABLE` in `migrate_add_columns()` (`packages/data/src/db/sqlite.py`). Append the column there and to the SQLModel class. Run the server once to apply.
- Routes in `apps/api/src/routes/` should be **thin** — delegate logic to a package.
- The pipeline is config-driven: adding a category should not require code changes (see `docs/ARCHITECTURE.md` § Extension points).

### Testing

- New features need at least a unit test. Integration tests for new pipeline stages.
- Tests use in-memory SQLite (`tests/conftest.py`) — they never touch `data/app.db`.
- Mock external services: see `tests/fixtures/mock_ebay.py` and `mock_extraction.py`.
- Don't commit tests that hit live external APIs.

---

## Pull request process

1. **Open an issue first** for non-trivial changes. We can discuss approach before code.
2. **Branch from `master`.** The `main` branch is currently empty/stale.
3. **One logical change per PR.** Easier to review, easier to revert.
4. **Update relevant docs:**
   - New architectural decision → update `docs/ARCHITECTURE.md`
   - New phase milestone or known-issue fix → update `docs/ROADMAP.md`
   - New or changed HTTP endpoint → run `make docs` and commit `docs/openapi.json`
   - User-facing setup change → update top-level `README.md`
5. **Pass `make lint` and `make test`.**
6. **Describe the change clearly** in the PR body. Include a "Why" section if the change isn't obvious from the diff.

---

## Architectural decisions worth knowing

Before proposing a major refactor, please read these sections of `docs/ARCHITECTURE.md`:

- **Design principles** — local-first, prefix-as-authority, manual-override-wins, idempotent upserts, Result[T] everywhere
- **Invariants** — properties the code maintains; violating them is a bug
- **Concurrency model** — single-writer assumption is currently part of the contract

These shape what kinds of changes are easy vs. hard. Working with the grain saves a lot of pain.

---

## Sensitive data

- **Never commit `.env`** — gitignored, but double-check.
- **Never commit `data/app.db`** or files in `data/exports/`, `data/imports/`, `data/logs/`, `data/category_intelligence/` — gitignored.
- **Never commit `data/ebay_tokens.json`** — gitignored. If you accidentally do, rotate eBay credentials and force-push remove.
- **Never commit photos in `intake/`** — gitignored.

---

## Questions

Open an issue. The codebase is small enough that "where do I add X?" can usually be answered with a quick exchange.
