"""Natural-language questions: the NL-to-Cypher agent, and a dry run of its guardrails."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent.graph import LLM_ERRORS, RecipeAgent, validate_cypher
from app.models.agent import AskRequest, AskResponse, CheckRequest, CheckResponse
from app.models.recipes import ErrorMessage

router = APIRouter(prefix="/api/ask", tags=["agent"])


def get_agent(request: Request) -> RecipeAgent:
    """Return the agent created at startup, or answer 503 if no Gemini key is configured."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The question endpoint is not configured: set GEMINI_API_KEY in .env",
        )
    return agent


@router.post(
    "",
    response_model=AskResponse,
    responses={503: {"model": ErrorMessage, "description": "No LLM configured, or the LLM / database is unavailable"}},
)
def ask(body: AskRequest, agent: Annotated[RecipeAgent, Depends(get_agent)]) -> AskResponse:
    """Ask a question about the recipes in plain English.

    Gemini writes a Cypher query, the guardrails check it (read-only, schema, LIMIT, Neo4j EXPLAIN),
    it is retried once if rejected, then run read-only. The answer separates the verified rows from the
    AI-generated summary, and lists every check and step.
    """
    try:
        return AskResponse(**agent.ask(body.question, summarize=body.summarize))
    except LLM_ERRORS as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The language model is unavailable: {type(error).__name__}",
        ) from error


@router.post(
    "/check",
    response_model=CheckResponse,
    responses={503: {"model": ErrorMessage, "description": "The graph database is unavailable"}},
)
def check(body: CheckRequest, request: Request) -> CheckResponse:
    """Run the agent's guardrails on any Cypher query, without running it (no LLM involved).

    Shows exactly what would happen to a query written by the LLM: try MATCH (n) DETACH DELETE n,
    or MATCH (r:Recipe) WHERE r.rating > 4 RETURN r.name (rating is not a Recipe property).
    """
    report = validate_cypher(request.app.state.db, body.cypher)
    return CheckResponse(passed=report.passed, cypher=report.cypher, checks=report.checks)
