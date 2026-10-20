# Makefile for the Recipe & Ingredient Explorer.
# Short commands for the long ones. Type "make help" to see them all.

# --- Settings that depend on the operating system ---
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3.11
VENV_PY := .venv\Scripts\python.exe
else
PYTHON ?= python3.11
VENV_PY := .venv/bin/python
endif

# "make" alone shows the help.
.DEFAULT_GOAL := help

# These names are commands, not files.
.PHONY: help venv install run prepare-data seed gds ml benchmark evaluate-agent docker-build docker-run docker-down logs test lint format clean

help: ## Show this list of commands
	@$(PYTHON) -c "import re; [print(f'  make {name:<14} {desc}') for name, desc in re.findall(r'^([a-z-]+):.*?## (.*)$$', open('Makefile').read(), re.M)]"

venv: ## Create the virtual environment in .venv (if it does not exist yet)
	$(PYTHON) -c "import os, venv; os.path.isdir('.venv') or venv.create('.venv', with_pip=True)"

install: venv ## Install all runtime and development libraries into .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements-dev.txt

run: ## Start the API locally with auto-reload on http://127.0.0.1:8000
	$(VENV_PY) -m uvicorn app.main:app --reload

prepare-data: ## Clean the raw CSVs into data/processed (needs data/raw)
	$(VENV_PY) -m scripts.clean_data

seed: ## Load data/processed into Neo4j (Neo4j must be running: make docker-run)
	$(VENV_PY) -m scripts.seed_data

gds: ## Run the graph algorithms (GDS) and write their results (after make seed, about 3 minutes)
	$(VENV_PY) -m scripts.graph_algorithms

ml: ## ML workflow: predict vegetarian recipes from FastRP embeddings, writes docs/ml.md
	$(VENV_PY) -m scripts.ml_diet

benchmark: ## Compare queries with and without their index (PROFILE db hits + timings)
	$(VENV_PY) -m scripts.benchmark

evaluate-agent: ## Ask the AI agent 13 test questions and write docs/agent_evaluation.md (uses Gemini quota)
	$(VENV_PY) -m scripts.evaluate_agent

docker-build: ## Build the Docker images
	docker compose build

docker-run: ## Build and start Neo4j, the API and Nginx in the background
	docker compose up --build -d

docker-down: ## Stop and remove the containers (the database data is kept)
	docker compose down

logs: ## Follow the logs of all containers (Ctrl+C to stop)
	docker compose logs -f

test: ## Run the tests with a coverage report
	$(VENV_PY) -m pytest --cov=app --cov-report=term-missing

lint: ## Check code quality with pylint
	$(VENV_PY) -m pylint app scripts tests

format: ## Format the code with black (line length 120)
	$(VENV_PY) -m black -l 120 app scripts tests

clean: ## Delete caches and coverage files (keeps .venv)
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in [*pathlib.Path('.').rglob('__pycache__'), pathlib.Path('.pytest_cache'), pathlib.Path('htmlcov')] if '.venv' not in p.parts]; pathlib.Path('.coverage').unlink(missing_ok=True)"
