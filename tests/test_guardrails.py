"""Unit tests for the Cypher guardrails (app/agent/guardrails.py)."""

import pytest

from app.agent.guardrails import DEFAULT_LIMIT, MAX_LIMIT, check_cypher


def failed(cypher: str) -> list[str]:
    """Names of the checks that failed for a query."""
    return [str(check["name"]) for check in check_cypher(cypher).checks if not check["passed"]]


def test_valid_query_passes_and_gets_a_limit() -> None:
    """A correct read query passes every check, and a LIMIT is added when missing."""
    report = check_cypher("MATCH (r:Recipe)-[:CONTAINS]->(i:Ingredient {name: 'egg'}) RETURN r.name AS recipe")

    assert report.passed
    assert report.cypher.endswith(f"LIMIT {DEFAULT_LIMIT}")


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (r:Recipe) DETACH DELETE r",
        "MATCH (r:Recipe) SET r.name = 'x' RETURN r.name",
        "CREATE (:Recipe {id: 1}) RETURN 1",
        "MERGE (i:Ingredient {name: 'x'}) RETURN i.name",
        "MATCH (r:Recipe) REMOVE r.name RETURN r.id",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "MATCH (r:Recipe) FOREACH (x IN [1] | SET r.id = x) RETURN r.id",
        "match (r:Recipe) detach delete r",
    ],
)
def test_write_queries_are_rejected(cypher: str) -> None:
    """Any clause that changes the database is refused, whatever the letter case."""
    assert "read-only" in failed(cypher)


def test_keywords_inside_text_or_comments_are_allowed() -> None:
    """'remove' in a string or 'DELETE' in a comment is text, not a clause."""
    cypher = (
        "MATCH (r:Recipe) // never DELETE\nWHERE r.description CONTAINS 'set aside, remove from heat' RETURN r.name"
    )

    assert check_cypher(cypher).passed


def test_several_statements_are_rejected() -> None:
    """A second statement hidden after ';' is refused."""
    assert "single statement" in failed("MATCH (r:Recipe) RETURN r.name; MATCH (n) DETACH DELETE n")


@pytest.mark.parametrize(
    ("cypher", "message"),
    [
        ("MATCH (r:Recipe)-[:HAS_INGREDIENT]->(i:Ingredient) RETURN r.name", "relationship :HAS_INGREDIENT"),
        ("MATCH (f:Food) RETURN f.name", "label :Food"),
        ("MATCH (r:Recipe) WHERE r.rating > 4 RETURN r.name", "property .rating on r"),
        ("MATCH (r:Recipe) WHERE r:Secret RETURN r.name", "label :Secret"),
    ],
)
def test_names_outside_the_schema_are_rejected(cypher: str, message: str) -> None:
    """Invented labels, relationship types and properties (the usual LLM mistakes) are caught."""
    report = check_cypher(cypher)

    assert not report.passed
    assert any(message in error for error in report.errors)


def test_rating_is_allowed_on_the_reviewed_relationship() -> None:
    """rating is refused on a Recipe but accepted on REVIEWED, where it really is."""
    assert check_cypher("MATCH (:User)-[rev:REVIEWED]->(r:Recipe) RETURN r.name, avg(rev.rating) AS rating").passed


def test_only_the_fulltext_procedure_can_be_called() -> None:
    """db.index.fulltext.queryNodes is allowed, admin and plugin procedures are not."""
    assert check_cypher(
        "CALL db.index.fulltext.queryNodes('recipe_name_fulltext', 'lemon') YIELD node RETURN node.name"
    ).passed
    assert "allowed procedures" in failed("CALL dbms.listUsers() YIELD username RETURN username")
    assert "allowed procedures" in failed("MATCH (r:Recipe) RETURN apoc.text.join([r.name], ',') AS x")


def test_limit_is_lowered_when_too_high() -> None:
    """LIMIT 1000 becomes the maximum allowed."""
    report = check_cypher("MATCH (r:Recipe) RETURN r.name LIMIT 1000")

    assert report.passed
    assert report.cypher.endswith(f"LIMIT {MAX_LIMIT}")


def test_limit_must_be_a_number() -> None:
    """A parameter as LIMIT cannot be checked, so it is refused."""
    assert "limit" in failed("MATCH (r:Recipe) RETURN r.name LIMIT $n")


def test_code_fences_and_semicolon_are_removed() -> None:
    """LLMs often wrap queries in ```cypher fences and end them with ';'."""
    report = check_cypher("```cypher\nMATCH (r:Recipe) RETURN r.name LIMIT 5;\n```")

    assert report.passed
    assert report.cypher == "MATCH (r:Recipe) RETURN r.name LIMIT 5"


def test_query_must_return_something() -> None:
    """A query without RETURN is refused."""
    assert "returns data" in failed("MATCH (r:Recipe) WITH r.name AS name")
