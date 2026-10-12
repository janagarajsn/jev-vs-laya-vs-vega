# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask app that benchmarks four typed-decision ("System-1") models — **Jev** (typesafe.ai cloud API, package `jev`, imported as `typesafe_sdk`), **Laya** (local HF weights `convaiinnovations/laya-typed-decisions`), **Vega** (`vegaml`, local 0.8B or 4B checkpoint via `VEGA_SIZE`) and **OpenAI** (Decisions API `POST /v1/decisions` via `openai` SDK `client.decisions.create`, default model `gpt-6-luna`) — on golden datasets (JSONL files in `data/golden/` with known-correct labels), streaming results to a browser dashboard over SSE.

## Commands

```bash
source venv/bin/activate          # Python 3.14 venv already exists in repo root
pip install -r requirements.txt
cd src && python app.py           # http://localhost:5050
```

- Modules use flat imports (`from decision_models import ...`), so run from inside `src/`.
- `.env` in the project root (template: `.env.example`) supplies `HF_TOKEN`, `TYPESAFE_API_KEY` (Jev only), `OPENAI_API_KEY` (OpenAI only; optional `OPENAI_DECISIONS_MODEL`) and `VEGA_SIZE` (`0.8b` default or `4b`, passed as `vegaml.load()`'s first argument; read once when Vega loads, so changing it needs a restart). A model whose key is missing is skipped and the run continues with the others.
- Headless run without the UI:
  ```bash
  curl -X POST localhost:5050/api/benchmark/start -H 'Content-Type: application/json' -d '{"dataset": "spam_detection", "limit": 20}'
  curl -N localhost:5050/api/benchmark/stream
  ```
- `python golden.py` (from `src/`) validates every dataset; `python metrics.py ../results/<run>.jsonl` rescores a saved run offline. `python export.py ../results/<run>.jsonl` writes an `.xlsx` next to it (same as `GET /api/results/<run>.xlsx`). `python report.py [runs...]` writes `results/performance_dashboard.jpg` (same as `GET /api/report.jpg`).

There is no test suite, linter config, or build step.

## Architecture

Request flow: `app.py` → background thread → `benchmark_engine.run_benchmark_stream(dataset, emit, limit)` → `emit` pushes event dicts onto a `queue.Queue` → `/api/benchmark/stream` drains it as SSE until a `stream_end` event → `static/dashboard.js` renders.

- **`app.py`**: Only one run at a time (`_run_state` guarded by `_run_lock`; returns 409 otherwise). On startup it pre-warms Laya and Vega in a background thread — only in the Werkzeug reloader child (`WERKZEUG_RUN_MAIN == "true"`) since `debug=True`. `MODEL_INFO` holds display names.
- **`decision_models.py`**: `DecisionModel` subclasses wrap each vendor and normalize answers into a single vendor-independent shape (documented in the module docstring) for `choice` / `score` / `noul` question types, and set `last_usage` after every call. Laya and Vega weights are module-level singletons behind locks, so `get_model()` is cheap after the first load. Vega uses `"boolean"` instead of `"noul"` (translated in `_to_vega`) and is explicitly given `mps` on Apple Silicon. OpenAI takes an ordered question list (`noul`→`predicate`, criteria→`choices`/`levels`, translated in `_to_openai`), gets the state as a JSON string, and can return `{"type": "refusal"}` for a question.
- **`golden.py`**: loads and validates `data/golden/<name>.jsonl` (header line `{"meta": ...}` with `questions`, `tags`, optional `context` merged into every item's input; then items with `id`, `input`, `labels`, `tags`, `rationale`, `reviewed`). Format is in the module docstring. Invalid files are skipped by `list_datasets()`. `sample_items` shuffles with a fixed seed before applying `limit`, so subsets are stable. Don't name a module `datasets` — it would shadow the HF `datasets` package the local models import.
- **`benchmark_engine.py`**: each item gets one `answer()` call per model, run **sequentially** (latency is wall-clock per call). A call that raises becomes `{"type": "error"}` answers plus a `model_error` event; the run continues. Streams live running totals and disagreements, writes each item's record to `results/<timestamp>-<dataset>.jsonl` as it goes, and puts `metrics.compute(...)` in the `done` event.
- **`metrics.py`**: all final metrics, pure functions of `(questions, records, models)`. Scoring is in `pick_and_confidence`: noul is `≥ 0.5`; score uses the **argmax rubric level**, not the rounded expected value; "confidence" is the probability assigned to the predicted value, deliberately **not** the vendor's own `confidence` field. A refusal or error predicts `None`: a miss for accuracy, excluded from calibration/Brier/AUC/MAE. Per type: choice → macro F1, per-class P/R/F1, confusion; noul → P/R/F1, Brier, AUC; score → MAE (expected score), within-one, QWK. Plus ECE, latency p50/p95, tokens, and accuracy by tag.
- **`export.py`**: builds the Excel workbook from a saved run with openpyxl, recomputing metrics via `metrics.compute` and pulling item text from the dataset by `id`. "Save as PDF" is just `window.print()` plus the `@media print` block in `style.css`.
- **`report.py`**: the cross-run dashboard JPG, drawn with matplotlib (Agg) on a 1600-unit canvas with y pointing down, so layout numbers read like CSS pixels; saved at 2x. Picks the latest full run (`limit` null) per dataset, else the latest run. Handles models missing from some runs and datasets without `easy`/`hard` tags. Key findings are generated from the numbers, so no dataset names are hard-coded.
- **Frontend**: `templates/dashboard.html` + `static/dashboard.js` (vanilla JS consuming the SSE event types listed in README) + `static/style.css` (per-model colours). The question table and results panel are built in JS from the `benchmark_started` and `done` events, so any dataset's questions work without template changes.

Token counts are not comparable across models: Laya counts encoder tokens once per question, Vega counts prompt tokens, and Jev and OpenAI report billable input/output.

## Cross-file changes

- **Add a model**: subclass `DecisionModel`, register it in `get_model()`, add it to `LANES` in **both** `benchmark_engine.py` and `dashboard.js`, add it to `MODEL_INFO` in `app.py`, give it a colour in `style.css`, and bump the `repeat(N, ...)` lane counts in the `.scoreboard` and `.ticket-row` grids there.
- **Add a dataset**: drop a valid `<name>.jsonl` into `data/golden/` (header `name` must match the file name); no code changes. Hand-written items start `"reviewed": false` until a person checks them.
- **Add a question**: add it to a dataset header's `questions` and label every item for it.
