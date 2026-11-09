"""GameValue training rows: one game's play-by-play -> rows of causal features and next-minutes value labels.

Why: the brain needs "how much is this game worth over the next minutes" labels on one scale for every league,
built from the same moment detectors the product uses, and every row must be provably from an allowed night and
from before its decision (the-mind sections 5 and 9, build plan WP-25). Pure: no clock, no network, no TV.

    rows = build_rows("nfl", game_id, summary, night="20231008", season=2023, source="nflverse_pbp", ...)
    checked, bad = prefix_check("nfl", game_id, summary, rows)      # features rebuilt from a cut-off feed must match
    write_shard(out, "nfl", "2023", rows, info); finalize(out)      # resumable: a finished shard is never rebuilt

One row is one decision: every play and every 30 s of a game (5 s rows are near-duplicates). Each draws a feed lag
from the measured per-league profile (redzone/lab/feed_lag_profile.json), seeded by the game id, because training
on a fixed lag teaches a feed that never exists. `feed_cut_t = decision_t - lag_s`; features read only plays with
wall <= feed_cut_t. The labels are anchored on the decision, so they are the future the viewer can still see:

    value_next_3m   sum of tier weights of redzone.moments in (decision_t, +180 s]    raw points; the scale is WP-26's
    p_tier1_60s     any Tier 1 in (decision_t, +60 s]

Features v2: v1 anchored the labels on the feed head, so `since_head_s` told the model how much of the label had already
passed and a silent feed read as a zero label. `label_first_t` records the first counted event; it must be after decision_t.
Football reads drives.current as well as drives.previous, so a live feed head matches a finished game's. What this does
not model is how far the picture runs behind the feed (a stream's delay is not in play-by-play): that needs a live night.

Published win-probability columns never enter (nflfastR's were fit across seasons), so `winprobability` is removed
before detection and wp5 / wp10 fire on no row: public and archive rows mean the same thing.

Features v3 (RZ_GV_FEATURES=v3, off by default so v2 shards and models stay comparable) add recent history at the feed head:
points and swing over the last 3-5 plays, the current unanswered run, turnovers and fouls in the last 5 min, pace, timeouts.
All twelve come from gamevalue.history_features, the same numpy code a live caller runs on the plays of the feed so far.

Features v4 (RZ_GV_FEATURES=v4) are v3 plus the drive state, closeness and league-relative numbers of gamevalue.py. Under v4 a football feed drops
rows only one source has (gamevalue.feed_row), and yards_to_goal reads -1 once the ball has changed hands, because ESPN and nflverse put that
field in different places then. Rows and labels are the same plays either way; only the features change.

Features v5 (RZ_GV_FEATURES=v5) are v4 plus the kept columns of wide_features.py. Those read only plays at or before the feed head and never the decision.

Sources: only what docs/agents/data/2026-11-09-sports-data-gaps.md lists as licence-cleared, see `public_status`.
NBA and CBB (hoopR) stay off in code unless `personal_nba=True`, because Nick has not ruled on that report's conditional row.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from redzone.context.tokens import PERSONAL_USE
from redzone.moments.detect import Detected, clock_s, detect, football_drives, game_span, parse_ts
from redzone.noggin import factory
from redzone.noggin import gamevalue as G
from redzone.noggin import wide_features as W

FEATURES_VERSION, VALUE_FEATURES, VALUE_COLUMNS = "", (), ()     # set by select_features below
_STATE_VERSIONS = ("v4", "v5")   # the versions built on the drive state and the feed_row filter
HORIZON_S, TIER1_S, GRID_S, MAX_GAP_S, LAG_CAP_S = 180.0, 60.0, 30.0, 900.0, 180.0
TIER_WEIGHTS = {1: 5.0, 2: 2.0, 3: 0.5}      # scripts/holdset.py WEIGHTS: same scale as the bar the brain must beat
# pp_start waits on later penalties at the same game-clock tick (coincidental minors), so it is a label only.
FEATURE_UNSAFE_KINDS = frozenset({"pp_start"})
PREFIX_ROWS = 5                              # rows per game re-derived from a cut-off feed

REGULATION_LEN = {"nfl": (4, 900.0), "cfb": (4, 900.0), "nba": (4, 720.0), "wnba": (4, 600.0), "cbb": (2, 1200.0),
                  "nhl": (3, 1200.0)}
LAG_PROFILE = Path(__file__).resolve().parents[1] / "lab" / "feed_lag_profile.json"

VALUE_LABELS = ("value_next_3m", "p_tier1_60s")
VALUE_META = ("league", "season", "game_id", "game_key", "night", "source_night_id", "source", "license", "plan",
        "decision_t", "feed_cut_t", "feed_head_t", "feature_t_max", "lag_s", "label_full_window", "label_first_t")


def select_features(version: str) -> None:
    """Make `version` (gamevalue.GV_FEATURE_SETS) the active list, here and in the runtime, which must always agree."""
    global FEATURES_VERSION, VALUE_FEATURES, VALUE_COLUMNS
    G.select_features(version)
    FEATURES_VERSION, VALUE_FEATURES = version, G.GV_FEATURE_SETS[version]
    VALUE_COLUMNS = VALUE_META + VALUE_FEATURES + VALUE_LABELS


select_features(G.GV_FEATURES_VERSION)

# league -> (source id, licence line, plan, url, seasons newest first). From the gaps report: Licence OK = Yes.
_CLEARED = {"nfl": ("nflverse_pbp", "CC-BY 4.0 (nflverse data); credit nflverse", "A", factory.SOURCES["nfl"]["url"],
                    tuple(range(2024, 1998, -1)))}   # 1999-2010: nflverse has no time_of_day for most of them;
# a game with no snap clock is skipped (stats no_wall_games), so a season without one is an empty shard, not an error
# Personal-use hoopR leagues, one row each: nothing else in the build knows which leagues these are (WP-25 CBB).
# CBB has two 20 min halves (REGULATION_LEN), so period 3+ is overtime; ESPN's feed carries no shot clock, so none is read.
_PERSONAL = {
    "nba": ("hoopr_espn_nba", f"{PERSONAL_USE}; hoopR ESPN NBA", "B", factory.SOURCES["nba"]["url"], tuple(range(2026, 2013, -1))),
    "cbb": ("hoopr_espn_cbb", f"{PERSONAL_USE}; hoopR ESPN men's college basketball", "B", factory.SOURCES["cbb"]["url"],
            tuple(range(2026, 2005, -1))),
    # CFB: ESPN-derived (sportsdataverse espn_cfb_pbp / cfbfastR-data), fetched by agent/kaggle/fetch_sports_cfb.py. Football rules
    # (nfl detectors and drives); the fetch has 2002-2024, a season whose columns lack the wall clock builds an empty shard.
    "cfb": ("espn_cfb", f"{PERSONAL_USE}; ESPN college football via sportsdataverse", "B",
            "https://github.com/sportsdataverse/sportsdataverse-data", tuple(range(2024, 2001, -1))),
    # WNBA: wehoop ESPN pbp (agent/kaggle/fetch_sports_wnba.py). Four 10 min quarters, NBA detectors; seasons before the
    # wall clock was kept build empty shards.
    "wnba": ("wehoop_espn_wnba", f"{PERSONAL_USE}; wehoop ESPN WNBA", "B",
             "https://github.com/sportsdataverse/wehoop-wnba-data", tuple(range(2025, 2001, -1))),
}
PERSONAL_LEAGUES = tuple(_PERSONAL)
_NO_WALLCLOCK = {"mlb": "no wall clock: Retrosheet (16.76 M plays, cleared) and Statcast pitches (game_date, inning, pitch number) "
                        "carry no timestamp, so no timed label exists. The only source with one is the MLB Stats API live feed "
                        "(playEvents startTime, 2008-24, scripts/pbp/mlb_statsapi.py, personal-use under Nick's Nov 5 ruling): "
                        "it needs a fetch notebook and a plate-appearance adapter before MLB can be built"}


class SourceNotCleared(ValueError):
    """A public source the licence report does not clear was asked for."""


def public_status(league: str, personal_nba: bool = False) -> tuple[str, str]:
    """('ok', why) when a public source may be built for this league, else ('no_wallclock' | 'not_cleared', why)."""
    if league in _CLEARED:
        return "ok", _CLEARED[league][1]
    if league in _PERSONAL:
        if personal_nba:
            return "ok", _PERSONAL[league][1]
        return "not_cleared", (f"{league.upper()} is conditional (private, non-commercial only): "
                               "pass personal_nba (--personal-nba / --personal-hoopr) after Nick rules")
    if league in _NO_WALLCLOCK:
        return "no_wallclock", _NO_WALLCLOCK[league]
    return "not_cleared", "the licence report lists no cleared source for this league"


def public_source(league: str, personal_nba: bool = False) -> tuple:
    """(source id, licence, plan, url, seasons) or SourceNotCleared."""
    status, why = public_status(league, personal_nba)
    if status != "ok":
        raise SourceNotCleared(f"{league}: {why}")
    return _CLEARED[league] if league in _CLEARED else _PERSONAL[league]


@lru_cache(maxsize=1)
def _lag_profile() -> dict:
    return json.loads(LAG_PROFILE.read_text())


def sample_lags(league: str, game_id: str, n: int) -> np.ndarray:
    """n feed lags from the league's measured quantiles (capped at 180 s), the same for the same game every run."""
    prof = _lag_profile()
    near = {"cfb": "nfl", "wnba": "nba"}.get(league, "nfl")   # no measured profile of its own: the nearest league's
    q = (prof["leagues"].get(league) or prof["leagues"][near])["q"]
    seed = int(hashlib.sha256(f"{league}|{game_id}".encode()).hexdigest()[:16], 16)
    u = np.random.default_rng(seed).uniform(0.0, 100.0, n)
    return np.minimum(np.interp(u, prof["quantiles"], q), LAG_CAP_S)


