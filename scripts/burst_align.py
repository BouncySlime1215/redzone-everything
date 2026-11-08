"""[burst anchor] Do YouTube TV's MediaSession bursts line up with the feed's TV timeouts?

The live lab (Nov 4) found that YTTV's media session republishes its state in bursts tied to the
content: same stream position on both sticks, exactly one hold later on a held stick. If a burst
follows a TV timeout / break by a steady gap, every burst is a delay anchor on BOTH sticks (YTTV shows
no UI text and logs no ad lines, so today it has no other). This script answers that from one night:

    python scripts/burst_align.py --probe recordings/<day>/sync_probe.jsonl \\
        --plays recordings/<day>/plays.jsonl --game <espn id> --delay 45 [--host-has <text>]

--probe takes the app's sync_probe.jsonl ("log" lists) or the lab's adwatch.jsonl ("line" rows).
--delay is the screen delay of that stream (feed wall -> picture), e.g. the night's measured lag plus
any hold. For each burst onset (sync_probe.BurstWatch) it prints the last dead ball before the burst's
real-life moment (onset - delay) and the gap, then the same for random moments as the null. A steady
gap (sd well under the null's) after timeout-type plays is the evidence to turn sync.burst_anchor on
with gap_s / gap_sd_s from here. Reads local files only; prints numbers, never addresses.
Why: Checks whether YouTube TV bursts line up with feed TV timeouts (docstring, EXP-084 commit).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.lab.timing import is_dead_ball  # noqa: E402
from redzone.sync_probe import burst_watch  # noqa: E402

BREAKISH = ("timeout", "end of", "end period", "period end", "halftime", "two-minute", "end quarter")


def probe_lines(path: Path, host_has: str | None) -> dict[str, list[str]]:
    """host -> log lines in file order (both the app's probe rows and the lab's adwatch rows)."""
    out: dict[str, list[str]] = {}
    for raw in path.read_text().splitlines():
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        host = str(r.get("host") or "?")
        if host_has and host_has not in host:
            continue
        lines = r.get("log") or ([r["line"]] if r.get("kind") == "log" and r.get("line") else [])
        out.setdefault(host, []).extend(lines)
    return out


def onsets(lines_by_host: dict[str, list[str]]) -> list[tuple[int, float]]:
    """(stick index, onset epoch) over all sticks; the index stands in for the address."""
    out = []
    for i, host in enumerate(sorted(lines_by_host)):
        w = burst_watch()
        out += [(i, t) for t, _ in w.feed(lines_by_host[host])]
    return sorted(out, key=lambda x: x[1])


def dead_balls(path: Path, game: str) -> list[tuple[float, str, bool]]:
    """(feed wall, type, timeout-like) of the game's dead balls, oldest first, one row per play."""
    seen, out = set(), []
    for raw in path.read_text().splitlines():
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if str(r.get("game")) != str(game) or r.get("play") in seen or not r.get("wall"):
            continue
        seen.add(r.get("play"))
        typ = str(r.get("type") or "")
        if is_dead_ball(str(r.get("league") or ""), typ):
            out.append((float(r["wall"]), typ, any(k in typ.lower() for k in BREAKISH)))
    return sorted(out)


def last_before(dead: list[tuple[float, str, bool]], real: float, max_s: float, breakish: bool):
    best = None
    for w, typ, br in dead:
        if w > real:
            break
        if real - w <= max_s and (br or not breakish):
            best = (w, typ)
    return best


def summary(gaps: list[float]) -> str:
    if not gaps:
        return "n 0"
    sd = statistics.pstdev(gaps) if len(gaps) > 1 else float("nan")
    return f"n {len(gaps)}  median {statistics.median(gaps):.1f} s  sd {sd:.1f} s  min {min(gaps):.1f}  max {max(gaps):.1f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--plays", required=True, type=Path)
    ap.add_argument("--game", required=True)
    ap.add_argument("--delay", required=True, type=float, help="screen delay of that stream (s)")
    ap.add_argument("--host-has", help="only sticks whose address contains this")
    ap.add_argument("--max-gap", type=float, default=240.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    ons = onsets(probe_lines(a.probe, a.host_has))
    dead = dead_balls(a.plays, a.game)
    if not ons or not dead:
        print(f"bursts {len(ons)}, dead balls {len(dead)}: nothing to line up")
        return 1
    rows = {"any": [], "timeout": []}
    for stick, t in ons:
        real = t - a.delay
        anyb, brk = last_before(dead, real, a.max_gap, False), last_before(dead, real, a.max_gap, True)
        print(f"stick {stick}  onset {t:.1f}  last dead ball "
              + (f"{real - anyb[0]:6.1f} s before ({anyb[1]})" if anyb else "none")
              + (f"  last timeout-like {real - brk[0]:6.1f} s before ({brk[1]})" if brk else ""))
        if anyb:
            rows["any"].append(real - anyb[0])
        if brk:
            rows["timeout"].append(real - brk[0])
    rng = random.Random(a.seed)
    lo, hi = dead[0][0], dead[-1][0]
    null = {"any": [], "timeout": []}
    for _ in range(2000):
        real = rng.uniform(lo, hi)
        for k, br in (("any", False), ("timeout", True)):
            b = last_before(dead, real, a.max_gap, br)
            if b:
                null[k].append(real - b[0])
    for k in ("any", "timeout"):
        print(f"{k:8s} bursts: {summary(rows[k])}")
        print(f"{k:8s} null:   {summary(null[k])}  (random moments; hit rate {len(null[k]) / 2000:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
