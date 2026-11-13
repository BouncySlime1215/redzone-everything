"""Evaluation harness command (task F-04): run one named arm on a fixed set of real nights, write ONE JSON verdict.

    python scripts/arm_verdict.py run --arm director_v1 --nights 20261021 20261023          # paired with planner
    python scripts/arm_verdict.py run --arm random_legal --night-set dev2 --minutes 60
    python scripts/arm_verdict.py run --arm stage6_rerank --hook mod:make_hook --model m.npz --nights 20261021
    python scripts/arm_verdict.py recompute data/armeval/<run>          # the verifier: recount from the raw logs
    python scripts/arm_verdict.py arms                                   # the arms and their seams
    python scripts/arm_verdict.py l105 data/armeval/<run>               # stage 6's L105 metric on that run (A-03)

Writes into --out (default data/armeval/<arm>-<config hash>, git-ignored): verdict.json (canonical bytes: the same
arm, nights, options and commit give the same file), raw/<arm>/<night>.json (every cut, the screen timeline, every
graded item shown; the baseline's too), raw/manifest.json (sha256 of each raw log; the registry artifact), and
timing.json (CPU seconds: the only output that changes run to run, so it stays out of the verdict).
Night specs are NIGHT[:GAMES][+nflI] (park_replay dev nights; +nflI adds a time-shifted one-game NFL night: the
football + NBA mix). Locked nights are refused (redzone/lab/fence.py, .claude/hooks/guard.py G8). The baseline arm
(default planner) is replayed on the same nights and every number is paired with it. Replays run one at a time;
run it under `taskpolicy -b nice -n 15` on the Mac. The process re-runs itself with PYTHONHASHSEED=0.
The checkout-level graders do not take an arm: `--scorecard FILE` records a scripts/ci_scorecard.py output of the
same commit next to the verdict (its sha256 and card names), and the end-to-end night (python -m redzone.lab.night)
grades the app's runtime, not a decision policy.
Why: WORKPLAN v2 F-04 / V2-PLAN 8.4: one command, one verdict per arm, recountable by an independent verifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HARNESS_REPO = Path(__file__).resolve().parent.parent
if HARNESS_REPO.as_posix() not in sys.path:
    sys.path.insert(0, HARNESS_REPO.as_posix())

from redzone.lab import fence  # noqa: E402
from redzone.lab.armeval import arms as A  # noqa: E402
from redzone.lab.armeval import episode as E  # noqa: E402
from redzone.lab.armeval import recompute as R  # noqa: E402
from redzone.lab.armeval import stage6 as S6  # noqa: E402
from redzone.lab.armeval import verdict as V  # noqa: E402

NIGHT_SETS = {                                   # fixed sets of real dev nights (park_replay refuses any other night)
    "dev2": ["20261021", "20261023"],
    "dev3": ["20261021", "20261023", "20261028"],   # L114's three NBA nights; gym2_parity's
    "dev3_football": ["20261021+nfl0", "20261023+nfl1", "20261028+nfl2"],
}
CODE_PATHS = ("redzone", "scripts/park_replay.py", "scripts/park_ceiling.py", "scripts/arm_verdict.py")


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=HARNESS_REPO, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def code_hash() -> str:
    """sha256 over (path, content sha256) of every tracked source file the run can execute, as it is on disk."""
    lines = []
    for rel in sorted(git("ls-files", "--", *CODE_PATHS).split()):
        p = HARNESS_REPO / rel
        if p.suffix in (".py", ".yaml", ".yml", ".json") and p.is_file():
            lines.append(f"{rel}\t{fence.file_sha256(p)}")
    return V.sha256_text("\n".join(lines))


def git_meta() -> dict:
    sha = git("rev-parse", "HEAD").strip()
    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    return {"git_sha": sha, "dirty": dirty}


def fence_block(nights: list, extra: list) -> dict:
    """Locked nights are refused outright; tuning-window nights are named (the verdict cannot PASS on them)."""
    where = {n: (fence.fenced(n) or None) for n in [*nights, *extra]}
    locked = sorted(n for n, w in where.items() if w is not None and w.name == "locked")
    if locked:
        raise SystemExit(f"REFUSED: locked night(s) {', '.join(locked)}: scored by the release gate only")
    return {"nights": {n: (w.name if w else "none") for n, w in sorted(where.items())},
            "tuning_nights": sorted(n for n, w in where.items() if w is not None and w.name == "tuning"),
            "locked_refused": True}


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def baseline_cache(a, meta: dict, base) -> dict:
    """The baseline's cards and raw logs from an earlier verdict of the SAME commit, code, nights, games, minutes and
    seed (so a baseline whole night is replayed once, not once per arm). Refuses anything else."""
    d = Path(a.baseline_from)
    old = json.loads((d / "verdict.json").read_text())
    om, oc, nc = old["meta"], old["meta"]["config"], meta["config"]
    same = (om["git_sha"] == meta["git_sha"] and om["code_hash"] == meta["code_hash"] and not om["dirty"] and not meta["dirty"]
            and om["data_manifest_hash"] == meta["data_manifest_hash"]
            and all(oc[k] == nc[k] for k in ("nights", "games", "minutes", "seed", "engine")))
    if base is None or not same:
        raise SystemExit(f"--baseline-from {d}: not the same commit, code, data, nights, games, minutes and seed")
    if old["arm"]["name"] == base.name:
        cards = old["per_night"]
    elif old["baseline"] == base.name and old["baseline_per_night"] is not None:
        cards = old["baseline_per_night"]
    else:
        raise SystemExit(f"--baseline-from {d}: holds no {base.name} cards")
    out = {}
    for c in cards:
        text = (d / "raw" / base.name / f"{c['night']}.json").read_text()
        if V.sha256_text(text) != old["raw_logs"].get(f"{base.name}/{c['night']}.json"):
            raise SystemExit(f"--baseline-from {d}: raw/{base.name}/{c['night']}.json changed since that run")
        out[c["night"]] = (c, text)
    return out


def run_cmd(a) -> int:
    specs = list(a.nights or []) + list(NIGHT_SETS.get(a.night_set, []) if a.night_set else [])
    if a.night_set and a.night_set not in NIGHT_SETS:
        raise SystemExit(f"unknown night set {a.night_set}; known: {', '.join(NIGHT_SETS)}")
    if not specs:
        raise SystemExit("give --nights or --night-set")
    arm = A.make_arm(a.arm, a.seed, a.hook, a.model)
    base = None if a.baseline in ("", "none") else A.make_arm(a.baseline, a.seed)
    loaded = [E.load_night(s, a.games) for s in specs]
    fb = fence_block([x.night for x in loaded], [n for x in loaded for n in x.extra_nights])
    if arm.heavy:
        fb["model"] = arm.check([x.night for x in loaded])          # HeavyArmUnavailable / FenceError stop here
    else:
        fb["model"] = "no model (rule arm)"
    manifest_data = {name: fence.file_sha256(p) for x in loaded for name, p in x.files}
    prereg = json.loads(Path(a.prereg).read_text()) if a.prereg else None
    config = {"arm": arm.describe(), "baseline": base.describe() if base else None, "nights": specs, "games": a.games,
              "minutes": a.minutes, "seed": a.seed, "engine": V.ARMEVAL_ENGINE}
    meta = {**git_meta(), "code_hash": code_hash(), "config_hash": V.sha256_text(V.canon_json(config)),
            "data_manifest_hash": V.sha256_text(V.canon_json(manifest_data)), "data_manifest": manifest_data,
            "prereg_hash": V.sha256_text(Path(a.prereg).read_text()) if a.prereg else V.sha256_text(""),
            "prereg": bool(a.prereg), "seed": a.seed, "arm": arm.name, "baseline": base.name if base else None,
            "fence": fb, "config": config}
    out = Path(a.out) if a.out else HARNESS_REPO / "data/armeval" / f"{arm.name}-{meta['config_hash'][:12]}"
    census = Path(a.census) if a.census else None
    cards, bcards, manifest, timing = [], [] if base else None, {}, {}
    cached = baseline_cache(a, meta, base) if a.baseline_from else None
    for x in loaded:
        if cached is not None:
            card, text = cached[x.spec]
            rel = f"{base.name}/{x.spec}.json"
            manifest[rel] = write(out / "raw" / rel, text)
            bcards.append(card)
        for who in ([arm] if base is None or base.name == arm.name or cached is not None else [arm, base]):
            t0 = time.process_time()
            card, raw = E.run_night(who, x, minutes=a.minutes, seed=a.seed, census=census)
            timing[f"{who.name}/{x.spec}"] = round(time.process_time() - t0, 1)
            rel = f"{who.name}/{x.spec}.json"
            manifest[rel] = write(out / "raw" / rel, V.canon_json(raw))
            (cards if who is arm else bcards).append(card)
            print(f"{who.name} {x.spec}: {card['pace']['jumps']} jumps in {card['minutes']} min, Rule #1 "
                  f"{[card['rule1'][k] for k in V.ARMEVAL_RULE1]}, big {card['big']['seen_live']}/{card['big']['total']} "
                  f"({timing[f'{who.name}/{x.spec}']} s CPU)", flush=True)
        if base is not None and base.name == arm.name:
            bcards.append(cards[-1])
    write(out / "raw/manifest.json", V.canon_json(manifest))
    extra = {}
    if a.scorecard:
        sc = json.loads(Path(a.scorecard).read_text())
        extra["checkout_scorecard"] = {"sha256": fence.file_sha256(Path(a.scorecard)),
                                       "cards": {k: ("ok" if "metrics" in v else v.get("error", "?"))
                                                 for k, v in sorted((sc.get("cards") or {}).items())}}
    v = V.build_verdict(arm.describe(), cards, bcards, meta, prereg=prereg, seed=a.seed, raw_manifest=manifest, extra=extra)
    vsha = write(out / "verdict.json", V.canon_json(v))
    write(out / "timing.json", V.canon_json({"cpu_s": timing, "note": "not part of the verdict (changes run to run)"}))
    print(f"VERDICT {v['verdict']} ({'; '.join(v['fails'] + v['why_not_pass']) or 'all bars met'})")
    print(f"wrote {out / 'verdict.json'} sha256 {vsha}")
    return 0


def recompute_cmd(a) -> int:
    d = Path(a.dir)
    v = json.loads((d / "verdict.json").read_text())
    manifest = json.loads((d / "raw/manifest.json").read_text())
    probs = []
    if V.sha256_text(V.canon_json(manifest)) != v["raw_manifest_sha256"]:
        probs.append("raw/manifest.json is not the manifest the verdict names")
    for rel, sha in sorted(manifest.items()):
        if fence.file_sha256(d / "raw" / rel) != sha:
            probs.append(f"raw/{rel} changed since the run")
    games, files = {}, {}
    for spec in v["nights"]:
        x = E.load_night(spec, v["meta"]["config"]["games"])
        rs = list(x.rs)
        if x.nfl >= 0:
            rs.append(E.replay_module().shifted_nfl(x.nfl, x.slate)[0])
        games[spec], files[spec] = rs, dict(x.files)
    sides = [(v["arm"]["name"], v["per_night"])]
    if v["baseline"] and v["baseline"] != v["arm"]["name"]:
        sides.append((v["baseline"], v["baseline_per_night"]))
    for who, cards in sides:
        raws = {spec: json.loads((d / "raw" / who / f"{spec}.json").read_text()) for spec in v["nights"]}
        probs += [f"{who} {p}" for p in R.recheck({"per_night": cards}, raws, games, files)]
    if V.totals(v["per_night"]) != v["scorecard"]:
        probs.append("scorecard is not the fold of the per-night cards")
    if v["baseline_per_night"] is not None and V.totals(v["baseline_per_night"]) != v["baseline_scorecard"]:
        probs.append("baseline scorecard is not the fold of its per-night cards")
    print("\n".join(probs) if probs else "every recounted number matches")
    print("RECOMPUTE " + ("FAIL" if probs else "PASS"))
    return 1 if probs else 0


def l105_cmd(a) -> int:
    """L105's metric (high-leverage points, pooled and per mix, plus all value) on a run's arm vs its baseline, from the
    raw logs and the archive's grader facts. Writes l105.json next to the verdict (outside the verdict's bytes)."""
    d = Path(a.dir)
    v = json.loads((d / "verdict.json").read_text())
    if not v["baseline"] or v["baseline"] == v["arm"]["name"]:
        raise SystemExit("l105 needs a run paired with a baseline arm")
    pairs = []
    for card in v["per_night"]:
        spec = card["night"]
        x = E.load_night(spec, v["meta"]["config"]["games"])
        facts = list(x.slate) + ([E.replay_module().shifted_nfl(x.nfl, x.slate)[1]] if x.nfl >= 0 else [])
        sides = [S6.stage6_hl_card(json.loads((d / "raw" / who / f"{spec}.json").read_text()), facts)
                 for who in (v["arm"]["name"], v["baseline"])]
        pairs.append((card["mix"], *sides))
    out = {"arm": v["arm"]["name"], "baseline": v["baseline"], "verdict_sha256": fence.file_sha256(d / "verdict.json"),
           **S6.stage6_l105(pairs)}
    write(d / "l105.json", V.canon_json(out))
    print(V.canon_json(out), end="")
    return 0


def arms_cmd(_a) -> int:
    for name, cls in sorted(A.ARMEVAL_ARMS.items()):
        print(f"{name:14s} seam {cls.seam:6s} {'HEAVY (Kaggle / Colab model)' if cls.heavy else 'local'}")
    return 0


def main(argv=None) -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, *sys.argv])
    ap = argparse.ArgumentParser(description="Run one arm on real nights; one JSON verdict (F-04).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", required=True)
    r.add_argument("--baseline", default="planner", help="the arm every number is paired with ('none': no pairing)")
    r.add_argument("--nights", nargs="*", default=[])
    r.add_argument("--night-set", default="")
    r.add_argument("--games", type=int, default=4)
    r.add_argument("--minutes", type=float, default=0.0, help="0 = the whole night")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--out", default="")
    r.add_argument("--baseline-from", default="", help="reuse the baseline's cards and raw logs from an earlier run "
                   "folder of the same commit, nights and options (one whole-night baseline replay, not one per arm)")
    r.add_argument("--prereg", default="", help="pre-registered bars (JSON); without it the verdict is exploratory")
    r.add_argument("--hook", default="", help="heavy arms: MODULE:FACTORY or path/to/file.py:FACTORY")
    r.add_argument("--model", default="", help="heavy arms: the model file (leak-fence checked)")
    r.add_argument("--census", default="", help="the census nights.csv (models/mix-census-v1): the real night's mix")
    r.add_argument("--scorecard", default="", help="a scripts/ci_scorecard.py output of the same commit to record")
    c = sub.add_parser("recompute")
    c.add_argument("dir")
    sub.add_parser("arms")
    lv = sub.add_parser("l105")
    lv.add_argument("dir")
    a = ap.parse_args(argv)
    return {"run": run_cmd, "recompute": recompute_cmd, "arms": arms_cmd, "l105": l105_cmd}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
