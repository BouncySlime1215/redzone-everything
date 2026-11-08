"""Jump mode on the real TV: turn it on / off through the running app's settings API (undoable), and watch
what it does from data/jumps.jsonl (one row per jump: from, to, why, plays seen, snap wait, cost, ok, path,
and the hold reasons counted since the previous jump). Writes nothing but the setting.

    .venv/bin/python scripts/jump_try.py on       # jump_mode.enabled true (+ the 34.9 s YouTube TV edge prior)
    .venv/bin/python scripts/jump_try.py watch    # live summary every 60 s: jumps/min, ok rate, why, holds
    .venv/bin/python scripts/jump_try.py report   # one summary of the whole file (or --since-min N)
    .venv/bin/python scripts/jump_try.py off      # jump_mode.enabled false (the normal decider)
"""
import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "jumps.jsonl"


def post(port: int, changes: dict) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings", data=json.dumps({"changes": changes}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        print(json.loads(r.read().decode()))


def rows(since: float) -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", 0) >= since:
            out.append(r)
    return out


def standby_summary(ss: list[dict]) -> str:
    """The "standby" rows: how often the hidden stick held a ready game, and why preloads were refused."""
    if not ss:
        return "standby: no status rows yet"
    ready = sum(1 for r in ss if r.get("standby_ready"))
    pre = Counter(r.get("preload", "?") for r in ss)
    last = ss[-1]
    return (f"standby ready {ready}/{len(ss)} checks | preload {dict(pre)} | now: {last.get('standby')} "
            f"ready={last.get('standby_ready')} want={last.get('want')} ({last.get('preload')})")


def summary(rs: list[dict], minutes: float) -> str:
    ss = [r for r in rs if r.get("event") == "standby"]
    rs = [r for r in rs if r.get("event") is None]
    if not rs:
        return "no jumps yet | " + standby_summary(ss)
    ok = [r for r in rs if r.get("ok") and not r.get("dry_run")]
    why, holds, fail, path = Counter(), Counter(), Counter(), Counter()
    for r in rs:
        why[r.get("why", "?")] += 1
        holds.update(r.get("holds") or {})
        if not r.get("ok"):
            fail[r.get("fail", "?") + (f" ({r['fail_detail']})" if r.get("fail") == "other" and r.get("fail_detail")
                                       else "")] += 1
        else:
            path[r.get("path", "?")] += 1
    took = sorted(r.get("took_s", 0) for r in ok)
    med = took[len(took) // 2] if took else 0
    return (f"{len(rs)} jump tries, {len(ok)} on screen in {minutes:.1f} min = {len(ok) / max(minutes, 1e-9):.1f}/min "
            f"(median {med:.1f} s each) | why {dict(why)} | failed {dict(fail)} | path {dict(path)} | "
            f"waited because (ticks) {dict(holds)} | " + standby_summary(ss))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["on", "off", "watch", "report"])
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--since-min", type=float, default=None)
    ap.add_argument("--no-edge-prior", action="store_true", help="on: leave delay.edge_prior as it is")
    a = ap.parse_args()
    if a.cmd == "on":
        ch = {"jump_mode": {"enabled": True}}
        if not a.no_edge_prior:
            ch["delay"] = {"edge_prior": {"enabled": True}}
        post(a.port, ch)
    elif a.cmd == "off":
        post(a.port, {"jump_mode": {"enabled": False}})
    elif a.cmd == "report":
        since = time.time() - a.since_min * 60 if a.since_min else 0.0
        rs = rows(since)
        span = ((rs[-1]["ts"] - rs[0]["ts"]) / 60 if len(rs) > 1 else 1.0) if not a.since_min else a.since_min
        print(summary(rs, span))
    else:
        t0 = time.time()
        while True:
            time.sleep(60)
            print(time.strftime("%H:%M"), "last 1 min:", summary(rows(time.time() - 60), 1.0))
            print("      since start:", summary(rows(t0), (time.time() - t0) / 60))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
