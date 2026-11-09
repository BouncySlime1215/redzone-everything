#!/usr/bin/env python3
"""Grade an exported GameValue model on scripts/holdset.py's own test nights, against the real Planner on those nights.

    nice -n 15 python scripts/kaggle_gamevalue_holdset.py --archive data/archive --models <dir> --out report.json [--leagues nfl]

Why: a model may only be promoted by beating what the app does today on the same decisions, so this plugs the model and
then the real standby.Planner into holdset's ranker slot, on the same rows, rather than comparing against a stored number
(build plan WP-26). Numpy prediction only, no training, dev nights only (holdset reads through DevOnly). The feed is read
at the decision with no lag, the Planner column's rule. Published win probability is removed from the rows first, so the
wp5 / wp10 events are out of y for every method, as they are out of the training labels.

holdset calls whatever the ranker returns its "planner" column; the model's run and the Planner's run are kept apart.

--fair-capture adds three columns per K on the same test nights and decisions (docs/agents/training/2026-11-09-frontier-prediction.md):
    FC        the test above (no lag, every held game counts)
    FC-lag    the ranker reads the feed as it arrives: each play lands after a lag drawn from redzone/lab/feed_lag_profile.json
    FC-reach  FC-lag, but a held game earns only the value after its first entry the real timing gate (Decider.entry_plan) allows
Why: the no-lag test cannot judge a timing experiment, and it pays for games no stick could have reached. Without the flag nothing changes.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from redzone.moments.detect import detect  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from scripts import holdset  # noqa: E402
from scripts.holdset_public import PlannerRanker, load_replay, make_config  # noqa: E402

# The picture the timing gate plans for: the scorecards' defaults (scripts/eval.py: 8 s feed + 35 s delay, sigma 3, a 5 s tune).
GATE_SCREEN_DELAY_S, GATE_SIGMA_S, GATE_TUNE_S = 43.0, 3.0, 5.0


class LagTableError(ValueError):
    """The league's feed-lag table is missing or was not measured: no FC-lag number may be made up from it."""


class FeedLag:
    """When each play of a game reaches the app: its wall time plus a lag drawn from the league's measured table, delivered
    in order, the sims' feed model (backtest._feed, the same LagSampler, so draws stop at the p95). The draw is seeded by the
    seed, game and play, so the same play gets the same lag in the model's run, the Planner's run and the entry gate's."""

    def __init__(self, league: str, cfg, seed: int = 0, profile: dict | None = None):
        from redzone.lab.timing import PROFILE_FALLBACK, LagSampler, load_profile, sources_in_use
        prof = load_profile() if profile is None else profile
        self.league, self.seed = league, seed
        self.sampler = LagSampler(prof, use=sources_in_use(cfg))
        source = self.sampler.use.get(league)
        leagues = prof.get("leagues") or {}
        row = prof["sources"][source][league] if source else leagues.get(league) or leagues.get(PROFILE_FALLBACK.get(league, ""))
        if not row or not row.get("n"):       # n=0 is a derived table (sources.nhl_wsc), not a measurement
            raise LagTableError(f"{league}: no measured feed-lag table ({source or prof.get('source')})")
        self.info = {"source": source or prof.get("source"), "n": row["n"], "basis": row.get("basis"), "p50_s": row["q"][10],
                     "p95_s": row["q"][19], "days": row.get("days") or prof.get("days")}

    def arrivals(self, gid: str, walls) -> np.ndarray:
        lags = [self.sampler.draw(self.league, random.Random(f"{self.seed}|{self.league}|{gid}|{float(w):.3f}")) for w in walls]
        return np.maximum.accumulate(np.asarray(walls, float) + np.asarray(lags, float))


class Recording:
    """A ranker that keeps what it returned per night, so the fair columns reuse the very ranks of the FC column."""

    def __init__(self, ranker):
        self.ranker, self.ranks = ranker, {}

    def __call__(self, league, night, night_rows):
        self.ranks[night] = self.ranker(league, night, night_rows)
        return self.ranks[night]


