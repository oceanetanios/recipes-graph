"""Unit tests for the PROFILE helpers of scripts/benchmark.py (no Neo4j needed)."""

from scripts.benchmark import BENCHMARKS, leaf_operators, total_db_hits

PLAN = {
    "operatorType": "ProduceResults@neo4j",
    "dbHits": 0,
    "children": [
        {
            "operatorType": "Filter@neo4j",
            "dbHits": 10,
            "children": [{"operatorType": "NodeByLabelScan@neo4j", "dbHits": 5, "children": []}],
        },
        {"operatorType": "NodeUniqueIndexSeek@neo4j", "dbHits": 2, "children": []},
    ],
}


def test_total_db_hits_adds_every_operator() -> None:
    """Db hits are summed over the whole plan tree."""
    assert total_db_hits(PLAN) == 17


def test_leaf_operators_returns_plan_starting_points() -> None:
    """Only the bottom operators are returned, without the '@neo4j' suffix."""
    assert sorted(leaf_operators(PLAN)) == ["NodeByLabelScan", "NodeUniqueIndexSeek"]


def test_every_scan_version_forces_a_scan_or_avoids_the_index() -> None:
    """The 'scan' query of each benchmark must really bypass the index, otherwise the comparison is meaningless."""
    for name, indexed, scan, _ in BENCHMARKS:
        assert indexed != scan, name
        assert "USING SCAN" in scan or "CONTAINS" in scan, name
