"""Unit tests for app/services/recipes.py, with a fake database (no Neo4j needed)."""

from typing import Any

from app.services.recipes import (
    RESOLVE_INGREDIENTS_QUERY,
    SEARCH_QUERY,
    SIMILAR_QUERY,
    SUGGEST_INGREDIENTS_QUERY,
    UNKNOWN_FILTERS_QUERY,
    RecipeService,
    fulltext_query,
    name_candidates,
    normalize_filter,
)


class FakeDb:
    """Records the queries it receives and answers with prepared rows, chosen by query."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """Store the rows to return for each query text (any other query returns no rows)."""
        self.answers = answers or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run_read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Remember the call and return the rows prepared for this query."""
        self.calls.append((query, params or {}))
        return self.answers.get(query, [])


def test_name_candidates_tries_singular_forms() -> None:
    """The typed name comes first, then the possible singular forms."""
    assert name_candidates("berries") == ["berries", "berry", "berri", "berrie"]
    assert name_candidates("eggs") == ["eggs", "egg"]
    assert name_candidates("glass") == ["glass"]


def test_fulltext_query_escapes_special_characters() -> None:
    """Lucene special characters cannot change the meaning of the full-text query."""
    assert fulltext_query("cherry tom") == "cherry* AND tom*"
    assert fulltext_query("cherry tom", prefix=False) == "cherry AND tom"
    assert fulltext_query("a(b") == "a\\(b*"
    assert fulltext_query("   ") == ""


def test_normalize_filter() -> None:
    """Typed filter values are turned into the tag format."""
    assert normalize_filter(" Main  Dish ") == "main-dish"
    assert normalize_filter("") is None
    assert normalize_filter(None) is None


def test_resolve_ingredients_methods() -> None:
    """Exact names win over singular forms; with no match, the full-text guess is used; else unknown."""
    db = FakeDb(
        {
            RESOLVE_INGREDIENTS_QUERY: [
                {"input": "eggs", "rank": 1, "name": "egg"},
                {"input": "milk", "rank": 0, "name": "milk"},
            ],
            SUGGEST_INGREDIENTS_QUERY: [{"name": "parmesan cheese", "recipe_count": 3048}],
        }
    )

    resolutions, unknown = RecipeService(db).resolve_ingredients(["Eggs", "milk", "parmesan"])

    assert resolutions == [
        {"typed": "eggs", "name": "egg", "method": "singular"},
        {"typed": "milk", "name": "milk", "method": "exact"},
        {"typed": "parmesan", "name": "parmesan cheese", "method": "closest"},
    ]
    assert not unknown
    closest_call = [params for query, params in db.calls if query == SUGGEST_INGREDIENTS_QUERY][0]
    assert closest_call["text"] == "parmesan"  # whole word, no prefix: 'egg' must never become 'eggplant'


def test_resolve_ingredients_reports_unknown_and_removes_duplicates() -> None:
    """Names with no match at all are unknown; two names matching the same ingredient count once."""
    db = FakeDb(
        {
            RESOLVE_INGREDIENTS_QUERY: [
                {"input": "egg", "rank": 0, "name": "egg"},
                {"input": "eggs", "rank": 1, "name": "egg"},
            ]
        }
    )

    resolutions, unknown = RecipeService(db).resolve_ingredients(["egg", "eggs", "unicorn"])

    assert [resolution["name"] for resolution in resolutions] == ["egg"]
    assert unknown == ["unicorn"]


def test_search_asks_one_extra_row_to_detect_next_page() -> None:
    """With limit=2, the query asks for 3 rows; getting 3 means there is another page."""
    db = FakeDb({SEARCH_QUERY: [{"id": 1}, {"id": 2}, {"id": 3}]})

    rows, has_more = RecipeService(db).search(["egg"], limit=2)

    assert db.calls[0][1]["limit"] == 3
    assert rows == [{"id": 1}, {"id": 2}]
    assert has_more is True


def test_search_match_mode_sets_minimum_matched_ingredients() -> None:
    """'all' needs every ingredient, 'any' needs at least one."""
    db = FakeDb()
    service = RecipeService(db)

    service.search(["egg", "flour", "milk"], match="all")
    service.search(["egg", "flour", "milk"], match="any")

    assert [params["min_matched"] for _, params in db.calls] == [3, 1]


def test_unknown_filters_lists_only_bad_values() -> None:
    """Only the filters whose value does not exist are reported."""
    db = FakeDb({UNKNOWN_FILTERS_QUERY: [{"diet": "diet", "course": None, "cuisine": None}]})

    assert RecipeService(db).unknown_filters("carnivore", None, None) == ["diet"]


def test_similar_recipes_passes_id_and_limit() -> None:
    """The similar-recipes query gets the id and limit, and an unknown id gives None."""
    row = {"recipe_name": "guacamole", "similar": []}
    db = FakeDb({SIMILAR_QUERY: [row]})

    assert RecipeService(db).similar_recipes(8739, limit=3) == row
    assert db.calls[0][1] == {"id": 8739, "limit": 3}
    assert RecipeService(FakeDb()).similar_recipes(1) is None


def test_get_recipe_returns_none_when_missing() -> None:
    """An unknown id gives None (the router turns it into a 404)."""
    assert RecipeService(FakeDb()).get_recipe(1) is None


def test_suggest_ingredients_skips_empty_text() -> None:
    """Text with only spaces does not even reach the database."""
    db = FakeDb()

    assert not RecipeService(db).suggest_ingredients("   ")
    assert not db.calls
