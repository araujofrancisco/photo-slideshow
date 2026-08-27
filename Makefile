# Photo Slideshow — local development helpers
#
# Usage:
#   make install     # install Python + Node dependencies (run once)
#   make build-ui    # build the Astro/React frontend into frontend/dist
#   make run         # start the FastAPI server (serves API + built UI)
#   make dev         # build-ui, then run (one command to get going)
#   make lint        # check code with ruff
#   make format      # auto-fix with ruff
#
# The backend serves the UI from web/static if present, else frontend/dist,
# so a plain `npm run build` is enough for local development (see web/app.py).

PYTHON  ?= $(or $(wildcard .venv/bin/python3),python3)
UVICORN := $(PYTHON) -m uvicorn
HOST    ?= 127.0.0.1
PORT    ?= 28000

.PHONY: install build-ui run dev lint format

install:
	$(PYTHON) -m pip install -r requirements.txt
	cd frontend && npm ci

build-ui:
	cd frontend && npm ci && npm run build

run:
	$(UVICORN) web.app:app --host $(HOST) --port $(PORT)

dev: build-ui run

lint:
	$(PYTHON) -m ruff check slideshow/ web/
	$(PYTHON) -m ruff format --check slideshow/ web/

format:
	$(PYTHON) -m ruff check --fix slideshow/ web/
	$(PYTHON) -m ruff format slideshow/ web/
