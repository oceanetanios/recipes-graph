"""Tests of the LangGraph workflow with a fake LLM and a fake database (no Gemini, no Neo4j)."""

from typing import Any

import pytest
from neo4j.exceptions import CypherSyntaxError

from app.agent.graph import MAX_ATTEMPTS, RecipeAgent, to_json_safe, unsupported_numbers
from app.models.agent import CypherProposal

GOOD_QUERY = "MATCH (r:Recipe) WHERE r.minutes <= 10 RETURN r.name AS recipe, r.minutes AS minutes LIMIT 5"
BAD_QUERY = "MATCH (r:Recipe) WHERE r.rating > 4 RETURN r.name AS recipe"
ROWS = [{"recipe": "toast", "minutes": 5}, {"recipe": "salad", "minutes": 10}]


class FakeMessage:
    """Looks like a LangChain AI message."""

    def __init__(self, text: str) -> None:
        """Store the text of the answer."""
        self.text = text


class FakeProposer:
    """Returns the prepared proposals one after the other, and remembers the prompts."""

    def __init__(self, proposals: list[CypherProposal | None]) -> None:
        """Store the proposals to return."""
        self.proposals = proposals
        self.prompts: list[Any] = []

    def invoke(self, messages: Any) -> CypherProposal | None:
        """Return the next proposal."""
        self.prompts.append(messages)
        return self.proposals.pop(0)


class FakeLlm:
    """A fake chat model: structured output gives the proposals, plain calls give the summary."""

    def __init__(self, proposals: list[CypherProposal | None], summary: str = "Toast and salad are quick.") -> None:
        """Prepare the proposer and the summary."""
        self.proposer = FakeProposer(proposals)
        self.summary = summary

    def with_structured_output(self, _schema: Any) -> FakeProposer:
        """Return the fake proposer."""
        return self.proposer

    def invoke(self, _messages: Any) -> FakeMessage:
        """Return the summary."""
        return FakeMessage(self.summary)


class FakeDb:
    """Answers the values query, EXPLAIN and the real query."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, explain_error: bool = False) -> None:
        """Prepare the rows of the real query and whether EXPLAIN fails."""
        self.rows = ROWS if rows is None else rows
        self.explain_error = explain_error
        self.queries: list[str] = []

    def run_read(self, query: str, params: Any = None, timeout: float | None = None) -> list[dict[str, Any]]:
        """Return values for the prompt, nothing for EXPLAIN, the rows otherwise."""
        del params, timeout
        self.queries.append(query)
        if "COLLECT" in query:
            return [{"diets": ["vegan"], "courses": ["desserts"], "cuisines": ["italian"]}]
        if query.startswith("EXPLAIN"):
            if self.explain_error:
                raise CypherSyntaxError("Invalid input 'RETRUN'")
            return []
        return self.rows


def proposal(cypher: str, answerable: bool = True) -> CypherProposal:
    """Build a proposal like the LLM would."""
    return CypherProposal(answerable=answerable, cypher=cypher, explanation="test", confidence=0.9)


def test_good_question_is_answered() -> None:
    """Generate -> validate -> execute -> summarize, in one attempt."""
    db = FakeDb()
    result = RecipeAgent(db, FakeLlm([proposal(GOOD_QUERY)])).ask("quick recipes?")

    assert result["status"] == "answered"
    assert result["attempts"] == 1
    assert result["results"] == ROWS
    assert result["summary"] == "Toast and salad are quick."
    assert db.queries[-1] == GOOD_QUERY  # the checked query is the one that runs
    assert [step["step"] for step in result["trace"]] == ["generate #1", "validate", "execute", "summarize"]


def test_rejected_query_is_retried_with_the_errors() -> None:
    """A query using a property that does not exist is refused, then fixed on the second attempt."""
    llm = FakeLlm([proposal(BAD_QUERY), proposal(GOOD_QUERY)])

    result = RecipeAgent(FakeDb(), llm).ask("well rated recipes?")

    assert result["status"] == "answered"
    assert result["attempts"] == 2
    retry_prompt = llm.proposer.prompts[1][1][1]
    assert "property .rating on r" in retry_prompt  # the LLM is told what to fix


def test_query_rejected_twice_is_never_run() -> None:
    """After two invalid queries the workflow stops, and the database never sees them."""
    db = FakeDb()
    llm = FakeLlm([proposal("MATCH (r:Recipe) DETACH DELETE r"), proposal("MATCH (n) DETACH DELETE n")])

    result = RecipeAgent(db, llm).ask("delete everything")

    assert result["status"] == "rejected"
    assert result["attempts"] == MAX_ATTEMPTS
    assert not result["results"]
    assert not any("DELETE" in query for query in db.queries)


def test_explain_error_counts_as_a_failed_check() -> None:
    """If Neo4j's EXPLAIN refuses the query, it is treated like a guardrail failure."""
    result = RecipeAgent(FakeDb(explain_error=True), FakeLlm([proposal(GOOD_QUERY), proposal(GOOD_QUERY)])).ask("x")

    assert result["status"] == "rejected"
    assert any(check["name"] == "explain" and not check["passed"] for check in result["checks"])


