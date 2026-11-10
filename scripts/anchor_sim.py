"""Anchor mode, flag off vs on, on replayed nights: the counters of docs/plans/2026-11-09-anchor-mode.md section 6.

    python scripts/anchor_sim.py --split fit                       # NFL slates of the fit split (scripts/eval.py's set)
    python scripts/anchor_sim.py --split fit --set timing.measured=true
    python scripts/anchor_sim.py --fixture                         # the small NBA fixture night the tests replay

Per run: entries_mid_play (joins mid-play, must be 0), mid_play_cuts and mid_play_cuts_high (leaves mid-play; the anchor is
crunch, so a leave of it mid-play is a high one), crunch_on_replay (crunch plays that aired while a clip was on screen: an
anchor live play missed while away, or a return after the restart; must be 0 with the flag on), crunch live, big seen, and
the decider's anchor counters (anchors, clips aired, gaps with no budget / nothing fitting / the stick loading / vetoed at the
restart, late clips dropped or kept for after the anchor). Heavy: one whole replay per night and flag; not for the live Mac.
Why: anchor mode ships off until a sim over the fit nights shows these counters (spec section 8 step 3).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.lab import backtest, evaldata  # noqa: E402

KEYS = ("entries", "entries_mid_play", "mid_play_cuts", "mid_play_cuts_high", "crunch", "crunch_live", "crunch_on_replay",
        "big", "big_seen", "on_replay_s")


def _eval_cfg() -> Config:
    """scripts/eval.py's config: the pinned copy when one exists, switch costs from the one shipped source."""
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    return cfg


def _nights(a) -> list[tuple[list, set[str], dict | None, object]]:
    """(replays, your teams, targets, a fresh config per run) per night."""
    if a.fixture:
        fix = ROOT / "tests" / "fixtures" / "your_team_crunch"
        evs = json.loads((fix / "espn_nba_scoreboard_20251212.json").read_text())["events"]
        from redzone.models import League
        reps = [r for r in (backtest.Replay.load(League.NBA, fix / f"espn_nba_summary_{e['id']}.json") for e in evs) if r]
        targets = {r.gid: ("prime:" if i % 3 == 0 else "youtubetv:") + r.gid for i, r in enumerate(reps)}
        return [(reps, {"WSH"}, targets, lambda: Config(user_path=fix / "eval_config.yaml"))]
    from scripts.eval import eval_set
    teams = set(_eval_cfg().get("teams.nfl") or ())
    return [(reps, teams, None, _eval_cfg) for reps in eval_set(a.n, split=a.split)]


def _run(reps, teams, targets, cfg, sets: list[str]) -> dict:
    for kv in sets:
        evaldata.apply_set(cfg, kv)
    made: list = []
    real = backtest.Decider

    class Watched(real):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            made.append(self)
    backtest.Decider = Watched
    try:
        kw = dict(targets=targets, seed=1, **backtest.feed_lag_knobs(70.0, 90.0)) if targets else {}
        r = backtest.run(reps, cfg, teams, **kw)
    finally:
        backtest.Decider = real
    rc = r.cut.summary()
    row = {"entries": rc["entries"], "entries_mid_play": rc["entries_mid_play"], "mid_play_cuts": len(r.mid_play_cuts),
           "mid_play_cuts_high": len(r.mid_play_cuts_high), "crunch": r.crunch, "crunch_live": r.crunch_live,
           "crunch_on_replay": r.crunch_on_replay, "big": r.big_plays, "big_seen": r.big_plays_caught,
           "on_replay_s": round(r.behind_live_s, 1)}
    anchor = Counter()
    for d in made:
        anchor.update(getattr(d, "anchor_counts", None) or {})
    return {**row, **{f"anchor_{k}": v for k, v in anchor.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="fit")
    ap.add_argument("--n", type=int, default=16, help="NFL slates when the split is 'all'")
    ap.add_argument("--fixture", action="store_true", help="the NBA fixture night of tests/fixtures/your_team_crunch only")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="applied to both runs")
    a = ap.parse_args()
    tot = {"off": Counter(), "on": Counter()}
    for reps, teams, targets, new_cfg in _nights(a):
        for flag in ("off", "on"):
            row = _run(reps, teams, targets, new_cfg(), [*a.set, f"anchor.enabled={'true' if flag == 'on' else 'false'}"])
            tot[flag].update(row)
            print(json.dumps({"night": reps[0].gid, "anchor": flag, **row}))
    keys = sorted(set(tot["off"]) | set(tot["on"]), key=lambda k: (k not in KEYS, KEYS.index(k) if k in KEYS else 0, k))
    print(f"{'counter':28s} {'off':>8s} {'on':>8s}")
    for k in keys:
        print(f"{k:28s} {tot['off'][k]:>8g} {tot['on'][k]:>8g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
