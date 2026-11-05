"""BUZZ core for Data Crew B: Wikipedia pageviews, Wikipedia edit rate and GDELT news volume/tone.

Shared by scripts/context/{wiki_pageviews,wiki_edits,gdelt_buzz,run_buzz}.py. Pullers never store raw files or
text: each source is reduced on the fly to SIGNALS (numbers per team per time bin) plus COVERAGE (which bins we
actually looked at), kept under data/context/buzz/. Facts (redzone/context/tokens.py rows) are then derived
from signals with strict as-of rules:

* every bin is usable only from its END (bin_start + bin_s); a window [a, b) sums bins with start >= a and
  end <= b, and b is always <= asof_utc;
* a z-score compares the window's rate (value per covered bin) with the same-length windows of the 28 days
  strictly before a (only covered bins count; uncovered bins are unknown, never zero).

Spec + cadence + sizes: docs/datasources/context-buzz.md.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from redzone.context.tokens import PERSONAL_USE, Fact

log = logging.getLogger("buzz")

UA = "redzone-research/0.1 (personal project; github.com/BouncySlime1215/redzone-everything)"
ROOT = Path(__file__).resolve().parents[2]
SEED_CSV = Path(__file__).with_name("teams_seed.csv")
TEAMS_CSV = ROOT / "data/context/teams.csv"
BUZZ_DIR = ROOT / "data/context/buzz"
LEAGUES = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")

SRC_PV, SRC_ED, SRC_GD = "wikimedia-pageviews", "wikimedia-edits", "gdelt-gkg"
LICENSE = {
    SRC_PV: f"Wikimedia pageview data CC0; {PERSONAL_USE}",
    SRC_ED: f"Wikimedia EventStreams, CC0 metadata; {PERSONAL_USE}",
    SRC_GD: f"GDELT: free and open, cite The GDELT Project; {PERSONAL_USE}",
}
DAY, HOUR, Q15 = 86400, 3600, 900
BASELINE_DAYS = 28
PRE_LIVE_S = 6 * HOUR                     # live facts start 6 h before kickoff (the hype ramp)
GAME_S = {"nfl": 4 * HOUR, "cfb": int(4.5 * HOUR), "nba": 3 * HOUR, "cbb": int(2.5 * HOUR), "nhl": 3 * HOUR,
          "mlb": 4 * HOUR}
Z_CLIP = 10.0


# ---------------------------------------------------------------- time helpers
def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(t)))


def parse_utc(s) -> int:
    """'2024-01-05T00:30:00Z' / '2024-01-05 00:30' / pandas Timestamp / epoch -> epoch seconds (UTC)."""
    import pandas as pd
    if isinstance(s, (int, float, np.integer, np.floating)):
        return int(s)
    ts = pd.Timestamp(s)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return int(ts.timestamp())


def floor_to(t: int, step: int) -> int:
    return int(t) // step * step


# ---------------------------------------------------------------- HTTP (UA, throttle, backoff, cache)
class Http:
    """requests.Session with our User-Agent, a per-host rate limit, 429/5xx backoff and an optional disk cache.

    Only small API responses are cached (JSON / text); big dump files are streamed and never written to disk."""

    def __init__(self, rps: float = 5.0, cache_dir: Path | None = None, timeout: float = 60, retries: int = 6):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Api-User-Agent": UA, "Accept-Encoding": "gzip"})
        self.gap, self.timeout, self.retries = 1.0 / rps, timeout, retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._last: dict[str, float] = {}

    def _throttle(self, url: str):
        host = url.split("/")[2]
        wait = self._last.get(host, 0) + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, stream: bool = False, headers: dict | None = None):
        """Response, or None on 404. Retries 429/5xx/network errors with Retry-After or exponential backoff."""
        import requests
        for i in range(self.retries):
            self._throttle(url)
            try:
                r = self.s.get(url, params=params, stream=stream, timeout=self.timeout, headers=headers)
            except requests.RequestException as e:
                log.warning("GET %s failed (%s), retry %d", url, e, i + 1)
                time.sleep(min(120, 2 ** (i + 1)))
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After", "")
                delay = float(ra) if ra.isdigit() else min(300, 5 * 2 ** i)
                log.warning("GET %s -> %d, backing off %.0fs", url, r.status_code, delay)
                r.close()
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"giving up on {url} after {self.retries} tries")

    def get_json(self, url: str, params: dict | None = None, cache: bool = True):
        key = None
        if cache and self.cache_dir:
            key = self.cache_dir / (hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
                                    + ".json")
            if key.exists():
                return json.loads(key.read_text())
        r = self.get(url, params)
        data = None if r is None else r.json()
        if key is not None and data is not None:
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text(json.dumps(data))
        return data


# ---------------------------------------------------------------- teams
TEAM_COLS = ["league", "abbr", "name", "enwiki", "former_titles", "former_abbrs", "aliases", "active",
             "qid", "venue_qid", "venue_lat", "venue_lon"]


def title_key(t: str) -> str:
    """Wikipedia title as it appears in pageview dumps / REST URLs: underscores, first letter upper."""
    t = unquote(t).strip().replace(" ", "_")
    return t[:1].upper() + t[1:] if t else t


def _split(v) -> list[str]:
    return [x.strip() for x in str(v or "").split("|") if x.strip() and str(x).strip().lower() != "nan"]


def load_teams(path: Path | None = None, leagues=LEAGUES) -> list[dict]:
    """teams.csv (Wikidata step) if present, else the hand-checked seed (pro leagues only). Seed rows fill any
    pro team missing from teams.csv."""
    rows: list[dict] = []
    p = Path(path) if path else TEAMS_CSV
    seen = set()
    for src in ([p] if p.exists() else []) + [SEED_CSV]:
        with open(src, newline="") as f:
            for r in csv.DictReader(f):
                k = (r["league"], r["abbr"])
                if k in seen or r["league"] not in leagues:
                    continue
                seen.add(k)
                rows.append({c: r.get(c, "") for c in TEAM_COLS})
    return rows


class TeamIndex:
    """Lookups from titles (pageviews / edits), names (GDELT) and schedule strings to (league, abbr)."""

    def __init__(self, teams: list[dict]):
        self.teams = teams
        self.by_title: dict[str, list[tuple[str, str]]] = {}
        self.by_alias: dict[str, list[tuple[str, str]]] = {}
        self.main_title: dict[tuple[str, str], str] = {}
        self.titles_of: dict[tuple[str, str], list[str]] = {}
        self._abbr: dict[tuple[str, str], str] = {}
        self._name: dict[tuple[str, str], str] = {}
        for t in teams:
            key = (t["league"], t["abbr"])
            titles = [title_key(t["enwiki"])] + [title_key(x) for x in _split(t["former_titles"])]
            self.main_title[key] = titles[0]
            self.titles_of[key] = titles
            for ti in titles:
                self.by_title.setdefault(ti, []).append(key)
            names = {t["name"]} | set(_split(t["aliases"])) | {x.replace("_", " ") for x in titles}
            for n in names:
                n = norm_name(n)
                if len(n.split()) >= 2:            # one-word names ("Giants", "Kings") are ambiguous
                    lst = self.by_alias.setdefault(n, [])
                    if key not in lst:
                        lst.append(key)
                    self._name[(t["league"], n)] = t["abbr"]
            self._abbr[(t["league"], t["abbr"].upper())] = t["abbr"]
            for a in _split(t["former_abbrs"]):
                self._abbr[(t["league"], a.upper())] = t["abbr"]
        self.max_alias_words = max((len(a.split()) for a in self.by_alias), default=1)

    def resolve(self, league: str, s: str) -> str | None:
        """Schedule team string (abbr, former abbr, full name, or college school name) -> our abbr."""
        s = str(s or "").strip()
        if not s:
            return None
        hit = self._abbr.get((league, s.upper())) or self._name.get((league, norm_name(s)))
        if hit:
            return hit
        if league in ("cfb", "cbb"):              # "North Carolina" -> "North Carolina Tar Heels football"
            n = norm_name(s) + " "
            cands = [t["abbr"] for t in self.teams if t["league"] == league
                     and norm_name(t["enwiki"].replace("_", " ")).startswith(n)]
            if len(cands) == 1:
                return cands[0]
        return None

    def match_names(self, names) -> set[tuple[str, str]]:
        out = set()
        for n in names:
            out.update(self.by_alias.get(norm_name(n), ()))
        return out

    def match_text(self, text: str) -> set[tuple[str, str]]:
        """Teams whose multi-word alias appears as a whole-word n-gram of `text` (used for page titles only)."""
        w = norm_name(text).split()
        out = set()
        for n in range(2, self.max_alias_words + 1):
            for i in range(len(w) - n + 1):
                out.update(self.by_alias.get(" ".join(w[i:i + n]), ()))
        return out


_NONWORD = re.compile(r"[^0-9a-z&'.]+")


def norm_name(s: str) -> str:
    s = str(s).lower().replace("’", "'").replace("_", " ")
    s = re.sub(r"\s*\((?:american football|baseball|basketball|ice hockey|nfl|nba|nhl|mlb)\)", "", s)
    return " ".join(_NONWORD.sub(" ", s).split())


# ---------------------------------------------------------------- signal store
SIG_COLS = ["source", "metric", "league", "team", "ts", "bin_s", "value"]
COV_COLS = ["source", "team", "ts", "bin_s"]       # team "*" = whole source (one GDELT file, one dump hour)


def ckey(league: str, abbr: str) -> str:
    """Coverage key of one team (abbrs repeat across leagues: PHI is four teams)."""
    return f"{league}:{abbr}"


class Store:
    """Signals and coverage as month-partitioned parquet: <root>/{signals,coverage}/<source>/<YYYY-MM>.parquet."""

    def __init__(self, root: Path = BUZZ_DIR):
        self.root = Path(root)
        self._sig: list[dict] = []
        self._cov: list[dict] = []

    def add(self, source: str, metric: str, league: str, team: str, ts: int, bin_s: int, value: float):
        self._sig.append({"source": source, "metric": metric, "league": league, "team": team, "ts": int(ts),
                          "bin_s": int(bin_s), "value": float(value)})

    def cover(self, source: str, ts: int, bin_s: int, team: str = "*"):
        self._cov.append({"source": source, "team": team, "ts": int(ts), "bin_s": int(bin_s)})

    def flush(self):
        import pandas as pd
        for kind, buf, cols, keys in (("signals", self._sig, SIG_COLS, SIG_COLS[:-1]),
                                      ("coverage", self._cov, COV_COLS, COV_COLS)):
            if not buf:
                continue
            df = pd.DataFrame(buf, columns=cols)
            df["month"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%Y-%m")
            for (src, month), g in df.groupby(["source", "month"]):
                p = self.root / kind / src / f"{month}.parquet"
                p.parent.mkdir(parents=True, exist_ok=True)
                g = g[cols]
                if p.exists():
                    g = pd.concat([pd.read_parquet(p), g], ignore_index=True)
                g.drop_duplicates(keys, keep="last").sort_values(keys).to_parquet(p, index=False)
            buf.clear()

    def _load(self, kind: str, cols: list[str], sources, t0: int, t1: int):
        import pandas as pd
        parts = []
        for src in sources:
            d = self.root / kind / src
            for p in sorted(d.glob("*.parquet")) if d.exists() else []:
                m = pd.Timestamp(p.stem + "-01", tz="UTC").timestamp()
                if m > t1 or m + 32 * DAY < t0:
                    continue
                parts.append(pd.read_parquet(p))
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
        return df[(df["ts"] >= t0) & (df["ts"] < t1)] if len(df) else df

    def signals(self, sources, t0: int, t1: int):
        return self._load("signals", SIG_COLS, sources, t0, t1)

    def coverage(self, sources, t0: int, t1: int):
        return self._load("coverage", COV_COLS, sources, t0, t1)

    def covered_ts(self, source: str, t0: int, t1: int, team: str = "*") -> set[int]:
        """Bin starts already covered for `team` ("*" or ckey(league, abbr)) in [t0, t1)."""
        self.flush()
        c = self.coverage([source], t0, t1)
        return set(c.loc[c["team"] == team, "ts"].astype(int)) if len(c) else set()


# ---------------------------------------------------------------- leak-safe windows and z-scores
class Cum:
    """Prefix sums over sorted bin starts, so a window sum is two searchsorted calls."""

    def __init__(self, ts, val=None):
        order = np.argsort(np.asarray(ts, dtype=np.int64), kind="stable")
        self.ts = np.asarray(ts, dtype=np.int64)[order]
        v = np.ones(len(self.ts)) if val is None else np.asarray(val, dtype=float)[order]
        self.cum = np.concatenate([[0.0], np.cumsum(v)])

    def sums(self, bounds: np.ndarray, bin_s: int) -> np.ndarray:
        """Sum of bins with start >= bounds[i] and end <= bounds[i+1] (i.e. fully inside the window)."""
        lo = np.searchsorted(self.ts, bounds[:-1], side="left")
        hi = np.searchsorted(self.ts, bounds[1:] - bin_s, side="right")
        return np.where(hi > lo, self.cum[np.maximum(hi, lo)] - self.cum[lo], 0.0)


EMPTY = Cum([])


def zscore(x: float, base: np.ndarray, min_n: int) -> float:
    base = np.asarray(base, dtype=float)
    base = base[np.isfinite(base)]
    if not np.isfinite(x) or len(base) < max(2, min_n):
        return float("nan")
    mu, sd = float(base.mean()), float(base.std(ddof=1))
    sd = max(sd, 1e-3 + 0.05 * abs(mu))        # floor: a flat baseline must not explode the z
    return round(float(np.clip((x - mu) / sd, -Z_CLIP, Z_CLIP)), 3)


def window_rates(sig: Cum, cov: Cum, bin_s: int, a: int, b: int, days: int = BASELINE_DAYS):
    """Rate (value per covered bin) of target window [a, b) and of the K same-length windows before a.

    Returns (x, base_rates). Every bin used ends <= b; the baseline ends <= a."""
    L = b - a
    K = max(1, days * DAY // L)
    bounds = np.concatenate([a - L * np.arange(K, -1, -1), [b]]).astype(np.int64)
    s, c = sig.sums(bounds, bin_s), cov.sums(bounds, bin_s)
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = np.where(c > 0, s / c, np.nan)
    return float(rate[-1]), rate[:-1]


def window_z(sig: Cum, cov: Cum, bin_s: int, a: int, b: int, days: int = BASELINE_DAYS) -> float:
    x, base = window_rates(sig, cov, bin_s, a, b, days)
    K = len(base)
    return zscore(x, base, min_n=max(5, K // 8))


# ---------------------------------------------------------------- facts from signals
@dataclass
class Signals:
    """Signals + coverage for a time range, indexed per (source, metric, league, team)."""
    sig: dict
    cov: dict

    @classmethod
    def from_store(cls, store: Store, t0: int, t1: int, sources=(SRC_PV, SRC_ED, SRC_GD)):
        store.flush()
        s, c = store.signals(sources, t0, t1), store.coverage(sources, t0, t1)
        return cls.from_frames(s, c)

    @classmethod
    def from_frames(cls, s, c):
        sig = {k: Cum(g["ts"].to_numpy(), g["value"].to_numpy())
               for k, g in s.groupby(["source", "metric", "league", "team", "bin_s"])} if len(s) else {}
        cov = {k: Cum(g["ts"].to_numpy()) for k, g in c.groupby(["source", "team", "bin_s"])} if len(c) else {}
        return cls(sig, cov)

    def _cov(self, source, league, team, bin_s) -> Cum:
        return self.cov.get((source, ckey(league, team), bin_s)) or self.cov.get((source, "*", bin_s), EMPTY)

    def get(self, source, metric, league, team, bin_s) -> tuple[Cum, Cum]:
        return self.sig.get((source, metric, league, team, bin_s), EMPTY), self._cov(source, league, team, bin_s)

    def covered_ends(self, source, league, team, bin_s, t0, t1) -> np.ndarray:
        cov = self._cov(source, league, team, bin_s)
        ends = cov.ts + bin_s
        return np.unique(ends[(ends > t0) & (ends <= t1)])


def _fact(game_id, team, asof, field, value, source) -> Fact | None:
    if value is None or not np.isfinite(value):
        return None
    return Fact(game_id, team, iso(asof), field, float(value), "new", source, LICENSE[source])


def pregame_facts(games: list[dict], S: Signals) -> list[Fact]:
    """Per game and team, asof = kickoff - 1 s.

    pageviews_z: the last full UTC day before game day vs the 28 days before it.
    edit_rate_z / news_volume_z: the 24 h ending at the last 15-min boundary <= asof, vs the 28 days before.
    news_tone: mean GDELT tone of the articles in that same 24 h window."""
    out = []
    for g in games:
        asof = int(g["start_utc"]) - 1
        d1 = floor_to(asof, DAY)
        q1 = floor_to(asof, Q15)
        for team in (g["home"], g["away"]):
            lg = g["league"]
            sig, cov = S.get(SRC_PV, "views", lg, team, DAY)
            out.append(_fact(g["game_id"], team, asof, "pageviews_z", window_z(sig, cov, DAY, d1 - DAY, d1), SRC_PV))
            sig, cov = S.get(SRC_ED, "edits", lg, team, Q15)
            out.append(_fact(g["game_id"], team, asof, "edit_rate_z", window_z(sig, cov, Q15, q1 - DAY, q1), SRC_ED))
            sig, cov = S.get(SRC_GD, "articles", lg, team, Q15)
            out.append(_fact(g["game_id"], team, asof, "news_volume_z",
                             window_z(sig, cov, Q15, q1 - DAY, q1), SRC_GD))
            tone, _ = S.get(SRC_GD, "tone_sum", lg, team, Q15)
            b = np.array([q1 - DAY, q1])
            n, ts = sig.sums(b, Q15)[0], tone.sums(b, Q15)[0]
            out.append(_fact(g["game_id"], team, asof, "news_tone", round(ts / n, 3) if n > 0 else None, SRC_GD))
    return [f for f in out if f is not None]


def live_facts(games: list[dict], S: Signals, t0: int | None = None, t1: int | None = None) -> list[Fact]:
    """In-window facts from PRE_LIVE_S before kickoff to the league's game length after it, one per covered bin,
    asof = bin end. Optionally limited to bins ending in (t0, t1].

    pageviews_live_z: that hour's views x 24 vs the 28 full UTC days before the hour's day (daily REST).
    edit_rate_live_z / news_volume_live_z: that 15-min bin vs the 15-min bins of the previous 28 days.
    news_tone_live: mean tone of that bin's articles (only when it has any)."""
    out = []
    for g in games:
        lg, k = g["league"], int(g["start_utc"])
        w0, w1 = k - PRE_LIVE_S, k + GAME_S.get(lg, 4 * HOUR)
        if t0 is not None:
            w0 = max(w0, t0)
        if t1 is not None:
            w1 = min(w1, t1)
        if w1 <= w0:
            continue
        for team in (g["home"], g["away"]):
            hsig, _ = S.get(SRC_PV, "views_h", lg, team, HOUR)
            dsig, dcov = S.get(SRC_PV, "views", lg, team, DAY)
            hcov = S.get(SRC_PV, "views_h", lg, team, HOUR)[1]
            for e in S.covered_ends(SRC_PV, lg, "*", HOUR, w0, w1):
                e = int(e)
                x_rate, _ = window_rates(hsig, hcov, HOUR, e - HOUR, e, days=1)
                d = floor_to(e - HOUR, DAY)
                _, base = window_rates(dsig, dcov, DAY, d, d + DAY)   # base = the 28 full days before d; the
                # target (day d itself, still running) is discarded
                out.append(_fact(g["game_id"], team, e, "pageviews_live_z",
                                 zscore(x_rate * 24, base, min_n=7), SRC_PV))
            sig, cov = S.get(SRC_ED, "edits", lg, team, Q15)
            for e in S.covered_ends(SRC_ED, lg, team, Q15, w0, w1):
                e = int(e)
                out.append(_fact(g["game_id"], team, e, "edit_rate_live_z", window_z(sig, cov, Q15, e - Q15, e),
                                 SRC_ED))
            sig, cov = S.get(SRC_GD, "articles", lg, team, Q15)
            tone, _ = S.get(SRC_GD, "tone_sum", lg, team, Q15)
            for e in S.covered_ends(SRC_GD, lg, "*", Q15, w0, w1):
                e = int(e)
                out.append(_fact(g["game_id"], team, e, "news_volume_live_z", window_z(sig, cov, Q15, e - Q15, e),
                                 SRC_GD))
                b = np.array([e - Q15, e])
                n = sig.sums(b, Q15)[0]
                if n > 0:
                    out.append(_fact(g["game_id"], team, e, "news_tone_live", round(tone.sums(b, Q15)[0] / n, 3),
                                     SRC_GD))
    return [f for f in out if f is not None]


