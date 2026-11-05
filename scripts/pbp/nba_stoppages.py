#!/usr/bin/env python3
"""Measured NBA stoppage lengths from NBA.com's cdn action log (timeActual wall clock on every action), 2020-21 on.

Serves: Nick's NBA-first priority (2:00 AM Nov 5): 21% of NBA entries land mid-commercial; crew F merges this table
into redzone/scoring/broadcast/break_lengths.json (#250) and EXP-205's entry hold reads the early quantiles.
Owner: Data Planner A.

    python scripts/pbp/nba_stoppages.py OUT.json --seasons 2020-2025 [--raw DIR]

Definition (crew F's): a stoppage runs from the wall time of the action that stopped the clock to the REAL restart,
i.e. the next action that moves the game clock, back-dated by the game seconds that ran before it:
    restart_wall = wall(next clock-moving action) - (stopped clock - its clock)
Type = the most telling action inside the dead ball: period_break / halftime (period end -> next period's restart),
review (challenge timeout or instant replay), timeout_long (1st and 2nd timeout of the quarter, any team),
timeout_short (3rd and later), free_throws, foul, violation, turnover, other. Cells: season x type x period group
(q1-q4, ot) x clock bucket (12-7, 7-3, 3-2, 2-0 min left) with n, q05, q10, q25, q50, q75, q90 seconds.
network: "national" / "local" per game from crew B's schedule (--tv nba-national-tv.csv, joined on nba_game_id;
national = ESPN/ABC/TNT/NBA TV/Prime/NBC/Peacock), "unknown" when the game has no broadcast row.
Source: shufinskiy/nba_data cdnnba_<y>.tar.xz (NBA.com ToS private non-commercial; plan B personal use). Numbers
only, no rows are copied. Season keys are start years; 2025 (2025-26) is kept apart and every game on or after the
NBA locked/scorecard window (EVAL_FIRST_NIGHT) is excluded.
"""
from __future__ import annotations

import argparse
import io
import json
import lzma
import re
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from pbp import schema  # noqa: E402

URL = "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/cdnnba_{y}.tar.xz"
LICENSE = ("Derived from NBA.com cdn action log via shufinskiy/nba_data (NBA.com ToS private non-commercial); "
           "numbers only; plan B personal use")
COLS = ["gameId", "actionNumber", "orderNumber", "clock", "timeActual", "period", "actionType", "subType"]
QS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90)
PRIORITY = ("review", "timeout", "free_throws", "foul", "violation", "turnover", "other")


def clock_s(x) -> float | None:
    m = re.match(r"PT(\d+)M([\d.]+)S", str(x))
    return int(m[1]) * 60 + float(m[2]) if m else None


def bucket(c: float) -> str:
    return "12-7" if c > 420 else "7-3" if c > 180 else "3-2" if c > 120 else "2-0"


def kind_of(a: str, s: str) -> str:
    if a == "instantreplay" or (a == "timeout" and s == "challenge"):
        return "review"
    if a == "timeout":
        return "timeout"
    return {"freethrow": "free_throws", "foul": "foul", "violation": "violation", "turnover": "turnover"}.get(a, "other")


def game_stoppages(g) -> list[tuple]:
    """(type, period, clock_at_stop, seconds) for one game's actions (DataFrame sorted by orderNumber)."""
    import pandas as pd
    t = pd.to_datetime(g.timeActual, utc=True, errors="coerce", format="ISO8601")
    wall = ((t - pd.Timestamp(0, tz="UTC")).dt.total_seconds()).fillna(-1).to_numpy()   # unit-safe (pandas 2 ns / 3 us)
    clk = [clock_s(c) for c in g.clock]
    per = g.period.to_numpy()
    act = g.actionType.astype(str).to_numpy()
    sub = g.subType.fillna("").astype(str).to_numpy()
    full = [720.0 if p <= 4 else 300.0 for p in per]
    out, n, i, tos = [], len(g), 0, defaultdict(int)
    while i < n:
        c = clk[i]
        if c is None or not wall[i] > 0:
            i += 1
            continue
        j = i + 1                       # first later action that moves the clock (same period lower, or a new period started)
        while j < n and not (clk[j] is not None and wall[j] > 0 and
                             ((per[j] == per[i] and clk[j] < c) or (per[j] != per[i] and clk[j] < full[j]))):
            j += 1
        if j >= n:
            break
        if per[j] != per[i]:
            typ = "halftime" if per[i] == 2 else "period_break"
            secs = wall[j] - (full[j] - clk[j]) - wall[i]
        else:
            kinds = {kind_of(act[k], sub[k]) for k in range(i, j)}
            typ = next(p for p in PRIORITY if p in kinds)
            secs = wall[j] - (c - clk[j]) - wall[i]
            if typ == "timeout":
                tos[per[i]] += sum(1 for k in range(i, j) if act[k] == "timeout" and sub[k] != "challenge")
                typ = "timeout_long" if tos[per[i]] <= 2 else "timeout_short"
            elif typ == "other":        # live play (shot, rebound, sub on the fly): not a stoppage
                i = j
                continue
        if 0 <= secs <= (1500 if typ == "halftime" else 900):
            out.append((typ, int(per[i]), float(c), round(float(secs), 1)))
        i = j
    return out


