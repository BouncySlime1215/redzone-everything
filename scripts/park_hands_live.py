"""PARK-HANDS acceptance on the real sticks and TV: the park loop through the APP's controller (unit 7).

    python scripts/park_hands_live.py --channels espn,fs1,espn2,tnt --moves 40 --look 16 [--hold-check 240]

First a pause check (`--hold-check` s, default 240; 0 skips it): the hidden stick is parked on the first channel and
held unseen, its media session read before, every `--hold-every` s and after, then resumed and read 30 s later
(HELD: paused at every read, the frozen place moved at most 1 s, the resume started from it). One move = Controller.park_tune (the hidden stick to the next channel, with its read), park_pause (frozen about 1 s
later), `--look` seconds on the picture, then park_jump (resume, flip, pause the stick left). The flags are set in
memory only for this run (park.enabled, sound_guard.enabled, sound_guard.must_be_silent; data/config.yaml is not
written). Silence is read back before the first command and after every flip; any sound stop ends the run with
nothing more sent. At the end every paused stick is resumed. One row per step goes to --out (words and numbers
only: no address, device id or frame). Compare with ledger L19: tune 2.7 s median, pause 0.77 s, jump 2.25 s, 0
failures (with the read, a tune takes about 13-19 s).

Agents never run this (CLAUDE.md: no stick or TV scripts); the coordinator does, with the TV muted.
Why: unit 7's acceptance is the same 40-move park loop the coordinator ran by hand on Nov 10, driven by the app's code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import control as ctl  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config, _deep_merge  # noqa: E402
from redzone.platform.sound_guard import MARKER, SoundGuard  # noqa: E402

FLAGS = {"mode": {"dry_run": False}, "park": {"enabled": True},
         "sound_guard": {"enabled": True, "must_be_silent": True}}
PERMISSION = ROOT / "data" / ".agent_hw_ok"   # Nick creates it for a live test (CLAUDE.md)

SERVICE_LABEL = "com.redzone.everything"   # the login service (deploy/com.redzone.everything.plist.template)
# The app's command lines: `python -m redzone` by hand, and the login service, which runs scripts/service_runtime.py
# through `python -c` (runpy), so its command line never says `-m redzone`.
APP_PATTERNS = ("[Pp]ython[^ ]* (.* )?-m redzone( |$)", r"service_runtime\.py")


def app_running() -> bool:
    """Is any copy of the app running, by hand or as the login service? Unsure counts as yes."""
    try:
        for pat in APP_PATTERNS:
            if subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True, timeout=5).stdout.split():
                return True
    except Exception:
        return True
    try:
        r = subprocess.run(["launchctl", "list", SERVICE_LABEL], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return False   # no launchd here (CI): pgrep above is the whole answer
    except Exception:
        return True
    return r.returncode == 0 and '"PID" =' in r.stdout   # loaded and its process is up (a stopped job has no PID)


HOLD_TOL_S = 1.0   # a held pause: the frozen place moves at most this much, and the resume starts from it


async def hold_check(c, g, target: str, hold_s: float, every: float = 30.0, *, after_s: float = 30.0,
                     sleep=asyncio.sleep, now=time.time) -> dict:
    """[pause check] Park the hidden stick on `target` (tune, read, pause) and hold it `hold_s` seconds, unseen. Its media
    session is read before the hold, every `every` s and at the end, with silence read back each time; then it is
    resumed and read again `after_s` later. HELD = paused at every read, the frozen place moved at most HOLD_TOL_S, the
    resumed stream started from that place (not the live edge) and then played at normal speed. Any silence readback
    that is not "ok" stops it ("stop": nothing more is sent). Numbers only."""
    v = {"target": target, "hold_s": hold_s, "held": False, "stop": False, "why": "", "reads": []}
    hands = c.park_hands()
    for make in (lambda: c.park_tune(target), lambda: c.park_pause(target, now() + 1.0)):
        st = await make()
        if st.outcome == "stop":
            v.update(stop=True, why=f"sound: {st.why}")
            return v
        if not st.ok:
            v["why"] = f"{st.step} {st.outcome}: {st.why}"
            return v
    hid, t0 = c.hidden, now()
    p0 = None
    while True:
        r, snd = await hands._read(hid), await g.check("park_hands_live hold")
        t = now() - t0
        v["reads"].append({"t": round(t, 1), "state": getattr(r, "state", None), "pos": getattr(r, "position_s", None),
                           "sound": snd})
        if snd != "ok":
            v.update(stop=True, why=f"silence not read back during the hold ({snd})")
            return v
        if r is None or r.state != "paused" or r.position_s is None:
            v["why"] = f"not paused at +{t:.0f} s ({getattr(r, 'state', 'unread')})"
            return v
        p0 = r.position_s if p0 is None else p0
        if abs(r.position_s - p0) > HOLD_TOL_S:
            v["why"] = f"the frozen place moved {r.position_s - p0:+.1f} s at +{t:.0f} s"
            return v
        if t >= hold_s:
            break
        await sleep(min(every, hold_s - t))
    t_key = now()
    rs = await c.park_resume(target)
    place = hands.place_of(hid)
    if not rs.ok or place is None:
        v.update(stop=rs.outcome == "stop", why=f"resume {rs.outcome}: {rs.why}")
        return v
    start_err = place.ref_pos - p0                      # played since the resume key: at most ref_wall - t_key
    v["resume_from_s"] = round(start_err, 2)
    if not -HOLD_TOL_S <= start_err <= (place.ref_wall - t_key) + HOLD_TOL_S:
        v["why"] = f"the resume started {start_err:+.1f} s from the frozen place, not from it"
        return v
    await sleep(after_s)
    r, snd = await hands._read(hid), await g.check("park_hands_live after hold")
    if snd != "ok":
        v.update(stop=True, why=f"silence not read back after the hold ({snd})")
        return v
    if r is None or r.state != "playing" or r.pos_now_s is None:
        v["why"] = f"not playing {after_s:.0f} s after the resume ({getattr(r, 'state', 'unread')})"
        return v
    speed_err = (r.pos_now_s - place.ref_pos) - (r.wall - place.ref_wall)
    v["after_err_s"] = round(speed_err, 2)
    if abs(speed_err) > 2 * HOLD_TOL_S:
        v["why"] = f"after the resume it ran {speed_err:+.1f} s off the wall clock in {after_s:.0f} s"
        return v
    v.update(held=True, why=f"held {hold_s:.0f} s: moved at most {HOLD_TOL_S:.0f} s, resumed from its place")
    return v


async def run(args) -> int:
    if not PERMISSION.exists():
        # Like park_prime_accept: guard.py G3's pattern does not name this script, so the script asks for itself.
        print("Refused: no data/.agent_hw_ok (Nick creates it for a live test). Nothing was sent.")
        return 2
    if MARKER.exists():
        print("Refused: a sound incident is open (data/sound_guard/incident.json). Nothing was sent.")
        return 4
    if app_running():
        print("Refused: the app is running (or that cannot be ruled out). Nothing was sent.")
        return 2
    cfg = Config()
    cfg.data = _deep_merge(cfg.data, FLAGS)                  # in memory only
    c = ctl.Controller(cfg, ChannelMap(ROOT / "data" / "channels.json"))
    c.load_devices()
    c.tv.sound_settings = lambda k, fb=None: cfg.get(k, fb)
    c.tv.sound_guard = g = SoundGuard(c.tv)
    s = await g.silence()
    if await g.check("park_hands_live start") != "ok":
        print(f"Silence not read back before the start ({ {k: s.get(k) for k in ('mute', 'volume')} }). Nothing else sent.")
        return 4
    for st in c.sticks:
        if not await st.connect():
            print(f"{c._stick_label(st.host)} did not answer. Nothing was sent to a stick.")
            return 2
    inp = (g.last or {}).get("input")
    on = next((i for i, st in enumerate(c.sticks) if c.hdmi.get(st.host) == inp), None)
    if on is None:
        print("Cannot tell which stick is on screen. Nothing was sent to a stick.")
        return 2
    c.active = on
    why = c.park_why()
    if why:
        print(f"PARK refuses: {why}. Nothing was sent to a stick.")
        return 2
    chans = [x.strip() for x in args.channels.split(",") if x.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, rc = [], 0
    with out.open("a") as f:
        if args.hold_check > 0:
            hv = await hold_check(c, g, f"youtubetv:{chans[0]}", args.hold_check, args.hold_every)
            f.write(json.dumps({"move": "hold", **hv}) + "\n")
            print(f"HOLD {'HELD' if hv['held'] else 'NOT HELD'} {hv['hold_s']:.0f} s on {hv['target']}: {hv['why']}")
            if hv["stop"]:
                print("SOUND STOP: nothing more is sent. Clear the incident by hand after finding its cause.")
                rc = 4
        for i in range(args.moves if rc == 0 else 0):
            target = f"youtubetv:{chans[i % len(chans)]}"
            steps = [await c.park_tune(target)]
            if steps[-1].ok:
                steps.append(await c.park_pause(target, time.time() + 1.0))
            if steps[-1].ok:
                await asyncio.sleep(args.look)
                steps.append(await c.park_jump(target))
            for st in steps:
                row = {"move": i + 1, **st.view()}
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                print(f"{i + 1:>3} {st.step:<5} {st.outcome:<7} {st.took_s:5.1f} s  {st.target:<16} {st.way}  {st.why}")
            if any(st.outcome == "stop" for st in steps):
                print("SOUND STOP: nothing more is sent. Clear the incident by hand after finding its cause.")
                rc = 4
                break
            if steps[-1].ok:
                await asyncio.sleep(args.look)
        fin = await c.park_finish()
        f.write(json.dumps({"move": 0, **fin.view()}) + "\n")
    summary(rows, fin)
    return rc


def summary(rows: list, fin) -> None:
    def med(step, key=None):
        xs = [(r["numbers"].get(key) if key else r["took_s"]) for r in rows if r["step"] == step and r["outcome"] == "ok"]
        xs = [x for x in xs if isinstance(x, (int, float))]
        return f"{statistics.median(xs):.2f} s (n={len(xs)})" if xs else "none"
    bad = {}
    for r in rows:
        if r["outcome"] != "ok":
            bad[f"{r['step']} {r['outcome']}"] = bad.get(f"{r['step']} {r['outcome']}", 0) + 1
    reads = [r["numbers"].get("read") for r in rows if r["step"] == "tune" and r["outcome"] == "ok"]
    print(f"tune {med('tune')}, pause {med('park')}, jump {med('jump')}, flip {med('jump', 'flip_s')}; "
          f"places read {sum(1 for x in reads if x)} of {len(reads)}; not ok: {bad or 'none'}; finish: {fin.outcome}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels", default="espn,fs1,espn2,tnt", help="YouTube TV short names, in turn")
    ap.add_argument("--moves", type=int, default=40)
    ap.add_argument("--look", type=float, default=16.0, help="seconds on each picture before the next jump")
    ap.add_argument("--hold-check", type=float, default=240.0,
                    help="first, hold one park on the hidden stick this many seconds and read it back (0 = skip)")
    ap.add_argument("--hold-every", type=float, default=30.0, help="seconds between reads during the hold")
    ap.add_argument("--out", default=str(ROOT / "data" / "park_hands" / "run.jsonl"))
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
