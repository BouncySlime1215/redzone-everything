"""Data Division A (play-by-play): sportsdataverse wehoop release pbp -> the play table (schema.py).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining data scale (EXP-097,
EXP-1000). Owner: Data Planner A.

Sources: WNBA (sportsdataverse/wehoop-wnba-data, league "wnba", level "pro", 2002-2025) and women's college
basketball (sportsdataverse/wehoop-wbb-data, league "wcbb", level "college", 2005-2026). Release assets are the
ESPN pbp frames hoopR/wehoop publish (sportsdataverse-py WNBA_BASE_URL / WBB_BASE_URL); URL_TEMPLATES holds the
template first and fallback spellings after it, so a rename is a one-line fix.

WCBB is OFF for the product (Nick 7:55 PM) but allowed as world-model pretraining data (Nick 11:35 PM); its
2026-27 nights from schema.EVAL_FIRST_NIGHT["wcbb"] on are never written (skipped as locked_games).
Licence: ESPN-derived, data plan B only, personal use (LICENSE, tag = schema.PERSONAL).

Mapping notes:
  clock_s   seconds left, from clock_display_value ("M:SS" or "SS.s"), else clock_minutes*60 + clock_seconds.
  wall_ts   from `wallclock` (ISO UTC) only; older seasons have none -> null.
  night     first wallclock -> America/New_York minus 5 h (factory.night_of rule), else game_date YYYYMMDD.
  sub_kind  "made"/"missed" for shooting plays (shooting_play column, else a shot-word match on type_text).
  x, y      ESPN raw shot coords: x 0..50 ft across the court, y 0..94 ft from the shooter's baseline (shots
            live in 0..~47). Normalised x/50, y/94. Prefers coordinate_x_raw/_y_raw; coordinate_x/_y are used
            only where they fall inside those ranges (hoopR's re-centred coords and the -214748340 sentinel
            for non-shots -> null).
  stop      "timeout" (any timeout incl. official TV timeout), "end_period" (end period/half/game),
            "review" (instant replay / challenge), else "none".
  possession team with the ball after the event (possession_after): made shot / turnover -> other team, rebound
            -> rebounder's team, made last free throw of a trip -> other team, else carried; "" until known.
CLI: python scripts/pbp/wehoop.py --league wnba --seasons 2025-2002 --out DIR [--keep-raw]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

LICENSE = ("ESPN-derived via sportsdataverse wehoop (Disney ToU bars AI training); usable only under data plan B "
           "for personal use")
TAG = schema.PERSONAL
UA = "redzone-personal-research/1.0"
GH = "https://github.com/sportsdataverse"
URL_TEMPLATES = {
    "wnba": [f"{GH}/wehoop-wnba-data/releases/download/espn_wnba_pbp/play_by_play_{{season}}.parquet",
             f"{GH}/wehoop-wnba-data/releases/download/espn_wnba_pbp/wnba_pbp_{{season}}.parquet"],
    "wcbb": [f"{GH}/wehoop-wbb-data/releases/download/espn_womens_college_basketball_pbp/play_by_play_{{season}}"
             ".parquet",
             f"{GH}/wehoop-wbb-data/releases/download/espn_womens_college_basketball_pbp/wbb_pbp_{{season}}"
             ".parquet"],
}
LEAGUES = {"wnba": {"level": "pro", "seasons": (2002, 2025), "repo": "sportsdataverse/wehoop-wnba-data"},
           "wcbb": {"level": "college", "seasons": (2005, 2026), "repo": "sportsdataverse/wehoop-wbb-data"}}
SOURCE = {"id": "wehoop", "sport": "basketball", "leagues": LEAGUES, "license": LICENSE, "tag": TAG,
          "data_plan": "B", "url_templates": URL_TEMPLATES, "est_events_per_game": 430}

ET = ZoneInfo("America/New_York")
SENTINEL = -1e8  # ESPN writes -214748340 (int32 min / 10) for "no location"
SHOT_WORDS = ("shot", "jumper", "layup", "dunk", "free throw", "hook", "tip", "fadeaway", "bank")


def _get(r: dict, *names, default=None):
    for n in names:
        v = r.get(n)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return default


def _id(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def _bool(v) -> bool | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "t", "yes")
    return bool(v)


def clock_seconds(r: dict) -> float | None:
    d = _get(r, "clock_display_value", "clock.displayValue")
    if isinstance(d, str) and d.strip():
        m = re.fullmatch(r"\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*", d)
        if m:
            return int(m.group(1) or 0) * 60 + float(m.group(2))
    mi, se = _get(r, "clock_minutes"), _get(r, "clock_seconds")
    if mi is not None or se is not None:
        try:
            return float(mi or 0) * 60 + float(se or 0)
        except (TypeError, ValueError):
            return None
    return None


def wall_ts(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
        return None
    if hasattr(v, "timestamp") and not isinstance(v, str):
        if getattr(v, "tzinfo", None) is None:
            v = v.replace(tzinfo=timezone.utc)
        return float(v.timestamp())
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def night_of(ts: float) -> str:
    """factory.night_of rule without importing redzone: UTC -> America/New_York, minus 5 h, date."""
    return (datetime.fromtimestamp(ts, timezone.utc).astimezone(ET) - timedelta(hours=5)).strftime("%Y%m%d")


def night_from_date(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (date, datetime)) or hasattr(v, "strftime"):
        return v.strftime("%Y%m%d")
    s = re.sub(r"\D", "", str(v))[:8]
    return s if len(s) == 8 else ""


def coord(v, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f <= SENTINEL or f < 0 or f > hi:
        return None
    return f / hi


def stop_of(kind: str) -> str:
    if "timeout" in kind:
        return "timeout"
    if kind.startswith("end ") or kind in ("endperiod", "endgame"):
        return "end_period"
    if "replay" in kind or "review" in kind or "challenge" in kind:
        return "review"
    return "none"


def possession_after(kind: str, side: str, made: bool, prev: str) -> str:
    """Team with the ball after the event: made shot / turnover -> other team, any rebound -> rebounder's team
    (a made free throw only when it is the last of its trip, never on technicals), else carry prev."""
    if side not in ("home", "away"):
        return prev
    other = "away" if side == "home" else "home"
    if "rebound" in kind:
        return side
    if "turnover" in kind:
        return other
    if made:
        if "free throw" in kind:
            m = re.search(r"(\d) of (\d)", kind)
            return other if (m and m.group(1) == m.group(2) and "technical" not in kind) else prev
        return other
    return prev


def convert_game(plays: list[dict], league: str, season: int) -> list[dict]:
    """ESPN pbp rows of one game -> play-table rows (seq 0.. by sequence_number)."""
    level = LEAGUES[league]["level"]
    plays = sorted(plays, key=lambda r: float(_get(r, "sequence_number", "game_play_number", default=0)))
    first = next((t for t in (wall_ts(r.get("wallclock")) for r in plays) if t is not None), None)
    night = night_of(first) if first is not None else night_from_date(_get(plays[0], "game_date", "date"))
    out, poss = [], ""
    for i, r in enumerate(plays):
        home_id, away_id, team = (_id(_get(r, k)) for k in ("home_team_id", "away_team_id", "team_id"))
        side = "home" if team and team == home_id else "away" if team and team == away_id else ""
        kind = " ".join(str(_get(r, "type_text", "type.text", default="")).lower().split())
        scoring = _bool(_get(r, "scoring_play"))
        shooting = _bool(_get(r, "shooting_play"))
        if shooting is None:
            shooting = any(w in kind for w in SHOT_WORDS)
        sub = ("made" if scoring else "missed") if shooting and scoring is not None else ""
        poss = possession_after(kind, side, sub == "made", poss)
        cx = _get(r, "coordinate_x_raw", "coordinate_x", "coordinate.x")
        cy = _get(r, "coordinate_y_raw", "coordinate_y", "coordinate.y")
        x, y = coord(cx, 50.0), coord(cy, 94.0)
        if x is None or y is None:
            x = y = None
        out.append(schema.row(
            source="wehoop", sport="basketball", league=league, level=level,
            season=int(_get(r, "season", default=season)), game_id=_id(_get(r, "game_id")), night=night, seq=i,
            period=_get(r, "period_number", "period.number", "period"), clock_s=clock_seconds(r),
            wall_ts=wall_ts(r.get("wallclock")),
            home=_get(r, "home_team_abbrev", "home_team_abbreviation", default=home_id),
            away=_get(r, "away_team_abbrev", "away_team_abbreviation", default=away_id),
            home_score=_get(r, "home_score"), away_score=_get(r, "away_score"), side=side, possession=poss,
            kind=kind,
            sub_kind=sub, actor_id=_id(_get(r, "athlete_id_1")), actor2_id=_id(_get(r, "athlete_id_2")),
            x=x, y=y, value=float(_get(r, "score_value", default=0) or 0) if scoring else 0.0,
            stop=stop_of(kind), license=LICENSE, tag=TAG))
    return out


def convert_frame(df, league: str, season: int) -> tuple[list[dict], dict]:
    """Whole season frame -> (valid rows, counts {games, dropped_games, locked_games})."""
    rows, counts = [], {"games": 0, "dropped_games": 0, "locked_games": 0}
    cut = schema.EVAL_FIRST_NIGHT.get(league)
    for _, g in df.groupby(df["game_id"].map(_id), sort=True):
        game = convert_game(g.to_dict("records"), league, season)
        if cut and game and game[0]["night"] >= cut:
            counts["locked_games"] += 1
            continue
        if schema.validate(game):
            counts["dropped_games"] += 1
            continue
        counts["games"] += 1
        rows.extend(game)
    return rows, counts


def download(url: str, dest: Path, tries: int = 5) -> bool:
    """Stream url -> dest via dest.part (resumes with Range). False on 404; raises after `tries` failures."""
    import requests
    part = Path(str(dest) + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for k in range(tries):
        have = part.stat().st_size if part.exists() else 0
        hdr = {"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with requests.get(url, headers=hdr, stream=True, timeout=60, allow_redirects=True) as r:
                if r.status_code == 404:
                    return False
                if r.status_code == 416:  # already complete
                    part.replace(dest)
                    return True
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                with open(part, mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            part.replace(dest)
            return True
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(min(60, 2 ** k * 2))
    return False


def pull_season(league: str, season: int, out_dir, keep_raw: bool = False, downloader=download) -> dict | None:
    """Download one season's release parquet, convert, validate, write out_dir/<league>/<season>.parquet."""
    import pandas as pd
    out_dir = Path(out_dir)
    shard = out_dir / league / f"{season}.parquet"
    if shard.exists() and Path(str(shard) + ".json").exists():
        return {"skipped": True, "path": str(shard)}
    raw = out_dir / "raw" / league / f"play_by_play_{season}.parquet"
    if not raw.exists():
        for tpl in URL_TEMPLATES[league]:
            if downloader(tpl.format(season=season), raw):
                break
        else:
            return None
    rows, counts = convert_frame(pd.read_parquet(raw), league, season)
    meta = {"source": "wehoop", "league": league, "season": season, "license": LICENSE, "tag": TAG,
            "data_plan": "B", **counts}
    side = schema.write_shard(rows, shard, meta)
    if not keep_raw:
        raw.unlink(missing_ok=True)
    return side


def _seasons(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    a, b = int(a), int(b or a)
    return list(range(a, b - 1, -1)) if a >= b else list(range(a, b + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--league", choices=sorted(LEAGUES), required=True)
    ap.add_argument("--seasons", help="e.g. 2025-2002 (default: the league's full range, newest first)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args(argv)
    lo, hi = LEAGUES[a.league]["seasons"]
    for s in _seasons(a.seasons or f"{hi}-{lo}"):
        res = pull_season(a.league, s, a.out, keep_raw=a.keep_raw)
        print(a.league, s, "missing" if res is None else
              {k: res.get(k) for k in ("skipped", "events", "games", "dropped_games", "locked_games")},
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
