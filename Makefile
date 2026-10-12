SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help up down restart logs ps rebuild migrate makemigration seed api-shell \
        test lint fmt gen-api fe-dev api-dev clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env: ## Create .env from template if missing
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

up: .env ## Build and start the full stack (api + postgres + redis + web + caddy)
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

restart: ## Restart the stack
	$(COMPOSE) restart

logs: ## Tail logs
	$(COMPOSE) logs -f --tail=200

ps: ## Show service status
	$(COMPOSE) ps

rebuild: ## Rebuild images without cache
	$(COMPOSE) build --no-cache

migrate: ## Apply DB migrations
	$(COMPOSE) exec api alembic upgrade head

makemigration: ## Autogenerate migration, usage: make makemigration m="add x"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

seed: ## Create default roles + bootstrap admin/user accounts
	$(COMPOSE) exec api python -m app.initial_data

api-shell: ## Shell into the api container
	$(COMPOSE) exec api bash

test: ## Run backend tests
	cd apps/api && pytest -q

lint: ## Ruff check + format check
	cd apps/api && ruff check . && ruff format --check .

fmt: ## Ruff autofix + format
	cd apps/api && ruff check --fix . && ruff format .

gen-api: ## Regenerate the typed API client from the live OpenAPI schema
	cd apps/web && npm run gen:api

fe-dev: ## Run Next.js dev server
	cd apps/web && npm run dev

api-dev: ## Run FastAPI dev server (local python)
	cd apps/api && uvicorn app.main:app --reload

clean: ## Remove containers, volumes and build artifacts
	$(COMPOSE) down -v
	rm -rf apps/web/.next apps/web/node_modules apps/api/.pytest_cache apps/api/.ruff_cache
