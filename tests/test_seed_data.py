"""Unit tests for the helpers of scripts/seed_data.py (no Neo4j needed)."""

from pathlib import Path

from scripts.seed_data import LOAD_STEPS, SCHEMA, batches, read_rows


def test_read_rows_turns_empty_cells_into_none(tmp_path: Path) -> None:
    """Empty cells become None, but text such as 'NA' is kept as text."""
    path = tmp_path / "table.csv"
    path.write_text("id,minutes,description\n1,,NA\n2,30,tasty\n", encoding="utf-8")

    rows = read_rows(path)

    assert rows[0] == {"id": 1, "minutes": None, "description": "NA"}
    assert rows[1]["minutes"] == 30


def test_read_rows_keeps_whole_numbers_as_integers(tmp_path: Path) -> None:
    """A whole-number column with empty cells must give 30, not 30.0."""
    path = tmp_path / "reviews.csv"
    path.write_text("rating,calories\n,105.7\n5,200.5\n", encoding="utf-8")

    rows = read_rows(path)

    assert isinstance(rows[1]["rating"], int)
    assert rows[1]["calories"] == 200.5


def test_read_rows_parses_steps_as_a_list(tmp_path: Path) -> None:
    """The steps column is stored as JSON text and must come back as a list."""
    path = tmp_path / "recipes.csv"
    path.write_text('id,steps\n1,"[""mix"", ""bake""]"\n', encoding="utf-8")

    assert read_rows(path)[0]["steps"] == ["mix", "bake"]


def test_batches_cover_all_rows_without_overlap() -> None:
    """25 rows in batches of 10 give 10 + 10 + 5."""
    rows = [{"id": number} for number in range(25)]

    sizes = [len(batch) for batch in batches(rows, size=10)]

    assert sizes == [10, 10, 5]


def test_every_load_step_uses_the_rows_parameter() -> None:
    """Each import query must be parameterized with $rows (no values pasted into the Cypher text)."""
    for _, filename, query in LOAD_STEPS:
        assert "$rows" in query, filename


def test_schema_is_idempotent() -> None:
    """Every schema statement can be run twice without failing."""
    assert all("IF NOT EXISTS" in statement for statement in SCHEMA)
