# ─── Resale AI System — Makefile ─────────────────────────────────────────────
# Requires: uv  (https://docs.astral.sh/uv/getting-started/installation/)
# On Windows, run these inside PowerShell or Git Bash.

.PHONY: install dev api worker migrate lint test clean help

help:
	@echo ""
	@echo "  install    Install all dependencies via uv"
	@echo "  dev        Start API + worker in dev mode"
	@echo "  api        Start FastAPI server only"
	@echo "  worker     Start background worker only"
	@echo "  migrate    Run database migrations"
	@echo "  lint       Run ruff + mypy"
	@echo "  test       Run test suite"
	@echo "  clean      Remove generated files (not intake folders)"
	@echo ""

install:
	uv sync --all-extras

dev:
	uv run uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000 --reload

api:
	uv run uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000

worker:
	uv run python apps/worker/src/main.py

migrate:
	uv run alembic upgrade head

lint:
	uv run ruff check .
	uv run mypy apps packages

test:
	uv run pytest tests/ -v --cov=packages --cov-report=term-missing

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
