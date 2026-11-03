"""Cross-league scorecard: real archived NIGHTS with every sport on at once (NFL + NBA + NHL + MLB
+ college), run through the real scorer and decider. This is where the hype score matters most:
which of 6-15 simultaneous games deserves the TV.

    .venv/bin/python scripts/eval_hype.py                 # every archived night with 3+ live games
    .venv/bin/python scripts/eval_hype.py --legacy        # same, with the old hand-tuned score
    .venv/bin/python scripts/eval_hype.py --nights 12 --slates
    (stress knobs as in scripts/eval.py: --true-delay 41 --sigma 5 --lag-jitter --drop --late-plays)

Graded with TRUE wallclocks (never the app's own estimates):
  key_live     key moments (redzone/lab/hypetrain.key_moments: every NHL goal, NFL score/turnover/
               4th-down try, clutch basket, late MLB run/HR, 2nd-half lead change/tie, OT score,
               15%+ win-probability swing; garbage time excluded) watched live, start to finish
  key_seen     ...live or by a catch-up replay
  crunch_live  crunch plays (sportsgrade.is_crunch, pro leagues) watched live
  my_key_live  key moments in your teams' games watched live
  mid_play_cuts, dead_air_pct, switches_per_hour as everywhere else
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.lab import sportsgrade  # noqa: E402
from redzone.lab.backtest import Replay, run, watched_whole  # noqa: E402
from redzone.lab.hypetrain import _day, key_moments  # noqa: E402
from redzone.models import League  # noqa: E402

LEAGUES = ("nfl", "nba", "nhl", "mlb", "cbb", "wcbb")
PLAY_LEN = {League.NFL: 7.0, League.MLB: 6.0}


def nights(min_games: int = 3, min_leagues: int = 2):
    by = defaultdict(list)
    for lg in LEAGUES:
        for f in sorted((ROOT / "data/archive" / lg).glob("summary_*.json")):
            try:
                r = Replay.load(League(lg), f)
            except Exception:
                continue
            if r and r.wall[0] is not None:
                by[_day(r.wall[0])].append(r)
    for day in sorted(by):
        reps = by[day]
        if len(reps) >= min_games and len({r.league.base for r in reps}) >= min_leagues:
            yield day, reps


def grade(reps, rep, mine: set[str], tune_s: float = 5.0) -> dict:
    watching = [(s.t, f"replay:{s.to}" if s.kind == "replay" else s.to) for s in rep.switches]
    lag = rep.lag_true
    out = defaultdict(int)
    by_id = {r.gid: r for r in reps}
    prev_kind = None
    for s in rep.switches:
        was_replay, prev_kind = prev_kind == "replay", s.kind
        r = by_id.get(s.frm or "")
        if r is None or was_replay:
            continue
        ok, _ = sportsgrade._clean_cut(r.league.base, r.wall, r.plays, s.t - lag)
        out["cuts"] += not ok
    replayed = rep.replayed or set()
    for r in reps:
        lg = r.league.base.value
        is_mine = bool(set(r.matchup.split(" @ ")) & mine)
        plen = PLAY_LEN.get(r.league.base, 0.0)
        for w, kind, _swing in key_moments(r):
            on = watched_whole(watching, r.gid, w + lag - max(plen, 4.0), w + lag, tune_s, rep.t1)
            out["key"] += 1
            out[f"key_{lg}"] += 1
            out["key_live"] += on
            out[f"key_live_{lg}"] += on
            out["key_seen"] += on or any((r.gid, round(w) + d) in replayed for d in range(-3, 4))
            if is_mine:
                out["my_key"] += 1
                out["my_key_live"] += on
        if r.league.college:
            continue
        for w, p in zip(r.wall, r.plays):
            if sportsgrade.is_crunch(r.league, p) and not sportsgrade._marker(sportsgrade._type(p)):
                on = watched_whole(watching, r.gid, w + lag - max(plen, 2.0), w + lag, tune_s, rep.t1)
                out["crunch"] += 1
                out["crunch_live"] += on
    out["dead_s"] = rep.dead_air_s
    out["watched_s"] = rep.watched_s
    out["hours"] = rep.hours
    out["switches"] = sum(1 for s in rep.switches if s.kind != "replay")
    out["replays"] = sum(1 for s in rep.switches if s.kind == "replay")
    return out


def summary(t: dict) -> dict:
    pct = lambda a, b: round(100 * t.get(a, 0) / max(1, t.get(b, 0)), 1)
    s = {"nights": t.get("nights", 0), "mid_play_cuts": t.get("cuts", 0),
         "key_live_pct": pct("key_live", "key"), "key_seen_pct": pct("key_seen", "key"),
         "crunch_live_pct": pct("crunch_live", "crunch"), "my_key_live_pct": pct("my_key_live", "my_key"),
         "dead_air_pct": pct("dead_s", "watched_s"),
         "switches_per_hour": round(t.get("switches", 0) / max(0.1, t.get("hours", 0)), 1),
         "replays": t.get("replays", 0), "key_moments": t.get("key", 0), "crunch_plays": t.get("crunch", 0)}
    for lg in ("nfl", "nba", "nhl", "mlb"):
        if t.get(f"key_{lg}"):
            s[f"key_live_{lg}_pct"] = pct(f"key_live_{lg}", f"key_{lg}")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nights", type=int, default=0, help="only the last N qualifying nights (0 = all)")
    ap.add_argument("--legacy", action="store_true", help="score with the old hand-tuned model")
    ap.add_argument("--slates", action="store_true")
    ap.add_argument("--true-delay", type=float, default=None)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--lag-jitter", action="store_true")
    ap.add_argument("--drop", type=float, nargs="?", const=0.1, default=0.0)
    ap.add_argument("--late-plays", type=float, nargs="?", const=0.15, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    cfg = Config(user_path=ROOT / "data/config.yaml")
    if a.legacy:
        cfg.data["score"]["v2"]["enabled"] = False  # in memory only; never saved
    teams = cfg.get("teams") or {}
    mine = {t for v in teams.values() for t in (v or [])} or {"WSH"}
    ns = list(nights())
    if a.nights:
        ns = ns[-a.nights:]
    tot = defaultdict(float)
    t0 = time.time()
    for day, reps in ns:
        rep = run(reps, cfg, mine, true_delay=a.true_delay, sigma=a.sigma,
                  lag_jitter=(3.0, 25.0) if a.lag_jitter else None, drop=a.drop, late_plays=a.late_plays,
                  seed=a.seed)
        g = grade(reps, rep, mine)
        g["nights"] = 1
        for k, v in g.items():
            tot[k] += v
        if a.slates:
            lgs = ",".join(sorted({r.league.value for r in reps}))
            print(f"  {day} {len(reps):2d} games ({lgs})", json.dumps(summary(g)), flush=True)
    out = summary(tot)
    out["seconds"] = round(time.time() - t0)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
