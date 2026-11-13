"""Gym v2 slates: census-weighted sport mixes built from recorded dev-night games (A-01 slice 3).

    mix = draw_mix(seed)                         # e.g. {"cbb": 1, "nba": 2}, weighted by the census
    rs, slate = compose(mix, seed)               # recorded games of those leagues, moved in time to overlap
    GymV2("mix", len(rs), loaded=(rs, slate), mixed=True, ...)

The mix weights are the ten-year schedule census (L100: 588,941 events; redzone-agents models/mix-census-v1,
top_mixes.csv, universe "owner_known": reachable with today's subscriptions and a known league), kept to PARK's leagues
(nba, cbb, nfl, cfb), two games or more, at most MIX_MAX_GAMES. Nights with two or more NFL games (Sunday afternoons) are
left out: RedZone's football scope is Monday and Thursday nights. Only the top 50 mixes of the census table exist, so
rarer mixes are absent (named, not hidden). `CENSUS_MINUTES` is that table's aggregate minutes a mix was on.

A composed slate is NOT a recorded night: each game is a recorded dev (fit) night game, its every stamp moved by whole
seconds so the games tip off as a real night staggers them (the first at 0, the others ESTIMATE: uniform 0-90 minutes
later, the replay's 2.5-hour slate rule). Different games' dead and live stretches do not depend on each other, which
is how park_replay.shifted_nfl builds its "mnf+K" slate. Train on these; judge only on real nights (track A section 4).
Locked nights are never opened (`splits.nights_for(..., "fit")`).

Why: the old teacher saw at most 6 games of few mixes; real nights mix sports, and the director must learn to navigate them.
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

PARK_LEAGUES = ("nba", "cbb", "nfl", "cfb")
MIX_MAX_GAMES = 6                                  # track A section 4: at most 6 games
STAGGER = (0, 90 * 60)                       # ESTIMATE: later tip-offs, uniform
# owner_known universe of the census's top 50 (minutes a year the mix was on, summed over the full years):
# kept: PARK leagues only, at least 2 games, at most 1 NFL game. Larger mixes are capped at MIX_MAX_GAMES by `draw_mix`.
CENSUS_MINUTES = {
    "nba:2": 26510.0, "cbb:2": 18587.0, "nba:3": 12862.0, "cbb:1+nba:1": 12231.0, "cfb:2": 10171.0, "cbb:3": 9382.0,
    "nba:1+nfl:1": 9266.0, "cfb:3": 9024.0, "cbb:2+nba:1": 7803.0, "nba:4": 7147.0, "nba:2+nfl:1": 6862.0,
    "cfb:10": 6727.0, "nba:2+cbb:1": 5945.0, "cfb:9": 5913.0, "cbb:7": 5259.0, "cfb:11": 5246.0, "cbb:6": 5039.0,
    "cfb:8": 4722.0, "cbb:5": 4629.0, "cbb:4": 4487.0, "cbb:2+nba:2": 4400.0, "nba:5": 4383.0, "cfb:1+nba:1": 4284.0,
    "cfb:4": 4232.0, "cbb:3+nba:2": 3955.0, "cbb:1+cfb:1": 3920.0,
}


def _root(root: Path | None) -> Path:
    """The repo root (gym2.env's, imported late: env imports this module)."""
    if root is not None:
        return Path(root)
    from redzone.lab.gym2.env import REPO_DIR
    return REPO_DIR


def parse_mix(s: str) -> dict:
    out: dict = {}
    for part in s.split("+"):
        lg, n = part.split(":")
        out[lg] = out.get(lg, 0) + int(n)
    return out


def available(leagues=PARK_LEAGUES, root: Path | None = None) -> set:
    """Leagues with recorded dev (fit) games here (CI's pinned eval data has nba and nfl; the Mac's data may have more)."""
    root = _root(root)
    return {lg for lg in leagues if (root / "data/archive" / lg).is_dir() and fit_games(lg, root)}


def draw_mix(seed: int, *, have: set | None = None, weights: dict | None = None) -> dict:
    """One mix, census-weighted, among those whose leagues all have recorded games here; capped at MIX_MAX_GAMES games
    (a larger mix keeps its leagues in proportion, each at least 1)."""
    have = available() if have is None else set(have)
    w = {m: v for m, v in (weights or CENSUS_MINUTES).items() if set(parse_mix(m)) <= have}
    if not w:
        raise ValueError(f"no census mix can be built from the leagues here: {sorted(have)}")
    rng = random.Random(f"gym2-mix:{int(seed)}")
    keys = sorted(w)
    x, acc, pick = rng.random() * sum(w.values()), 0.0, keys[-1]
    for k in keys:
        acc += w[k]
        if x < acc:
            pick = k
            break
    mix = parse_mix(pick)
    tot = sum(mix.values())
    if tot > MIX_MAX_GAMES:
        mix = {lg: max(1, round(n * MIX_MAX_GAMES / tot)) for lg, n in mix.items()}
        while sum(mix.values()) > MIX_MAX_GAMES:
            big = max(sorted(mix), key=lambda lg: mix[lg])
            mix[big] -= 1
    return mix


def shift_replay(r, dt: int):
    """The recorded game with every stamp moved by dt whole seconds (its rows' wallclock and its wall list)."""
    from redzone.sources import espn
    dt = int(dt)
    plays = []
    for p in r.plays:
        q = dict(p)
        t = espn._ts(p.get("wallclock"))
        if t is not None:
            q["wallclock"] = datetime.fromtimestamp(t + dt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        plays.append(q)
    return replace(r, plays=plays, wall=[w + dt for w in r.wall], _cache={})


def fit_games(league: str, root: Path | None = None) -> list[tuple[str, str]]:
    """(night, game id) of every recorded dev (fit) game of a league, oldest first. Locked nights never appear."""
    from redzone.lab import splits
    d = _root(root) / "data/archive" / league
    keep, games = splits.nights_for(d, "fit") or set(), splits.archive_nights(d)
    return [(n, g) for n in sorted(keep) for g in games.get(n, ())]


def compose(mix: dict, seed: int, *, root: Path | None = None, games: list | None = None) -> tuple[list, list, list]:
    """(recorded games, their whole-night facts, the games used as [league, game id, shift s]) for one census mix: random
    fit games of each league, the first tipping off at the earliest original tip, the others STAGGER later.
    Deterministic in (mix, seed) on one archive; with `games` (from a draw log) exactly those games and shifts, so a
    mixed episode re-runs identically on another machine. A chosen game that cannot be loaded FAILS (never skipped);
    games with fewer than 60 rows are passed over in the draw (load_replay's own rule) and counted in the error."""
    from redzone.lab.backtest import Replay
    from redzone.lab.gym2.env import replay_module
    from redzone.models import League
    root = _root(root)
    pr = replay_module()                       # (puts scripts/ on the path)
    import park_ceiling as pc

    def load(lg: str, gid: str):
        f = root / "data/archive" / lg / f"summary_{gid}.json"
        if not f.exists():
            raise FileNotFoundError(f"mix game {lg} {gid}: {f} is not here")
        r = Replay.load(League(lg), f)         # a broken file raises: never skipped silently
        if r is None:
            raise ValueError(f"mix game {lg} {gid}: no plays in {f}")
        return r

    if games:
        out = [pr.repair(shift_replay(load(lg, gid), int(dt))) for lg, gid, dt in games]
        out.sort(key=lambda r: r.wall[0])
        return out, [pc.truth_of(r) for r in out], [list(x) for x in games]
    rng = random.Random(f"gym2-compose:{int(seed)}:{sorted(mix.items())}")
    rs, used = [], []
    for lg in sorted(mix):
        pool = fit_games(lg, root)
        rng.shuffle(pool)
        got, short = 0, 0
        for night, gid in pool:
            if got == mix[lg]:
                break
            r = load(lg, gid)
            if len(r.plays) >= 60:
                rs.append(r)
                used.append((lg, gid))
                got += 1
            else:
                short += 1
        if got < mix[lg]:
            raise ValueError(f"only {got} recorded {lg} dev games here ({short} passed over with < 60 rows), the mix needs {mix[lg]}")
    t_first = min(r.wall[0] for r in rs)
    order = list(range(len(rs)))
    rng.shuffle(order)
    out, spec = [], []
    for n, k in enumerate(order):
        r = rs[k]
        at = t_first + (0 if n == 0 else rng.randint(*STAGGER))
        dt = int(round(at - r.wall[0]))
        out.append(pr.repair(shift_replay(r, dt)))
        spec.append([used[k][0], used[k][1], dt])
    pairs = sorted(zip(out, spec), key=lambda x: x[0].wall[0])
    return [p[0] for p in pairs], [pc.truth_of(p[0]) for p in pairs], [p[1] for p in pairs]


def mixed_timing(rs: list):
    """A `timing` hook for park_replay.build: each game keeps its own league's timing (stamp spread), as the replay
    already does for football."""
    def fix(tm):
        from redzone.noggin.park_show import ShowTiming
        from redzone.noggin.park_show import GameTiming
        games = [GameTiming(str(r.league.value if hasattr(r.league, "value") else r.league), g.k, g.params) for r, g in zip(rs, tm.g)]
        return ShowTiming(games, delay=tm.delay, read=tm.read, eyes=tm.eyes, params=tm.p)
    return fix
