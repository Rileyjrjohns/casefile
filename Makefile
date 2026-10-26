UV ?= uv
NODE ?= node
NPM ?= npm
RUN = $(UV) run --locked
ENV_RUN = $(RUN) $(if $(wildcard .env),--env-file .env)

.DEFAULT_GOAL := check
.PHONY: sync check lint format format-check typecheck architecture test test-unit test-postgres test-mcp demo-mcp demo-agent migrate eval

sync:
	$(UV) sync --locked --group training

check: lint format-check typecheck architecture test web-check

lint:
	$(RUN) ruff check .

format:
	$(RUN) ruff format .

format-check:
	$(RUN) ruff format --check .

typecheck:
	$(RUN) mypy

architecture:
	$(RUN) lint-imports

test:
	$(RUN) pytest --cov --cov-report=term-missing:skip-covered --cov-report=xml
	$(RUN) coverage report --include="src/casefile/features/*/domain/*" --fail-under=90 --skip-covered
	$(RUN) coverage report --include="src/casefile/features/*/application/*" --fail-under=90 --skip-covered

test-unit:
	$(RUN) pytest -m "not postgres"

test-postgres:
	$(RUN) pytest -m postgres

test-mcp:
	$(RUN) pytest -m mcp

PROVIDER ?= transactions
demo-mcp:
	@$(RUN) casefile-demo-mcp $(PROVIDER)

demo-agent:
	@$(RUN) casefile-investigate

migrate:
	$(RUN) alembic upgrade head

EVAL_MODE ?= replay
EVAL_OUTPUT ?= build/evals
eval:
	$(RUN) casefile-eval --mode $(EVAL_MODE) --output $(EVAL_OUTPUT)
	$(RUN) casefile-decisions --output $(EVAL_OUTPUT)

.PHONY: eval-decisions-live
DECISION_PROVIDERS ?= local jev llm
DECISION_MAX_COST ?= 1
DECISION_MAX_TOKENS ?= 2500000
eval-decisions-live:
	$(ENV_RUN) casefile-decisions --live --providers $(DECISION_PROVIDERS) --max-cost $(DECISION_MAX_COST) --max-tokens $(DECISION_MAX_TOKENS) --output $(EVAL_OUTPUT)

# Bills your Jev key: replaces evals/decision-replay/jev.json with the real answers, replayed by CI.
.PHONY: record-decisions-jev
record-decisions-jev:
	$(ENV_RUN) casefile-decisions --live --record --providers local jev --max-cost $(DECISION_MAX_COST) --max-tokens $(DECISION_MAX_TOKENS) --output $(EVAL_OUTPUT)

.PHONY: web-sync web-check serve mcp-inspect
web-sync:
	cd web && $(NPM) ci

web-check:
	$(NODE) web/node_modules/typescript/bin/tsc --project web/tsconfig.json --noEmit
	$(NODE) web/node_modules/vitest/vitest.mjs run --root web
	cd web && $(NODE) node_modules/vite/bin/vite.js build

serve:
	$(ENV_RUN) casefile-serve

mcp-inspect:
	cd web && $(NPM) exec --yes --package=@modelcontextprotocol/inspector@2.12.0 -- mcp-inspector $(UV) run --project .. --locked casefile-mcp

.PHONY: train-risk
train-risk:
	$(UV) run --locked --group training casefile-train-risk