def test_declined_question_stops_immediately() -> None:
    """Off-topic questions are declined by the model, nothing is validated or run."""
    db = FakeDb()
    result = RecipeAgent(db, FakeLlm([proposal("", answerable=False)])).ask("capital of France?")

    assert result["status"] == "not_answerable"
    assert result["cypher"] is None
    assert not any(query.startswith("EXPLAIN") for query in db.queries)


def test_missing_structured_output_is_rejected_then_retried() -> None:
    """If the model returns nothing usable, the workflow retries instead of crashing."""
    result = RecipeAgent(FakeDb(), FakeLlm([None, proposal(GOOD_QUERY)])).ask("quick recipes?")

    assert result["status"] == "answered"
    assert result["attempts"] == 2


def test_same_question_is_answered_from_the_cache() -> None:
    """Asking twice (even with other spaces or capitals) costs one LLM call, and says it was cached."""
    llm = FakeLlm([proposal(GOOD_QUERY)])
    agent = RecipeAgent(FakeDb(), llm)

    first = agent.ask("Quick recipes?")
    second = agent.ask("  quick   RECIPES? ")

    assert len(llm.proposer.prompts) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["results"] == first["results"]


def test_summary_can_be_skipped() -> None:
    """summarize=False stops after execute: rows but no summary."""
    result = RecipeAgent(FakeDb(), FakeLlm([proposal(GOOD_QUERY)])).ask("quick recipes?", summarize=False)

    assert result["results"] == ROWS
    assert result["summary"] is None
    assert result["trace"][-1]["step"] == "execute"


def test_no_rows_means_no_summary() -> None:
    """With no results there is nothing to summarize (so nothing to invent)."""
    result = RecipeAgent(FakeDb(rows=[]), FakeLlm([proposal(GOOD_QUERY)])).ask("quick recipes?")

    assert result["status"] == "no_results"
    assert result["summary"] is None


def test_summary_with_invented_number_is_flagged() -> None:
    """A summary mentioning a number that is not in the rows fails the grounding check."""
    llm = FakeLlm([proposal(GOOD_QUERY)], summary="There are 42 quick recipes.")

    result = RecipeAgent(FakeDb(), llm).ask("quick recipes?")

    grounding = [check for check in result["checks"] if check["name"] == "summary grounding"][0]
    assert not grounding["passed"]
    assert "42" in grounding["detail"]


@pytest.mark.parametrize(
    ("summary", "invented"),
    [("Toast takes 5 minutes.", []), ("2 recipes found.", []), ("Toast takes 7 minutes.", ["7"])],
)
def test_unsupported_numbers(summary: str, invented: list[str]) -> None:
    """Numbers from the rows, and the number of rows, are allowed; others are reported."""
    assert unsupported_numbers(summary, ROWS) == invented


def test_to_json_safe_converts_dates() -> None:
    """Neo4j dates become ISO text so the answer can be sent as JSON."""

    class FakeNeo4jDate:
        """Has iso_format() like neo4j.time.Date."""

        def iso_format(self) -> str:
            """Return a fixed date."""
            return "2005-09-16"

    assert to_json_safe({"when": FakeNeo4jDate(), "tags": ("a", "b")}) == {"when": "2005-09-16", "tags": ["a", "b"]}
