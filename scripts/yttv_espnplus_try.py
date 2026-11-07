"""Stick test: find a LIVE ESPN+ event in YouTube TV, tune it, then check the scrub holds (tuners.espnplus_yttv).

    python scripts/yttv_espnplus_try.py [hidden stick ip:port | auto] [--game ESPN_ID] [--scrub 60] [--again 300]
                                        [--home NAME --away NAME] [--prove-code] [--allow-app-active]

HIDDEN STICK ONLY: it opens YouTube TV search, moves focus, plays video and scrubs. The app's /api/state must say
this stick is not the one on screen, and auto-switching must be paused (or pass --allow-app-active), else it refuses.

1. Picks the game: --game, else the first LIVE game the app routes to ESPN+ (or --home/--away by hand).
2. Finds it in YouTube TV (redzone/tuners/yttv_event.hunt_event): a LIVE tile naming both teams, selected, and the
   media session must then name both teams. No match = "found": false and nothing else happens (fail closed).
   The autoplay prompt is answered "Ask later" only.
3. Scrub: yttv_seek.enter_buffer --scrub seconds with the overlay label reader, so behind_live_s is MEASURED; with
   --again S it waits S seconds and reads once more (each read adds ~7.5 s). "holds" = the second read is not
   more than 10 s closer to live than the first (a snap back to live reads ~0).
4. --prove-code: after the scrub, collect the event's watch-code candidates and prove one (ESPN channel first, then
   the candidate: the media session must come back to both teams). It only PRINTS a proven code; nothing is saved.

Prints one JSON object. Screenshots are read for text only and deleted at once; the stick address is not printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = ("http://127.0.0.1:8778/api/state", "http://127.0.0.1:8777/api/state")
SNAP_TOL_S = 10.0


def app_state() -> dict | None:
    for url in API:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.load(r)
        except Exception:
            continue
    return None


def on_screen(st: dict, host: str) -> bool | None:
    """True/False when /api/state says which stick is on screen; None when it can't tell."""
    health = st.get("health") or {}
    agent = (health.get("agent") or {}).get("on_screen")
    ip = host.split(":")[0]
    if agent:
        return agent.split(":")[0] == ip
    for s in health.get("sticks") or []:
        if str(s.get("host", "")).split(":")[0] == ip:
            role = str(s.get("role") or "").lower()
            return any(w in role for w in ("screen", "visible", "active"))
    return None


def hidden_stick(st: dict | None) -> str:
    """The one stick /api/state says is NOT on screen, or '' when that isn't clear."""
    hosts = [str(s.get("host") or "") for s in ((st or {}).get("health") or {}).get("sticks") or [] if s.get("host")]
    hidden = [h for h in hosts if on_screen(st, h) is False]
    return hidden[0] if len(hosts) == 2 and len(hidden) == 1 else ""


def pick_game(st: dict | None, game: str) -> dict | None:
    games = (st or {}).get("games") or []
    if game:
        return next((g for g in games if str(g.get("id")) == game), None)
    live = [g for g in games if g.get("phase") == "live" and "ESPN+" in str((g.get("where") or {}).get("label", ""))]
    return live[0] if live else None


def holds(first: float | None, second: float | None) -> bool | None:
    """The scrub held = the second read is not closer to live than the first (minus SNAP_TOL_S). Each read
    freezes the picture ~7.5 s, so a held stick reads a little FURTHER back; a snap back to live reads ~0."""
    if first is None or second is None:
        return None
    return second >= first - SNAP_TOL_S


