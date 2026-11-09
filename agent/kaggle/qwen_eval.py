#!/usr/bin/env python3
"""Kaggle GPU: grade a fine-tuned Qwen3 on holdset's capture, against the real Planner and the small GameValue model, same nights.

    python agent/kaggle/qwen_eval.py --model /kaggle/working/qwen --archive /kaggle/input/rz-archive/archive --gamevalue /kaggle/input/rz-gamevalue --out /kaggle/working/qwen/eval.json --leagues nfl

Why: a billion parameters earns nothing by itself. The model may only move forward by beating what runs today on the same
decisions, so this plugs it into the same ranker slot scripts/kaggle_gamevalue_holdset.py uses (the-mind section 9). A model
that does not beat the small GameValue model on capture is reported plainly, in the "result" line.

Refuses: no manifest, a leak check that did not pass, or a training night inside the nights graded here. Dev nights only:
holdset reads through DevOnly, so a locked night stops the league (LockedNightError). Partial grading is not a grade: out of
time means no verdict. Promotion needs the shadow nights, which never run here, so this never says promote.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.kaggle import qwen_common as C  # noqa: E402
from agent.kaggle import qwen_finetune as QF  # noqa: E402
from redzone.contracts import brain  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from scripts import holdset  # noqa: E402
from scripts import kaggle_gamevalue_holdset as HOLD  # noqa: E402
from scripts.holdset_public import PlannerRanker  # noqa: E402

EXIT_REFUSED, EXIT_MAC = 2, 3
BUDGET_H = 3.0


class Refused(RuntimeError):
    """Grading must not go on: the reason is the message."""


class OutOfTime(RuntimeError):
    """The budget ran out mid-league. Part of a league cannot be compared with all of the Planner."""


class TimedBackend:
    """Stops the first batch after the deadline, so a slow GPU ends in a clear no-verdict, not Kaggle's kill."""

    def __init__(self, backend, deadline: float, clock):
        self.backend, self.deadline, self.clock = backend, deadline, clock

    def _check(self):
        if self.clock() >= self.deadline:
            raise OutOfTime("the grading budget ran out")

    def digit_probs(self, prompts):
        self._check()
        return self.backend.digit_probs(prompts)

    def yes_probs(self, prompts):
        return self.backend.yes_probs(prompts)

    def words(self, prompts):
        return self.backend.words(prompts)


def load_manifest(model: Path) -> dict:
    path = Path(model) / "qwen_model.json"
    if not path.exists():
        raise Refused(f"{path}: no manifest, so nothing says what this model was trained on")
    m = json.loads(path.read_text())
    if not (m.get("leak_check") or {}).get("passed") or not (m.get("leak_check") or {}).get("raw_rows"):
        raise Refused("the training run has no passed leak check with a raw audit")
    if m.get("status") != C.STATUS or m.get("schema") != C.SCHEMA:
        raise Refused(f"status {m.get('status')!r} schema {m.get('schema')!r}: not a model this grader knows")
    return m


def _digits(night) -> str:
    return re.sub(r"\D", "", str(night))


def graded_nights(rows: dict) -> list[str]:
    """holdset.report's own cut: oldest 60% fit, the rest graded."""
    nights = sorted(rows)
    return nights[int(len(nights) * 0.6):]


def check_no_overlap(manifest: dict, rows: dict) -> None:
    trained = {_digits(n) for n in list(manifest.get("train_nights") or []) + list(manifest.get("calib_nights") or [])}
    both = sorted(n for n in graded_nights(rows) if _digits(n) in trained)
    if both:
        raise Refused(f"{len(both)} graded nights were trained on, e.g. {both[0]}: this capture would mean nothing")


def contract_check(predictor: C.Predictor, backend, model_id: str, tick: int = 1) -> dict:
    """The last scored rows as one brain tick, words included, through the contract's `valid()`."""
    recent = predictor.recent
    if not recent:
        return {"checked": 0, "valid": False, "problem": "nothing was scored"}
    words = backend.words([C.after_tier1(q, d, C.YES if p >= 0.5 else C.NO) for q, d, _, p in recent])
    ids = [f"g{i}" for i in range(len(recent))]
    preds = {gid: C.Prediction(raw, p, w) for gid, (_, _, raw, p), w in zip(ids, recent, words)}
    now = 1_000_000.0
    inp = brain.BrainInput(tick=tick, now=now, games=tuple(brain.GameFacts(game_id=g) for g in ids))
    out = C.to_brain_output(inp, preds, model_id)
    return {"checked": len(ids), "valid": out is not None, "problem": None if out is not None else "brain.valid refused it"}


