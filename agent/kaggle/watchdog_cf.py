#!/usr/bin/env python3
"""[V8.1 watchdog, EXP-101] Counterfactual training rows: the same TRAIN wordings, with the field that decides the
answer flipped on purpose, so the student learns to read it.

    python agent/kaggle/watchdog_cf.py --make-data <checkout of claude/watchdog-v7-data>/agent/train/make_data.py \\
        --out agent/kaggle/assets/watchdog_v81/train.jsonl.gz

Serves: North Star "app health / watchdog gate" (the V8 watchdog failed its 95% gate at 83.1%); owner: lane 1.

Why: V8's misses sit in the kinds whose answer turns on a field, not on the wording: core_loop (count 5+ and
crunch on screen), resource (crunch on screen), paused (which stick), mixed (several of these at once). Kinds with
one fixed answer are 40-42/42. The V7 rows sample those fields at random, so a wording rarely appears on both sides
of its threshold. Here every TRAIN wording of a conditional kind gets rows on both sides (count just under and at
or over its threshold, crunch on and off, one stick and two), labelled by the same make_data teacher and rules.

Valid and test are untouched (they come from their own seeds in make_data; this only reads TRAIN wordings), so the
686 held-out test rows stay byte-identical and the grade is comparable to V8. It must run against V7's own
make_data and agent/policy (claude/watchdog-v7-data), the ones the test labels came from.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import itertools
import json
import random
import sys
from pathlib import Path

SEED = 101
# kind -> the counts that straddle its threshold (teach_novel: core_loop/component 5, upstream 10, network 3)
COUNTS = {"core_loop": [3, 4, 5, 6], "component": [3, 4, 5, 6], "upstream": [8, 9, 10, 12], "network": [1, 2, 3, 4],
          "resource": [1, 2, 6, 12], "paused": [1, 3], "frozen": [1, 3], "prompt": [1, 3]}
CRUNCH = {"core_loop", "resource", "frozen", "paused", "component"}
TWO_STICK = {"paused", "frozen", "prompt"}
N_MIXED = 600


def load(make_data: Path):
    sys.path.insert(0, str(make_data.resolve().parents[2]))           # its repo root: agent.policy from V7
    spec = importlib.util.spec_from_file_location("make_data_v7", make_data)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def rows(md) -> list[dict]:
    rng = random.Random(SEED)
    real_count, real_base = md.count, md.base_obs
    force: dict = {}

    def count(r):
        return force["n"] if "n" in force else real_count(r)

    def base_obs(r, two_stick=None):
        o = real_base(r, two_stick=force.get("two", two_stick))
        if "crunch" in force:
            o.crunch_on_screen = force["crunch"]
            o.live_games = max(1, o.live_games) if force["crunch"] else o.live_games
        return o

    md.count, md.base_obs = count, base_obs
    out = []
    try:
        cos = md.NOVEL["cosmetic"]["train"]
        for kind, ns in COUNTS.items():
            crunch = (True, False) if kind in CRUNCH else (None,)
            two = (True, False) if kind in TWO_STICK else (None,)
            for w in md.NOVEL[kind]["train"]:
                for n, c, t in itertools.product(ns, crunch, two):
                    force.clear()
                    force["n"] = n
                    if c is not None:
                        force["crunch"] = c
                    if t is not None:
                        force["two"] = t
                    out.append(md.sample(rng, kind, w, cos))
        words = {k: d["train"] for k, d in md.NOVEL.items()}
        for i in range(N_MIXED):
            force.clear()
            force["crunch"] = i % 2 == 0
            out.append(md.mixed_sample(rng, words))
    finally:
        md.count, md.base_obs = real_count, real_base
    rng.shuffle(out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-data", type=Path, required=True)
    ap.add_argument("--v7-train", type=Path, required=True, help="agent/kaggle/assets/watchdog_v7/train.jsonl.gz")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    md = load(a.make_data)
    md.check_split()
    extra = rows(md)
    with gzip.open(a.v7_train, "rt") as f:
        base = [json.loads(x) for x in f]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt") as f:
        for r in base:
            f.write(json.dumps(r) + "\n")
        for r in extra:
            f.write(json.dumps({"messages": r["messages"]}) + "\n")
    kinds: dict[str, int] = {}
    for r in extra:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(json.dumps({"v7_rows": len(base), "counterfactual_rows": len(extra), "by_kind": kinds}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
