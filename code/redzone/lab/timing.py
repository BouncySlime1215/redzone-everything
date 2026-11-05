"""[switch timing] How late we learn about a dead ball, and what that leaves before it airs on screen.

Measured from the flight recorder (redzone/recorder.py), never from a model:

    data/recordings/<day>/plays.jsonl   {"t": first seen by us, "wall": the feed's wallclock of the
                                         play, "lag": t - wall, "league", "game", "type", "source"}

detection lag   t - wall: seconds from the play (the whistle, for a dead ball) to the app knowing it,
                per source and league. It is poll cadence plus the source's own publish delay.
on-screen lead  stream delay - detection lag: seconds between the app learning of a dead ball and
                the screen airing it. Positive = we know before it airs (a cut can be timed onto
                it); negative = it already aired when we heard (we can only use the NEXT one, or
                predict it). Stream delay per channel comes from data/redzone.db calibrations.

Rows are kept only while a game is being tracked continuously: a game's first batch (the
backlog when polling starts) and any batch after a silence longer than TRACK_GAP_S (the Mac
asleep, away, restarted) are left out, since their lag measures our absence, not the feed.

The profile (FEED_LAG_PROFILE) is what the backtest draws per-play arrival from with
feed_lag_model="measured": quantiles per league, so scorecards can be graded under the feed lag
the live app really sees instead of the fixed 8 s the scorecard assumes.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

TRACK_GAP_S = 120.0      # a batch this long after the game's previous one starts a new tracking stretch
MAX_LAG_S = 600.0        # beyond this a row is a backlog we missed, not a feed delay
TARGET_S = 3.0           # the user's goal: dead ball known within 3 s of the whistle
QS = tuple(range(0, 101, 5))   # profile quantiles
PROFILE_PATH = Path(__file__).with_name("feed_lag_profile.json")

# Play types that mean the ball is dead (whistle, timeout, review, period end). Lower-case substrings.
DEAD_WORDS = ("timeout", "foul", "free throw", "stoppage", "penalty", "end of", "end period", "review",
              "challenge", "injury", "violation", "jump ball", "two-minute", "two minute", "kick",
              "punt", "field goal", "extra point", "touchdown", "goal", "icing", "offside", "turnover",
              "out of bounds")
BASKETBALL = ("nba", "cbb", "wcbb", "wnba")


def is_dead_ball(league: str, typ: str) -> bool:
    t = (typ or "").lower()
    return any(w in t for w in DEAD_WORDS)


def quantile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile (q in 0..1) of xs; nan when empty."""
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(len(s) - 1, lo + 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


@dataclass
class LagRow:
    t: float
    wall: float
    lag: float
    league: str
    game: str
    source: str
    typ: str

    @property
    def dead(self) -> bool:
        return is_dead_ball(self.league, self.typ)


def load_rows(paths: Iterable[Path]) -> list[LagRow]:
    out = []
    for p in paths:
        try:
            lines = Path(p).read_text().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                d = json.loads(ln)
                out.append(LagRow(float(d["t"]), float(d["wall"]), float(d.get("lag", d["t"] - d["wall"])),
                                  str(d.get("league", "")), str(d.get("game", "")), str(d.get("source", "")),
                                  str(d.get("type", ""))))
            except (ValueError, KeyError, TypeError):
                continue
    return out


def tracked(rows: list[LagRow], gap_s: float = TRACK_GAP_S, max_lag_s: float = MAX_LAG_S) -> list[LagRow]:
    """Only rows from batches that follow an earlier batch of the same game and source within gap_s."""
    batches: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        batches[(r.game, r.source)].append(r.t)
    ok: dict[tuple[str, str], set[float]] = {}
    for k, ts in batches.items():
        u = sorted(set(round(t, 1) for t in ts))
        ok[k] = {b for a, b in zip(u, u[1:]) if b - a <= gap_s}
    return [r for r in rows if round(r.t, 1) in ok[(r.game, r.source)] and -5.0 <= r.lag <= max_lag_s]


def poll_gaps(rows: list[LagRow], gap_s: float = TRACK_GAP_S) -> dict[str, list[float]]:
    """Seconds between consecutive batches that brought new plays, per league (cadence floor)."""
    batches: dict[tuple[str, str, str], set[float]] = defaultdict(set)
    for r in rows:
        batches[(r.league, r.game, r.source)].add(round(r.t, 1))
    out: dict[str, list[float]] = defaultdict(list)
    for (lg, _, _), ts in batches.items():
        u = sorted(ts)
        out[lg] += [b - a for a, b in zip(u, u[1:]) if b - a <= gap_s]
    return dict(out)


def newest_lag(rows: list[LagRow]) -> dict[str, list[float]]:
    """Per league: the lag of the NEWEST play in each batch, i.e. the source's own publish delay plus
    the wait for our next poll, without the older plays the same batch carried."""
    best: dict[tuple[str, str, str, float], float] = {}
    for r in rows:
        k = (r.league, r.game, r.source, round(r.t, 1))
        best[k] = min(best.get(k, float("inf")), r.lag)
    out: dict[str, list[float]] = defaultdict(list)
    for (lg, _, _, _), v in best.items():
        out[lg].append(v)
    return dict(out)


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p10": round(quantile(xs, .1), 1), "p50": round(quantile(xs, .5), 1),
            "p90": round(quantile(xs, .9), 1), "p99": round(quantile(xs, .99), 1),
            "within_3s_pct": round(100 * sum(x <= TARGET_S for x in xs) / len(xs), 1),
            "within_10s_pct": round(100 * sum(x <= 10 for x in xs) / len(xs), 1)}


