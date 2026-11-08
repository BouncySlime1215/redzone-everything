#!/usr/bin/env python3
"""[Data division E] Build synthetic + teacher token shards for the NFM, inside the 3x-real cap.

Serves: EXP-1400 (Teacher answers landed as tokens) and EXP-1401 (mirror + tempo re-time for the world model).
Owner: Data planner E. Runs on Kaggle / Colab CPU next to the factory, never on the Mac. Light: numpy only.

    # mirror + re-time the factory's vocab v1 shards, train nights only, inside the cap
    python scripts/synth_build.py augment --shards TOKENS --out SYN --train-before 20240901 \
        --mirror 1.0 --retime 1.0 --seed 7
    # land the EXP-086 Teacher's teacher_out.jsonl as teacher-v1 tokens (side channel, aux target only)
    python scripts/synth_build.py teacher --answers teacher_out.jsonl --out SYN
    # the cap check over every manifest in SYN (exit 1 if any league is over 3x real)
    python scripts/synth_build.py check SYN

Inputs: TOKENS/<league>/<year>.npy + <year>.json (scripts/noggin_tokens.py: games = [gid, night, offset, length]).
Outputs, each with license "personal-use, not for commercial release" and synthetic=true:
    SYN/<league>/<year>.<aug>.npy + .json      same shard format, gids suffixed ~mirror / ~retime
    SYN/teacher/<league>.npz                   game_id, moment, hindsight, tokens (N x 6, teacher-v1)
    SYN/teacher/text.jsonl                     storyline / why / next per answer, for the Mind (never tokens)
    SYN/manifest_augment.json, SYN/manifest_teacher.json   per-league counts the ledger folds in
Why: Builds synthetic and teacher token shards for the NFM, inside the 3x-real cap (docstring).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.contracts import events as V  # noqa: E402
from redzone.noggin import synth as S  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _shards(root: Path):
    for meta in sorted(root.glob("*/*.json")):
        if meta.stem.count(".") or not meta.with_suffix(".npy").exists():
            continue                                  # skip our own <year>.<aug>.json outputs
        yield meta.parent.name, meta, json.loads(meta.read_text())


def augment(a) -> dict:
    rng = np.random.default_rng(a.seed)
    real: dict[str, int] = {}
    shards: dict[str, list] = {}
    for lg, meta, m in _shards(a.shards):
        if m.get("synthetic"):
            continue
        real[lg] = real.get(lg, 0) + int(m.get("tokens") or sum(g[3] for g in m["games"]))
        shards.setdefault(lg, []).append((meta, m))
    prior = {lg: int(x) for lg, _, x in (s.partition("=") for s in a.other_synthetic) if x}
    per: dict[str, dict] = {}
    for lg, items in sorted(shards.items()):
        left = S.budget(real[lg], prior.get(lg, 0), a.cap)
        want = {"mirror": int(a.mirror * real[lg]), "retime": int(a.retime * real[lg])}
        made = {"mirror": 0, "retime": 0}
        skipped_nights = 0
        for meta, m in items:
            toks = np.load(meta.with_suffix(".npy"), mmap_mode="r")
            for aug in ("mirror", "retime"):
                out, games, off = [], [], 0
                for gid, night, o, n in m["games"]:
                    if str(night) >= a.train_before:
                        skipped_nights += aug == "mirror"
                        continue
                    if made[aug] + n > want[aug] or n > left:
                        break
                    g = np.asarray(toks[o:o + n])
                    g = S.mirror_game(g) if aug == "mirror" else S.retime_game(g, rng, a.jitter)
                    out.append(g)
                    games.append([f"{gid}~{aug}", night, off, n])
                    off += n
                    made[aug] += n
                    left -= n
                if not games:
                    continue
                dst = a.out / lg
                dst.mkdir(parents=True, exist_ok=True)
                np.save(dst / f"{meta.stem}.{aug}.npy", np.concatenate(out).astype(np.uint16))
                (dst / f"{meta.stem}.{aug}.json").write_text(json.dumps({
                    "synthetic": True, "complete": True, "tags": ["synthetic", aug], "aug": aug, "source_shard": f"{lg}/{meta.name}", "license": S.LICENSE,
                    "vocab": V.VOCAB_VERSION, "train_before": a.train_before, "seed": a.seed,
                    "jitter": a.jitter if aug == "retime" else None, "tokens": off, "games": games}) + "\n")
        per[lg] = {"real_tokens": real[lg], "synthetic_tokens": prior.get(lg, 0) + sum(made.values()),
                   "mirror_tokens": made["mirror"], "retime_tokens": made["retime"],
                   "held_out_games_skipped": skipped_nights}
    man = {"kind": "augment", "built": _now(), "license": S.LICENSE, "cap_x": a.cap, "train_before": a.train_before,
           "seed": a.seed, "vocab": V.VOCAB_VERSION, "per_league": per}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "manifest_augment.json").write_text(json.dumps(man, indent=1) + "\n")
    return man


def teacher(a) -> dict:
    rows, bad, skipped = {}, 0, 0
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "teacher").mkdir(exist_ok=True)
    with open(a.answers) as f, open(a.out / "teacher" / "text.jsonl", "w") as tx:
        for line in f:
            if not line.strip():
                continue
            ans = json.loads(line)
            r = S.teacher_tokens(ans)
            if r is None:
                bad += "|jump" not in str(ans.get("id", ""))
                skipped += "|jump" in str(ans.get("id", ""))
                continue
            rows.setdefault(r.league, []).append(r)
            tx.write(json.dumps({"league": r.league, "game_id": r.game_id, "moment": r.moment,
                                 "hindsight": r.hindsight, "model": ans.get("model"), **r.text,
                                 "license": S.LICENSE, "synthetic": True}) + "\n")
    per = {}
    for lg, rs in sorted(rows.items()):
        np.savez_compressed(a.out / "teacher" / f"{lg}.npz",
                            game_id=np.array([r.game_id for r in rs]), moment=np.array([r.moment for r in rs]),
                            hindsight=np.array([r.hindsight for r in rs]),
                            tokens=np.array([r.tokens for r in rs], np.uint16))
        per[lg] = {"answers": len(rs), "hindsight": sum(r.hindsight for r in rs),
                   "synthetic_tokens": len(rs) * S.TEACHER_TOKENS}
    man = {"kind": "teacher", "built": _now(), "license": S.LICENSE, "vocab": S.TEACHER_VOCAB_VERSION,
           "vocab_size": S.TEACHER_VOCAB, "source": str(a.answers.name), "unreadable": bad, "skipped_other_tasks": skipped,
           "use": "grounded (hindsight) and blind: auxiliary target only, never context; commentary (causal, mode 2): may be context", "per_league": per}
    (a.out / "manifest_teacher.json").write_text(json.dumps(man, indent=1) + "\n")
    return man


def totals(out: Path) -> dict:
    """Per league: real tokens (from the augment manifest) and synthetic tokens over every manifest in out."""
    per: dict[str, dict] = {}
    for p in sorted(out.glob("manifest_*.json")):
        for lg, e in json.loads(p.read_text()).get("per_league", {}).items():
            t = per.setdefault(lg, {"real_tokens": 0, "synthetic_tokens": 0})
            t["real_tokens"] = max(t["real_tokens"], int(e.get("real_tokens") or 0))
            t["synthetic_tokens"] += int(e.get("synthetic_tokens") or 0)
    return per


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("augment")
    g.add_argument("--shards", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--train-before", required=True, help="YYYYMMDD: the first valid night; nothing on or after it")
    g.add_argument("--mirror", type=float, default=1.0, help="mirror tokens as a multiple of real")
    g.add_argument("--retime", type=float, default=1.0, help="re-timed tokens as a multiple of real")
    g.add_argument("--jitter", type=float, default=0.10)
    g.add_argument("--cap", type=float, default=S.CAP_X)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--other-synthetic", nargs="*", default=[], help="league=tokens already used (self-play, teacher)")
    t = sub.add_parser("teacher")
    t.add_argument("--answers", type=Path, required=True)
    t.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check")
    c.add_argument("out", type=Path)
    c.add_argument("--cap", type=float, default=S.CAP_X)
    a = ap.parse_args(argv)
    if a.cmd == "augment":
        if not (len(a.train_before) == 8 and a.train_before.isdigit()):
            ap.error("--train-before must be YYYYMMDD")
        print(json.dumps(augment(a), indent=1))
    elif a.cmd == "teacher":
        print(json.dumps(teacher(a), indent=1))
    per = totals(a.out)
    bad = S.check_cap(per, getattr(a, "cap", S.CAP_X))
    for lg, e in per.items():
        r = e["synthetic_tokens"] / e["real_tokens"] if e["real_tokens"] else float("nan")
        print(f"{lg}: real {e['real_tokens']:,}  synthetic {e['synthetic_tokens']:,}  ratio {r:.2f}x")
    for b in bad:
        if a.cmd == "check" or " x real 0" not in b:      # teacher-only runs don't know real tokens yet
            print("OVER CAP:", b, file=sys.stderr)
    return 1 if bad and a.cmd == "check" else 0


if __name__ == "__main__":
    raise SystemExit(main())
