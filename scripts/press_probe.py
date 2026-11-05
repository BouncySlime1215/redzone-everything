#!/usr/bin/env python3
"""[press-to-TV, real hardware] Press GO NOW on the running app the way the phone does, and time each
press from the tap to the game playing on the TV. Run it on the Mac while games are live and nobody is
watching the TV (it switches the TV, mid-play allowed: GO NOW is a forced switch).

    python scripts/press_probe.py --presses 10 --gap 45            # alternates pre-tuned / cold presses
    python scripts/press_probe.py --presses 4 --kind warm --json /tmp/press.json

Each press: POST /api/action {"action": "hold", "game_id", "value": "force", "id"} (the phone's "Go now"),
then GET /api/action/<id> until "on_tv" (the app's own picture confirmation) or --timeout. After all
presses it releases the hold (back to autopilot) and prints per-press tap -> on-TV seconds, split by
whether the game was already on the hidden stick (warm) or needed a tune (cold).

Segment split: each press is joined with the app's own trace of that switch (switches.detail.trace in
data/redzone.db, read-only), so its seconds split into where they went:
    intake   tap -> the decider took it (HTTP + the engine's next tick)
    queued   waiting for the controller's lock (a switch or preload in flight)
    tune     the hidden (or visible) stick tuning (cold presses only)
    safe     held for a dead ball on screen (GO NOW is forced, so ~0)
    flip     the TV input change (SmartThings / HOME)
    resume   the game confirmed playing again after the flip
    ack      picture confirmed -> the phone's poll saw "on_tv"
Every run is saved to data/press_probe/<when>.json: northstar.py's press_to_tv_p95 line grades the real
presses there (PASS/FAIL once there are REAL_MIN of them). Talks only to the app on this machine and reads
its database; touches no stick directly. Serves: BTN-01 / press_to_tv_p95. Owner: lane 3.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEGMENTS = ("intake", "queued", "tune", "safe", "flip", "resume", "ack")


def _call(base: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


APPS = {"prime": "prime", "youtubetv": "yttv"}   # target prefix -> the "app" tag northstar's Prime sub-gate reads


def app_of(target: str | None) -> str | None:
    """"prime" / "yttv" (else the target's own prefix) for a tuner target, None when unknown."""
    if not target or ":" not in target:
        return None
    pre = target.split(":", 1)[0]
    return APPS.get(pre, pre)


def game_app(state: dict, gid: str) -> str | None:
    g = next((x for x in state.get("games") or [] if x.get("id") == gid), None)
    return app_of(((g or {}).get("where") or {}).get("target"))


def pick(state: dict, kind: str, last: str | None, app: str | None = None) -> tuple[str, str] | None:
    """(game_id, warm|cold): the standby game for a warm press, any other live game for a cold one.
    app: only games on that app ("prime" / "yttv")."""
    cur = (state.get("now") or {}).get("game_id") or (state.get("now") or {}).get("id")
    sb = state.get("standby") or {}
    live = [g["id"] for g in state.get("games") or [] if g.get("phase") == "live" and g["id"] != cur
            and (app is None or game_app(state, g["id"]) == app)]
    if kind in ("warm", "any") and sb.get("ready") and sb.get("game_id") in live:
        return sb["game_id"], "warm"
    cold = [g for g in live if g not in (sb.get("game_id"), last)]
    if kind in ("cold", "any") and cold:
        return cold[0], "cold"
    return None


def trace_for(db: Path, game_id: str, t0: float, t1: float) -> dict | None:
    """The app's trace of the press: the first user switch to game_id stored between t0 and t1."""
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT detail FROM switches WHERE to_game = ? AND kind = 'user' AND ts >= ? AND ts <= ? "
                           "ORDER BY ts LIMIT 1", (game_id, t0 - 1.0, t1)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not rows:
        return None
    try:
        return (json.loads(rows[0][0] or "{}") or {}).get("trace")
    except ValueError:
        return None


def segments(t0: float, t_on: float | None, tr: dict | None) -> dict:
    """Where one press's seconds went (see the module doc). None = that step didn't run or wasn't seen."""
    if not tr:
        return {k: None for k in SEGMENTS}
    sp = tr.get("spans") or {}
    dec, pic = tr.get("decided_at"), tr.get("picture_at")

    def r(x):
        return None if x is None else round(max(0.0, float(x)), 2)
    return {"intake": r(dec - t0) if dec is not None else None, "queued": r(sp.get("queued")),
            "tune": r(sp.get("tune")), "safe": r(sp.get("safe_wait")), "flip": r(sp.get("flip")),
            "resume": r(sp.get("resume")),
            "ack": r(t0 + t_on - pic) if pic is not None and t_on is not None else None}


def p(xs: list[float], q: float) -> float | None:
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(q * len(s) + 0.999999) - 1))], 2) if s else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the app (default: the first of :8777 / :8778 that answers)")
    ap.add_argument("--presses", type=int, default=10)
    ap.add_argument("--gap", type=float, default=45.0, help="seconds between presses (lets the hidden stick re-tune)")
    ap.add_argument("--kind", choices=("alternate", "warm", "cold"), default="alternate")
    ap.add_argument("--app", choices=sorted(set(APPS.values())), default=None,
                    help="press only games on this app (northstar grades Prime presses on their own)")
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--json", help="write the per-press rows here (default: data/press_probe/<when>.json)")
    ap.add_argument("--db", type=Path, default=ROOT / "data/redzone.db", help="the app's database (read-only)")
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
            got = pick(st, want, last, a.app) or pick(st, "any", last, a.app)
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
            time.sleep(1.0)   # the engine stores the switch row right after the picture
            seg = segments(t0, t_on, trace_for(a.db, gid, t0, time.time()))
            rows.append({"i": i, "game_id": gid, "kind": kind, "app": game_app(st, gid), "ok": bool(r.get("ok")), "tap_to_tv_s": t_on,
                         "message": r.get("message"), "at": round(t0, 2), "segments": seg})
            split = " ".join(f"{k} {v:.2f}" for k, v in seg.items() if v)
            print(f"press {i + 1}: {kind:4} {gid}: " + (f"{t_on:.2f} s" if t_on is not None else
                                                        f"not on TV after {a.timeout:.0f} s ({r.get('message')})")
                  + (f"  [{split}]" if split else ""))
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
    for app in sorted({r["app"] for r in rows if r.get("app")}):
        xs = [r["tap_to_tv_s"] for r in rows if r.get("app") == app and r["tap_to_tv_s"] is not None]
        out[app] = {"n": len(xs), "p95_s": p(xs, .95),
                    "missed": sum(1 for r in rows if r.get("app") == app and r["tap_to_tv_s"] is None)}
    out["segments"] = summarize_segments(rows)
    print(json.dumps(out, indent=1))
    path = Path(a.json) if a.json else ROOT / "data/press_probe" / time.strftime("%Y%m%d-%H%M%S.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": out, "presses": rows}, indent=1))
    print(f"saved {path}")
    return 0


def summarize_segments(rows: list[dict]) -> dict:
    """p50 / p95 of each segment, per kind (warm / cold), over the presses that reached the TV."""
    out: dict = {}
    for k in ("warm", "cold"):
        got = [r["segments"] for r in rows if r["kind"] == k and r.get("tap_to_tv_s") is not None and r.get("segments")]
        out[k] = {s: {"p50_s": p(xs, .5), "p95_s": p(xs, .95)}
                  for s in SEGMENTS if (xs := [g[s] for g in got if g.get(s) is not None])}
    return out


if __name__ == "__main__":
    sys.exit(main())
