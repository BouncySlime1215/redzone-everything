#!/usr/bin/env python3
"""[press-to-TV, real hardware] Press GO NOW on the running app the way the phone does, and time each
press from the tap to the game playing on the TV. Run it on the Mac while games are live and nobody is
watching the TV (it switches the TV, mid-play allowed: GO NOW is a forced switch).

    python scripts/press_probe.py --presses 10 --gap 45            # alternates pre-tuned / cold presses
    python scripts/press_probe.py --presses 4 --kind warm --json /tmp/press.json

Each press: POST /api/action {"action": "hold", "game_id", "value": "force", "id"} (the phone's "Go now"),
then GET /api/action/<id> until "on_tv" (the app's own picture confirmation) or --timeout. After all
presses it releases the hold (back to autopilot) and prints per-press tap -> on-TV seconds, split by
whether the game was already on the hidden stick (warm) or needed a tune (cold). The app's own trace of
each press (queued / tune / flip / resume spans) lands in data/traces.jsonl, which the live-night
collector summarizes (press_spans). Talks only to the app on this machine; touches no stick directly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid


def _call(base: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def pick(state: dict, kind: str, last: str | None) -> tuple[str, str] | None:
    """(game_id, warm|cold): the standby game for a warm press, any other live game for a cold one."""
    cur = (state.get("now") or {}).get("game_id") or (state.get("now") or {}).get("id")
    sb = state.get("standby") or {}
    live = [g["id"] for g in state.get("games") or [] if g.get("phase") == "live" and g["id"] != cur]
    if kind in ("warm", "any") and sb.get("ready") and sb.get("game_id") in live:
        return sb["game_id"], "warm"
    cold = [g for g in live if g not in (sb.get("game_id"), last)]
    if kind in ("cold", "any") and cold:
        return cold[0], "cold"
    return None


def p(xs: list[float], q: float) -> float | None:
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(q * len(s) + 0.999999) - 1))], 2) if s else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the app (default: the first of :8777 / :8778 that answers)")
    ap.add_argument("--presses", type=int, default=10)
    ap.add_argument("--gap", type=float, default=45.0, help="seconds between presses (lets the hidden stick re-tune)")
    ap.add_argument("--kind", choices=("alternate", "warm", "cold"), default="alternate")
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--json", help="write the per-press rows here")
    a = ap.parse_args(argv)
    for u in ([a.url] if a.url else ["http://127.0.0.1:8777", "http://127.0.0.1:8778"]):
        try:
            st0 = _call(u, "/api/state", timeout=3.0)
            a.url = u
            break
        except OSError:
            continue
    else:
        print("the app isn't answering on :8777 or :8778 (pass --url)")
        return 2
    if (st0.get("mode") or {}).get("dry_run"):
        print("the app is in dry run: GO NOW wouldn't touch the TV; nothing pressed")
        return 2
    was_paused = bool((st0.get("mode") or {}).get("paused"))
    rows, last = [], None
    try:
        for i in range(a.presses):
            want = a.kind if a.kind != "alternate" else ("warm" if i % 2 == 0 else "cold")
            st = _call(a.url, "/api/state")
            got = pick(st, want, last) or pick(st, "any", last)
            if got is None:
                print("no live game to press (other than the one on screen): stopping")
                break
            gid, kind = got
            cid = uuid.uuid4().hex[:16]
            t0 = time.time()
            r = _call(a.url, "/api/action", {"action": "hold", "game_id": gid, "value": "force", "id": cid})
            on, t_on = bool(r.get("on_tv")), None
            while r.get("ok") and not on and time.time() - t0 < a.timeout:
                time.sleep(0.1)
                r = _call(a.url, f"/api/action/{cid}")
                on = bool(r.get("on_tv"))
            t_on = round(time.time() - t0, 2) if on else None
            rows.append({"i": i, "game_id": gid, "kind": kind, "ok": bool(r.get("ok")), "tap_to_tv_s": t_on,
                         "message": r.get("message"), "at": round(t0, 2)})
            print(f"press {i + 1}: {kind:4} {gid}: " + (f"{t_on:.2f} s" if t_on is not None else
                                                        f"not on TV after {a.timeout:.0f} s ({r.get('message')})"))
            last = gid
            time.sleep(a.gap)
    finally:
        try:
            _call(a.url, "/api/action", {"action": "release", "id": uuid.uuid4().hex[:16]})
            if was_paused:   # release resumes autopilot: put the pause back the way we found it
                _call(a.url, "/api/action", {"action": "pause", "value": True, "id": uuid.uuid4().hex[:16]})
                print("released, and paused again as it was")
            else:
                print("released: back on autopilot")
        except Exception as e:  # the summary still prints
            print(f"release failed: {e}", file=sys.stderr)
    out = {}
    for k in ("warm", "cold"):
        xs = [r["tap_to_tv_s"] for r in rows if r["kind"] == k and r["tap_to_tv_s"] is not None]
        out[k] = {"n": len(xs), "p50_s": p(xs, .5), "p95_s": p(xs, .95), "within_3s": sum(x <= 3 for x in xs),
                  "missed": sum(1 for r in rows if r["kind"] == k and r["tap_to_tv_s"] is None)}
    allx = [r["tap_to_tv_s"] for r in rows if r["tap_to_tv_s"] is not None]
    out["all"] = {"n": len(allx), "p50_s": p(allx, .5), "p95_s": p(allx, .95)}
    print(json.dumps(out, indent=1))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"summary": out, "presses": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
