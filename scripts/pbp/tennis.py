"""Jeff Sackmann's tennis_slam_pointbypoint -> play table (one row per point).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining scale (tennis point streams,
a sport with no game clock: break/timing structure is set ends + change-of-ends). Owner: Data Planner A.

Source: github JeffSackmann/tennis_slam_pointbypoint, per slam-year files `{year}-{slam}-matches.csv` and
`{year}-{slam}-points.csv` (slam in ausopen, frenchopen, wimbledon, usopen; ~2011-2024, some missing: 404s are
tolerated). Licence: CC BY-NC-SA 4.0 (credit Jeff Sackmann; non-commercial; share-alike if a derivative is published).

Mapping (documented choices):
  league     f"{slam}_{men|women}": match_num < 2000 is men, >= 2000 women (match_num column, else the match_id suffix).
  night      the slam's APPROXIMATE first day (START table; files carry no per-match date). Tennis has no locked window.
  period     SetNo.  elapsed_s = ElapsedTime ("H:MM:SS", since MATCH start in the source) minus the set's first point
             time, so it is seconds since the SET started, as the schema asks.  clock_s / wall_ts null.
  home/away  player1 / player2 (slugged).  side = possession = the server's side.  actor_id = server,
             actor2_id = point winner (both slugged names, or the source player ids when the matches file has them).
  kind       "ace" | "double_fault" | "winner" | "unforced_error" | "point" from the flag columns (ace/winner looked up
             on the point winner, double fault/unforced error on the loser).  sub_kind = f"{P1Score}-{P2Score}" as the
             source gives it on that row.  value = 1.
  scores     home_score/away_score = games won in the CURRENT set after the point, counted by us from GameWinner
             (resets each set; a tiebreak counts as a game, 7-6).  Sets won are not stored.
  stop       "end_period" on a set's last point (SetWinner != 0, or the next point is in a new set / end of match),
             "score_break" on a game-ending point that leaves an odd game total in the set (change of ends), else "none".
Rows whose PointNumber is not a positive integer (the "0X"/"0Y" set markers) are skipped.

Status 2026-11-05: raw.githubusercontent.com returned a real GitHub 404 for every JeffSackmann repo from this box
(tennis_atp too; git ls-remote: not found), so the repo looks moved/withdrawn. Columns were coded from the published
layout and tested on a SYNTHETIC fixture; re-verify on the first real file (see pull_all's `base`).
Why: Converts Sackmann's tennis point-by-point data into the play table (docstring, Data A commit).
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_slam_pointbypoint/master/"
LICENSE = ("CC BY-NC-SA 4.0: credit Jeff Sackmann / tennis_slam_pointbypoint; non-commercial only; share-alike if we "
           "publish a derivative")
CREDIT = "Data: Jeff Sackmann, tennis_slam_pointbypoint (https://github.com/JeffSackmann/tennis_slam_pointbypoint)"
SLAMS = ("ausopen", "frenchopen", "wimbledon", "usopen")
# Not measured (repo unreachable 2026-11-05): a best-of-5 men's match is ~250-300 points, women's ~150.
SOURCE = {"id": "tennis_slam_pbp", "leagues": [f"{s}_{g}" for s in SLAMS for g in ("men", "women")],
          "license": LICENSE, "tag": "", "est_events_per_game": 200, "years": "2011-2024 (some slams missing)"}

# APPROXIMATE first day of the main draw (local date). Used as `night` for every match of that slam-year.
START = {
    "ausopen": {2011: "0117", 2012: "0116", 2013: "0114", 2014: "0113", 2015: "0119", 2016: "0118", 2017: "0116",
                2018: "0115", 2019: "0114", 2020: "0120", 2021: "0208", 2022: "0117", 2023: "0116", 2024: "0114"},
    "frenchopen": {2011: "0522", 2012: "0527", 2013: "0526", 2014: "0525", 2015: "0524", 2016: "0522", 2017: "0528",
                   2018: "0527", 2019: "0526", 2020: "0927", 2021: "0530", 2022: "0522", 2023: "0528", 2024: "0526"},
    "wimbledon": {2011: "0620", 2012: "0625", 2013: "0624", 2014: "0623", 2015: "0629", 2016: "0627", 2017: "0703",
                  2018: "0702", 2019: "0701", 2021: "0628", 2022: "0627", 2023: "0703", 2024: "0701"},
    "usopen": {2011: "0829", 2012: "0827", 2013: "0826", 2014: "0825", 2015: "0831", 2016: "0829", 2017: "0828",
               2018: "0827", 2019: "0826", 2020: "0831", 2021: "0830", 2022: "0829", 2023: "0828", 2024: "0826"},
}
DEFAULT_START = {"ausopen": "0115", "frenchopen": "0525", "wimbledon": "0630", "usopen": "0828"}


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def night_of(year: int, slam: str) -> str:
    return f"{int(year)}{START.get(slam, {}).get(int(year), DEFAULT_START.get(slam, '0101'))}"


def _int(v, default=None):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return default
    return int(f) if f == f else default


def _secs(v):
    """'H:MM:SS' (or 'MM:SS') -> seconds; anything else -> None."""
    parts = str(v or "").strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        nums = [0.0] + nums
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def _flag(p: dict, col: str) -> bool:
    return _int(p.get(col), 0) == 1


def gender(match_id: str, match_num=None) -> str:
    n = _int(match_num)
    if n is None:
        tail = re.findall(r"(\d+)$", str(match_id))
        n = int(tail[0]) if tail else 0
    return "women" if n >= 2000 else "men"


def _kind(p: dict, winner: int) -> str:
    w, lo = f"P{winner}", f"P{3 - winner}"
    if _flag(p, w + "Ace"):
        return "ace"
    if _flag(p, lo + "DoubleFault"):
        return "double_fault"
    if _flag(p, w + "Winner"):
        return "winner"
    if _flag(p, lo + "UnfErr"):
        return "unforced_error"
    return "point"


def convert_match(match: dict, points: list[dict]) -> list[dict]:
    """match: one row of the matches file (dict of str); points: that match's rows of the points file."""
    mid = str(match.get("match_id") or (points[0].get("match_id") if points else ""))
    m = re.match(r"(\d{4})-([a-z]+)-", mid)
    year = _int(match.get("year")) or (int(m.group(1)) if m else None)
    slam = slug(match.get("slam")) or (m.group(2) if m else "")
    league = f"{slam}_{gender(mid, match.get('match_num'))}"
    p1 = slug(match.get("player1")) or "player1"
    p2 = slug(match.get("player2")) or "player2"
    ids = {1: str(match.get("player1id") or "").strip() or p1, 2: str(match.get("player2id") or "").strip() or p2}
    base = dict(source="tennis_slam_pbp", sport="tennis", league=league, level="pro", season=year, game_id=mid,
                night=night_of(year, slam) if year else "", home=p1, away=p2, license=LICENSE, tag="")
    pts = []
    for p in points:
        n = _int(p.get("PointNumber"))
        if n is None or n <= 0 or not re.fullmatch(r"\s*\d+(\.0+)?\s*", str(p.get("PointNumber"))):
            continue
        if _int(p.get("PointWinner")) not in (1, 2):
            continue
        pts.append((n, p))
    pts.sort(key=lambda t: t[0])
    pts = [p for _, p in pts]
    side = {1: "home", 2: "away"}
    rows, games, cur_set, set_t0 = [], {1: 0, 2: 0}, None, None
    for i, p in enumerate(pts):
        s = _int(p.get("SetNo"), 1)
        t = _secs(p.get("ElapsedTime"))
        if s != cur_set:
            cur_set, games, set_t0 = s, {1: 0, 2: 0}, t
        if set_t0 is None:
            set_t0 = t
        elapsed = None if t is None or set_t0 is None else max(t - set_t0, 0.0)
        winner = _int(p.get("PointWinner"))
        server = _int(p.get("PointServer"))
        gw = _int(p.get("GameWinner"), 0)
        if gw in (1, 2):
            games[gw] += 1
        nxt = pts[i + 1] if i + 1 < len(pts) else None
        set_end = _int(p.get("SetWinner"), 0) in (1, 2) or nxt is None or _int(nxt.get("SetNo"), 1) != s
        stop = "end_period" if set_end else ("score_break" if gw in (1, 2) and (games[1] + games[2]) % 2 == 1
                                             else "none")
        sc1, sc2 = str(p.get("P1Score") or "").strip(), str(p.get("P2Score") or "").strip()
        rows.append(schema.row(**base, seq=len(rows), period=s, elapsed_s=elapsed, clock_s=None, wall_ts=None,
                               side=side.get(server, ""), possession=side.get(server, ""), kind=_kind(p, winner),
                               sub_kind=f"{sc1}-{sc2}" if sc1 or sc2 else "", actor_id=ids.get(server, ""),
                               actor2_id=ids[winner], value=1.0, home_score=games[1], away_score=games[2], stop=stop))
    return rows


