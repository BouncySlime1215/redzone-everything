"""Schedule + results loaders shared by the context scripts (stakes, weather).

Both return one row per game: game_id, season, game_type, kickoff_utc, final_utc, home, away, home_score,
away_score, neutral, venue_id, roof (+ daynight for MLB). Sources: nflverse schedules games.csv (CC-BY-4.0) and
Retrosheet game logs GL<year>.TXT (free with the Retrosheet notice).

Why: first added in 51db66ed (2026-11-05) as "Data B: stakes (standings, Elo, playoff-odds swing) landed NFL+MLB; Open-Meteo weather and CFBD c..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

NFL_GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
RETRO_URL = "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons/{y}/GL{y}.TXT"  # 2024: gl2024.txt


def load_nfl(path) -> pd.DataFrame:
    g = pd.read_csv(path, low_memory=False)
    t = g["gametime"].fillna("13:00")
    local = pd.to_datetime(g["gameday"] + " " + t, format="%Y-%m-%d %H:%M")
    kick = local.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    kick = kick.fillna(local.dt.tz_localize("UTC") + pd.Timedelta(hours=5))
    return pd.DataFrame({
        "game_id": g["game_id"], "season": g["season"].astype(int),
        "game_type": g["game_type"].where(g["game_type"] == "REG", "POST"),
        "kickoff_utc": kick, "final_utc": kick + pd.Timedelta(hours=4),   # NFL games run ~3h10m
        "home": g["home_team"], "away": g["away_team"],
        "home_score": g["home_score"], "away_score": g["away_score"],
        "neutral": g["location"].eq("Neutral"),
        "venue_id": g["stadium_id"], "roof": g["roof"],
    })


def load_retrosheet(retro_dir) -> pd.DataFrame:
    """Retrosheet game logs (regular season only). No start times: kickoff = the date (day game 17:00Z, night
    23:00Z, used only for ordering); the result counts from 09:00Z the next day, so the "day" snapshot at 09:00Z
    on game day sees only earlier dates (doubleheader game 2 never sees game 1)."""
    frames = []
    for p in sorted(Path(retro_dir).glob("*")):
        if not p.name.lower().startswith("gl") or p.stat().st_size == 0:
            continue
        d = pd.read_csv(p, header=None, usecols=[0, 1, 3, 6, 9, 10, 12, 16], dtype=str)
        d.columns = ["date", "num", "away", "home", "away_score", "home_score", "dn", "park"]
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    day = pd.to_datetime(d["date"], format="%Y%m%d").dt.tz_localize("UTC")
    kick = day + pd.to_timedelta(d["dn"].map({"D": 17}).fillna(23), unit="h")
    return pd.DataFrame({
        "game_id": d["home"] + d["date"] + d["num"], "season": day.dt.year.astype(int), "game_type": "REG",
        "kickoff_utc": kick, "final_utc": day + pd.Timedelta(hours=33),
        "home": d["home"], "away": d["away"],
        "home_score": pd.to_numeric(d["home_score"]), "away_score": pd.to_numeric(d["away_score"]),
        "neutral": False, "venue_id": d["park"], "roof": None, "daynight": d["dn"],
    })