class ModelRanker:
    """ranker(l, night, night_rows) -> {decision time: {game id: value_next_3m}}. A game that fails closed gets no rank."""

    def __init__(self, model: G.GameValueModel, games=None, feed: FeedLag | None = None):
        self.model, self.games = model, games     # games(league, night) -> [(night, gid, summary)]; default: the archive
        self.feed = feed                          # None: the feed is read at the decision with no lag

    def __call__(self, league, night, night_rows):
        times = np.array(holdset.decision_times(night_rows), float)
        out = {float(t): {} for t in times}
        want = {g[0] for g in night_rows}
        for _, gid, summary in (self.games(league, night) if self.games else holdset.games(league, only={night})):
            if gid not in want:
                continue
            clean = V.strip_published_wp(summary)
            playoff = int(((summary.get("header") or {}).get("season") or {}).get("type") == 3)
            view = V.make_view(league, clean, detect(league, clean, gid), playoff)
            arrive = view.walls if self.feed is None else self.feed.arrivals(gid, view.walls)
            head = np.searchsorted(arrive, times, "right") - 1
            seen = head >= 0
            if not seen.any():
                continue
            feats = V.features(view, head[seen], times[seen])
            b = self.model.predict_matrix(np.column_stack([feats[f] for f in G.GV_FEATURES]), max_rows=None)
            for t, value, ok in zip(times[seen], b.value, b.ok):
                if ok:
                    out[float(t)][gid] = float(value)
        return out


class LaggedPlannerRanker(PlannerRanker):
    """PlannerRanker.__call__ with each game's feed read through a FeedLag (no feed: the same ranks as PlannerRanker, a test holds it to that)."""

    def __init__(self, feed: FeedLag | None, config_path: Path | None = None, poll_s: float = 30.0):
        super().__init__(config_path, poll_s)
        self.feed = feed

    def __call__(self, l, night, night_rows):
        from redzone import standby
        from redzone.decider.decider import Candidate
        from redzone.lab.backtest import _Clock
        from redzone.models import League, Phase
        from redzone.scoring.drive import DriveScorer
        from redzone.scoring.engine import Personal

        times = holdset.decision_times(night_rows)
        if not times:
            return {}
        want, lg = {g[0] for g in night_rows}, League(l)
        replays = [r for r in (load_replay(lg, gid, d) for _, gid, d in holdset.games(l, only={night}) if gid in want) if r is not None]
        arrive = {r.gid: r.wall if self.feed is None else self.feed.arrivals(r.gid, r.wall).tolist() for r in replays}
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
                i = bisect.bisect_right(arrive[r.gid], t) - 1
                g = r.state_at(i) if i >= 0 else None
                if g is None:
                    continue
                g.updated_at = t
                states[r.gid] = g
                if g.phase == Phase.LIVE:
                    cands[r.gid] = Candidate(scorer.score(g, me), f"youtubetv:{r.gid}")
            if t in due:
                opts = standby.options(cands, current=None, games=states, my_teams={x: set(self.my_teams) for x in League},
                                       return_bonus=margin)
                seen = {}
                for place, o in enumerate(opts):
                    seen.setdefault(o.game_id, -float(place))
                out[t] = seen
        return out


