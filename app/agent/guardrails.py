"""Guardrails: checks every Cypher query written by the LLM before it can reach the database.

The LLM output is treated as untrusted. A query is only run if it passes all the static checks below,
then Neo4j's own EXPLAIN (done in the agent), and it always runs in a read-only transaction with a timeout.
"""

import re
from dataclasses import dataclass, field

from app.agent.schema import LABELS, NODE_PROPERTIES, PROPERTIES, RELATIONSHIP_TYPES, RELATIONSHIPS

DEFAULT_LIMIT = 25
MAX_LIMIT = 50

# Clauses that change data, the schema, users or the database in use.
WRITE_KEYWORDS = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    "LOAD",
    "ALTER",
    "RENAME",
    "GRANT",
    "REVOKE",
    "DENY",
    "TERMINATE",
    "USE",
)
ALLOWED_PROCEDURES = frozenset({"db.index.fulltext.querynodes"})

STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
CODE_FENCE = re.compile(r"^```(?:cypher)?\s*|\s*```$", re.IGNORECASE)
WRITE_PATTERN = re.compile(r"\b(" + "|".join(WRITE_KEYWORDS) + r")\b", re.IGNORECASE)
PROCEDURE_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.IGNORECASE)
NAMESPACE = re.compile(r"\b(apoc|dbms|gds)\s*\.", re.IGNORECASE)
NODE_LABEL = re.compile(r"\(\s*(?:[A-Za-z_]\w*)?\s*((?::\s*[A-Za-z_]\w*\s*)+)")
REL_TYPE = re.compile(r"\[\s*(?:[A-Za-z_]\w*)?\s*:\s*([A-Za-z_]\w*(?:\s*\|\s*:?\s*[A-Za-z_]\w*)*)")
LABEL_PREDICATE = re.compile(r"\b[a-z_]\w*:([A-Za-z_]\w*)")  # WHERE r:Recipe (no space, unlike map keys)
PROPERTY_ACCESS = re.compile(r"\b([a-z_]\w*)\.([A-Za-z_]\w*)\b")
NODE_VARIABLE = re.compile(r"\(\s*([a-z_]\w*)\s*:\s*([A-Za-z_]\w*)")
REL_VARIABLE = re.compile(r"\[\s*([a-z_]\w*)\s*:\s*([A-Za-z_]\w*)")
LIMIT_AT_END = re.compile(r"\bLIMIT\s+(\S+)\s*$", re.IGNORECASE)


@dataclass
class GuardrailReport:
    """Result of the checks: the (possibly fixed) query, and every check with its outcome."""

    cypher: str
    checks: list[dict[str, str | bool]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when every check passed."""
        return all(check["passed"] for check in self.checks)

    @property
    def errors(self) -> list[str]:
        """Details of the failed checks, to explain the rejection (and to tell the LLM what to fix)."""
        return [str(check["detail"]) for check in self.checks if not check["passed"]]

    def add(self, name: str, passed: bool, detail: str) -> None:
        """Record one check."""
        self.checks.append({"name": name, "passed": passed, "detail": detail})


def strip_code(cypher: str) -> str:
    """Remove string literals and comments, so keywords inside text ('Set aside') are not mistaken for clauses."""
    return STRING_LITERAL.sub("''", COMMENT.sub(" ", cypher))


def check_cypher(raw: str) -> GuardrailReport:
    """Run every static check on an LLM-written query and enforce a LIMIT."""
    cypher = CODE_FENCE.sub("", raw.strip()).strip().rstrip(";").strip()
    report = GuardrailReport(cypher=cypher)
    code = strip_code(cypher)

    report.add("not empty", bool(code.strip()), "the query is empty" if not code.strip() else "ok")

    statements = ";" not in code
    report.add("single statement", statements, "ok" if statements else "only one statement is allowed (found ';')")

    writes = sorted({match.upper() for match in WRITE_PATTERN.findall(code)})
    report.add("read-only", not writes, f"write clauses are not allowed: {', '.join(writes)}" if writes else "ok")

    procedures = [name for name in PROCEDURE_CALL.findall(code) if name.lower() not in ALLOWED_PROCEDURES]
    namespaces = sorted({name.lower() for name in NAMESPACE.findall(code)} - {"db"})
    blocked = procedures + [f"{name}.*" for name in namespaces]
    report.add(
        "allowed procedures",
        not blocked,
        f"only db.index.fulltext.queryNodes may be called, not: {', '.join(blocked)}" if blocked else "ok",
    )

    has_return = re.search(r"\bRETURN\b", code, re.IGNORECASE) is not None
    report.add("returns data", has_return, "ok" if has_return else "the query must end with RETURN")

    _check_schema(code, report)
    report.cypher = _enforce_limit(cypher, code, report)
    return report


def _variable_properties(code: str) -> dict[str, set[str]]:
    """Map each variable bound to a known label or relationship type, e.g. (r:Recipe), to its allowed properties."""
    allowed: dict[str, set[str]] = {}
    for variable, label in NODE_VARIABLE.findall(code):
        allowed.setdefault(variable, set()).update(NODE_PROPERTIES.get(label, []))
    for variable, rel_type in REL_VARIABLE.findall(code):
        allowed.setdefault(variable, set()).update(RELATIONSHIPS[rel_type][2] if rel_type in RELATIONSHIPS else [])
    return allowed


def _check_schema(code: str, report: GuardrailReport) -> None:
    """Every label, relationship type and property used must exist in the schema (no invented names).

    Properties are checked per variable: r.rating is refused when r is a :Recipe, because rating
    belongs to the REVIEWED relationship. Variables with no label (like YIELD node) are checked
    against all the properties of the schema.
    """
    without_calls = PROCEDURE_CALL.sub("CALL x(", code)
    labels = {
        label.strip() for group in NODE_LABEL.findall(without_calls) for label in group.split(":") if label.strip()
    }
    labels |= set(LABEL_PREDICATE.findall(without_calls)) - RELATIONSHIP_TYPES
    rel_types = {
        rel.strip(" :") for group in REL_TYPE.findall(without_calls) for rel in group.split("|") if rel.strip(" :")
    }
    allowed = _variable_properties(without_calls)
    wrong_properties = sorted(
        {
            f"property .{prop}" + (f" on {variable}" if variable in allowed else "")
            for variable, prop in PROPERTY_ACCESS.findall(without_calls)
            if prop not in allowed.get(variable, PROPERTIES)
        }
    )

    unknown = (
        [f"label :{name}" for name in sorted(labels - LABELS)]
        + [f"relationship :{name}" for name in sorted(rel_types - RELATIONSHIP_TYPES)]
        + wrong_properties
    )
    report.add(
        "schema",
        not unknown,
        f"not in the graph schema: {', '.join(unknown)}" if unknown else "ok",
    )


def _enforce_limit(cypher: str, code: str, report: GuardrailReport) -> str:
    """Add LIMIT when missing and lower it when too high, so one question can never return the whole graph."""
    match = LIMIT_AT_END.search(code)
    if match is None:
        report.add("limit", True, f"no LIMIT given, LIMIT {DEFAULT_LIMIT} added")
        return f"{cypher}\nLIMIT {DEFAULT_LIMIT}"
    value = match.group(1)
    if not value.isdigit():
        report.add("limit", False, f"LIMIT must be a number, not {value}")
        return cypher
    if int(value) > MAX_LIMIT:
        report.add("limit", True, f"LIMIT {value} lowered to {MAX_LIMIT}")
        return LIMIT_AT_END.sub(f"LIMIT {MAX_LIMIT}", cypher)
    report.add("limit", True, f"LIMIT {value} kept")
    return cypher
