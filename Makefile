SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: help setup fetch-district report dev backend frontend stop test typecheck build clean

help:
	@echo "make setup           install backend + frontend dependencies"
	@echo "make fetch-district  one-time district extract from Overpass (~0.9 MB cached)"
	@echo "make report          print the coverage report for the cached district"
	@echo "make dev             run the API (:8000) and the web app (:5173); Ctrl+C stops both"
	@echo "make stop            kill servers left running by another terminal"
	@echo "make test            run backend tests and frontend typecheck"

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e "backend[dev]"

frontend/node_modules:
	npm --prefix frontend install

setup: $(VENV)/bin/python frontend/node_modules

fetch-district: $(VENV)/bin/python
	cd backend && ../$(PY) -m app.cli fetch-district $(ARGS)

report: $(VENV)/bin/python
	cd backend && ../$(PY) -m app.cli report

UVICORN := $(PY) -m uvicorn app.main:app --port 8000 --app-dir backend \
	--reload --reload-dir backend/app

backend: $(VENV)/bin/python
	$(UVICORN)

frontend: frontend/node_modules
	npm --prefix frontend run dev

# `kill 0` signals the whole process group, so Ctrl+C stops both servers.
dev: setup
	@trap 'kill 0' EXIT INT TERM; \
	$(UVICORN) & \
	npm --prefix frontend run dev & \
	wait

# For servers left running by another terminal, where Ctrl+C cannot reach them.
stop:
	@for port in 8000 5173; do \
		pids=$$(lsof -ti tcp:$$port 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "stopping port $$port (pid $$pids)"; kill $$pids 2>/dev/null || true; \
		else \
			echo "port $$port already free"; \
		fi; \
	done

test: setup
	cd backend && ../$(PY) -m pytest -q
	npm --prefix frontend run typecheck

typecheck: frontend/node_modules
	npm --prefix frontend run typecheck

build: frontend/node_modules
	npm --prefix frontend run build

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist backend/.pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
