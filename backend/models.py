"""Shared data model for a parsed Dataiku project export.

Every parser module (datasets, recipes, zones, webapps) produces or consumes
these types. Nothing here encodes project-specific names - a Project is
built entirely from what's found in a given zip.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import PurePath


@dataclass
class Column:
    name: str
    type: str


@dataclass
class Dataset:
    name: str
    type: str
    columns: list[Column] = field(default_factory=list)

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


@dataclass
class Recipe:
    name: str
    type: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass
class Zone:
    id: str
    name: str
    color: str | None
    dataset_ids: list[str] = field(default_factory=list)
    recipe_ids: list[str] = field(default_factory=list)


class SectionState(str, Enum):
    READY = "ready"
    MOCK = "mock"
    REFERENCED_MISSING = "referenced_missing"


@dataclass
class RealRead:
    """A `dataiku.Dataset(...)` read site found in a webapp backend."""
    dataset_name: str
    resolved: bool  # True if the name came from a resolved variable
    line_no: int


@dataclass
class RequiredColsCheck:
    var_name: str
    columns: list[str]
    line_no: int


@dataclass
class MockBlock:
    id: str
    title: str | None            # banner / section label, if any
    start_line: int
    end_line: int
    trigger_keywords: list[str] = field(default_factory=list)
    mock_functions: list[str] = field(default_factory=list)
    migration_hint: str | None = None
    migration_hint_dataset: str | None = None  # dataset name captured from hint, if any
    snippet: str = ""


@dataclass
class WebappSection:
    """One reportable unit of a webapp's data situation: either a real read
    site or a mock block, cross-referenced against the project's datasets."""
    id: str
    label: str
    state: SectionState
    real_read: RealRead | None = None
    mock_block: MockBlock | None = None
    required_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    matched_dataset: str | None = None


@dataclass
class Webapp:
    id: str
    name: str
    type: str  # STANDARD | DASH | ...
    has_frontend_files: bool
    backend_source: str = ""
    sections: list[WebappSection] = field(default_factory=list)
    content_hash: str = ""  # for duplicate-webapp detection


@dataclass
class ManifestCheck:
    exported_with_options: dict
    actual_content: dict
    generated_with_dss_version: str | None
    has_row_data: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class Project:
    manifest: ManifestCheck
    datasets: dict[str, Dataset] = field(default_factory=dict)
    recipes: list[Recipe] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    webapps: list[Webapp] = field(default_factory=list)
    discovery_warnings: list[str] = field(default_factory=list)


def to_dict(obj):
    """Recursively convert dataclasses (incl. nested Enums) to plain JSON-able dicts."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, PurePath):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_dict(v) for v in obj]
    return obj
