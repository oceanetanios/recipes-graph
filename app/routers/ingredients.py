"""Ingredient endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_recipe_service
from app.models.recipes import ErrorMessage, IngredientSuggestion
from app.services.recipes import RecipeService

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


@router.get(
    "",
    response_model=list[IngredientSuggestion],
    responses={503: {"model": ErrorMessage, "description": "The graph database is unavailable"}},
)
def suggest_ingredients(
    service: Annotated[RecipeService, Depends(get_recipe_service)],
    q: Annotated[str, Query(min_length=2, max_length=50, description="Start of an ingredient name, e.g. tom")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[IngredientSuggestion]:
    """Suggest ingredient names (full-text search), to know the exact names to use in a recipe search."""
    return [IngredientSuggestion(**row) for row in service.suggest_ingredients(q, limit)]
