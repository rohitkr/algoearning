# One entry point for everything. `make help` lists the targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help setup dev dev-fresh dev-api dev-web up down db-up db-down db-status migrate migration test test-py test-js lint lint-py lint-js typecheck format types build check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install all JS and Python dependencies
	pnpm install
	uv sync --all-packages

dev: ## Start everything locally (DB, migrations, API :8000, web :3000): scripts/dev.sh
	@scripts/dev.sh

dev-fresh: ## Same, but first stop whatever holds ports 3000/8000
	@scripts/dev.sh --kill-ports

dev-api: ## Run only the API with reload (reads .env)
	uv run --env-file .env uvicorn ae_api.main:app --reload --port 8000

dev-web: ## Run only the web app
	pnpm --filter @algoearning/web dev

db-up: ## Start local Postgres + Redis without Docker (Homebrew; data in .data/)
	scripts/local-db.sh up

db-down: ## Stop local Postgres + Redis
	scripts/local-db.sh down

db-status:
	scripts/local-db.sh status

ALEMBIC := uv run alembic -c packages/py-db/alembic.ini
migrate: ## Apply database migrations (DATABASE_URL, default: local dev database)
	DATABASE_URL=$${DATABASE_URL:-postgresql://algoearning:algoearning@localhost:5432/algoearning} $(ALEMBIC) upgrade head

migration: ## Create a migration from model changes: make migration m="add foo"
	DATABASE_URL=$${DATABASE_URL:-postgresql://algoearning:algoearning@localhost:5432/algoearning} $(ALEMBIC) revision --autogenerate -m "$(m)"

up: ## Start the full stack in Docker (web, api, engine, worker, Postgres, Redis)
	docker compose -f infra/docker/compose.yml up --build

down: ## Stop the Docker stack
	docker compose -f infra/docker/compose.yml down

test: test-py test-js ## All tests
test-py:
	uv run pytest
test-js:
	pnpm test

lint: lint-py lint-js ## All linters and format checks
lint-py:
	uv run ruff check .
	uv run ruff format --check .
lint-js:
	pnpm lint
	pnpm format:check
	uv run python scripts/check-contrast.py

typecheck: ## mypy (strict) + tsc
	uv run mypy apps/api/src apps/engine/src apps/worker/src packages/py-*/src
	pnpm typecheck

format: ## Auto-format Python and JS
	uv run ruff format .
	uv run ruff check --fix .
	pnpm format

types: ## Regenerate web API types from the FastAPI OpenAPI spec
	pnpm --filter @algoearning/api-types generate

build: ## Production build of the web app
	pnpm build

check: lint typecheck test ## What CI runs
