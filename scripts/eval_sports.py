"""Scorecard for NBA / NHL (and NFL/MLB) using real archived nights.

    .venv/bin/python scripts/eval_sports.py nba          # every archived NBA night
    .venv/bin/python scripts/eval_sports.py nhl --nights 6 --slates
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
from redzone.lab import sportsgrade  # noqa: E402
from redzone.lab.backtest import Replay, run  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402


def nights(league: League, n: int):
    arch = ROOT / "data/archive" / league.value
    for sb in sorted(arch.glob("scoreboard_*.json"))[-n:]:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--nights", type=int, default=14)
    ap.add_argument("--slates", action="store_true")
    ap.add_argument("--step", type=float, default=None)
    a = ap.parse_args()
    lg = League(a.league)
    cfg = Config(user_path=ROOT / "data/config.yaml")
    mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
    total = sportsgrade.Card(lg.value)
    t0 = time.time()
    for day, reps, targets in nights(lg, a.nights):
        rep = run(reps, cfg, mine, targets=targets, step=a.step)
        card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_total, 5.0, rep.t0, rep.t1, rep.replayed)
        total.add(card)
        if a.slates:
            print(" ", day, json.dumps(card.summary()))
            for t, what in card.cuts[:3]:
                print("     CUT", round(t), what)
    out = total.summary()
    out["seconds"] = round(time.time() - t0)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
