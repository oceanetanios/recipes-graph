"""Recipe and ingredient queries: the Cypher lives here, the routers only handle HTTP."""

import re
from typing import Any

from app.database.neo4j_client import Neo4jClient
from app.ingredient_names import normalize_name, singular_candidates
from app.models.recipes import MatchMode

# Starts from the indexed Ingredient nodes (never from all recipes), filters, sorts and cuts the page,
# and only then builds the list of missing ingredients, so that expensive part runs for one page only.
SEARCH_QUERY = """
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

RESOLVE_INGREDIENTS_QUERY = """
UNWIND $candidates AS candidate
MATCH (i:Ingredient {name: candidate.name})
RETURN candidate.input AS input, candidate.rank AS rank, i.name AS name
"""

UNKNOWN_FILTERS_QUERY = """
RETURN
  CASE WHEN $diet IS NULL OR EXISTS { (:Diet {name: $diet}) } THEN null ELSE 'diet' END AS diet,
  CASE WHEN $course IS NULL OR EXISTS { (:Course {name: $course}) } THEN null ELSE 'course' END AS course,
  CASE WHEN $cuisine IS NULL OR EXISTS { (:Cuisine {name: $cuisine}) } THEN null ELSE 'cuisine' END AS cuisine
"""

RECIPE_QUERY = """
MATCH (r:Recipe {id: $id})
CALL (r) {
  OPTIONAL MATCH (r)<-[rev:REVIEWED]-()
  RETURN count(rev) AS reviews, avg(rev.rating) AS average_rating
}
RETURN r.id AS id, r.name AS name, r.minutes AS minutes, r.n_steps AS n_steps,
       toString(r.submitted) AS submitted, r.description AS description, coalesce(r.steps, []) AS steps,
       [(r)-[:CONTAINS]->(i) | i.name] AS ingredients,
       [(r)-[:HAS_CUISINE]->(c) | c.name] AS cuisines,
       [(r)-[:FITS_DIET]->(d) | d.name] AS diets,
       [(r)-[:SERVED_AS]->(c) | c.name] AS courses,
       {calories: r.calories, total_fat_pdv: r.total_fat_pdv, sugar_pdv: r.sugar_pdv,
        sodium_pdv: r.sodium_pdv, protein_pdv: r.protein_pdv,
        saturated_fat_pdv: r.saturated_fat_pdv, carbs_pdv: r.carbs_pdv} AS nutrition,
       reviews, average_rating
"""

# SIMILAR_TO is precomputed by GDS Node Similarity (scripts/graph_algorithms.py), so this is a cheap read.
SIMILAR_QUERY = """
MATCH (r:Recipe {id: $id})
OPTIONAL MATCH (r)-[s:SIMILAR_TO]->(other:Recipe)
WITH r, s, other ORDER BY s.score DESC LIMIT $limit
RETURN r.name AS recipe_name,
       [row IN collect(CASE WHEN other IS NULL THEN null ELSE {
          id: other.id, name: other.name, score: round(s.score, 3), minutes: other.minutes,
          reviews: COUNT { (other)<-[:REVIEWED]-() },
          shared_ingredients: COLLECT {
            MATCH (r)-[:CONTAINS]->(i:Ingredient)<-[:CONTAINS]-(other)
            RETURN i.name ORDER BY i.recipe_count
          }
       } END) WHERE row IS NOT NULL] AS similar
"""

# Recipe.community is written by GDS Louvain. Characteristic ingredients: frequent in the community AND rare
# overall (count x IDF), otherwise salt would top every community.
COMMUNITY_QUERY = """
MATCH (r:Recipe {id: $id})
WITH r, r.community AS c, COUNT { (:Recipe) } AS total
CALL (c) { MATCH (o:Recipe {community: c}) RETURN count(o) AS size }
CALL (c, total) {
  MATCH (:Recipe {community: c})-[:CONTAINS]->(i:Ingredient)
  WITH i, count(*) AS n ORDER BY n * log(toFloat(total) / i.recipe_count) DESC LIMIT 8
  RETURN collect(i.name) AS ingredients
}
CALL (r, c) {
  MATCH (o:Recipe {community: c}) WHERE o <> r
  WITH o, COUNT { (o)<-[:REVIEWED]-() } AS reviews ORDER BY reviews DESC LIMIT $limit
  RETURN collect({id: o.id, name: o.name, reviews: reviews}) AS recipes
}
RETURN r.name AS recipe_name, c AS community, size, ingredients, recipes
"""

# Shortest chain of SIMILAR_TO between two recipes (both ends bound, so Neo4j searches from both sides).
PATH_QUERY = """
MATCH (a:Recipe {id: $from_id}), (b:Recipe {id: $to_id})
OPTIONAL MATCH p = shortestPath((a)-[:SIMILAR_TO*..8]-(b))
RETURN [n IN nodes(p) | {id: n.id, name: n.name}] AS path,
       [rel IN relationships(p) | round(rel.score, 3)] AS scores