def entry_series(league: str, night: str, night_rows, feed: FeedLag, cfg) -> dict:
    """{gid: (ticks, landings)}: each poll at which the real timing gate lets a fresh tune into the game, and the game moment the
    picture lands on (the screen delay and any rewind put it before the poll). The gate sees only the plays that have arrived;
    a poll where it has no evidence or says wait is not an entry (Rule #1). The scorecards' picture model, the shipped entry config."""
    from redzone.decider.decider import Candidate, Decider
    from redzone.decider.screen import DelayBook
    from redzone.lab.backtest import _Clock
    from redzone.lab.evaldata import config_behind_live
    from redzone.models import League, Phase
    from redzone.scoring.drive import DriveScorer
    from redzone.scoring.engine import Personal

    want, lg = {g[0] for g in night_rows}, League(league)
    replays = [r for r in (load_replay(lg, gid, d) for _, gid, d in holdset.games(league, only={night}) if gid in want) if r is not None]
    if not replays:
        return {}
    arrive = {r.gid: feed.arrivals(r.gid, r.wall).tolist() for r in replays}
    clock = _Clock()
    behind = config_behind_live(cfg, league) or 0.0
    delays = DelayBook({"youtubetv": GATE_SCREEN_DELAY_S + behind}, sigma_default=GATE_SIGMA_S, clock=clock, model=cfg.get("delay.model", "channel"))
    dec = Decider(cfg, delays, clock=clock)
    dec.entry_cost = lambda target: GATE_TUNE_S
    scorer = DriveScorer(cfg, clock=clock)
    me = Personal(my_teams={x: {"WSH", "UNC"} for x in League})
    t0, t1 = min(r.wall[0] for r in replays), max(g[2] for g in night_rows)
    clock.t = t0
    for r in replays:
        d = delays.get(f"youtubetv:{r.gid}")
        d.calibrated_at, d.sigma = t0, GATE_SIGMA_S
    poll = float(cfg.get("polling.live_s", 10))
    series = {r.gid: ([], []) for r in replays}
    last, cands = {}, {}
    for step in range(int((t1 - t0) // poll) + 1):
        t = clock.t = t0 + step * poll
        for r in replays:
            i = bisect.bisect_right(arrive[r.gid], t) - 1
            g = r.state_at(i) if i >= 0 else None
            if g is None:
                continue
            g.updated_at = t
            dec.timeline(r.gid, lg).observe(g)
            if g.phase != Phase.LIVE:
                cands.pop(r.gid, None)
            elif last.get(r.gid) != i:     # the score only needs redoing when a play arrived
                cands[r.gid] = Candidate(scorer.score(g, me), f"youtubetv:{r.gid}")
            last[r.gid] = i
        for gid in cands:
            plan = dec.entry_plan(gid, cands, t)
            if plan is not None and plan.ok:
                d = delays.get(f"youtubetv:{gid}")
                series[gid][0].append(t)
                series[gid][1].append(t + GATE_TUNE_S - (d.mu - d.behind) - plan.rewind_s)
    return series


def first_landing(series, t: float, end: float) -> float | None:
    """The game moment of the first entry the gate allows in [t, end], None when it allows none."""
    if not series:
        return None
    i = bisect.bisect_left(series[0], t)
    return series[1][i] if i < len(series[0]) and series[0][i] <= end else None


def decision_clock(night_rows, decs) -> list[float]:
    """The wall time of each decision holdset.decisions returned (it drops the time), checked game by game against those decisions."""
    clock, live = [], []
    for t in holdset.decision_times(night_rows):
        here = [g[0] for g in night_rows if g[1] + 120 <= t <= g[2] - holdset.H and np.searchsorted([x[0] for x in g[3]], t) - 1 >= 0]
        if len(here) >= 2:
            clock.append(t)
            live.append(here)
    if live != [[x[0] for x in d] for d in decs]:
        raise RuntimeError("decision clock does not match holdset.decisions")
    return clock


def reach_capture(by_night: dict, night_rows_of: dict, entries: dict, K: int, rnd: random.Random) -> tuple[float, float, int]:
    """holdset.capture's top-K picks and denominator, but a held game earns only after its first allowed entry. A game still
    held from the decision before (and already entered) earns the whole window; one that drops out must be entered again."""
    got = tot = 0.0
    n = 0
    for night in sorted(by_night):
        night_rows = night_rows_of[night]
        events = {g[0]: g[4] for g in night_rows}
        entered, prev = set(), None
        for t, dec in zip(decision_clock(night_rows, by_night[night]), by_night[night]):
            if prev is not None and t - prev > holdset.H + 1e-6:
                entered = set()
            prev = t
            scored = len(dec) > K
            picks = list(dec)
            if scored:
                rnd.shuffle(picks)
                picks.sort(key=lambda x: x[5], reverse=True)
                picks = picks[:K]
            now_in = set()
            for x in picks:
                gid = x[0]
                land = t if gid in entered else first_landing(entries.get(night, {}).get(gid), t, t + holdset.H)
                if land is None:
                    continue
                now_in.add(gid)
                if scored:
                    earn_from = max(t, land)
                    got += sum(holdset.WEIGHTS[tier] for w, tier in events[gid] if earn_from < w <= t + holdset.H)
            entered = now_in
            if scored:
                tot += sum(x[3] for x in dec)
                n += 1
    return got, tot, n


def fair_capture(league: str, rows: dict, base_ranks: dict, lag_ranks: dict, entries: dict) -> dict:
    """{K2: {fc_pct, fc_lag_pct, fc_reach_pct, decisions, nights}, ...} on the nights base_ranks covers, the report's own test nights.
    base_ranks and lag_ranks: {night: {decision time: {gid: score}}}. Every column divides by the same decisions' available value."""
    nights = sorted(base_ranks)
    base = {n: holdset.decisions(league, rows[n], base_ranks[n]) for n in nights}
    lagged = {n: holdset.decisions(league, rows[n], lag_ranks[n]) for n in nights}
    all_base = [d for n in nights for d in base[n]]
    all_lagged = [d for n in nights for d in lagged[n]]
    pct = lambda g, t: round(g / t * 100, 1) if t else None
    out = {}
    for K in holdset.KS:
        g_fc, t_fc, _, n_fc = holdset.capture(all_base, lambda x: x[5], K, random.Random(0))
        g_lag, t_lag, _, _ = holdset.capture(all_lagged, lambda x: x[5], K, random.Random(0))
        g_reach, t_reach, _ = reach_capture(lagged, {n: rows[n] for n in nights}, entries, K, random.Random(0))
        out[f"K{K}"] = {"decisions": n_fc, "nights": sum(any(len(d) > K for d in base[n]) for n in nights),
                        "fc_pct": pct(g_fc, t_fc), "fc_lag_pct": pct(g_lag, t_lag), "fc_reach_pct": pct(g_reach, t_reach)}
    return out


def build_rows(league: str) -> dict:
    """holdset.build with published win probability removed, so y counts what the training labels count."""
    rows = collections.defaultdict(list)
    for night, gid, summary in holdset.games(league):
        row = holdset.game_row(league, gid, V.strip_published_wp(summary))
        if row:
            rows[night].append(row)
    return rows


def verdict(model_results: dict, planner_results: dict) -> dict:
    """holdset's report per league, model and real Planner, -> the promotion bar. Shadow nights are not run here, so this
    never says promote. A league whose two runs did not score the same decisions gets no bar."""
    out = {}
    for league, res in model_results.items():
        base = planner_results.get(league) or {}
        same = all(res.get(k) == base.get(k) for k in ("nights_test", "decisions_test"))
        k4, bar = res["K4"]["planner_pct"], (base.get("K4") or {}).get("planner_pct") if same else None
        out[league] = {"model_k4_pct": k4, "real_planner_k4_pct": bar, "beats_planner": bool(k4 is not None and bar is not None and k4 > bar),
                       "test_nights": res["nights_test"], "same_decisions": same, "promotion": G.promotion(league, k4, [], bar)}
    return out


def fair_report(league, rows, config, poll_s, seed, model, model_ranks, planner_ranks, model_res, planner_res) -> dict:
    """One league's fair columns for the model and the real Planner, or why there are none. The FC column must be the number
    the report just gave, or the columns are not on the same decisions."""
    cfg = make_config(config, league)
    try:
        feed = FeedLag(league, cfg, seed)
    except LagTableError as e:
        return {"skipped": str(e)}
    nights = sorted(model_ranks)
    entries = {n: entry_series(league, n, rows[n], feed, cfg) for n in nights}
    lag_rankers = {"model": ModelRanker(model, feed=feed), "planner": LaggedPlannerRanker(feed, config, poll_s)}
    base = {"model": (model_ranks, model_res), "planner": (planner_ranks, planner_res)}
    out = {"lag_table": feed.info, "lag_seed": seed,
           "gate": {"screen_delay_s": GATE_SCREEN_DELAY_S, "sigma_s": GATE_SIGMA_S, "tune_s": GATE_TUNE_S, "poll_s": float(cfg.get("polling.live_s", 10))}}
    for name, (ranks, res) in base.items():
        lag_ranks = {n: lag_rankers[name](league, n, rows[n]) for n in nights}
        out[name] = fair_capture(league, rows, ranks, lag_ranks, entries)
        for K, cell in out[name].items():
            if cell["fc_pct"] != res[K]["planner_pct"]:
                raise RuntimeError(f"{league} {name} {K}: FC {cell['fc_pct']} is not the report's {res[K]['planner_pct']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--config", type=Path, default=None, help="default: <archive>/../eval_config.yaml when it exists")
    ap.add_argument("--poll-s", type=float, default=30.0)
    ap.add_argument("--fair-capture", action="store_true", help="also report FC, FC-lag and FC-reach per K (see the header)")
    ap.add_argument("--fc-seed", type=int, default=0, help="seed of the feed-lag draws")
    a = ap.parse_args(argv)
    holdset.A = a.archive
    config = a.config or (a.archive.parent / "eval_config.yaml")
    config = config if config.exists() else None
    model_results, planner_results, fair = {}, {}, {}
    for lg in a.leagues.split(","):
        try:
            rows = build_rows(lg)
        except holdset.LockedNightError as e:
            print(lg, "REFUSED", e)
            continue
        model = G.load(G.model_path(lg, a.version, a.models), lg)
        model_rank, planner_rank = ModelRanker(model), PlannerRanker(config, a.poll_s)
        if a.fair_capture:
            model_rank, planner_rank = Recording(model_rank), Recording(planner_rank)
        model_results[lg] = holdset.report(lg, rows, model_rank)
        planner_results[lg] = holdset.report(lg, rows, planner_rank)
        if a.fair_capture:
            fair[lg] = fair_report(lg, rows, config, a.poll_s, a.fc_seed, model, model_rank.ranks, planner_rank.ranks,
                                   model_results[lg], planner_results[lg])
    out = {"model": model_results, "planner": planner_results}
    if a.fair_capture:
        out["fair_capture"] = fair
        print(json.dumps(fair, indent=1))
    a.out.with_suffix(".holdset.json").write_text(json.dumps(out, indent=1))
    report = verdict(model_results, planner_results)
    a.out.write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
