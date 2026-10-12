"""Unified wrapper around the System-1 decision engines this app compares:

- Laya (convaiinnovations/laya-typed-decisions): local weights, loaded once and reused.
- Jev (typesafe.ai): a cloud API, needs TYPESAFE_API_KEY in the environment.
- Vega (vegaml, https://github.com/NandhaKishorM/vegaml): local weights, zero-shot "engine"
  readout, loaded once and reused. VEGA_SIZE in the environment picks the checkpoint: "0.8b"
  (the default) or "4b".
- OpenAI Decisions API (POST /v1/decisions, public beta): a cloud API, needs OPENAI_API_KEY
  in the environment. The model defaults to gpt-6-luna; override with OPENAI_DECISIONS_MODEL.

answer(state, questions) -> {qid: normalized_answer} answers general multi-type typed
questions (choice/score/noul in one call), e.g. laya.presets.triage_questions(),
normalized to a vendor-independent shape so callers don't need to branch on which
model answered:
    choice: {"type": "choice", "choice": str, "probabilities": {opt: float}, "confidence": float}
    score:  {"type": "score", "score": float, "legend": {lvl: str}, "probabilities": {lvl: float}, "confidence": float}
    noul:   {"type": "noul", "noul": float, "confidence": float}
    refusal: {"type": "refusal"}  (OpenAI only: it may decline a single question)
After each answer(), `last_usage` holds that call's token counts as reported by the vendor:
{"input_tokens": int, "output_tokens": int}. The two aren't directly comparable: Laya counts
every token its encoder processed (one sequence per question, so the message is counted once
per question), Vega counts the prompt tokens its frozen language model read, and Jev and
OpenAI report their own billable tokens.
"confidence" is each vendor's own reported field (Jev's noul answers don't ship one, so it's
computed the same way Laya computes its own: max(noul, 1 - noul)). Callers that want
"probability the model assigned to what it actually predicted" for calibration purposes
should derive it from probabilities/noul/score themselves -- see metrics.py for why
the raw vendor confidence field isn't a reliable stand-in for that.
"""
import json
import os
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_laya_agent = None
_laya_lock = threading.Lock()
_vega = None
_vega_info = {}
_vega_lock = threading.Lock()
VEGA_YES_NO_TEXT = Path(__file__).resolve().parent.parent / "data" / "vega_yes_no_text.json"


def _pkg_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _env_flag(name, default=False):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def _normalize_choice(choice, probabilities, confidence):
    return {
        "type": "choice", "choice": choice,
        "probabilities": {str(k): float(v) for k, v in probabilities.items()},
        "confidence": float(confidence),
    }


def _normalize_score(score, legend, probabilities, confidence):
    return {
        "type": "score", "score": float(score),
        "legend": {str(k): v for k, v in legend.items()},
        "probabilities": {str(k): float(v) for k, v in probabilities.items()},
        "confidence": float(confidence),
    }


def _normalize_noul(noul):
    noul = float(noul)
    return {"type": "noul", "noul": noul, "confidence": round(max(noul, 1.0 - noul), 4)}


