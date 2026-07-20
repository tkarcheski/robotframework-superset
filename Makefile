COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
PYTHON ?= python3

.PHONY: help up down bootstrap diagnose cache-flush sanitize logs ps test lint typecheck

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

.env: .env.example
	cp .env.example .env
	@echo "Created .env; replace placeholder credentials before shared deployment."

up: .env ## Start PostgreSQL, Redis, initialize Superset, and serve dashboards
	$(COMPOSE) up -d --build

down: .env ## Stop the local stack
	$(COMPOSE) down

bootstrap: .env ## Re-run the idempotent Superset dashboard bootstrap
	$(COMPOSE) run --rm superset-init

diagnose: .env ## Check environment, database, schema, data, and Superset health
	$(PYTHON) infra/scripts/diagnose_superset_db.py

cache-flush: .env ## Flush Redis so dashboards query fresh event data
	$(COMPOSE) exec redis redis-cli FLUSHALL

sanitize: .env ## Delete event rows while preserving Superset configuration (ARGS=--yes skips the prompt)
	$(PYTHON) infra/scripts/sanitize_superset_db.py $(ARGS)

logs: .env ## Follow logs for all services
	$(COMPOSE) logs -f

ps: .env ## Show service status
	$(COMPOSE) ps

test: ## Run unit tests
	pytest -q

lint: ## Run Ruff
	ruff check src tests infra

typecheck: ## Run strict type checking
	mypy src
