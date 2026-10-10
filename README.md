# Jev vs Laya vs Vega — Ticket Triage Benchmark

A small Flask app that runs the same stream of synthetic customer-support tickets through three typed-decision ("System-1") models side by side, scores all three against known-correct labels, and shows the results live in the browser.

| Model | Where it runs | Package |
|-------|---------------|---------|
| **Jev** | Cloud API from [typesafe.ai](https://console.typesafe.ai) | `jev` (imported as `typesafe_sdk`) |
| **Laya** | Locally, weights from Hugging Face ([`convaiinnovations/laya-typed-decisions`](https://huggingface.co/convaiinnovations/laya-typed-decisions)) | `laya` |
| **Vega** | Locally, the default 0.8B checkpoint in zero-shot `engine` mode ([vegaml](https://github.com/NandhaKishorM/vegaml)) | `vegaml` |

Each ticket is generated with its correct labels already known, so the accuracy numbers are exact and don't depend on anyone's judgement.

![Dashboard after a 200-ticket run](docs/images/dashboard-200-tickets.png)

## Features

- **Live scoreboard**: overall accuracy, average confidence, average latency and token usage for each model.
- **Per-question breakdown**: accuracy for each question, how often the models disagreed (not all three gave the same answer), and how many times each model was right when they did.
- **Live ticket feed**: every ticket, what each model predicted with its confidence, and each model's latency and tokens for that ticket. You can filter by question type (`choice` / `noul` / `score`).
- **Calibration and error data**: the final `done` event reports confidence-bucketed calibration and the mean absolute error for score questions.
- **Token usage**: tokens as each model reports them, shown per ticket and as a running total. Laya counts every token its encoder processes, Vega counts the prompt tokens its frozen language model reads, and neither has output tokens. Jev reports its own billable input and output tokens.
- **Latency**: wall-clock time of each model's call per ticket, measured the same way for all three. Models are called one after another, never in parallel. Jev's figure includes the network round trip; Laya and Vega run on your machine (Apple GPU via MPS on a Mac).
- **Graceful degradation**: if a model can't load (for example, no Jev API key), the benchmark still runs with the others.

## What gets measured

Every ticket is answered with a single call per model that asks all of these typed questions at once. The first five come from `laya.presets.triage_questions()`; `impact_scope` is added in [src/triage_engine.py](src/triage_engine.py).

| Question | Type | Values |
|----------|------|--------|
| `intent` | choice | `refund`, `technical_help`, `billing_question`, `information`, `cancellation`, `other` |
| `is_urgent` | noul (yes/no probability) | true / false |
| `frustration` | score | 0 calm · 1 concerned · 2 annoyed · 3 angry |
| `impact_scope` | score | 0 just the sender · 1 the sender's team · 2 the whole company or its customers |
| `refund_requested` | noul | true / false |
| `churn_risk` | noul | true / false |

How each prediction is scored:

- **choice**: the option the model picked.
- **noul**: `true` if the probability is ≥ 0.5. Vega calls the yes/no type `boolean`; the wrapper renames `noul` before calling it.
- **score**: the rubric level with the highest probability. It is not the rounded expected value, which would push spread-out distributions toward the middle level.

For calibration, "confidence" means the probability the model gave to the value it actually predicted. This is deliberately not each vendor's own `confidence` field, so the number means the same thing for all three question types.

## Project structure

```
.
├── requirements.txt
├── LICENSE                 # MIT
├── CLAUDE.md               # Guidance for Claude Code when working in this repo
├── .env                    # API keys (keep out of version control)
├── docs/
│   ├── medium-article.md         # Write-up of the benchmark and its results
│   ├── medium-article.medium.md  # Generated copy with tables swapped for images
│   ├── render_tables.py          # Renders the article's tables to PNGs for Medium
│   └── images/                   # Dashboard screenshot and rendered tables
└── src/
    ├── app.py              # Flask server, API routes, Laya/Vega pre-warm on startup
    ├── decision_models.py  # Common wrapper for Jev, Laya and Vega with normalized answers
    ├── triage_engine.py    # Benchmark loop, scoring, calibration, streamed events
    ├── ticket_gen.py       # Synthetic ticket generator with known-correct labels
    ├── templates/
    │   └── triage.html     # Dashboard UI
    └── static/
        ├── style.css
        └── triage.js       # Client: starts runs, reads the SSE stream, renders results
```

## Getting started

### Prerequisites

- Python 3.14+
- A [typesafe.ai](https://console.typesafe.ai) API key, needed only for Jev
- A Hugging Face access token, recommended for downloading Laya's and Vega's weights
- Enough disk space and RAM for Laya (ModernBERT-large) and Vega (0.8B); the first run downloads both

### Installation

```bash
git clone <repo-url> jev-vs-laya-vs-vega
cd jev-vs-laya-vs-vega

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root. It is already listed in `.gitignore`.

```dotenv
# Hugging Face token used to download Laya's and Vega's weights
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx

# Required only for the Jev model. Get a key at https://console.typesafe.ai
TYPESAFE_API_KEY=xxxxxxxxxxxxxxxxxxxx
```

### Run

```bash
cd src
python app.py
```

Open **http://localhost:5050**, choose a ticket count (20 / 50 / 100 / 200; 50 is selected by default) and click **Run triage benchmark**.

When the server starts, it loads Laya and Vega in the background, so the first run doesn't have to wait for them. Look for `[prewarm] Laya decision model loaded and ready.` and the matching Vega line in the console.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Dashboard UI |
| `POST` | `/api/triage/start` | Starts a run. Body: `{"tickets": <1–500>}`. Returns `400` for an invalid count and `409` if a run is already in progress. |
| `GET` | `/api/triage/stream` | Server-Sent Events stream of the current run |

Only one run can be active at a time.

Example:

```bash
curl -X POST localhost:5050/api/triage/start \
     -H 'Content-Type: application/json' -d '{"tickets": 20}'
curl -N localhost:5050/api/triage/stream
```

### Stream events

Each SSE message is a JSON object with a `type` field:

| `type` | When | Key fields |
|--------|------|------------|
| `status` | While models load | `message` |
| `model_unavailable` | A model failed to load | `model`, `message` |
| `benchmark_started` | Run begins | `tickets`, `models`, `questions` |
| `ticket_start` | Each ticket | `ticket`, `total`, `message`, `labels` |
| `ticket_timing` | After all models answer | `latency_ms` per model |
| `answer` | For each question on each ticket | `question`, `qtype`, `correct`, `picks`, `disagree`, `totals`, `disagreements`, `disagreement_wins` |
| `ticket_done` | Ticket finished | `ticket`, `tokens` (this ticket), `token_totals` (running) |
| `done` | Run finished | `elapsed`, `totals`, `calibration`, `mean_abs_error`, `disagreements`, `disagreement_wins`, `avg_latency_ms`, `token_totals` |
| `error` | Unrecoverable failure | `message` |
| `stream_end` | Always sent last | — |

## How the synthetic tickets are built

[src/ticket_gen.py](src/ticket_gen.py) builds each message by picking from separate phrase banks for intent, impact scope, refund request, churn threat, urgency and frustration. The bank a phrase came from **is** its label, so the correct answers are known without any manual labelling. The result still reads like a normal support email:

> Hi team, We were charged twice for our Pro plan last cycle. Please look into the duplicate charge. I'm asking on behalf of my whole team. Please refund the charge as soon as possible. This needs to be resolved today. I'm getting pretty frustrated with these repeated issues. Thanks,

Low-stakes intents (`information`, `other`) never include urgency, refund or churn phrases, and they skew toward low frustration.

## Extending

- **Add a question**: add it to `_build_questions()` in `triage_engine.py` and produce a matching label in `generate_ticket()`.
- **Add a model**: subclass `DecisionModel` in `decision_models.py`, return answers in the normalized shape documented at the top of that file, register it in `get_model()`, add its name to `LANES` in `triage_engine.py` and `triage.js`, add a display name to `MODEL_INFO` in `app.py`, and give it a colour in `style.css`.
- **Reproducible runs**: `run_triage_stream()` accepts a `seed` argument.

## Troubleshooting

- **"Jev needs a TYPESAFE_API_KEY"**: add the key to `.env` and restart the server. The benchmark keeps running with Laya and Vega.
- **Slow first run / "Could not preload Laya" or "Vega"**: check your network connection and `HF_TOKEN`. The model is downloaded into the Hugging Face cache the first time.
- **"A triage run is already in progress"**: wait for the current run to finish. Only one run is allowed at a time.
- **Port 5050 already in use**: change `port=` at the bottom of `src/app.py`.

## Docs

[docs/medium-article.md](docs/medium-article.md) is a write-up of the benchmark: how the scoring rule, question wording and answer key were refined, and what the results showed. Medium's editor has no tables, so a script renders each table as an image and writes a Medium-ready copy of the article:

```bash
python3 docs/render_tables.py
```

It needs Google Chrome installed at the default macOS path (`CHROME` at the top of the script). It writes `docs/images/tables/table-NN.png` and `docs/medium-article.medium.md`.

## Tech stack

Flask 3.1 · python-dotenv · `laya` 0.3.5 · `jev` 0.3.0 · `vegaml` 0.6.1 · vanilla JavaScript with Server-Sent Events

## License

MIT. See [LICENSE](LICENSE).