async def main(a) -> dict:
    from redzone.channels import ChannelMap
    from redzone.discovery.yttv import _connect
    from redzone.screen_ocr import prepare
    from redzone.tuners import yttv_seek as ys
    from redzone.tuners.tuners import _ocr_observer
    from redzone.tuners.yttv_event import event_spec, hunt_event, prove_event_code

    out: dict = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "found": False, "tuned": False, "scrub": None}
    st = app_state()
    if a.stick == "auto":
        a.stick = hidden_stick(st)
        if not a.stick:
            out["refused"] = "couldn't tell the hidden stick from /api/state; pass its ip:port"
            return out
    if st is not None:
        vis = on_screen(st, a.stick)
        if vis is not False and not a.hidden_unknown_ok:
            out["refused"] = ("this stick is ON SCREEN" if vis else "can't tell from /api/state which stick is on "
                              "screen (pass --hidden-unknown-ok only if you know this one is hidden)")
            return out
        paused = bool((st.get("mode") or {}).get("paused"))
        if not paused and not a.allow_app_active:
            out["refused"] = "app auto-switching is not paused (pause it, or pass --allow-app-active)"
            return out
    elif not a.hidden_unknown_ok:
        out["refused"] = "the app's /api/state didn't answer, so the hidden stick can't be confirmed"
        return out
    g = pick_game(st, a.game)
    home = a.home or (((g or {}).get("home") or {}).get("name") or "")
    away = a.away or (((g or {}).get("away") or {}).get("name") or "")
    out["game"] = {"id": (g or {}).get("id") or a.game, "matchup": (g or {}).get("matchup") or f"{away} at {home}",
                   "route_now": ((g or {}).get("where") or {}).get("label")}
    spec = event_spec(str(out["game"]["id"] or "manual"), home, away)
    if spec is None:
        out["reason"] = "no live ESPN+ game (pass --game or --home/--away), or the teams can't be told apart by name"
        return out
    prep = await asyncio.to_thread(prepare)
    if prep.status != "ready":
        out["reason"] = f"Vision OCR helper not ready ({prep.status})"
        return out
    stick = await _connect(a.stick)
    if stick is None:
        out["reason"] = "stick not reachable over adb"
        return out
    try:
        profile = a.profile
        if not profile:
            from redzone.config import Config
            profile = (Config().get("providers.profiles", {}) or {}).get("youtubetv", "") or ""
        lounge = None
        if a.prove_code:
            from redzone.discovery import lounge as lounge_mod
            lounge = lounge_mod.now_playing
        hunt = await hunt_event(stick, _ocr_observer(stick), spec, profile=profile,
                                collect_codes=a.prove_code, lounge=lounge)
        out.update(hunt.row())
        out["tuned"] = hunt.status == "confirmed"
        if not out["tuned"]:
            return out
        if a.scrub > 0:
            from redzone.tuners.yttv_label import LabelProbe
            await asyncio.sleep(3.0)   # let the live edge settle before the anchor probe
            probe = LabelProbe()
            armed = probe.arm(stick)
            kw = {"read_behind": probe} if armed else {}
            r = await ys.enter_buffer(stick, a.scrub, read_overlay=probe.overlay_reader if armed else ys.ocr_overlay,
                                      still_hidden=lambda: True, **kw)
            row = r.row()
            scrub = {"ok": r.ok, "asked_s": a.scrub, "behind_live_s": row.get("behind_live_s"),
                     "measured": r.measured, "label_armed": armed, "reason": r.reason, "took_s": row.get("took_s")}
            if armed and r.ok and a.again > 0:
                await asyncio.sleep(a.again)
                p2 = await probe.probe(stick)
                scrub["again_after_s"] = a.again
                scrub["again_behind_live_s"] = None if p2.behind_live_s is None else round(p2.behind_live_s, 2)
                scrub["again_reason"] = p2.reason
                scrub["holds"] = holds(r.behind_s, p2.behind_live_s)
            out["scrub"] = scrub
        if a.prove_code:
            cm = ChannelMap()
            decoy = cm.codes.get("espn", "")
            proofs = []
            for cand in hunt.candidates[:3]:
                ok, why = await prove_event_code(stick, spec, cand["code"], decoy)
                proofs.append({"source": cand["source"], "proven": ok, "detail": why})
                if ok:
                    out["proven_code"] = cand["code"]
                    break
            out["code_proofs"] = proofs
        return out
    finally:
        await stick.close()


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stick", nargs="?", default="auto", help="the HIDDEN stick, ip:port (default: auto, from /api/state)")
    p.add_argument("--game", default="", help="ESPN game id (default: first live game the app routes to ESPN+)")
    p.add_argument("--home", default="", help="home team name as YouTube TV shows it (overrides the app)")
    p.add_argument("--away", default="", help="away team name")
    p.add_argument("--scrub", type=float, default=60.0, help="seconds behind live to scrub (0 = skip the scrub)")
    p.add_argument("--again", type=float, default=300.0, help="read the label again after this many seconds (0 = no)")
    p.add_argument("--prove-code", action="store_true", help="after the scrub, prove a per-event watch code")
    p.add_argument("--profile", default="", help="YouTube TV profile (default: providers.profiles.youtubetv)")
    p.add_argument("--allow-app-active", action="store_true", help="run even though auto-switching isn't paused")
    p.add_argument("--hidden-unknown-ok", action="store_true", help="run even if /api/state can't say which stick is hidden")
    a = p.parse_args(argv)
    res = asyncio.run(main(a))
    print(json.dumps(res, indent=1, default=str))
    return 0 if res.get("tuned") and (a.scrub <= 0 or (res.get("scrub") or {}).get("ok")) else 1


if __name__ == "__main__":
    sys.exit(cli())
