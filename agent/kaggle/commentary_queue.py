#!/usr/bin/env python3
"""[EXP-1402, Data planner E] Causal per-play commentary requests for the 32B Teacher, TRAIN nights only.

Serves: the NFM's teacher channel (redzone/noggin/synth.py mode 2) and the Mind's commentary text. Owner: Data
planner E. Runs inside the Colab notebook colab/teacher32b_commentary_a100.ipynb (CPU part), never on the Mac.

    python agent/kaggle/commentary_queue.py --moments MOMENTS --out requests.jsonl --leagues nfl nba cbb nhl mlb

Reads the moment rows build_moments_kaggle.py writes (MOMENTS/rows/<league>_train.jsonl.gz, one row per live play)
and writes one request per play in agent/kaggle/run_teacher.py's format, id "<league>:<gid>:<i>|commentary".
The Teacher sees ONLY row["brief"]: what the live app knows at that play. No hindsight field is ever put in the
prompt (big_next, next_text, t_big, target ... are dropped), so the answer is causal and may be used as context.
Order: your-team, crunch and late plays first, then the rest; leagues round-robin, so a budget stop stays balanced.
Only split == "train" rows are read; valid, test and locked nights never reach the queue.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
from pathlib import Path

SEED = 7
TASK = "commentary_causal"
MAX_TOKENS = 90
SYSTEM = (
    "You are a sports analyst writing one line of live commentary for ONE play of a game, for a model that learns "
    "what matters in sports. You see only the brief: the game state right after this play. Never use facts that "
    "are not in the brief, and never guess the future as fact. Answer with ONE JSON object only, keys in this "
    "order: importance (integer 0-10: how much this moment matters to the game's outcome and to a neutral viewer; "
    "most plays are 1-3, a tied game's final minute is 8+, garbage time is 0-1), lock_in (none|watch|must_see), "
    "next (score|turnover|stoppage|big_play|routine: the single most likely next thing), confidence (0-1 for "
    "next), commentary (at most 15 words, plain, no hype words)."
)
HINDSIGHT_KEYS = ("big_next", "crunch_next", "next_text", "t_big", "t_break", "target", "export_target", "y", "hard")


def rows(moments: Path, league: str):
    p = moments / "rows" / f"{league}_train.jsonl.gz"
    if not p.exists():
        return
    with gzip.open(p, "rt") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("split") == "train" and r.get("brief"):
                    yield r


def priority(r: dict) -> int:
    return 0 if r.get("mine") else 1 if r.get("crunch") else 2 if r.get("late") else 3


def request(r: dict) -> dict:
    return {"id": f"{r['id']}|commentary", "task": TASK, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": r["brief"]}]}


def build(moments: Path, leagues: list[str], per_league: int | None = None) -> list[dict]:
    rng = random.Random(SEED)
    per = []
    for lg in leagues:
        rs = list(rows(moments, lg))
        rng.shuffle(rs)
        rs.sort(key=priority)                       # stable: shuffled within each priority tier
        per.append([request(r) for r in rs[:per_league]])
    return [m[k] for k in range(max((len(m) for m in per), default=0)) for m in per if k < len(m)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--moments", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", nargs="+", default=["nfl", "cfb", "nba", "cbb", "nhl", "mlb"])
    ap.add_argument("--per-league", type=int, default=None)
    a = ap.parse_args(argv)
    reqs = build(a.moments, a.leagues, a.per_league)
    with open(a.out, "w") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")
    counts = {lg: sum(1 for r in reqs if r["id"].startswith(f"{lg}:")) for lg in a.leagues}
    print(json.dumps({"requests": len(reqs), "per_league": counts, "task": TASK}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
