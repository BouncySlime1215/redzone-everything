"""Pappalardo et al. 2019 public Wyscout soccer events -> play table (one row per event).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining scale (1,941 matches of
2017/18 top-5 leagues + World Cup 2018 + Euro 2016, ~1.7k events/match). Owner: Data Planner A.

Source: figshare collection 4415000 (events.zip -> events_<Competition>.json, matches.zip -> matches_<Competition>
.json, players.json, teams.json). Licence CC BY 4.0 (metadata CC0); cite Pappalardo et al., Scientific Data 2019.
Converter written against the documented schema (figshare not reachable from the build box; verify on first pull).
Goals: tag 101 on a Shot / Free Kick event (the keeper's "Save attempt" also carries 101 and is not counted);
own goal: tag 102 on any event, credited to the other side. Penalty shootout ("P") goals do not move the score.
x, y = positions[0] / 100 (Wyscout coordinates are in the acting team's attacking direction). No wall clock.
Why: Converts Pappalardo et al. Wyscout soccer events into the play table (docstring, Data A commit).
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

LICENSE = "CC BY 4.0 (metadata CC0); cite Pappalardo et al., Scientific Data 2019"
CREDIT = "Pappalardo et al. (2019) A public data set of spatio-temporal match events in soccer competitions. Sci Data 6:236"
UA = "redzone-personal-research/1.0"
# TODO-verify: figshare file ids (as used by socceraction's public Wyscout loader); fix here if a download 404s.
FIGSHARE = "https://figshare.com/ndownloader/files/{}"
FILES = {"events.zip": 14464685, "matches.zip": 14464622, "players.json": 15073721, "teams.json": 15073697,
         "competitions.json": 15073685}
# events_<Competition>.json suffix -> (league slug, level). Slugs match statsbomb.py's competition slugs.
COMPETITIONS = {"England": ("premier_league", "pro"), "France": ("ligue_1", "pro"),
                "Germany": ("1_bundesliga", "pro"), "Italy": ("serie_a", "pro"), "Spain": ("la_liga", "pro"),
                "European_Championship": ("uefa_euro", "intl"), "World_Cup": ("fifa_world_cup", "intl")}
SOURCE = {"id": "wyscout_pappalardo", "leagues": {v[0]: v[1] for v in COMPETITIONS.values()}, "license": LICENSE,
          "tag": "", "est_events_per_game": 1700, "matches": 1941}
PERIOD = {"1H": 1, "2H": 2, "E1": 3, "E2": 4, "P": 5}
GOAL, OWN_GOAL = 101, 102
SHOTS = {"Shot", "Free Kick"}
WHISTLE_SUB = {"Ball out of the field", "Whistle"}


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def _date(m: dict) -> datetime:
    return datetime.strptime(m["dateutc"][:10], "%Y-%m-%d")


def convert_match(match: dict, events: list[dict], league: str, level: str = "pro",
                  teams: dict | None = None) -> list[dict]:
    """events: this match's events (any order; sorted by period, eventSec, file order). teams: wyId -> name."""
    teams = teams or {}
    side_of = {str(t): d.get("side", "") for t, d in match["teamsData"].items()}
    tid = {s: t for t, s in side_of.items()}
    d = _date(match)
    season = d.year if level == "intl" or d.month < 7 else d.year + 1
    base = dict(source="wyscout_pappalardo", sport="soccer", league=league, level=level, season=season,
                game_id=str(match["wyId"]), night=d.strftime("%Y%m%d"),
                home=slug(teams.get(int(tid.get("home", 0)), tid.get("home", ""))),
                away=slug(teams.get(int(tid.get("away", 0)), tid.get("away", ""))), license=LICENSE, tag="")
    evs = sorted(enumerate(events), key=lambda ie: (PERIOD.get(ie[1].get("matchPeriod"), 9),
                                                    ie[1].get("eventSec", 0), ie[0]))
    evs = [e for _, e in evs]
    score = {"home": 0, "away": 0}
    rows = []
    for i, ev in enumerate(evs):
        side = side_of.get(str(ev.get("teamId")), "")
        tags = {t.get("id") for t in ev.get("tags") or []}
        per = PERIOD.get(ev.get("matchPeriod"))
        value, credit = 0.0, ""
        if OWN_GOAL in tags:
            credit = {"home": "away", "away": "home"}.get(side, "")
        elif GOAL in tags and ev.get("eventName") in SHOTS:
            credit = side
        if credit and per != 5:
            score[credit] += 1
            value = 1.0 if credit == side else 0.0  # value = goals for the acting team; own goal moves score only
        nxt = evs[i + 1] if i + 1 < len(evs) else None
        name, sub = ev.get("eventName", ""), ev.get("subEventName", "")
        if nxt is None or nxt.get("matchPeriod") != ev.get("matchPeriod"):
            stop = "halftime" if per == 1 else "end_period"
        elif name in ("Foul", "Offside") or sub in WHISTLE_SUB:
            stop = "whistle"
        else:
            stop = "none"
        pos = (ev.get("positions") or [{}])[0]
        x, y = pos.get("x"), pos.get("y")
        rows.append(schema.row(**base, seq=i, period=per, elapsed_s=ev.get("eventSec"), clock_s=None,
                               wall_ts=None, side=side, possession=side, kind=slug(name),
                               sub_kind="own_goal" if OWN_GOAL in tags else ("goal" if credit and credit == side
                                                                              else slug(sub)),
                               actor_id=ev.get("playerId") or "", x=None if x is None else min(max(x / 100, 0), 1),
                               y=None if y is None else min(max(y / 100, 0), 1), value=value,
                               home_score=score["home"], away_score=score["away"], stop=stop))
    return rows


