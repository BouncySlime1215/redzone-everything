"""Phone presses during the end-to-end night: does every button do what it says, and how fast?

Plugs into the night like a fault (`--faults redzone.lab.night.presses:controls`). Each press goes
through the same path as a tap on the phone (POST /api/action: engine.action, then decide and
broadcast) at a seeded time while games are live. grade(night) then reads what the TV really
showed (house.shown) and scores every press:

  go_now   "Watch this" tapped twice (hold + force): the TV shows the game playing. press->TV
           seconds (target p95 <= 3 s once pre-tuned; a cold tune includes the app's own load).
  lock     "Watch this" (hold) then, `dur` later, Resume auto: once the game is on, the TV never
           leaves it before the release.
  pause    Pause then Resume: no input change and no new channel while paused.
  skip     Skip: the TV leaves the skipped game.
  open_go  The game's sheet opened on the phone (action "peek"), then "Watch this" + "Go now" `dur`
           seconds later, as a person reads the sheet first. With two_stick.pretune_on_open the
           hidden stick is tuned while they read. Graded like go_now, timed from the "Go now".

A press that answered ok=False, or that the TV never followed, is "dropped".

Every press carries a tap id through engine.commands (as the phone's POST /api/action does). With
RZ_FLAKY=1 the network misbehaves on purpose: a request is lost before it arrives, an answer is lost
so the phone resends the same id, or the same id arrives twice. grade() checks every tap ran exactly
once ("not_once" lists the ones that didn't).

    python scripts/controls_drill.py --seeds 4
"""
from __future__ import annotations

import os
import random
import time

from redzone.lab.night.faults import Fault

GO_NOW_WITHIN_S = 3.0      # Nick's target, press -> the game playing on the TV
GIVE_UP_S = 120.0          # a "now" press the TV hasn't followed after this long was dropped
SAFE_GIVE_UP_S = 600.0     # a lock / skip waits for a safe moment (never mid-play): this long


_N = [0]


def _press(world, action: str, gid: str | None = None, value=None) -> dict:
    e = world.engine
    t = time.time()
    _N[0] += 1
    cid = f"drill-{_N[0]:05d}"
    runs = [0]

    def run():
        runs[0] += 1
        return e.action(action, gid, value)

    net = "ok"
    if os.environ.get("RZ_FLAKY") == "1":
        net = random.Random(cid).choice(("ok", "lost_request", "lost_answer", "twice"))
    sends = {"ok": 1, "lost_request": 1, "lost_answer": 2, "twice": 2}[net]
    rec = None
    taps = getattr(e, "commands", None)   # None = a build before tap ids: every send runs
    for _ in range(sends):   # lost_request: the first send never arrived, so only the retry reaches us
        try:
            if taps is not None:
                rec = taps.run(cid, action, gid, value, run)
            else:
                ok, msg = run()
                rec = {"ok": ok, "message": msg}
        except Exception as ex:   # a tap that crashes is a dropped tap
            rec = {"ok": False, "message": f"crashed: {type(ex).__name__}: {ex}"}
    ok, msg = rec["ok"], rec["message"]
    world.__dict__.setdefault("taps", []).append({"id": cid, "action": action, "net": net, "runs": runs[0]})
    try:
        e.decide()
        e.broadcast()
    except Exception:
        pass
    return {"t": t, "action": action, "gid": gid, "value": value, "ok": bool(ok), "msg": str(msg)[:120],
            "current": e.decider.current}


def _target(world, gid: str | None) -> str | None:
    w = (getattr(world.engine, "where", {}) or {}).get(gid or "")
    return getattr(w, "target", None)


def _other_live(world, rng: random.Random) -> str | None:
    """A live game with somewhere to watch it, not the one on screen."""
    from redzone.models import Phase
    e = world.engine
    cur = e.decider.current
    gids = sorted(g.game_id for g in e.merger.games.values()
                  if g.phase == Phase.LIVE and g.game_id != cur and _target(world, g.game_id))
    return rng.choice(gids) if gids else None


