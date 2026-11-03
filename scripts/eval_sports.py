"""Scorecard for NBA / NHL (and NFL/MLB) using real archived nights.

    .venv/bin/python scripts/eval_sports.py nba          # every archived NBA night
    .venv/bin/python scripts/eval_sports.py nhl --nights 6 --slates

Real-world messiness (all off by default; same knobs as scripts/eval.py, see backtest.run):
    --true-delay 41      the stream really runs 41 s behind (the app still believes 35)
    --sigma 5            the uncertainty the app states for its delay estimate
    --lag-jitter         each play reaches the feed 3-25 s after it happened (seeded)
    --drop [RATE]        the source goes silent 60-120 s, RATE times per game-minute (0.1)
    --late-plays [FRAC]  this fraction of plays arrive a poll late / out of order (0.15)
    --no-wallclock [FRAC]  this fraction of plays come without a wallclock (0.5)
    --seed N             which random draw of the above
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.lab import evaldata, sportsgrade  # noqa: E402
from redzone.lab.backtest import Replay, run  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402


# The graded window ends here. The service archives each new night at 5 AM (and refreshes the last
# scoreboards), so an open window slides under any before/after comparison. Move it on purpose:
# the pinned eval dataset (eval-data.lock.json) wins; this is the fallback without one.
EVAL_END = "20261101"


def nights(league: League, n: int, end: str | None = None):
    """The last n archived nights, up to `end` (YYYYMMDD) when given: the service archives every
    new night at 5 AM, and an unpinned window would slide under a before/after comparison."""
    arch = ROOT / "data/archive" / league.value
    sbs = sorted(sb for sb in arch.glob("scoreboard_*.json") if not end or sb.stem.split("_")[1] <= str(end))
    for sb in sbs[-n:]:
        evs = json.loads(sb.read_text()).get("events", [])
        reps, targets = [], {}
        for e in evs:
            f = arch / f"summary_{e['id']}.json"
            r = Replay.load(league, f) if f.exists() else None
            if not r:
                continue
            reps.append(r)
            g = espn._parse_event(league, e)
            w = ChannelMap(ROOT / "data/_eval_channels.json").where(g) if g else None
            app = (w.target or "youtubetv:x").split(":")[0] if w else "youtubetv"
            targets[r.gid] = f"{app}:{r.gid}"
        if len(reps) >= 2:
            yield sb.stem.split("_")[-1], reps, targets


def stress_args(ap: argparse.ArgumentParser) -> None:
    """[xleague] The stress knobs, spelled exactly like scripts/eval.py's."""
    ap.add_argument("--true-delay", type=float, default=None)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--lag-jitter", action="store_true")
    ap.add_argument("--drop", type=float, nargs="?", const=0.1, default=0.0)
    ap.add_argument("--late-plays", type=float, nargs="?", const=0.15, default=0.0)
    ap.add_argument("--no-wallclock", type=float, nargs="?", const=0.5, default=0.0)
    ap.add_argument("--seed", type=int, default=0)


def knobs(a: argparse.Namespace) -> dict:
    return dict(true_delay=a.true_delay, sigma=a.sigma, lag_jitter=(3.0, 25.0) if a.lag_jitter else None,
                drop=a.drop, late_plays=a.late_plays, no_wallclock=a.no_wallclock, seed=a.seed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--nights", type=int, default=14)
    ap.add_argument("--slates", action="store_true")
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--end", default=None, help="last night to grade (YYYYMMDD); default eval.<league>_end")
    stress_args(ap)
    a = ap.parse_args()
    lg = League(a.league)
    # The live service rewrites data/config.yaml (measured switch costs, taps): scorecards read a
    # pinned copy when one exists, so a before/after comparison isn't moved by the live night.
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    if pinned.exists() and (cfg.rejected or not cfg.overrides):
        # Config falls back to defaults when a file doesn't validate (an unknown key, say): a
        # scorecard on silent defaults compares the wrong thing.
        sys.exit(f"{pinned} has settings the app rejects ({cfg.rejected}): refusing to grade on a partial config")
    mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
    total = sportsgrade.Card(lg.value)
    t0 = time.time()
    end = a.end or evaldata.window_end(lg.value, EVAL_END)
    for day, reps, targets in nights(lg, a.nights, end):
        rep = run(reps, cfg, mine, targets=targets, step=a.step, **knobs(a))
        # [xleague] Grade against what the screen REALLY showed (true delay), not the app's belief.
        card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
        total.add(card)
        if a.slates:
            print(" ", day, json.dumps(card.summary()))
            for t, what in card.cuts[:3]:
                print("     CUT", round(t), what)
            for t, kind, what in card.cut.mid_entries[:3]:
                print("     MID-PLAY ENTRY", round(t), kind, what)
    out = total.summary()
    out["seconds"] = round(time.time() - t0)
    out["eval_data"] = evaldata.stamp()
    out["eval_end"] = end
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
