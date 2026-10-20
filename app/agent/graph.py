"""The NL-to-Cypher agent, built as a LangGraph workflow.

    generate --> validate --> execute --> summarize
        ^           |             |
        +-- retry --+ (once)      +--> end when there are no rows
                    +--> end when rejected twice

Each step adds a line to the trace, so the answer shows how it was produced.
"""

import json
import operator
import re
from datetime import date, datetime
from typing import Annotated, Any, TypedDict

import httpx
from google.genai.errors import APIError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from langgraph.graph import END, START, StateGraph
from neo4j.exceptions import Neo4jError

from app.agent.guardrails import GuardrailReport, check_cypher
from app.agent.prompts import CYPHER_SYSTEM_PROMPT, RETRY_PROMPT, SUMMARY_SYSTEM_PROMPT
from app.agent.schema import schema_text
from app.database.neo4j_client import Neo4jClient
from app.models.agent import CypherProposal

MAX_ATTEMPTS = 2
QUERY_TIMEOUT_SECONDS = 10
ROWS_FOR_SUMMARY = 25

# Errors meaning "the language model could not be reached or refused the request" (the API answers 503).
LLM_ERRORS = (ChatGoogleGenerativeAIError, APIError, httpx.HTTPError)

VALUES_QUERY = """
RETURN COLLECT { MATCH (d:Diet) RETURN d.name ORDER BY d.name } AS diets,
       COLLECT { MATCH (c:Course) RETURN c.name ORDER BY c.name } AS courses,
       COLLECT { MATCH (c:Cuisine) RETURN c.name ORDER BY c.name } AS cuisines
"""
NUMBER = re.compile(r"\d+(?:\.\d+)?")


class AgentState(TypedDict, total=False):
    """Everything the workflow knows about one question. The trace list grows, the rest is overwritten."""

    question: str
    summarize: bool
    attempts: int
    proposal: CypherProposal | None
    report: GuardrailReport | None
    rows: list[dict[str, Any]]
    summary: str | None
    status: str
    trace: Annotated[list[dict[str, str]], operator.add]


def to_json_safe(value: Any) -> Any:
    """Turn Neo4j values (dates, nodes as dicts, lists) into plain JSON values."""
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)) or hasattr(value, "iso_format"):
        return value.iso_format() if hasattr(value, "iso_format") else value.isoformat()
    return str(value)


def unsupported_numbers(summary: str, rows: list[dict[str, Any]]) -> list[str]:
    """Numbers written in the summary that appear nowhere in the rows: a simple hallucination check."""
    known = set(NUMBER.findall(json.dumps(rows))) | {str(len(rows))}
    found = NUMBER.findall(summary)
    return sorted({number for number in found if number not in known and number.rstrip("0").rstrip(".") not in known})


def validate_cypher(db: Neo4jClient, cypher: str) -> GuardrailReport:
    """All the checks a query must pass before it may run: the static guardrails, then Neo4j's EXPLAIN.

    EXPLAIN makes Neo4j parse and plan the query without running it, so syntax errors and invalid
    constructs are caught with zero risk.
    """
    report = check_cypher(cypher)
    if report.passed:
        try:
            db.run_read("EXPLAIN " + report.cypher)
            report.add("explain", True, "Neo4j accepted the query plan")
        except Neo4jError as error:
            report.add("explain", False, f"Neo4j rejected the query: {(error.message or str(error)).splitlines()[0]}")
    return report


def build_llm(api_key: str, model: str) -> ChatGoogleGenerativeAI:
    """Create a Gemini chat model. Temperature 0: the same question should give the same query.

    Gemini sometimes answers 503 "high demand": the client retries once before the next fallback model is tried.
    """
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0, timeout=30, max_retries=1)


def build_agent(db: Neo4jClient, api_key: str, model: str, fallback_models: str) -> "RecipeAgent":
    """Create the agent with its main model and the comma-separated fallback models."""
    fallbacks = [build_llm(api_key, name.strip()) for name in fallback_models.split(",") if name.strip()]
    return RecipeAgent(db, build_llm(api_key, model), fallbacks)


