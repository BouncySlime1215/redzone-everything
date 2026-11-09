"""Brain v3 training tables: one row per logged event, with the v3 sequence features and labels.

Built by ``scripts/build_training.py --v3`` into ``data/training3/<league>.parquet`` (git-ignored),
from the same free play-by-play the v2 brain used (``redzone.lab.sportsdata`` normalizers) plus our
own ESPN archive (``espn_<league>``, the live feed itself: the head-to-head test set).

Time. Every horizon is in WALL seconds (what the screen lives in), from the feed's timestamps:
nflverse ``time_of_day``, ESPN ``wallclock`` (NBA, our archive), MLB plate-appearance stamps. The
public NHL feed has no wall clock: its horizons use the game clock, which runs exactly while the puck
is live, so "a stoppage within 30 s of play" is the same thing (``clock`` column says which).

Labels (NaN where not defined):
    stop15/30/60    a stoppage STARTS within 15 / 30 / 60 s (rows in play only)
    stop_type       the first such stoppage's type (TYPES index, 0 = none within 60 s)
    len_s           at a stoppage's first row: seconds until play resumes (the dead time)
    entry_s         at a dead-ball row: seconds until the next live snap / faceoff / inbound
    big60/big180    a big play (TD, turnover, goal incl. power-play goals, lead change late, clutch
                    shot, big win-probability swing: v2's key_now) within 60 / 180 s
    hotness         v2's 0-100 game flow (kept: the momentum graph and the scorer read it)

Split: by NIGHT (``night``, YYYYMMDD ET). Every game of one evening lands in the same split.

Why: first added in afd3508e (2026-11-03) as "Brain v3 (WIP): one compact model per league, numpy runtime, calibrated stoppage odds/type/length..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from redzone.lab import datasets, splits, sportsdata
from redzone.lab.datasets import STOPPAGE_CODE

TRAINING3_DIR = datasets.ROOT / "data" / "training3"

# Stoppage types the brain names. "other" = pitching change, an unlogged long dead spell, ...
TYPES = ("none", "tv_timeout", "team_timeout", "review", "free_throws", "injury", "score_break",
         "period_end", "other")
TYPE_CODE = {t: i for i, t in enumerate(TYPES)}
HORIZONS = (15, 30, 60)
BIG = (60, 180)
MAX_DEAD_S = 1500.0     # longer "dead time" is a feed gap (or a rain delay), not a break
# An unlogged dead spell this long after an ordinary NFL play is a commercial (TV timeout).
NFL_UNLOGGED_BREAK_S = 100.0
# A basketball timeout whose dead time runs this long went to commercial.
NBA_TV_TIMEOUT_S = 140.0

# Event classes and the v3 sequence features: one definition, shared with the live tracker.
from redzone.scoring.brain3 import KC, KCLASS, NHL_PENALTIES, SEQ, V3_EXTRA, kclass  # noqa: E402,F401


def night_splits(league: str, nights) -> dict[str, str]:
    """night -> "train" / "valid" / "test": the TIME split by whole night (oldest train, newest >= 25% locked
    test; redzone/lab/splits.py under the active pin). Replaces the crc32 hash of the night (grader v47, F6):
    a hash put ~70% of the locked nights in train and made every brain number leaked. `league` may be an
    archive name ("espn_nhl")."""
    lg = str(league).removeprefix("espn_")
    key = {n: str(n).replace("-", "") for n in set(nights)}
    sp = splits.league_split(lg, key.values())
    where = {**{n: "train" for n in sp.train}, **{n: "valid" for n in sp.valid}, **{n: "test" for n in sp.test}}
    return {n: where.get(k, "train") for n, k in key.items()}


# ----------------------------------------------------------------------------- per-league typing

PRESNAP = ("false start|delay of game|encroachment|neutral zone|illegal shift|illegal formation|"
           "defensive offside|illegal substitution|too many men|unsportsmanlike|taunting|"
           "unnecessary roughness, .*enforced between downs")


def _restart_mask(base: str, e: pd.DataFrame) -> np.ndarray:
    """Events that put the ball back in play (the clean ENTRY point is just before them)."""
    t = e.ttext.astype(str).values
    st = e.stoppage.values
    if base == "nfl":
        dead = ("timeout", "end quarter", "end game", "game start", "comment", "unspecified",
                "xp kick", "pat2", "end period", "end of half", "end of game", "two-minute warning",
                "end of regulation", "coin toss", "extra point")
        d = e.desc.astype(str)
        # A penalty row is a snap that was flagged, unless the foul came before the snap.
        presnap = (pd.Series(t).str.contains("penalty").values
                   & d.str.contains(PRESNAP, regex=True).values)
        return (~pd.Series(t).str.contains("|".join(dead), regex=True).values & ~presnap
                & (st != "game_end"))
    if base == "nba":
        return (st == "none") & ~pd.Series(t).str.contains("timeout|end period|end game|substitution|"
                                                            "jumpball|jump ball", regex=True).values
    if base == "nhl":
        return pd.Series(t).str.contains("faceoff|face off", regex=True).values
    # mlb: every pitch / plate appearance is the ball in play
    return (st == "none") | (st == "whistle")


_PRIORITY = {TYPE_CODE[t]: r for r, t in enumerate(
    ("other", "free_throws", "team_timeout", "injury", "review", "tv_timeout", "score_break", "period_end"), 1)}


def _one_per_spell(typ: np.ndarray, restart: np.ndarray) -> np.ndarray:
    """One dead spell (between two restarts), one stoppage start: the spell's first typed event,
    named for the spell's strongest type (a TD then an official timeout is one break, a TV one)."""
    spell = np.cumsum(restart)
    out = np.zeros_like(typ)
    pri = np.array([_PRIORITY.get(int(x), 0) for x in typ])
    seen: dict[int, int] = {}
    for i in np.flatnonzero(typ > 0):
        s_ = int(spell[i])
        j = seen.get(s_)
        if j is None:
            seen[s_] = i
            out[i] = typ[i]
        elif pri[i] > _PRIORITY.get(int(out[j]), 0):
            out[j] = typ[i]
    return out


