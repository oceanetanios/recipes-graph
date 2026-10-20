"""Cypher tests against the real Neo4j database. They are skipped when Neo4j is not running or is empty."""

from collections.abc import Iterator

import pytest

from app.config import settings
from app.database.neo4j_client import Neo4jClient
from app.services.recipes import RecipeService

pytestmark = pytest.mark.neo4j


@pytest.fixture(name="service", scope="module")
def service_fixture() -> Iterator[RecipeService]:
    """A RecipeService on the real database, or skip the tests if the data is not there."""
    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    if not db.is_alive() or not db.run_read("MATCH (r:Recipe) RETURN r LIMIT 1"):
        db.close()
        pytest.skip("Neo4j is not running or has no data (make docker-run, then make seed)")
    yield RecipeService(db)
    db.close()


def test_resolve_ingredients_on_real_names(service: RecipeService) -> None:
    """'Eggs' is the singular 'egg', 'parmesan' the closest 'parmesan cheese', nonsense stays unknown."""
    resolutions, unknown = service.resolve_ingredients(["Eggs", "parmesan", "egg", "no such thing"])

    assert resolutions == [
        {"typed": "eggs", "name": "egg", "method": "singular"},
        {"typed": "parmesan", "name": "parmesan cheese", "method": "closest"},
    ]
    assert unknown == ["no such thing"]


def test_search_all_returns_recipes_with_every_ingredient(service: RecipeService) -> None:
    """In 'all' mode, every result contains all the searched ingredients."""
    rows, _ = service.search(["egg", "parmesan cheese"], limit=10)

    assert rows
    for row in rows:
        assert set(row["matched_ingredients"]) == {"egg", "parmesan cheese"}
        assert "egg" not in row["missing_ingredients"]


def test_search_filters_by_time_and_diet(service: RecipeService) -> None:
    """Filters are applied: no result above the time limit."""
    rows, _ = service.search(["egg"], max_minutes=15, diet="vegetarian", limit=10)

    assert rows
    assert all(row["minutes"] is not None and row["minutes"] <= 15 for row in rows)


def test_search_cuisine_includes_sub_cuisines(service: RecipeService) -> None:
    """Searching 'asian' also returns recipes tagged only with a sub-cuisine such as 'chinese'."""
    asian, _ = service.search(["soy sauce"], cuisine="asian", limit=50)
    chinese, _ = service.search(["soy sauce"], cuisine="chinese", limit=50)

    assert {row["id"] for row in chinese} & {row["id"] for row in asian}


def test_search_pages_do_not_overlap(service: RecipeService) -> None:
    """Page 2 continues page 1 without repeating recipes."""
    first, has_more = service.search(["egg"], limit=5)
    second, _ = service.search(["egg"], limit=5, offset=5)

    assert has_more
    assert not {row["id"] for row in first} & {row["id"] for row in second}


def test_get_recipe_details(service: RecipeService) -> None:
    """A recipe comes back with its ingredients, steps and a rating between 1 and 5."""
    recipe_id = service.search(["egg", "parmesan cheese"], limit=1)[0][0]["id"]

    recipe = service.get_recipe(recipe_id)

    assert recipe is not None
    assert {"egg", "parmesan cheese"} <= set(recipe["ingredients"])
    assert recipe["steps"]
    assert recipe["average_rating"] is None or 1 <= recipe["average_rating"] <= 5


def test_similar_recipes_share_ingredients(service: RecipeService) -> None:
    """Every similar recipe (from GDS) shares at least one ingredient, and scores are sorted."""
    recipe_id = service.search(["avocado", "lime"], limit=1)[0][0]["id"]

    row = service.similar_recipes(recipe_id, limit=5)

    if not row["similar"]:
        pytest.skip("SIMILAR_TO not computed yet (make gds)")
    scores = [item["score"] for item in row["similar"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0 < score <= 1 for score in scores)
    assert all(item["shared_ingredients"] for item in row["similar"])


def test_unknown_filters(service: RecipeService) -> None:
    """Existing filter values are accepted, others are reported."""
    assert not service.unknown_filters("vegetarian", "desserts", "italian")
    assert service.unknown_filters("carnivore", None, "atlantis") == ["diet", "cuisine"]


def test_suggest_ingredients(service: RecipeService) -> None:
    """Full-text suggestions find ingredients from the start of a word."""
    names = [row["name"] for row in service.suggest_ingredients("tom", limit=10)]

    assert any("tomato" in name for name in names)
