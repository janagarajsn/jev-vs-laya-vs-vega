# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask app that benchmarks three typed-decision ("System-1") models — **Jev** (typesafe.ai cloud API, package `jev`, imported as `typesafe_sdk`), **Laya** (local HF weights `convaiinnovations/laya-typed-decisions`) and **Vega** (`vegaml`, local 0.8B checkpoint) — on synthetic support tickets whose correct labels are known by construction, streaming results to a browser dashboard over SSE.

## Commands

```bash
source venv/bin/activate          # Python 3.14 venv already exists in repo root
pip install -r requirements.txt
cd src && python app.py           # http://localhost:5050
```

- Modules use flat imports (`from decision_models import ...`), so run from inside `src/`.
- `.env` in the project root supplies `HF_TOKEN` and `TYPESAFE_API_KEY` (Jev only; without it Jev is skipped and the run continues with the other models).
- Headless run without the UI:
  ```bash
  curl -X POST localhost:5050/api/triage/start -H 'Content-Type: application/json' -d '{"tickets": 20}'
  curl -N localhost:5050/api/triage/stream
  ```
- `python3 docs/render_tables.py` renders the Markdown tables in `docs/medium-article.md` to PNGs via headless Chrome (macOS Chrome path hard-coded) and writes `docs/medium-article.medium.md`.

There is no test suite, linter config, or build step.

## Architecture

Request flow: `app.py` → background thread → `triage_engine.run_triage_stream(n, emit)` → `emit` pushes event dicts onto a `queue.Queue` → `/api/triage/stream` drains it as SSE until a `stream_end` event → `static/triage.js` renders.

- **`app.py`**: Only one run at a time (`_triage_state` guarded by `_triage_lock`; returns 409 otherwise). On startup it pre-warms Laya and Vega in a background thread — only in the Werkzeug reloader child (`WERKZEUG_RUN_MAIN == "true"`) since `debug=True`. `MODEL_INFO` holds display names.
- **`decision_models.py`**: `DecisionModel` subclasses wrap each vendor and normalize answers into a single vendor-independent shape (documented in the module docstring) for `choice` / `score` / `noul` question types, and set `last_usage` after every call. Laya and Vega weights are module-level singletons behind locks, so `get_model()` is cheap after the first load. Vega uses `"boolean"` instead of `"noul"` (translated in `_to_vega`) and is explicitly given `mps` on Apple Silicon.
- **`triage_engine.py`**: `QUESTIONS` = `laya.presets.triage_questions()` plus a custom `impact_scope` score question. Each ticket gets one `answer()` call per model, run **sequentially** (latency is wall-clock per call). Scoring is in `_pick_and_confidence`: noul is `≥ 0.5`; score uses the **argmax rubric level**, not the rounded expected value; "confidence" is the probability assigned to the predicted value, deliberately **not** the vendor's own `confidence` field. Also tracks disagreements, calibration bins (`N_BINS`), score MAE, latency and tokens. `seed` makes runs reproducible.
- **`ticket_gen.py`**: Builds each message from separate phrase banks. The bank a phrase is drawn from *is* its label. Low-stakes intents (`information`, `other`) never get urgency/refund/churn phrases.
- **Frontend**: `templates/triage.html` + `static/triage.js` (vanilla JS consuming the SSE event types listed in README) + `static/style.css` (per-model colours).

Token counts are not comparable across models: Laya counts encoder tokens once per question, Vega counts prompt tokens, and Jev reports billable input/output.

## Cross-file changes

- **Add a model**: subclass `DecisionModel`, register it in `get_model()`, add it to `LANES` in **both** `triage_engine.py` and `triage.js`, add it to `MODEL_INFO` in `app.py`, and give it a colour in `style.css`.
- **Add a question**: add it in `_build_questions()` and produce a matching label in `generate_ticket()`.
