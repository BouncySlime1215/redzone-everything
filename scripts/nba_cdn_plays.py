#!/usr/bin/env python3
"""NBA.com's own action log (cdn liveData, packaged by shufinskiy/nba_data) -> hoopR-schema play rows (plan A NBA).

    python scripts/nba_cdn_plays.py 2024 --out RAW     # season start year; writes RAW/nba/cdn_2024.parquet

Serves: North Star "data scale" (catalog-OK NBA play-by-play for the world model and a green-only Mind; ingest plan
Wave 2 #2); owner: lane 1 (Noggin training).

Source terms (#202 catalog, yellow): NBA.com ToS §9 = private, non-commercial use, credit NBA.com; the packaging repo
is Apache-2.0. One file per season from raw.githubusercontent.com (2020-21 on: the cdn log starts in 2020), raw rows
stay private (never committed or redistributed). Credit: play-by-play data (c) NBA.com, via shufinskiy/nba_data.

The rows use the column names factory.build_basketball and noggin_tokens read for hoopR, with ESPN-style type texts so
espn._kind / espn.stoppage_for read them the same way (timeouts, challenges, free-throw "n of m", team rebounds, end
period / end game). Each action keeps NBA.com's timeActual as its wallclock. Blocks and steals are separate actions in
the cdn log but folded into other plays by ESPN, so they are dropped; substitutions keep the "in" half only.
Why: Turns NBA.com's own action log into hoopR-schema rows, plan A for NBA (docstring, plan A commit).
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/cdnnba_{y}.tar.xz"
FIRST = 2020
CREDIT = "Play-by-play data (c) NBA.com, via shufinskiy/nba_data (Apache-2.0 packaging); private, non-commercial use."
COLS = ["actionNumber", "orderNumber", "clock", "timeActual", "period", "actionType", "subType", "qualifiers",
        "personId", "scoreHome", "scoreAway", "description", "teamTricode", "gameId"]
EXTRA = ["x", "y", "shotDistance", "shotResult", "possession", "descriptor", "area", "assistPersonId"]   # vocab v2
DROP = {"steal", "block", "memo", "heave"}


def clock_display(x) -> str:
    """PT11M58.00S -> '11:58'; under a minute ESPN shows seconds with a tenth ('58.0')."""
    m = re.match(r"PT(\d+)M([\d.]+)S", str(x))
    if not m:
        return ""
    mins, secs = int(m[1]), float(m[2])
    return f"{secs:.1f}" if mins == 0 else f"{mins}:{int(secs):02d}"


def type_text(r) -> str | None:
    a, s = str(r.actionType), (r.subType if isinstance(r.subType, str) else "")
    q = r.qualifiers if isinstance(r.qualifiers, str) else ""
    if a in DROP or (a == "period" and s == "start") or (a == "substitution" and s != "in"):
        return None
    if a in ("2pt", "3pt"):
        return (s.title() if s.lower().endswith("shot") else f"{s.title()} Shot") if s else "Shot"
    if a == "rebound":
        return "Offensive Rebound" if s == "offensive" else "Defensive Rebound"
    if a == "freethrow":
        return f"Free Throw - {s}" if s else "Free Throw"
    if a == "timeout":
        return "Coach's Challenge" if s == "challenge" else "Official Timeout" if "mandatory" in q else "Full Timeout"
    if a == "foul":
        return f"{s.title()} Foul" if s else "Foul"
    if a == "turnover":
        return f"{s.replace('-', ' ').title()} Turnover" if s else "Turnover"
    if a == "violation":
        return f"{s.replace('-', ' ').title()} Violation" if s else "Violation"
    if a == "period":
        return "End Period"
    if a == "game":
        return "End Game"
    return {"jumpball": "Jumpball", "substitution": "Substitution", "ejection": "Ejection",
            "instantreplay": "Instant Replay", "stoppage": "Stoppage"}.get(a, a.title())


def convert(df: pd.DataFrame) -> pd.DataFrame:
    """cdn rows (one season) -> hoopR-schema rows."""
    df = df.sort_values(["gameId", "orderNumber"], kind="stable")
    out = []
    for gid, g in df.groupby("gameId", sort=False):
        hs = pd.to_numeric(g.scoreHome, errors="coerce").ffill().fillna(0).astype(int).to_numpy()
        as_ = pd.to_numeric(g.scoreAway, errors="coerce").ffill().fillna(0).astype(int).to_numpy()
        dh, da = np.diff(hs, prepend=0), np.diff(as_, prepend=0)
        team = g.teamTricode.to_numpy()
        home = pd.Series(team[dh > 0]).mode()
        away = pd.Series(team[da > 0]).mode()
        home = str(home.iloc[0]) if len(home) else "?"
        away = str(away.iloc[0]) if len(away) else "?"
        seq = 0
        for i, r in enumerate(g.itertuples(index=False)):
            t = type_text(r)
            if t is None:
                continue
            seq += 1
            pts = int(max(dh[i], 0) + max(da[i], 0))
            text = str(r.description) if isinstance(r.description, str) else ""
            if r.actionType == "rebound" and text.upper().startswith("TEAM"):
                text = "Team Rebound"
            out.append({"id": f"{gid}{int(r.actionNumber):05d}", "sequence_number": seq, "type_text": t, "text": text,
                        "period_number": int(r.period), "clock_display_value": clock_display(r.clock),
                        "wallclock": str(r.timeActual)[:19] + "Z" if isinstance(r.timeActual, str) else None,
                        "scoring_play": pts > 0, "score_value": pts, "home_score": int(hs[i]), "away_score": int(as_[i]),
                        "game_id": str(gid), "home_team_abbrev": home, "away_team_abbrev": away,
                        "game_date": str(r.timeActual)[:10] if isinstance(r.timeActual, str) else None,
                        **{f"x_{c}": getattr(r, c, None) for c in ["subType", "personId", "teamTricode"] + EXTRA}})
    return pd.DataFrame(out)


def fetch_season(year: int, out: Path) -> Path | None:
    """One season -> out/nba/cdn_<year>.parquet (cached); None when the season is not packaged."""
    dst = out / "nba" / f"cdn_{year}.parquet"
    if dst.exists():
        return dst
    if year < FIRST:
        return None
    try:
        raw = urllib.request.urlopen(URL.format(y=year), timeout=300).read()
    except Exception as e:  # noqa: BLE001 - a missing season is skipped, and the ledger says so
        print(f"nba cdn {year}: not downloaded ({type(e).__name__})", flush=True)
        return None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz") as t:
        m = next(x for x in t.getmembers() if x.name.endswith(".csv"))
        df = pd.read_csv(t.extractfile(m), usecols=lambda c: c in COLS or c in EXTRA,
                         low_memory=False)
    rows = convert(df)
    dst.parent.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(dst.with_suffix(".tmp"), index=False)
    dst.with_suffix(".tmp").replace(dst)
    print(f"nba cdn {year}: {rows.game_id.nunique()} games, {len(rows):,} plays", flush=True)
    return dst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", type=int, nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    for y in a.years:
        fetch_season(y, a.out)
    print(CREDIT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
