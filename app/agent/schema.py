"""The graph schema, written once and used by the LLM prompt, the guardrails and the cleaning script."""

# Order matters: it is the order of the 7 numbers in the raw Food.com "nutrition" column.
NUTRITION_PROPERTIES = [
    "calories",
    "total_fat_pdv",
    "sugar_pdv",
    "sodium_pdv",
    "protein_pdv",
    "saturated_fat_pdv",
    "carbs_pdv",
]

NODE_PROPERTIES = {
    "User": ["id"],
    "Recipe": ["id", "name", "minutes", "n_steps", "n_ingredients", "submitted", "description", "steps"]
    + NUTRITION_PROPERTIES,
    "Ingredient": ["name", "recipe_count"],
    "IngredientCategory": ["name", "source"],
    "Cuisine": ["name"],
    "Diet": ["name"],
    "Course": ["name"],
}

# type: (start label, end label, properties)
RELATIONSHIPS = {
    "SUBMITTED": ("User", "Recipe", []),
    "REVIEWED": ("User", "Recipe", ["rating", "date"]),
    "CONTAINS": ("Recipe", "Ingredient", []),
    "IN_CATEGORY": ("Ingredient", "IngredientCategory", ["match_score"]),
    "HAS_CUISINE": ("Recipe", "Cuisine", []),
    "PART_OF": ("Cuisine", "Cuisine", []),
    "FITS_DIET": ("Recipe", "Diet", []),
    "SERVED_AS": ("Recipe", "Course", []),
}

LABELS = frozenset(NODE_PROPERTIES)
RELATIONSHIP_TYPES = frozenset(RELATIONSHIPS)
PROPERTIES = frozenset(
    [prop for props in NODE_PROPERTIES.values() for prop in props]
    + [prop for _, _, props in RELATIONSHIPS.values() for prop in props]
)


def schema_text() -> str:
    """Describe the schema in plain text for the LLM prompt."""
    nodes = "\n".join(f"  (:{label} {{{', '.join(props)}}})" for label, props in NODE_PROPERTIES.items())
    rels = "\n".join(
        f"  (:{start})-[:{rel_type}{' {' + ', '.join(props) + '}' if props else ''}]->(:{end})"
        for rel_type, (start, end, props) in RELATIONSHIPS.items()
    )
    return f"Nodes:\n{nodes}\nRelationships:\n{rels}"
