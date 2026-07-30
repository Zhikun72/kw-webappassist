"""Column-name matching across the whole project's real dataset schemas.

Tier 1 of the column-level gap analysis (see spec Part 2/3): exact-after-
normalization name matching only - no recipe lineage, no LLM. This alone
answers "does a needed column exist anywhere in the project," independent of
which single dataset a webapp section happens to read.
"""
from __future__ import annotations

import unicodedata

from backend.models import Dataset


def normalize_column_name(name: str) -> str:
    """NFKC normalization folds full-width/half-width variants together (a
    common source of mismatches in Japanese-language projects) as a side
    effect of Unicode compatibility decomposition; trim + casefold handle
    whitespace and case. No project-specific rules."""
    return unicodedata.normalize("NFKC", name).strip().casefold()


def build_column_index(datasets: dict[str, Dataset]) -> dict[str, list[str]]:
    """normalized column name -> sorted list of dataset names that declare a
    column matching it."""
    index: dict[str, set[str]] = {}
    for dataset in datasets.values():
        for column in dataset.columns:
            key = normalize_column_name(column.name)
            index.setdefault(key, set()).add(dataset.name)
    return {key: sorted(names) for key, names in index.items()}
