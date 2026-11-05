#!/usr/bin/env python3
"""NBA break lengths from NBA.com's own action log (cdn liveData, as packaged by shufinskiy/nba_data).

    python scripts/measure_nba_cdn_breaks.py 2024 [2023 ...]      # season start years; prints q10/50/90 per break

Serves: North Star "landings in dead time" / "dead air" (EXP-019, the break table); owner: lane 1.
Source terms (#202 catalog, yellow): NBA.com ToS = private, non-commercial use, credit NBA.com; the packaging repo is
Apache-2.0. Read-only, one file per season from raw.githubusercontent.com, nothing redistributed.

Unlike the ESPN feed (break_table.v1: "break play -> next live play, restart included"), timeActual is when each
action happened, so the restart is break -> next live action minus the game clock that ran before it.
Measured on 2024-25 (1,230 games): mandatory TV timeout q50 176 s (q90 204), team timeout 101 s, quarter break
161 s, halftime 919 s, coach's challenge 144 s. The ESPN-feed table's NBA quarter (187 s) and long (202 s) are
these plus ~20-25 s of restart-to-next-play.
"""
from __future__ import annotations

import io
import re
import sys
import tarfile
import urllib.request

import numpy as np
import pandas as pd

URL = "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/cdnnba_{y}.tar.xz"
SKIP = {"timeout", "substitution", "period", "game"}
COLS = ["orderNumber", "actionType", "subType", "qualifiers", "gameId", "period", "clock", "timeActual"]


def _clock(x) -> float:
    m = re.match(r"PT(\d+)M([\d.]+)S", str(x))
    return int(m[1]) * 60 + float(m[2]) if m else np.nan


def season(year: int) -> pd.DataFrame:
    raw = urllib.request.urlopen(URL.format(y=year), timeout=120).read()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz") as t:
        m = next(x for x in t.getmembers() if x.name.endswith(".csv"))
        return pd.read_csv(t.extractfile(m), usecols=COLS)


def breaks(d: pd.DataFrame) -> dict[str, list[float]]:
    d = d.copy()
    d["t"] = (pd.to_datetime(d.timeActual, utc=True, errors="coerce", format="ISO8601")
              - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    d["c"] = d.clock.map(_clock)
    out: dict[str, list[float]] = {}
    for _, g in d.sort_values(["gameId", "orderNumber"]).groupby("gameId", sort=False):
        a, s, q = g.actionType.values, g.subType.astype(str).values, g.qualifiers.astype(str).values
        t, per, c = g.t.values, g.period.values, g.c.values
        for i in range(len(g)):
            if a[i] == "timeout":
                key = "timeout_mandatory" if "mandatory" in q[i] else "challenge" if s[i] == "challenge" else "timeout_team"
                c0 = c[i]
            elif a[i] == "period" and s[i] == "end":
                key, c0 = {1: "q1_end", 2: "half", 3: "q3_end"}.get(per[i], "q4_ot_end"), None
            else:
                continue
            j = i + 1
            while j < len(g) and a[j] in SKIP:
                j += 1
            if j >= len(g) or not (np.isfinite(t[i]) and np.isfinite(t[j])):
                continue
            start = c0 if c0 is not None else (720.0 if per[j] <= 4 else 300.0)
            ran = start - c[j] if np.isfinite(c[j]) and np.isfinite(start) else 0.0
            dt = (t[j] - t[i]) - max(0.0, ran)
            if 0 < dt < 1800:
                out.setdefault(key, []).append(float(dt))
    return out


def main(argv=None) -> int:
    years = [int(y) for y in (argv if argv is not None else sys.argv[1:])] or [2024]
    allb: dict[str, list[float]] = {}
    for y in years:
        for k, v in breaks(season(y)).items():
            allb.setdefault(k, []).extend(v)
    for k, v in sorted(allb.items()):
        q = np.percentile(v, [10, 50, 90]).round(0)
        print(f"{k:18s} n={len(v):6d} q10/50/90 = {q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