def _types(base: str, e: pd.DataFrame, dead_after: np.ndarray, restart: np.ndarray) -> np.ndarray:
    """Stoppage type STARTING at each event (TYPE_CODE, 0 = none)."""
    st = e.stoppage.values
    t = e.ttext.astype(str).values
    d = e.desc.astype(str).values
    n = len(e)
    out = np.zeros(n, dtype=np.int8)
    m = lambda arr, pat: pd.Series(arr).str.contains(pat, regex=True).values  # noqa: E731
    is_to = st == "timeout"
    if base == "nfl":
        team = is_to & (m(d, r"timeout #") | m(t, r"^timeout$") & m(d, r"timeout #"))
        out[is_to] = TYPE_CODE["tv_timeout"]
        out[team] = TYPE_CODE["team_timeout"]
        out[st == "two_minute_warning"] = TYPE_CODE["tv_timeout"]
        # An ordinary play followed by a long unlogged dead spell: a commercial (punt, turnover, injury).
        nxt_restart = np.r_[restart[1:], False]
        quiet = (st == "none") & nxt_restart & (dead_after >= NFL_UNLOGGED_BREAK_S) & (dead_after <= MAX_DEAD_S)
        out[quiet] = TYPE_CODE["tv_timeout"]
    elif base == "nba":
        long_ = dead_after >= NBA_TV_TIMEOUT_S
        out[is_to & long_] = TYPE_CODE["tv_timeout"]
        out[is_to & ~long_] = TYPE_CODE["team_timeout"]
        ft = m(t, "free throw|freethrow")
        prev_ft = np.r_[False, ft[:-1]]
        foul = m(t, "foul")
        nxt_ft = np.r_[ft[1:], False] | np.r_[ft[2:], False, False]
        prev_foul = np.r_[False, foul[:-1]]
        out[(foul & nxt_ft) | (ft & ~prev_ft & ~prev_foul)] = TYPE_CODE["free_throws"]
    elif base == "nhl":
        tv = is_to & m(d, "tv")
        out[is_to] = TYPE_CODE["team_timeout"]
        out[tv] = TYPE_CODE["tv_timeout"]
    if base == "mlb":
        out[st == "pitching_change"] = TYPE_CODE["other"]
    out[st == "review"] = TYPE_CODE["review"]
    out[st == "injury"] = TYPE_CODE["injury"]
    out[st == "score_break"] = TYPE_CODE["score_break"]
    out[np.isin(st, ["end_period", "halftime", "intermission"])] = TYPE_CODE["period_end"]
    # One stoppage, one start: an event inside the same dead spell as the one before it doesn't start
    # a new one (a timeout right after a TD is still the score break).
    return out


# ----------------------------------------------------------------------------- derive

