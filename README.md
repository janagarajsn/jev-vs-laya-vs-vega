# Jev vs Laya vs Vega vs OpenAI — Typed-Decision Benchmark

A small Flask app that runs golden datasets (fixed items with known-correct answers) through four typed-decision ("System-1") models side by side, scores all four against those answers, and shows the results live in the browser.

| Model | Where it runs | Package |
|-------|---------------|---------|
| **Jev** | Cloud API from [typesafe.ai](https://console.typesafe.ai) | `jev` (imported as `typesafe_sdk`) |
| **Laya** | Locally, weights from Hugging Face ([`convaiinnovations/laya-typed-decisions`](https://huggingface.co/convaiinnovations/laya-typed-decisions)) | `laya` |
| **Vega** | Locally, the default 0.8B checkpoint in zero-shot `engine` mode ([vegaml](https://github.com/NandhaKishorM/vegaml)) | `vegaml` |
| **OpenAI** | Cloud API: the [Decisions API](https://platform.openai.com/docs) (`POST /v1/decisions`, public beta), model `gpt-6-luna` by default | `openai` |

Every model answers the exact same items on every run, so results are comparable across runs, models and code changes. Five datasets cover each question type:

| Dataset | Task | Question types | Items |
|---------|------|----------------|-------|
| Support ticket triage | Six questions per support email | choice, noul, score | 100 |
| Spam detection | Spam and phishing | noul | 100 |
| Bug severity scoring | 0–3 severity rubric, judged by impact rather than tone | score | 100 |
| Email topic routing | Route email to one of 7 teams | choice | 100 |
| Refund policy decisions | Apply a written policy to a case: approve or deny, and which rule decides | choice | 100 |

See [data/golden/README.md](data/golden/README.md) for the format and how to review items.

## Features

- **Dataset picker**: choose a golden dataset and run all items, or a fixed 20 or 50 item subset (the same items every time).
- **Live scoreboard**: overall accuracy, average confidence, average latency and token usage for each model.
- **Per-question breakdown**: accuracy for each question, how often the models disagreed (not all four gave the same answer), and how many times each model was right when they did.
- **Live item feed**: every item with its tags and correct answers, what each model predicted with its confidence, and each model's latency and tokens for that item. You can filter by question type (`choice` / `noul` / `score`).
- **Results panel**: when a run finishes, tables compare all four models on every metric (below), per question and per tag, with the best value per row underlined.
- **Export**: **Download Excel** in the results panel gives a workbook with Summary, Questions, By tag, Items (every answer, colour-coded right/wrong) and Confusion sheets. **Save as PDF** prints the scoreboard and results on a light, print-friendly page; choose "Save as PDF" in the print dialog.
- **Saved runs**: each run's raw answers are written to `results/<timestamp>-<dataset>.jsonl` as it goes, so a run can be rescored with `python metrics.py ../results/<file>` or exported with `python export.py ../results/<file>` without calling any model again.
- **Refusals**: the OpenAI Decisions API can decline a single question instead of answering. A refusal shows as `refused` in the item feed and counts as a wrong answer for accuracy. It is left out of average confidence, calibration and mean absolute error, and the `done` event reports refusal counts per model and question.
- **Token usage**: tokens as each model reports them, shown per item and as a running total. Laya counts every token its encoder processes, Vega counts the prompt tokens its frozen language model reads, and neither has output tokens. Jev and OpenAI report their own billable input and output tokens (OpenAI bills input tokens only).
- **Latency**: wall-clock time of each model's call per item, measured the same way for all four. Models are called one after another, never in parallel. Jev's and OpenAI's figures include the network round trip; Laya and Vega run on your machine (Apple GPU via MPS on a Mac).
- **Graceful degradation**: if a model can't load (for example, no Jev API key), the benchmark still runs with the others. If a single call fails mid-run, that item counts as a miss for that model and the run continues.

## What gets measured

Each item is answered with a single call per model that asks all of the dataset's typed questions at once. The questions, their options or rubric levels, and the correct answers all live in the dataset file.

How each prediction is scored:

- **choice**: the option the model picked.
- **noul**: `true` if the probability is ≥ 0.5. Vega calls the yes/no type `boolean` and OpenAI calls it `predicate`; the wrappers translate `noul` before calling them.
- **score**: the rubric level with the highest probability. It is not the rounded expected value, which would push spread-out distributions toward the middle level.

For calibration, "confidence" means the probability the model gave to the value it actually predicted. This is deliberately not each vendor's own `confidence` field, so the number means the same thing for all three question types.

Metrics in the results panel ([src/metrics.py](src/metrics.py)):

| Applies to | Metrics |
|------------|---------|
| Every model | accuracy, average confidence, expected calibration error (ECE), refusals, failed calls, latency p50 / p95, tokens per item |
| choice | accuracy, macro F1, F1 per option (precision, recall and the confusion matrix are in the `done` event and results file) |
| noul | accuracy, precision, recall, F1, AUC, Brier score, ECE |
| score | accuracy (exact level), within one level, mean absolute error of the expected score, quadratic weighted kappa, ECE |
| Every tag | accuracy over all questions on items with that tag |

A refusal or a failed call counts as a miss for accuracy and is left out of the probability-based metrics (calibration, Brier, AUC, MAE).

OpenAI takes its questions as an ordered list rather than a dictionary: choice options become `choices` (with their descriptions) and score rubric levels become `levels`. The item's input is sent as JSON, so instructions that mention `message` or `email` still make sense.

## Project structure

```
.
├── requirements.txt
├── LICENSE                 # MIT
├── CLAUDE.md               # Guidance for Claude Code when working in this repo
├── .env                    # API keys (keep out of version control)
├── data/golden/            # Golden datasets (JSONL) and a guide to reviewing them
├── results/                # Saved runs, one JSONL per run (git-ignored)
└── src/
    ├── app.py              # Flask server, API routes, Laya/Vega pre-warm on startup
    ├── decision_models.py  # Common wrapper for Jev, Laya, Vega and OpenAI with normalized answers
    ├── benchmark_engine.py # Benchmark loop, live totals, streamed events, saved results
    ├── golden.py           # Loads and validates the golden datasets
    ├── metrics.py          # All final metrics; also a CLI to rescore a saved run
    ├── export.py           # Excel export of a saved run (also a CLI)
    ├── templates/
    │   └── dashboard.html  # Dashboard UI
    └── static/
        ├── style.css
        └── dashboard.js    # Client: starts runs, reads the SSE stream, renders results
```

## Getting started

### Prerequisites

- Python 3.14+
- A [typesafe.ai](https://console.typesafe.ai) API key, needed only for Jev
- An OpenAI API key with access to the Decisions API, needed only for OpenAI
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

On Linux x86_64, the PyPI `torch` wheel includes CUDA, so an NVIDIA GPU is used automatically as long as the driver supports that build's CUDA version (check with `nvidia-smi`). If `python -c "import torch; print(torch.cuda.is_available())"` prints `False`, install a build that matches your driver, for example `pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu128`. On macOS the same wheel uses the Apple GPU via MPS.

### Configuration

Create a `.env` file in the project root. It is already listed in `.gitignore`.

```dotenv
# Hugging Face token used to download Laya's and Vega's weights
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx

# Required only for the Jev model. Get a key at https://console.typesafe.ai
TYPESAFE_API_KEY=xxxxxxxxxxxxxxxxxxxx

# Required only for the OpenAI model (Decisions API)
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
# Optional: Decisions model to use (default gpt-6-luna)
# OPENAI_DECISIONS_MODEL=gpt-6-luna
```

### Run

```bash
cd src
python app.py
```

Open **http://localhost:5050**, choose a dataset and how many items to run (20 / 50 / All), and click **Run benchmark**.

When the server starts, it loads Laya and Vega in the background, so the first run doesn't have to wait for them. Look for `[prewarm] Laya decision model loaded and ready.` and the matching Vega line in the console.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/datasets` | The available datasets: name, title, item count, reviewed count, questions |
| `POST` | `/api/benchmark/start` | Starts a run. Body: `{"dataset": "<name>", "limit": <n or null for all>}`. Returns `400` for an unknown dataset or invalid limit and `409` if a run is already in progress. |
| `GET` | `/api/benchmark/stream` | Server-Sent Events stream of the current run |
| `GET` | `/api/results/<run>.xlsx` | Excel export of a saved run, where `<run>` is the file name in `results/` without `.jsonl`. Returns `404` if there is no such run. |

Only one run can be active at a time.

Example:

```bash
curl -X POST localhost:5050/api/benchmark/start \
     -H 'Content-Type: application/json' -d '{"dataset": "spam_detection", "limit": 20}'
curl -N localhost:5050/api/benchmark/stream
```

### Stream events

Each SSE message is a JSON object with a `type` field:

| `type` | When | Key fields |
|--------|------|------------|
| `status` | While models load | `message` |
| `model_unavailable` | A model failed to load | `model`, `message` |
| `benchmark_started` | Run begins | `dataset`, `title`, `items`, `models`, `questions`, `score_levels` |
| `item_start` | Each item | `item`, `total`, `id`, `text`, `tags`, `labels` |
| `model_error` | A single model call failed (the run continues) | `model`, `item`, `message` |
| `item_timing` | After all models answer | `latency_ms` per model |
| `answer` | For each question on each item | `question`, `qtype`, `correct`, `picks`, `disagree`, `totals`, `disagreements`, `disagreement_wins` |
| `item_done` | Item finished | `item`, `tokens` (this item), `token_totals` (running) |
| `done` | Run finished | `elapsed`, `results_file`, `metrics` (the full output of `metrics.compute`) |
| `error` | Unrecoverable failure | `message` |
| `stream_end` | Always sent last | — |

## Golden datasets

Each dataset is a JSONL file in [data/golden/](data/golden/): a header line with the questions and tag definitions, then one item per line with its input, correct labels, tags and a one-line rationale. `python golden.py` (from `src/`) validates them all; the dashboard skips any file that fails validation.

- **Support ticket triage** has 100 synthetic support emails, each assembled from separate phrase banks for intent, urgency, frustration, impact scope, refund request and churn threat. The bank a phrase came from is its label, so the answers are correct by construction.
- **Spam, severity, routing and policy** were drafted with hard cases on purpose (`loud_but_minor` bug reports, polite phishing, emails that mention another team's keyword, policy cases where two rules fail). They start as `"reviewed": false`, and the dashboard shows the reviewed count until a person has checked every item. See [data/golden/README.md](data/golden/README.md).

## Extending

- **Add a dataset**: drop a `<name>.jsonl` file into `data/golden/` (format in [data/golden/README.md](data/golden/README.md)). It appears in the picker automatically.
- **Add a question to a dataset**: add it to the header's `questions` and give every item a label for it.
- **Add a model**: subclass `DecisionModel` in `decision_models.py`, return answers in the normalized shape documented at the top of that file, register it in `get_model()`, add its name to `LANES` in `benchmark_engine.py` and `dashboard.js`, add a display name to `MODEL_INFO` in `app.py`, give it a colour in `style.css` and bump the lane counts in its `.scoreboard` / `.ticket-row` grids.
- **Subsets**: `run_benchmark_stream()` shuffles items with a fixed `seed` (default 0) before taking `limit`, so a 20-item run is always the same 20 items.

## Troubleshooting

- **"Jev needs a TYPESAFE_API_KEY"**: add the key to `.env` and restart the server. The benchmark keeps running with the other models.
- **"OpenAI needs an OPENAI_API_KEY"**: add the key to `.env` and restart the server. The benchmark keeps running with the other models.
- **Slow first run / "Could not preload Laya" or "Vega"**: check your network connection and `HF_TOKEN`. The model is downloaded into the Hugging Face cache the first time.
- **A dataset is missing from the picker**: run `python golden.py` from `src/`. It prints why a file failed validation.
- **"A benchmark run is already in progress"**: wait for the current run to finish. Only one run is allowed at a time.
- **Port 5050 already in use**: change `port=` at the bottom of `src/app.py`.

## Tech stack

Flask 3.1 · python-dotenv · `laya` 0.3.5 · `jev` 0.3.0 · `vegaml` 0.6.1 · `openai` 3.28.0 · `openpyxl` 3.1.5 · vanilla JavaScript with Server-Sent Events

## License

MIT. See [LICENSE](LICENSE).
