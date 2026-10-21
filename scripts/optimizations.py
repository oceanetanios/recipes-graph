"""Before/after benchmarks of query rewrites. Each pair must return the same rows; db hits from PROFILE.

Usage (from the project root, with Neo4j running and loaded):
    python -m scripts.optimizations
"""

# SEARCH_BEFORE below is on purpose a copy of the old search query (it is the "before" being measured).
# pylint: disable=duplicate-code

from app.config import settings
from app.database.neo4j_client import Neo4jClient
from app.services.recipes import SEARCH_QUERY
from scripts.benchmark import median_ms, total_db_hits

# The first version of the search query (step 19), kept to measure the rewrite now in app/services/recipes.py.
SEARCH_BEFORE = """
UNWIND $ingredients AS name
MATCH (r:Recipe)-[:CONTAINS]->(i:Ingredient {name: name})
WITH r, collect(i.name) AS matched
WHERE size(matched) >= $min_matched
  AND ($max_minutes IS NULL OR r.minutes <= $max_minutes)
  AND ($diet IS NULL OR EXISTS { (r)-[:FITS_DIET]->(:Diet {name: $diet}) })
  AND ($course IS NULL OR EXISTS { (r)-[:SERVED_AS]->(:Course {name: $course}) })
  AND ($cuisine IS NULL OR EXISTS { (r)-[:HAS_CUISINE]->(:Cuisine)-[:PART_OF*0..4]->(:Cuisine {name: $cuisine}) })
WITH r, matched,
     COUNT { (r)-[:CONTAINS]->() } - size(matched) AS missing_count,
     COUNT { (r)<-[:REVIEWED]-() } AS reviews
ORDER BY size(matched) DESC, missing_count ASC, reviews DESC, r.id
SKIP $offset LIMIT $limit
RETURN r.id AS id, r.name AS name, r.minutes AS minutes, reviews,
       matched AS matched_ingredients,
       [(r)-[:CONTAINS]->(x) WHERE NOT x.name IN matched | x.name] AS missing_ingredients
"""

SEARCH_PARAMS = {
    "ingredients": ["egg", "parmesan cheese"],
    "min_matched": 2,
    "max_minutes": 30,
    "diet": "vegetarian",
    "course": None,
    "cuisine": "european",
    "offset": 0,
    "limit": 21,
}

# Popular recipes: the shopping list (missing ingredients) built for every recipe, then cut; versus cut first.
PAGE_BEFORE = """
MATCH (r:Recipe)
WITH r, COUNT { (r)<-[:REVIEWED]-() } AS reviews, [(r)-[:CONTAINS]->(i) | i.name] AS ingredients
ORDER BY reviews DESC, r.id SKIP $offset LIMIT $limit
RETURN r.id AS id, reviews, ingredients
"""
PAGE_AFTER = """
MATCH (r:Recipe)
WITH r, COUNT { (r)<-[:REVIEWED]-() } AS reviews
ORDER BY reviews DESC, r.id SKIP $offset LIMIT $limit
RETURN r.id AS id, reviews, [(r)-[:CONTAINS]->(i) | i.name] AS ingredients
"""

# Two disconnected patterns (cartesian product) versus one connected pattern.
CARTESIAN_BEFORE = """
MATCH (c:Cuisine {name: $cuisine}), (r:Recipe)
WHERE (r)-[:HAS_CUISINE]->(c) AND r.minutes <= $max_minutes
RETURN count(r) AS recipes
"""
CARTESIAN_AFTER = """
MATCH (r:Recipe)-[:HAS_CUISINE]->(:Cuisine {name: $cuisine})
WHERE r.minutes <= $max_minutes
RETURN count(r) AS recipes
"""

PAIRS = [
    ("filtered search (cuisine hierarchy once)", SEARCH_BEFORE, SEARCH_QUERY, SEARCH_PARAMS),
    ("pagination: LIMIT before the expensive part", PAGE_BEFORE, PAGE_AFTER, {"offset": 0, "limit": 20}),
    (
        "cartesian product vs connected pattern",
        CARTESIAN_BEFORE,
        CARTESIAN_AFTER,
        {"cuisine": "italian", "max_minutes": 30},
    ),
]


def main() -> None:
    """Run every before/after pair, check they return the same rows, print db hits and timings."""
    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        print(f"{'rewrite':<46} {'version':<7} {'db hits':>10} {'median ms':>10}")
        for name, before, after, params in PAIRS:
            same = db.run_read(before, params) == db.run_read(after, params)
            for version, query in (("before", before), ("after", after)):
                hits = total_db_hits(db.profile(query, params))
                print(f"{name:<46} {version:<7} {hits:>10,} {median_ms(db, query, params):>10.1f}")
            print(f"{'':<46} same results: {same}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
