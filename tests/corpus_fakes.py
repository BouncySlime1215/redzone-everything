"""Fixture games and folders for the corpus tests (B-02): ESPN-shaped plays, a B-01 history folder, a hoopR parquet file, an archive folder.

Why: the corpus tests must run in CI with no real nights, no network and no archive, so every folder they read is built here in a tmp dir
from plays that look like ESPN's (the shape redzone/sources/espn.py and scripts/history/espn.py produce).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import random
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KINDS = ("Jump Shot", "Defensive Rebound", "Personal Foul", "Layup", "Timeout", "Three Point Jumper", "Offensive Rebound", "Turnover")


def start_utc(night: str, hour: int = 19, minute: int = 30) -> dt.datetime:
    d = dt.datetime.strptime(night, "%Y%m%d").replace(hour=hour, minute=minute, tzinfo=ET)
    return d.astimezone(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_plays(gid: str, night: str | None, n: int = 40, wall: bool = True) -> list[dict]:
    """n ESPN-shaped basketball plays. The score trajectory depends on the game id, so two different ids differ."""
    rng = random.Random(gid)
    t0 = start_utc(night) if night else start_utc("20200101")
    h = a = 0
    plays = []
    for i in range(n):
        per = 1 + (4 * i) // n
        left = 720 - ((i * 4 * 720 // n) % 720) - 1
        scoring = i % 3 == 1
        pts = rng.choice((1, 2, 2, 3)) if scoring else 0
        if scoring:
            if rng.random() < 0.5:
                h += pts
            else:
                a += pts
        play = {"id": f"{gid}{i:03d}", "sequenceNumber": str(i + 1), "type": {"id": str(i), "text": KINDS[i % len(KINDS)]},
                "text": f"play {i}", "period": {"number": per}, "clock": {"displayValue": f"{left // 60}:{left % 60:02d}"},
                "homeScore": h, "awayScore": a, "scoringPlay": scoring, "scoreValue": pts}
        if wall:
            play["wallclock"] = iso(t0 + dt.timedelta(seconds=25 * i))
        plays.append(play)
    return plays


def espn_record(league: str, season: int, gid: str, night: str | None, n: int = 40, wall: bool = True, home: str = "HOM", away: str = "AWY") -> dict:
    """One B-01 per-game record (scripts/history/espn.game_record): header fields and pruned plays."""
    rec = {"v": 1, "source": "espn", "league": league, "season": season, "id": gid, "matchup": f"{away} @ {home}",
           "home": {"abbr": home, "id": "1", "score": "100"}, "away": {"abbr": away, "id": "2", "score": "90"},
           "plays": make_plays(gid, night, n, wall), "n_plays": n, "wall_frac": 1.0 if wall else 0.0}
    if night:
        rec["night"], rec["date"] = night, f"{night[:4]}-{night[4:6]}-{night[6:]}"
    return rec


def write_history_shard(root: Path, league: str, season: int, records: list[dict]) -> Path:
    """<root>/shards/espn_<league>_<season>.jsonl.gz as scripts/history/pack.py writes it."""
    p = root / "shards" / f"espn_{league}_{season}.jsonl.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(("\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n").encode(), mtime=0))
    return p


def write_history_game(root: Path, rec: dict) -> Path:
    p = root / "espn" / rec["league"] / str(rec["season"]) / f"{rec['id']}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(rec, sort_keys=True).encode(), mtime=0))
    return p


def hoopr_frame(games: list[tuple[str, str | None, list[dict]]], home: str = "HOM", away: str = "AWY"):
    """A hoopR-shaped DataFrame: (game id, night, plays) -> one row per play, with the columns scripts/noggin_tokens.games_of reads."""
    import pandas as pd
    rows = []
    for gid, night, plays in games:
        for p in plays:
            rows.append({"id": p["id"], "sequence_number": int(p["sequenceNumber"]), "type_text": p["type"]["text"], "text": p["text"],
                         "period_number": p["period"]["number"], "clock_display_value": p["clock"]["displayValue"],
                         "wallclock": p.get("wallclock"), "scoring_play": p["scoringPlay"], "score_value": p["scoreValue"],
                         "home_score": p["homeScore"], "away_score": p["awayScore"], "game_id": int(gid), "home_team_abbrev": home,
                         "away_team_abbrev": away, "game_date": f"{night[:4]}-{night[4:6]}-{night[6:]}" if night else None})
    return pd.DataFrame(rows)


def write_hoopr(root: Path, key: str, season: int, df) -> Path:
    p = root / "datasets" / key / f"play_by_play_{season}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)
    return p


def write_archive_game(root: Path, league: str, night: str, gid: str, n: int = 30, completed: bool = True) -> None:
    d = root / league
    d.mkdir(parents=True, exist_ok=True)
    board = d / f"scoreboard_{night}.json"
    events = (json.loads(board.read_text())["events"] if board.exists() else []) + [{"id": gid}]
    board.write_text(json.dumps({"events": events}))
    summary = {"header": {"id": gid, "competitions": [{"status": {"type": {"completed": completed}},
                                                       "competitors": [{"homeAway": "home", "team": {"abbreviation": "HOM"}},
                                                                       {"homeAway": "away", "team": {"abbreviation": "AWY"}}]}]},
               "plays": make_plays(gid, night, n)}
    (d / f"summary_{gid}.json").write_text(json.dumps(summary))


def nba_night(season: int, k: int) -> str:
    """The k-th night (0..19) of an NBA season label: November of the year before, so no fence is near it."""
    return f"{season - 1}11{10 + k:02d}"