def _next_time(mask: np.ndarray, t: np.ndarray) -> np.ndarray:
    """For each i: t[j] - t[i] for the first j > i with mask[j] (NaN if none)."""
    n = len(mask)
    idx = np.where(mask, np.arange(n), n)
    nxt = np.minimum.accumulate(idx[::-1])[::-1]
    nxt = np.append(nxt[1:], n)
    ok = nxt < n
    return np.where(ok, t[np.minimum(nxt, n - 1)] - t, np.nan)


def _roll(x: np.ndarray, w: int) -> np.ndarray:
    """Sum of the last w values up to and including i."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(1, len(x) + 1)
    return c[i] - c[np.maximum(0, i - w)]


def _group_count(x: np.ndarray, key: np.ndarray) -> np.ndarray:
    """Running count of x within runs of the same key (key already sorted in event order)."""
    s = pd.Series(x.astype(float))
    return s.groupby(key).cumsum().values


def derive3(league: str, e: pd.DataFrame) -> pd.DataFrame:
    """v2 features + labels (sportsdata.derive already ran) plus the v3 sequence features and labels."""
    base = league.removeprefix("espn_")
    e = e.reset_index(drop=True)
    for c in ("ttext", "desc", "night"):
        if c not in e:
            e[c] = ""
        e[c] = e[c].fillna("").astype(str)
    st_codes = e.stoppage.values
    inv = {v: k for k, v in STOPPAGE_CODE.items()}
    st = np.array([inv.get(int(x), "unknown") for x in st_codes]) if np.issubdtype(np.asarray(st_codes).dtype,
                                                                                     np.number) else st_codes
    e["_st"] = st
    work = e.assign(stoppage=st)
    has_wall = e.wall_s.notna().groupby(e.game_id).transform("mean").values >= 0.8
    n_tot = len(e)
    cols = {k: np.full(n_tot, np.nan) for k in (
        "stop15", "stop30", "stop60", "stop_type", "start_type", "len_s", "entry_s", "big60", "big180", "clock_wall",
        "dead_after", "kc0", "kc1", "kc2", "wall_gap", "pace5", "n_stop20", "n_brk20", "n_foul20",
        "n_score20", "brk_period", "to_period", "fouls_period", "ft_pending")}
    kc = np.array([kclass(s, t, d) for s, t, d in zip(st, e.ttext.values, e.desc.values)], dtype=np.int8)
    key = e.key_now.values.astype(bool)
    restart_all = _restart_mask(base, work)
    starts = np.flatnonzero(np.r_[True, e.game_id.values[1:] != e.game_id.values[:-1]])
    ends = np.r_[starts[1:], n_tot]
    t_all = e.t_game.values.astype(float)
    w_all = e.wall_s.values.astype(float)
    per_all = e.period.values
    for a, b in zip(starts, ends):
        sl = slice(a, b)
        n = b - a
        wall_ok = has_wall[a]
        clock = w_all[sl] if wall_ok else t_all[sl]
        if wall_ok:
            clock = pd.Series(clock).ffill().bfill().fillna(0).values
        cols["clock_wall"][sl] = float(wall_ok)
        restart = restart_all[sl]
        dead_after = _next_time(restart, clock) if wall_ok else np.full(n, np.nan)
        cols["dead_after"][sl] = dead_after
        sub = work.iloc[a:b]
        typ = _types(base, sub, dead_after, restart)
        typ = np.where(sub._st.values == "game_end", 0, typ).astype(np.int8)
        typ = _one_per_spell(typ, restart)
        is_start = typ > 0
        cols["start_type"][sl] = typ
        # horizons: time to the next stoppage start (strictly after this event)
        nxt = _next_time(is_start, clock)
        for h in HORIZONS:
            cols[f"stop{h}"][sl] = (nxt <= h).astype(float)
        # type of the first start within 60 s
        idx = np.where(is_start, np.arange(n), n)
        first = np.minimum.accumulate(idx[::-1])[::-1]
        first = np.append(first[1:], n)
        ft_ = np.where((first < n) & (nxt <= 60), typ[np.minimum(first, n - 1)], 0)
        cols["stop_type"][sl] = ft_
        # lengths and entry: wall only
        if wall_ok:
            ln = np.where(is_start & (dead_after <= MAX_DEAD_S), dead_after, np.nan)
            cols["len_s"][sl] = ln
            dead_row = ~restart | (base in ("nfl", "mlb"))
            en = np.where(dead_row & (dead_after <= MAX_DEAD_S), dead_after, np.nan)
            cols["entry_s"][sl] = en
        nb = _next_time(key[sl], clock)
        cols["big60"][sl] = (nb <= 60).astype(float)
        cols["big180"][sl] = (nb <= 180).astype(float)
        # sequence features (all computable live from the plays the feed shows)
        k = kc[sl]
        cols["kc0"][sl] = k
        cols["kc1"][sl] = np.r_[-1, k[:-1]]
        cols["kc2"][sl] = np.r_[-1, -1, k[:-2]]
        w = w_all[sl]
        gap = np.r_[np.nan, np.diff(w)]
        cols["wall_gap"][sl] = np.clip(gap, 0, 3600)
        g5 = pd.Series(np.clip(gap, 0, 600)).rolling(5, min_periods=1).mean().values
        cols["pace5"][sl] = g5
        stp = (sub._st.values != "none").astype(float)
        brk = sub._st.isin(list(datasets.BREAKS)).values.astype(float)
        foul = (k == KC["foul"]).astype(float)
        score = (k == KC["score"]) | (np.r_[0, np.diff(sub.score_home.values + sub.score_away.values)] > 0)
        cols["n_stop20"][sl] = _roll(stp, SEQ)
        cols["n_brk20"][sl] = _roll(brk, SEQ)
        cols["n_foul20"][sl] = _roll(foul, SEQ)
        cols["n_score20"][sl] = _roll(score.astype(float), SEQ)
        per = per_all[sl]
        long_brk = ((k == KC["timeout"]) | (sub._st.values == "two_minute_warning")).astype(float)
        cols["brk_period"][sl] = _group_count(long_brk, per)
        cols["to_period"][sl] = _group_count((k == KC["timeout"]).astype(float), per)
        cols["fouls_period"][sl] = _group_count(foul, per)
        ftp = (sub._st.values == "whistle") & (k == KC["free_throw"])
        cols["ft_pending"][sl] = ftp.astype(float)
    for c, v in cols.items():
        e[c] = v
    # labels only where they mean something
    inplay = (e.in_break.values == 0) & (e._st.values != "game_end")
    for h in HORIZONS:
        e.loc[~inplay, f"stop{h}"] = np.nan
    e.loc[~inplay, "stop_type"] = np.nan
    e["split"] = e.night.map(night_splits(league, e.night.unique()))
    return e.drop(columns=["_st"])


KEEP = (["league", "season", "game_id", "seq", "night", "split", "clock_wall"] + datasets.FEATURES + V3_EXTRA
        + ["stop15", "stop30", "stop60", "stop_type", "start_type", "len_s", "entry_s", "big60", "big180", "hotness",
           "break_soon", "key_soon", "dead_after"])


def build3(league: str, raw_dir: Path, archive_dir: Path | None = None, max_games: int | None = None) -> pd.DataFrame:
    """Events -> v2 derive -> v3 derive -> published columns."""
    raw = lambda pat: sorted(raw_dir.glob(pat))  # noqa: E731
    sd = sportsdata
    if league == "nfl":
        # ESPN's own NFL feed (2023-24 seasons; 2025 is our archive = the scorecards, held out). The
        # nflverse feed logs no official timeouts and stamps plays differently: models trained on it
        # graded 0.64 AUC through the live ESPN path (Nov 3), so the live feed's format wins.
        e = sd.norm_espn_nfl_public([p for p in raw("espnnfl_*.parquet") if "2025" not in p.name])
    elif league in ("nba", "cbb", "wcbb"):
        e = sd.norm_basketball(league, raw(f"{league}_*.parquet"), max_games=max_games)
    elif league == "nhl":
        e = sd.norm_nhl(raw("nhl_*.parquet"))
    elif league == "mlb":
        e = sd.norm_mlb(raw("mlb_pbp_*.parquet"), raw("mlb_pitches_*.parquet"))
    elif league.startswith("espn_"):
        e = sd.norm_espn_archive(league.removeprefix("espn_"), archive_dir / league.removeprefix("espn_"))
    else:
        raise ValueError(league)
    if e.empty:
        raise ValueError(f"no events for {league}")
    e = sd.fill_wp(league, e)
    e = sd.derive(league, e)
    knots = np.nanquantile(e.hot_raw.values, np.linspace(0, 1, 101))
    e["hotness"] = (np.searchsorted(knots, e.hot_raw.values, side="right") - 1).clip(0, 100).astype("float32")
    e = derive3(league, e)
    for c in KEEP:
        if c not in e:
            e[c] = np.nan
    e = e[KEEP].copy()
    for c in KEEP:
        if c not in ("league", "season", "game_id", "seq", "night", "split") and e[c].dtype != object:
            e[c] = e[c].astype("float32")
    return e
