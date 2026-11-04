"""Scenario-tree lookup on the Mac (idea #19, Mac half; docs/playbooks.md GAME-*). Flag
`playbooks.game.enabled` (OFF).

The slate is discretized into one STATE per live game (what its screen shows right now), and a TABLE
of branches says what to do when the states match: "A (on screen) in a TV timeout while B is in the
red zone -> B now, back to A at its restart". The cloud will build the full table from the oracle
(idea #19, cloud half) in the same format; until then it holds the hand-written GAME-01..03 seeds.

Table format (JSON-able, one row per branch, first match wins; `scenarios.table` can point at a file):
    {"id": "GAME-01", "on": ["long_break"], "other": ["redzone"], "league": "nfl",
     "do": "visit", "back": "restart"}
  on      states the game on screen (A) must be in (any of)
  other   states another live game (B) must be in (any of); [] = none needed
  league  B's league (optional)
  do      "visit" (switch to B; A stays home, come back at A's restart), "switch" (go to B), or
          "hold" (stay on A: a soft lock)
  back    "restart" (return to A when its break ends) or "" (no return planned)
  a_crunch  false: never while A is in crunch time (crunch first, L1)

A live plan is kept until a branch's state stops matching (invalidation); then the slate is looked up
again (re-plan). Lookup is a scan of a few rows per game (<1 ms). Every decision a branch makes carries
`playbook=<id>` in its reason and still goes through entry timing and the safety playbooks (rule #1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from redzone.decider.playbooks import LONG_BREAKS

SEED = [
    # GAME-03: your team in crunch time is on screen: soft lock until it ends or turns into a blowout.
    {"id": "GAME-03", "on": ["my_crunch"], "other": [], "do": "hold", "back": ""},
    # GAME-03: your team's crunch starts elsewhere: go now (entry timing still lands it on a dead ball).
    {"id": "GAME-03", "on": ["long_break", "break", "live", "redzone", "pp", "crunch"], "other": ["my_crunch"],
     "do": "switch", "back": ""},
    # GAME-01: A in a TV timeout / long break while B is in the red zone: B now, back to A at the restart.
    {"id": "GAME-01", "on": ["long_break"], "other": ["redzone"], "league": "nfl", "do": "visit", "back": "restart",
     "a_crunch": False},
    # GAME-02: A just scored (dead after the score) while C has a power play: C next at its first whistle.
    {"id": "GAME-02", "on": ["after_score"], "other": ["pp"], "league": "nhl", "do": "visit", "back": "restart",
     "a_crunch": False},
]


@dataclass
class Plan:
    branch: dict
    a: str                 # the game on screen when it matched (home for a visit)
    b: str | None          # the game the branch acts on


def load(cfg) -> list[dict]:
    path = cfg.get("playbooks.game.table") if cfg is not None else None
    if path:
        with open(path) as f:
            return list(json.load(f))
    return SEED


def state_of(dec, gid: str, x, now: float) -> str:
    """One word for what game gid's screen shows now (the discretized slate state)."""
    s = x.scored
    hold = dec.cfg.get("decider.crunch.hold_personal", 5)
    if s.crunch and s.personal >= hold and not s.garbage:
        return "my_crunch"
    if s.is_break:
        tl = dec.timelines.get(gid)
        if tl is not None and x.target:
            d = dec.delays.get(x.target)
            t = now - d.mu            # what A's screen shows now (mu includes any time-shift)
            land = tl.dead.landing(t, t)
            if land.state == "dead" and land.kind in LONG_BREAKS:
                return "long_break"
            if land.state == "dead" and land.kind in ("score", "goal"):
                return "after_score"
        return "break"
    if s.lock == "red zone drive":
        return "redzone"
    if s.lock == "power play" or s.pre_moment == "power play":
        return "pp"
    if s.crunch:
        return "crunch"
    return "live"


class Scenarios:
    def __init__(self, cfg):
        self.cfg = cfg
        self.table = load(cfg)
        self.plan: Plan | None = None
        self.seen: dict[str, int] = {}

    def enabled(self) -> bool:
        return bool(self.cfg.get("playbooks.game.enabled", False)) if self.cfg is not None else False

    def slate(self, dec, cands: dict, now: float) -> dict[str, str]:
        return {gid: state_of(dec, gid, x, now) for gid, x in cands.items()
                if x.target and not x.stale and gid not in dec.user.muted}

    def lookup(self, dec, slate: dict[str, str], cands: dict) -> Plan | None:
        a = dec.current
        if a not in slate:
            return None
        for br in self.table:
            if slate[a] not in br["on"]:
                continue
            if br.get("a_crunch") is False and cands[a].scored.crunch:
                continue
            if not br["other"]:
                return Plan(br, a, None)
            lg = br.get("league")
            hits = [g for g, st in slate.items() if g != a and st in br["other"]
                    and (lg is None or cands[g].scored.league.base.value == lg)]
            if hits:
                return Plan(br, a, max(hits, key=lambda g: cands[g].scored.score))
        return None

    def valid(self, slate: dict[str, str]) -> bool:
        """The live plan still holds: B is still in the state that justified it (a visit's way home is
        the decider's own home-return logic, not the plan)."""
        p = self.plan
        if p is None:
            return False
        if p.b is None:
            return slate.get(p.a) in p.branch["on"]
        return slate.get(p.b) in p.branch["other"]

    def step(self, dec, cands: dict, now: float):
        """The branch to act on this tick (re-planned when the live one is invalidated), else None."""
        sl = self.slate(dec, cands, now)
        if not self.valid(sl) or (self.plan is not None and self.plan.a != dec.current
                                  and self.plan.b != dec.current):
            self.plan = None
        if self.plan is None:
            self.plan = self.lookup(dec, sl, cands)
        return self.plan
