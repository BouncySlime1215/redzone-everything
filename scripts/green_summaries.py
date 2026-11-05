#!/usr/bin/env python3
"""[EXP-102] Green moment source: nflverse NFL and NBA.com cdn NBA play-by-play -> ESPN-summary-shaped archives, so
the reasoner moment builder (agent/kaggle/build_moments_kaggle.py --archive) runs on catalog-OK data under plan A.

    python scripts/green_summaries.py --out /content/archive --leagues nfl nba [--seasons 3] [--max-games N]

Serves: North Star "crunch live %" through the Teacher's labels (plan A: no ESPN-derived source); owner: lane 1.

Writes <out>/<league>/summary_<gid>.json (the fields espn.parse_summary, espn.flatten_plays, grading.is_big/is_crunch
and the brief read) and <out>/<league>/scoreboard_<night>.json ({"events": [{"id": gid}]}: reasoner_data.nights_of).
The plays come from the factory's own converters (redzone.noggin.factory._plays_football / _plays_basketball, the
ones Timing v1 trains on); this adds the header (teams in ESPN's abbreviations), NFL drive wrapping with
sequenceNumber, and the NFL "end" situation (next snap's possession, down, distance, yards to the end zone).

Nights on or after the factory EVAL_FIRST_NIGHT cut are never written (scorecard and locked nights stay unseen); the
moment builder then makes its own time split with a locked block of >= 25% of the remaining nights.
Sources and terms: docs/data-sources.md (nflverse CC-BY 4.0 / NBA.com cdn via shufinskiy/nba_data, both green).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# nflverse / NBA.com tricodes -> ESPN's (the brief, config teams (WSH) and logos use ESPN's)
ESPN_ABBR = {"nfl": {"LA": "LAR", "WAS": "WSH", "JAC": "JAX", "LV": "LV", "OAK": "OAK", "SD": "SD", "STL": "STL"},
             "nba": {"WAS": "WSH", "GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH"}}
SEASONS = {"nfl": list(range(2001, 2025)), "nba": list(range(2021, 2026))}   # NBA: end years (cdn start + 1)


def abbr(league: str, x: str) -> str:
    return ESPN_ABBR[league].get(x, x)


def header(gid: str, home: str, away: str, first_wall: str, final: tuple, playoff: bool) -> dict:
    def comp(side, team, score):
        return {"homeAway": side, "score": str(score), "team": {"id": team, "abbreviation": team,
                                                               "displayName": team, "shortDisplayName": team}}
    return {"id": gid, "season": {"type": 3 if playoff else 2},
            "competitions": [{"id": gid, "date": first_wall,
                              "competitors": [comp("home", home, final[0]), comp("away", away, final[1])],
                              "status": {"type": {"state": "post", "shortDetail": "Final", "detail": "Final"}}}]}


def _num(v):
    try:
        return None if v is None or v != v else int(v)
    except (TypeError, ValueError):
        return None


def nfl_games(path: Path, max_games: int | None = None):
    """nflverse season parquet -> (gid, summary) per game with wall clocks."""
    import pandas as pd
    from redzone.noggin import factory as F
    import pyarrow.parquet as pq
    have = set(pq.read_schema(path).names)
    if "time_of_day" not in have:
        return
    cols = F.NFL_COLS + ["home_team", "away_team", "posteam", "down", "ydstogo", "yardline_100", "drive"]
    df = pd.read_parquet(path, columns=[c for c in cols if c in have])
    df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
    for n, (gid, g) in enumerate(df.groupby("game_id", sort=False)):
        if max_games and n >= max_games:
            break
        plays, _ = F._plays_football(g)
        if len(plays) < 20:
            continue
        home, away = abbr("nfl", g.home_team.iloc[0]), abbr("nfl", g.away_team.iloc[0])
        # the next scrimmage snap's situation, by play id: ESPN's "end" is the situation after this play
        snaps = [(int(r.play_id), r) for r in g.itertuples(index=False)
                 if isinstance(r.posteam, str) and _num(r.down) and _num(r.yardline_100)]
        nxt, j = {}, 0
        for r in g.itertuples(index=False):
            while j < len(snaps) and snaps[j][0] <= int(r.play_id):
                j += 1
            if j < len(snaps):
                nxt[int(r.play_id)] = snaps[j][1]
        for seq, p in enumerate(plays):
            p["sequenceNumber"] = str(seq)
            s = nxt.get(int(p["id"].rsplit(":", 1)[1]))
            if s is not None:
                p["end"] = {"team": {"id": abbr("nfl", s.posteam)}, "down": _num(s.down), "distance": _num(s.ydstogo),
                            "yardsToEndzone": _num(s.yardline_100)}
            t = p["type"]["text"].lower()
            p["isTurnover"] = "interception" in t or "fumble recovery (opponent)" in t
        last = plays[-1]
        drive_of = {int(r.play_id): _num(r.drive) for r in g.itertuples(index=False)} if "drive" in g else {}
        drives, cur = [], None
        for p in plays:                                 # nflverse's drive number; END rows join the drive before
            dn = drive_of.get(int(p["id"].rsplit(":", 1)[1])) or cur
            if not drives or dn != cur:
                drives.append({"plays": []})
                cur = dn
            drives[-1]["plays"].append(p)
        summ = {"header": header(str(gid), home, away, plays[0]["wallclock"],
                                 (_num(last["homeScore"]) or 0, _num(last["awayScore"]) or 0),
                                 g.season_type.iloc[0] == "POST"),
                "drives": {"previous": drives}}
        yield str(gid), summ


def nba_games(path: Path, max_games: int | None = None):
    """NBA.com cdn parquet (hoopR schema, scripts/nba_cdn_plays.py) -> (gid, summary) per game."""
    import pandas as pd
    from redzone.noggin import factory as F
    df = pd.read_parquet(path)
    df = df[df.type_text.fillna("").ne("Substitution") & df.period_number.notna() & df.wallclock.notna()]
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    df = df.sort_values(["game_id", "sq"], kind="stable")
    for n, (gid, g) in enumerate(df.groupby("game_id", sort=False)):
        if max_games and n >= max_games:
            break
        plays = F._plays_basketball(g)
        if len(plays) < 20:
            continue
        home, away = abbr("nba", g.home_team_abbrev.iloc[0]), abbr("nba", g.away_team_abbrev.iloc[0])
        last = plays[-1]
        if not plays[-1]["type"]["text"].lower().startswith("end game"):
            plays.append({**last, "id": f"{last['id']}e", "type": {"text": "End Game"}, "text": "End of Game",
                          "scoringPlay": False, "scoreValue": 0})
        gid = f"cdn{gid}"
        playoff = str(g.game_id.iloc[0]).lstrip("0").startswith("4")
        yield gid, {"header": header(gid, home, away, plays[0]["wallclock"],
                                     (_num(last["homeScore"]) or 0, _num(last["awayScore"]) or 0), playoff),
                    "plays": plays}


def write(league: str, games, out: Path) -> dict:
    """Write the summaries and per-night scoreboards; drops nights on or after the factory cut."""
    from redzone.noggin import factory as F
    from redzone.sources import espn
    cut = F.EVAL_FIRST_NIGHT.get(league)
    d = out / league
    d.mkdir(parents=True, exist_ok=True)
    nights, dropped = defaultdict(list), 0
    for gid, s in games:
        t = espn._ts(s["header"]["competitions"][0]["date"])
        if t is None:
            continue
        night = F.night_of(t)
        if cut and night >= cut:
            dropped += 1
            continue
        (d / f"summary_{gid}.json").write_text(json.dumps(s))
        nights[night].append(gid)
    for night, gids in nights.items():                  # merge with what an earlier season already wrote
        p = d / f"scoreboard_{night}.json"
        old = json.loads(p.read_text())["events"] if p.exists() else []
        seen = {e["id"] for e in old}
        p.write_text(json.dumps({"events": old + [{"id": g} for g in gids if g not in seen]}))
    return {"games": sum(len(v) for v in nights.values()), "nights": len(nights), "dropped_eval_window": dropped}


def main(argv=None) -> int:
    import world_model_pilot as W
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--raw", type=Path, default=None, help="raw cache (default <out>/../raw)")
    ap.add_argument("--leagues", nargs="+", default=["nfl", "nba"])
    ap.add_argument("--seasons", type=int, default=3, help="the most recent N seasons per league")
    ap.add_argument("--max-games", type=int, default=None, help="per season (smoke tests)")
    a = ap.parse_args(argv)
    raw = a.raw or a.out.parent / "raw"
    report = {}
    for lg in a.leagues:
        conv = {"nfl": nfl_games, "nba": nba_games}[lg]
        for y in SEASONS[lg][-a.seasons:]:
            p = W.fetch(lg, y, raw)
            if p is None:
                report[f"{lg}_{y}"] = {"games": 0, "note": "not downloaded"}
                continue
            report[f"{lg}_{y}"] = write(lg, conv(p, a.max_games), a.out)
            print(lg, y, report[f"{lg}_{y}"], flush=True)
    (a.out / "green_report.json").write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
