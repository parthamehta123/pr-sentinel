.DEFAULT_GOAL := help
VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(VENV): ## Create the virtualenv
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip

install: $(VENV) ## Install the package and dev extras
	$(PIP) install -q -e ".[dev,langgraph]"

up: ## Start Postgres + Redis
	docker compose up -d
	@$(PY) scripts/wait_for_services.py

down: ## Stop containers (keeps volumes)
	docker compose down

nuke: ## Stop containers AND delete all data
	docker compose down -v

migrate: ## Apply SQL migrations
	$(PY) -m pr_sentinel.cli migrate

doctor: ## Verify config, extensions, schema and embedding dimension agree
	$(PY) -m pr_sentinel.cli doctor

api: ## Run the webhook ingress (uvicorn, reload)
	$(VENV)/bin/uvicorn pr_sentinel.ingress.app:app --host 0.0.0.0 --port 8080 --reload

worker: ## Run the ARQ review worker
	$(VENV)/bin/arq pr_sentinel.queue.worker.WorkerSettings

dashboard: ## Run the HITL queue + cost dashboard
	$(VENV)/bin/uvicorn pr_sentinel.dashboard.app:app --host 0.0.0.0 --port 8081 --reload

test: ## Unit tests (no services required)
	$(VENV)/bin/pytest tests/unit -q

test-integration: ## Integration tests (needs `make up migrate`)
	$(VENV)/bin/pytest tests/integration -q -m integration

lint: ## Ruff + mypy
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests
	$(VENV)/bin/mypy src

fmt: ## Autoformat
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

demo: ## End-to-end proof: signed webhook -> agents -> gate -> decision
	$(PY) scripts/demo_end_to_end.py

fixtures: ## Rebuild the golden set from tests/eval/cases.py
	$(PY) scripts/build_eval_fixtures.py

eval: ## Score the panel offline (echo provider — measures the harness, not quality)
	$(PY) -m pr_sentinel.cli eval --provider echo --baseline tests/eval/baselines/echo.json

eval-live: ## Score the panel against real models. Needs ANTHROPIC_API_KEY. Costs money.
	@test -n "$$ANTHROPIC_API_KEY" || { echo "ANTHROPIC_API_KEY is not set"; exit 1; }
	$(PY) -m pr_sentinel.cli eval --provider anthropic --concurrency 3 \
	  --save tests/eval/baselines/anthropic.json --verbose

eval-baseline: ## Re-record the offline baseline after an intentional change
	$(PY) -m pr_sentinel.cli eval --provider echo --save tests/eval/baselines/echo.json

.PHONY: help install up down nuke migrate doctor api worker dashboard test test-integration \
        lint fmt demo fixtures eval eval-live eval-baseline
