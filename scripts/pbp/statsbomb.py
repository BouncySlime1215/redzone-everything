"""StatsBomb open data -> play table (one row per event).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining scale (soccer event streams,
~3.6k events/match). Owner: Data Planner A.

Source: github statsbomb/open-data (competitions.json, matches/{cid}/{sid}.json, events/{match_id}.json). Licence:
StatsBomb Open Data user agreement (research and personal interest; credit StatsBomb, logo if published). No wall
clock in open data (wall_ts null); elapsed_s comes from the per-period "timestamp". Penalty shootout (period 5)
goals keep sub_kind "goal" but value 0 and do not move the score (match score excludes the shootout).
Follow-up: 360 freeze frames (three-sixty/{match_id}.json) are skipped for now.
"""
from __future__ import annotations

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

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/"
LICENSE = "StatsBomb Open Data user agreement: research and personal interest; credit StatsBomb (logo if published)"
CREDIT = "Data: StatsBomb Open Data (https://github.com/statsbomb/open-data)"
# Measured 2026-11-05: 80 competition-seasons, 3961 matches; 10 sampled matches averaged 3642 events.
SOURCE = {"id": "statsbomb", "leagues": "many", "license": LICENSE, "tag": schema.PERSONAL, "est_events_per_game": 3640,
          "matches": 3961, "competition_seasons": 80}
WHISTLE = {"Foul Committed", "Offside"}


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def season_year(name: str) -> int:
    return int(re.findall(r"\d{4}", str(name))[-1])


def _ts(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _outcome(ev: dict) -> str:
    for k, v in ev.items():
        if isinstance(v, dict) and isinstance(v.get("outcome"), dict):
            return slug(v["outcome"].get("name"))
    for k in ("foul_committed", "bad_behaviour"):
        card = (ev.get(k) or {}).get("card")
        if card:
            return slug(card.get("name"))
    return ""


def convert_match(match: dict, events: list[dict], competition: dict | None = None) -> list[dict]:
    comp = competition or {}
    league = slug(comp.get("competition_name") or match["competition"]["competition_name"])
    season = season_year(comp.get("season_name") or match["season"]["season_name"])
    level = "intl" if comp.get("competition_international", match["competition"].get("country_name") ==
                               "International") else "pro"
    hid, aid = match["home_team"]["home_team_id"], match["away_team"]["away_team_id"]
    base = dict(source="statsbomb", sport="soccer", league=league, level=level, season=season,
                game_id=str(match["match_id"]), night=match["match_date"].replace("-", ""),
                home=slug(match["home_team"]["home_team_name"]), away=slug(match["away_team"]["away_team_name"]),
                license=LICENSE, tag=schema.PERSONAL)
    evs = sorted(events, key=lambda e: e.get("index", 0))
    side_of = {hid: "home", aid: "away"}
    score = {"home": 0, "away": 0}
    rows = []
    for i, ev in enumerate(evs):
        t = ev["type"]["name"]
        side = side_of.get((ev.get("team") or {}).get("id"), "")
        sub = _outcome(ev)
        goal = (t == "Shot" and sub == "goal") or t == "Own Goal For"
        value = 1.0 if goal and ev.get("period") != 5 else 0.0
        if value and side:
            score[side] += 1
        nxt = evs[i + 1] if i + 1 < len(evs) else None
        stop = "none"
        if t == "Half End":
            last = not (nxt and nxt["type"]["name"] == "Half End" and nxt.get("period") == ev.get("period"))
            stop = ("halftime" if ev.get("period") == 1 else "end_period") if last else "none"
        elif t == "Injury Stoppage":
            stop = "injury"
        elif t in WHISTLE or (t == "Pass" and sub == "out"):
            stop = "whistle"
        loc = ev.get("location") or []
        x = min(max(loc[0] / 120, 0), 1) if len(loc) >= 2 else None
        y = min(max(loc[1] / 80, 0), 1) if len(loc) >= 2 else None
        actor2 = ((ev.get("pass") or {}).get("recipient") or (ev.get("substitution") or {}).get("replacement")
                  or {}).get("id")
        rows.append(schema.row(**base, seq=i, period=ev.get("period"), elapsed_s=_ts(ev["timestamp"]),
                               clock_s=None, wall_ts=None, side=side,
                               possession=side_of.get((ev.get("possession_team") or {}).get("id"), ""),
                               kind=slug(t), sub_kind=sub, actor_id=(ev.get("player") or {}).get("id"),
                               actor2_id=actor2, x=x, y=y, value=value, home_score=score["home"],
                               away_score=score["away"], stop=stop))
    return rows


class _Fetcher:
    def __init__(self, raw_dir: Path, rps: float, session=None):
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "redzone-personal-research/1.0"
        self.s, self.raw, self.gap, self.last = session, raw_dir, 1.0 / rps if rps else 0.0, 0.0

    def get(self, rel: str):
        p = self.raw / rel
        if p.exists():
            return json.loads(p.read_text())
        wait = self.last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        r = self.s.get(BASE + rel, timeout=60)
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return json.loads(r.content)


def pull_all(out_dir, rps: float = 4, competitions=None, session=None, keep_raw: bool = False) -> list[dict]:
    """competitions -> matches -> events; raw cached under out_dir/statsbomb/_raw; one shard per comp-season.
    `competitions`: optional iterable of competition_id ints or league slugs to keep."""
    out = Path(out_dir) / "statsbomb"
    f = _Fetcher(out / "_raw", rps, session)
    keep = None if competitions is None else {str(c) for c in competitions}
    metas = []
    for comp in f.get("competitions.json"):
        league = slug(comp["competition_name"])
        if keep is not None and str(comp["competition_id"]) not in keep and league not in keep:
            continue
        shard = out / league / f"{season_year(comp['season_name'])}.parquet"
        if shard.exists() and Path(str(shard) + ".json").exists():
            metas.append(json.loads(Path(str(shard) + ".json").read_text()))
            continue
        rows, dropped, ids = [], 0, []
        for m in f.get(f"matches/{comp['competition_id']}/{comp['season_id']}.json"):
            ids.append(m["match_id"])
            g = convert_match(m, f.get(f"events/{m['match_id']}.json"), comp)
            if schema.validate(g):
                dropped += 1
                continue
            rows += g
        if not rows:
            continue
        metas.append(schema.write_shard(rows, shard, {
            "source": "statsbomb", "league": league, "season": season_year(comp["season_name"]),
            "competition_id": comp["competition_id"], "season_id": comp["season_id"], "data_plan": "A/B",
            "license": LICENSE, "tag": schema.PERSONAL, "credit": CREDIT, "gender": comp.get("competition_gender"),
            "dropped_games": dropped}))
        if not keep_raw:      # events files are ~3.5 MB each; the shard holds everything we use
            for mid in ids:
                (out / "_raw" / "events" / f"{mid}.json").unlink(missing_ok=True)
    return metas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--rps", type=float, default=4)
    ap.add_argument("--competitions", nargs="*")
    a = ap.parse_args()
    for meta in pull_all(a.out_dir, a.rps, a.competitions):
        print(meta["league"], meta["season"], meta["events"], meta["games"])