def load_season(y: int, raw: Path | None):
    import pandas as pd
    import requests
    f = raw / f"cdnnba_{y}.tar.xz" if raw else None
    if f and f.exists():
        blob = f.read_bytes()
    else:
        r = requests.get(URL.format(y=y), timeout=600, headers={"User-Agent": "redzone-personal-research/1.0"})
        if r.status_code != 200:
            return None
        blob = r.content
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(blob)), mode="r:") as tar:
        m = next(x for x in tar.getmembers() if x.name.endswith(".csv"))
        return pd.read_csv(tar.extractfile(m), usecols=COLS, low_memory=False)


def load_tv(path: Path | None) -> dict:
    """crew B's nba-national-tv.csv -> {10-digit nba game id: "national" | "local"} (broadcast_known rows only)."""
    if not path:
        return {}
    import pandas as pd
    d = pd.read_csv(path, dtype={"nba_game_id": str})
    d = d[d.broadcast_known.astype(str).str.lower() == "true"].dropna(subset=["nba_game_id"])
    return {g.zfill(10): ("national" if str(n).lower() == "true" else "local")
            for g, n in zip(d.nba_game_id, d.national)}


def table(seasons: list[int], raw: Path | None = None, tv: dict | None = None) -> dict:
    import numpy as np
    import pandas as pd
    cut = schema.EVAL_FIRST_NIGHT["nba"]
    cells: dict = defaultdict(list)
    games = {}
    for y in seasons:
        d = load_season(y, raw)
        if d is None:
            continue
        ng = 0
        for gid, g in d.sort_values(["gameId", "orderNumber"], kind="stable").groupby("gameId", sort=False):
            first = pd.to_datetime(g.timeActual.iloc[0], utc=True, errors="coerce", format="ISO8601")
            if first is pd.NaT or (first.tz_convert("America/New_York") - pd.Timedelta(hours=5)).strftime("%Y%m%d") >= cut:
                continue
            ng += 1
            net = (tv or {}).get(f"{int(gid):010d}", "unknown")
            for typ, p, c, secs in game_stoppages(g):
                pg = f"q{p}" if p <= 4 else "ot"
                cells[(y, typ, pg, bucket(c) if typ not in ("halftime", "period_break") else "end", net)].append(secs)
        games[y] = ng
    rows = []
    for (y, typ, pg, b, net), v in sorted(cells.items()):
        a = np.asarray(v)
        rows.append({"season": y, "type": typ, "period": pg, "clock_bucket": b, "network": net, "n": len(a),
                     **{f"q{int(q * 100):02d}": round(float(np.quantile(a, q)), 1) for q in QS}})
    return {"source": "nba_cdn_stoppages", "license": LICENSE, "tag": schema.PERSONAL, "games": games,
            "definition": "stop action wall time -> real restart (next clock-moving action back-dated by its game "
                          "seconds); timeout_long = 1st/2nd timeout of the quarter, timeout_short = 3rd+",
            "rows": rows}


def render(t: dict) -> str:
    keep = [r for r in t["rows"] if r["n"] >= 30]
    lines = ["# NBA stoppage lengths (measured, cdn wall clocks)", "",
             f"Generated by `python scripts/pbp/nba_stoppages.py`. {t['definition']}. Games per season: "
             f"{t['games']}. Cells with n >= 30 shown; the JSON has all. {t['license']}; tag {t['tag']}.", "",
             "| season | type | period | clock left | network | n | q05 | q10 | q50 | q90 |",
             "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for r in keep:
        lines.append(f"| {r['season']} | {r['type']} | {r['period']} | {r['clock_bucket']} | {r['network']} | {r['n']} | {r['q05']} | "
                     f"{r['q10']} | {r['q50']} | {r['q90']} |")
    return "\n".join(lines) + "\n"


def _seasons(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--seasons", default="2020-2025")
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--tv", type=Path, help="crew B nba-national-tv.csv (national/local split)")
    a = ap.parse_args(argv)
    t = table(_seasons(a.seasons), a.raw, load_tv(a.tv))
    a.out.write_text(json.dumps(t, indent=1) + "\n")
    a.out.with_suffix(".md").write_text(render(t))
    print(len(t["rows"]), "cells", t["games"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
