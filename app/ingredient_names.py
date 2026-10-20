"""Ingredient name rules shared by the cleaning script and the API, so both treat names the same way."""

import re


def normalize_name(name: str) -> str:
    """Lowercase, trim, use 'and' instead of '&' and collapse repeated spaces."""
    name = name.lower().replace("&", " and ")
    return re.sub(r"\s+", " ", name).strip()


def singular_candidates(name: str) -> list[str]:
    """Possible singular forms of a name, most likely first (empty if it does not look plural)."""
    candidates = []
    if name.endswith("ies"):
        candidates.append(name[:-3] + "y")
    if name.endswith("es"):
        candidates.append(name[:-2])
    if name.endswith("s") and not name.endswith("ss"):
        candidates.append(name[:-1])
    return candidates
