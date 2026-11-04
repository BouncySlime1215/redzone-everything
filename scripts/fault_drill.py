"""Fault drill: run the real Controller / Fetcher / Engine against injected faults and score them.

Nothing touches real sticks, the TV or the network (redzone/platform/faults.py fakes them). Every
scenario reports:
  ok             the switch (or feed read) still worked
  wall_s         seconds of wall time the scenario took (how long a fault holds the system up)
  mid_play_cuts  picture changes while the visible game was live (must be 0, always)
  stuck          a switch or lock still held after the scenario finished
  flagged        the fault was reported with a cause (switches) / the feed was flagged stale (feeds)
  detail         what the code reported

    python scripts/fault_drill.py              # table
    python scripts/fault_drill.py --json       # machine-readable, for the PR before/after table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone import control as ctl  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.platform.faults import FaultStick, FaultTuner, FaultTV, SkewClock, World, failing_transport  # noqa: E402

# A play runs from 0.3 s to 1.5 s into every scenario: a switch that is ready early must wait.
PLAY = (0.3, 1.5)


def _safe(t: float) -> bool:
    return not (PLAY[0] <= t < PLAY[1])


def _controller(tmp: Path, world: World) -> tuple[ctl.Controller, FaultStick, FaultStick, FaultTV]:
    cfg = Config(user_path=tmp / "config.yaml", history_dir=tmp / "hist")
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC"})
    c = ctl.Controller(cfg, ch)
    a, b = FaultStick("a:5555", world), FaultStick("b:5555", world)
    c.sticks = [a, b]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    tv = FaultTV(world, c.hdmi)
    c.tv.set_input = tv.set_input
    c.tv.input_status = tv.input_status
    c.tv.st_device = "drill"
    world.input_to("a:5555")
    world.cuts.clear()
    c.on_target = "youtubetv:fox"
    a.remember(c.on_target, {"state": "playing", "pkg": "yttv.pkg", "position": 0, "updated": 0})
    return c, a, b, tv


def _stuck(c: ctl.Controller) -> bool:
    return bool(c._lock.locked() or c._flipping or (c._task and not c._task.done()))


async def _switch(fault_on: str | None, fault: str | None, tv_fail: bool = False, repeats: int = 1) -> dict:
    with tempfile.TemporaryDirectory() as d:
        world = World(safe=_safe)
        c, a, b, tv = _controller(Path(d), world)
        ctl.TUNERS["youtubetv"] = FaultTuner()
        stick = {"visible": a, "hidden": b}.get(fault_on or "")
        if stick is not None:
            stick.fault = fault
        tv.fail = tv_fail
        t0 = time.monotonic()
        outs = []
        targets = ["youtubetv:espn", "youtubetv:cbs", "youtubetv:espn"]
        for i in range(repeats):
            world.t0 = time.monotonic()     # every attempt meets the same live play
            outs.append(await c.execute(targets[i % len(targets)], may_cut=world.is_safe))
        wall = time.monotonic() - t0
        tasks = [t for t in (c._refresh_task, c._preload_task, c._resume_task) if t and not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        out = outs[-1]
        try:   # which runtime invariants are broken once the dust settles (redzone/platform/health.py)
            from types import SimpleNamespace
            from redzone.platform.health import PlatformHealth
            eng = SimpleNamespace(control=c, cfg=c.cfg, _pending_decision=None,
                                  decider=SimpleNamespace(current="fox", replaying=None, after_replay=False))
            broken = sorted({code for code, _, _ in PlatformHealth(eng).conditions()})
        except ImportError:
            broken = None
        return {"invariants": broken, "ok": out.ok, "wall_s": round(wall, 2), "mid_play_cuts": world.mid_play_cuts,
                "stuck": _stuck(c), "flagged": bool(out.ok or out.detail),
                "per_try_s": [round(o.took, 2) for o in outs] if repeats > 1 else None,
                "timeouts": getattr(stick, "timeouts", 0),
                "breaker": getattr(getattr(stick, "breaker", None), "state", None),
                "errors_toward_safe_mode": sum(not o.ok and not o.screen_unchanged for o in outs),
                "detail": out.detail[:90]}


async def _feed_5xx() -> dict:
    """ESPN answers 503 for 2 minutes, then recovers. A hot game is polled every 3 s (polling.hot_s).
    How long until the system KNOWS the feed is stale, and how long after ESPN is back until the
    game counts as fresh again? (Stale games are never entered.)"""
    import httpx
    from redzone.models import GameState, League, Phase, Team
    from redzone.sources.http import Fetcher
    from redzone.sources.merge import Merger
    try:
        from redzone.sources.feeds import FeedBook
    except ImportError:
        FeedBook = None
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    down = [False]
    fetcher = Fetcher(client=httpx.AsyncClient(transport=failing_transport(lambda: 503 if down[0] else 200)),
                      clock=clock)
    merger = Merger(clock=clock)
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=1"
    g = GameState(league=League.NFL, game_id="1", home=Team("A", score=0), away=Team("B", score=0),
                  phase=Phase.LIVE, period=1, clock_s=900.0)
    if FeedBook is not None:
        book = FeedBook(merger, fetcher, lambda _g: [url], lambda _gid: 3.0, clock=clock)
        stale = lambda: book.stale("1")  # noqa: E731
    else:
        stale = lambda: merger.is_stale("1")  # noqa: E731   (before: the merger's rule only)
    fail_at, back_at = 1003.0, 1123.0
    detect_at = fresh_at = None
    t = 1000.0
    while t < 1300.0:
        now[0] = t
        down[0] = fail_at <= t < back_at
        body = await fetcher.get_json(url, min_interval=2.5)
        if body is not None:
            if fetcher.fetched_at(url) == t:          # fresh data: the game moved on (clock runs)
                g = GameState(league=League.NFL, game_id="1", home=Team("A", score=0), away=Team("B", score=0),
                              phase=Phase.LIVE, period=1, clock_s=max(0.0, 900.0 - (t - 1000.0)))
            merger.update("espn_summary", g)          # ...else the poll loop re-feeds the cached copy
        if down[0] and detect_at is None and stale():
            detect_at = t
        if t >= back_at and fresh_at is None and not stale():
            fresh_at = t
        t += 3.0
    await fetcher.close()
    return {"ok": fresh_at is not None, "wall_s": None, "mid_play_cuts": 0, "stuck": False,
            "flagged": detect_at is not None,
            "detect_s": None if detect_at is None else round(detect_at - fail_at, 1),
            "recover_s": None if fresh_at is None else round(fresh_at - back_at, 1),
            "detail": "2-minute ESPN 503 outage, hot game polled every 3 s"}


async def _clock_skew() -> dict:
    """The wall clock jumps forward 3 minutes mid-switch (NTP correction, laptop lid). The switch is
    healthy and only 1 s old: it must not be declared hung and cancelled."""
    from redzone.decider.decider import Decision, State
    from redzone.engine import Engine
    from redzone.store import Store
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = Config(user_path=tmp / "config.yaml", history_dir=tmp / "hist")
        cfg.update({"mode": {"dry_run": False}})
        engine = Engine(cfg, Store(tmp / "t.db"))
        engine.decider.current = "old"
        engine.control.submit = lambda *a, **k: None
        cancelled = []
        engine.control.cancel_pending = lambda: cancelled.append(True)
        engine._act(Decision(1, "switch", "new", "better game", State.WATCHING), "old", {})
        skew = SkewClock(time.time)
        real = time.time
        time.time = skew          # every wall-clock read in the process sees the jump, as on the Mac
        try:
            skew.jump(180.0)
            engine._switch_deadline(time.time())
            errors = len(engine.decider.errors)
        finally:
            time.time = real
        await engine.close()
    return {"ok": not cancelled, "wall_s": None, "mid_play_cuts": 0, "stuck": False, "flagged": True,
            "detail": "cancelled a healthy switch as hung" if cancelled else "kept the switch",
            "spurious_errors": errors}


async def _corrupt_state() -> dict:
    """Every state file left broken (a crash mid-write, a full disk, a bad copy), one at a time: does
    the engine still start, and does it keep your settings (a lost config = dry run = no TV control)?"""
    from redzone.engine import Engine
    from redzone.store import Store
    from redzone.supervisor import read_jsonl
    cases = {
        "config.yaml": b"\x00\x00 mode: {dry_run: fal",
        "switch_times.json": b'{"samples": {"youtubetv": [4.1, 5',
        "user_state.json": b'{"locked_game": "40',
        "traces.jsonl": b'{"id": 1}\n{"id": 2, "stag',
        "supervisor.jsonl": b'{"ts": 1}\n{"ts": 2, "ki',
        "redzone.db": b"SQLite format 3\x00" + b"\x00" * 50,
    }
    crashed, lost_settings = [], []
    for name, body in cases.items():
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = Config(user_path=root / "config.yaml", history_dir=root / "hist")
            cfg.update({"mode": {"dry_run": False}})          # you turned TV control on earlier
            (root / name).write_bytes(body)
            try:
                cfg = Config(user_path=root / "config.yaml", history_dir=root / "hist")
                e = Engine(cfg, Store(root / "redzone.db"))
                e._restore_user_state()
                e.store.recent_switches(5)
                read_jsonl(root / "supervisor.jsonl")
                if cfg.get("mode.dry_run", True):
                    lost_settings.append(name)
                await e.close()
            except Exception as ex:
                crashed.append(f"{name}: {type(ex).__name__}")
    return {"ok": not crashed and not lost_settings, "wall_s": None, "mid_play_cuts": 0, "stuck": False,
            "flagged": True, "crashed_at_start": crashed, "fell_back_to_dry_run": lost_settings,
            "detail": f"{len(cases)} broken state files, one at a time"}


SCENARIOS = {
    "baseline":            lambda: _switch(None, None),
    "adb_drop_hidden":     lambda: _switch("hidden", "adb_drop"),
    "adb_drop_visible":    lambda: _switch("visible", "adb_drop"),
    "adb_hang_hidden":     lambda: _switch("hidden", "adb_hang"),
    "adb_hang_hidden_x3":  lambda: _switch("hidden", "adb_hang", repeats=3),
    "app_crash_hidden":    lambda: _switch("hidden", "app_crash"),
    "prompt_screen":       lambda: _switch("hidden", "prompt_screen"),
    "tv_flip_fail":        lambda: _switch("hidden", "tv_flip_fail", tv_fail=True),
    "feed_5xx":            _feed_5xx,
    "reboot_visible":      lambda: _visible_fault("reboot"),
    "stuck_buffering":     lambda: _visible_fault("buffering"),
    "st_outage":           lambda: _st_fault("outage"),
    "st_429":              lambda: _st_fault("429"),
    "st_auth":             lambda: _st_fault("auth"),
    "clock_skew":          _clock_skew,
    "corrupt_state":       _corrupt_state,
}


async def _visible_fault(kind: str) -> dict:
    """The stick on screen stops showing the game for 60 s of keepalive ticks (10 s apart):
      reboot     its adb link drops and comes back (keepalive's reconnect -> _forget) on the Fire TV
                 home screen;
      buffering  its player spins on "buffering" forever (a dead stream).
    Compared with hardening.sticks off and on: seconds until the game plays again (None = never in
    the 60 s), re-links sent, and whether the other stick or the TV input was touched."""
    res = {}
    for on in (False, True):
        with tempfile.TemporaryDirectory() as d:
            world = World(safe=_safe)
            c, a, b, tv = _controller(Path(d), world)
            c.cfg.update({"hardening": {"sticks": on}})
            tuner = FaultTuner()
            plain = tuner.tune

            async def tune(stick, ref, target, may_cut=None, plain=plain):
                stick.playing = "playing"        # a fresh deep link plays (the app itself is fine)
                return await plain(stick, ref, target, may_cut)
            tuner.tune = tune
            ctl.TUNERS["youtubetv"] = tuner
            c.adb_ok = True
            c._watchdog_applied = True
            clock = [time.time()]
            real = ctl.time.time
            ctl.time.time = lambda: clock[0]
            fixed_at = None
            try:
                a.playing = "none" if kind == "reboot" else "buffering"
                if kind == "reboot":
                    c._forget(a)                 # what _reconnect does when the stick comes back
                tv.calls = 0
                for tick in range(7):            # t = 0, 10, ... 60 s
                    await c._watch_visible()
                    if c._resume_task:
                        await asyncio.gather(c._resume_task, return_exceptions=True)
                    await c._watch_visible()
                    if c._resume_task:
                        await asyncio.gather(c._resume_task, return_exceptions=True)
                    if a.playing == "playing" and fixed_at is None:
                        fixed_at = tick * 10
                    clock[0] += 10
            finally:
                ctl.time.time = real
            res["on" if on else "off"] = {"restored_after_s": fixed_at, "relinks": len(tuner.calls),
                                          "other_stick_touched": any("am start" in x for x in b.log),
                                          "tv_input_changes": tv.calls}
    return {"ok": res["on"]["restored_after_s"] is not None and not res["on"]["other_stick_touched"],
            "wall_s": None, "mid_play_cuts": 0, "stuck": False, "flagged": True,
            "detail": f"{kind}: visible stick not playing for 60 s, off vs on", "off": res["off"], "on": res["on"]}


async def _st_fault(kind: str) -> dict:
    """SmartThings fails (5xx/timeouts, 429 or a refused sign-in) for 10 flips, one every 60 s, then
    comes back. Each flip either tries the cloud (a timed-out call costs its 8 s inside the flip) or
    goes straight to HOME/CEC. Compared with hardening.smartthings off and on, on a fake cloud and a
    fake clock (no TV, no network)."""
    import httpx

    from redzone import tv as tvmod
    tvmod.VERIFY_GAP_S = tvmod.ST_RETRY_GAP_S = 0.0
    res = {}
    for hard in (False, True):
        t = [1000.0]
        down = [True]
        timed_out = [0]
        in_flips = [0]

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/token":
                if down[0] and kind == "auth":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 86400})
            if down[0]:
                if kind == "outage":
                    timed_out[0] += 1
                    raise httpx.ReadTimeout("no reply", request=req)
                if kind == "429":
                    return httpx.Response(429, headers={"retry-after": "120"})
                return httpx.Response(401)
            if req.url.path == "/v1/devices":
                return httpx.Response(200, json={"items": [{"deviceId": "tv-1", "components": [
                    {"capabilities": [{"id": "mediaInputSource"}]}]}]})
            if req.url.path.endswith("/status"):
                return httpx.Response(200, json={"inputSource": {"value": "HDMI2"}})
            return httpx.Response(200, json={})
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "st.json"
            f.write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r",
                                     "access_token": "a", "expires_at": 10 ** 12, "refreshed_at": 10 ** 12}))
            tr = httpx.MockTransport(handler)
            tv = tvmod.SamsungTV(st_device="tv-1", auth=tvmod.SmartThingsAuth(f, transport=tr, clock=lambda: t[0]),
                                 transport=tr, clock=lambda: t[0], hardening=lambda: hard)
            tried = 0
            for i in range(10):
                if tv.paths():
                    tried += 1
                    before = timed_out[0]
                    await tv.set_input("HDMI2", methods=tv.paths())
                    in_flips[0] += timed_out[0] - before
                t[0] += 30
                await tv.maintain()
                t[0] += 30
            down[0] = False
            if kind == "auth":   # the user re-runs tv_setup.py --smartthings
                import os
                os.utime(f, (t[0] + 1, t[0] + 1))
                tv.auth._d = None
            back = None
            for s in range(0, 1200, 10):
                t[0] += 10
                await tv.maintain()
                if tv.paths():
                    back = s + 10
                    break
            res["on" if hard else "off"] = {"flips_that_tried_cloud": tried, "timeout_s_inside_flips": in_flips[0] * 8,
                                            "recover_s": back, "fault": tv.st_fault or None}
    on, off = res["on"], res["off"]
    return {"ok": on["flips_that_tried_cloud"] <= 2 and on["recover_s"] is not None, "wall_s": None,
            "mid_play_cuts": 0, "stuck": False, "flagged": True,
            "detail": f"{kind}: 10 flips with SmartThings down, off vs on", "off": off, "on": on}


async def run(names: list[str]) -> dict:
    # Test-sized waits where the drill isn't measuring them (same knobs the unit tests use).
    ctl.REFRESH_POLL_S = 0.01
    out = {}
    for n in names:
        out[n] = await SCENARIOS[n]()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("only", nargs="*")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    res = asyncio.run(run(a.only or list(SCENARIOS)))
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{'scenario':22} {'ok':5} {'wall_s':>7} {'cuts':>4} {'stuck':5} {'flag':5} detail")
        for n, r in res.items():
            extra = {k: v for k, v in r.items() if k not in ("ok", "wall_s", "mid_play_cuts", "stuck", "flagged", "detail")}
            print(f"{n:22} {str(r['ok']):5} {str(r['wall_s']):>7} {r['mid_play_cuts']:>4} {str(r['stuck']):5} "
                  f"{str(r['flagged']):5} {r['detail']} {extra or ''}")
    cuts = sum(r["mid_play_cuts"] for r in res.values())
    return 1 if cuts else 0


if __name__ == "__main__":
    sys.exit(main())
