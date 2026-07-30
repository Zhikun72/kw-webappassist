# Phase 0 Discovery Report — KW_SOP_DP.zip

## 1. Manifest check (export-manifest.json)
- `exportedWithOptions.exportProjectResources = true`, `.exportDatasetResources = true` ✅ matches expected input
- `.exportAllDatasets = false`, `.exportUploads = false`, `actualContent.includedDatasets = []` → **no row data**. Confirms metadata/schema-only mode; row-level profiling is correctly out of scope for this zip.
- `hasPartitionedDataset = false`
- `generatedWithDSSVersion = "14.7.1"` — same version as the Appendix.
- Top-level layout matches the Appendix exactly: `export-manifest.json`, `project_config/`, `discussions/`, `experiment-tracking/`, `timelines/`.

## 2. Datasets (`project_config/datasets/*.json`) — 225 files
Type histogram (from each file's `type` field):
| type | count |
|---|---|
| Snowflake | 219 |
| Trino | 4 |
| UploadedFiles | 2 |

All 225 are real, backed datasets — confirms the spec's core claim ("all datasets are real; mock lives only in webapp code"). Schema shape matches the Appendix: `schema.columns[]` with `name`/`type`/`comment`/`originalType`/`maxLength`.

## 3. Recipes (`project_config/recipes/*.json` + sidecars) — 190 recipes
Recipe `type` field histogram (190 `.json` control files, each with matching sidecar payload file):
join 45, shaker 43, grouping 38, prediction_training 14, evaluation 14, sql_script 12, split 10, sampling 9, window 2, vstack 2, sync 1 — sidecar extension counts line up 1:1 with these. All types from the Appendix are present; no unexpected types found.

Wiring confirmed as `inputs.main.items[].ref` → `outputs.main.items[].ref`, e.g.:
```json
"inputs": {"main": {"items": [{"ref": "CALENDAR_prepared_campaign_joined_prepared", ...}]}},
"outputs": {"main": {"items": [{"ref": "CALENDAR_campaign_filtered", ...}]}}
```

Flow graph computed from all recipes:
- 220/225 datasets appear in at least one recipe edge; **31 datasets are orphaned** (no recipe references them at all — not even as inputs). These are candidates for "Built-unused" alongside terminal-but-unread datasets.
- **58 terminal datasets** (out-degree 0) — the delivery-surface candidates webapps are expected to read from.

## 4. Zones (`project_config/zones/*.json`) — 28 files
Shape matches Appendix: `name` (Japanese business label), `color`, `position`, `items[{objectId, objectType}]` where `objectType` is `DATASET` or `RECIPE`. Zone item counts are small (single digits to a few dozen) — a legitimate grouping/coloring dimension, not a partition of the whole flow (some datasets are zone-less, e.g. "Default" zone catches leftovers).

## 5. Web apps (`project_config/web_apps/`) — 4 webapps
| id | name | type | files present |
|---|---|---|---|
| WobyKzs | DP_Dashboard | DASH | `backend.py` only (no app.js/body.html/style.css — expected for DASH type, layout lives in backend.py) |
| o7H32bc | DP_Dashboard_v0.0.1_ma | STANDARD | backend.py, app.js, body.html, style.css |
| qd7YjPb | DP_Dashboard_v1.0.0 | STANDARD | backend.py, app.js, body.html, style.css |
| v79XFj9 | DP_Dashboard_v1.0.0_hanai | STANDARD | backend.py, app.js, body.html, style.css |

**Notable finding: o7H32bc and v79XFj9 are byte-identical** across all four files (confirmed via `diff`). This is exactly the "same dashboard as multiple versions" case the spec calls out for cross-webapp comparison — worth surfacing as "these two are duplicates" rather than silently listing them as unrelated.

**Naming is not a progress signal**: `qd7YjPb` ("v1.0.0") has *zero* mock markers and a much smaller backend.py (17.7KB, ~580 lines) than `o7H32bc`/`v79XFj9` ("v0.0.1"/"v1.0.0_hanai", 78KB each, full of mock sections). Version-like names in the `name` field don't indicate which is more complete — confirms the spec's "treat all webapps equally" instruction is necessary, not just cautious.

## 6. Mock detection signals found (per webapp)

| webapp | banner `# ===` blocks | contain "MOCK" | `_mock_*` functions | dummy/placeholder/ダミー/暫定 | migration hints |
|---|---|---|---|---|---|
| WobyKzs (DASH) | 10 blocks | none | none | none (2 UI `placeholder="..."` string literals — false-positive risk, see below) | none |
| o7H32bc | 10 blocks | 8 sections marked `MOCK DATA`/`ALL MOCK DATA` | 2 (`_mock_dataset_a`, `_mock_flat_category_data`) | none extra found beyond "mock" itself | 1 (`実データ移行時は Dataiku の dataiku.Dataset("データセットA") から`) |
| qd7YjPb | none | none | none | none | none |
| v79XFj9 | identical to o7H32bc (file is a byte copy) | | | | |

All real reads follow the exact pattern from the spec:
```python
DATASET_NAME = "SAL_QTY_output"   # module-level string assignment
...
dataset = dataiku.Dataset(DATASET_NAME)
df = dataset.get_dataframe(...)
```
`SAL_QTY_output.json` exists in `project_config/datasets/` — confirmed a "Ready" case.

**Column-name variables need the same resolution as dataset names.** `required_cols` checks don't use string literals directly — they reference module-level constants:
```python
DATE_COL = "日付"
SALES_ACTUAL_COL = "SAL_QTY_avg"
FILTER_COL_1 = "SAL_NAM"
FILTER_COL_2 = "STR_FIG_NAM"
...
required_cols = [DATE_COL, SALES_ACTUAL_COL, FILTER_COL_1, FILTER_COL_2]
missing_cols = [c for c in required_cols if c not in df.columns]
```
The parser needs to resolve **both** dataset-name variables and column-name variables via the same simple module-level-assignment lookup — the spec's wording ("name may be a variable... resolve simple module-level string assignments") should be applied generically to any string constant, not just `DATASET_NAME`.

**False-positive risk confirmed for naive keyword scanning.** WobyKzs contains two hits for `placeholder` — both are Dash UI component props (`placeholder="すべて"`), not mock markers. Marker matching must be scoped to **comments** (lines starting with `#`, or comment portions after code), not arbitrary string literals, or "placeholder"/"dummy" style markers will false-positive on ordinary UI code.

**Migration hint points at a dataset that doesn't exist (partial-match case, exactly what the LLM step is for).** The mock's stated intended source is `"データセットA"` / internally named `HANBAIMEISAI_PRC_Output_v2` — neither exists as an exact dataset name, but close relatives do (`HANBAIMEISAI_MASTA_EXCEL*`, `FP_HANBAIMEISAI_*_SQL`, etc.). This is a real example of "referenced-missing with plausible candidates nearby" — good validation case for the derivability LLM step rather than a hardcoded exact-name lookup.

## 7. Confirmed vs. spec — summary
Everything in the Appendix checks out against this zip: manifest flags, dataset/recipe/zone JSON shapes, recipe type list, `web_apps/<id>.json` + `<id>/` layout, STANDARD-vs-DASH file sets, and all four mock-signal layers (banners, `_mock_*` names, inline "mock" comments, migration hints). The only real-world wrinkles beyond the spec's text:
1. Two of four webapps are literal duplicates — needs explicit dedup/flagging in the cross-webapp view, not just parallel listing.
2. Marker matching must be comment-scoped to avoid false positives from ordinary UI strings (e.g. `placeholder=`).
3. Variable resolution for string constants must cover column names used in `required_cols`, not only dataset names.
4. Migration-hint intended sources can name datasets that don't exist under that exact name — the LLM step needs schema-based fuzzy candidates, not just an exact lookup, exactly as the spec already intends.

No changes needed to the planned architecture — these are refinements to apply within the generic, config-driven design (e.g., a "scan only comment text" rule, and resolving any module-level string constant, not just ones named `DATASET_NAME`).
