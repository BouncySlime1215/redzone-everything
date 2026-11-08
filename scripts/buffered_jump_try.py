"""Buffered jumps on the real TV (both YouTube TV sticks scrubbed ~75 s behind live, cuts planned from the play-by-play).
Turns it on / off through the running app's settings API (undoable) and reads what it did. Writes nothing but the
setting. Only while Nick watches (the hidden stick is scrubbed with overlay keys).

    .venv/bin/python scripts/buffered_jump_try.py on          # jump_mode + buffered_jump + the 34.9 s edge prior
    .venv/bin/python scripts/buffered_jump_try.py on --eager  # ...cutting at every shared dead ball (more cuts)
    .venv/bin/python scripts/buffered_jump_try.py watch       # every 60 s: jumps/min, bj why, holds, scrubs
    .venv/bin/python scripts/buffered_jump_try.py report --since-min 20
    .venv/bin/python scripts/buffered_jump_try.py off         # buffered_jump false (jump mode stays as it was)

What to look for: data/buffer.jsonl "scrubbed" rows (each fresh tune label-read 45-130 s behind), jump rows with
why bj_snap_next / bj_long_dead and behind_s on both sides, and on screen: every cut lands on the line-up a few
seconds before a snap, never mid-play. Expected (sim, 37 NFL slates): ~1.1 plays/min shown, 0.4-1 cuts/min
snap-timed (+ break cuts); eager ~4-5 cuts/min.
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jump_try import LOG, ROOT, STATUS, post, rows, summary  # noqa: E402

BUFFER = ROOT / "data" / "buffer.jsonl"


def bj_summary(since: float, minutes: float) -> str:
    rs = [r for r in rows(since) if r.get("event") is None]
    bj = [r for r in rs if str(r.get("why", "")).startswith("bj_")]
    ok = [r for r in bj if r.get("ok") and not r.get("dry_run")]
    holds = Counter()
    for r in rows(since, STATUS):
        holds.update({k: v for k, v in (r.get("holds") or {}).items() if k.startswith("bj_")})
    sc = rows(since, BUFFER)
    scr = [r for r in sc if r.get("kind") == "scrubbed"]
    fail = Counter((r.get("why") or "?")[:60] for r in sc if r.get("kind") == "scrub_failed")
    behind = sorted(r.get("behind_s", 0) for r in scr)
    snap = sorted(r.get("h_snap_s", 0) for r in ok)
    return (f"buffered: {len(ok)}/{len(bj)} cuts ok = {len(ok) / max(minutes, 1e-9):.1f}/min | why "
            f"{dict(Counter(r['why'] for r in bj))} | snap after landing median "
            f"{snap[len(snap) // 2] if snap else '-'} s | scrubs ok {len(scr)} (behind {behind[0] if behind else '-'}"
            f"..{behind[-1] if behind else '-'} s) failed {dict(fail)} | editor holds {dict(holds)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["on", "off", "watch", "report"])
    ap.add_argument("--port", type=int, default=None, help="the app's port (default: server.port in data/config.yaml, else 8778)")
    ap.add_argument("--eager", action="store_true", help="on: cut at every shared dead ball")
    ap.add_argument("--since-min", type=float, default=None)
    a = ap.parse_args()
    if a.cmd == "on":
        post(a.port, {"jump_mode": {"enabled": True}, "delay": {"edge_prior": {"enabled": True}},
                      "buffered_jump": {"enabled": True, "eager": bool(a.eager)}})
    elif a.cmd == "off":
        post(a.port, {"buffered_jump": {"enabled": False}})
    elif a.cmd == "report":
        since = time.time() - a.since_min * 60 if a.since_min else 0.0
        rs = rows(since)
        span = a.since_min or ((rs[-1]["ts"] - rs[0]["ts"]) / 60 if len(rs) > 1 else 1.0)
        print(summary(rs, span, rows(since, STATUS)))
        print(bj_summary(since, span))
    else:
        t0 = time.time()
        while True:
            time.sleep(60)
            print(time.strftime("%H:%M"), "last 1 min:", bj_summary(time.time() - 60, 1.0))
            print("      since start:", bj_summary(t0, (time.time() - t0) / 60))
            sys.stdout.flush()


if __name__ == "__main__":
    assert LOG  # jump rows live in data/jumps.jsonl
    main()
