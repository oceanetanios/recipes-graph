"""Clean the raw Food.com CSVs and write the files that the Neo4j import reads.

Usage (from the project root):
    python -m scripts.clean_data                  # recipes and users with at least 5 reviews
    python -m scripts.clean_data --min-reviews 3

Input:  data/raw/RAW_recipes.csv, data/raw/RAW_interactions.csv
Output: data/processed/*.csv (one file per node type and per relationship type)
"""

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from app.agent.schema import NUTRITION_PROPERTIES as NUTRITION_COLUMNS
from app.ingredient_names import normalize_name as basic_clean
from app.ingredient_names import singular_candidates
from scripts.tag_groups import COURSES, CUISINE_PARENT, CUISINES, DIETS

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed")

MAX_MINUTES = 7 * 24 * 60  # more than a week is a placeholder, not a real cooking time
MAX_CALORIES = 10_000
MAX_PDV = 10_000  # percent of daily value

# Words removed from the start of an ingredient name, only if the shorter name is a known ingredient.
LEADING_DESCRIPTORS = {"fresh", "freshly", "large", "medium", "small", "chopped", "minced", "diced", "sliced"}
SALT_AND_PEPPER = re.compile(r"^(?P<salt>.*\bsalt) and (?P<pepper>.*\bpepper)$")


def singular(name: str, known: set[str]) -> str:
    """Return the singular form of a plural name if that singular form is itself a known ingredient."""
    for candidate in singular_candidates(name):
        if candidate in known:
            return candidate
    return name


def strip_descriptors(name: str, known: set[str]) -> str:
    """Drop leading words like 'fresh' or 'large' while the remaining name is a known ingredient."""
    words = name.split(" ")
    while len(words) > 1 and words[0] in LEADING_DESCRIPTORS and " ".join(words[1:]) in known:
        words = words[1:]
    return " ".join(words)


def normalize_ingredient(raw: str, known: set[str]) -> list[str]:
    """Turn one raw ingredient string into one or more clean ingredient names.

    'salt and pepper' becomes two ingredients. The other rules only apply when they lead to a
    name that already exists in the data, so rare or unusual names are never invented.
    """
    name = basic_clean(raw)
    match = SALT_AND_PEPPER.match(name)
    parts = [match["salt"], match["pepper"]] if match else [name]
    cleaned = []
    for part in parts:
        part = strip_descriptors(singular(part, known), known)
        part = singular(part, known)
        if part and part not in cleaned:
            cleaned.append(part)
    return cleaned


def clean_minutes(value: int) -> int | None:
    """Return the cooking time, or None when it is 0 or impossibly long."""
    return int(value) if 0 < value <= MAX_MINUTES else None


def clean_nutrition(values: list[float]) -> dict[str, float | None]:
    """Split the 7 nutrition numbers into named values; obviously wrong values become None."""
    cleaned = {}
    for column, value in zip(NUTRITION_COLUMNS, values):
        limit = MAX_CALORIES if column == "calories" else MAX_PDV
        cleaned[column] = float(value) if 0 <= value <= limit else None
    return cleaned


def clean_rating(value: int) -> int | None:
    """Return the rating, or None for 0 (a comment without a rating)."""
    return int(value) if 1 <= value <= 5 else None


def group_tags(tags: list[str]) -> dict[str, list[str]]:
    """Keep only the useful tags, sorted into courses, diets and cuisines."""
    return {
        "courses": sorted(set(tags) & COURSES),
        "diets": sorted(set(tags) & DIETS),
        "cuisines": sorted(set(tags) & CUISINES),
    }


def filter_by_reviews(reviews: pd.DataFrame, min_reviews: int) -> pd.DataFrame:
    """Keep reviews whose recipe and user both have at least min_reviews reviews, repeated until stable."""
    while True:
        per_recipe = reviews.groupby("recipe_id")["user_id"].transform("size")
        per_user = reviews.groupby("user_id")["recipe_id"].transform("size")
        kept = reviews[(per_recipe >= min_reviews) & (per_user >= min_reviews)]
        if len(kept) == len(reviews):
            return kept
        reviews = kept


def build_ingredient_map(raw_lists: pd.Series) -> dict[str, list[str]]:
    """Map every raw ingredient string to its clean name(s), using all recipes as the vocabulary."""
    raw_names = Counter(raw_lists.explode().dropna())
    known = {basic_clean(name) for name in raw_names}
    return {raw: normalize_ingredient(raw, known) for raw in raw_names}


