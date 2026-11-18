"""Arrival-lag numbers from the feed-arrival logs, and the replay-lab lag profile rebuilt from them (no network).

Reads data/feedlag/*.jsonl (written by scripts/feedlag_logger.py, row format in redzone/lab/feedlag.py) and reports,
per source and per row kind, how long after a row's own stamp we first saw it: p10/p50/p90/p95/p99/max, and the share
that arrived within the lag the show assumes (8 s). Each row was published somewhere in (prev, t], so there are two
estimates: `obs` = t - stamp (an upper bound, up to one poll gap too high) and `mid` = the midpoint of the window
minus the stamp (unbiased if rows land uniformly inside the gap). The report uses `mid`; the profile can use either.
Rows whose window is wider than `max_gap` (the logger asleep, a host in backoff) are left out, and counted.

`build_replay_profile` returns a dict in the schema of redzone/lab/feed_lag_profile.json (the file
`timing.LagSampler` draws from), starting from that file and replacing only the entries the logs cover. It never
writes the committed file: scripts/feedlag_report.py writes a copy under data/feedlag/, and swapping it in is a
reviewed change.

Why: C-02 (v2 WORKPLAN, tracks/C-picture-audio-timing.md C2): the bar is 5+ nights, 10+ NBA games, both sources, tails by row kind, then replays rebuilt with real arrival lags; this computes exactly that.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from redzone.lab import feedlag, timing

ASSUMED_LAG = 8.0             # the lag the show and the scorecards assume today (planner constant)
WIDE_GAP = 30.0               # a window wider than this says little about the lag (3 poll gaps)
EARLIEST_LAG = -10.0          # below this the Mac's clock or the feed's stamp is off; not a lag
LATEST_LAG = 1800.0           # beyond half an hour is a feed fix-up, not an arrival
BAR_NIGHTS = 5                # C2 bar
BAR_GAMES = 10
MIN_GAME_ROWS = 50            # usable rows before a game counts toward the bar
PROFILE_MIN = 50              # usable rows before a league/source replaces the committed profile entry
PROFILE_DEAD_MIN = 200        # dead-ball rows needed to build the profile on dead balls alone (as timing.build_profile)
PROFILE_LEAGUES = ("nba", "cbb")


@dataclass(frozen=True)
class Arrival:
    t: float
    prev: float | None
    src: str
    lg: str
    game: str
    row: str
    kind: str
    typ: str
    stop: str
    stamp: float | None
    night: str

    @property
    def lag_obs(self) -> float:
        return self.t - self.stamp

    @property
    def lag_mid(self) -> float:
        return (self.t + self.prev) / 2.0 - self.stamp

    @property
    def dead(self) -> bool:
        return self.stop not in ("none", "unknown", "")


def load_arrivals(paths: Iterable[Path]) -> list[Arrival]:
    """Every `row` event in the given day files. Unreadable files and lines are skipped, never fatal."""
    out = []
    for p in paths:
        try:
            lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        night = Path(p).stem
        for ln in lines:
            try:
                d = json.loads(ln)
                if d.get("ev") != "row":
                    continue
                out.append(Arrival(float(d["t"]), None if d.get("prev") is None else float(d["prev"]), str(d["src"]),
                                   str(d.get("lg", "")), str(d["game"]), str(d["row"]), str(d.get("kind", "other")),
                                   str(d.get("typ", "")), str(d.get("stop", "")),
                                   None if d.get("stamp") is None else float(d["stamp"]), night))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return out


def day_files(root: Path, nights: Iterable[str] | None = None) -> list[Path]:
    want = set(nights or ())
    return [p for p in sorted(Path(root).glob("????-??-??.jsonl")) if not want or p.stem in want]


def usable(rows: list[Arrival], max_gap: float = WIDE_GAP) -> tuple[list[Arrival], dict]:
    """Rows with a stamp and a narrow, sane window, and a count of why the others were left out."""
    keep, why = [], defaultdict(int)
    for a in rows:
        if a.stamp is None:
            why["no_stamp"] += 1
        elif a.prev is None:
            why["no_window"] += 1
        elif not 0 < a.t - a.prev <= max_gap:
            why["wide_window"] += 1
        elif not EARLIEST_LAG <= a.lag_mid <= LATEST_LAG:
            why["implausible_lag"] += 1
        else:
            keep.append(a)
    return keep, dict(why)


def stats(xs: list[float], assumed: float = ASSUMED_LAG) -> dict:
    if not xs:
        return {"n": 0}
    q = lambda f: round(timing.quantile(xs, f), 1)       # noqa: E731
    return {"n": len(xs), "p10": q(.10), "p50": q(.50), "p90": q(.90), "p95": q(.95), "p99": q(.99),
            "max": round(max(xs), 1), "within_assumed_pct": round(100.0 * sum(x <= assumed for x in xs) / len(xs), 1)}


def report(rows: list[Arrival], max_gap: float = WIDE_GAP, assumed: float = ASSUMED_LAG) -> dict:
    """Lag quantiles per source and per row kind, the poll-gap context, and the C2 coverage check."""
    ok, why = usable(rows, max_gap)
    out: dict = {"assumed_lag": assumed, "max_gap": max_gap, "rows": len(rows), "usable": len(ok), "left_out": why,
                 "sources": {}}
    for src in feedlag.FEED_SOURCES:
        rs = [a for a in ok if a.src == src]
        if not rs:
            continue
        kinds = {}
        for kind in feedlag.ROW_KINDS:
            ks = [a for a in rs if a.kind == kind]
            if ks:
                kinds[kind] = {**stats([a.lag_mid for a in ks], assumed),
                               "obs_p50": round(timing.quantile([a.lag_obs for a in ks], .5), 1),
                               "obs_p90": round(timing.quantile([a.lag_obs for a in ks], .9), 1)}
        out["sources"][src] = {
            "all": stats([a.lag_mid for a in rs], assumed),
            "all_obs": stats([a.lag_obs for a in rs], assumed),
            "dead_ball": stats([a.lag_mid for a in rs if a.dead], assumed),
            "poll_window": stats([a.t - a.prev for a in rs], assumed),
            "by_kind": kinds,
        }
    out["coverage"] = coverage(ok)
    return out


def coverage(ok: list[Arrival]) -> dict:
    """The C2 bar: 5+ nights, 10+ NBA games, both sources, tails by row kind."""
    per_game: dict[tuple[str, str, str], int] = defaultdict(int)         # (night, src, game) -> usable rows
    for a in ok:
        if a.lg == "nba":
            per_game[(a.night, a.src, a.game)] += 1
    full = {k for k, n in per_game.items() if n >= MIN_GAME_ROWS}
    per_source = {src: len({(n, g) for n, s, g in full if s == src}) for src in feedlag.FEED_SOURCES}
    both = len({(n, g) for n, s, g in full if s == feedlag.SRC_ESPN} & {(n, g) for n, s, g in full if s == feedlag.SRC_CDN})
    nights = sorted({a.night for a in ok})
    bar = {f"nights>={BAR_NIGHTS}": len(nights) >= BAR_NIGHTS,
           f"nba_games>={BAR_GAMES}": min(per_source.values(), default=0) >= BAR_GAMES,
           "both_sources": all(per_source[s] > 0 for s in feedlag.FEED_SOURCES)}
    return {"nights": nights, "nba_games_by_source": per_source, "nba_games_with_both": both,
            "min_rows_per_game": MIN_GAME_ROWS, "bar": bar, "bar_met": all(bar.values())}


def format_report(rep: dict) -> str:
    lines = [f"{rep['usable']}/{rep['rows']} rows usable (window <= {rep['max_gap']:g} s); left out: {rep['left_out'] or 'none'}",
             f"lag = midpoint of (previous poll sent, first seen) minus the row's own stamp; the show assumes {rep['assumed_lag']:g} s"]
    for src, d in rep["sources"].items():
        a, o = d["all"], d["all_obs"]
        lines.append("")
        lines.append(f"== {src}: {a['n']} rows, p50 {a['p50']} s, p90 {a['p90']} s, p99 {a['p99']} s; "
                     f"{a['within_assumed_pct']}% within {rep['assumed_lag']:g} s (upper bound p50 {o['p50']} s)")
        db = d["dead_ball"]
        if db.get("n"):
            lines.append(f"   dead-ball rows: {db['n']}, p50 {db['p50']} s, p90 {db['p90']} s, "
                         f"{db['within_assumed_pct']}% within {rep['assumed_lag']:g} s")
        lines.append(f"   {'kind':13} {'n':>5} {'p10':>6} {'p50':>6} {'p90':>6} {'p95':>6} {'p99':>6} {'max':>7} {'<=8s%':>6}")
        for kind, s in sorted(d["by_kind"].items(), key=lambda kv: -kv[1]["n"]):
            lines.append(f"   {kind:13} {s['n']:>5} {s['p10']:>6} {s['p50']:>6} {s['p90']:>6} {s['p95']:>6} "
                         f"{s['p99']:>6} {s['max']:>7} {s['within_assumed_pct']:>6}")
    c = rep["coverage"]
    lines += ["", f"coverage: nights {len(c['nights'])} {c['nights']}, NBA games with >= {c['min_rows_per_game']} usable rows "
                  f"{c['nba_games_by_source']}, games on both sources {c['nba_games_with_both']}",
              "C2 bar (" + ", ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in c["bar"].items()) + ") -> "
              + ("MET" if c["bar_met"] else "not met yet")]
    return "\n".join(lines)


# --- the replay-lab input --------------------------------------------------------------------------------------

def _quantiles(xs: list[float]) -> list[float]:
    return [round(timing.quantile(xs, q / 100), 2) for q in timing.QS]


def build_replay_profile(rows: list[Arrival], base: dict | None = None, basis: str = "mid", max_gap: float = WIDE_GAP,
                         min_rows: int = PROFILE_MIN) -> dict:
    """The timing profile (schema of feed_lag_profile.json) with real arrival lags where the logs have enough rows.

    ESPN's summary replaces `leagues[nba|cbb]`; the NBA liveData feed replaces `sources.nba_cdn.nba`. Everything the
    logs do not cover stays exactly as in `base` (default: the committed profile). Lags are the `mid` estimate unless
    basis="obs" (the upper bound). Each replaced entry names the nights it came from."""
    if basis not in ("mid", "obs"):
        raise ValueError("basis must be 'mid' or 'obs'")
    prof = copy.deepcopy(base if base is not None else timing.load_profile())
    prof.setdefault("quantiles", list(timing.QS))
    prof.setdefault("leagues", {})
    prof.setdefault("sources", {})
    ok, _ = usable(rows, max_gap)
    lag_of = (lambda a: a.lag_mid) if basis == "mid" else (lambda a: a.lag_obs)
    replaced = []

    def entry(rs: list[Arrival]) -> dict | None:
        if len(rs) < min_rows:
            return None
        dead = [lag_of(a) for a in rs if a.dead]
        xs, how = (dead, "dead_ball") if len(dead) >= PROFILE_DEAD_MIN else ([lag_of(a) for a in rs], "all_plays")
        return {"n": len(xs), "basis": how, "q": _quantiles(xs), "days": sorted({a.night for a in rs}),
                "estimate": basis, "from": "feedlag"}

    for lg in PROFILE_LEAGUES:
        e = entry([a for a in ok if a.src == feedlag.SRC_ESPN and a.lg == lg])
        if e:
            prof["leagues"][lg] = e
            replaced.append(f"espn_summary/{lg}")
    e = entry([a for a in ok if a.src == feedlag.SRC_CDN and a.lg == "nba"])
    if e:
        prof["sources"].setdefault("nba_cdn", {})["nba"] = e
        replaced.append("nba_cdn/nba")
    prof["feedlag"] = {"replaced": replaced, "estimate": basis, "max_gap": max_gap,
                       "rows_used": len(ok), "nights": sorted({a.night for a in ok}),
                       "note": "entries marked from=feedlag are arrival lags from the nightly feed-arrival log "
                               "(redzone/lab/feedlag.py); the rest is the committed profile. Not the committed file."}
    return prof


def write_profile(prof: dict, path: Path) -> Path:
    """Write a rebuilt profile. The committed profile is never overwritten from here."""
    path = Path(path)
    if path.resolve() == timing.PROFILE_PATH.resolve():
        raise ValueError(f"refusing to overwrite {timing.PROFILE_PATH}: write a copy and swap it in a reviewed change")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prof, indent=1) + "\n", encoding="utf-8")
    return path
