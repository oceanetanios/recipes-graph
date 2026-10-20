"""Pydantic models for the natural-language question endpoint."""

from typing import Any, Literal

from pydantic import BaseModel, Field

AskStatus = Literal["answered", "no_results", "not_answerable", "rejected"]


class AskRequest(BaseModel):
    """A question in plain English."""

    question: str = Field(min_length=3, max_length=300, examples=["Vegetarian italian dinners under 30 minutes"])
    summarize: bool = Field(
        default=True, description="Also write a short AI summary of the rows (one more LLM call, uses more quota)"
    )


class CheckRequest(BaseModel):
    """A Cypher query to check with the guardrails (it is never run)."""

    cypher: str = Field(min_length=1, max_length=5000, examples=["MATCH (n) DETACH DELETE n"])


class CheckResponse(BaseModel):
    """What the guardrails decided about a query."""

    passed: bool = Field(description="True if the agent would be allowed to run this query")
    cypher: str = Field(description="The query after the automatic fixes (fences removed, LIMIT added or lowered)")
    checks: list["Check"]


class CypherProposal(BaseModel):
    """What the LLM must return (structured output): never free text."""

    answerable: bool = Field(description="False if the question is off-topic or asks to modify data")
    cypher: str = Field(default="", description="One read-only Cypher query, empty if not answerable")
    explanation: str = Field(description="One sentence: what the query does, or why it is not answerable")
    confidence: float = Field(ge=0, le=1, description="How sure the model is that the query answers the question")


class Check(BaseModel):
    """One guardrail check and its outcome."""

    name: str
    passed: bool
    detail: str


CheckResponse.model_rebuild()


class TraceStep(BaseModel):
    """One step of the workflow, to show how the answer was produced."""

    step: str
    detail: str


class AskResponse(BaseModel):
    """The answer, keeping verified facts (database rows) apart from AI-generated text."""

    question: str
    status: AskStatus = Field(
        description="answered / no_results: query ran; not_answerable: the model declined; "
        "rejected: the generated query failed the guardrails twice and was never run"
    )
    cypher: str | None = Field(description="The query that was run (or the last rejected one)")
    results: list[dict[str, Any]] = Field(description="VERIFIED: rows returned by Neo4j, unchanged")
    summary: str | None = Field(description="AI-GENERATED: short answer written by the LLM from the rows only")
    explanation: str | None = Field(description="AI-GENERATED: what the model says its query does")
    llm_confidence: float | None = Field(description="Self-reported by the LLM (0-1): a hint, not a guarantee")
    checks: list[Check] = Field(description="Guardrail checks of the last generated query")
    attempts: int = Field(description="Number of queries generated (2 means the first one was rejected)")
    trace: list[TraceStep]
    cached: bool = Field(description="True when the same question was already answered: no LLM call was made")
