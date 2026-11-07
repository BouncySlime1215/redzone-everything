"""ESPN+ events through YouTube TV (Nick, Nov 7: ESPN app links opened 0 of 15; every ESPN+ event is
also in YouTube TV through ESPN Unlimited). Flag: tuners.espnplus_yttv (default off).

Per event, fail closed: search YouTube TV for the matchup, walk the focus ring to a tile that names
BOTH teams and carries a LIVE marker, select it, and accept only when the app's media session then
names both teams. Anything else (no tile, one team only, no LIVE marker, purchase/account screen,
focus that won't move, a session naming something else) stops with a reason and nothing is tuned,
so the game keeps its current route.

YouTube TV's "autoplay" prompt is answered with "Ask later" and nothing else: that button is
selected only when OCR focus is on it, and no account setting is ever changed.
Screenshots are read for text and boxes only and deleted right after (the caller's observe()).
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
from redzone.tuners.prime_search import CapturedScreen, _words, unique_focus
from redzone.tuners.profiles import is_profile_screen
from redzone.tuners.yttv_search import (LOADED_STATES, _center, _purchase, _same_focus, focus_text,
                                        loose_codes_in, step_toward)

EVENT_BUDGET_S = 120.0
WATCH_LIVE = ("watch live", "live", "watch now", "watch")   # exact labels only; never "start from beginning"
AUTOPLAY = ("autoplay", "auto-play", "auto play", "ask later")
ASK_LATER = "ask later"
MIN_ALIAS = 4


# --------------------------------------------------------------------------- who is playing

def team_aliases(name: str) -> tuple[str, ...]:
    """Readable names for one team, longest first: 'Maple Leafs' -> ('maple leafs', 'leafs');
    'North Carolina' -> ('north carolina', 'carolina'). Abbreviations are never used (too short)."""
    w = _words(name or "")
    out = [w] if len(w) >= MIN_ALIAS else []
    parts = w.split()
    if len(parts) > 1 and len(parts[-1]) >= MIN_ALIAS + 1:
        out.append(parts[-1])
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class EventSpec:
    game_id: str
    home: tuple[str, ...]
    away: tuple[str, ...]
    queries: tuple[str, ...]

    def ok(self) -> bool:
        return bool(self.home and self.away and not set(self.home) & set(self.away))

    def ref(self) -> str:
        """Tuner ref: everything the tuner needs, no lookup later (game id never goes to the app)."""
        return "event:" + "|".join((self.game_id, ";".join(self.home), ";".join(self.away), ";".join(self.queries)))

    @classmethod
    def from_ref(cls, ref: str) -> "EventSpec | None":
        if not ref.startswith("event:"):
            return None
        parts = ref[6:].split("|")
        if len(parts) != 4:
            return None
        gid, h, a, q = parts
        spec = cls(gid, tuple(x for x in h.split(";") if x), tuple(x for x in a.split(";") if x),
                   tuple(x for x in q.split(";") if x))
        return spec if spec.ok() else None


def event_spec(game_id: str, home_name: str, away_name: str) -> EventSpec | None:
    """None when the two teams can't be told apart by readable name (fail closed)."""
    home, away = team_aliases(home_name), team_aliases(away_name)
    shared = set(home) & set(away)
    home = tuple(a for a in home if a not in shared)
    away = tuple(a for a in away if a not in shared)
    if not home or not away:
        return None
    # Matchup first ("hurricanes capitals"), then each full name: a team page lists its live game.
    queries = tuple(q for q in dict.fromkeys((f"{away[-1]} {home[-1]}", home[0], away[0]))
                    if re.fullmatch(r"[a-z0-9 .-]{3,60}", q))
    spec = EventSpec(str(game_id), home, away, queries)
    return spec if spec.ok() and queries else None


def _has(text: str, alias: str) -> bool:
    return f" {alias} " in f" {_words(text)} "


def names_event(text: str, spec: EventSpec) -> bool:
    """Both teams are named (any alias of each). One team alone never counts."""
    return any(_has(text, a) for a in spec.home) and any(_has(text, a) for a in spec.away)


HEADING_Y = .30   # the search heading / typed query lives above this line; never a tile


def _cluster(ocr, box) -> list:
    cx, cy = _center(box)
    return [b for b in getattr(ocr, "observations", ())
            if b.y >= HEADING_Y and abs(_center(b)[0] - cx) < .12 and abs(_center(b)[1] - cy) < .15]


