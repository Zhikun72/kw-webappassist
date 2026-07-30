# Dataiku Webapp Data Inspector

A local Flask app that ingests a **Dataiku project export `.zip`** and, for every
webapp in the project, reports which data is real, which is still hardcoded
mock, which required columns actually exist anywhere in the project, and
whether a mock's fields could plausibly be derived from real data.

Fully generic and config-driven: no dataset names, column names, webapp IDs,
or marker strings are hardcoded anywhere in the code. Everything is inferred
from the zip you drop in, and mock-detection patterns live in
[`config/markers.yaml`](config/markers.yaml).

See [`dataiku-webapp-data-inspector-prompt.md`](dataiku-webapp-data-inspector-prompt.md)
for the original spec and [`phase0-discovery-report.md`](phase0-discovery-report.md)
for the structural discovery pass this build was validated against.

## What it answers

- **Data-vs-render field classification**: a mock block's declared fields
  are classified `data` / `render` / `uncertain` *by position* (does it
  touch a dataframe/dataset, or only ever sit inside a `jsonify(...)`
  response payload) before anything else - never by name, since the same
  name (`value`, `label`) genuinely plays both roles in different places.
  Only `data` fields get scored for fill-ability, so a section's "missing"
  count reflects genuine data gaps, not JSON response scaffolding.
- **Three/four-state inventory per webapp section**: Ready, Partial (some
  required columns missing), Mock/to-build, Referenced-missing.
- **Column-level gap analysis**: every *data* column a webapp needs - from a
  real `required_cols` check or from a mock block's own field construction -
  is cross-referenced against the *whole project's* schemas (not just the
  one dataset a section happens to read), classified Satisfied /
  Available-elsewhere / Derivable / Missing.
- **Mock gap cards**: click a mock section to see its declared fields, their
  state, the intended migration source (if the code names one) and whether
  it actually exists in the project, plus an LLM-backed derivability check
  for whatever's left unresolved.
- **Fact export for downstream triage**: `GET /api/analyses/<id>/export`
  (JSON) and `?format=markdown` serialize every field's evidence - kind,
  state, source dataset(s), migration hint, description, how it was
  detected - plus per-webapp and cross-webapp summaries computed over data
  fields only. The webapp never decides "build vs request from client"
  itself; that triage happens downstream, with business context this tool
  doesn't have.
- **Flow graph**: datasets as nodes, recipes as edges, terminal datasets and
  Flow Zones highlighted, with upstream lineage lookups.
- **Cross-webapp view**: every webapp under `web_apps/` enumerated equally,
  with duplicate (byte-identical) webapps flagged.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optional: set GEMINI_API_KEY to enable real LLM derivability
.venv/bin/python3 app.py
```

Open `http://127.0.0.1:5000`, drop a Dataiku project export `.zip` on the
page. Without a `GEMINI_API_KEY`, the LLM step runs in stub mode (naive
name-overlap suggestions, clearly labeled) so the rest of the app works
end-to-end regardless.

Debug mode runs without the auto-reloader (`use_reloader=False` in
`app.py`) - a stale `watchdog` install in some Python environments (e.g. a
shared anaconda base env) breaks Werkzeug's reloader on import. Restart
manually after editing backend code.

## How it works

```
zip upload
  -> backend/ingest.py         extract + parse export-manifest.json
  -> backend/layout.py          locate datasets/recipes/zones/web_apps generically
  -> backend/parse_datasets.py  dataset schemas (name, type, columns)
  -> backend/parse_recipes.py   recipe inputs/outputs -> flow graph edges
  -> backend/parse_zones.py     Flow Zone grouping
  -> backend/parse_webapps.py   per-webapp backend.py + sidecar json
  -> backend/mock_detector.py   pattern-scan each backend.py:
                                   - dataiku.Dataset(...) real reads
                                   - required_cols checks (+ descriptions,
                                     always data-kind by construction)
                                   - banner/keyword/migration-hint mock blocks
                                   - each mock block's own declared fields (AST),
                                     classified data/render/uncertain by position
  -> backend/column_matching.py normalize + index every column name project-wide
  -> backend/inventory.py       cross-reference sections + columns -> states
                                   (data fields only; render/uncertain tracked
                                   separately, excluded from fill-ability counts)
  -> backend/llm_gap_analysis.py  (on demand) derivability for unresolved fields
  -> backend/export.py          (on demand) serialize every field's evidence as
                                   JSON + markdown, folding in any cached
                                   derivability results - never triggers new
                                   LLM calls itself
```

