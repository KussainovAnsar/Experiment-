# LessonLens — developer shortcuts. Run from the repository root.
#
#   make db-up      start the local PostgreSQL 16 cluster
#   make migrate    alembic upgrade head (dev DB)
#   make seed       idempotent demo seed (make seed-reset to wipe and rebuild the demo centre)
#   make api        FastAPI on :8000 (reload)
#   make worker     job worker (PIPELINE_MODE=worker)
#   make web        Vite dev server on :5173 (proxies /api -> :8000)
#   make test       backend pytest (Postgres lessonlens_test) + frontend vitest
#   make e2e        Playwright browser smoke (needs `make api` and `make web` running + seeded DB)
#   make lint       ruff + tsc

SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
ALEMBIC := $(VENV)/bin/alembic
RUFF := $(VENV)/bin/ruff
UVICORN := $(VENV)/bin/uvicorn

API_PORT ?= 8000
WEB_PORT ?= 5173
SEED_ARGS ?=
TEST_DATABASE_URL_SYNC ?= postgresql+psycopg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test
# Preinstalled Playwright browsers (CI image); falls back to Playwright's own cache when the dir is absent.
PW_BROWSERS ?= $(wildcard /opt/pw-browsers)
PW_ENV := $(if $(PW_BROWSERS),PLAYWRIGHT_BROWSERS_PATH=$(PW_BROWSERS),)

.PHONY: help db-up venv web-install migrate migrate-test seed seed-reset api worker web build test test-backend test-frontend e2e lint

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'

db-up:
	@pg_ctlcluster 16 main start 2>/dev/null || sudo pg_ctlcluster 16 main start || true
	@pg_lsclusters

venv:
	@test -x $(PY) || python3 -m venv $(VENV)
	$(PIP) install -q -r $(BACKEND)/requirements.txt

web-install:
	cd $(FRONTEND) && npm install

migrate:
	cd $(BACKEND) && $(abspath $(ALEMBIC)) upgrade head

migrate-test:
	cd $(BACKEND) && DATABASE_URL_SYNC=$(TEST_DATABASE_URL_SYNC) $(abspath $(ALEMBIC)) upgrade head

seed: migrate
	cd $(BACKEND) && $(abspath $(PY)) -m app.seed_demo $(SEED_ARGS)

seed-reset:
	$(MAKE) seed SEED_ARGS="--reset $(SEED_ARGS)"

api:
	cd $(BACKEND) && $(abspath $(UVICORN)) app.main:app --reload --port $(API_PORT)

worker:
	cd $(BACKEND) && PIPELINE_MODE=worker $(abspath $(PY)) -m app.worker

web:
	cd $(FRONTEND) && npm run dev -- --port $(WEB_PORT)

build:
	cd $(FRONTEND) && npm run build

test: test-backend test-frontend

test-backend:
	cd $(BACKEND) && $(abspath $(PY)) -m pytest -q

test-frontend:
	cd $(FRONTEND) && npm test

e2e:
	cd $(BACKEND) && E2E=1 $(PW_ENV) E2E_BASE_URL=http://127.0.0.1:$(WEB_PORT) $(abspath $(PY)) -m pytest tests/e2e -q

lint:
	cd $(BACKEND) && $(abspath $(RUFF)) check app tests
	cd $(FRONTEND) && npm run typecheck