class Press(Fault):
    """One scripted tap (or tap + release after `dur`)."""

    def __init__(self, name: str, at: float, dur: float = 0.0, rng: random.Random | None = None):
        super().__init__(name, at, dur)
        self.rng = rng or random.Random(0)
        self.rec: dict | None = None

    def start(self, world):
        log = world.__dict__.setdefault("presses", [])
        if self.name == "open_go":
            gid = _other_live(world, self.rng)
            if gid is None:
                self.rec = {"t": time.time(), "action": self.name, "skipped": "no other live game"}
                log.append(self.rec)
                return
            _press(world, "peek", gid)
            self.rec = {"t": time.time(), "kind": "open_go", "gid": gid, "target": _target(world, gid),
                        "ok": True, "msg": "", "pending": True}
        elif self.name in ("go_now", "lock"):
            gid = _other_live(world, self.rng)
            if gid is None:
                self.rec = {"t": time.time(), "action": self.name, "skipped": "no other live game"}
                log.append(self.rec)
                return
            first = _press(world, "hold", gid)
            if self.name == "go_now":   # the second tap while it waits = "now"
                second = _press(world, "hold", gid, "force")
                first = {**first, "ok": first["ok"] and second["ok"], "msg": second["msg"]}
            self.rec = {**first, "kind": self.name, "target": _target(world, gid)}
        elif self.name == "pause":
            self.rec = {**_press(world, "pause"), "kind": "pause"}
        elif self.name == "skip":
            cur = world.engine.decider.current
            self.rec = {**_press(world, "skip"), "kind": "skip", "gid": cur, "target": _target(world, cur)}
        log.append(self.rec)

    def stop(self, world):
        if self.rec is None or self.rec.get("skipped"):
            return
        if self.name == "open_go":
            gid = self.rec["gid"]
            first = _press(world, "hold", gid)
            second = _press(world, "hold", gid, "force")
            self.rec.update(t=first["t"], ok=first["ok"] and second["ok"], msg=second["msg"],
                            current=first["current"], target=_target(world, gid), pending=False)
            if self.rec["current"] == gid:
                self.rec["skipped"] = "already on it"
        elif self.name == "lock":
            self.rec["released"] = _press(world, "release")["t"]
        elif self.name == "pause":
            self.rec["released"] = _press(world, "resume")["t"]


def _time_ticks(world) -> None:
    """Real CPU milliseconds of every decision tick (engine.decide), for the tick p99 report. The
    night's clock is virtual; perf_counter isn't."""
    e = world.engine
    if getattr(e, "_tick_ms", None) is not None:
        return
    e._tick_ms = []
    inner = e.decide

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return inner(*a, **k)
        finally:
            e._tick_ms.append((time.perf_counter() - t) * 1000)
    e.decide = timed


def controls(world, rng: random.Random) -> list[Fault]:
    """A few of each press per night, at seeded times while at least two games are live."""
    _time_ticks(world)
    wins = world.live_windows
    # Leave every press room to finish (a lock or skip may wait SAFE_GIVE_UP_S for a dead ball)
    # before the night ends: a press cut off by --hours says nothing about the press.
    end = min(max(hi for _, hi in wins), world.t1 - world.t0) - SAFE_GIVE_UP_S

    def two_live(t: float) -> bool:
        return sum(1 for lo, hi in wins if lo + 300 <= t <= hi - 600) >= 2

    times = [t for t in range(0, int(end), 60) if two_live(t)]
    if not times:
        return []
    out: list[Fault] = []
    picks = sorted(rng.sample(times, min(len(times), 16)))
    if os.environ.get("RZ_PRETUNE") == "1":
        world.engine.cfg.update({"two_stick": {"pretune_on_open": True}})
    plan = ["go_now"] * 4 + ["open_go"] * 4 + ["lock"] * 3 + ["pause"] * 3 + ["skip"] * 2
    rng.shuffle(plan)
    last = -1e9
    for t, kind in zip(picks, plan):
        if t - last < 420:          # let one press finish (a lock lasts up to 5 min) before the next
            continue
        last = t
        dur = {"lock": rng.uniform(180, 300), "pause": rng.uniform(60, 180),
               "open_go": rng.uniform(4, 12)}.get(kind, 0.0)
        out.append(Press(kind, float(t), dur, random.Random(rng.random())))
    return out


def _playing_at(shown: list, target: str, t0: float, t1: float) -> float | None:
    """First time in [t0, t1] the TV showed `target` playing."""
    for i, s in enumerate(shown):
        end = shown[i + 1].t if i + 1 < len(shown) else float("inf")
        if end < t0 or s.t > t1:
            continue
        if s.target == target and s.what == "playing":
            return max(s.t, t0)
    return None