def _live(texts) -> bool:
    return any(re.search(r"\blive\b", normalize_text(t)) and "start from" not in normalize_text(t) for t in texts)


def event_tiles(ocr, spec: EventSpec, query: str = "") -> list[tuple[float, object, bool]]:
    """[(score, box, live)] for boxes whose tile names both teams, best first. The search heading
    band (the typed query, whatever it says) and anything offering a purchase are skipped."""
    out = []
    for box in getattr(ocr, "observations", ()):
        text = box.text or ""
        if box.y < HEADING_Y:
            continue
        near = [b.text for b in _cluster(ocr, box)]
        joined = " ".join(near)
        if _purchase(joined) or not names_event(joined, spec):
            continue
        live = _live(near)
        out.append(((10 if names_event(text, spec) else 0) + (5 if live else 0) + box.confidence, box, live))
    return sorted(out, key=lambda t: -t[0])


# --------------------------------------------------------------------------- the hunt

@dataclass
class EventHunt:
    game_id: str
    status: str = "stopped"          # confirmed | stopped
    reason: str = ""
    query: str = ""
    tile: str = ""
    description: str = ""            # media-session text that confirmed the matchup
    autoplay_prompt: str = ""        # "" | answered_ask_later | not_answered
    candidates: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0

    def row(self) -> dict:
        return {"found": self.status == "confirmed", "reason": self.reason, "query": self.query,
                "tile": self.tile[:120], "media": self.description[:160], "autoplay_prompt": self.autoplay_prompt,
                "code_candidates": [c["source"] for c in self.candidates], "elapsed_s": self.elapsed_s,
                "keys": [s["detail"] for s in self.steps if s["action"] == "key"]}


def is_autoplay_prompt(text: str) -> bool:
    t = normalize_text(text)
    return ASK_LATER in t and any(w in t for w in AUTOPLAY[:3])


