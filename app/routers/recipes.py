"""Recipe endpoints: search by ingredients and recipe details."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.dependencies import get_recipe_service
from app.models.recipes import (
    ErrorMessage,
    IngredientResolution,
    MatchMode,
    RecipeDetail,
    RecipeSearchResponse,
    RecipeSummary,
    SimilarRecipe,
    SimilarRecipesResponse,
)
from app.services.recipes import RecipeService, normalize_filter

router = APIRouter(prefix="/api/recipes", tags=["recipes"])

Service = Annotated[RecipeService, Depends(get_recipe_service)]

MAX_INGREDIENTS = 10
DATABASE_DOWN: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorMessage, "description": "The graph database is unavailable"}
}


def split_ingredients(values: list[str]) -> list[str]:
    """Accept both ?ingredients=egg&ingredients=flour and ?ingredients=egg,flour (or a mix of both)."""
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def checked_filters(service: RecipeService, diet: str | None, course: str | None, cuisine: str | None) -> dict:
    """Normalize the filter values ('Main Dish' -> 'main-dish') and answer 422 if one does not exist."""
    filters = {"diet": normalize_filter(diet), "course": normalize_filter(course), "cuisine": normalize_filter(cuisine)}
    bad_filters = service.unknown_filters(**filters)
    if bad_filters:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown " + ", ".join(f"{kind} '{filters[kind]}'" for kind in bad_filters),
        )
    return filters


@router.get(
    "/search",
    response_model=RecipeSearchResponse,
    responses={
        404: {"model": ErrorMessage, "description": "None of the typed ingredients matches an ingredient"},
        422: {"description": "Invalid parameter, too many ingredients, or unknown diet / course / cuisine"},
        **DATABASE_DOWN,
    },
)
def search_recipes(  # pylint: disable=too-many-arguments
    *,
    service: Service,
    ingredients: Annotated[
        list[str],
        Query(
            min_length=1,
            description="Ingredients you have: one per box, or separated by commas (egg, parmesan). "
            f"At most {MAX_INGREDIENTS}.",
        ),
    ],
    match: Annotated[MatchMode, Query(description="all: recipes must contain every ingredient; any: at least one")] = (
        "all"
    ),
    max_minutes: Annotated[int | None, Query(ge=1, le=10080, description="Maximum cooking time")] = None,
    diet: Annotated[str | None, Query(description="For example vegetarian, vegan, gluten-free")] = None,
    course: Annotated[str | None, Query(description="For example main dish, desserts, breakfast")] = None,
    cuisine: Annotated[str | None, Query(description="For example italian, or a region such as asian")] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Recipes per page")] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000, description="Recipes to skip: 0 = page 1, 20 = page 2...")] = 0,
) -> RecipeSearchResponse:
    """Find recipes you can cook with the given ingredients.

    Results are sorted by how many of your ingredients they use, then by how few other ingredients
    they need, then by popularity. A cuisine filter includes its sub-cuisines (asian includes szechuan).
    The answer shows how each typed ingredient was understood (exact name, singular form or closest match).
    """
    typed = split_ingredients(ingredients)
    if not typed or len(typed) > MAX_INGREDIENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Give between 1 and {MAX_INGREDIENTS} ingredients (got {len(typed)})",
        )
    resolutions, unknown = service.resolve_ingredients(typed)
    if not resolutions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ingredient matches: {', '.join(unknown)}. Try GET /api/ingredients?q=...",
        )
    filters = checked_filters(service, diet, course, cuisine)
    rows, has_more = service.search(
        [resolution["name"] for resolution in resolutions],
        match=match,
        max_minutes=max_minutes,
        limit=limit,
        offset=offset,
        **filters,
    )
    return RecipeSearchResponse(
        ingredients=[IngredientResolution(**resolution) for resolution in resolutions],
        unknown_ingredients=unknown,
        match=match,
        limit=limit,
        offset=offset,
        has_more=has_more,
        results=[RecipeSummary(**row) for row in rows],
    )


@router.get(
    "/{recipe_id}/similar",
    response_model=SimilarRecipesResponse,
    responses={404: {"model": ErrorMessage, "description": "No recipe with this id"}, **DATABASE_DOWN},
)
def similar_recipes(
    service: Service,
    recipe_id: Annotated[int, Path(ge=1)],
    limit: Annotated[int, Query(ge=1, le=10, description="How many similar recipes")] = 5,
) -> SimilarRecipesResponse:
    """Recipes that share the most characteristic ingredients with this one (GDS Node Similarity).

    Similarity is a Jaccard index weighted by how rare each ingredient is (IDF), so sharing saffron counts
    much more than sharing salt. Each result lists the shared ingredients that explain it, rarest first.
    """
    row = service.similar_recipes(recipe_id, limit)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Recipe {recipe_id} not found")
    return SimilarRecipesResponse(
        recipe_id=recipe_id,
        recipe_name=row["recipe_name"],
        similar=[SimilarRecipe(**item) for item in row["similar"]],
    )


@router.get(
    "/{recipe_id}",
    response_model=RecipeDetail,
    responses={404: {"model": ErrorMessage, "description": "No recipe with this id"}, **DATABASE_DOWN},
)
def get_recipe(service: Service, recipe_id: Annotated[int, Path(ge=1)]) -> RecipeDetail:
    """Return everything about one recipe: ingredients, steps, tags, nutrition and ratings."""
    row = service.get_recipe(recipe_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Recipe {recipe_id} not found")
    return RecipeDetail(**row)
