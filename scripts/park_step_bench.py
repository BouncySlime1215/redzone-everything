"""PARK step bench: the app's own PARK steps (`Controller.park_tune`, `park_pause`, `park_jump`) on fake sticks and a fake
TV whose every call takes the time a real one does, with `park.fast_steps` off and on. No device I/O.

    python scripts/park_step_bench.py                      # 20 moves each way, as the Nov 11 hands test ran them
    python scripts/park_step_bench.py --look 0             # back to back: the cycle a jump, the re-tune and the park make
    python scripts/park_step_bench.py --moves 40 --json out.json

The fakes are tests/test_park_hands.py's sticks, tuner and label reader (they behave like the real ones did on Nov 10),
with time added to every call (`Lat`): an adb key, a media-session read, the foreground read, the tuner, the label read,
a SmartThings command or status read, the TV's own input change. The adb always-open shell is modelled too: commands
to one stick go one at a time, and one that finds the shell busy longer than adb.LOCK_WAIT_S runs as its own adb process.
The TV runs the real `SamsungTV.set_input` (sound guard before and after) and `sound_state`, and the real `SoundGuard`.
`Lat` is set so that with the flag OFF the steps take what the Nov 11 hands test measured on the real TV
(data/hands_2026-11-11.log: tune 16.0 s, park 4.2 s, jump 6.9 s, flip 2.43 s, medians of 20). It is a model: the
real seconds are measured on the TV.

A park is timed from its pause key (the wait for its frame before the key is the plan's, not the step's; tonight's
4.2 s park held 0.3 s of it). Each step's time is split by what it waited on (`budget`): a 10 ms sweep over the step that charges each slice to the
first of keys, TV flip, TV sound read, stick read, foreground read, tuner, label read, or else a fixed wait.

Why: Nick wants "all the code times down"; this shows where each step's seconds go and what the flag saves, before
the real-TV test (no stick or TV is touched: CLAUDE.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "tests").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone import control as ctl  # noqa: E402
from redzone import tv as tvmod  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.platform.sound_guard import SoundGuard  # noqa: E402
from redzone.tuners import adb, yttv_timing  # noqa: E402
from redzone.tuners.tuners import TuneResult  # noqa: E402
from redzone.tuners.yttv_label import LabelRead  # noqa: E402
from test_park_hands import Clock, Labels, Tuner, YTStick  # noqa: E402

CHANNELS = ("espn", "fs1", "espn2", "tnt")
TONIGHT = {"tune": 16.02, "park": 4.22, "jump": 6.92, "flip": 2.43}     # data/hands_2026-11-11.log, medians
CATS = ("key", "flip", "sound", "read", "foreground", "tuner", "label")  # the order a slice is charged in


@dataclass
class Lat:
    """Seconds each call takes (round trip). Set so the flag-off steps match TONIGHT."""
    key: float = 0.25            # adb `input keyevent`
    read: float = 0.22           # cat /proc/uptime; dumpsys media_session (and the playback grep)
    foreground: float = 0.65     # dumpsys activity activities | grep (the heaviest read)
    spawn: float = 0.10          # a command that finds the shell busy: its own adb process
    resume: float = 0.55         # a resume key to the stream moving again (its "playing" report)
    tuner: float = 2.0           # a YouTube TV tune by its direct code (the fake's session then says 0.0 for 2 s more)
    label: float = 3.3           # the overlay frame and its text read (screenshot, on-Mac OCR)
    st_get: float = 0.35         # one SmartThings status read
    st_post: float = 0.36        # one SmartThings command (tonight: 0.24-0.53 s)
    switch: float = 1.30         # the TV reports its new input this long after the command was accepted


class Trace:
    def __init__(self, clock):
        self.clock, self.rows, self.steps = clock, [], []

    def add(self, cat: str, a: float, b: float) -> None:
        self.rows.append((cat, a, b))

    def budget(self, a: float, b: float) -> dict:
        out = dict.fromkeys(CATS + ("wait",), 0.0)
        rows = [r for r in self.rows if r[2] > a and r[1] < b]
        t = a
        while t < b - 1e-9:
            mid = t + 0.005
            on = {c for c, x, y in rows if x <= mid < y}
            out[next((c for c in CATS if c in on), "wait")] += 0.01
            t += 0.01
        return {k: round(v, 2) for k, v in out.items() if v >= 0.005}


class BenchStick(YTStick):
    """A YTStick whose adb calls take time, through a model of the always-open shell. A key or a read acts in the middle
    of its round trip. The pause is published when the frame freezes (the key's lead later); a resume plays (and says
    so) `Lat.resume` after its key."""

    def __init__(self, host, clock, model, lead, lat: Lat, trace: Trace, seed: int = 0):
        super().__init__(host, clock, model, lead)
        self.lat, self.trace, self.busy_until, self.half = lat, trace, 0.0, 0.0
        self.rng = random.Random(f"{seed}:{host}")

    def _cat(self, cmd: str) -> tuple[str, float]:
        if cmd.startswith("input keyevent"):
            return "key", self.lat.key
        if "dumpsys activity" in cmd:
            return "foreground", self.lat.foreground
        return "read", self.lat.read

    async def _shell(self, cmd, timeout):
        now = self.clock.now()
        cat, rtt = self._cat(cmd)
        rtt *= self.rng.uniform(0.8, 1.2)                                  # no two round trips alike
        if self.busy_until - now > adb.LOCK_WAIT_S:
            start, extra = now + adb.LOCK_WAIT_S, self.lat.spawn          # its own adb process
        else:
            start, extra = max(now, self.busy_until), 0.0
            self.busy_until = start + rtt
        act = start + extra + rtt / 2
        await self.clock.sleep(act - now)
        self.half = act - now                                              # the key went out this long ago
        r = await super()._shell(cmd, timeout)
        await self.clock.sleep(rtt / 2)
        self.trace.add(cat, now, self.clock.now())
        return r

    def _pause(self, lead):
        """The frame freezes `lead` after the key was SENT (sd 0.08 s), as measured: not after it acts."""
        super()._pause(max(0.0, lead - self.half + self.rng.gauss(0.0, 0.08)))

    def _play(self, pos):
        super()._play(pos)
        self.wall0 += self.lat.resume
        self.event += self.lat.resume

    def pos(self, w=None):
        w = self.clock.now() if w is None else w
        if self.ps == "playing" and w < self.wall0:
            return self.pos0
        return super().pos(w)

    def _media(self) -> str:
        now = self.clock.now()
        if self.ps == "paused" and now < self.event:                      # the pause is not published yet
            self.ps = "playing"
            try:
                return super()._media()
            finally:
                self.ps = "paused"
        if self.ps == "playing" and now < self.wall0 and now - self.loaded_at >= 2.0:   # resumed, not moving yet
            self.ps, frozen, self.frozen = "paused", self.frozen, self.pos0
            ev, self.event = self.event, now - 1.0
            try:
                return super()._media()
            finally:
                self.ps, self.frozen, self.event = "playing", frozen, ev
        return super()._media()


class BenchLabels(Labels):
    """Labels' read of the overlay's time label, taking `Lat.label`."""

    def __init__(self, clock, lat: Lat, trace: Trace):
        super().__init__(clock)
        self.lat, self.trace = lat, trace

    async def __call__(self, stick):
        a = self.clock.now()
        self.calls += 1
        await self.clock.sleep(self.lat.label)
        self.trace.add("label", a, self.clock.now())
        if not stick.overlay:
            return LabelRead("no_overlay", self.clock.now())
        knob = stick.content(stick.frozen - 15.0 * stick.lefts)
        start = math.floor(knob / 300.0) * 300.0
        return LabelRead("ok", self.clock.now(), label_s=float(math.floor(knob - start)), prog_start=start)


class BenchTuner(Tuner):
    """The YouTube TV tuner by direct code, taking `Lat.tuner`."""

    def __init__(self, clock, lat: Lat, trace: Trace):
        super().__init__(clock)
        self.lat, self.trace = lat, trace

    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append(ref)
        a = self.clock.now()
        await self.clock.sleep(self.lat.tuner)
        stick.load(target.split(":", 1)[1], base_lag=20.0)
        self.trace.add("tuner", a, self.clock.now())
        return TuneResult(True, target, self.lat.tuner, ["playing"], "")


class BenchTV(tvmod.SamsungTV):
    """The real SamsungTV's set_input (sound guard before and after) and sound_state over a fake SmartThings: a command
    or a status read takes its round trip, and the TV reports a new input `Lat.switch` after the command."""

    def __init__(self, clock, lat: Lat, trace: Trace, settings, marker: Path):
        super().__init__(st_device="bench-display", st_capability="mediaInputSource", cloud_enabled=True)
        self.clk, self.lat, self.trace = clock, lat, trace
        self.input, self.mute, self.volume, self.power = "HDMI1", "muted", 0, "on"
        self._to_input, self._switch_at, self._flipping = None, None, False
        self.in_jump = lambda: False
        self.sets, self.commands = [], []
        self.sound_settings = settings
        self.sound_guard = SoundGuard(self, settings=settings, clock=clock.now, sleep=clock.sleep, marker=marker)

    @property
    def has_readback(self) -> bool:
        return True

    def ready(self) -> bool:
        return True

    def paths(self) -> list[str]:
        return ["smartthings"]

    def _what(self) -> str:
        """A status read inside the flip, a jump's sound read-back, or the guard's periodic check (not a step's)."""
        return "flip" if self._flipping else "sound" if self.in_jump() else "watch"

    def _now_input(self) -> str:
        if self._switch_at is not None and self.clk.now() >= self._switch_at:
            self.input, self._switch_at = self._to_input, None
        return self.input

    async def _st_value(self, capability, attr):
        a = self.clk.now()
        await self.clk.sleep(self.lat.st_get)
        self.trace.add(self._what(), a, self.clk.now())
        return {"switch": self.power, "mute": self.mute, "volume": self.volume}.get(attr)

    async def input_status(self):
        a = self.clk.now()
        await self.clk.sleep(self.lat.st_get / 2)
        v = self._now_input()
        await self.clk.sleep(self.lat.st_get / 2)
        self.trace.add(self._what(), a, self.clk.now())
        return v

    async def st_command(self, capability, command, args=None):
        await self.clk.sleep(self.lat.st_post)
        self.commands.append((capability, command))
        if capability == "switch" and command == "off":
            self.power = "off"
        return True

    async def _set_input(self, hdmi, methods=None):
        """The SmartThings path of SamsungTV._set_input and _verify_input, on the bench clock."""
        n = hdmi.upper().replace(" ", "")
        self.sets.append((n, list(methods or [])))
        self._flipping, a = True, self.clk.now()
        try:
            await self.clk.sleep(self.lat.st_post)
            self.trace.add("flip", a, self.clk.now())
            self._to_input, self._switch_at = n, self.clk.now() + self.lat.switch
            t0 = self.clk.now()
            for i in range(tvmod.VERIFY_READS):
                if self.clk.now() - t0 >= tvmod.VERIFY_DEADLINE_S:
                    break
                if await self.input_status() == n:
                    return True
                if i < tvmod.VERIFY_READS - 1:
                    b = self.clk.now()
                    await self.clk.sleep(tvmod.VERIFY_GAP_S)
                    self.trace.add("flip", b, self.clk.now())
            return False
        finally:
            self._flipping = False


def build(fast: bool, lat: Lat, tmp: Path, seed: int = 0):
    clk = Clock()
    trace = Trace(clk)
    cfg = Config(user_path=tmp / "config.yaml", history_dir=tmp / "hist")
    cfg.update({"mode": {"dry_run": False}, "park": {"enabled": True, "fast_steps": fast},
                "sound_guard": {"enabled": True, "must_be_silent": True}})
    ch = ChannelMap(tmp / "ch.json")
    ch.codes.update({c: f"CODE{c.upper():0<7}"[:11] for c in CHANNELS})
    c = ctl.Controller(cfg, ch)
    a = BenchStick("a:5555", clk, "Fire TV Stick 4K (2023)", 0.70, lat, trace, seed)
    b = BenchStick("b:5555", clk, "Fire TV Stick 4K Plus", 0.60, lat, trace, seed)
    c.sticks, c.hdmi = [a, b], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv = BenchTV(clk, lat, trace, lambda k, fb=None: cfg.get(k, fb), tmp / "sound_incident.json")
    c.tv.in_jump = lambda: c._flipping
    tuner = BenchTuner(clk, lat, trace)
    labels = BenchLabels(clk, lat, trace)
    c._park_hands = ctl.ParkHands(c, now=clk.now, sleep=clk.sleep, label_reader=labels)
    a.load("tnt")
    a.loaded_at -= 10
    return SimpleNamespace(c=c, a=a, b=b, clk=clk, trace=trace, tuner=tuner, hands=c._park_hands)


async def run_moves(w, moves: int, look: float, seed: int = 0) -> list[dict]:
    c, clk, rows, rng = w.c, w.clk, [], random.Random(seed)
    assert await c.tv.sound_guard.check("bench start") == "ok"
    for i in range(moves):
        target = f"youtubetv:{CHANNELS[i % len(CHANNELS)]}"
        lead = 1.0 + rng.uniform(0.0, 0.5)                   # the park's frame, as the hands test asked it (+1 s), at any phase
        steps = []
        for name, call in (("tune", lambda: c.park_tune(target)), ("park", lambda: c.park_pause(target, clk.now() + lead)),
                           ("jump", lambda: c.park_jump(target))):
            if steps and not steps[-1][0].ok:
                break
            if name == "jump" and look:
                await clk.sleep(look)
            a, hid = clk.now(), c.hidden
            st = await call()
            key_at = a + lead - w.hands.pause_lead(hid) if name == "park" else a     # when the park's key went out
            steps.append((st, key_at, clk.now()))
        for st, a, b in steps:
            rows.append({"move": i + 1, "step": st.step, "outcome": st.outcome, "took_s": round(b - a, 2), "why": st.why,
                         "flip_s": st.numbers.get("flip_s"), "jump_s": st.numbers.get("jump_s"), "at": a, "end": b,
                         "budget": w.trace.budget(a, b)})
        if not steps[-1][0].ok:
            break
        if look:
            await clk.sleep(look)
    for s in c.sticks:
        await w.hands._settle(s)
    rows.append({"move": 0, "step": "finish", "outcome": (await c.park_finish()).outcome})
    c.tv.sound_guard.stop()
    forbidden = w.a.forbidden() + w.b.forbidden()
    assert not forbidden, f"PARK sent {forbidden}"
    return rows


def bench(fast: bool, moves: int = 20, look: float = 16.0, lat: Lat | None = None, seed: int = 0) -> list[dict]:
    lat = lat or Lat()
    real_time = yttv_timing.time
    real_tuner = ctl.TUNERS.get("youtubetv")
    with tempfile.TemporaryDirectory(prefix="rz-bench-") as d:
        w = build(fast, lat, Path(d), seed)
        yttv_timing.time = SimpleNamespace(time=w.clk.now)
        ctl.TUNERS["youtubetv"] = w.tuner
        try:
            return asyncio.run(run_moves(w, moves, look, seed))
        finally:
            yttv_timing.time = real_time
            if real_tuner is not None:
                ctl.TUNERS["youtubetv"] = real_tuner


def summary(rows: list[dict]) -> dict:
    out = {}
    for step in ("tune", "park", "jump"):
        rs = [r for r in rows if r["step"] == step and r["outcome"] == "ok" and r["took_s"] > 1.0]
        if not rs:
            continue
        cats: dict = {}
        for r in rs:
            for k, v in r["budget"].items():
                cats.setdefault(k, []).append(v)
        out[step] = {"median_s": round(statistics.median(r["took_s"] for r in rs), 2), "n": len(rs),
                     "budget_s": {k: round(statistics.median(v + [0.0] * (len(rs) - len(v))), 2) for k, v in cats.items()}}
        if step == "jump":
            out[step]["flip_s"] = round(statistics.median(r["flip_s"] for r in rs), 2)
            out[step]["picture_s"] = round(statistics.median(r["jump_s"] for r in rs), 2)
    out["not_ok"] = [f"{r['step']} {r['outcome']}: {r['why']}" for r in rows if r.get("outcome") not in ("ok",)]
    return out


def cycles(rows: list[dict]) -> float | None:
    """Back to back (--look 0): the median seconds from one jump's start to the next jump's start."""
    starts = [r["at"] for r in rows if r["step"] == "jump" and r["outcome"] == "ok"]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    return round(statistics.median(gaps), 2) if gaps else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--moves", type=int, default=20)
    ap.add_argument("--look", type=float, default=16.0, help="seconds on each picture before the jump and after it")
    ap.add_argument("--json", default="", help="write both runs' summaries here")
    a = ap.parse_args()
    res = {}
    for fast in (False, True):
        rows = bench(fast, a.moves, a.look)
        res["on" if fast else "off"] = {**summary(rows), "cycle_s": cycles(rows)}
    print(f"PARK step bench (fake sticks and TV, NOT measured on the TV): {a.moves} moves, look {a.look:g} s; "
          f"tonight on the TV: {TONIGHT}")
    for step in ("tune", "park", "jump"):
        for k in ("off", "on"):
            s = res[k].get(step)
            if s:
                extra = f", flip {s['flip_s']} s, picture up {s['picture_s']} s" if step == "jump" else ""
                print(f"  {step:<5} flag {k:<3} {s['median_s']:5.2f} s (n={s['n']}{extra})  budget {s['budget_s']}")
    for k in ("off", "on"):
        print(f"  flag {k}: jump-to-jump {res[k]['cycle_s']} s; not ok: {res[k]['not_ok'] or 'none'}")
    if a.json:
        Path(a.json).write_text(json.dumps({"lat": asdict(Lat()), **res}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