`backend/project_analysis.py` orchestrates the pipeline end to end
(`analyze_zip`). Nothing project-specific lives outside
`config/markers.yaml` - a different project's mock-marking conventions only
require editing that file.

## Configuration

- [`config/markers.yaml`](config/markers.yaml) - mock-detection banners,
  keywords, function-name patterns, migration-hint patterns, the
  `required_cols` variable-name patterns, a small denylist for boilerplate
  dict keys (e.g. the standard Flask `{"error": str(e)}` pattern), and the
  `dataframe_constructor_names` / `response_wrapper_names` call-name lists
  that drive data-vs-render field classification.
- `.env` (copy from `.env.example`): `GEMINI_API_KEY`, `GEMINI_MODEL`,
  `FLASK_SECRET_KEY`, `UPLOAD_DIR`.

## API

All analysis state lives in memory, keyed by an `analysis_id` returned from
upload - this is a local single-operator tool, not a multi-tenant service.

| Route | What it returns |
|---|---|
| `POST /api/upload` | Analyzes an uploaded zip; returns `analysis_id` + discovery payload |
| `GET /api/analyses/<id>/discovery` | Project counts, webapp summaries, warnings |
| `GET /api/analyses/<id>/graph` | Flow graph nodes/edges (zone, terminal, degree) |
| `GET /api/analyses/<id>/webapps/<webapp_id>` | Full per-section inventory + column states |
| `GET /api/analyses/<id>/webapps/<webapp_id>/columns` | Deduped per-webapp column rollup |
| `GET /api/analyses/<id>/webapps/<webapp_id>/sections/<section_id>/derivability` | LLM derivability for a mock section's unresolved fields |
| `GET /api/analyses/<id>/datasets/<name>` | Full column list for one dataset |
| `GET /api/analyses/<id>/inventory` | Built-unused datasets + duplicate webapp groups |
| `GET /api/analyses/<id>/export` (`?format=markdown` for text) | Per-field triage facts + per-webapp/cross-webapp summaries (data fields only) |

## Testing

```bash
.venv/bin/python3 -m pytest tests/
```

Covers mock detection (banner/keyword/migration-hint parsing, the
comment-scoping that prevents UI strings like `placeholder="..."` from
false-positiving as mock markers, and declared-field extraction), the
data/render/uncertain field-kind classification (including the real
project's own ambiguity - the same field name used as a dataframe column in
one function and a jsonify key in a sibling function must resolve to
`uncertain`, not silently either way), constant resolution, flow graph
terminal/lineage calculation, column-name normalization and matching, the
column-level state classification (Satisfied/Available-elsewhere/Missing,
Partial section state), and the export builder (field record shape,
webapp/cross-webapp summaries, cached-derivable state surfacing).

## Known limitations

- **Field extraction is still permissive at the dict-literal level** - every
  dict key inside a mock block's owning function(s) is a extraction
  candidate. The data/render/uncertain classification gate removes JSON
  response scaffolding from the fill-ability counts, but genuinely
  ambiguous keys (a lookup/config dict never passed to `DataFrame`/
  `jsonify`, or a value built by a helper function that itself doesn't
  directly touch either) land in `uncertain` rather than being guessed -
  by design, not a bug, but it means `uncertain` needs a human glance.
- **Classification has no interprocedural tracing.** A dict returned by a
  helper function that's later passed to `jsonify`/`DataFrame` by its
  *caller* isn't traced back into that helper - only within one function
  (or the handful of functions a mock block directly spans) is a dict's
  origin resolved. This is a deliberate scope boundary (full data-flow
  analysis is a much larger undertaking), not an oversight.
- **Column matching is Tier 1 only**: exact name matching after NFKC
  normalization (trim/case/full-width-half-width). No recipe-level column
  lineage yet (Tier 2 - tracing which join/grouping/shaker step actually
  produces a column). `derivable` in the export only ever reflects an
  already-cached LLM check (the derivability button) - export itself never
  triggers a new LLM call.
- **No row-level profiling** (missing-rate, distributions, samples) unless
  the export actually contains dataset data - this build targets
  metadata/schema-only exports per the spec.
- Left-panel-to-graph lineage highlighting and a terminal-only view of
  built-unused datasets are designed but not yet built.
