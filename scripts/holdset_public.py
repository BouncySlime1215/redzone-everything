"""The bar the brain must beat: the hold-set measurement with the REAL standby.Planner, and on public seasons.

    nice -n 15 python scripts/holdset_public.py archive --archive data/archive --out planner.json [--leagues nfl,nba,nhl]
    nice -n 15 python scripts/holdset_public.py public --public-dir <dir of play_by_play_<y>.parquet> --league nfl --out pub.json

`archive`: scripts/holdset.py's dev-night measurement plus one more method, the code the app runs today. At each 180 s
decision the night's games are replayed to that wall time, scored by the live scorer (DriveScorer, the pinned eval
config), and ranked by `standby.options`; the top K options are the games it would hold. Table, closest score, random,
Planner and hindsight sit side by side per league at K=2, 4 and 8, with the number of nights.

`public`: the same measurement on free public play-by-play (nflverse `play_by_play_<y>.parquet`, hoopR NBA), read through
the data factory's own night rule and eval-night drop (nothing on or after `EVAL_FIRST_NIGHT` is read), scored by
forward-chaining folds (oldest nights fit, the next block tests; mean and worst fold). Methods: table, closest score,
random, hindsight. The Planner is not run on public rows: it needs a live GameState (scorer history, drive heat), and the
public rows have none. Run it on Kaggle or a laptop with the parquet files; nothing here trains a model.

What the Planner baseline stands in for (said once, in the output): the app's decider is not simulated, so
  - current=None: nothing is "on screen", every live game can be held (the other methods choose among all live games too);
  - no replay, break-visit, hand-back or NEXT inputs, no hidden-stick tune time, no Planner hysteresis (hold_s, min_gain
    only matter for one pick over time; a K-set is the ranking, `options()` order);
  - the feed is read at wall time t with no lag; the scorer sees a 30 s poll (--poll-s), not the app's 10 s;
  - a game `options()` leaves out (stale, or on a break and not hot) gets no rank: the hidden stick would not hold it;
  - my teams are WSH and UNC, as in the product spec; `score.personal` boosts other than that are off.

Why: the hold-set model comparison (the-mind, step 1) was against closest-score only. A model that cannot beat what the
app does today is not worth shipping, so the baseline is the app (build plan WP-24). Dev nights only: locked and fresh
nights are refused by scripts/coverage_audit.py DevOnly (archive) and by the factory's eval cut (public).
"""
import argparse
import bisect
import collections
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from scripts import holdset  # noqa: E402
from scripts.holdset import KS, LockedNightError, capture, decision_times, decisions, fit, game_row  # noqa: E402

MY_TEAMS = ("WSH", "UNC")
STANDS_IN = ("current=None (nothing on screen: every live game may be held); no replay, break-visit, hand-back or NEXT "
             "inputs; no hidden-stick tune time; no Planner hysteresis (a K-set is the options() ranking); feed read at "
             "wall time t with no lag, scorer polled every poll_s; games options() drops rank last; my teams WSH, UNC")


# --- archive: the real standby.Planner ------------------------------------------------------------

def load_replay(league, gid, data):
    """backtest.Replay.load for a summary already in memory (same code path, no second file read)."""
    from redzone.lab.backtest import Replay
    from redzone.sources import espn
    plays = espn.flatten_plays(league, data)
    if not plays:
        return None
    wall, last = [], None
    for p in plays:
        t = espn._ts(p.get("wallclock"))
        t = t if t is not None else (last + 5 if last else None)
        wall.append(t)
        last = t if t is not None else last
    if wall[0] is None:
        return None
    for i in range(1, len(wall)):
        wall[i] = max(wall[i], wall[i - 1])
    r = Replay(league, gid, data, plays, wall)
    g = espn.parse_summary(league, data, upto=0, plays=plays)
    r.matchup = g.matchup if g else gid
    return r


def make_config(path: Path | None, league: str):
    """The pinned eval config (as scripts/eval.py reads it) with the shipped switch costs and this league's overrides."""
    from redzone.config import Config
    from redzone.lab import evaldata
    cfg = Config(user_path=path) if path else Config(user_path=Path("/nonexistent/holdset-defaults.yaml"))
    evaldata.use_shipped_switch_costs(cfg)
    evaldata.flatten_league_overrides(cfg, league)
    return cfg


