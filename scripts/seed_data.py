"""Load data/processed/*.csv into Neo4j: constraints and indexes first, then nodes, then relationships.

Usage (from the project root, with Neo4j running, for example with "make docker-run"):
    python -m scripts.seed_data            # load (safe to run again: MERGE never creates duplicates)
    python -m scripts.seed_data --reset    # delete everything first, then load

Connection settings come from .env (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD).
"""

import argparse
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.database.neo4j_client import Neo4jClient

DATA_DIR = Path("data/processed")
BATCH_SIZE = 10_000

SCHEMA = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT recipe_id IF NOT EXISTS FOR (r:Recipe) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT ingredient_name IF NOT EXISTS FOR (i:Ingredient) REQUIRE i.name IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:IngredientCategory) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT cuisine_name IF NOT EXISTS FOR (c:Cuisine) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT diet_name IF NOT EXISTS FOR (d:Diet) REQUIRE d.name IS UNIQUE",
    "CREATE CONSTRAINT course_name IF NOT EXISTS FOR (c:Course) REQUIRE c.name IS UNIQUE",
    "CREATE RANGE INDEX recipe_minutes IF NOT EXISTS FOR (r:Recipe) ON (r.minutes)",
    "CREATE RANGE INDEX recipe_calories IF NOT EXISTS FOR (r:Recipe) ON (r.calories)",
    "CREATE FULLTEXT INDEX recipe_name_fulltext IF NOT EXISTS FOR (r:Recipe) ON EACH [r.name]",
    "CREATE FULLTEXT INDEX ingredient_name_fulltext IF NOT EXISTS FOR (i:Ingredient) ON EACH [i.name]",
]

# Each step: (description, csv file, Cypher query run once per batch of rows given as $rows).
LOAD_STEPS = [
    (
        "User nodes",
        "users.csv",
        "UNWIND $rows AS row MERGE (:User {id: row.id})",
    ),
    (
        "Recipe nodes",
        "recipes.csv",
        """
        UNWIND $rows AS row
        MERGE (r:Recipe {id: row.id})
        SET r.name = row.name, r.minutes = row.minutes, r.n_steps = row.n_steps,
            r.n_ingredients = row.n_ingredients, r.submitted = date(row.submitted),
            r.description = row.description, r.steps = row.steps,
            r.calories = row.calories, r.total_fat_pdv = row.total_fat_pdv, r.sugar_pdv = row.sugar_pdv,
            r.sodium_pdv = row.sodium_pdv, r.protein_pdv = row.protein_pdv,
            r.saturated_fat_pdv = row.saturated_fat_pdv, r.carbs_pdv = row.carbs_pdv
        """,
    ),
    (
        "Ingredient nodes",
        "ingredients.csv",
        "UNWIND $rows AS row MERGE (i:Ingredient {name: row.ingredient}) SET i.recipe_count = row.recipe_count",
    ),
    (
        "Cuisine nodes",
        "cuisines.csv",
        "UNWIND $rows AS row MERGE (:Cuisine {name: row.name})",
    ),
    (
        "Cuisine PART_OF",
        "cuisines.csv",
        """
        UNWIND $rows AS row
        WITH row WHERE row.parent IS NOT NULL
        MATCH (child:Cuisine {name: row.name}), (parent:Cuisine {name: row.parent})
        MERGE (child)-[:PART_OF]->(parent)
        """,
    ),
    (
        "SUBMITTED",
        "submitted.csv",
        """
        UNWIND $rows AS row
        MATCH (u:User {id: row.user_id}), (r:Recipe {id: row.recipe_id})
        MERGE (u)-[:SUBMITTED]->(r)
        """,
    ),
    (
        "REVIEWED",
        "reviewed.csv",
        """
        UNWIND $rows AS row
        MATCH (u:User {id: row.user_id}), (r:Recipe {id: row.recipe_id})
        MERGE (u)-[rev:REVIEWED]->(r)
        SET rev.rating = row.rating, rev.date = date(row.date)
        """,
    ),
    (
        "CONTAINS",
        "contains.csv",
        """
        UNWIND $rows AS row
        MATCH (r:Recipe {id: row.recipe_id}), (i:Ingredient {name: row.ingredient})
        MERGE (r)-[:CONTAINS]->(i)
        """,
    ),
    (
        "HAS_CUISINE",
        "has_cuisine.csv",
        """
        UNWIND $rows AS row
        MATCH (r:Recipe {id: row.recipe_id}), (c:Cuisine {name: row.cuisine})
        MERGE (r)-[:HAS_CUISINE]->(c)
        """,
    ),
    (
        "FITS_DIET (+ Diet nodes)",
        "fits_diet.csv",
        """
        UNWIND $rows AS row
        MATCH (r:Recipe {id: row.recipe_id})
        MERGE (d:Diet {name: row.diet})
        MERGE (r)-[:FITS_DIET]->(d)
        """,
    ),
    (
        "SERVED_AS (+ Course nodes)",
        "served_as.csv",
        """
        UNWIND $rows AS row
        MATCH (r:Recipe {id: row.recipe_id})
        MERGE (c:Course {name: row.course})
        MERGE (r)-[:SERVED_AS]->(c)
        """,
    ),
]


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read a processed CSV into a list of dicts, with missing values as None (null in Neo4j).

    Only empty cells count as missing: by default pandas would also turn text such as "NA" or "null" into NaN.
    """
    table = pd.read_csv(path, keep_default_na=False, na_values=[""])
    # A column of whole numbers with empty cells is read as floats (30.0); convert_dtypes turns it back into integers.
    table = table.convert_dtypes()
    if "steps" in table.columns:
        table["steps"] = table["steps"].map(json.loads)
    table = table.astype(object).where(table.notna(), None)
    return table.to_dict("records")


def batches(rows: list[dict[str, Any]], size: int = BATCH_SIZE) -> Iterator[list[dict[str, Any]]]:
    """Cut a list of rows into consecutive batches of at most `size` rows."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def reset_database(db: Neo4jClient) -> None:
    """Delete every node and relationship, in batches so one transaction never gets too big."""
    while True:
        deleted = db.run_write("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS deleted")
        if deleted[0]["deleted"] == 0:
            return


def print_counts(db: Neo4jClient) -> None:
    """Print how many nodes of each label and relationships of each type are in the database."""
    labels = db.run_read("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS total ORDER BY label")
    types = db.run_read("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS total ORDER BY type")
    for row in labels:
        print(f"  {'(:' + row['label'] + ')':<28} {row['total']:>9,}")
    for row in types:
        print(f"  {'[:' + row['type'] + ']':<28} {row['total']:>9,}")


def main() -> None:
    """Create the schema, then load every processed file into Neo4j."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete all existing data before loading")
    args = parser.parse_args()

    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        if not db.is_alive():
            raise SystemExit(f"Cannot reach Neo4j at {settings.neo4j_uri}. Is it running? (make docker-run)")
        if args.reset:
            print("deleting existing data...")
            reset_database(db)

        print("constraints and indexes...")
        for statement in SCHEMA:
            db.run_write(statement)
        db.run_write("CALL db.awaitIndexes(300)")

        for description, filename, query in LOAD_STEPS:
            start = time.perf_counter()
            rows = read_rows(DATA_DIR / filename)
            for batch in batches(rows):
                db.run_write(query, {"rows": batch})
            print(f"  {description:<28} {len(rows):>9,} rows  {time.perf_counter() - start:6.1f} s")

        print("in the database now:")
        print_counts(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
