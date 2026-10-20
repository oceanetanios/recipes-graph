"""Entry point of the Recipe & Ingredient Explorer API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from app.agent.graph import build_agent
from app.config import settings
from app.database.neo4j_client import Neo4jClient
from app.routers import agent, health, ingredients, recipes

PLACEHOLDER_KEYS = {"", "your-gemini-key-here"}


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j client (and the agent, when a Gemini key is set) at startup; close the client at shutdown."""
    application.state.db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    application.state.agent = None
    if settings.gemini_api_key not in PLACEHOLDER_KEYS:
        application.state.agent = build_agent(
            application.state.db, settings.gemini_api_key, settings.gemini_model, settings.gemini_fallback_models
        )
    try:
        yield
    finally:
        application.state.db.close()


app = FastAPI(title="Recipe & Ingredient Explorer", lifespan=lifespan)
app.include_router(health.router)
app.include_router(recipes.router)
app.include_router(ingredients.router)
app.include_router(agent.router)


@app.exception_handler(ServiceUnavailable)
@app.exception_handler(SessionExpired)
async def database_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Answer 503 with a clear message when Neo4j cannot be reached, instead of a generic 500 error."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "The graph database is unavailable, try again later"},
    )