class PlannerRanker:
    """ranker(l, night, night_rows) -> {decision time: {gid: score}}, score = minus the place in standby.options()."""

    def __init__(self, config_path: Path | None = None, poll_s: float = 30.0, my_teams=MY_TEAMS):
        self.config_path, self.poll_s, self.my_teams = config_path, poll_s, tuple(my_teams)
        self.polls = 0

    def __call__(self, l, night, night_rows):
        from redzone import standby
        from redzone.decider.decider import Candidate
        from redzone.lab.backtest import _Clock
        from redzone.models import League, Phase
        from redzone.scoring.drive import DriveScorer
        from redzone.scoring.engine import Personal

        times = decision_times(night_rows)
        if not times:
            return {}
        want = {g[0] for g in night_rows}
        lg = League(l)
        replays = []
        for _, gid, d in holdset.games(l, only={night}):
            if gid in want:
                r = load_replay(lg, gid, d)
                if r is not None:
                    replays.append(r)
        cfg = make_config(self.config_path, l)
        clock = _Clock()
        scorer = DriveScorer(cfg, clock=clock)
        me = Personal(my_teams={x: set(self.my_teams) for x in League})
        margin = float(cfg.get("decider.switch_margin", 10))
        t0 = min(r.wall[0] for r in replays) if replays else times[0]
        ticks = sorted(set(times) | {t0 + self.poll_s * i for i in range(int((times[-1] - t0) // self.poll_s) + 1)})
        due, out = set(times), {}
        for t in ticks:
            clock.t = t
            cands, states = {}, {}
            for r in replays:
                i = bisect.bisect_right(r.wall, t) - 1
                if i < 0:
                    continue
                g = r.state_at(i)
                if g is None:
                    continue
                g.updated_at = t
                states[r.gid] = g
                if g.phase != Phase.LIVE:
                    continue
                cands[r.gid] = Candidate(scorer.score(g, me), f"youtubetv:{r.gid}")
            self.polls += 1
            if t in due:
                opts = standby.options(cands, current=None, games=states, my_teams={x: set(self.my_teams) for x in League},
                                       return_bonus=margin)
                seen = {}
                for place, o in enumerate(opts):
                    seen.setdefault(o.game_id, -float(place))
                out[t] = seen
        return out


# --- public seasons: nflverse and hoopR rows -> the summary shape holdset.py reads ---------------------

NFL_EXTRA = ["drive", "down", "ydstogo", "yardline_100", "yards_gained"]


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def nfl_summary(g):
    """One nflfastR game (DataFrame, plays in order) -> {'drives': {'previous': [...]}} as football() reads it.
    Play types come from the factory's own nflfastR->ESPN mapping. The published win-probability columns are left out
    on purpose (they were fit across seasons: leakage), so the wp5 tier-3 event is absent on public rows."""
    from redzone.noggin import factory
    plays, by_drive = [], collections.OrderedDict()
    for r in g.itertuples(index=False):
        if r.play_type in ("extra_point",) or r.two_point_attempt == 1:
            continue
        tt = factory._nfl_type(r)
        wall = r.time_of_day if isinstance(r.time_of_day, str) else None
        if not tt or wall is None:
            continue
        q = int(r.qtr) if r.qtr == r.qtr else None
        yl = _num(r.yardline_100)
        dn = _num(r.down)
        p = {"id": f"{r.game_id}:{int(r.play_id)}", "type": {"text": tt}, "text": r.desc if isinstance(r.desc, str) else "",
             "period": {"number": q}, "clock": {"displayValue": r.time if isinstance(r.time, str) else ""},
             "wallclock": wall, "scoringPlay": bool(r.sp == 1), "homeScore": _num(r.total_home_score) or 0,
             "awayScore": _num(r.total_away_score) or 0, "statYardage": _num(r.yards_gained) or 0,
             "start": {"down": int(dn) if dn else 0, "yardsToEndzone": yl, "distance": _num(r.ydstogo) or 0},
             "end": {"yardsToEndzone": None if yl is None else max(0.0, yl - (_num(r.yards_gained) or 0))}}
        by_drive.setdefault(_num(r.drive), []).append(p)
    for ps in by_drive.values():
        plays.append({"plays": ps})
    return {"drives": {"previous": plays}}


def hoops_summary(g):
    """One hoopR game (DataFrame in sequence order) -> {'plays': [...]} as hoops() reads it."""
    from redzone.noggin import factory
    out = factory._plays_basketball(g)
    for p, r in zip(out, g.itertuples(index=False)):
        p["shootingPlay"] = bool(getattr(r, "shooting_play", False)) if getattr(r, "shooting_play", False) == getattr(r, "shooting_play", False) else False
    return {"plays": out}


BASKETBALL_EXTRA = ["shooting_play"]


def public_rows(l, df):
    """{night: [game_row]} from one league's public play-by-play (all the seasons concatenated), minus the factory's eval nights.
    The night is the factory's (ET, 5 AM boundary) so public and factory splits agree."""
    import pandas as pd
    from redzone.noggin import factory
    rows = collections.defaultdict(list)
    cut = factory.EVAL_FIRST_NIGHT.get(l)
    if l == "nfl":
        df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
        make = nfl_summary
    else:
        df = df[df.type_text.fillna("").ne("Substitution") & df.period_number.notna() & df.wallclock.notna()].copy()
        df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
        df = df.sort_values(["game_id", "sq"], kind="stable")
        make = hoops_summary
    for gid, g in df.groupby("game_id", sort=False):
        d = make(g)
        r = game_row(l, str(gid), d)
        if not r:
            continue
        n = factory.night_of(r[1])
        if cut and n >= cut:
            continue
        rows[n].append(r)
    return rows


def forward_folds(nights, folds=4):
    """Expanding window over whole nights, oldest first: fold i fits on blocks 0..i, tests on block i+1 (folds+1 blocks)."""
    nights = sorted(nights)
    edges = [round(len(nights) * k / (folds + 1)) for k in range(folds + 2)]
    return [(nights[:edges[i + 1]], nights[edges[i + 1]:edges[i + 2]]) for i in range(folds)]


def public_report(l, rows, folds=4):
    """Per fold and per K: capture of table, closest score, random, hindsight; then the mean and the worst fold."""
    res = {"league": l, "nights": len(rows), "games": sum(len(v) for v in rows.values()), "folds": []}
    per = {K: [] for K in KS}
    for tr, te in forward_folds(rows, folds):
        Dtr = [dd for n in tr for dd in decisions(l, rows[n])]
        Dte = [dd for n in te for dd in decisions(l, rows[n])]
        if not Dtr or not Dte:
            continue
        pred, _ = fit(Dtr)
        fo = {"nights_fit": len(tr), "nights_test": len(te), "decisions": len(Dte)}
        for K in KS:
            g_h, t_h, _, n = capture(Dte, lambda x: -x[2], K, random.Random(0))
            if not t_h:
                continue
            g_t, t_t, b_t, _ = capture(Dte, lambda x: pred(x[1]), K, random.Random(0))
            rs = [capture(Dte, lambda x: 0.0, K, random.Random(s)) for s in range(30)]
            c = {"decisions": n, "state_table_pct": round(g_t / t_t * 100, 1), "closest_score_pct": round(g_h / t_h * 100, 1),
                 "random_pct": round(float(np.mean([a / b for a, b, _, _ in rs])) * 100, 1),
                 "planner_pct": None, "hindsight_pct": round(b_t / t_t * 100, 1)}
            fo[f"K{K}"] = c
            per[K].append(c)
        res["folds"].append(fo)
    for K in KS:
        cs = per[K]
        if cs:
            res[f"K{K}"] = {"folds": len(cs), "decisions": sum(c["decisions"] for c in cs),
                            "nights": res["nights"],
                            **{k: round(float(np.mean([c[k] for c in cs])), 1)
                               for k in ("state_table_pct", "closest_score_pct", "random_pct", "hindsight_pct")},
                            "planner_pct": None,
                            "worst_fold_table_pct": min(c["state_table_pct"] for c in cs),
                            "worst_fold_table_minus_hand": round(min(c["state_table_pct"] - c["closest_score_pct"] for c in cs), 1)}
    res["nights_test"], res["nights_train"] = (sum(f["nights_test"] for f in res["folds"]), res["nights"])
    return res


# --- command line ------------------------------------------------------------------------------------

def run_archive(args):
    holdset.A = args.archive
    cfgp = args.config or (args.archive.parent / "eval_config.yaml")
    cfgp = cfgp if cfgp.exists() else None
    R = {}
    for l in [x for x in args.leagues.split(",") if x in holdset.FN]:
        try:
            rows = holdset.build(l)
        except LockedNightError as e:
            print(l, "REFUSED", e)
            continue
        res = holdset.report(l, rows, PlannerRanker(cfgp, args.poll_s))
        res["planner_stands_in_for"] = STANDS_IN
        R[l] = res
        print(l, json.dumps(res)); sys.stdout.flush()
    args.out.write_text(json.dumps(R, indent=1))
    print(holdset.table(R))
    return R


def run_public(args):
    import pandas as pd
    from redzone.noggin import factory
    l = args.league
    files = sorted(args.public_dir.glob("play_by_play_*.parquet"))
    if not files:
        sys.exit(f"{args.public_dir}: no play_by_play_<year>.parquet files")
    frames = []
    for f in files:
        y = int(f.stem.split("_")[-1])
        if y < factory.SOURCES[l]["first"]:
            continue
        import pyarrow.parquet as pq
        have = set(pq.read_schema(f).names)
        want = (factory.NFL_COLS + NFL_EXTRA) if l == "nfl" else (
            ["id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock", "scoring_play",
             "score_value", "home_score", "away_score", "game_id", "season", "season_type"] + BASKETBALL_EXTRA)
        if ("time_of_day" if l == "nfl" else "wallclock") not in have:
            continue
        frames.append(pd.read_parquet(f, columns=[c for c in want if c in have]))
    rows = public_rows(l, pd.concat(frames, ignore_index=True))
    res = public_report(l, rows, args.folds)
    res["planner_stands_in_for"] = "not run on public seasons: it needs a live GameState (scorer history, drive heat)"
    args.out.write_text(json.dumps({l: res}, indent=1))
    print(json.dumps(res, indent=1))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("archive", help="dev nights of data/archive, with the real standby.Planner")
    a.add_argument("--archive", required=True, type=Path)
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--leagues", default="nfl,nba,nhl")
    a.add_argument("--config", type=Path, default=None, help="default: <archive>/../eval_config.yaml when it exists")
    a.add_argument("--poll-s", type=float, default=30.0)
    p = sub.add_parser("public", help="free public seasons, forward-chaining folds")
    p.add_argument("--public-dir", required=True, type=Path)
    p.add_argument("--league", choices=("nfl", "nba"), required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--folds", type=int, default=4)
    args = ap.parse_args(argv)
    return run_archive(args) if args.mode == "archive" else run_public(args)


if __name__ == "__main__":
    main()
