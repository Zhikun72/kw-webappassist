"""Semantic gap/derivability analysis for mock blocks - the LLM step.

Mock detection itself never needs the LLM (comments already mark it). This
module's only job: given a mock block and candidate real datasets, judge
which fields the mock effectively needs and whether each is plausibly
derivable from an existing real dataset's columns.

Swappable by design: GapAnalyzer is the interface; GeminiGapAnalyzer is the
real implementation (key from env, isolated here); StubGapAnalyzer runs when
no key is configured so the rest of the app works end-to-end regardless.
Only metadata/code snippets are ever sent - never row data.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests

from backend.models import Dataset, MockBlock

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class FieldDerivability:
    field: str
    derivable: bool | None  # None = unknown / no candidate found
    source_dataset: str | None
    source_columns: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class DerivabilityResult:
    mock_block_id: str
    fields: list[FieldDerivability]
    overall_note: str
    engine: str  # "gemini" | "stub"


class GapAnalyzer(ABC):
    @abstractmethod
    def analyze(self, mock_block: MockBlock, candidate_datasets: list[Dataset]) -> DerivabilityResult: ...


def _guess_needed_fields(mock_block: MockBlock) -> list[str]:
    """Best-effort extraction of output field names from a mock block's code
    (dict keys / DataFrame column assignments) - used both as the stub's
    input and as a fallback if the LLM response can't be parsed."""
    fields_found = set()
    for m in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*:', mock_block.snippet):
        fields_found.add(m.group(1))
    for m in re.finditer(r"df\[[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\]\s*=", mock_block.snippet):
        fields_found.add(m.group(1))
    return sorted(fields_found)


def _candidate_match(field_name: str, candidate_datasets: list[Dataset]) -> tuple[str | None, list[str]]:
    """Heuristic-only fallback: does any candidate dataset have a column
    whose name overlaps the field name (case-insensitive substring)."""
    field_lower = field_name.lower()
    for ds in candidate_datasets:
        matches = [c.name for c in ds.columns if field_lower in c.name.lower() or c.name.lower() in field_lower]
        if matches:
            return ds.name, matches
    return None, []


class StubGapAnalyzer(GapAnalyzer):
    """Runs with no external calls. Provides naive name-overlap suggestions
    so the UI has something to show before a Gemini key is wired in."""

    def analyze(self, mock_block: MockBlock, candidate_datasets: list[Dataset]) -> DerivabilityResult:
        needed = _guess_needed_fields(mock_block)
        results = []
        for f in needed:
            source, cols = _candidate_match(f, candidate_datasets)
            results.append(
                FieldDerivability(
                    field=f,
                    derivable=bool(source) or None,
                    source_dataset=source,
                    source_columns=cols,
                    note=(
                        f"Heuristic name match against {source}."
                        if source
                        else "No candidate column found by name overlap; needs human judgment or a Gemini key."
                    ),
                )
            )
        return DerivabilityResult(
            mock_block_id=mock_block.id,
            fields=results,
            overall_note=(
                "GEMINI_API_KEY is not set - showing naive name-overlap matches only. "
                "Set the env var to enable real semantic derivability judgments."
            ),
            engine="stub",
        )


class GeminiGapAnalyzer(GapAnalyzer):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model

    def _build_prompt(self, mock_block: MockBlock, candidate_datasets: list[Dataset]) -> str:
        candidates_desc = "\n".join(
            f"- {ds.name} ({ds.type}): columns = {[c.name for c in ds.columns]}" for ds in candidate_datasets
        )
        return f"""You are analyzing a mock data block in a Dataiku webapp backend.py.
Identify the fields this mock block effectively needs, and for each field judge
whether it could plausibly be derived from one of the candidate real datasets below.

Mock block title: {mock_block.title!r}
Migration hint (if any): {mock_block.migration_hint!r}
Mock code snippet:
```python
{mock_block.snippet}
```

Candidate real datasets (name, type, columns - no row data available):
{candidates_desc}

Respond with ONLY a JSON object of this shape:
{{"fields": [{{"field": str, "derivable": true|false, "source_dataset": str|null,
"source_columns": [str], "note": str}}], "overall_note": str}}
"""

    def analyze(self, mock_block: MockBlock, candidate_datasets: list[Dataset]) -> DerivabilityResult:
        prompt = self._build_prompt(mock_block, candidate_datasets)
        url = GEMINI_ENDPOINT.format(model=self.model)
        resp = requests.post(
            url,
            params={"key": self.api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)

        fields = [
            FieldDerivability(
                field=f["field"],
                derivable=f.get("derivable"),
                source_dataset=f.get("source_dataset"),
                source_columns=f.get("source_columns", []),
                note=f.get("note", ""),
            )
            for f in parsed.get("fields", [])
        ]
        return DerivabilityResult(
            mock_block_id=mock_block.id,
            fields=fields,
            overall_note=parsed.get("overall_note", ""),
            engine="gemini",
        )


def get_gap_analyzer() -> GapAnalyzer:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return StubGapAnalyzer()
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return GeminiGapAnalyzer(api_key=api_key, model=model)