def download(name: str, raw_dir: Path, session=None, chunk: int = 1 << 20) -> Path:
    """Resumable download of one figshare file (HTTP Range onto <name>.part, renamed when complete)."""
    dst = raw_dir / name
    if dst.exists():
        return dst
    if session is None:
        import requests
        session = requests.Session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    part = Path(str(dst) + ".part")
    have = part.stat().st_size if part.exists() else 0
    r = session.get(FIGSHARE.format(FILES[name]), headers={"User-Agent": UA, **({"Range": f"bytes={have}-"}
                                                                                  if have else {})},
                    stream=True, timeout=120)
    if have and r.status_code != 206:
        have = 0  # server ignored Range: start over
    r.raise_for_status()
    with open(part, "ab" if have else "wb") as f:
        for b in r.iter_content(chunk):
            f.write(b)
    part.rename(dst)
    return dst


def _zip_json(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return {Path(n).stem: json.load(io.TextIOWrapper(z.open(n), "utf-8"))
                for n in z.namelist() if n.endswith(".json")}


def pull_all(out_dir, competitions=None, session=None) -> list[dict]:
    """Download (resumable) + convert; one shard per competition-season out_dir/wyscout/<league>/<season>.parquet."""
    out = Path(out_dir) / "wyscout"
    raw = out / "_raw"
    paths = {n: download(n, raw, session) for n in ("events.zip", "matches.zip", "teams.json")}
    teams = {t["wyId"]: t.get("name", "") for t in json.loads(paths["teams.json"].read_text("utf-8"))}
    matches = _zip_json(paths["matches.zip"])
    metas = []
    with zipfile.ZipFile(paths["events.zip"]) as z:
        for n in z.namelist():
            comp = Path(n).stem.replace("events_", "")
            if comp not in COMPETITIONS or (competitions and comp not in competitions
                                            and COMPETITIONS[comp][0] not in competitions):
                continue
            league, level = COMPETITIONS[comp]
            by_match: dict[int, list] = {}
            for ev in json.load(io.TextIOWrapper(z.open(n), "utf-8")):
                by_match.setdefault(ev["matchId"], []).append(ev)
            groups: dict[int, list] = {}
            for m in matches.get(f"matches_{comp}", []):
                g = convert_match(m, by_match.get(m["wyId"], []), league, level, teams)
                if g and not schema.validate(g):
                    groups.setdefault(g[0]["season"], []).extend(g)
            for season, rows in groups.items():
                metas.append(schema.write_shard(rows, out / league / f"{season}.parquet", {
                    "source": "wyscout_pappalardo", "league": league, "season": season, "competition": comp,
                    "data_plan": "A/B", "license": LICENSE, "tag": "", "credit": CREDIT}))
    return metas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--competitions", nargs="*")
    a = ap.parse_args()
    for meta in pull_all(a.out_dir, a.competitions):
        print(meta["league"], meta["season"], meta["events"], meta["games"])
