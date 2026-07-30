"""Flask entry point for the Dataiku Webapp Data Inspector.

Routes are intentionally thin - all analysis logic lives in backend/*;
this file only wires HTTP in and out.
"""
from __future__ import annotations

import os
import uuid
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from backend.config import load_markers, upload_dir  # noqa: E402
from backend.flow_graph import zone_for_dataset  # noqa: E402
from backend.llm_gap_analysis import get_gap_analyzer  # noqa: E402
from backend.models import SectionState, to_dict  # noqa: E402
from backend.project_analysis import analyze_zip, candidate_datasets_for_mock  # noqa: E402

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB - metadata-only exports are small; generous cap

# In-memory analysis store. This is a local single-operator tool - no need
# for a database; each upload gets a fresh id and its own extraction dir.
ANALYSES: dict[str, dict] = {}


def _section_counts(webapp):
    counts = {"ready": 0, "mock": 0, "referenced_missing": 0}
    for section in webapp.sections:
        counts[section.state.value] += 1
    return counts


def _webapp_summary(webapp, duplicate_groups):
    dup_group = next((g for g in duplicate_groups if webapp.id in g), None)
    return {
        "id": webapp.id,
        "name": webapp.name,
        "type": webapp.type,
        "has_frontend_files": webapp.has_frontend_files,
        "section_counts": _section_counts(webapp),
        "duplicate_of": [w for w in dup_group if w != webapp.id] if dup_group else [],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    file = request.files.get("zipfile")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded (expected form field 'zipfile')."}), 400
    if not file.filename.lower().endswith(".zip"):
        return jsonify({"error": "File must be a .zip export."}), 400

    analysis_id = uuid.uuid4().hex[:12]
    work_dir = upload_dir() / analysis_id
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / "export.zip"
    file.save(zip_path)

    if not zipfile.is_zipfile(zip_path):
        return jsonify({"error": "Uploaded file is not a valid zip archive."}), 400

    markers = load_markers()
    try:
        project, graph, inventory, layout = analyze_zip(zip_path, work_dir / "extracted", markers)
    except Exception as exc:  # noqa: BLE001 - surface any ingest/parse failure to the caller
        return jsonify({"error": f"Analysis failed: {exc}"}), 422

    ANALYSES[analysis_id] = {
        "project": project,
        "graph": graph,
        "inventory": inventory,
        "layout": layout,
    }

    return jsonify({"analysis_id": analysis_id, **_discovery_payload(analysis_id)})


def _get_analysis(analysis_id: str):
    bundle = ANALYSES.get(analysis_id)
    if not bundle:
        return None
    return bundle


def _discovery_payload(analysis_id: str) -> dict:
    bundle = _get_analysis(analysis_id)
    project = bundle["project"]
    inventory = bundle["inventory"]
    layout = bundle["layout"]

    datasets_by_type: dict[str, int] = {}
    for ds in project.datasets.values():
        datasets_by_type[ds.type] = datasets_by_type.get(ds.type, 0) + 1

    recipes_by_type: dict[str, int] = {}
    for r in project.recipes:
        recipes_by_type[r.type] = recipes_by_type.get(r.type, 0) + 1

    return {
        "manifest": {
            "generated_with_dss_version": project.manifest.generated_with_dss_version,
            "has_row_data": project.manifest.has_row_data,
        },
        "counts": {
            "datasets": len(project.datasets),
            "datasets_by_type": datasets_by_type,
            "recipes": len(project.recipes),
            "recipes_by_type": recipes_by_type,
            "zones": len(project.zones),
            "webapps": len(project.webapps),
            "built_unused": len(inventory.built_unused),
        },
        "webapps": [_webapp_summary(w, inventory.duplicate_groups) for w in project.webapps],
        "duplicate_groups": inventory.duplicate_groups,
        "warnings": project.discovery_warnings,
        "layout_missing": [
            name
            for name, path in (
                ("datasets", layout.datasets_dir),
                ("recipes", layout.recipes_dir),
                ("zones", layout.zones_dir),
                ("web_apps", layout.web_apps_dir),
            )
            if path is None
        ],
    }


@app.route("/api/analyses/<analysis_id>/discovery")
def discovery(analysis_id):
    if not _get_analysis(analysis_id):
        return jsonify({"error": "Unknown analysis_id"}), 404
    return jsonify(_discovery_payload(analysis_id))


@app.route("/api/analyses/<analysis_id>/graph")
def graph_view(analysis_id):
    bundle = _get_analysis(analysis_id)
    if not bundle:
        return jsonify({"error": "Unknown analysis_id"}), 404

    project = bundle["project"]
    graph = bundle["graph"]
    zone_map = zone_for_dataset(project.zones)
    known = set(project.datasets.keys())
    terminals = set(graph.terminal_datasets(known))

    all_node_ids = set(project.datasets.keys()) | graph.nodes
    nodes = []
    for node_id in sorted(all_node_ids):
        ds = project.datasets.get(node_id)
        zone = zone_map.get(node_id)
        nodes.append(
            {
                "id": node_id,
                "known_dataset": ds is not None,
                "type": ds.type if ds else "unknown",
                "column_count": len(ds.columns) if ds else 0,
                "zone_id": zone.id if zone else None,
                "zone_name": zone.name if zone else None,
                "zone_color": zone.color if zone else None,
                "terminal": node_id in terminals,
                "out_degree": graph.out_degree(node_id),
                "in_degree": graph.in_degree(node_id),
            }
        )

    edges = [{"from": frm, "to": to, "recipe": recipe} for frm, to, recipe in graph.edges]

    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/analyses/<analysis_id>/webapps/<webapp_id>")
def webapp_detail(analysis_id, webapp_id):
    bundle = _get_analysis(analysis_id)
    if not bundle:
        return jsonify({"error": "Unknown analysis_id"}), 404

    project = bundle["project"]
    webapp = next((w for w in project.webapps if w.id == webapp_id), None)
    if not webapp:
        return jsonify({"error": "Unknown webapp_id"}), 404

    return jsonify(
        {
            "webapp": {"id": webapp.id, "name": webapp.name, "type": webapp.type},
            "sections": [to_dict(s) for s in webapp.sections],
        }
    )


@app.route("/api/analyses/<analysis_id>/datasets/<dataset_name>")
def dataset_detail(analysis_id, dataset_name):
    bundle = _get_analysis(analysis_id)
    if not bundle:
        return jsonify({"error": "Unknown analysis_id"}), 404

    dataset = bundle["project"].datasets.get(dataset_name)
    if not dataset:
        return jsonify({"error": "Unknown dataset"}), 404

    return jsonify(to_dict(dataset))


@app.route("/api/analyses/<analysis_id>/inventory")
def inventory_view(analysis_id):
    bundle = _get_analysis(analysis_id)
    if not bundle:
        return jsonify({"error": "Unknown analysis_id"}), 404
    inventory = bundle["inventory"]
    project = bundle["project"]
    return jsonify(
        {
            "built_unused": [
                {"name": name, "type": project.datasets[name].type} for name in inventory.built_unused
            ],
            "duplicate_groups": inventory.duplicate_groups,
        }
    )


@app.route("/api/analyses/<analysis_id>/webapps/<webapp_id>/sections/<section_id>/derivability")
def derivability(analysis_id, webapp_id, section_id):
    bundle = _get_analysis(analysis_id)
    if not bundle:
        return jsonify({"error": "Unknown analysis_id"}), 404

    project = bundle["project"]
    graph = bundle["graph"]
    webapp = next((w for w in project.webapps if w.id == webapp_id), None)
    if not webapp:
        return jsonify({"error": "Unknown webapp_id"}), 404

    section = next((s for s in webapp.sections if s.id == section_id), None)
    if not section or section.state != SectionState.MOCK or not section.mock_block:
        return jsonify({"error": "Section is not a mock block."}), 400

    candidates = candidate_datasets_for_mock(section.mock_block, project, graph)
    analyzer = get_gap_analyzer()
    try:
        result = analyzer.analyze(section.mock_block, candidates)
    except Exception as exc:  # noqa: BLE001 - never let an LLM outage break the page
        return jsonify(
            {
                "mock_block_id": section.mock_block.id,
                "fields": [],
                "overall_note": f"LLM call failed ({exc}); showing no derivability data.",
                "engine": "error",
            }
        )

    return jsonify(to_dict(result))


if __name__ == "__main__":
    # use_reloader=False: the debug auto-reloader pulls in `watchdog`, whose
    # API has drifted across versions in ways that break Werkzeug's reloader
    # on some environments (e.g. a stale watchdog in a shared anaconda base
    # env). Debug mode (tracebacks, auto server errors) still works fine
    # without it; you just restart manually after editing backend code.
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
