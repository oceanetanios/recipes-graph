"""Health tests of the running Docker stack (Nginx -> FastAPI -> Neo4j). Skipped when the stack is not running."""

import httpx
import pytest

from app.config import settings
from app.database.neo4j_client import Neo4jClient

pytestmark = pytest.mark.containers

NGINX_URL = "http://localhost:8080"
NEO4J_BROWSER_URL = "http://localhost:7474"


def get(url: str) -> httpx.Response:
    """GET a URL, or skip the test if nothing answers (stack not started with make docker-run)."""
    try:
        return httpx.get(url, timeout=10)
    except httpx.ConnectError:
        pytest.skip(f"{url} is not reachable: start the stack with make docker-run")
    raise AssertionError("unreachable")


def test_api_container_is_healthy_through_nginx() -> None:
    """Nginx answers, forwards to the API, and the API reaches Neo4j."""
    response = get(f"{NGINX_URL}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "neo4j": "up"}
    assert response.headers["server"].startswith("nginx")


def test_api_docs_are_served() -> None:
    """Swagger is available through Nginx."""
    assert get(f"{NGINX_URL}/docs").status_code == 200
    assert "/api/recipes/search" in get(f"{NGINX_URL}/openapi.json").json()["paths"]


def test_neo4j_container_answers_http() -> None:
    """The Neo4j container serves its HTTP endpoint (the Neo4j Browser)."""
    assert get(NEO4J_BROWSER_URL).status_code == 200


def test_neo4j_bolt_connectivity_and_data() -> None:
    """The driver can log in over Bolt, and the graph is loaded."""
    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        if not db.is_alive():
            pytest.skip("Neo4j is not reachable over Bolt: start the stack with make docker-run")
        assert db.run_read("MATCH (r:Recipe) RETURN count(r) > 0 AS loaded")[0]["loaded"]
    finally:
        db.close()
