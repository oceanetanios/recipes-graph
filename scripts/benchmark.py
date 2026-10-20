"""Measure how much the indexes help: each query runs with the index and forced to scan (USING SCAN).

Usage (from the project root, with Neo4j running and loaded):
    python -m scripts.benchmark

For each query it prints the database hits (the work Neo4j did, from PROFILE), the median time
over several runs, and the operator that starts the plan (NodeIndexSeek, NodeByLabelScan...).
"""

import statistics
import time
from typing import Any

from app.config import settings
from app.database.neo4j_client import Neo4jClient

RUNS = 7

# (name, query using the index, same query forced to scan, parameters)
BENCHMARKS = [
    (
        "ingredient by name (unique constraint)",
        "MATCH (i:Ingredient {name: $name}) RETURN i.recipe_count AS n",
        "MATCH (i:Ingredient) USING SCAN i:Ingredient WHERE i.name = $name RETURN i.recipe_count AS n",
        {"name": "basil"},
    ),
    (
        "recipe by id (unique constraint)",
        "MATCH (r:Recipe {id: $id}) RETURN r.name AS name",
        "MATCH (r:Recipe) USING SCAN r:Recipe WHERE r.id = $id RETURN r.name AS name",
        {"id": 137739},
    ),
    (
        "recipes under 10 minutes (range index)",
        "MATCH (r:Recipe) WHERE r.minutes < $max RETURN count(r) AS n",
        "MATCH (r:Recipe) USING SCAN r:Recipe WHERE r.minutes < $max RETURN count(r) AS n",
        {"max": 10},
    ),
    (
        "low-calorie recipes (range index)",
        "MATCH (r:Recipe) WHERE r.calories < $max RETURN count(r) AS n",
        "MATCH (r:Recipe) USING SCAN r:Recipe WHERE r.calories < $max RETURN count(r) AS n",
        {"max": 50},
    ),
    (
        "recipe name search (full-text index)",
        "CALL db.index.fulltext.queryNodes('recipe_name_fulltext', $words) YIELD node "
        "RETURN node.name AS name LIMIT 10",
        "MATCH (r:Recipe) WHERE r.name CONTAINS $words RETURN r.name AS name LIMIT 10",
        {"words": "lemon chicken"},
    ),
    (
        "recipes with egg and parmesan (index anchor)",
        "MATCH (:Ingredient {name: $a})<-[:CONTAINS]-(r:Recipe)-[:CONTAINS]->(:Ingredient {name: $b}) "
        "RETURN count(r) AS n",
        "MATCH (r:Recipe) USING SCAN r:Recipe "
        "MATCH (:Ingredient {name: $a})<-[:CONTAINS]-(r)-[:CONTAINS]->(:Ingredient {name: $b}) "
        "RETURN count(r) AS n",
        {"a": "egg", "b": "parmesan cheese"},
    ),
]


def total_db_hits(plan: dict[str, Any]) -> int:
    """Add up the database hits of an operator and of all the operators below it."""
    return plan.get("dbHits", 0) + sum(total_db_hits(child) for child in plan.get("children", []))


def leaf_operators(plan: dict[str, Any]) -> list[str]:
    """Return the operators at the bottom of the plan: they show how Neo4j finds its first nodes."""
    children = plan.get("children", [])
    if not children:
        return [plan["operatorType"].split("@")[0]]
    return [name for child in children for name in leaf_operators(child)]


def median_ms(db: Neo4jClient, query: str, params: dict[str, Any]) -> float:
    """Run the query several times (the first run warms the cache) and return the median time in ms."""
    db.run_read(query, params)
    timings = []
    for _ in range(RUNS):
        start = time.perf_counter()
        db.run_read(query, params)
        timings.append((time.perf_counter() - start) * 1000)
    return statistics.median(timings)


def main() -> None:
    """Run every benchmark with and without its index and print a comparison table."""
    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        if not db.is_alive():
            raise SystemExit(f"Cannot reach Neo4j at {settings.neo4j_uri}. Is it running? (make docker-run)")
        print(f"{'query':<46} {'version':<8} {'db hits':>10} {'median ms':>10}  first operator")
        for name, indexed, scan, params in BENCHMARKS:
            for version, query in (("index", indexed), ("scan", scan)):
                plan = db.profile(query, params)
                print(
                    f"{name:<46} {version:<8} {total_db_hits(plan):>10,} {median_ms(db, query, params):>10.1f}  "
                    f"{', '.join(sorted(set(leaf_operators(plan))))}"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
