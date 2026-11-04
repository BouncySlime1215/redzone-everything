"""[shadow] EXP-004, the Mac half: the candidate decider runs silently beside live.

Serves: EXP-004 (docs/experiments.md), North Star "Release gate (fresh nights)". Owner: lane 3.

A second Decider, with a Noggin profile's overrides on top of today's config (redzone/noggin/profile.py,
default "candidate"), sees exactly what the live decider sees: the same scored candidates every tick
and the same play-by-play (its own Timelines, fed at the same points). It never touches the controller
or the TV: it believes its own switches happen at once (commit=True, like a dry run), and every call it
makes is logged to data/shadow.jsonl next to what live had on screen, for the next morning's scoring
(lane 4's half). Flag `shadow.enabled` (off). Any error turns it off for the night: the live decider
never waits on it or breaks because of it.

What it can't see: the controller's time-shift holds (the candidate's behind_live buffer) and real
switch outcomes. It reads the live delay book and never writes to it, so those stay live's.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

import yaml

from redzone.decider.decider import Candidate, Decider
from redzone.models import League

log = logging.getLogger(__name__)


class ShadowConfig:
    """Today's config with a profile's overrides on top. Reads only (the decider never writes config).
    Rebuilt when the live config's data object changes (the phone or a measurement edits it)."""

    def __init__(self, base, sets: list[str]):
        self.base = base
        self.over = {}
        for kv in sets:
            key, _, val = kv.partition("=")
            self.over[key] = yaml.safe_load(val)
        self._src = None
        self.data: dict = {}
        self._refresh()

    def _refresh(self) -> None:
        if self._src is self.base.data:
            return
        self._src = self.base.data
        data = copy.deepcopy(self.base.data)
        for key, val in self.over.items():
            node = data
            *path, last = key.split(".")
            for part in path:
                node = node.setdefault(part, {})
            node[last] = val
        self.data = data

    def get(self, dotted: str, default=None):
        self._refresh()
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class ReadOnlyDelays:
    """The live delay book, read-only: the shadow's own holds never shift live's delays."""

    def __init__(self, live):
        self._live = live

    def get(self, target: str):
        return self._live.get(target)

    def set_behind(self, *_a, **_k) -> None:
        return None

    def __getattr__(self, name):   # reads only (mu, sigma, by-app tables)
        return getattr(self._live, name)


class Shadow:
    def __init__(self, cfg, delays, log_path: Path | None, clock=time.time):
        from redzone.noggin import profile as P
        self.cfg = cfg
        self.profile = str(cfg.get("shadow.profile"))
        self.decider = Decider(ShadowConfig(cfg, P.PROFILES[self.profile]["set"]), ReadOnlyDelays(delays), clock=clock)
        self.log_path = log_path
        self.clock = clock
        self.alive = True
        self.calls = 0
        self.spent_s = 0.0

    def observe(self, game_id: str, league: League, game) -> None:
        """The same play-by-play the live decider just observed."""
        if not self.alive:
            return
        try:
            self.decider.timeline(game_id, league).observe(game)
        except Exception:
            self._die("observe")

    def tick(self, cands: dict[str, Candidate], redzone: str | None, live_current: str | None,
             live_action: str) -> None:
        """One decision beside live's. Logged when it differs from staying put (or from live)."""
        if not self.alive:
            return
        t0 = time.perf_counter()
        try:
            d = self.decider.decide(cands, redzone, commit=True)
            self.calls += 1
            if d.action != "stay" or self.decider.current != live_current:
                self._write({"ts": round(self.clock(), 2), "profile": self.profile, "action": d.action,
                             "target": d.target, "kind": d.kind, "reason": str(d.reason)[:160],
                             "on": self.decider.current, "live_on": live_current, "live_action": live_action})
        except Exception:
            self._die("decide")
        finally:
            self.spent_s += time.perf_counter() - t0

    def _write(self, row: dict) -> None:
        if self.log_path is None:
            return
        with self.log_path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def _die(self, where: str) -> None:
        log.exception("shadow decider failed in %s: off for the rest of the night", where)
        self.alive = False
        try:
            self._write({"ts": round(self.clock(), 2), "action": "shadow_off", "reason": f"error in {where}"})
        except OSError:
            pass

    def view(self) -> dict:
        return {"profile": self.profile, "alive": self.alive, "calls": self.calls,
                "ms_per_call": round(1000 * self.spent_s / self.calls, 2) if self.calls else None,
                "on": self.decider.current}
