"""Health-check endpoint used by Docker, Nginx and humans."""

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Body returned by GET /health."""

    status: str
    neo4j: str


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    """Report whether the API is running and whether Neo4j is reachable."""
    if request.app.state.db.is_alive():
        return HealthResponse(status="ok", neo4j="up")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="degraded", neo4j="down")
