SHELL := /bin/bash
PY ?= python3
COMPOSE ?= docker compose

.PHONY: help setup test coverage backtest hyperopt data dryrun live stop logs report clean

help:
	@echo "Santinela — targets:"
	@echo "  make setup      bootstrap .env, runtime files, install dev deps"
	@echo "  make test       run the full pytest suite (required before dryrun/live)"
	@echo "  make coverage   pytest with branch coverage for riskguard & co."
	@echo "  make data       download OHLCV history for backtesting"
	@echo "  make backtest   multi-regime backtest suite + lookahead/recursive analysis"
	@echo "  make hyperopt   bounded hyperopt with walk-forward validation"
	@echo "  make dryrun     start the stack in dry-run (paper) mode  [default]"
	@echo "  make live       go-live gate — interactive, multi-step, refuses on any miss"
	@echo "  make report     generate today's daily report on demand"
	@echo "  make stop       stop the stack"
	@echo "  make logs       tail service logs"

setup:
	@test -f .env || (cp .env.example .env && echo ">> created .env — fill in your keys")
	@test -f user_data/runtime/overrides.json || cp user_data/runtime/overrides.example.json user_data/runtime/overrides.json
	@mkdir -p journal reports user_data/data user_data/riskguard_state
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest -q

coverage:
	$(PY) -m pytest --cov=riskguard --cov=strategist --cov=orchestrator \
		--cov-branch --cov-report=term-missing

data:
	bash scripts/download_data.sh

backtest:
	bash scripts/backtest_suite.sh

hyperopt:
	bash scripts/hyperopt_suite.sh

# Dry-run is the default mode. Tests MUST pass first (S-rules regression gate).
dryrun: test
	@test -f .env || (echo "ERROR: .env missing — run 'make setup' first" && exit 1)
	@test -f user_data/runtime/overrides.json || cp user_data/runtime/overrides.example.json user_data/runtime/overrides.json
	@grep -q '^TRADING_MODE=live' .env && \
		echo "ERROR: TRADING_MODE=live in .env — use 'make live' (go-live gate) instead" && exit 1 || true
	$(COMPOSE) up -d --build
	@echo ">> dry-run stack started. FreqUI: http://127.0.0.1:8080  |  make logs"

# Live mode is a deliberate, gated, multi-step action (S11).
live: test
	@set -a; [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/go_live_gate.py && \
	$(COMPOSE) -f docker-compose.yml -f docker-compose.live.yml up -d --build && \
	echo ">> LIVE stack started. Monitor it. You can stop entries any time with /stopentry."

report:
	$(COMPOSE) exec brain python -m strategist.daily_report || \
		$(PY) -m strategist.daily_report

stop:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

clean:
	rm -rf .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} + 2>/dev/null || true
