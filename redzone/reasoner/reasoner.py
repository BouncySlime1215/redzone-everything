"""The live game reasoner at runtime: one reading per live game, rebuilt on each new play.

    Reasoner.read(g, row, reading, ctx) -> Reading     every poll, every live game (head only, < 1 ms)
    Reasoner.narrate(...)                               event-driven, at most every min_interval_s per
                                                        hot game: the language model's JSON, in the
                                                        background (never blocks a poll)
    lock_points(reading, cfg, league)                   the scorer's bounded points for lock_in

What the decider may do with it (and what it never can):
  * lock_in adds at most `reasoner.max_points` (hard cap 15) moment points to its game, before the
    garbage-time multiplier, so a decided game gets nothing. Moment points never touch the hard
    rules: mid-play cut timing, crunch time elsewhere, your team late and garbage time are all
    decided by code that doesn't read these points (redzone/decider).
  * The model's text never moves the TV. With `reasoner.decide_with: model` the model's lock_in
    replaces the head's (same cap, same rules); the default is the head, which is the one the
    scorecards measured.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from redzone.models import GameState, Phase, REAL_BREAKS
from redzone.reasoner import brief
from redzone.reasoner.head import LEVELS, Head

log = logging.getLogger(__name__)

ADAPTER = "game-reasoner"
MAX_POINTS = 15.0      # a hard cap, whatever the config says
MAX_GAMES = 300

SYSTEM = (
    "You are RedZone's live game reasoner. You read a brief of one live game and say what it has "
    "going right now, for a viewer flipping between games. Answer with ONE JSON object only, keys "
    "in this order: lock_in (none|watch|must_see), safe_to_leave (yes|no: safe to leave at the next "
    "dead ball), confidence (0-1), storyline (one short line), why (one short line), next (what to "
    "expect and roughly when), stay_s (seconds worth staying, 0-900). Never invent facts that are not "
    "in the brief. Garbage time is never must_see.")


@dataclass
class Narration:
    lock_in: str = "none"
    safe_to_leave: str = "yes"
    confidence: float = 0.0
    storyline: str = ""
    why: str = ""
    next: str = ""
    stay_s: int = 0
    at: float = 0.0
    ms: float = 0.0
    events: int = 0


@dataclass
class Reading:
    p: float | None = None           # head odds of a big/crunch play in the next few minutes
    lock_in: str = "none"            # what the scorer uses (head, or model with decide_with: model)
    head_lock_in: str = "none"
    events: int = 0
    narration: Narration | None = None
    facts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        n = self.narration
        return {"p": None if self.p is None else round(self.p, 3), "lock_in": self.lock_in,
                "head_lock_in": self.head_lock_in,
                "narration": asdict(n) if n is not None else None}


EMPTY = Reading()


def parse_answer(text: str) -> Narration | None:
    """The model's JSON, validated and clamped. None for anything unusable (never raises)."""
    from redzone.jsonfind import first_json_object
    d = first_json_object(text)
    if not isinstance(d, dict) or d.get("lock_in") not in LEVELS:
        return None

    def s(k, n=120):
        v = d.get(k, "")
        return " ".join(str(v).split())[:n] if v is not None else ""
    try:
        conf = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        stay = int(float(d.get("stay_s", 0)))
    except (TypeError, ValueError):
        stay = 0
    safe = str(d.get("safe_to_leave", "yes")).lower()
    return Narration(lock_in=d["lock_in"], safe_to_leave="no" if safe in ("no", "false") else "yes",
                     confidence=max(0.0, min(1.0, conf if math.isfinite(conf) else 0.0)),
                     storyline=s("storyline"), why=s("why"), next=s("next"), stay_s=max(0, min(900, stay)))


def lock_points(r: Reading | None, c, league: str = "") -> tuple[float, tuple[float, str] | None]:
    """(moment points, reason part) for a reading. Bounded: 0 <= points <= min(max_points, 15)."""
    if r is None or r.lock_in == "none":
        return 0.0, None

    def knob(name: str, default: float) -> float:
        v = c.get(f"reasoner.{league}.{name}") if league else None
        try:
            return float(v if v is not None else c.get(f"reasoner.{name}", default))
        except (TypeError, ValueError):
            return default
    cap = max(0.0, min(MAX_POINTS, knob("max_points", 12)))
    pts = knob("must_points", 8) if r.lock_in == "must_see" else knob("watch_points", 3)
    if knob("by_odds", 0) > 0 and r.p is not None:
        # points grow with how far the odds clear the must_see cut (crunch time counts as sure)
        base = max(0.0, min(0.99, knob("odds_floor", 0.2)))
        p = 1.0 if (r.facts or {}).get("crunch") else r.p
        pts *= max(0.0, min(1.0, (p - base) / (1.0 - base)))
    pts = max(0.0, min(cap, pts))
    bb = r.facts.get("brain_break") if r.facts else None
    if bb is not None and bb == bb and knob("break_discount", 0) > 0:
        # a break about to start buys commercials, not plays: scale down by the brain's break odds
        pts *= max(0.0, 1.0 - knob("break_discount", 0) * float(bb))
    if pts <= 0:
        return 0.0, None
    line = r.narration.storyline if r.narration and r.narration.storyline else ""
    why = line or ("lock in: big moment likely" if r.lock_in == "must_see" else "worth watching")
    return pts, (pts + 4, why)