def grade(world) -> dict:
    shown = world.house.shown
    out = {"go_now_s": [], "open_go_s": [], "lock_s": [], "skip_s": [], "dropped": [], "lock_broken": [], "pause_broken": [],
           "skip_stuck": [], "lock_waited_out": [], "n": {}}
    out["press_at"] = [(r.get("kind") or r.get("action"), round(r["t"], 1)) for r in getattr(world, "presses", [])]
    taps = getattr(world, "taps", [])
    out["taps"] = len(taps)
    out["taps_retried"] = sum(1 for x in taps if x["net"] != "ok")
    out["not_once"] = [x for x in taps if x["runs"] != 1]
    for r in getattr(world, "presses", []):
        if r.get("skipped") or r.get("pending"):
            continue
        kind = r["kind"]
        out["n"][kind] = out["n"].get(kind, 0) + 1
        if not r["ok"]:
            out["dropped"].append({"kind": kind, "msg": r["msg"]})
            continue
        if kind in ("go_now", "open_go", "lock"):
            give = GIVE_UP_S if kind != "lock" else SAFE_GIVE_UP_S
            end = r.get("released", float("inf")) if kind == "lock" else float("inf")
            at = _playing_at(shown, r["target"], r["t"], min(r["t"] + give, end)) if r.get("target") else None
            if at is None and kind == "lock" and r.get("released", float("inf")) - r["t"] < SAFE_GIVE_UP_S:
                # Released while still waiting for a dead ball in the game on screen (rule #1): the
                # press was honored, the cut never became safe. Reported, not a dropped press.
                out["lock_waited_out"].append({"target": r["target"], "released_after_s": round(r["released"] - r["t"], 1)})
                continue
            if at is None:
                out["dropped"].append({"kind": kind, "msg": f"TV never showed {r.get('target')} playing"})
                continue
            if kind in ("go_now", "open_go"):
                out[kind + "_s"].append(round(at - r["t"], 2))
            else:
                out["lock_s"].append(round(at - r["t"], 1))
                end = r.get("released", float("inf"))
                left = [s for s in shown if at < s.t < end and s.target not in (r["target"], None)
                        and s.what == "playing"]
                if left:
                    out["lock_broken"].append({"target": r["target"], "left_for": left[0].target,
                                               "after_s": round(left[0].t - at, 1)})
        elif kind == "pause":
            end = r.get("released", r["t"] + GIVE_UP_S)
            before = next((s for s in reversed(shown) if s.t <= r["t"]), None)
            moved = [s for s in shown if r["t"] < s.t < end and s.target and before is not None
                     and s.target != before.target and s.what == "playing"]
            if moved:
                out["pause_broken"].append({"to": moved[0].target, "after_s": round(moved[0].t - r["t"], 1)})
        elif kind == "skip" and r.get("target"):
            gone = next((s for s in shown if s.t > r["t"] and s.target and s.target != r["target"]
                         and s.what == "playing"), None)
            if gone is None or gone.t - r["t"] > SAFE_GIVE_UP_S:
                out["skip_stuck"].append({"target": r["target"]})
            else:
                out["skip_s"].append(round(gone.t - r["t"], 1))
    ticks = sorted(getattr(world.engine, "_tick_ms", None) or [])
    if ticks:
        out["tick_n"] = len(ticks)
        out["tick_p50_ms"] = round(ticks[len(ticks) // 2], 2)
        out["tick_p99_ms"] = round(ticks[min(len(ticks) - 1, int(0.99 * len(ticks)))], 2)
        out["tick_max_ms"] = round(ticks[-1], 2)
    try:
        import resource
        import sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["rss_peak_mb"] = round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        pass
    xs = sorted(out["go_now_s"] + out["open_go_s"])
    q = (lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]) if xs else (lambda p: None)
    out["go_now_p50_s"], out["go_now_p95_s"], out["go_now_max_s"] = q(0.5), q(0.95), (xs[-1] if xs else None)
    out["go_now_within_pct"] = round(100 * sum(x <= GO_NOW_WITHIN_S for x in xs) / len(xs), 1) if xs else None
    return out
