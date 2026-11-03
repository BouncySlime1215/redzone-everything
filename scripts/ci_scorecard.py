"""Fast scorecard smoke run for CI: a small, fixed slice of every scorecard, as one JSON file.

    python scripts/ci_scorecard.py --out head.json                  # this checkout
    python scripts/ci_scorecard.py --root ../base --out base.json   # another checkout, same data

Each scorecard runs as its own process from `--root`, so a base-branch checkout is graded with
the base branch's code (and this script never imports the app). Both checkouts must read the same
pinned data: `data/archive/`, `data/eval_config.yaml` and `data/demo/` (CI links one copy into
each). A scorecard that crashes or times out is recorded as an error instead of stopping the
others, so one broken league still leaves numbers for the rest.

PRs into `integration/next` (and pushes to it) run `--full`, the whole merge scorecard.
The slice (all overridable): NFL `--n 4` slates, NBA and NHL `--nights 3`, and the Nov 1 mixed
demo. It is a smoke test, not the merge scorecard: numbers are only comparable with another run
of the same slice on the same data, which is exactly what the CI comment shows (PR vs base).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def last_json(text: str):
    """The last top-level JSON object a scorecard printed (they print one, indented, at the end;
    the demo prints its timeline first)."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].rstrip() == "{":
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object in the scorecard's output")


def cards(a) -> list[tuple[str, list[str], Path]]:
    """(name, argv, data it needs) for each scorecard in the slice."""
    out = [("nfl", ["scripts/eval.py", "--n", str(a.nfl_n)], Path("data/archive/nfl"))]
    for lg in ("nba", "nhl"):
        out.append((lg, ["scripts/eval_sports.py", lg, "--nights", str(a.nights)], Path(f"data/archive/{lg}")))
    if a.demo_day:
        out.append((f"demo_{a.demo_day}", ["-m", "redzone.demo", a.demo_day, "--summary", "--json"],
                    Path(f"data/demo/{a.demo_day}")))
    return out


def run_one(root: Path, argv: list[str], timeout: float) -> tuple[dict | None, str | None, float]:
    t0 = time.time()
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONHASHSEED": "0"}
    try:
        p = subprocess.run([sys.executable, *argv], cwd=root, env=env, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f} s", time.time() - t0
    took = time.time() - t0
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-6:]
        return None, f"exit {p.returncode}: " + " | ".join(tail), took
    try:
        return last_json(p.stdout), None, took
    except ValueError as e:
        return None, str(e), took


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=HERE, help="checkout to grade (default: this one)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--nfl-n", type=int, default=int(os.environ.get("RZ_SMOKE_NFL_N", 4)))
    ap.add_argument("--nights", type=int, default=int(os.environ.get("RZ_SMOKE_NIGHTS", 3)))
    ap.add_argument("--demo-day", default=os.environ.get("RZ_SMOKE_DEMO_DAY", "20261101"),
                    help="mixed demo day to replay ('' to skip)")
    ap.add_argument("--full", action="store_true", default=os.environ.get("RZ_SCORECARD_FULL") == "1",
                    help="the whole merge scorecard (NFL 16 slates, NBA/NHL 14 nights) instead of the slice")
    ap.add_argument("--timeout", type=float, default=None,
                    help="seconds per scorecard (default 900, 2400 with --full)")
    a = ap.parse_args(argv)
    if a.full:
        a.nfl_n, a.nights = 16, 14   # the scorecards' own defaults (scripts/eval.py, eval_sports.py)
    if a.timeout is None:
        a.timeout = 2400 if a.full else 900
    root = a.root.resolve()
    if a.nfl_n < 1 or a.nights < 1:
        ap.error("--nfl-n and --nights must be at least 1")
    result = {"root": str(root), "slice": {"nfl_n": a.nfl_n, "nights": a.nights, "demo_day": a.demo_day,
                                           "full": a.full},
              "cards": {}}
    for name, args, needs in cards(a):
        if not (root / needs).exists():
            result["cards"][name] = {"error": f"no {needs} (pinned data missing)", "seconds": 0}
            print(f"{name}: skipped, no {needs}", flush=True)
            continue
        card, err, took = run_one(root, args, a.timeout)
        result["cards"][name] = {"metrics": card, "seconds": round(took)} if card is not None \
            else {"error": err, "seconds": round(took)}
        print(f"{name}: {'ok' if card is not None else err} ({took:.0f} s)", flush=True)
    a.out.write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
