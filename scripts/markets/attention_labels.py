#!/usr/bin/env python3
"""Polymarket ticks -> DERIVED market-attention labels for Watch Value (crew G), Nick 11:30 AM Nov 5.

Per game minute: how far the market moved (|dp| over 1 and 5 minutes) and whether the game was live; per game:
total traded volume, live swing count and live range. Derived numbers only: no price levels, no trades, no
wallets. Kalshi is not used (red). Personal-use, not for commercial release.

    python scripts/markets/attention_labels.py --in out/markets --out out/attention
      -> out/attention/market_minutes.csv  (game_key, minute_ts, live, min_from_start, abs_dp_1m, abs_dp_5m)
         out/attention/market_games.csv    (game_key, league, start_ts, volume_usd, live_minutes, swings_3pt, live_range)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

TAG = "personal-use, not for commercial release"
SWING = 0.03


def minutes(ticks: list[dict]) -> list[dict]:
    """One market's 1-minute ticks -> per-minute derived rows."""
    t = sorted(ticks, key=lambda r: r["ts"])
    out = []
    for i, r in enumerate(t):
        p = r["p_home"]
        d1 = abs(p - t[i - 1]["p_home"]) if i and r["ts"] - t[i - 1]["ts"] <= 120 else None
        j = i
        while j > 0 and r["ts"] - t[j - 1]["ts"] <= 300:
            j -= 1
        d5 = max(abs(p - x["p_home"]) for x in t[j:i + 1]) if j < i else None
        out.append({"minute_ts": int(r["ts"]), "live": int(r.get("phase") == "live"),
                    "abs_dp_1m": None if d1 is None else round(d1, 4), "abs_dp_5m": None if d5 is None else round(d5, 4)})
    return out


def run(inp: Path, out: Path) -> dict:
    by_mkt: dict[tuple, list[dict]] = defaultdict(list)
    vol: dict[str, dict] = {}
    for f in sorted(inp.rglob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if f.name == "markets.jsonl":
                vol[r["market_id"]] = r
            elif r.get("source") == "polymarket" and r.get("p_home") is not None and r.get("game_key"):
                by_mkt[(r["league"], r["game_key"], r["market_id"])].append(r)
    out.mkdir(parents=True, exist_ok=True)
    n_min = n_games = 0
    with open(out / "market_minutes.csv", "w", newline="") as fm, open(out / "market_games.csv", "w", newline="") as fg:
        wm = csv.writer(fm)
        wg = csv.writer(fg)
        wm.writerow(["game_key", "minute_ts", "live", "min_from_start", "abs_dp_1m", "abs_dp_5m", "tag"])
        wg.writerow(["game_key", "league", "start_ts", "volume_usd", "live_minutes", "swings_3pt", "live_range", "tag"])
        for (lg, gk, mid), ticks in sorted(by_mkt.items()):
            rows = minutes(ticks)
            v = vol.get(mid, {})
            start = v.get("start_ts")
            for m in rows:
                mfs = None if start is None else round((m["minute_ts"] - start) / 60.0, 1)
                wm.writerow([gk, m["minute_ts"], m["live"], mfs, m["abs_dp_1m"], m["abs_dp_5m"], TAG])
            live = [t["p_home"] for t in ticks if t.get("phase") == "live"]
            swings = sum(1 for m in rows if m["live"] and (m["abs_dp_1m"] or 0) >= SWING)
            wg.writerow([gk, lg, start, v.get("volume_usd"), len(live), swings,
                         round(max(live) - min(live), 4) if live else None, TAG])
            n_min += len(rows)
            n_games += 1
    counts = {"games": n_games, "labeled_minutes": n_min, "with_volume": sum(1 for k in by_mkt if k[2] in vol),
              "tag": TAG}
    (out / "counts.json").write_text(json.dumps(counts, indent=1) + "\n")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", type=Path, default=Path("out/markets"))
    ap.add_argument("--out", type=Path, default=Path("out/attention"))
    a = ap.parse_args(argv)
    print(json.dumps(run(a.inp, a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
