"""Zip extraction and export-manifest.json parsing.

Structure can vary by DSS version (see spec). This module locates the export
root generically - by finding export-manifest.json - rather than assuming the
zip's top level *is* the root (some zip tools wrap contents in one extra
directory).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from backend.models import ManifestCheck

MANIFEST_FILENAME = "export-manifest.json"


class IngestError(Exception):
    pass


def _safe_members(zf: zipfile.ZipFile, dest_dir: Path):
    """Reject zip entries that would escape dest_dir (zip-slip)."""
    dest_resolved = dest_dir.resolve()
    for member in zf.infolist():
        target = (dest_dir / member.filename).resolve()
        if dest_resolved not in target.parents and target != dest_resolved:
            raise IngestError(f"Unsafe path in archive: {member.filename}")
        yield member


def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract zip_path into dest_dir and return the export root (the
    directory that directly contains export-manifest.json)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in _safe_members(zf, dest_dir):
            zf.extract(member, dest_dir)

    matches = list(dest_dir.rglob(MANIFEST_FILENAME))
    if not matches:
        raise IngestError(
            f"No {MANIFEST_FILENAME} found anywhere in the archive - "
            "this doesn't look like a Dataiku project export."
        )
    if len(matches) > 1:
        # Prefer the shallowest match.
        matches.sort(key=lambda p: len(p.relative_to(dest_dir).parts))
    return matches[0].parent


def parse_manifest(export_root: Path) -> ManifestCheck:
    manifest_path = export_root / MANIFEST_FILENAME
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    warnings = []
    opts = manifest.get("exportedWithOptions", {})
    content = manifest.get("actualContent", {})

    if not opts.get("exportProjectResources"):
        warnings.append("exportProjectResources was not set - project_config/ may be incomplete.")
    if not opts.get("exportDatasetResources"):
        warnings.append("exportDatasetResources was not set - dataset schemas may be missing.")

    has_row_data = bool(
        opts.get("exportAllDatasets")
        or opts.get("exportUploads")
        or content.get("includedDatasets")
    )
    if has_row_data:
        warnings.append(
            "This export appears to include dataset row data. Row-level profiling "
            "(missing-rate, distributions, samples) is a bonus in this build, not "
            "the default path - schema-level analysis still runs regardless."
        )

    if manifest.get("hasPartitionedDataset"):
        warnings.append("Project has partitioned datasets - partition-level nuance is not modeled.")

    return ManifestCheck(
        exported_with_options=opts,
        actual_content=content,
        generated_with_dss_version=manifest.get("generatedWithDSSVersion"),
        has_row_data=has_row_data,
        warnings=warnings,
    )
