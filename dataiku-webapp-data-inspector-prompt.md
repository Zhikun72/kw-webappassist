# Build Prompt: Dataiku Webapp Data Inspector (generic, zip-driven)

## What to build
A **local Flask web application** that ingests a **Dataiku project export `.zip`** and automatically produces, for **every webapp in the project**, a visual report of its data situation: which datasets it really reads, which parts are still hardcoded **mock data**, what columns each real dataset exposes, where each consumed dataset sits in the **Flow**, and — via an LLM step — whether the fields a mock block needs could be **derived from existing real datasets**.

It must be **generic and configuration-driven**. Do **not** hardcode dataset names, webapp IDs, column names, or comment strings from any specific project. The tool is dropped a zip and adapts. The reference project described in the Appendix is a *concrete example to design against and validate on* — not values to bake in.

**Runs in a free (non-client) environment.** The zip is exported and analyzed here; LLM (Gemini) use is allowed.

---

## Expected input
A Dataiku project export produced with **"Export project resources" + "Export dataset resources"** checked, **without** dataset data (no source rows). Verify against the export manifest at the zip root (`export-manifest.json` → `exportedWithOptions`). If dataset data *is* present, surface that and treat value-level analysis as an optional bonus; otherwise the tool works at the **metadata/schema level only** (no missing-rate %, distributions, or sample rows — those require real rows that aren't in this export).

**Phase 0 (discovery) is mandatory and runs on every uploaded zip.** Parse the manifest, enumerate what's actually inside, and report it before rendering the main views. Structure can vary by DSS version; adapt to what's found rather than assuming. The Appendix documents the layout observed in DSS 14.7.1 as a starting hypothesis.

---

## The mock concept — corrected model (read carefully)
The intuitive assumption is "mock = a fake dataset in the Flow." **That is wrong for this class of project.** In the reference export, **all 225 datasets are real** (backed by Snowflake / Trino / uploaded files). **Mock data lives inside each webapp's `backend.py`** as hardcoded Python (literal arrays, `pd.DataFrame([...])`, functions named like `_mock_*`), used where a real dataset or column doesn't exist yet.

Crucially, **the developer already marks mock explicitly in code comments.** So mock detection is primarily **pattern-scanning, not guessing.** Detect mock blocks via layered signals (all configurable):
1. **Section-header comments** delimited by `# ===...` banners containing markers like `MOCK DATA` / `ALL MOCK DATA`.
2. **Function names** matching `_mock_*` / `load_*mock*`.
3. **Inline comments** flagging dummy values (e.g. Japanese `ダミー`, `暫定`, English `mock`/`dummy`/`placeholder`) — keep the marker set configurable and easy to extend, since projects differ.
4. Migration hints in comments (e.g. "when moving to real data, read from `dataiku.Dataset(\"X\")`") — capture these; they state the *intended* real source.

Detect **real dataset reads** via `dataiku.Dataset(<NAME>)` (name may be a variable like `DATASET_NAME = "..."` defined earlier — resolve simple module-level string assignments) followed by `.get_dataframe()`. Also capture any `required_cols` / column-existence checks the backend performs — that logic is itself a field-gap check and its declared required columns are high-value signal for the LLM step.

---

## Three-state inventory (per webapp)
Cross-reference what each webapp needs against the project's real datasets:
- **Ready** — webapp reads a real `dataiku.Dataset(...)` that exists in the project, and the columns it requires are present in that dataset's declared schema.
- **Mock / to-build** — the webapp renders this section from a hardcoded mock block instead of a real dataset (optionally with a stated intended source from a migration-hint comment).
- **Referenced-missing** — the webapp references a dataset name (or requires columns) not found in the project's datasets/schemas.

Also surface **Built-unused**: real datasets in the project that no webapp reads (lower priority, but useful).

---

## Flow analysis
- Build a directed graph from **recipes**: each recipe `.json` has `inputs` and `outputs` (`{"main":{"items":[{"ref":"<dataset>"}]}}`). Datasets = nodes, recipes = edges (input→output).
- **Terminal datasets** = out-degree 0 (nothing downstream consumes them). These are the delivery surface and most likely what webapps consume — highlight them.
- Attach each dataset's **declared schema** (columns + types) from its dataset `.json` (`schema.columns[]`).
- Use **Flow Zones** (`zones/*.json`, each with a human name like a business-flow label, color, and member `items[]`) as a grouping/coloring dimension.
- For a selected mock/missing section: show the **upstream lineage** of its intended-or-related datasets and list those upstreams' columns, so the human can judge derivability alongside the LLM's assessment.

---

## LLM step (Gemini) — narrowed role
Mock detection does **not** need the LLM (comments already mark it). The LLM's job is **semantic gap/derivability analysis**:
- Input per mock block: the mock code snippet + surrounding comments (incl. any migration hint) + the schemas of candidate real datasets (the intended source if named, plus datasets whose columns look related).
- Output: the **fields this mock block effectively needs**, and for each, a **derivability judgment** — which real dataset + columns could plausibly produce it (e.g. "region derivable from `address`"), or "no upstream source found."
- Present as human-readable leads; the human decides. Route calls through **Gemini API**, key from an env var, isolated behind a small swappable interface. Send only metadata/code snippets (there is no row data anyway); keep payloads scoped.

---

## Multiple webapps
Enumerate **all** webapps under `web_apps/` (each has a `<id>.json` sidecar with `name`/`type` and a `<id>/` dir with `backend.py`, and for STANDARD types also `app.js`/`body.html`/`style.css`). Produce a per-webapp report and a **cross-webapp comparison** (same dashboard often exists as multiple versions — showing mock-vs-real progress across versions is valuable). Treat all webapps equally; do not assume which is canonical.

---

## UI (developer's rough shape: MATLAB-style side panel / devtools console)
- **Main area**: interactive Flow graph (clickable nodes/edges, terminal nodes highlighted, zones as color groups) and/or the data tables.
- **Side panel**: per-webapp three-state inventory, color-coded; click an item to expand detail (columns, mock snippet, LLM derivability note).
- **Webapp switcher / comparison** across the enumerated webapps.
- Exact visualization depth (graph vs list, chart richness) is **deliberately open** — the developer's needs are still forming. Ship a sensible modular default that's easy to iterate. Charts requiring real values are out of scope unless the zip contains data.

---

## Explicit boundaries
- **No row-level profiling** (missing-rate, distributions, samples) unless the zip actually contains dataset data — this export does not. Don't fabricate.
- **No live Dataiku connection** in this build, but keep a clean seam so a future Dataiku-API data source can be swapped in for real profiling.
- Do not conflate the **local Flask tool** (what you're building) with the **Dataiku webapps** (what it analyzes).
- Everything project-specific (names, markers, patterns) is **config**, not code constants.

## Deliverable
A runnable local Flask app. Emit the **Phase 0 discovery report first** so structural assumptions are validated against the real zip before the full UI builds out. Prefer modular, iterable code — requirements will evolve.

---

## Appendix — verified structure of a real export (DSS 14.7.1, for design reference only; do not hardcode)
```
<zip root>/
  export-manifest.json         # exportedWithOptions{}, actualContent{}, hasPartitionedDataset, requiredConnections{}
  project_config/
    datasets/<NAME>.json        # keys: type, params, schema{columns:[{name,type},...]}, tags, ...
                                # type observed: Snowflake / Trino / UploadedFiles (all REAL; none are "mock datasets")
    recipes/
      compute_<OUT>.json        # keys: type, inputs{main.items[].ref}, outputs{main.items[].ref}
                                # recipe types: shaker, join, grouping, split, sampling, window, vstack, sync,
                                #               sql_script, prediction_training, evaluation
      compute_<OUT>.<ext>       # sidecar payloads: .shaker .join .grouping .sql .window .split .sampling ...
    web_apps/
      <id>.json                 # keys: name, type (STANDARD | DASH), params, config
      <id>/backend.py           # real reads: dataiku.Dataset(DATASET_NAME).get_dataframe(); required_cols checks
                                # mock: "# === ... — MOCK DATA ===" banners, _mock_*() fns, ダミー/暫定 inline comments,
                                #       migration hints ("実データ移行時は dataiku.Dataset(\"X\") から")
      <id>/app.js body.html style.css   # present for STANDARD webapps
    zones/<id>.json             # Flow zone: name (business label), color, items[{objectId,objectType}]
    analysis/ saved_models/ model_evaluation_stores/ dashboards/ ipython_notebooks/ lib/ .git/
  discussions/ timelines/ experiment-tracking/
```
Manifest flags to check on ingest: `exportedWithOptions.exportProjectResources`, `.exportDatasetResources`, `.exportUploads`/`.exportAllDatasets` (row data present?), `actualContent.includedDatasets` (empty ⇒ schema-only), `hasPartitionedDataset`.