def _normalize_usage(input_tokens, output_tokens):
    return {"input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0)}


class DecisionModel:
    name = "base"
    last_usage = None
    # What produced the answers (package versions, checkpoint, harness options); saved in every
    # results file so a run says exactly which configuration it measured.
    config = {}

    def answer(self, state, questions):
        raise NotImplementedError


class LayaModel(DecisionModel):
    name = "laya"

    def __init__(self):
        global _laya_agent
        with _laya_lock:
            if _laya_agent is None:
                import laya
                _laya_agent = laya.load("convaiinnovations/laya-typed-decisions")
        self.agent = _laya_agent
        self.config = {"laya": _pkg_version("laya"), "checkpoint": "convaiinnovations/laya-typed-decisions"}

    def answer(self, state, questions):
        result = self.agent.predict(state, questions)
        usage = result.get("usage") or {}
        self.last_usage = _normalize_usage(usage.get("input_tokens"), usage.get("output_tokens"))
        out = {}
        for qid, ans in result["answers"].items():
            if ans["type"] == "choice":
                out[qid] = _normalize_choice(ans["choice"], ans["probabilities"], ans["confidence"])
            elif ans["type"] == "score":
                out[qid] = _normalize_score(ans["score"], ans["legend"], ans["probabilities"], ans["confidence"])
            else:
                out[qid] = _normalize_noul(ans["noul"])
        return out


class JevModel(DecisionModel):
    name = "jev"

    def __init__(self):
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Jev needs a TYPESAFE_API_KEY. Get one at https://console.typesafe.ai, "
                "add it to your .env file, then restart the server."
            )
        from typesafe_sdk import TypeSafeClient
        self.client = TypeSafeClient(api_key=api_key)
        self.config = {"jev": _pkg_version("jev")}

    def answer(self, state, questions):
        result = self.client.system_one(state=state, questions=questions)
        usage = result.usage
        self.last_usage = _normalize_usage(usage and usage.input_tokens, usage and usage.output_tokens)
        out = {}
        for qid, ans in result.answers.items():
            if ans.type == "choice":
                out[qid] = _normalize_choice(ans.choice, ans.probabilities, ans.confidence)
            elif ans.type == "score":
                out[qid] = _normalize_score(ans.score, ans.legend, ans.probabilities, ans.confidence)
            else:
                out[qid] = _normalize_noul(ans.noul)
        return out


class VegaModel(DecisionModel):
    """Vega, with harness options that each follow vegaml's documented usage.

    Every option defaults to the plain call (`decide(state, questions)` with our question
    format), so results stay comparable unless an option is switched on. Set them in .env, or
    pass them as keyword arguments (the ablation script does):

    - VEGA_YES_NO_TEXT=1: give yes/no questions real option text from data/vega_yes_no_text.json.
      Without it vegaml shows "no - the statement does not hold / yes - the statement holds".
    - VEGA_SCORE_AS_CHOICE=1: ask score questions as a choice between the rubric levels and map
      the answer back to the level index, instead of vegaml's ordered score rail.
    - VEGA_PLAIN_STATE=1: send a one-field input as plain text, and several fields as
      "Key: value" lines, instead of the JSON string vegaml makes from a dict.
    - VEGA_VIEWS=1|2|3: vegaml's test-time averaging (2 adds a reading with the options
      reversed, 3 also a prose rendering); about that many times the latency.

    The readout is always vegaml's zero-shot engine (`mode="engine"`), so a fitted adapter can
    never change this lane's answers.
    """

    name = "vega"

    def __init__(self, yes_no_text=None, score_as_choice=None, plain_state=None, views=None):
        global _vega
        with _vega_lock:
            if _vega is None:
                import torch
                import vegaml
                # vegaml only picks between cuda and cpu on its own, so ask for Apple's GPU explicitly.
                device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
                # "0.8b" (vegaml's own default) or "4b"; the size goes in as load()'s first argument.
                size = os.environ.get("VEGA_SIZE", "0.8b").strip()
                _vega = vegaml.load(size, device=device)
                _vega_info.update(size=size.lower(), device=device)
                print(f"[vega] loaded the {size} checkpoint on {device}")
        self.vega = _vega
        self.yes_no_text = _env_flag("VEGA_YES_NO_TEXT") if yes_no_text is None else yes_no_text
        self.score_as_choice = _env_flag("VEGA_SCORE_AS_CHOICE") if score_as_choice is None else score_as_choice
        self.plain_state = _env_flag("VEGA_PLAIN_STATE") if plain_state is None else plain_state
        self.views = int(os.environ.get("VEGA_VIEWS", "1")) if views is None else int(views)
        if self.views not in (1, 2, 3):
            raise ValueError(f"VEGA_VIEWS must be 1, 2 or 3, got {self.views}")
        self.yes_no = json.loads(VEGA_YES_NO_TEXT.read_text(encoding="utf-8")) if self.yes_no_text else {}
        self.config = {
            "vegaml": _pkg_version("vegaml"), "size": _vega_info.get("size"), "device": _vega_info.get("device"),
            "mode": "engine", "views": self.views, "yes_no_text": self.yes_no_text,
            "score_as_choice": self.score_as_choice, "plain_state": self.plain_state,
        }

    def _to_vega(self, questions):
        out = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                # Vega calls the yes/no type "boolean".
                vq = {**q, "type": "boolean"}
                if qid in self.yes_no:
                    vq["criteria"] = {"false": self.yes_no[qid]["false"], "true": self.yes_no[qid]["true"]}
            elif q["type"] == "score" and self.score_as_choice:
                vq = {**q, "type": "choice", "criteria": {str(i): text for i, text in enumerate(q["criteria"])}}
            else:
                vq = q
            out[qid] = vq
        return out

    def _state(self, state):
        if not self.plain_state:
            return state
        if len(state) == 1:
            return str(next(iter(state.values())))
        return "\n".join(f"{str(k).replace('_', ' ').capitalize()}: {v}" for k, v in state.items())

    def answer(self, state, questions):
        result = self.vega.decide(self._state(state), self._to_vega(questions), mode="engine", views=self.views)
        self.last_usage = _normalize_usage(result.get("n_tokens"), 0)
        out = {}
        for qid, ans in result["answers"].items():
            q = questions[qid]
            if q["type"] == "score":
                legend = dict(enumerate(q.get("criteria") or []))
                probs = {int(k): v for k, v in ans["probs"].items()}
                expected = ans["expected_score"] if "expected_score" in ans else sum(k * v for k, v in probs.items())
                out[qid] = _normalize_score(expected, legend, probs, ans["confidence"])
            elif q["type"] == "choice":
                out[qid] = _normalize_choice(ans["choice"], ans["probs"], ans["confidence"])
            else:
                out[qid] = _normalize_noul(ans["boolean"])
        return out


