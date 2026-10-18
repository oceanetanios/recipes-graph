"""Tests for the /health endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_expected_shape() -> None:
    """GET /health answers 200 or 503 with exactly the keys status and neo4j."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code in (200, 503)
    assert set(response.json().keys()) == {"status", "neo4j"}