class RecipeAgent:
    """Answers questions in plain English by writing, checking and running Cypher queries."""

    def __init__(self, db: Neo4jClient, llm: Any, fallback_llms: list[Any] | None = None) -> None:
        """Keep the database and the LLMs, and compile the workflow once.

        When the main model fails (overloaded, timeout, daily quota used up), the same request goes to the
        fallback models, in order. Answers are cached by question, so asking twice costs no LLM call.
        """
        self._db = db
        self._llm = llm
        self._proposer = llm.with_structured_output(CypherProposal)
        if fallback_llms:
            self._llm = llm.with_fallbacks(fallback_llms, exceptions_to_handle=LLM_ERRORS)
            self._proposer = self._proposer.with_fallbacks(
                [fallback.with_structured_output(CypherProposal) for fallback in fallback_llms],
                exceptions_to_handle=LLM_ERRORS,
            )
        self._system_prompt: str | None = None
        self._cache: dict[tuple[str, bool], dict[str, Any]] = {}
        self._workflow = self._build_workflow()

    def _build_workflow(self) -> Any:
        """Wire the four steps together (see the module docstring)."""
        workflow = StateGraph(AgentState)
        workflow.add_node("generate", self._generate)
        workflow.add_node("validate", self._validate)
        workflow.add_node("execute", self._execute)
        workflow.add_node("summarize", self._summarize)
        workflow.add_edge(START, "generate")
        workflow.add_conditional_edges("generate", self._after_generate, ["validate", END])
        workflow.add_conditional_edges("validate", self._after_validate, ["execute", "generate", END])
        workflow.add_conditional_edges("execute", self._after_execute, ["summarize", END])
        workflow.add_edge("summarize", END)
        return workflow.compile()

    def ask(self, question: str, summarize: bool = True) -> dict[str, Any]:
        """Run the whole workflow for one question and return the fields of AskResponse.

        summarize=False skips the summary step: half the LLM calls, only the verified rows come back.
        """
        key = (" ".join(question.lower().split()), summarize)
        if key in self._cache:
            return {**self._cache[key], "question": question, "cached": True}
        state = self._workflow.invoke({"question": question, "summarize": summarize, "attempts": 0, "trace": []})
        proposal, report = state.get("proposal"), state.get("report")
        answer = {
            "question": question,
            "status": state["status"],
            "cypher": report.cypher if report and report.cypher else None,
            "results": state.get("rows", []),
            "summary": state.get("summary"),
            "explanation": proposal.explanation if proposal else None,
            "llm_confidence": proposal.confidence if proposal else None,
            "checks": report.checks if report else [],
            "attempts": state["attempts"],
            "trace": state["trace"],
            "cached": False,
        }
        self._cache[key] = answer
        return answer

    def system_prompt(self) -> str:
        """The Cypher prompt with the schema and the real diet, course and cuisine names (read once)."""
        if self._system_prompt is None:
            values = self._db.run_read(VALUES_QUERY)[0]
            self._system_prompt = CYPHER_SYSTEM_PROMPT.format(
                schema=schema_text(),
                diets=", ".join(values["diets"]),
                courses=", ".join(values["courses"]),
                cuisines=", ".join(values["cuisines"]),
            )
        return self._system_prompt

    # --- steps -------------------------------------------------------------------------------

    def _generate(self, state: AgentState) -> AgentState:
        """Ask the LLM for a query (structured output). On a retry, tell it what was wrong."""
        message = state["question"]
        report = state.get("report")
        if report is not None:
            message += "\n\n" + RETRY_PROMPT.format(cypher=report.cypher, errors="\n".join(report.errors))
        proposal = self._proposer.invoke([("system", self.system_prompt()), ("human", message)])
        attempt = state["attempts"] + 1
        if proposal is None:
            detail = "the model did not return a valid structured answer"
        elif not proposal.answerable:
            detail = f"the model declined: {proposal.explanation}"
        else:
            detail = f"query written (self-reported confidence {proposal.confidence:.2f})"
        return {
            "attempts": attempt,
            "proposal": proposal,
            "status": "not_answerable" if proposal is not None and not proposal.answerable else "generated",
            "trace": [{"step": f"generate #{attempt}", "detail": detail}],
        }

    def _validate(self, state: AgentState) -> AgentState:
        """Static guardrails first; if they pass, ask Neo4j to EXPLAIN the query (checks it without running it)."""
        proposal = state["proposal"]
        if proposal is None:
            report = check_cypher("")
            report.add("structured output", False, "the model did not return a valid structured answer")
        else:
            report = validate_cypher(self._db, proposal.cypher)
        status = "valid" if report.passed else "invalid"
        detail = "all checks passed" if report.passed else "; ".join(report.errors)
        if not report.passed and state["attempts"] >= MAX_ATTEMPTS:
            status = "rejected"
            detail += " (no attempts left, the query is never run)"
        return {"report": report, "status": status, "trace": [{"step": "validate", "detail": detail}]}

    def _execute(self, state: AgentState) -> AgentState:
        """Run the checked query in a read-only transaction with a timeout."""
        report = state["report"]
        try:
            rows = to_json_safe(self._db.run_read(report.cypher, timeout=QUERY_TIMEOUT_SECONDS))
        except Neo4jError as error:
            report.add("execution", False, f"the query failed while running: {(error.message or str(error))[:200]}")
            return {
                "report": report,
                "status": "rejected",
                "rows": [],
                "trace": [{"step": "execute", "detail": "failed"}],
            }
        return {
            "rows": rows,
            "status": "answered" if rows else "no_results",
            "trace": [{"step": "execute", "detail": f"{len(rows)} rows returned by Neo4j"}],
        }

    def _summarize(self, state: AgentState) -> AgentState:
        """Let the LLM write a short answer from the rows, then check it does not invent numbers."""
        rows = state["rows"][:ROWS_FOR_SUMMARY]
        message = (
            f"Question: {state['question']}\n"
            "Cypher query that produced the rows (its filters were applied by the database):\n"
            f"{state['report'].cypher}\n"
            f"Rows (JSON): {json.dumps(rows)}"
        )
        summary = self._llm.invoke([("system", SUMMARY_SYSTEM_PROMPT), ("human", message)]).text.strip()
        invented = unsupported_numbers(summary, rows)
        report = state["report"]
        report.add(
            "summary grounding",
            not invented,
            f"the summary mentions numbers not in the results: {', '.join(invented)}" if invented else "ok",
        )
        return {
            "summary": summary,
            "report": report,
            "trace": [{"step": "summarize", "detail": "summary written" + (" (flagged)" if invented else "")}],
        }

    # --- routing -----------------------------------------------------------------------------

    @staticmethod
    def _after_generate(state: AgentState) -> str:
        """Stop when the model declined, otherwise validate the query."""
        return END if state["status"] == "not_answerable" else "validate"

    @staticmethod
    def _after_validate(state: AgentState) -> str:
        """Run a valid query, retry an invalid one once, stop after that."""
        if state["status"] == "valid":
            return "execute"
        return "generate" if state["status"] == "invalid" else END

    @staticmethod
    def _after_execute(state: AgentState) -> str:
        """Summarize only when there are rows and a summary was asked for."""
        return "summarize" if state.get("rows") and state.get("summarize", True) else END