@dataclass
class LagReport:
    by_league: dict = field(default_factory=dict)        # (league) -> {source -> {all, dead_ball, newest, poll_gap}}
    days: list = field(default_factory=list)
    rows: int = 0
    kept: int = 0

    def to_json(self) -> dict:
        return {"days": self.days, "rows": self.rows, "kept": self.kept, "target_s": TARGET_S,
                "by_league": self.by_league}


def detection_report(rows: list[LagRow], days: list[str] | None = None) -> LagReport:
    kept = tracked(rows)
    rep = LagReport(days=list(days or []), rows=len(rows), kept=len(kept))
    gaps = poll_gaps(rows)
    by: dict[tuple[str, str], list[LagRow]] = defaultdict(list)
    for r in kept:
        by[(r.league, r.source)].append(r)
    for (lg, src), rs in sorted(by.items()):
        newest = newest_lag(rs).get(lg, [])
        rep.by_league.setdefault(lg, {})[src] = {
            "all": _stats([r.lag for r in rs]),
            "dead_ball": _stats([r.lag for r in rs if r.dead]),
            "newest_in_batch": _stats(newest),
            "poll_gap_s": _stats(gaps.get(lg, [])),
        }
    return rep


def lead_table(report: LagReport, delays: dict[str, float], source: str = "espn_summary") -> dict:
    """On-screen lead per (channel, league): stream delay minus dead-ball detection lag, at the
    detection lag's p10/p50/p90, and the share of dead balls known before they air."""
    out = {}
    for ch, d in sorted(delays.items()):
        for lg, srcs in sorted(report.by_league.items()):
            s = srcs.get(source, {}).get("dead_ball") or {}
            if not s.get("n"):
                continue
            out[f"{ch} / {lg}"] = {"delay_s": round(d, 1), "lead_p10_lag": round(d - s["p10"], 1),
                                   "lead_median_lag": round(d - s["p50"], 1),
                                   "lead_p90_lag": round(d - s["p90"], 1)}
    return out


def known_before_air_pct(rows: list[LagRow], delay_s: float, league: str, source: str = "espn_summary",
                         margin_s: float = 0.0) -> float:
    """Share of tracked dead balls the app knew at least margin_s before a channel with this delay aired them."""
    xs = [r.lag for r in tracked(rows) if r.league == league and r.source == source and r.dead]
    return round(100 * sum(x + margin_s <= delay_s for x in xs) / len(xs), 1) if xs else float("nan")


# ---- the profile the backtest draws from -------------------------------------------------------

PROFILE_MIN_DEAD = 200   # enough dead-ball rows to use them alone; else every play of the league


def build_profile(report_rows: list[LagRow], days: list[str], source: str = "espn_summary") -> dict:
    """Dead-ball rows (whistles, timeouts, stoppages) are what switch timing waits on. Bookkeeping
    entries ESPN back-fills minutes later (NHL hits and giveaways: p50 110-170 s) are left out:
    the backtest delivers plays in order, so one back-filled row would hold up every play after it."""
    kept = [r for r in tracked(report_rows) if r.source == source]
    dead: dict[str, list[float]] = defaultdict(list)
    every: dict[str, list[float]] = defaultdict(list)
    for r in kept:
        every[r.league].append(r.lag)
        if r.dead:
            dead[r.league].append(r.lag)
    leagues = {}
    for lg in sorted(every):
        xs, basis = (dead[lg], "dead_ball") if len(dead[lg]) >= PROFILE_MIN_DEAD else (every[lg], "all_plays")
        if len(xs) >= 50:
            leagues[lg] = {"n": len(xs), "basis": basis, "q": [round(quantile(xs, q / 100), 2) for q in QS]}
    return {"source": source, "days": days, "quantiles": list(QS), "leagues": leagues,
            "note": "detection lag t - wallclock of tracked plays (first batch / gaps excluded); "
                    "built by scripts/timing_report.py --write-profile"}


def load_profile(path: Path | None = None) -> dict:
    try:
        return json.loads((path or PROFILE_PATH).read_text())
    except (OSError, ValueError):
        return {}


def sources_in_use(cfg) -> dict[str, str]:
    """[sim lag in use] The feed each league really decides from under this config: the NHL api-web
    play-by-play when sources.nhl_primary is on, the NBA liveData cdn when sources.nba_primary is on.
    A league whose source has no profile yet keeps ESPN's (LagSampler drops it)."""
    out = {}
    if cfg.get("sources.nhl_primary", False):
        out["nhl"] = "nhl_wsc"
    if cfg.get("sources.nba_primary", False):
        out["nba"] = "nba_cdn"
    return out