class OpenAIDecisionsModel(DecisionModel):
    name = "openai"

    def __init__(self):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OpenAI needs an OPENAI_API_KEY. Add it to your .env file, then restart the server."
            )
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = os.environ.get("OPENAI_DECISIONS_MODEL", "gpt-6-luna")
        self.config = {"openai": _pkg_version("openai"), "model": self.model}

    @staticmethod
    def _to_openai(questions):
        # Decisions takes an ordered list of named questions: noul is "predicate", choice
        # options become `choices` and score rubric levels become `levels`.
        out = []
        for qid, q in questions.items():
            item = {"name": qid, "instructions": q["instructions"]}
            criteria = q.get("criteria") or []
            if q["type"] == "noul":
                item["type"] = "predicate"
            elif q["type"] == "choice":
                item["type"] = "choice"
                if isinstance(criteria, dict):
                    item["choices"] = [{"value": k, "description": v} for k, v in criteria.items()]
                else:
                    item["choices"] = [{"value": k} for k in criteria]
            else:
                item["type"] = "score"
                item["levels"] = [{"label": c} for c in criteria]
            out.append(item)
        return out

    def answer(self, state, questions):
        # The questions refer to state keys like `message`, so send the state as JSON.
        result = self.client.decisions.create(
            model=self.model,
            input=json.dumps(state, ensure_ascii=False),
            questions=self._to_openai(questions),
        )
        self.last_usage = _normalize_usage(result.usage.input_tokens, result.usage.output_tokens)
        out = {}
        for ans in result.answers:
            if ans.type == "choice":
                probs = {p.value: p.probability for p in ans.probabilities}
                out[ans.name] = _normalize_choice(ans.choice, probs, ans.confidence)
            elif ans.type == "score":
                legend = {p.value: p.label for p in ans.probabilities}
                probs = {p.value: p.probability for p in ans.probabilities}
                out[ans.name] = _normalize_score(ans.score, legend, probs, ans.confidence)
            elif ans.type == "predicate":
                out[ans.name] = _normalize_noul(ans.probability)
            else:
                out[ans.name] = {"type": "refusal"}
        return out


def get_model(name):
    if name == "laya":
        return LayaModel()
    if name == "jev":
        return JevModel()
    if name == "vega":
        return VegaModel()
    if name == "openai":
        return OpenAIDecisionsModel()
    raise ValueError(f"Unknown decision model: {name}")
