"""PRIME-PARK acceptance on the real sticks and TV (10 minutes at most, TV muted): PARK's Prime steps through the APP's
controller, on any Prime Video title, live or on demand (a replay needs no live game).

    python scripts/park_prime_accept.py --gti <GTI> --yttv espn [--home LAL --away GSW] [--hold 300]

Checks, in this order (the four unknowns first):
  1. EPOCH     does the Prime media session give an epoch position (the frame's wall-clock stamp) on this title?
  2. BACK      after a Prime pause, does BACK leave the player? (PARK never sends it; this is the one time it is sent)
  3. FOCUS     after the flip to the Prime stick, which app holds audio focus on each stick (role names only)
  4. AFTER-YT  Prime after YouTube TV on the same stick: did the tuner confirm, how long did it take, was it playing
then: park (pause) and hold `--hold` s (re-read every 15 s), three short commanded pauses for primegrade.pauses, resume,
flip (park_jump), read the position (E = wall - epoch) and, with --home/--away, the score bug (numbers only), and jump
back to the stick that was on screen. At the end every paused stick is resumed (park_finish).

Thursday Night Football (Prime, PARK-NFL), the first five minutes of the game, on the hidden stick with the TV muted:
    python scripts/park_prime_accept.py --gti <TNF GTI> --yttv espn --home NYG --away WSH --hold 60
  must show: CHECK_1_EPOCH epoch=True on LIVE football (one Nov 4 stick read a counter instead); CHECK_BUG read=True
  with a football clock, period and down (down="3&7" style; the bug parser's _DOWN); one park held (CHECK_HOLD
  paused_share near 1) and one jump (CHECK_JUMP outcome=ok). A football park sits in the 25-40 s between plays, so
  a park point almost always exists; the run does not judge Rule #1 (the feed is not read here).

Prints numbers and status words only: never an address, a device id, an account or a frame. Rows for
redzone/lab/primegrade (pauses, epoch share) go to --out with sticks named by HDMI input. The flags are set in memory
only for this run (park.enabled, park.prime.enabled, sound_guard.*); data/config.yaml is not written.

Agents never run this (CLAUDE.md: no stick or TV scripts). It refuses without data/.agent_hw_ok (Nick makes it), with
the app running, with a sound incident open, or without silence read back. Nothing here buys, rents or subscribes: the
Prime tuner's purchase-screen guard stays in force, and a title that asks for money is a failed tune.
Why: Prime Video carries TNF and Friday NBA; PARK's Prime steps are proven only in fake-stick tests until this runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PERMISSION = ROOT / "data" / ".agent_hw_ok"
BUDGET_S = 600.0
FLAGS = {"mode": {"dry_run": False}, "park": {"enabled": True, "prime": {"enabled": True}},
         "sound_guard": {"enabled": True, "must_be_silent": True}}


def say(word: str, **nums) -> None:
    """One status line: a word, then numbers (and short status words) only."""
    print(word + ("  " + "  ".join(f"{k}={v}" for k, v in nums.items()) if nums else ""), flush=True)


async def ms_row(stick, label: str, pkg: str | None, channel: str, tune_t: float | None) -> dict:
    """One primegrade row: {t, stick, app, channel, uptime_ms, ms: {state, position, updated, speed}, tune_t}."""
    from redzone.sync_edge import parse_uptime
    from redzone.tuners.adb import parse_playback
    from redzone.tuners.yttv_timing import EDGE_READ
    t0 = time.time()
    r = await stick.shell(EDGE_READ, timeout=4)
    t = (t0 + time.time()) / 2.0
    text = str(getattr(r, "out", "") or "")
    ms = parse_playback(text, pkg)
    up = parse_uptime(text)
    return {"t": round(t, 3), "stick": label, "app": "prime", "channel": channel,
            "uptime_ms": None if up is None else int(up * 1000),
            "ms": {k: ms.get(k) for k in ("state", "position", "updated", "speed")}, "tune_t": tune_t}


async def focus_of(stick, roles: dict) -> str:
    """Which app holds audio focus on this stick, as a role name ("prime", "youtubetv", "other", "none")."""
    try:
        r = await stick.shell("dumpsys audio | grep -A4 'Audio Focus stack'", timeout=5)
    except Exception:
        return "unread"
    text = str(getattr(r, "out", "") or "")
    for role, pkg in roles.items():
        if pkg and pkg in text:
            return role
    return "other" if "pack:" in text else "none"


async def run(a) -> int:
    from redzone import control as ctl
    from redzone.channels import ChannelMap
    from redzone.config import Config, _deep_merge
    from redzone.noggin import park_prime
    from redzone.platform.sound_guard import MARKER, SoundGuard
    from redzone.screen.scorebug import clock_seconds
    import park_hands_live as phl
    t_start = time.monotonic()
    if not PERMISSION.exists():
        say("REFUSED", why="no_hw_permission")
        return 2
    if MARKER.exists():
        say("REFUSED", why="sound_incident_open")
        return 4
    if phl.app_running():
        say("REFUSED", why="app_running")
        return 2
    cfg = Config()
    cfg.data = _deep_merge(cfg.data, FLAGS)                         # in memory only
    c = ctl.Controller(cfg, ChannelMap(ROOT / "data" / "channels.json"))
    c.load_devices()
    c.tv.sound_settings = lambda k, fb=None: cfg.get(k, fb)
    c.tv.sound_guard = g = SoundGuard(c.tv)
    await g.silence()
    if await g.check("park_prime_accept start") != "ok":
        say("REFUSED", why="silence_not_read_back")
        return 4
    for st in c.sticks:
        if not await st.connect():
            say("REFUSED", why="stick_not_answering", stick=c._stick_label(st.host))
            return 2
    inp = (g.last or {}).get("input")
    on = next((i for i, st in enumerate(c.sticks) if c.hdmi.get(st.host) == inp), None)
    if on is None:
        say("REFUSED", why="screen_stick_unknown")
        return 2
    c.active = on
    why = c.park_why()
    if why:
        say("REFUSED", why=why.replace(" ", "_")[:60])
        return 2
    hid, vis = c.hidden, c.main
    H, V = c._stick_label(hid.host), c._stick_label(vis.host)
    hands = c.park_hands()
    target, yt = f"prime:{a.gti}", f"youtubetv:{a.yttv}"
    pkg = (hid.packages or {}).get("prime")
    roles = {"prime": pkg, "youtubetv": (hid.packages or {}).get("youtubetv")}
    if a.home and a.away:
        hands.bug_ctx[target] = (a.home, a.away, lambda per: [])
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, commands, verdict = [], [], {}
    left = lambda: BUDGET_S - (time.monotonic() - t_start)          # noqa: E731

    async def stop_if_sound(step) -> bool:
        if step.outcome == "stop" or c.park_why():
            say("SOUND_STOP", step=step.step, why=(step.why or c.park_why())[:60].replace(" ", "_"))
            return True
        return False

    async def sample(seconds: float, every: float = 1.0, tune_t=None) -> None:
        end = time.time() + seconds
        while time.time() < end and left() > 20:
            rows.append(await ms_row(hid, H, pkg, a.gti[-6:], tune_t))
            await asyncio.sleep(every)

    rc = 0
    try:
        # setup for check 4: YouTube TV in front on the hidden stick
        s0 = await c.park_tune(yt)
        say("SETUP_YTTV", outcome=s0.outcome, took_s=s0.took_s)
        if await stop_if_sound(s0):
            return 4
        # check 4 (and 1): the Prime tune right after YouTube TV
        t_tune = time.time()
        s1 = await c.park_tune(target)
        pb = await hid.playback(pkg)
        verdict["4_after_yt"] = {"outcome": s1.outcome, "took_s": s1.took_s, "playing": pb.get("state") == "playing",
                                 "tune_s": s1.numbers.get("tune_s")}
        if await stop_if_sound(s1):
            return 4
        if not s1.ok:
            say("CHECK4_AFTER_YT", **verdict["4_after_yt"])
            say("STOP", why="prime_tune_failed")
            return 3
        r = await ms_row(hid, H, pkg, a.gti[-6:], t_tune)
        rows.append(r)
        pos = (r["ms"] or {}).get("position")
        ep = park_prime.epoch_s(pos / 1000.0 if isinstance(pos, (int, float)) else None)
        verdict["1_epoch"] = {"epoch": ep is not None, "E_s": round(r["t"] - ep, 2) if ep else None,
                              "held_s": s1.numbers.get("hold_s"), "bug_reads": s1.numbers.get("bug_reads")}
        # check 2: BACK on a paused Prime player (the one time it is sent)
        s2 = await c.park_pause(target, None)
        if await stop_if_sound(s2) or not s2.ok:
            say("STOP", why="park_failed", outcome=s2.outcome)
            return 3
        await hid.key("KEYCODE_BACK")
        await asyncio.sleep(2.0)
        pb, fg = await hid.playback(pkg), (await hid.foreground() or "")
        stayed = pb.get("state") == "paused" and bool(pkg) and pkg in fg
        verdict["2_back"] = {"state_after": pb.get("state") or "none", "prime_in_front": bool(pkg) and pkg in fg,
                             "stays_in_player": stayed}
        if not stayed:                                              # BACK left the player: tune again and park
            s1b = await c.park_tune(target, fresh=True)
            if not s1b.ok or not (await c.park_pause(target, None)).ok:
                say("STOP", why="lost_after_back")
                return 3
        # three short commanded pauses for primegrade.pauses (the stick is parked: play, pause, play ...)
        rr = await c.park_resume(target)
        await sample(3)
        for _ in range(3):
            if left() < 200:
                break
            t_p = time.time()
            await hid.key("KEYCODE_MEDIA_PAUSE")
            await sample(12)
            t_q = time.time()
            await hid.key("KEYCODE_MEDIA_PLAY")
            commands.append({"stick": H, "pause_t": round(t_p, 3), "play_t": round(t_q, 3)})
            await sample(5)
        # park and hold
        s3 = await c.park_pause(target, None)
        if not s3.ok:
            say("STOP", why="park_failed", outcome=s3.outcome)
            return 3
        hold = max(0.0, min(float(a.hold), left() - 90.0))
        say("HOLD", asked_s=a.hold, holding_s=round(hold), resume_ok=rr.ok)
        t_hold, states = time.time(), []
        while time.time() - t_hold < hold:
            rows.append(await ms_row(hid, H, pkg, a.gti[-6:], None))
            states.append(rows[-1]["ms"].get("state"))
            if len(states) % 15 == 1:
                say("HOLDING", s=round(time.time() - t_hold), state=states[-1] or "none", stale=c.park_stale() or "no")
            await asyncio.sleep(1.0)
        verdict["hold"] = {"held_s": round(time.time() - t_hold), "paused_share": round(states.count("paused") / max(1, len(states)), 3),
                           "stale": bool(c.park_stale())}
        # flip onto the Prime stick (resume, flip, pause the stick left), then focus, position, bug
        s4 = await c.park_jump(target)
        if await stop_if_sound(s4):
            return 4
        verdict["jump"] = {"outcome": s4.outcome, "flip_s": s4.numbers.get("flip_s"), "jump_s": s4.numbers.get("jump_s"),
                           "input_ok": s4.numbers.get("input_ok")}
        verdict["3_focus"] = {"on_screen": await focus_of(hid, roles), "other": await focus_of(vis, roles)}
        r = await ms_row(hid, H, pkg, a.gti[-6:], None)
        rows.append(r)
        pos = (r["ms"] or {}).get("position")
        ep = park_prime.epoch_s(pos / 1000.0 if isinstance(pos, (int, float)) else None)
        verdict["position"] = {"state": r["ms"].get("state"), "E_s": round(r["t"] - ep, 2) if ep else None}
        if target in hands.bug_ctx:
            bug = await hands._repo_bug(hid, a.home, a.away)
            verdict["bug"] = {"read": bug is not None, "clock_s": clock_seconds(bug.clock) if bug else None,
                              "period": bug.period if bug else None, "score": list(bug.score) if bug else None,
                              "down": (bug.down or None) if bug else None}
        # back to the stick that was on screen (it was paused at the swap)
        lp = hands.place_of(vis)
        if lp is not None and lp.paused:
            s5 = await c.park_jump(lp.target)
            verdict["back"] = {"outcome": s5.outcome}
            if await stop_if_sound(s5):
                rc = 4
    finally:
        fin = await c.park_finish()
        say("FINISH", outcome=fin.outcome)
        with out.open("a") as f:
            for row in rows:
                f.write(json.dumps({"kind": "row", **row}) + "\n")
            for cmd in commands:
                f.write(json.dumps({"kind": "pause", **cmd}) + "\n")
            f.write(json.dumps({"kind": "verdict", **verdict}) + "\n")
        for key in ("1_epoch", "2_back", "3_focus", "4_after_yt", "hold", "jump", "position", "bug", "back"):
            if key in verdict:
                say("CHECK_" + key.upper(), **verdict[key])
        try:
            from redzone.lab import primegrade
            say("PRIMEGRADE_PAUSES", **primegrade.pauses(rows, commands))
            for k, v in primegrade.epoch_share(rows).items():
                say("PRIMEGRADE_EPOCH", group=k, **(v if isinstance(v, dict) else {"value": v}))
        except Exception as e:                                      # the grade is read after; never fails the run
            say("PRIMEGRADE_UNREAD", why=type(e).__name__)
        say("DONE", took_s=round(time.monotonic() - t_start), rows=len(rows), pauses=len(commands))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gti", required=True, help="a Prime Video title id (live game or an on-demand replay)")
    ap.add_argument("--yttv", default="espn", help="a YouTube TV channel put on the hidden stick first (check 4)")
    ap.add_argument("--home", default="", help="home team short name, for the score-bug read (numbers only)")
    ap.add_argument("--away", default="")
    ap.add_argument("--hold", type=float, default=300.0, help="seconds to hold the park (cut to fit the 10-minute budget)")
    ap.add_argument("--out", default=str(ROOT / "data" / "park_prime" / "accept.jsonl"))
    sys.path.insert(0, str(ROOT / "scripts"))
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
