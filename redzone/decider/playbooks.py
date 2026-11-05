"""Safety playbooks SAFE-01/02/03/05 (docs/playbooks.md, family flag `playbooks.safe.enabled`, OFF).

Each entry is "given X, do Y": a pure check of the live state that turns a switch into a hold and
writes `playbook=<id>` into the decision's reason, so the End Planner and 👍/👎 can audit it. Rule #1
beats every playbook, so every action here is a HOLD (stay on the game on screen); none of them can
cause a cut. Nick's own buttons (kind "user") and the night's first pick are never held.

  SAFE-01  the target's feed is stale (> cadence + 10 s)      no cut into it; if the game on screen
                                                              is stale too, stay put
  SAFE-02  the target's delay doubt (sigma) > 3 s             no cut unless the entry lands on a dead
                                                              ball at the FULL margin (no "centred" or
                                                              "urgent" shortcut, no going in live)
  SAFE-03  Wi-Fi drop / buffering > 2 per minute on a stick   safe mode: cut only into a long break
                                                              (TV timeout, period end, halftime...)
  NET-01   every feed stale (the Mac is offline)             no cut of any kind, catch-up replays too:
                                                              keep the game on screen until a feed is back
  SAFE-05  the wall clock jumped (> 5 s vs monotonic)         no cut until re-anchored: every live
                                                              timeline has heard from its feed since,
                                                              or 90 s have passed

Cost: a dict lookup per signal and at most one entry plan per switch (<1 ms).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

# Dead windows long enough that a landing in one is safe even on a shaky picture (cuts.py kinds).
LONG_BREAKS = frozenset({"halftime", "intermission", "end_period", "inning", "tv_timeout", "media_timeout",
                         "two_minute", "timeout_long"})

DEFAULTS = {"feed_stale_s": 10.0, "sigma_s": 10.0, "net_q": 9.0, "net_window_s": 300.0,
            "skew_s": 5.0, "reanchor_s": 90.0}


@dataclass
class Safety:
    cfg: object
    # gid -> (seconds since the feed last had fresh data, the game's poll cadence); None: unknown
    feed_age: Callable[[str], tuple[float, float] | None] | None = None
    net: dict[str, tuple[float, float]] = field(default_factory=dict)   # stick/target -> (q_scale, mono t)
    _offset: float | None = None
    skew_at: float | None = None       # monotonic time a wall-clock jump was seen
    skew_s: float = 0.0
    _fresh0: dict | None = None        # each live timeline's fresh_at when the jump was seen
    checked: int = 0                   # switches/replays the gate looked at (night-lab audit)
    seen: dict[str, int] = field(default_factory=dict)   # playbook id -> holds (night-lab audit)
    events: deque = field(default_factory=lambda: deque(maxlen=500))   # (wall t, playbook id, game held from)

    def opt(self, k: str) -> float:
        v = self.cfg.get(f"playbooks.safe.{k}") if self.cfg is not None else None
        return float(DEFAULTS[k] if v is None else v)

    def enabled(self) -> bool:
        return bool(self.cfg.get("playbooks.safe.enabled", False)) if self.cfg is not None else False

    # ---- signals -------------------------------------------------------------------------------

    def tick(self) -> None:
        """SAFE-05's detector: the wall clock moved against the monotonic one."""
        off = time.time() - time.monotonic()
        if self._offset is not None and abs(off - self._offset) > self.opt("skew_s"):
            self.skew_at, self.skew_s, self._fresh0 = time.monotonic(), off - self._offset, None
        self._offset = off

    def condition(self, who: str, q_scale: float) -> None:
        """SAFE-03's signal: the sync probe's network condition for a stick (1 = clean; each buffering
        event per minute adds 4, a >=10 dB RSSI drop adds 8; sync_probe._condition)."""
        self.net[who] = (float(q_scale), time.monotonic())

    def degraded(self) -> str | None:
        now, lim, win = time.monotonic(), self.opt("net_q"), self.opt("net_window_s")
        for who, (q, t) in self.net.items():
            if q >= lim and now - t <= win:
                return f"{who} q={q:.0f}"
        return None

    def _stale(self, gid: str, x, tl, now: float) -> str | None:
        if x is None:
            return "no feed"
        if getattr(x, "stale", False):
            return "feed dark"
        fa = self.feed_age(gid) if self.feed_age is not None else None
        if fa is None and tl is not None and tl.fresh_at is not None:
            fa = (now - tl.fresh_at, float(self.cfg.get("polling.live_s", 10) or 10))
        if fa is not None:
            age, cad = fa
            if age > cad + self.opt("feed_stale_s"):
                return f"feed {age:.0f} s old"
        return None

    def _reanchoring(self, dec, now: float) -> bool:
        if self.skew_at is None:
            return False
        if time.monotonic() - self.skew_at >= self.opt("reanchor_s"):
            self.skew_at, self._fresh0 = None, None
            return False
        # Re-anchored once every live timeline has had a fresh poll since the jump. Compared by value,
        # not by time: after a backward jump every old stamp already looks "later" than the jump.
        tls = {gid: tl for gid, tl in dec.timelines.items() if gid in dec._known}
        if self._fresh0 is None:
            self._fresh0 = {gid: tl.fresh_at for gid, tl in tls.items()}
        if tls and all(tl.fresh_at != self._fresh0.get(gid) for gid, tl in tls.items()):
            self.skew_at, self._fresh0 = None, None
            return False
        return True

    # ---- the gate ------------------------------------------------------------------------------

    def check(self, dec, d, cands: dict, now: float) -> tuple[str, str] | None:
        """(playbook id, why) when this switch must be held, else None."""
        if d.action not in ("switch", "replay") or d.kind in ("user", "start") or dec.current is None:
            return None
        if d.action == "switch" and d.target == dec.current:
            return None
        self.checked += 1
        if self._reanchoring(dec, now):
            return "SAFE-05", f"wall clock jumped {self.skew_s:+.0f} s; re-anchoring"
        # NET-01: every feed is stale (the Mac is offline): the leave can't be judged either, so no cut of
        # any kind, a catch-up replay included (it's off a game whose state we no longer get).
        cur_stale = self._stale(dec.current, cands.get(dec.current), dec.timelines.get(dec.current), now)
        if cur_stale and d.target and self._stale(d.target, cands.get(d.target), dec.timelines.get(d.target), now):
            return "NET-01", f"every feed stale (on screen: {cur_stale}); offline, keeping the game on screen"
        if d.action != "switch" or not d.target:
            return None
        x = cands.get(d.target)
        why = self._stale(d.target, x, dec.timelines.get(d.target), now)
        if why:
            cur = cands.get(dec.current)
            return "SAFE-01", why + ("; on-screen feed stale too, staying put" if self._stale(dec.current, cur, dec.timelines.get(dec.current), now) else "")
        if x is None or not x.target:
            return None
        sd = dec.delays.get(x.target).current_sigma(now)
        net = self.degraded()
        if sd <= self.opt("sigma_s") and net is None:
            return None
        plan = dec.entry_plan(d.target, cands, now)
        land = plan.landing if plan is not None and plan.ok else None
        if net is not None and (land is None or land.state != "dead" or land.kind not in LONG_BREAKS):
            return "SAFE-03", f"network degraded ({net}): long breaks only"
        if sd > self.opt("sigma_s") and (land is None or land.state != "dead"
                                         or "(centred)" in plan.why or "(urgent)" in plan.why):
            return "SAFE-02", f"delay doubt {sd:.1f} s: waiting for a sure dead ball"
        return None