# ---------------------------------------------------------------- schedules and shards
def season_of(league: str, t: int) -> int:
    y, m = time.gmtime(t).tm_year, time.gmtime(t).tm_mon
    if league == "mlb":
        return y
    return y if m >= 7 else y - 1          # NFL/CFB/NBA/CBB/NHL seasons are named by their start year


def normalize_schedule(rows, idx: TeamIndex) -> list[dict]:
    """rows with game_id, league, home, away, start_utc (+ optional season) -> resolved dicts; unresolved
    teams are logged and dropped."""
    out, bad = [], set()
    for r in rows:
        lg = str(r["league"]).lower()
        h, a = idx.resolve(lg, r["home"]), idx.resolve(lg, r["away"])
        if not h or not a:
            bad.add((lg, r["home"] if not h else r["away"]))
            continue
        t = parse_utc(r["start_utc"])
        season = r.get("season")
        season = int(season) if season not in (None, "") and str(season) != "nan" else season_of(lg, t)
        out.append({"game_id": str(r["game_id"]), "league": lg, "home": h, "away": a, "start_utc": t,
                    "season": season})
    if bad:
        log.warning("schedule: %d unresolved team names, e.g. %s", len(bad), sorted(bad)[:8])
    return out


SHARD_KEYS = ["game_id", "team", "asof_utc", "field", "source"]


def write_shards(facts: list[Fact], games: list[dict], out_dir: Path) -> dict:
    """Merge facts into <out_dir>/<league>_<season>.parquet (one per league-season), dedupe on SHARD_KEYS."""
    import pandas as pd

    from redzone.context.tokens import COLUMNS, ledger_counts, to_frame
    if not facts:
        return {}
    meta = {g["game_id"]: (g["league"], g["season"]) for g in games}
    df = to_frame(facts)
    df["_shard"] = [f"{meta[g][0]}_{meta[g][1]}" for g in df["game_id"]]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for shard, part in df.groupby("_shard"):
        p = out_dir / f"{shard}.parquet"
        part = part[COLUMNS]
        if p.exists():
            part = pd.concat([pd.read_parquet(p), part], ignore_index=True)
        part = part.drop_duplicates(SHARD_KEYS, keep="last").sort_values(["game_id", "asof_utc", "team", "field"])
        part["bucket"] = part["bucket"].astype("Int8")
        part.to_parquet(p, index=False)
        counts[shard] = ledger_counts(part)
    return counts