def verdict(model: dict, planner: dict, small: dict, contract: dict) -> dict:
    """Per league: the Planner bar (HOLD.verdict), the small-model bar, one plain sentence. A missing bar is a failed bar."""
    out = HOLD.verdict(model, planner)
    for lg, cell in out.items():
        res, gv = model[lg], small.get(lg) or {}
        same = bool(gv) and all(res.get(k) == gv.get(k) for k in ("nights_test", "decisions_test"))
        gv_k4 = (gv.get("K4") or {}).get("planner_pct") if same else None
        k4 = cell["model_k4_pct"]
        beats = bool(k4 is not None and gv_k4 is not None and k4 > gv_k4)
        cell.update(gamevalue_k4_pct=gv_k4, beats_gamevalue=beats, same_decisions_gamevalue=same, contract=contract.get(lg))
        ok_contract = bool((contract.get(lg) or {}).get("valid"))
        if gv_k4 is None:
            line = f"NO COMPARISON: no GameValue capture on the same {res.get('decisions_test')} decisions, so the Qwen model has not beaten it."
        elif beats:
            line = f"Qwen {k4}% beat GameValue {gv_k4}% at K=4 on the same nights."
        else:
            line = f"DOES NOT BEAT GameValue: Qwen {k4}% vs {gv_k4}% at K=4 on the same nights. Keep the small model."
        if not cell["beats_planner"]:
            line += f" Planner bar not beaten ({cell['real_planner_k4_pct']}%)."
        if not ok_contract:
            line += " Contract check failed: output unusable."
        cell["result"] = line
    return out


def _real_backend(model: Path, manifest: dict, work: Path):
    """work, not the model folder: an earlier version's output arrives read-only under /kaggle/input."""
    base_dir = QF.prepare_base(manifest["base"], work)
    return QF.TorchLora(base_dir, adapter=Path(model) / "model" / "peft")


def run(a, backend_factory, clock=time.time, log=print) -> dict:
    manifest = load_manifest(a.model)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    holdset.A = a.archive
    config = a.config or (a.archive.parent / "eval_config.yaml")
    config = config if config.exists() else None
    backend = TimedBackend(backend_factory(a.model, manifest, a.out.parent), clock() + a.budget_h * 3600.0, clock)
    model_id = f"qwen-{manifest['base']}-{manifest['recipe']}-{manifest['steps_done']}"
    model_res, planner_res, small_res, contract = {}, {}, {}, {}
    for lg in [x for x in a.leagues.split(",") if x]:
        try:
            rows = HOLD.build_rows(lg)
        except holdset.LockedNightError as e:
            log(lg, "REFUSED", e, flush=True)
            continue
        check_no_overlap(manifest, rows)
        predictor = C.Predictor(backend, manifest, lg)
        model_res[lg] = holdset.report(lg, rows, HOLD.ModelRanker(predictor))
        contract[lg] = contract_check(predictor, backend, model_id)
        planner_res[lg] = holdset.report(lg, rows, PlannerRanker(config, a.poll_s))
        path = G.model_path(lg, a.version, a.gamevalue)
        if path.exists():
            small_res[lg] = holdset.report(lg, rows, HOLD.ModelRanker(G.load(path, lg)))
        else:
            log(f"{lg}: no GameValue model at {path}", flush=True)
    if not model_res:
        raise Refused("no league could be graded")
    report = verdict(model_res, planner_res, small_res, contract)
    a.out.with_suffix(".holdset.json").write_text(json.dumps({"qwen": model_res, "planner": planner_res, "gamevalue": small_res}, indent=1))
    a.out.write_text(json.dumps({"model_id": model_id, "finished_training": manifest.get("finished"), "license_tag": manifest.get("license_tag"),
                                 "leagues": report}, indent=1, sort_keys=True))
    return report


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", type=Path, required=True, help="qwen_finetune's --out folder")
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--gamevalue", type=Path, required=True, help="folder of the exported small GameValue models")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--config", type=Path, default=None, help="default: <archive>/../eval_config.yaml when it exists")
    ap.add_argument("--poll-s", type=float, default=30.0)
    ap.add_argument("--budget-h", type=float, default=BUDGET_H)
    return ap.parse_args(argv)


def main(argv=None, backend_factory=None, clock=time.time, log=print) -> int:
    a = parse(argv)
    if backend_factory is None and sys.platform == "darwin":
        print("REFUSED: the model runs on a Kaggle GPU, not the Mac.", flush=True)
        return EXIT_MAC
    try:
        report = run(a, backend_factory or _real_backend, clock, log)
    except (Refused, OutOfTime, QF.KG.Refused) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    for lg, cell in report.items():
        print(f"{lg}: {cell['result']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