class Client:
    """agent/llm_server.py over localhost HTTP (stdlib only). Any failure -> None, never a raise."""

    def __init__(self, url: str = "http://127.0.0.1:8779", timeout_s: float = 20.0):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.down_until = 0.0

    def _post(self, path: str, body: dict) -> dict | None:
        import urllib.request
        if time.time() < self.down_until:
            return None
        try:
            req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:   # noqa: S310 - localhost only
                return json.loads(resp.read().decode() or "{}")
        except Exception as e:  # noqa: BLE001 - the server being down is normal (unloaded, not installed)
            log.debug("reasoner: llm server %s failed: %s", path, e)
            self.down_until = time.time() + 60.0   # back off a minute, don't hammer a dead port
            return None

    def generate(self, messages: list[dict], max_tokens: int = 160) -> tuple[str, float] | None:
        out = self._post("/generate", {"adapter": ADAPTER, "messages": messages, "max_tokens": max_tokens})
        if not out or not isinstance(out.get("text"), str):
            return None
        return out["text"], float(out.get("ms") or 0.0)

    def keepalive(self, live_games: int) -> None:
        self._post("/keepalive", {"live_games": int(live_games)})


def messages_for(brief_text: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief_text}]


class Reasoner:
    def __init__(self, cfg=None, head: Head | None = None, client: Client | None = None, clock=time.time):
        self.cfg = cfg
        self.head = head or Head()
        self.client = client
        self.clock = clock
        self._cache: dict[str, tuple[int, Reading]] = {}
        self._narr: dict[str, Narration] = {}
        self._last_ask: dict[str, float] = {}
        self._last_key: dict[str, tuple] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None

    def _get(self, k, d):
        return self.cfg.get(k, d) if self.cfg is not None else d

    def on(self) -> bool:
        return bool(self._get("reasoner.enabled", False))

    def read(self, g: GameState | None, row: dict | None, reading=None, ctx: brief.Context | None = None) -> Reading:
        """This game's reading now. Cached per new play (the brain's event count)."""
        if g is None or g.phase != Phase.LIVE or not self.on():
            return EMPTY
        lg = g.league.value
        if self._get(f"reasoner.{lg}.enabled", True) is False:
            return EMPTY
        ctx = ctx or brief.Context()
        events = int(getattr(reading, "events", 0) or 0)
        key = (events, ctx.garbage, ctx.crunch, ctx.mine)
        hit = self._cache.get(g.game_id)
        if hit is not None and hit[0] == key:
            r = hit[1]
            r.narration = self._narr.get(g.game_id, r.narration)
            return r
        try:
            f = brief.facts(g, row, reading, ctx)
        except Exception:  # noqa: BLE001 - a strange feed costs the reading, never the poll
            log.exception("reasoner: brief failed for %s", g.game_id)
            return EMPTY
        p = self.head.odds(lg, f)
        head_level = self.head.level(lg, p, garbage=ctx.garbage, crunch=ctx.crunch)
        n = self._narr.get(g.game_id)
        level = head_level
        if self._get("reasoner.decide_with", "head") == "model" and n is not None \
                and self.clock() - n.at <= 3 * float(self._get("reasoner.min_interval_s", 20)):
            # the model's call, inside the same hard facts as the head's
            level = "none" if ctx.garbage else ("must_see" if ctx.crunch else n.lock_in)
        r = Reading(p=p, lock_in=level, head_lock_in=head_level, events=events, narration=n, facts=f)
        self._cache[g.game_id] = (key, r)
        while len(self._cache) > MAX_GAMES:
            self.forget(next(iter(self._cache)))
        return r

    def cached(self, gid: str) -> Reading | None:
        hit = self._cache.get(gid)
        if hit is None:
            return None
        r = hit[1]
        r.narration = self._narr.get(gid, r.narration)
        return r

    # --- the language model (phone text), event-driven and in the background ----------------------

    def due(self, g: GameState, r: Reading, now: float) -> bool:
        """Ask the model again? Only for a game worth narrating, on a real change, rate-limited."""
        if self.client is None or not self._get("reasoner.model", True) or r is EMPTY:
            return False
        if r.lock_in == "none" and not r.facts.get("mine"):
            return False                        # quiet games don't cost model time
        lp = g.last_play
        key = (g.home.score, g.away.score, g.stoppage in REAL_BREAKS, bool(g.situation.power_play),
               g.period, lp.id if lp else None)
        prev = self._last_key.get(g.game_id)
        if prev is not None and prev == key:
            return False
        scored = prev is not None and prev[:2] != key[:2]
        gap = float(self._get("reasoner.min_interval_s", 20))
        if now - self._last_ask.get(g.game_id, -1e9) < gap and not scored:
            return False
        if g.game_id in self._inflight:
            return False
        self._last_key[g.game_id] = key
        return True

    def narrate(self, g: GameState, r: Reading, ctx: brief.Context | None = None) -> bool:
        """Queue one model call for this game when due. True if queued. Never blocks."""
        now = self.clock()
        if not self.due(g, r, now):
            return False
        text = brief.text(g, r.facts, ctx, head_p=r.p)
        gid = g.game_id
        events = r.events
        with self._lock:
            self._inflight.add(gid)
            self._last_ask[gid] = now
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reasoner")
        self._pool.submit(self._ask, gid, text, events)
        return True

    def _ask(self, gid: str, text: str, events: int) -> None:
        try:
            out = self.client.generate(messages_for(text), int(self._get("reasoner.max_tokens", 160)))
            n = parse_answer(out[0]) if out else None
            if n is not None:
                n.at, n.ms, n.events = self.clock(), out[1], events
                self._narr[gid] = n
        except Exception:  # noqa: BLE001
            log.exception("reasoner: narration failed for %s", gid)
        finally:
            with self._lock:
                self._inflight.discard(gid)

    def keepalive(self, live_games: int) -> None:
        if self.client is not None and live_games > 0 and self._get("reasoner.model", True):
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reasoner")
            self._pool.submit(self.client.keepalive, live_games)

    def forget(self, gid: str) -> None:
        for d in (self._cache, self._narr, self._last_ask, self._last_key):
            d.pop(gid, None)
