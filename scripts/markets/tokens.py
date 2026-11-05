#!/usr/bin/env python3
"""Market ticks -> market events -> vocab v2 market-block tokens (Data Division D, markets).

Serves: the factory (event tokens) and the markets ledger. Owner: planner D; lane 1 assigns the block's
base offset in vocab v2. Contract: docs/datasources/markets/contract.md.

Market data is a model INPUT only (importance, closeness, volatility). It is never shown, never a nudge and
never a betting hook. Every output carries the tag `personal-use, not for commercial release` (plan B).

    python scripts/markets/tokens.py --in out/markets --out out/markets_tokens   # writes tokens + counts.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

TAG = "personal-use, not for commercial release"

SOURCES = ("polymarket", "kalshi", "nflverse_lines", "sbro", "odds_api", "football_data", "kaggle_odds", "other")
KINDS = ("moneyline", "spread", "total", "prop", "future")
PHASES = ("pregame", "live", "open", "close", "settled")
P_BUCKETS, JUMP_BUCKETS, DT_BUCKETS, ACT_BUCKETS = 32, 15, 24, 16
TOKENS_PER_EVENT = 6

# Emission filter: tokens count information, not clock.
MIN_JUMP_P = 0.02        # 2 probability points
MIN_JUMP_LINE = 0.5      # half a point of spread or total
HEARTBEAT_S = 300        # at most one unchanged event per 5 minutes

# Block layout (relative ids; lane 1 adds the vocab v2 base offset).
SRC0 = 0
KP0 = SRC0 + len(SOURCES)
P0 = KP0 + len(KINDS) * len(PHASES)
JUMP0 = P0 + P_BUCKETS + 1            # +1 = unknown prob
DT0 = JUMP0 + JUMP_BUCKETS
ACT0 = DT0 + DT_BUCKETS
BLOCK_SIZE = ACT0 + ACT_BUCKETS + 1   # +1 = unknown activity
JUMP_EDGES = (0.005, 0.01, 0.02, 0.04, 0.08, 0.15, 0.3)   # |delta| in prob units (line deltas / 20)


def _value(t: dict) -> float | None:
    """The tick's comparable value in prob units: p_home, or a line scaled so 0.5 pt ~ 0.025."""
    if t.get("p_home") is not None:
        return float(t["p_home"])
    if t.get("line") is not None:
        return float(t["line"]) / 20.0
    return None


def _jump_bucket(d: float | None) -> int:
    if d is None or not math.isfinite(d):
        return JUMP0 + 7                                    # 0 / unknown -> the middle bucket
    mag = sum(abs(d) >= e for e in JUMP_EDGES)             # 0..7
    return JUMP0 + 7 + (mag if d > 0 else -mag)


def _dt_bucket(dt: float | None) -> int:
    if dt is None or dt < 0 or not math.isfinite(dt):
        return DT0
    return DT0 + 1 + min(DT_BUCKETS - 2, int(math.log2(1.0 + dt) * 1.3))   # 0 s .. ~days


def _act_bucket(v: float | None) -> int:
    if v is None or not math.isfinite(v) or v < 0:
        return ACT0 + ACT_BUCKETS
    return ACT0 + min(ACT_BUCKETS - 1, int(math.log2(1.0 + v)))


def tokens(ev: dict) -> tuple[int, ...]:
    src = SOURCES.index(ev["source"]) if ev["source"] in SOURCES else SOURCES.index("other")
    kind = KINDS.index(ev["market_kind"]) if ev.get("market_kind") in KINDS else 0
    ph = PHASES.index(ev["phase"]) if ev.get("phase") in PHASES else 0
    p = ev.get("p_home")
    ptok = P0 + P_BUCKETS if p is None else P0 + min(P_BUCKETS - 1, max(0, int(float(p) * P_BUCKETS)))
    return (SRC0 + src, KP0 + kind * len(PHASES) + ph, ptok, _jump_bucket(ev.get("jump")),
            _dt_bucket(ev.get("dt_s")), _act_bucket(ev.get("volume")))


def emit(ticks: list[dict]) -> list[dict]:
    """One market's ticks (any order) -> the emitted events, each with `jump` and `dt_s` filled."""
    out: list[dict] = []
    last_v = last_ts = last_phase = None
    vol_acc = 0.0
    for t in sorted(ticks, key=lambda r: r["ts"]):
        v = _value(t)
        vol_acc += float(t.get("volume") or 0.0)
        is_line = t.get("p_home") is None and t.get("line") is not None
        thr = MIN_JUMP_LINE / 20.0 if is_line else MIN_JUMP_P
        first = last_ts is None
        moved = v is not None and last_v is not None and abs(v - last_v) >= thr
        phase_change = t.get("phase") != last_phase
        beat = not first and t["ts"] - last_ts >= HEARTBEAT_S and v != last_v
        if not (first or moved or phase_change or beat):
            continue
        ev = dict(t)
        ev["jump"] = None if (v is None or last_v is None) else v - last_v
        ev["dt_s"] = None if first else t["ts"] - last_ts
        ev["volume"] = vol_acc if vol_acc else t.get("volume")
        out.append(ev)
        last_v = v if v is not None else last_v
        last_ts, last_phase, vol_acc = t["ts"], t.get("phase"), 0.0
    return out


def run(inp: Path, out: Path) -> dict:
    by_market: dict[tuple, list[dict]] = defaultdict(list)
    n_ticks = 0
    bad_tag = 0
    for f in sorted(inp.rglob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            n_ticks += 1
            bad_tag += t.get("tag") != TAG
            by_market[(t["source"], t["league"], t.get("market_id", ""), t.get("market_kind", ""))].append(t)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict = {"tag": TAG, "ticks": n_ticks, "ticks_missing_tag": bad_tag, "by_source": {}}
    with (out / "market_events.jsonl").open("w") as fh:
        for (src, lg, mid, kind), ticks in sorted(by_market.items()):
            evs = emit(ticks)
            s = counts["by_source"].setdefault(src, {"markets": 0, "events": 0, "matched_events": 0, "leagues": {}})
            s["markets"] += 1
            s["events"] += len(evs)
            s["leagues"][lg] = s["leagues"].get(lg, 0) + len(evs)
            for ev in evs:
                s["matched_events"] += bool(ev.get("game_key"))
                fh.write(json.dumps({"source": src, "league": lg, "game_key": ev.get("game_key", ""),
                                     "market_id": mid, "ts": ev["ts"], "tokens": tokens(ev), "tag": TAG}) + "\n")
    for s in counts["by_source"].values():
        s["new_info_tokens"] = s["events"] * TOKENS_PER_EVENT    # observed prices: new information
    counts["new_info_tokens"] = sum(s["new_info_tokens"] for s in counts["by_source"].values())
    counts["derived_tokens"] = 0                                 # nothing computed is counted as new
    (out / "counts.json").write_text(json.dumps(counts, indent=1) + "\n")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", type=Path, default=Path("out/markets"))
    ap.add_argument("--out", type=Path, default=Path("out/markets_tokens"))
    a = ap.parse_args(argv)
    c = run(a.inp, a.out)
    print(json.dumps({k: v for k, v in c.items() if k != "by_source"}))
    return 0 if not c["ticks_missing_tag"] else 1


if __name__ == "__main__":
    sys.exit(main())