def edges(recipes: pd.DataFrame, column: str, target: str) -> pd.DataFrame:
    """Turn a list column into a two-column (recipe_id, target) relationship table."""
    table = recipes[["id", column]].explode(column).dropna()
    return table.rename(columns={"id": "recipe_id", column: target}).drop_duplicates()


def load_raw() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the two raw CSVs and turn the list columns (stored as text) into real lists."""
    recipes = pd.read_csv(RAW_DIR / "RAW_recipes.csv")
    reviews = pd.read_csv(RAW_DIR / "RAW_interactions.csv", usecols=["user_id", "recipe_id", "date", "rating"])
    for column in ["tags", "nutrition", "steps", "ingredients"]:
        recipes[column] = recipes[column].map(ast.literal_eval)
    return recipes, reviews


def clean_recipes(recipes: pd.DataFrame, ingredient_map: dict[str, list[str]]) -> pd.DataFrame:
    """Clean names, times and nutrition, and add the clean ingredient and tag lists."""
    recipes = recipes.copy()
    recipes["name"] = recipes["name"].fillna("").map(basic_clean)
    recipes["minutes"] = recipes["minutes"].map(clean_minutes).astype("Int64")
    recipes["steps"] = recipes["steps"].map(json.dumps)
    nutrition = pd.DataFrame(recipes["nutrition"].map(clean_nutrition).tolist(), index=recipes.index)
    recipes = recipes.join(nutrition)
    recipes["clean_ingredients"] = recipes["ingredients"].map(
        lambda names: sorted({clean for raw in names for clean in ingredient_map[raw]})
    )
    tag_groups = pd.DataFrame(recipes["tags"].map(group_tags).tolist(), index=recipes.index)
    return recipes.join(tag_groups)


def build_outputs(recipes: pd.DataFrame, reviews: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build one table per node type and per relationship type, keyed by output file name."""
    contains = edges(recipes, "clean_ingredients", "ingredient")
    has_cuisine = edges(recipes, "cuisines", "cuisine")
    cuisines = pd.DataFrame({"name": sorted(set(has_cuisine["cuisine"]) | set(CUISINE_PARENT.values()))})
    cuisines["parent"] = cuisines["name"].map(CUISINE_PARENT)
    users = pd.DataFrame({"id": sorted(set(reviews["user_id"]) | set(recipes["contributor_id"]))})

    return {
        "recipes.csv": recipes[
            ["id", "name", "minutes", "n_steps", "n_ingredients", "submitted", "description", "steps"]
            + NUTRITION_COLUMNS
        ],
        "users.csv": users,
        "submitted.csv": recipes[["contributor_id", "id"]].rename(
            columns={"contributor_id": "user_id", "id": "recipe_id"}
        ),
        "reviewed.csv": reviews[["user_id", "recipe_id", "rating", "date"]],
        "ingredients.csv": contains.groupby("ingredient").size().rename("recipe_count").reset_index(),
        "contains.csv": contains,
        "cuisines.csv": cuisines,
        "has_cuisine.csv": has_cuisine,
        "fits_diet.csv": edges(recipes, "diets", "diet"),
        "served_as.csv": edges(recipes, "courses", "course"),
    }


def main() -> None:
    """Read the raw files, clean them and write data/processed/*.csv."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-reviews", type=int, default=5, help="minimum reviews per recipe and per user")
    args = parser.parse_args()

    print("reading raw files...")
    recipes, reviews = load_raw()
    ingredient_map = build_ingredient_map(recipes["ingredients"])

    reviews = filter_by_reviews(reviews, args.min_reviews).copy()
    reviews["rating"] = reviews["rating"].map(clean_rating).astype("Int64")
    recipes = clean_recipes(recipes[recipes["id"].isin(reviews["recipe_id"])], ingredient_map)
    outputs = build_outputs(recipes, reviews)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, table in outputs.items():
        table.to_csv(OUT_DIR / filename, index=False)
        print(f"  {filename:<16} {len(table):>9,} rows")

    raw_count = recipes["ingredients"].explode().nunique()
    print(f"ingredient names in the kept recipes: {raw_count:,} raw -> {len(outputs['ingredients.csv']):,} clean")
    print(f"done, files written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
