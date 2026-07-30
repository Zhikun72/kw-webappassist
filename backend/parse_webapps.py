"""Parses project_config/web_apps/<id>.json + <id>/backend.py.

Every webapp under web_apps/ is enumerated and treated equally - none is
assumed canonical (multiple versions of "the same" dashboard are common; see
inventory.py for duplicate detection via content_hash).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.mock_detector import scan_backend
from backend.models import Webapp

FRONTEND_FILES = ("app.js", "body.html", "style.css")


def parse_webapps(web_apps_dir: Path, markers: dict) -> tuple[list[Webapp], dict[str, dict]]:
    webapps: list[Webapp] = []
    scan_results: dict[str, dict] = {}

    sidecar_files = sorted(web_apps_dir.glob("*.json"))
    for sidecar in sidecar_files:
        webapp_id = sidecar.stem
        app_dir = web_apps_dir / webapp_id
        if not app_dir.is_dir():
            continue

        try:
            with open(sidecar, encoding="utf-8") as fh:
                meta = json.load(fh)
        except json.JSONDecodeError:
            continue

        backend_filename = markers.get("backend_filename", "backend.py")
        backend_path = app_dir / backend_filename
        backend_source = backend_path.read_text(encoding="utf-8") if backend_path.exists() else ""
        content_hash = hashlib.sha256(backend_source.encode("utf-8")).hexdigest() if backend_source else ""

        has_frontend = any((app_dir / fn).exists() for fn in FRONTEND_FILES)

        webapp = Webapp(
            id=webapp_id,
            name=meta.get("name", webapp_id),
            type=meta.get("type", "unknown"),
            has_frontend_files=has_frontend,
            backend_source=backend_source,
            content_hash=content_hash,
        )
        webapps.append(webapp)

        scan_results[webapp_id] = (
            scan_backend(backend_source, markers)
            if backend_source
            else {"real_reads": [], "required_cols_checks": [], "mock_blocks": []}
        )

    return webapps, scan_results
