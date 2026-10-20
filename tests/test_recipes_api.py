"""API tests for the recipe and ingredient endpoints, with a fake service (no Neo4j needed)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable

from app.dependencies import get_recipe_service
from app.main import app

RECIPE = {
    "id": 99086,
    "name": "wonderful parmesan zucchini strips",
    "minutes": 35,
    "n_steps": 6,
    "submitted": "2004-08-17",
    "description": None,
    "steps": ["slice", "bake"],
    "ingredients": ["egg", "parmesan cheese", "zucchini"],
    "cuisines": [],
    "diets": ["vegetarian"],
    "courses": ["side-dishes"],
    "nutrition": {
        "calories": 120.5,
        "total_fat_pdv": 10.0,
        "sugar_pdv": 5.0,
        "sodium_pdv": 12.0,
        "protein_pdv": 20.0,
        "saturated_fat_pdv": 15.0,
        "carbs_pdv": 2.0,
    },
    "reviews": 53,
    "average_rating": 4.8,
}

SUMMARY = {
    "id": 99086,
    "name": "wonderful parmesan zucchini strips",
    "minutes": 35,
    "reviews": 53,
    "matched_ingredients": ["egg", "parmesan cheese"],
    "missing_ingredients": ["zucchini"],
}


class FakeRecipeService:
    """Stand-in for RecipeService with fixed answers."""

    known_ingredients = {"egg", "parmesan cheese"}
    fail = False
    last_filters: dict[str, str | None] = {}

    def resolve_ingredients(self, names: list[str]) -> tuple[list[dict[str, str]], list[str]]:
        """Known names are found exactly, the others are unknown."""
        if self.fail:
            raise ServiceUnavailable("database down")
        names = [name.lower() for name in names]
        found = [{"typed": n, "name": n, "method": "exact"} for n in names if n in self.known_ingredients]
        return found, [n for n in names if n not in self.known_ingredients]

    def unknown_filters(self, diet: str | None, course: str | None, cuisine: str | None) -> list[str]:
        """Only 'vegetarian' and 'main-dish' exist in these tests."""
        FakeRecipeService.last_filters = {"diet": diet, "course": course, "cuisine": cuisine}
        bad = []
        if diet and diet != "vegetarian":
            bad.append("diet")
        if course and course != "main-dish":
            bad.append("course")
        return bad

    def search(self, ingredients: list[str], **options: Any) -> tuple[list[dict[str, Any]], bool]:
        """Always return the same single recipe."""
        del ingredients, options
        return [SUMMARY], False

    def get_recipe(self, recipe_id: int) -> dict[str, Any] | None:
        """Only recipe 99086 exists."""
        return RECIPE if recipe_id == 99086 else None

    def similar_recipes(self, recipe_id: int, limit: int = 10) -> dict[str, Any] | None:
        """Recipe 99086 has one similar recipe."""
        del limit
        if recipe_id != 99086:
            return None
        similar = {**SUMMARY, "id": 1, "score": 0.5, "shared_ingredients": ["zucchini"]}
        return {"recipe_name": RECIPE["name"], "similar": [similar]}

    def community(self, recipe_id: int, limit: int = 5) -> dict[str, Any] | None:
        """Recipe 99086 is in community 7."""
        del limit
        if recipe_id != 99086:
            return None
        return {
            "recipe_name": RECIPE["name"],
            "community": 7,
            "size": 120,
            "ingredients": ["zucchini"],
            "recipes": [{"id": 1, "name": "zucchini bread", "reviews": 40}],
        }

    def path(self, from_id: int, to_id: int) -> dict[str, Any] | None:
        """Recipes 99086 and 2 are linked through recipe 1; recipe 3 is unreachable."""
        if 1 in (from_id, to_id):
            return None
        if 3 in (from_id, to_id):
            return {"path": None, "scores": None}
        return {
            "path": [{"id": from_id, "name": "a"}, {"id": 1, "name": "b"}, {"id": to_id, "name": "c"}],
            "scores": [0.4, 0.3],
        }

    def suggest_ingredients(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return one suggestion."""
        del text, limit
        return [{"name": "tomatoes", "recipe_count": 1971}]


@pytest.fixture(name="client")
def client_fixture() -> Iterator[TestClient]:
    """A test client whose routes use the fake service instead of Neo4j."""
    FakeRecipeService.fail = False
    app.dependency_overrides[get_recipe_service] = FakeRecipeService
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_search_returns_results(client: TestClient) -> None:
    """A valid search answers 200 with the found ingredients and the results."""
    response = client.get("/api/recipes/search", params={"ingredients": ["Egg", "unicorn"]})

    assert response.status_code == 200
    body = response.json()
    assert body["ingredients"] == [{"typed": "egg", "name": "egg", "method": "exact"}]
    assert body["unknown_ingredients"] == ["unicorn"]
    assert body["results"][0]["missing_ingredients"] == ["zucchini"]


