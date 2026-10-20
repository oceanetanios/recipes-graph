"""Shared FastAPI dependencies: they give each route the objects it needs."""

from fastapi import Request

from app.services.recipes import RecipeService


def get_recipe_service(request: Request) -> RecipeService:
    """Build a RecipeService on top of the Neo4j client created at startup."""
    return RecipeService(request.app.state.db)
