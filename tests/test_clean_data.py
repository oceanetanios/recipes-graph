"""Unit tests for the data cleaning rules in scripts/clean_data.py."""

import pandas as pd
import pytest

from scripts.clean_data import (
    basic_clean,
    clean_minutes,
    clean_nutrition,
    clean_rating,
    filter_by_reviews,
    group_tags,
    normalize_ingredient,
    singular,
)

KNOWN = {"egg", "eggs", "onion", "parsley", "fresh parsley", "salt", "pepper", "molasses", "peas", "berry"}


def test_basic_clean_lowercases_and_collapses_spaces() -> None:
    """Names are lowercased, '&' becomes 'and' and repeated spaces disappear."""
    assert basic_clean("  Salt &  Pepper ") == "salt and pepper"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("eggs", "egg"), ("berries", "berry"), ("molasses", "molasses"), ("peas", "peas"), ("onions", "onion")],
)
def test_singular_only_when_singular_is_known(name: str, expected: str) -> None:
    """A plural becomes singular only if the singular form exists in the data."""
    known = KNOWN | {"onions"}
    assert singular(name, known) == expected


def test_normalize_splits_salt_and_pepper() -> None:
    """'salt and pepper' is two ingredients."""
    assert normalize_ingredient("Salt & Pepper", KNOWN) == ["salt", "pepper"]


def test_normalize_strips_known_descriptor() -> None:
    """'fresh parsley' becomes 'parsley' because 'parsley' is a known ingredient."""
    assert normalize_ingredient("fresh parsley", KNOWN) == ["parsley"]


def test_normalize_keeps_unknown_names() -> None:
    """A name with no known shorter form is kept as it is."""
    assert normalize_ingredient("fresh saffron", KNOWN) == ["fresh saffron"]


@pytest.mark.parametrize(("value", "expected"), [(30, 30), (0, None), (2147483647, None)])
def test_clean_minutes(value: int, expected: int | None) -> None:
    """0 and impossible cooking times become None."""
    assert clean_minutes(value) == expected


def test_clean_nutrition_names_values_and_drops_outliers() -> None:
    """The 7 numbers get names, and impossible values become None."""
    result = clean_nutrition([434360.2, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    assert result["calories"] is None
    assert result["carbs_pdv"] == 60.0
    assert len(result) == 7


@pytest.mark.parametrize(("value", "expected"), [(5, 5), (1, 1), (0, None)])
def test_clean_rating(value: int, expected: int | None) -> None:
    """A rating of 0 means 'no rating'."""
    assert clean_rating(value) == expected


def test_group_tags_keeps_only_useful_tags() -> None:
    """Meta tags are dropped, the others go to the right group."""
    tags = ["preparation", "time-to-make", "desserts", "vegan", "italian", "30-minutes-or-less"]
    assert group_tags(tags) == {"courses": ["desserts"], "diets": ["vegan"], "cuisines": ["italian"]}


def test_filter_by_reviews_repeats_until_stable() -> None:
    """Removing a user can push a recipe under the threshold, so filtering repeats."""
    reviews = pd.DataFrame(
        {
            "user_id": [1, 1, 2, 2, 3],
            "recipe_id": [10, 20, 10, 20, 20],
        }
    )
    kept = filter_by_reviews(reviews, min_reviews=2)
    assert sorted(kept["user_id"].unique()) == [1, 2]
    assert sorted(kept["recipe_id"].unique()) == [10, 20]
