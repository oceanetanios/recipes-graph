"""Pydantic models for the recipe and ingredient endpoints."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MatchMode = Literal["all", "any"]
ResolutionMethod = Literal["exact", "singular", "closest"]


class ErrorMessage(BaseModel):
    """Body of every error answer (404, 422 from our checks, 503)."""

    detail: str


class IngredientResolution(BaseModel):
    """How one typed ingredient was matched to an ingredient of the graph."""

    typed: str = Field(description="What was typed, cleaned (lowercase, single spaces)")
    name: str = Field(description="Ingredient used in the search")
    method: ResolutionMethod = Field(
        description="exact: same name; singular: plural turned into singular; "
        "closest: best full-text match, a guess worth checking"
    )


class RecipeSummary(BaseModel):
    """One recipe in a search result."""

    id: int
    name: str
    minutes: int | None = Field(description="Cooking time, null when unknown")
    reviews: int = Field(description="Number of reviews (popularity)")
    matched_ingredients: list[str] = Field(description="Searched ingredients the recipe contains")
    missing_ingredients: list[str] = Field(description="Other ingredients needed (the shopping list)")


class RecipeSearchResponse(BaseModel):
    """A page of recipes matching an ingredient search."""

    ingredients: list[IngredientResolution] = Field(description="How each typed ingredient was understood")
    unknown_ingredients: list[str] = Field(description="Typed ingredients with no match at all (ignored)")
    match: MatchMode
    limit: int
    offset: int
    has_more: bool = Field(description="True when there is at least one more page")
    results: list[RecipeSummary]


class Nutrition(BaseModel):
    """Nutrition values of a recipe: calories, then percent of daily value (PDV)."""

    calories: float | None
    total_fat_pdv: float | None
    sugar_pdv: float | None
    sodium_pdv: float | None
    protein_pdv: float | None
    saturated_fat_pdv: float | None
    carbs_pdv: float | None


class RecipeDetail(BaseModel):
    """Everything known about one recipe."""

    id: int
    name: str
    minutes: int | None
    n_steps: int
    submitted: date | None
    description: str | None
    steps: list[str]
    ingredients: list[str]
    cuisines: list[str]
    diets: list[str]
    courses: list[str]
    nutrition: Nutrition
    reviews: int
    average_rating: float | None = Field(description="Average of the 1-5 ratings, null when nobody rated it")


class IngredientSuggestion(BaseModel):
    """An ingredient whose name matches a search text."""

    name: str
    recipe_count: int = Field(description="Number of recipes that use it")