def _read_csv(text: bytes) -> list[dict]:
    import pandas as pd
    df = pd.read_csv(io.BytesIO(text), dtype=str, keep_default_na=False, encoding_errors="replace", low_memory=False)
    return df.to_dict("records")


class _Fetcher:
    def __init__(self, raw_dir: Path, rps: float, session=None, base: str = BASE):
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "redzone-personal-research/1.0"
        self.s, self.raw, self.base = session, raw_dir, base
        self.gap, self.last, self.missing = 1.0 / rps if rps else 0.0, 0.0, []

    def get(self, name: str):
        """Raw bytes (cached under raw_dir) or None on 404."""
        p = self.raw / name
        if p.exists():
            return p.read_bytes()
        wait = self.last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        r = self.s.get(self.base + name, timeout=120)
        if getattr(r, "status_code", 200) == 404:
            self.missing.append(name)
            return None
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return r.content


def pull_all(out_dir, years=range(2024, 2010, -1), slams=SLAMS, session=None, rps: float = 2,
             keep_raw: bool = False, base: str = BASE) -> list[dict]:
    """Per slam-year: matches + points CSV (404 -> skipped); one shard per league-season at
    out_dir/tennis/<slam>_<men|women>/<year>.parquet; raw cached under out_dir/tennis/_raw (deleted unless keep_raw)."""
    out = Path(out_dir) / "tennis"
    f = _Fetcher(out / "_raw", rps, session, base)
    metas = []
    for year in years:
        for slam in slams:
            shards = {g: out / f"{slam}_{g}" / f"{year}.parquet" for g in ("men", "women")}
            done = {g: s for g, s in shards.items() if s.exists() and Path(str(s) + ".json").exists()}
            if len(done) == 2:
                metas += [json.loads(Path(str(s) + ".json").read_text()) for s in done.values()]
                continue
            stem = f"{year}-{slam}"
            pts_raw = f.get(f"{stem}-points.csv")
            if pts_raw is None:
                continue
            m_raw = f.get(f"{stem}-matches.csv")
            matches = {str(m.get("match_id")): m for m in (_read_csv(m_raw) if m_raw else [])}
            by_match: dict[str, list[dict]] = {}
            for p in _read_csv(pts_raw):
                by_match.setdefault(str(p.get("match_id")), []).append(p)
            rows = {"men": [], "women": []}
            dropped = {"men": 0, "women": 0}
            for mid, pts in by_match.items():
                match = matches.get(mid) or {"match_id": mid, "year": year, "slam": slam}
                g = gender(mid, match.get("match_num"))
                conv = convert_match(match, pts)
                if schema.validate(conv):
                    dropped[g] += 1
                    continue
                rows[g] += conv
            for g, shard in shards.items():
                if g in done:
                    metas.append(json.loads(Path(str(shard) + ".json").read_text()))
                    continue
                if not rows[g]:
                    continue
                league = f"{slam}_{g}"
                metas.append(schema.write_shard(rows[g], shard, {
                    "source": "tennis_slam_pbp", "league": league, "season": int(year), "license": LICENSE,
                    "tag": "", "data_plan": "A/B", "credit": CREDIT, "dropped_games": dropped[g],
                    "night_note": "night = approximate slam start date, not the match date"}))
            if not keep_raw:
                for name in (f"{stem}-points.csv", f"{stem}-matches.csv"):
                    (out / "_raw" / name).unlink(missing_ok=True)
    pull_all.missing = list(f.missing)
    return metas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--rps", type=float, default=2)
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--slams", nargs="*", default=list(SLAMS))
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()
    metas = pull_all(a.out_dir, a.years or range(2024, 2010, -1), a.slams, rps=a.rps, keep_raw=a.keep_raw)
    for meta in metas:
        print(meta["league"], meta["season"], meta["events"], meta["games"])
    print(f"shards {len(metas)}, matches {sum(m['games'] for m in metas)}, points {sum(m['events'] for m in metas)}, "
          f"missing files {len(getattr(pull_all, 'missing', []))}")