# Leagues the recorder hasn't seen enough of borrow a similar one's feed.
PROFILE_FALLBACK = {"mlb": "nfl", "cfb": "nfl", "wnba": "nba", "cbb": "nba", "wcbb": "nba"}


class LagSampler:
    """Draws one play's detection lag for a league from the measured quantiles (inverse CDF,
    linear between quantiles). Seeded by the caller's rng, so scorecards stay reproducible."""

    def __init__(self, profile: dict | None = None, use: dict[str, str] | None = None):
        self.profile = profile if profile is not None else load_profile()
        # [sim lag in use] league -> source whose profile replaces ESPN's (sources_in_use(cfg)).
        self.use = {k: v for k, v in (use or {}).items()
                    if ((self.profile.get("sources") or {}).get(v) or {}).get(k)}

    def has(self, league: str) -> bool:
        return self._q(league) is not None

    def _q(self, league: str) -> list[float] | None:
        lg = (league or "").lower()
        src = self.use.get(lg)
        if src:
            return self.profile["sources"][src][lg]["q"]
        leagues = self.profile.get("leagues") or {}
        row = leagues.get(lg) or leagues.get(PROFILE_FALLBACK.get(lg, ""))
        return row["q"] if row else None

    def draw(self, league: str, rng: random.Random) -> float:
        """Up to the p95 only: beyond it are rows ESPN back-filled minutes later, and the backtest
        delivers in order, so one 10-minute draw would hold up every play behind it."""
        q = self._q(league)
        if q is None:
            raise KeyError(f"no measured feed lag for {league}")
        q = q[:-1] if len(q) > 2 else q
        u = rng.random() * (len(q) - 1)
        i = int(u)
        j = min(len(q) - 1, i + 1)
        return max(0.0, q[i] + (q[j] - q[i]) * (u - i))


# --- A2: the source race (who knows a score or a whistle first, and by how much) -----------------

ESPN_SOURCES = ("espn_summary", "espn_scoreboard", "espn_fastcast")


def race_report(paths: Iterable[Path], league_of: dict[str, str] | None = None) -> dict:
    """Per league and label kind ("score" / "stop"), per source: how often it was first, and how
    many seconds it beat the fastest ESPN copy (positive = ahead of ESPN).

    Rows are deduped by (game, label), keeping the earliest (a restart re-reports the open
    labels). Rows written before the recorder waited for followers carry no "others", so they
    count toward first% but not the gain. league_of fills rows that predate the league field."""
    best: dict[tuple[str, str], dict] = {}
    for p in paths:
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("label") in (None, "score 0-0"):
                    continue
                k = (str(r.get("game")), r["label"])
                if k not in best or r["t"] < best[k]["t"]:
                    best[k] = r
    out: dict[str, dict[str, dict]] = {}
    for (gid, label), r in best.items():
        lg = r.get("league") or (league_of or {}).get(gid) or "?"
        kind = label.split(" ", 1)[0]
        lags = {r["first"]: 0.0, **{s: float(v) for s, v in (r.get("others") or {}).items()}}
        espn = [lags[s] for s in ESPN_SOURCES if s in lags]
        bucket = out.setdefault(lg, {}).setdefault(kind, {"n": 0, "with_followers": 0, "sources": {}})
        bucket["n"] += 1
        bucket["with_followers"] += bool(r.get("others"))
        for s, lag in lags.items():
            d = bucket["sources"].setdefault(s, {"seen": 0, "first": 0, "gain": []})
            d["seen"] += 1
            d["first"] += s == r["first"]
            if espn and s not in ESPN_SOURCES and r.get("others"):
                d["gain"].append(round(min(espn) - lag, 2))
    for kinds in out.values():
        for b in kinds.values():
            for d in b["sources"].values():
                d["first_pct"] = round(100.0 * d["first"] / b["n"], 1)
                d["gain_s"] = _stats(d.pop("gain"))
    return out


def fastest_feeds(report: LagReport, race: dict | None = None, min_n: int = 20) -> dict:
    """[sync 5] The fastest feed per league: lowest dead-ball detection lag p50 over sources with
    at least min_n dead balls, and (when race data exists) the source that reported "stop" labels
    first most often. Read-only advice for sources.*_primary flags; nothing switches by itself."""
    out = {}
    for lg, srcs in report.by_league.items():
        ok = {s: d["dead_ball"] for s, d in srcs.items() if d["dead_ball"].get("n", 0) >= min_n}
        row = {}
        if ok:
            s = min(ok, key=lambda k: ok[k]["p50"])
            row["by_lag"] = {"source": s, "p50": ok[s]["p50"], "p90": ok[s]["p90"], "n": ok[s]["n"]}
        stop = ((race or {}).get(lg) or {}).get("stop")
        if stop and stop["n"] >= min_n:
            s, d = max(stop["sources"].items(), key=lambda kv: kv[1]["first"])
            row["by_race"] = {"source": s, "first_pct": d["first_pct"], "n": stop["n"]}
        if row:
            out[lg] = row
    return out
