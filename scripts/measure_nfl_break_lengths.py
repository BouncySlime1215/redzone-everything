"""[Data F] NFL break lengths from nflverse play-by-play wall clocks (time_of_day), 2018-2024.

    pip install pandas pyarrow    # not in requirements.txt: a lab/Kaggle script, never the live app
    python scripts/measure_nfl_break_lengths.py DIR    # DIR holds play_by_play_YYYY.parquet + games.csv
    # merges "nfl_nflverse" into redzone/scoring/broadcast/break_lengths.json

Sources: nflverse-data releases (pbp) and nflverse/nfldata games.csv. Green (CC-BY 4.0 / MIT-style
open data; credit nflverse); numbers only. Seasons stop at 2024 so nothing touches the NFL held-out
window (first held 2025-11-13).

A break = wall time of the stopping play to wall time of the next play that restarts the game
(time_of_day is the play's own wall clock, so a length includes ~5 s of the stopping play).
Slot proxy for network (games.csv has no network column): primetime = Thu, Mon, Sat or a Sunday
kickoff at or after 19:00 ET (one national broadcaster); sunday_day = Sunday afternoon (CBS/FOX
regional windows).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "redzone" / "scoring" / "broadcast" / "break_lengths.json"
SEASONS = range(2018, 2025)
QS = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
MAX_S = 1500.0


def _secs(t) -> float | None:
    if not isinstance(t, str) or len(t) < 8:
        return None
    t = t[-8:] if "T" not in t else t.split("T")[1][:8]
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def slot(g) -> str:
    wd, gt = str(g["weekday"]), str(g["gametime"] or "13:00")
    hh = int(gt.split(":")[0]) if gt[:2].isdigit() else 13
    if wd in ("Thursday", "Monday", "Saturday", "Friday", "Tuesday", "Wednesday") or (wd == "Sunday" and hh >= 19):
        return "primetime"
    return "sunday_day"


def kinds_of(row) -> list[str]:
    d = str(row.get("desc") or "")
    out = []
    if "Two-Minute Warning" in d:
        out.append("two_minute_warning")
    if row.get("timeout") == 1:
        out.append("team_timeout")
    if row.get("replay_or_challenge") == 1:
        out.append("review")
    if row.get("play_type") == "punt":
        out.append("punt")
    if row.get("touchdown") == 1 or row.get("field_goal_result") == "made":
        out.append("score_break")
    if row.get("interception") == 1 or row.get("fumble_lost") == 1:
        out.append("turnover")
    return out


def main() -> int:
    import pandas as pd
    d = Path(sys.argv[1])
    games = pd.read_csv(d / "games.csv")
    slots = {r["game_id"]: slot(r) for r in games.to_dict("records")}
    rows = []
    cols = ["game_id", "play_id", "qtr", "desc", "play_type", "time_of_day", "timeout", "replay_or_challenge",
            "touchdown", "field_goal_result", "interception", "fumble_lost", "season_type", "quarter_end",
            "quarter_seconds_remaining"]
    for y in SEASONS:
        f = d / f"play_by_play_{y}.parquet"
        if not f.exists():
            f = d / f"pbp_{y}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f, columns=cols)
        for gid, g in df.groupby("game_id", sort=False):
            g = g.sort_values("play_id")
            recs = g.to_dict("records")
            ts, prev, off = [], None, 0.0
            for r in recs:
                s = _secs(r["time_of_day"])
                if s is not None and prev is not None and s + off < prev - 6 * 3600:
                    off += 86400.0   # crossed midnight UTC
                ts.append(None if s is None else s + off)
                if s is not None:
                    prev = s + off
            sl = slots.get(gid, "unknown")
            last = None
            for i, r in enumerate(recs):
                if ts[i] is not None:
                    last = ts[i]
                t0 = ts[i] if ts[i] is not None else last   # markers (2-min warning, END QUARTER) carry no wall time
                if t0 is None:
                    continue
                ks = kinds_of(r)
                if r.get("quarter_end") == 1 and r.get("qtr") in (1, 3):
                    ks.append("end_period")
                elif r.get("quarter_end") == 1 and r.get("qtr") == 2:
                    ks.append("halftime")
                # nflverse drops the two-minute-warning marker: a Q2/Q4 play starting above 2:00 whose
                # next snap starts at 2:00-1:55 had the warning between them
                q = r.get("qtr")
                if q in (2, 4) and (r.get("quarter_seconds_remaining") or 0) > 120 and i + 1 < len(recs):
                    nq = recs[i + 1].get("quarter_seconds_remaining")
                    if nq is not None and 115 <= nq <= 120 and recs[i + 1].get("qtr") == q:
                        ks.append("two_minute_warning")
                if not ks:
                    continue
                want_kick = "score_break" in ks
                for j in range(i + 1, len(recs)):
                    nr = recs[j]
                    if ts[j] is None or nr.get("play_type") in (None, "no_play") and "Timeout" in str(nr.get("desc")):
                        continue
                    if want_kick and nr.get("play_type") not in ("kickoff",):
                        if nr.get("play_type") in ("extra_point",) or "TWO-POINT" in str(nr.get("desc")).upper():
                            continue
                    if nr.get("play_type") is None and not want_kick:
                        continue
                    ln = ts[j] - t0
                    if 0 < ln <= MAX_S:
                        for k in ks:
                            rows.append((k, sl, ln))
                    break
    acc = defaultdict(list)
    for k, sl, ln in rows:
        acc[(k, sl)].append(ln)
        acc[(k, "*")].append(ln)
    tab: dict = {}
    for (k, sl), v in sorted(acc.items()):
        v.sort()
        n = len(v)
        tab.setdefault(k, {})[sl] = {"n": n, **{f"q{int(q * 100):02d}" if q < 0.1 else f"q{int(q * 100)}":
                                               v[min(n - 1, int(q * n))] for q in QS}}
    doc = json.loads(OUT.read_text())
    doc["nfl_nflverse"] = {"source": "nflverse pbp time_of_day 2018-2024 + nfldata games.csv (slot proxy)",
                           "license": "nflverse open data (credit nflverse); derived numbers only; personal-use, not for commercial release",
                           "definition": "stopping play wall time -> next restarting play wall time (includes the stopping play itself)",
                           "table": tab}
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"merged nfl_nflverse from {len(rows)} breaks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
