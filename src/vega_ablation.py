"""Measure Vega harness options one at a time on the dev set.

Runs Vega alone (no cloud calls, so no API cost) on every dev dataset, once per harness
configuration, and prints accuracy per dataset and per question for each configuration next to
the baseline. Tune on the dev set, then run the golden sets once with the options that helped.

    python src/vega_ablation.py                      # every configuration, every dev dataset
    python src/vega_ablation.py --configs baseline yes_no_text
    python src/vega_ablation.py --datasets severity_scoring support_triage
    python src/vega_ablation.py --split golden       # only for a final check, never for tuning

The checkpoint size comes from VEGA_SIZE in .env, as in the app. Every run file and a
summary.md are written to results/dev/ablation-<timestamp>/.
"""
import argparse
from datetime import datetime

import dotenv

from benchmark_engine import RESULTS_DIR, run_benchmark_stream
from decision_models import VegaModel
from golden import SPLITS, list_datasets

# Each configuration changes one thing from the baseline, then the combinations.
CONFIGS = {
    "baseline": {},
    "yes_no_text": {"yes_no_text": True},
    "score_as_choice": {"score_as_choice": True},
    "plain_state": {"plain_state": True},
    "views_2": {"views": 2},
    "views_3": {"views": 3},
    "all_text_options": {"yes_no_text": True, "score_as_choice": True, "plain_state": True},
    "all_text_options_views_2": {"yes_no_text": True, "score_as_choice": True, "plain_state": True, "views": 2},
}
OFF = {"yes_no_text": False, "score_as_choice": False, "plain_state": False, "views": 1}


def run(configs, datasets, split, limit):
    out_dir = RESULTS_DIR / "dev" / f"ablation-{datetime.now():%Y%m%d-%H%M%S}"
    results = {}  # config -> dataset -> metrics
    for name in configs:
        model = VegaModel(**{**OFF, **CONFIGS[name]})
        for ds in datasets:
            done = {}

            def emit(event):
                if event["type"] == "done":
                    done.update(event)
                elif event["type"] in ("error", "model_error"):
                    print(f"  ! {event.get('message')}")

            print(f"[{name}] {split}/{ds} ...", flush=True)
            run_benchmark_stream(ds, emit, limit=limit, split=split, models={"vega": model},
                                 out_dir=out_dir / name)
            if done:
                results.setdefault(name, {})[ds] = done["metrics"]
    return out_dir, results


def summarize(results, datasets):
    names = list(results)
    lines = ["| configuration | " + " | ".join(datasets) + " | all decisions | median latency |",
             "|---|" + "---:|" * (len(datasets) + 2)]
    for name in names:
        hits = n = 0
        lat = []
        cells = []
        for ds in datasets:
            o = results[name].get(ds, {}).get("overall", {}).get("vega")
            if not o:
                cells.append("—")
                continue
            hits += o["hits"]
            n += o["n"]
            lat.append(o["latency_ms"]["p50"])
            cells.append(f"{o['accuracy'] * 100:.1f}%")
        overall = f"{hits / n * 100:.1f}%" if n else "—"
        median = f"{sorted(lat)[len(lat) // 2]:.0f} ms" if lat else "—"
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {overall} | {median} |")

    lines += ["", "Per question (accuracy):", "",
              "| dataset | question | " + " | ".join(names) + " |", "|---|---|" + "---:|" * len(names)]
    for ds in datasets:
        qids = next((list(results[n][ds]["questions"]) for n in names if ds in results[n]), [])
        for qid in qids:
            cells = []
            for n in names:
                q = results[n].get(ds, {}).get("questions", {}).get(qid)
                cells.append(f"{q['models']['vega']['accuracy'] * 100:.0f}%" if q else "—")
            lines.append(f"| {ds} | {qid} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--configs", nargs="+", choices=list(CONFIGS), default=list(CONFIGS))
    parser.add_argument("--datasets", nargs="+", help="dataset names (default: every dataset in the split)")
    parser.add_argument("--split", choices=list(SPLITS), default="dev")
    parser.add_argument("--limit", type=int, help="items per dataset (default: all)")
    args = parser.parse_args()
    if "baseline" not in args.configs:
        args.configs = ["baseline", *args.configs]

    datasets = args.datasets or [d["name"] for d in list_datasets(args.split)]
    out_dir, results = run(args.configs, datasets, args.split, args.limit)
    report = summarize(results, datasets)
    print("\n" + report)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.md").write_text(
        f"# Vega harness ablation ({args.split} split)\n\n"
        f"Settings per configuration are in each run file's header (`model_config.vega`).\n\n{report}\n",
        encoding="utf-8")
    print(f"\nwrote {out_dir / 'summary.md'}")
