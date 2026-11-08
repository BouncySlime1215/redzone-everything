"""Per-stick fallback ladder (Nick's Nov 7 sync order, item 2). Flag `fallback_ladder.enabled`, default OFF.

When a join can't be made the way the plan asked, the controller climbs DOWN a fixed ladder instead of improvising:

    buffered     the stick plays the target B s behind live, MEASURED (YTTV overlay label / Prime timecode)
    live         the same stick re-tuned at the live edge (a known position), flipped at a dead ball as today
    other_stick  the other stick tunes the target in place, at a dead ball (the decider's one-stick path)
    hold         keep the game on screen; nothing is pressed on the visible stick

Every rung is tried at most once per climb, each attempt is one row in data/ladder.jsonl and one line on the phone
("Stick 2: buffered failed (no label read) -> live ok"). The ladder only CHOOSES and RECORDS: the rung callables
(the controller's) do the work, and every flip still goes through Controller._wait_safe, so Rule #1 is unchanged.
A rung that failed `skip_after` times on one stick inside `window_s` is skipped on that stick for `cooldown_s`
(no key storms on a path that is broken tonight, e.g. the label reader after a YouTube TV UI change).

"Never show buffered when it isn't": `Climb.shows_buffered` is True only for the buffered rung with a measured
behind-live. Anything else is "live" or "kept" on the phone, whatever the plan wanted.

Interface for the join watchdog (item 1): after an entry the watchdog verifies the position; if it is off target by
more than its margin and its one re-seek didn't fix it, it calls `Controller.ladder_descend(target, "buffered",
why)` (or `FallbackLadder.climb(..., start=next_rung("buffered"))` with its own rung callables). Owner: lane 3.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Mapping

log = logging.getLogger(__name__)

RUNGS = ("buffered", "live", "other_stick", "hold")
WORDS = {"buffered": "buffered", "live": "live edge", "other_stick": "other stick", "hold": "kept the current game"}


def next_rung(rung: str) -> str:
    """The rung below `rung` (hold is the floor)."""
    i = RUNGS.index(rung)
    return RUNGS[min(i + 1, len(RUNGS) - 1)]


@dataclass
class Step:
    """What one rung attempt returned. behind_s/measured only mean something on the buffered rung."""
    ok: bool
    why: str = ""
    behind_s: float | None = None
    measured: bool = False
    host: str | None = None   # the stick that carried it, when not the climbing stick (other_stick)


@dataclass
class Climb:
    target: str
    host: str
    stick: str               # display label ("Stick 2" / HDMI2), never an IP
    reason: str
    started: float
    attempts: list[dict] = field(default_factory=list)
    rung: str = "hold"
    ok: bool = False
    behind_s: float | None = None
    measured: bool = False

    @property
    def shows_buffered(self) -> bool:
        return self.ok and self.rung == "buffered" and self.measured and bool(self.behind_s)

    @property
    def fell_back(self) -> bool:
        return any(not a["ok"] for a in self.attempts) or self.rung != (self.attempts[0]["rung"] if self.attempts
                                                                       else self.rung)

    def text(self) -> str:
        """One phone line: 'Stick 2: buffered failed (no label read) -> live edge ok'."""
        parts = []
        for a in self.attempts:
            w = WORDS[a["rung"]]
            if a.get("skipped"):
                parts.append(f"{w} skipped ({a['why']})")
            elif a["ok"]:
                extra = f" {a['behind_s']:.0f}s behind" if a["rung"] == "buffered" and a.get("behind_s") else ""
                parts.append(f"{w} ok{extra}" if a["rung"] != "hold" else w)
            else:
                parts.append(f"{w} failed ({a['why']})" if a["why"] else f"{w} failed")
        return f"{self.stick}: " + " -> ".join(parts)

    def view(self) -> dict:
        return {"ts": round(self.started, 1), "target": self.target, "stick": self.stick, "reason": self.reason,
                "rung": self.rung, "ok": self.ok, "buffered": self.shows_buffered,
                "behind_s": self.behind_s if self.shows_buffered else None, "fell_back": self.fell_back,
                "text": self.text(), "steps": [dict(a) for a in self.attempts]}


StepFn = Callable[[], Awaitable[Step]]


class FallbackLadder:
    def __init__(self, cfg, log_path: Path | None = None, clock: Callable[[], float] = time.time,
                 label: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.log_path = log_path
        self.clock = clock
        self.label = label or (lambda host: "stick")
        self.steps_path: Path | None = None   # [night report, item 7] data/fallbacks.jsonl: one row per step
        self.recent: deque[Climb] = deque(maxlen=20)
        self._climbing: set[str] = set()   # hosts mid-climb: their rung results are logged by the climb itself
        self._fails: dict[tuple[str, str], deque[float]] = {}
        self._cool: dict[tuple[str, str], float] = {}

    # --- config -----------------------------------------------------------------------------------------------
    def _get(self, key: str, default):
        try:
            v = self.cfg.get(f"fallback_ladder.{key}", default)
        except Exception:
            return default
        return default if v is None else v

    @property
    def enabled(self) -> bool:
        return bool(self._get("enabled", False))

    # --- per-stick rung health --------------------------------------------------------------------------------
    def note(self, host: str, rung: str, ok: bool) -> None:
        if rung == "hold":
            return
        k = (host, rung)
        now = self.clock()
        if ok:
            self._fails.pop(k, None)
            return
        q = self._fails.setdefault(k, deque(maxlen=16))
        q.append(now)
        win = float(self._get("window_s", 600.0))
        while q and now - q[0] > win:
            q.popleft()
        if len(q) >= int(self._get("skip_after", 2)):
            self._cool[k] = now + float(self._get("cooldown_s", 300.0))
            q.clear()
            log.warning("ladder: %s on %s failed %d times in %.0fs: skipped for %.0fs", rung, self.label(host),
                        int(self._get("skip_after", 2)), win, float(self._get("cooldown_s", 300.0)))

    def cooling(self, host: str, rung: str) -> float:
        """Seconds left before `rung` is tried again on `host` (0 = usable)."""
        return max(0.0, self._cool.get((host, rung), 0.0) - self.clock())

    # --- climbing ---------------------------------------------------------------------------------------------
    async def climb(self, host: str, target: str, steps: Mapping[str, StepFn | None], start: str = "buffered",
                    reason: str = "") -> Climb:
        """Try `start` and every rung below it, in order, until one works. A rung with no callable (None or
        missing) is skipped as not applicable. `hold` always ends the climb: it needs no callable (keeping the
        current game is doing nothing). Never raises: a rung that crashes counts as failed."""
        self._climbing.add(host)
        try:
            return await self._climb(host, target, steps, start, reason)
        finally:
            self._climbing.discard(host)

    async def _climb(self, host: str, target: str, steps: Mapping[str, StepFn | None], start: str,
                     reason: str) -> Climb:
        c = Climb(target, host, self.label(host), reason, self.clock())
        budget = float(self._get("budget_s", 90.0))
        t0 = time.monotonic()
        for rung in RUNGS[RUNGS.index(start):]:
            if rung == "hold":
                fn = steps.get("hold")
                st = Step(True, "hold")
                if fn is not None:
                    try:
                        st = await fn()
                    except Exception as e:   # hold must never fail: it means "press nothing"
                        log.exception("ladder: hold(%s)", target)
                        st = Step(True, f"hold step crashed: {e}"[:80])
                self._add(c, rung, True, st.why if st.why != "hold" else "", None, False)
                c.rung, c.ok = "hold", False
                break
            fn = steps.get(rung)
            if fn is None or rung in (self._get("skip_rungs", ()) or ()):
                continue   # not applicable here, or turned off (e.g. skip_rungs [live] for a league whose replay says so)
            left = self.cooling(host, rung)
            if left:
                self._add(c, rung, False, f"failed twice lately, retry in {left:.0f}s", None, False, skipped=True)
                continue
            if time.monotonic() - t0 > budget:
                self._add(c, rung, False, f"over the {budget:.0f}s budget", None, False, skipped=True)
                continue
            a0 = time.monotonic()
            try:
                st = await fn()
            except Exception as e:
                log.exception("ladder: %s(%s) on %s", rung, target, self.label(host))
                st = Step(False, f"crashed: {e}"[:80])
            measured = bool(st.measured and st.behind_s)
            ok = bool(st.ok) and (rung != "buffered" or measured)
            why = st.why if ok or st.why else ""
            if st.ok and rung == "buffered" and not measured:
                why = "position not measured"   # never call a stream buffered on an estimate
            self._add(c, rung, ok, why, st.behind_s if ok else None, measured and ok,
                      took=round(time.monotonic() - a0, 1), host=st.host)
            self.note(st.host or host, rung, ok)
            if ok:
                c.rung, c.ok = rung, True
                c.behind_s, c.measured = (st.behind_s, True) if rung == "buffered" else (None, False)
                break
        self.recent.append(c)
        self._write(c)
        if c.fell_back:
            log.info("ladder: %s (%s)", c.text(), reason or "join")
        return c

    async def descend(self, host: str, target: str, steps: Mapping[str, StepFn | None], from_rung: str,
                      why: str) -> Climb:
        """The watchdog found `from_rung` didn't hold (off target after entry): climb from the rung below it."""
        return await self.climb(host, target, steps, start=next_rung(from_rung), reason=f"{from_rung} lost: {why}")

    def record(self, host: str, target: str, rung: str, ok: bool, why: str = "", behind_s: float | None = None,
               measured: bool = False, reason: str = "") -> Climb | None:
        """Log a one-step outcome decided elsewhere (e.g. the planned rung worked first time), so the night report
        counts every join on the same ladder. None (nothing logged) with the flag off, or while a climb on that stick
        is running: the climb logs that rung itself (the join watchdog, #395, records from inside the scrub, which is
        the buffered rung), so one scrub is never two rows or two failures toward a cooldown."""
        if not self.enabled or host in self._climbing:
            return None
        c = Climb(target, host, self.label(host), reason, self.clock())
        self._add(c, rung, ok, why, behind_s, measured)
        c.rung, c.ok = rung, ok and rung != "hold"
        if rung == "buffered" and ok and measured:
            c.behind_s, c.measured = behind_s, True
        self.note(host, rung, ok)
        self.recent.append(c)
        self._write(c)
        return c

    def _add(self, c: Climb, rung: str, ok: bool, why: str, behind_s, measured: bool, skipped: bool = False,
             took: float | None = None, host: str | None = None) -> None:
        row = {"rung": rung, "ok": ok, "why": (why or "")[:100]}
        if behind_s is not None:
            row["behind_s"] = round(float(behind_s), 1)
        if measured:
            row["measured"] = True
        if skipped:
            row["skipped"] = True
        if took is not None:
            row["took_s"] = took
        if host and host != c.host:
            row["stick"] = self.label(host)
        c.attempts.append(row)

    def _write(self, c: Climb) -> None:
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a") as f:
                    f.write(json.dumps({"kind": "ladder", **c.view()}) + "\n")
            except OSError:
                log.exception("ladder log")
        if self.steps_path is not None:
            # the night report's reader (redzone/platform/night_ops.py): {"ts","stick","target","step","why"}
            try:
                self.steps_path.parent.mkdir(parents=True, exist_ok=True)
                with self.steps_path.open("a") as f:
                    for a in c.attempts:
                        f.write(json.dumps({"ts": round(c.started, 1), "stick": a.get("stick", c.stick),
                                            "target": c.target, "step": a["rung"], "why": a["why"],
                                            "ok": a["ok"], **({"skipped": True} if a.get("skipped") else {}),
                                            "reason": c.reason}) + "\n")
            except OSError:
                log.exception("fallbacks log")

    # --- phone / night report ---------------------------------------------------------------------------------
    def phone_view(self, now: float | None = None, within_s: float = 900.0) -> dict:
        """Last climb that fell back (shown on the phone for 15 min) + tonight's counts."""
        now = self.clock() if now is None else now
        last = next((c for c in reversed(self.recent) if c.fell_back and now - c.started <= within_s), None)
        return {"enabled": self.enabled, "last": last.view() if last else None,
                "cooling": [{"stick": self.label(h), "rung": r, "s": round(t - now)}
                            for (h, r), t in self._cool.items() if t > now]}


def summarize(rows: list[dict]) -> dict:
    """Night report numbers from data/ladder.jsonl rows: joins per final rung, fallbacks, measured buffered share."""
    out = {"joins": 0, "by_rung": {r: 0 for r in RUNGS}, "fell_back": 0, "buffered_measured": 0, "skipped": 0,
           "failed_steps": {r: 0 for r in RUNGS}}
    for r in rows:
        if r.get("kind") != "ladder":
            continue
        out["joins"] += 1
        out["by_rung"][r.get("rung", "hold")] = out["by_rung"].get(r.get("rung", "hold"), 0) + 1
        out["fell_back"] += bool(r.get("fell_back"))
        out["buffered_measured"] += bool(r.get("buffered"))
        for s in r.get("steps") or ():
            if s.get("skipped"):
                out["skipped"] += 1
            elif not s.get("ok") and s.get("rung") in out["failed_steps"]:
                out["failed_steps"][s["rung"]] += 1
    return out