"""

SUGGEST_INGREDIENTS_QUERY = """
CALL db.index.fulltext.queryNodes('ingredient_name_fulltext', $text) YIELD node, score
RETURN node.name AS name, node.recipe_count AS recipe_count
ORDER BY score DESC, recipe_count DESC
LIMIT $limit
"""

LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def name_candidates(name: str) -> list[str]:
    """Possible spellings of an ingredient in the graph, best first: as typed, then singular forms."""
    return [name, *singular_candidates(name)]


def fulltext_query(text: str, prefix: bool = True) -> str:
    """Build a safe full-text (Lucene) query with every word required and special characters escaped.

    With prefix=True each word also matches longer words (tom -> tomato), for suggestions while typing.
    With prefix=False only whole words match, so 'egg' finds 'egg white' but never 'eggplant'.
    """
    words = [LUCENE_SPECIAL.sub(r"\\\1", word) for word in normalize_name(text).split(" ") if word]
    return " AND ".join(f"{word}*" if prefix else word for word in words)


def normalize_filter(value: str | None) -> str | None:
    """Turn a typed filter value into the tag format of the graph: 'Main Dish' -> 'main-dish'."""
    if value is None or not value.strip():
        return None
    return "-".join(normalize_name(value).split(" "))


class RecipeService:
    """Runs the recipe and ingredient queries against Neo4j and shapes the results."""

    def __init__(self, db: Neo4jClient) -> None:
        """Keep the shared Neo4j client."""
        self._db = db

    def resolve_ingredients(self, names: list[str]) -> tuple[list[dict[str, str]], list[str]]:
        """Match typed names to ingredients of the graph, in the typed order.

        Returns (resolutions, unknown). Each resolution says how the name was found: exact, singular,
        or closest (full-text guess, used only when the first two fail). Names with no match are unknown.
        """
        typed = list(dict.fromkeys(normalize_name(name) for name in names if name.strip()))
        candidates = [
            {"input": name, "rank": rank, "name": candidate}
            for name in typed
            for rank, candidate in enumerate(name_candidates(name))
        ]
        best: dict[str, tuple[int, str]] = {}
        for row in self._db.run_read(RESOLVE_INGREDIENTS_QUERY, {"candidates": candidates}):
            if row["input"] not in best or row["rank"] < best[row["input"]][0]:
                best[row["input"]] = (row["rank"], row["name"])

        resolutions, unknown, used = [], [], set()
        for name in typed:
            if name in best:
                rank, found = best[name]
                method = "exact" if rank == 0 else "singular"
            else:
                closest = self._closest_ingredient(name)
                if closest is None:
                    unknown.append(name)
                    continue
                found, method = closest, "closest"
            if found not in used:
                used.add(found)
                resolutions.append({"typed": name, "name": found, "method": method})
        return resolutions, unknown

    def _closest_ingredient(self, name: str) -> str | None:
        """Best full-text match on whole words (shortest, most used name first), or None."""
        query = fulltext_query(name, prefix=False)
        rows = self._db.run_read(SUGGEST_INGREDIENTS_QUERY, {"text": query, "limit": 1}) if query else []
        return rows[0]["name"] if rows else None

    def unknown_filters(self, diet: str | None, course: str | None, cuisine: str | None) -> list[str]:
        """Return the names of the filters ('diet', 'course', 'cuisine') whose value does not exist in the graph."""
        row = self._db.run_read(UNKNOWN_FILTERS_QUERY, {"diet": diet, "course": course, "cuisine": cuisine})[0]
        return [kind for kind in row.values() if kind]

    def search(  # pylint: disable=too-many-arguments
        self,
        ingredients: list[str],
        *,
        match: MatchMode = "all",
        max_minutes: int | None = None,
        diet: str | None = None,
        course: str | None = None,
        cuisine: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Find recipes containing the ingredients; return one page of rows and whether more pages exist."""
        params = {
            "ingredients": ingredients,
            "min_matched": len(ingredients) if match == "all" else 1,
            "max_minutes": max_minutes,
            "diet": diet,
            "course": course,
            "cuisine": cuisine,
            "offset": offset,
            "limit": limit + 1,  # one extra row tells us if there is a next page, without counting everything
        }
        rows = self._db.run_read(SEARCH_QUERY, params)
        return rows[:limit], len(rows) > limit

    def get_recipe(self, recipe_id: int) -> dict[str, Any] | None:
        """Return all the details of one recipe, or None if the id does not exist."""
        rows = self._db.run_read(RECIPE_QUERY, {"id": recipe_id})
        return rows[0] if rows else None

    def similar_recipes(self, recipe_id: int, limit: int = 10) -> dict[str, Any] | None:
        """Return the recipe name and its most similar recipes, or None if the id does not exist."""
        rows = self._db.run_read(SIMILAR_QUERY, {"id": recipe_id, "limit": limit})
        return rows[0] if rows else None

    def community(self, recipe_id: int, limit: int = 5) -> dict[str, Any] | None:
        """Return the Louvain community of a recipe (size, ingredients, popular recipes), or None if unknown id."""
        rows = self._db.run_read(COMMUNITY_QUERY, {"id": recipe_id, "limit": limit})
        return rows[0] if rows else None

    def path(self, from_id: int, to_id: int) -> dict[str, Any] | None:
        """Return the shortest SIMILAR_TO chain between two recipes, or None if one of the ids does not exist."""
        rows = self._db.run_read(PATH_QUERY, {"from_id": from_id, "to_id": to_id})
        return rows[0] if rows else None

    def suggest_ingredients(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return ingredients whose name contains words starting like the given text."""
        query = fulltext_query(text)
        if not query:
            return []
        return self._db.run_read(SUGGEST_INGREDIENTS_QUERY, {"text": query, "limit": limit})