async def hunt_event(stick, observe, spec: EventSpec, *, profile: str = "", sleep=asyncio.sleep,
                     clock=time.monotonic, max_moves: int = 14, budget_s: float = EVENT_BUDGET_S,
                     collect_codes: bool = False, lounge=None, may_run=None) -> EventHunt:
    """Find and tune one live event by in-app search. Never raises; status 'confirmed' only when the
    media session names both teams after the selection."""
    from redzone.discovery import stickio

    hunt = EventHunt(spec.game_id)
    started, last_sequence = clock(), 0

    def record(action, detail=""):
        hunt.steps.append({"at_s": round(clock() - started, 2), "action": action, "detail": str(detail)[:300]})

    def budget():
        if may_run is not None and not may_run():
            raise RuntimeError("no longer allowed to drive this stick; no further keys sent")
        if clock() - started > budget_s:
            raise RuntimeError("time budget exhausted; no further keys sent")

    async def read():
        nonlocal last_sequence
        budget()
        frame = await observe()
        if not isinstance(frame, CapturedScreen) or frame.sequence <= last_sequence:
            raise RuntimeError("screen capture is stale or out of order")
        last_sequence = frame.sequence
        if not frame.read_ok:
            raise RuntimeError("screen text could not be read (screencap/OCR)")
        verdict = classify_screen(frame.text)
        if is_purchase_screen(frame.text) or (verdict and verdict.stops):
            raise RuntimeError("account, payment or unavailable screen")
        record("read", frame.text[:300])
        return frame

    async def press(code):
        budget()
        sent = await stick.key(code)
        record("key", code)
        if not getattr(sent, "ok", False):
            raise RuntimeError(f"{code} delivery uncertain; no retry")
        await sleep(.6)

    async def ask_later(frame) -> bool:
        """Autoplay prompt: move focus onto 'Ask later' (at most 3 steps, re-reading after each) and
        select it. Any doubt: stop, nothing selected."""
        for _ in range(4):
            if not is_autoplay_prompt(frame.text):
                return True
            target = [b for b in frame.observations if normalize_text(b.text).strip() == ASK_LATER]
            focus = unique_focus(frame)
            if len(target) != 1 or focus is None:
                hunt.autoplay_prompt = "not_answered"
                raise RuntimeError("autoplay prompt: 'Ask later' or the focus isn't readable; nothing selected")
            tx, ty = _center(target[0])
            if focus.x - .01 <= tx <= focus.x + focus.width + .01 and focus.y - .01 <= ty <= focus.y + focus.height + .01:
                again = await read()
                if not _same_focus(unique_focus(again), focus):
                    raise RuntimeError("autoplay prompt: focus changed before selection")
                await press("KEYCODE_DPAD_CENTER")
                hunt.autoplay_prompt = "answered_ask_later"
                await sleep(1.0)
                return True
            await press(step_toward(focus, target[0]))
            frame = await read()
        hunt.autoplay_prompt = "not_answered"
        raise RuntimeError("autoplay prompt: focus did not reach 'Ask later'")

    async def open_search(query):
        launched = await stick.view("https://tv.youtube.com/search?q=" + quote(query, safe=""), pkg, wait=True)
        record("open_search", f"{query!r} ok={launched.ok}")
        if not launched.ok:
            raise RuntimeError("search link was refused; no retry")
        await sleep(2.0)
        frame = await read()
        if is_profile_screen(frame.text):
            if not profile:
                raise RuntimeError("profile picker shown and no YouTube TV profile configured")
            from redzone.tuners.profiles import select_profile
            notes: list[str] = []
            await select_profile(stick, frame.text, profile, notes, before_key=budget)
            record("profile", "; ".join(notes))
            await sleep(2.0)
            await stick.view("https://tv.youtube.com/search?q=" + quote(query, safe=""), pkg, wait=True)
            await sleep(2.0)
            frame = await read()
        return frame

    async def walk_to_tile(frame, query) -> bool:
        moved_down, last_focus, still = False, None, 0
        for _ in range(max_moves):
            focus = unique_focus(frame)
            here = focus_text(frame, focus)
            if focus is not None and names_event(here, spec) and not _purchase(here):
                near = [b.text for b in frame.observations if focus.x - .01 <= _center(b)[0] <= focus.x + focus.width + .01
                        and focus.y - .01 <= _center(b)[1] <= focus.y + focus.height + .14]
                if not _live(near + [here]):
                    raise RuntimeError("the focused matchup tile has no LIVE marker (replay or upcoming?)")
                again = await read()
                if not _same_focus(unique_focus(again), focus) or not names_event(focus_text(again, focus), spec):
                    raise RuntimeError("focused tile changed before selection")
                hunt.tile = focus_text(again, focus)
                await press("KEYCODE_DPAD_CENTER")
                return True
            tiles = event_tiles(frame, spec, query)
            if not tiles:
                return False
            if not tiles[0][2]:
                raise RuntimeError("matchup tile found but it has no LIVE marker (replay or upcoming?)")
            if focus is None:
                if moved_down:
                    raise RuntimeError("focus ring isn't readable on the results page")
                await press("KEYCODE_DPAD_DOWN")
                moved_down = True
            else:
                still = still + 1 if last_focus == focus else 0
                if still >= 2:
                    raise RuntimeError("focus stopped moving toward the matchup tile")
                last_focus = focus
                await press(step_toward(focus, tiles[0][1]))
            frame = await read()
        raise RuntimeError("matchup tile not reached within the move limit")

    async def wait_confirmed(before, seconds=18.0):
        pressed_watch = False
        t0 = clock()
        while clock() - t0 < seconds:
            ev = await stickio.read_evidence(stick, pkg, with_screen=False)
            record("media", ev.summary())
            if (ev.read_ok and ev.state in LOADED_STATES and ev.meta_text
                    and ev.meta_text != getattr(before, "meta_text", None) and names_event(ev.meta_text, spec)):
                return ev
            if ev.read_ok and ev.state in LOADED_STATES and ev.meta_text != getattr(before, "meta_text", None) \
                    and ev.meta_text and not names_event(ev.meta_text, spec) and clock() - t0 >= 6:
                raise RuntimeError(f"selected tile plays something else: {ev.meta_text[:80]!r}")
            if not ev.playing and clock() - t0 >= 3:
                frame = await read()
                if is_autoplay_prompt(frame.text):
                    await ask_later(frame)
                    continue
                focus = unique_focus(frame)
                label = normalize_text(focus.text if focus else "").strip()
                if (not pressed_watch and focus is not None and focus.source == "neutral_filled_button"
                        and label in WATCH_LIVE):
                    await press("KEYCODE_DPAD_CENTER")
                    pressed_watch = True
                    record("watch_button", label)
            await sleep(1.0)
        return None

    try:
        if not spec.ok():
            raise RuntimeError("teams can't be told apart by name")
        pkg = (getattr(stick, "packages", None) or {}).get("youtubetv") or stickio.yttv_package(stick)
        power = await stick.shell("dumpsys power | grep -m1 'mWakefulness='", timeout=4)
        if not power.ok or not re.search(r"mWakefulness=Awake\b", power.out):
            raise RuntimeError("stick is not Awake (WAKEUP can flip the TV over CEC, so it isn't sent)")
        before = await stickio.read_evidence(stick, pkg, with_screen=False)
        selected = False
        for query in spec.queries:
            hunt.query = query
            frame = await open_search(query)
            if await walk_to_tile(frame, query):
                selected = True
                break
            record("no_tile", query)
        if not selected:
            raise RuntimeError("no tile naming both teams in YouTube TV search (event not there: fail closed)")
        ev = await wait_confirmed(before)
        if ev is None:
            raise RuntimeError("selected the tile, but the media session never named both teams")
        hunt.description = ev.meta_text
        hunt.status, hunt.reason = "confirmed", "media session names both teams"
        try:   # a late autoplay prompt over the picture: answer it the same way
            frame = await read()
            if is_autoplay_prompt(frame.text):
                await ask_later(frame)
        except RuntimeError as exc:
            record("post_read", str(exc))
        if collect_codes:
            hunt.candidates = await _code_candidates(stick, ev, lounge, record)
        return hunt
    except Exception as exc:   # never takes the caller down
        hunt.status, hunt.reason = "stopped", str(exc)
        return hunt
    finally:
        hunt.elapsed_s = round(clock() - started, 2)


