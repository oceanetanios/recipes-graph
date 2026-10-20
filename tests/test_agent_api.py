"""API tests for POST /api/ask, with a fake agent (no Gemini, no Neo4j)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai.errors import ServerError

from app.main import app
from app.routers.agent import get_agent

ANSWER = {
    "question": "quick recipes?",
    "status": "answered",
    "cypher": "MATCH (r:Recipe) RETURN r.name AS recipe LIMIT 5",
    "results": [{"recipe": "toast"}],
    "summary": "Toast is quick.",
    "explanation": "Finds recipes.",
    "llm_confidence": 0.9,
    "checks": [{"name": "read-only", "passed": True, "detail": "ok"}],
    "attempts": 1,
    "trace": [{"step": "generate #1", "detail": "query written"}],
    "cached": False,
}


class FakeAgent:
    """Returns a fixed answer, or raises like an overloaded Gemini."""

    overloaded = False
    last_summarize: bool | None = None

    def ask(self, question: str, summarize: bool = True) -> dict[str, Any]:
        """Answer every question the same way (without summary when summarize is False)."""
        FakeAgent.last_summarize = summarize
        if self.overloaded:
            raise ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
        return {**ANSWER, "question": question, "summary": ANSWER["summary"] if summarize else None}


@pytest.fixture(name="client")
def client_fixture() -> Iterator[TestClient]:
    """A test client using the fake agent."""
    FakeAgent.overloaded = False
    app.dependency_overrides[get_agent] = FakeAgent
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_ask_returns_verified_rows_and_ai_summary(client: TestClient) -> None:
    """The answer keeps the rows (verified) and the summary (AI) in separate fields."""
    response = client.post("/api/ask", json={"question": "quick recipes?"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [{"recipe": "toast"}]
    assert body["summary"] == "Toast is quick."
    assert body["checks"][0]["passed"] is True


def test_ask_can_skip_the_summary(client: TestClient) -> None:
    """summarize=false is passed to the agent and gives no summary."""
    response = client.post("/api/ask", json={"question": "quick recipes?", "summarize": False})

    assert FakeAgent.last_summarize is False
    assert response.json()["summary"] is None


@pytest.mark.parametrize("question", ["", "hi", "x" * 301])
def test_ask_validates_question_length(client: TestClient, question: str) -> None:
    """Questions must be 3 to 300 characters long."""
    assert client.post("/api/ask", json={"question": question}).status_code == 422


def test_ask_gives_503_when_the_llm_is_overloaded(client: TestClient) -> None:
    """A Gemini outage becomes a clear 503, not a 500."""
    FakeAgent.overloaded = True

    response = client.post("/api/ask", json={"question": "quick recipes?"})

    assert response.status_code == 503
    assert "language model" in response.json()["detail"]


class ExplainOnlyDb:
    """Accepts every EXPLAIN (the static guardrails are what is tested here)."""

    def run_read(self, query: str, params: Any = None, timeout: float | None = None) -> list[dict[str, Any]]:
        """Pretend Neo4j accepted the plan."""
        del params, timeout
        assert query.startswith("EXPLAIN")
        return []

    def close(self) -> None:
        """Nothing to close."""


def test_check_rejects_a_write_query() -> None:
    """The dry-run endpoint shows the guardrails refusing a destructive query, without running it."""
    with TestClient(app) as test_client:
        app.state.db = ExplainOnlyDb()
        response = test_client.post("/api/ask/check", json={"cypher": "MATCH (n) DETACH DELETE n"})

    body = response.json()
    assert response.status_code == 200
    assert body["passed"] is False
    assert any(check["name"] == "read-only" and not check["passed"] for check in body["checks"])


def test_check_accepts_a_valid_query_and_adds_a_limit() -> None:
    """A correct query passes, with the LIMIT the agent would add."""
    with TestClient(app) as test_client:
        app.state.db = ExplainOnlyDb()
        response = test_client.post("/api/ask/check", json={"cypher": "MATCH (r:Recipe) RETURN r.name AS recipe"})

    body = response.json()
    assert body["passed"] is True
    assert body["cypher"].endswith("LIMIT 25")
    assert any(check["name"] == "explain" and check["passed"] for check in body["checks"])


def test_ask_gives_503_without_gemini_key() -> None:
    """Without a configured agent the endpoint explains what is missing."""
    with TestClient(app) as test_client:
        app.state.agent = None
        response = test_client.post("/api/ask", json={"question": "quick recipes?"})

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]
