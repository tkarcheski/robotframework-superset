# robotframework-superset — task runner for the local observability stack.
#
# Bring the stack up, bootstrap the dashboard, and inspect/clean the data:
#   make up          # start Postgres + Redis + Superset (runs bootstrap on init)
#   make bootstrap   # (re)create datasets/charts/dashboard in a running stack
#   make diagnose    # check env -> connection -> schema -> data -> Superset
#   make cache-flush # clear Superset's Redis cache so new data shows immediately
#   make sanitize    # truncate the events table (keeps dashboards/charts)
#   make down        # stop the stack

COMPOSE := docker compose -f infra/docker-compose.yml
PYTHON  ?= python

# Load .env (if present) so host-side targets get DATABASE_URL et al.
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: up down bootstrap diagnose cache-flush sanitize logs ps

## up: build and start Postgres + Redis + Superset (detached).
up:
	$(COMPOSE) up -d --build

## down: stop the stack (volumes are preserved).
down:
	$(COMPOSE) down

## bootstrap: create datasets, charts, and the dashboard in the running stack.
bootstrap:
	$(COMPOSE) exec -T superset python /app/bootstrap_dashboards.py

## diagnose: check the env -> connection -> schema -> data -> Superset chain.
diagnose:
	$(PYTHON) infra/scripts/diagnose_superset_db.py

## cache-flush: clear Superset's Redis cache (data + filter caches).
cache-flush:
	$(COMPOSE) exec -T redis redis-cli FLUSHALL

## sanitize: truncate the events table; pass ARGS="--yes" to skip the prompt.
sanitize:
	$(PYTHON) infra/scripts/sanitize_superset_db.py $(ARGS)

## logs: follow logs for all services.
logs:
	$(COMPOSE) logs -f

## ps: show service status.
ps:
	$(COMPOSE) ps