async def _code_candidates(stick, ev, lounge, record) -> list[dict]:
    """Watch codes the stick leaks for what it now plays (UNPROVEN until prove_event_code)."""
    from redzone.discovery import stickio
    from redzone.tuners.yttv_search import EXTRA_CODE_PROBES
    found: list[dict] = []

    def add(code, source):
        if code and stickio.CODE_RE.match(code) and all(c["code"] != code for c in found):
            found.append({"code": code, "source": source})

    if lounge is not None:
        try:
            add(await lounge(stick.host), "YouTube Lounge now-playing id")
        except Exception as exc:
            record("lounge", f"failed: {exc}")
    for code in ev.ids:
        add(code, "media session id")
    for label, cmd in stickio.CAPTURE_PROBES + EXTRA_CODE_PROBES:
        r = await stick.shell(cmd, timeout=10)
        codes = loose_codes_in(r.out if r.ok else "")
        if "logcat" in label:
            codes.reverse()
        for code in codes:
            add(code, label)
    return found


async def prove_event_code(stick, spec: EventSpec, code: str, decoy: str, *, sleep=asyncio.sleep,
                           clock=time.monotonic, seconds: float = 20.0) -> tuple[bool, str]:
    """Tune a known channel (decoy), then deep-link `code`: proven only when the media session moves
    from the decoy to a session naming both teams. Returns (proven, detail)."""
    from redzone.discovery import stickio
    if not stickio.CODE_RE.match(code or "") or not stickio.CODE_RE.match(decoy or "") or code == decoy:
        return False, "bad code or decoy"
    pkg = (getattr(stick, "packages", None) or {}).get("youtubetv") or stickio.yttv_package(stick)

    async def tune(c, before=None):
        r = await stick.view(f"https://tv.youtube.com/watch/{c}", pkg)
        if not r.ok:
            return None, "deep link refused"
        t0 = clock()
        while clock() - t0 < seconds:
            await sleep(1.0)
            ev = await stickio.read_evidence(stick, pkg, with_screen=False)
            if not (ev.meta_text and ev.state in LOADED_STATES):
                continue
            if before is None:
                if not names_event(ev.meta_text, spec):
                    return ev, ""
                continue
            if ev.meta_text != before.meta_text and names_event(ev.meta_text, spec):
                return ev, f"decoy {before.meta_text[:40]!r} -> {ev.meta_text[:60]!r}"
        return None, "the code did not bring the event back"

    first, why = await tune(decoy)
    if first is None:
        return False, f"decoy did not show up ({why or 'timeout'}), so a re-tune proves nothing"
    ev, why = await tune(code, before=first)
    return ev is not None, why
