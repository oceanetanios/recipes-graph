"""Entry point of the Recipe & Ingredient Explorer API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database.neo4j_client import Neo4jClient
from app.routers import health


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j client at startup and close it at shutdown."""
    application.state.db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        yield
    finally:
        application.state.db.close()


app = FastAPI(title="Recipe & Ingredient Explorer", lifespan=lifespan)
app.include_router(health.router)