def _football(league: str) -> bool:
    return league in ("nfl", "cfb")


def strip_published_wp(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if k != "winprobability"}


def flat_plays(league: str, summary: dict) -> list[tuple[float, dict]]:
    """(wall, play) for every timed play, oldest first (stable, so feed order breaks ties)."""
    if _football(league):
        raw = [p for dr in football_drives(summary) for p in dr.get("plays") or []]
    else:
        raw = summary.get("plays") or []
    timed = [(parse_ts(p.get("wallclock")), p) for p in raw]
    return sorted([(t, p) for t, p in timed if t], key=lambda x: x[0])


def wall_ordered(league: str, summary: dict) -> dict:
    """The summary with its plays in wall-clock order (stable, so feed order breaks ties). The detectors are stateful and
    walk the list: when a play's stamp is later than the next plays' (hoopR has these), a cut feed lacks it while the full
    game has already counted it, so runs and lead changes fire differently. In wall order a cut feed is a true prefix.
    Football keeps feed order: its only state is per drive and flat_plays does not regroup drives."""
    if _football(league):
        return summary
    plays = sorted(summary.get("plays") or [], key=lambda p: parse_ts(p.get("wallclock")) or float("inf"))
    return {**summary, "plays": plays}


def truncate(league: str, summary: dict, cut: float) -> dict:
    """The summary as a feed cut off at wall time `cut` would have shown it."""
    keep = lambda p: (parse_ts(p.get("wallclock")) or float("inf")) <= cut  # noqa: E731
    out = dict(summary)
    if _football(league):
        out["drives"] = {"previous": [{**dr, "plays": [p for p in dr.get("plays") or [] if keep(p)]}
                                      for dr in football_drives(summary)]}
    else:
        out["plays"] = [p for p in summary.get("plays") or [] if keep(p)]
    return out


