# Recipe & Ingredient Explorer

A graph-backed API to explore recipes and ingredients, built with **Neo4j 5 + Graph Data Science**, **FastAPI**, **Nginx** and **Docker Compose** (Python 3.11).

> Status: project skeleton. Only the `/health` endpoint exists so far.

## Architecture

```
Browser ──▶ Nginx (localhost:8080) ──▶ FastAPI (api:8000) ──▶ Neo4j + GDS (neo4j:7687)
```

Only Nginx is exposed to the outside. Neo4j's ports (7474 for the browser, 7687 for Bolt) are also exposed for development.

## Prerequisites

- Python 3.11
- Docker Desktop
- GNU make. On Windows: `winget install ezwinports.make`

## Setup

1. Clone the repository.
2. Create your settings file and fill in your own values:
   ```
   cp .env.example .env
   ```
   `NEO4J_PASSWORD` must be at least 8 characters (letters, digits and dashes only). Keep `NEO4J_USER=neo4j`.
3. Download the [Food.com dataset](https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions) and put `RAW_recipes.csv` and `RAW_interactions.csv` in `data/raw/`.
4. Install the development environment:
   ```
   make install
   ```
5. Clean the raw data (writes `data/processed/`):
   ```
   make prepare-data
   ```
6. Start the stack (see below), then load the data into Neo4j (about 3 minutes):
   ```
   make seed
   ```

## Run with Docker

```
make docker-run      # start Neo4j, the API and Nginx
make docker-down     # stop everything (data is kept)
```

- API health: http://localhost:8080/health → `{"status":"ok","neo4j":"up"}`
- API docs: http://localhost:8080/docs
- Neo4j Browser: http://localhost:7474 (user `neo4j`, password from `.env`)

## Screenshots

Recipes containing basil in the Neo4j Browser:

![Recipes containing basil in the Neo4j Browser](docs/screenshots/neo4j_graph.png)

PROFILE of a two-ingredient search: Neo4j starts from the indexed ingredient nodes (NodeUniqueIndexSeek) instead of scanning every recipe, which costs about 20k db hits instead of 1.1M (see docs/benchmarks.md):

![PROFILE plan of the egg and parmesan query](docs/screenshots/profile_index.png)


## Development

```
make help      # list all commands
make run       # run the API locally on http://127.0.0.1:8000
make prepare-data  # clean the raw CSVs into data/processed
make seed      # load data/processed into Neo4j (Neo4j must be running)
make test      # run the tests with coverage
make lint      # pylint
make format    # black
```

## Project structure

```
app/
├── main.py              # FastAPI app and startup/shutdown
├── config.py            # settings read from .env
├── database/
│   └── neo4j_client.py  # Neo4j driver wrapper
├── models/              # Pydantic models
└── routers/
    └── health.py        # GET /health
nginx/nginx.conf         # reverse proxy configuration
scripts/                 # data loading scripts
tests/                   # pytest tests
```