def test_search_accepts_comma_separated_ingredients(client: TestClient) -> None:
    """'egg, parmesan cheese' in one box is two ingredients."""
    response = client.get("/api/recipes/search", params={"ingredients": "egg, parmesan cheese"})

    assert [item["name"] for item in response.json()["ingredients"]] == ["egg", "parmesan cheese"]


def test_search_rejects_more_than_ten_ingredients(client: TestClient) -> None:
    """More than 10 ingredients (after splitting commas) is a 422."""
    response = client.get("/api/recipes/search", params={"ingredients": ",".join(f"i{n}" for n in range(11))})

    assert response.status_code == 422


def test_search_normalizes_filter_values(client: TestClient) -> None:
    """'Main Dish' is understood as the tag 'main-dish'."""
    response = client.get("/api/recipes/search", params={"ingredients": "egg", "course": "Main Dish"})

    assert response.status_code == 200
    assert FakeRecipeService.last_filters["course"] == "main-dish"


def test_search_needs_at_least_one_ingredient(client: TestClient) -> None:
    """No ingredient is a validation error (422)."""
    assert client.get("/api/recipes/search").status_code == 422
    assert client.get("/api/recipes/search", params={"ingredients": " , "}).status_code == 422


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"match": "some"}])
def test_search_rejects_bad_parameters(client: TestClient, params: dict[str, Any]) -> None:
    """Out-of-range or invalid parameters are rejected by Pydantic with 422."""
    response = client.get("/api/recipes/search", params={"ingredients": "egg", **params})
    assert response.status_code == 422


def test_search_with_only_unknown_ingredients_is_404(client: TestClient) -> None:
    """If no ingredient exists, the answer is 404 with a helpful message."""
    response = client.get("/api/recipes/search", params={"ingredients": "unicorn"})

    assert response.status_code == 404
    assert "unicorn" in response.json()["detail"]


def test_search_with_unknown_diet_is_422(client: TestClient) -> None:
    """A filter value that does not exist in the graph is a 422."""
    response = client.get("/api/recipes/search", params={"ingredients": "egg", "diet": "carnivore"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown diet 'carnivore'"


def test_get_recipe(client: TestClient) -> None:
    """An existing recipe is returned with its nutrition and rating."""
    response = client.get("/api/recipes/99086")

    assert response.status_code == 200
    assert response.json()["nutrition"]["calories"] == 120.5


def test_get_missing_recipe_is_404(client: TestClient) -> None:
    """An unknown id gives 404."""
    assert client.get("/api/recipes/1").status_code == 404


def test_similar_recipes(client: TestClient) -> None:
    """Similar recipes come with their score and the shared ingredients that explain them."""
    response = client.get("/api/recipes/99086/similar")

    assert response.status_code == 200
    assert response.json()["similar"][0]["shared_ingredients"] == ["zucchini"]


def test_similar_recipes_errors(client: TestClient) -> None:
    """Unknown recipe gives 404, more than 10 results is a 422."""
    assert client.get("/api/recipes/1/similar").status_code == 404
    assert client.get("/api/recipes/99086/similar", params={"limit": 11}).status_code == 422


def test_community(client: TestClient) -> None:
    """A recipe's community comes with its size, ingredients and popular recipes; unknown id is 404."""
    body = client.get("/api/recipes/99086/community").json()

    assert body["community"] == 7
    assert body["characteristic_ingredients"] == ["zucchini"]
    assert client.get("/api/recipes/1/community").status_code == 404


def test_path(client: TestClient) -> None:
    """A path gives its length and steps; no path gives found=false; same recipe twice is a 422."""
    found = client.get("/api/recipes/path", params={"from": 99086, "to": 2}).json()
    missing = client.get("/api/recipes/path", params={"from": 99086, "to": 3}).json()

    assert found["found"] is True and found["length"] == 2
    assert missing["found"] is False and missing["length"] is None
    assert client.get("/api/recipes/path", params={"from": 5, "to": 5}).status_code == 422
    assert client.get("/api/recipes/path", params={"from": 1, "to": 5}).status_code == 404


def test_suggest_ingredients_needs_two_characters(client: TestClient) -> None:
    """q must be at least 2 characters long."""
    assert client.get("/api/ingredients", params={"q": "t"}).status_code == 422
    assert client.get("/api/ingredients", params={"q": "tom"}).json()[0]["name"] == "tomatoes"


def test_database_down_gives_503(client: TestClient) -> None:
    """When Neo4j is unreachable, the API answers 503 with a clear message instead of crashing."""
    FakeRecipeService.fail = True

    response = client.get("/api/recipes/search", params={"ingredients": "egg"})

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]