@dataclass
class GameView:
    """Per-play arrays of one game, everything a feature may read."""
    league: str
    walls: np.ndarray
    period: np.ndarray
    clock: np.ndarray
    total: np.ndarray
    margin: np.ndarray
    lead_changes: np.ndarray
    last_score: np.ndarray
    down: np.ndarray
    distance: np.ndarray
    ytg: np.ndarray
    tier_t: dict[int, np.ndarray]
    points_t: np.ndarray
    points_cum: np.ndarray
    playoff: int
    history: G.History | None = None
    state: G.State | None = None
    wide: W.Series | None = None


def _value(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return -1.0
    return -1.0 if v != v else v


def _moment_arrays(moments) -> tuple[dict[int, np.ndarray], np.ndarray, np.ndarray]:
    """Sorted times per tier, plus sorted times with the running sum of tier weights."""
    ms = sorted(moments, key=lambda m: m.t)
    by_tier = {k: np.array([m.t for m in ms if m.tier == k], float) for k in TIER_WEIGHTS}
    t = np.array([m.t for m in ms], float)
    return by_tier, t, np.cumsum([TIER_WEIGHTS[m.tier] for m in ms]) if ms else np.zeros(0)


def make_view(league: str, summary: dict, det: Detected, playoff: int = 0) -> GameView:
    plays = flat_plays(league, summary)
    if FEATURES_VERSION in _STATE_VERSIONS:
        plays = [x for x in plays if G.feed_row(league, x[1])]
    n = len(plays)
    arr = {k: np.full(n, -1.0) for k in ("period", "clock", "down", "distance", "ytg")}
    home, away, lc, last = np.zeros(n), np.zeros(n), np.zeros(n), np.full(n, -1.0)
    h = a = 0.0
    sign, changes, scored = 0, 0, -1.0
    for i, (t, p) in enumerate(plays):
        arr["period"][i] = _value((p.get("period") or {}).get("number"))
        c = clock_s(p)
        arr["clock"][i] = -1.0 if c is None else c
        h1, a1 = p.get("homeScore"), p.get("awayScore")
        nh = h if h1 is None else float(h1)
        na = a if a1 is None else float(a1)
        if nh + na > h + a:
            scored = t
        h, a = nh, na
        s = (h > a) - (h < a)
        if s and sign and s != sign:
            changes += 1
        sign = s or sign
        home[i], away[i], lc[i], last[i] = h, a, changes, scored
        if _football(league):
            st, en = p.get("start") or {}, p.get("end") or {}
            arr["down"][i], arr["distance"][i], arr["ytg"][i] = (_value(st.get("down")), _value(st.get("distance")),
                                                                 _value(en.get("yardsToEndzone")))
    safe = [m for m in det.moments if m.kind not in FEATURE_UNSAFE_KINDS]
    tier_t, pts_t, pts_cum = _moment_arrays(safe)
    return GameView(league, np.array([t for t, _ in plays], float), arr["period"], arr["clock"], home + away, home - away,
                    lc, last, arr["down"], arr["distance"], arr["ytg"], tier_t, pts_t, pts_cum, int(playoff),
                    G.history_arrays(league, plays), G.state_arrays(league, plays) if FEATURES_VERSION in _STATE_VERSIONS else None,
                    W.track(league, plays) if FEATURES_VERSION == "v5" else None)


def _count_in(times: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return (np.searchsorted(times, hi, "right") - np.searchsorted(times, lo, "right")).astype(float)


def _sum_in(times: np.ndarray, cum: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    padded = np.concatenate(([0.0], cum))
    return padded[np.searchsorted(times, hi, "right")] - padded[np.searchsorted(times, lo, "right")]


def _first_in(times: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Time of the first event in (lo, hi], NaN where there is none."""
    first = np.concatenate((times, [np.inf]))[np.searchsorted(times, lo, "right")]
    return np.where(first <= hi, first, np.nan)


def features(v: GameView, head: np.ndarray, decision_t: np.ndarray) -> dict[str, np.ndarray]:
    """The whole feature definition. Reads only plays up to `head` and moments at or before that play's wall."""
    wall = v.walls[head]
    regs, length = REGULATION_LEN.get(v.league, (0, 0.0))
    period, clock = v.period[head], v.clock[head]
    is_ot = (regs > 0) & (period > regs)
    game_left = np.where(regs > 0, np.where(is_ot, np.maximum(clock, 0.0), (regs - period) * length + clock), -1.0)
    game_left = np.where(clock < 0, -1.0, game_left)
    five_ago = np.searchsorted(v.walls, wall - 300.0, "right") - 1
    total_5 = np.where(five_ago >= 0, v.total[np.maximum(five_ago, 0)], 0.0)
    margin_5 = np.where(five_ago >= 0, v.margin[np.maximum(five_ago, 0)], 0.0)
    scored = v.last_score[head]
    early = wall - 300.0
    out = {
        "period": period, "clock_left": clock, "game_left": game_left, "is_ot": is_ot.astype(float),
        "late": (is_ot | ((period == regs) & (clock >= 0) & (clock <= 300))).astype(float),
        "abs_margin": np.abs(v.margin[head]), "total_points": v.total[head], "pts_5m": v.total[head] - total_5,
        "swing_5m": np.abs(v.margin[head] - margin_5), "lead_changes": v.lead_changes[head],
        "plays_5m": (head - five_ago).astype(float), "since_head_s": decision_t - wall,
        "since_score_s": np.where(scored >= 0, wall - scored, wall - v.walls[0]), "since_start_s": wall - v.walls[0],
        "t1_5m": _count_in(v.tier_t[1], early, wall), "t2_5m": _count_in(v.tier_t[2], early, wall),
        "t3_5m": _count_in(v.tier_t[3], early, wall), "t1_game": np.searchsorted(v.tier_t[1], wall, "right").astype(float),
        "value_10m": _sum_in(v.points_t, v.points_cum, wall - 600.0, wall), "down": v.down[head],
        "distance": v.distance[head], "yards_to_goal": v.ytg[head], "playoff": np.full(len(head), float(v.playoff)),
    }
    if FEATURES_VERSION != "v2":
        out.update(G.history_features(v.history, head))
    if FEATURES_VERSION in _STATE_VERSIONS:
        out.update(G.state_features(v.state, head))
        out.update(G.derived_features(v.league, out))
    if FEATURES_VERSION == "v5":
        out.update(W.features(v.wide, head, _wide_columns(), G.GV_V5_OWN.get(v.league, frozenset())))       # head only: no decision time, no cut, no label reaches these columns
    return out


def _wide_columns() -> tuple[str, ...]:
    """The kept v5 columns of the active list, in its order."""
    return tuple(f for f in VALUE_FEATURES if f.startswith(W.PREFIX + W.SEP))


FeatureFn = Callable[[GameView, np.ndarray, np.ndarray], dict]


def build_rows(league: str, game_id: str, summary: dict, *, night: str, season: int, source: str, license_line: str,
               plan: str, playoff: int = 0, game_key: str = "", feature_fn: FeatureFn = features) -> pd.DataFrame:
    """All decision rows of one game; empty when the feed is not a whole game (30+ timed plays, 1.2-7 h)."""
    clean = wall_ordered(league, strip_published_wp(summary))
    det = detect(league, clean, game_id)
    if game_span(det.times) is None:
        return pd.DataFrame()
    view = make_view(league, clean, det, playoff)
    w = view.walls
    cuts = np.unique(np.concatenate((w, np.arange(w[0], w[-1] + 1.0, GRID_S))))
    lag = sample_lags(league, game_id, len(cuts))
    head = np.searchsorted(w, cuts, "right") - 1
    keep = (head >= 0) & (cuts - w[np.maximum(head, 0)] <= MAX_GAP_S)
    cuts, lag, head = cuts[keep], lag[keep], head[keep]
    decision = cuts + lag
    wall = w[head]
    tier_all, t_all, cum_all = _moment_arrays(det.moments)
    data: dict = {"decision_t": decision, "feed_cut_t": cuts, "feed_head_t": wall, "feature_t_max": wall, "lag_s": lag,
                  "label_full_window": (decision + HORIZON_S <= w[-1]).astype(np.int8),
                  "label_first_t": _first_in(t_all, decision, decision + HORIZON_S)}
    for k, val in feature_fn(view, head, decision).items():
        data[k] = np.asarray(val, dtype=np.float32)
    data["value_next_3m"] = _sum_in(t_all, cum_all, decision, decision + HORIZON_S).astype(np.float32)
    data["p_tier1_60s"] = (_count_in(tier_all[1], decision, decision + TIER1_S) > 0).astype(np.float32)
    df = pd.DataFrame(data)
    for k, val in (("league", league), ("season", int(season)), ("game_id", str(game_id)), ("game_key", game_key),
                   ("night", night), ("source_night_id", f"{league}:{night}"), ("source", source),
                   ("license", license_line), ("plan", plan)):
        df[k] = val
    return df[list(VALUE_COLUMNS)]


def prefix_check(league: str, game_id: str, summary: dict, rows: pd.DataFrame, n: int = PREFIX_ROWS,
                 feature_fn: FeatureFn = features, playoff: int = 0) -> tuple[int, list[str]]:
    """Rebuild n rows' features from a feed cut off at each row's feed_cut_t. A feature that reads the future differs.
    Returns (rows checked, descriptions of every mismatch)."""
    if rows.empty:
        return 0, []
    clean = wall_ordered(league, strip_published_wp(summary))
    bad, picks = [], np.unique(np.linspace(0, len(rows) - 1, min(n, len(rows))).astype(int))
    for i in picks:
        r = rows.iloc[int(i)]
        cut_summary = truncate(league, clean, float(r.feed_cut_t))
        view = make_view(league, cut_summary, detect(league, cut_summary, game_id), playoff)
        head = np.array([len(view.walls) - 1])
        again = feature_fn(view, head, np.array([float(r.decision_t)]))
        for k, val in again.items():
            if not np.isclose(float(np.float32(val[0])), float(r[k]), rtol=1e-5, atol=1e-3):
                bad.append(f"{game_id} row {int(i)} {k}: {float(r[k])} from the full game, {float(val[0])} from the feed")
    return len(picks), bad


# --- shards ---------------------------------------------------------------------------------------------------


def _kept_sha() -> str:
    """Hash of the active v5 columns, not the file: what a shard was built with."""
    return hashlib.sha256(json.dumps({"columns": sorted(_wide_columns()), "own": {lg: sorted(c) for lg, c in sorted(G.GV_V5_OWN.items())}}).encode()).hexdigest()


def shards_id(out: Path) -> str:
    """Short id of a finished shards dir (its manifest and splits): the kept list records which build picked it."""
    out = Path(out)
    return hashlib.sha256((out / "manifest.json").read_bytes() + (out / "splits.json").read_bytes()).hexdigest()[:16]


def shard_paths(out: Path, league: str, shard: str) -> tuple[Path, Path]:
    return Path(out) / league / f"{shard}.parquet", Path(out) / league / f"{shard}.json"


def shard_done(out: Path, league: str, shard: str) -> bool:
    """The sidecar is written last, so a killed session leaves no shard that looks finished. A shard from an older
    feature version is not finished either: its labels mean something else."""
    pq, side = shard_paths(out, league, shard)
    if not (side.exists() and pq.exists()):
        return False
    info = json.loads(side.read_text())
    if FEATURES_VERSION == "v5" and info.get("v5_kept_sha") != _kept_sha():
        return False
    return bool(info.get("complete")) and info.get("features_version") == FEATURES_VERSION


def rows_sha(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()).hexdigest()


def write_shard(out: Path, league: str, shard: str, rows: pd.DataFrame, info: dict) -> dict:
    """Atomic: parquet first, sidecar last, no timestamps, so the same input gives the same bytes."""
    pq, side = shard_paths(out, league, shard)
    pq.parent.mkdir(parents=True, exist_ok=True)
    frame = rows if len(rows) else pd.DataFrame({c: pd.Series(dtype="float32") for c in VALUE_COLUMNS})
    tmp = pq.with_suffix(".tmp")
    frame.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, pq)
    if FEATURES_VERSION == "v5":
        info = {**info, "v5_kept_sha": _kept_sha(), "v5_columns": list(_wide_columns()), "v5_own": sorted(G.GV_V5_OWN.get(league, ()))}
    meta = {**info, "league": league, "shard": shard, "rows": int(len(rows)), "complete": True,
            "features_version": FEATURES_VERSION, "rows_sha256": rows_sha(frame),
            "nights": sorted(rows["night"].unique().tolist()) if len(rows) else [],
            "games": int(rows["game_id"].nunique()) if len(rows) else 0}
    tmp_side = side.with_suffix(".tmp")
    tmp_side.write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.replace(tmp_side, side)
    return meta


def finalize(out: Path) -> dict:
    """manifest.json (rows per league and shard) and splits.json (public nights: whole nights, oldest first, the
    factory's train | calib | test fractions). Rebuilt from sidecars, so resuming never rewrites a finished shard."""
    out = Path(out)
    man: dict = {"features_version": FEATURES_VERSION, "leagues": {}}
    nights: dict[str, set] = {}
    for side in sorted(out.glob("*/*.json")):
        i = json.loads(side.read_text())
        lg = man["leagues"].setdefault(i["league"], {"rows": 0, "games": 0, "nights": 0, "shards": {}})
        lg["rows"] += i["rows"]
        lg["games"] += i["games"]
        lg["nights"] += len(i["nights"])
        lg["shards"][i["shard"]] = {k: i.get(k) for k in ("rows", "games", "source", "license", "plan")}
        if i.get("source") != "archive":
            nights.setdefault(i["league"], set()).update(i["nights"])
    splits = {}
    for lg, ns in sorted(nights.items()):
        if ns:
            t = factory.assign_splits(pd.DataFrame({"night": sorted(ns)}))
            splits[lg] = {s: sorted(g.night) for s, g in t.groupby("split")}
    (out / "splits.json").write_text(json.dumps(splits, indent=1, sort_keys=True))
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True))
    return man
