"""Find a YouTube TV channel nobody published a code for (locals, NFL RedZone, NBCSN, Sunday
Ticket 8/16) by searching for it inside the app on the stick.

Why "blind": the YouTube TV app paints its own screen, so we can't read the search results. A
recipe is a short list of steps (open a tv.youtube.com link, press keys, type, wait). After a
recipe the media session says whether video plays, and its title/subtitle say which channel it
is (judge()). Recipes live in yttv_channels.json -> "search" -> "recipes" so they can be fixed on
the device without code changes. ALL RECIPES ARE UNVERIFIED until run on a real Fire TV stick.

When the right channel plays we try to learn its watch code: the app's own now-playing id
(lounge.py) first, then anything the stick leaks (dumpsys / logcat / watch-next). A captured code
is only trusted after a clean re-check: switch to a different channel, deep-link the code, and see
our channel come back. If no code can be learned, the working recipe is saved instead and
tune_by_search() can replay it (slower, ~20 s, but zero pasting).

Guardrails: recipes may only open https://tv.youtube.com/ links and press KEYCODE_* keys; typed
text is reduced to letters, digits and spaces; waits are capped; runs are time-boxed, resumable
(state saved after every channel) and ask may_run() before each channel.

Why: first added in 634e0a01 (2026-11-02) as "Recover RedZone engine with explicit screen evidence and safe switch transactions"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Callable
from urllib.parse import quote

from redzone.discovery import stickio
from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
from redzone.discovery.yttv import (Entry, LoungeFn, Outcome, Table, VerifyReport, VerifyState, safe_lounge,
                                    check_code, judge, load_table)

log = logging.getLogger(__name__)

KEY_RE = re.compile(r"^KEYCODE_[A-Z0-9_]+$")
ALLOWED_LINK = "https://tv.youtube.com/"
MAX_STEPS = 30
MAX_WAIT_S = 20.0
RECIPE_FRESH_S = 7 * 86400     # a channel found by search isn't searched again for a week


class RecipeBlocked(RuntimeError):
    """The current screen cannot safely accept a recipe's selection key."""


def fill(template: str, entry: Entry) -> str | None:
    """Put the channel's words into a recipe string. None if the recipe needs a field we lack."""
    if "{browse_id}" in template and not entry.browse_id:
        return None
    try:
        return template.format(q=entry.search, q_url=quote(entry.search), browse_id=entry.browse_id or "")
    except (KeyError, IndexError, ValueError):
        return None


def usable(recipe: dict, entry: Entry) -> bool:
    need = recipe.get("needs")
    return isinstance(recipe.get("steps"), list) and (not need or bool(getattr(entry, str(need), None)))


async def run_step(stick, step: dict, entry: Entry, pkg: str, sleep, log_: list[str], nested: bool = False) -> None:
    if "view" in step:
        url = fill(str(step["view"]), entry)
        if url and url.startswith(ALLOWED_LINK):          # guardrail: only YouTube TV pages
            ok = await stickio.open_link(stick, url, pkg)
            log_.append(f"open {url}: {'ok' if ok else 'refused'}")
        else:
            log_.append(f"skipped link {step['view']!r}")
    elif "wait" in step:
        await sleep(min(float(step["wait"]), MAX_WAIT_S))
    elif "keys" in step:
        keys = [k for k in step["keys"] if isinstance(k, str) and KEY_RE.match(k)]
        # Search recipes are unverified on this device. Read immediately before each
        # selection so an account wall cannot turn a blind OK into a purchase.
        for key in keys:
            if key in ("KEYCODE_DPAD_CENTER", "KEYCODE_ENTER", "KEYCODE_NUMPAD_ENTER"):
                text = normalize_text(await stickio.screen_text(stick))
                verdict = classify_screen(text)
                if not text or is_purchase_screen(text) or (verdict and verdict.stops):
                    raise RecipeBlocked("search stopped: the selection screen is unreadable or needs account action")
            await stickio.press(stick, key)
        log_.append("keys " + " ".join(k.replace("KEYCODE_", "") for k in keys))
    elif "text" in step:
        text = fill(str(step["text"]), entry)
        if text and await stickio.type_text(stick, text):
            log_.append(f"typed {text!r}")
    elif "if_not_playing" in step and not nested:
        s = stickio.pick_session(await stickio.sessions(stick), pkg)
        if not (s and s.state == "playing"):
            for sub in list(step["if_not_playing"])[:MAX_STEPS]:
                if isinstance(sub, dict):
                    await run_step(stick, sub, entry, pkg, sleep, log_, nested=True)


async def run_recipe(stick, recipe: dict, entry: Entry, pkg: str, sleep=asyncio.sleep) -> list[str]:
    log_ = [f"recipe {recipe.get('name')}"]
    await stickio.press(stick, "KEYCODE_WAKEUP")
    for step in list(recipe.get("steps") or [])[:MAX_STEPS]:
        if isinstance(step, dict):
            await run_step(stick, step, entry, pkg, sleep, log_)
    return log_


def _codes_of_others(entry: Entry, table: Table, state: VerifyState) -> set[str]:
    """Codes known to belong to other channels: a leak of one of these is not our code."""
    out = {c.code for k, e in table.channels.items() if k != entry.key for c in e.candidates}
    out |= {code for code, r in state.codes.items() if r.get("channel") not in (None, entry.key)}
    return out


async def _decoy(stick, entry: Entry, table: Table, state: VerifyState, sleep, tries: int = 3) -> bool:
    """Put a different channel on screen so a re-check can prove the code switched it back."""
    for e in table.ordered():
        code = state.code_for(e.key) or (e.candidates[0].code if e.candidates else None)
        if e.key == entry.key or not code or (state.codes.get(code) or {}).get("status") == "dead":
            continue
        playing, _ = await stickio.tune(stick, code, style=state.url_style, timeout=15, sleep=sleep)
        tries -= 1
        if playing or tries <= 0:
            return playing
    return False


async def learn_code(stick, entry: Entry, table: Table, state: VerifyState, ev: stickio.Evidence, *,
                     lounge: LoungeFn | None, sleep, now, timeout: float) -> str | None:
    """The right channel is playing (found by search): find and prove its watch code."""
    others = _codes_of_others(entry, table, state)
    found = [c for c in [ev.lounge_id, *ev.ids] if c]
    found += [c for c, _where in await stickio.capture_codes(stick, exclude=frozenset(others))]
    for code in [c for i, c in enumerate(found) if c not in others and c not in found[:i]][:4]:
        await _decoy(stick, entry, table, state, sleep)
        res = await check_code(stick, entry, code, table, style=state.url_style, lounge=lounge,
                               timeout=timeout, sleep=sleep)
        state.record_code(code, entry.key, res.verdict, now(), res.evidence.summary())
        if res.verdict.status == "verified":
            return code
    return None


async def find_channel(stick, entry: Entry, table: Table, state: VerifyState, *, lounge: LoungeFn | None = None,
                       lock: asyncio.Lock | None = None, sleep=asyncio.sleep, now=time.time,
                       timeout: float = 20.0) -> Outcome:
    """Try each search recipe until the right channel plays; then learn its code or keep the recipe."""
    pkg = stickio.yttv_package(stick)
    for recipe in table.recipes:
        if not usable(recipe, entry):
            continue
        async with (lock or contextlib.nullcontext()):
            before = await stickio.read_evidence(stick, pkg, with_screen=False)
            await stickio.stop_playback(stick, pkg, sleep=sleep)
            steps = await run_recipe(stick, recipe, entry, pkg, sleep)
            ev = await stickio.read_evidence(stick, pkg)
            if lounge and ev.playing:
                ev.lounge_id = await safe_lounge(lounge, stick)
            verdict = judge(entry, ev, table, before=before)
            log.info("search %s via %s: %s (%s) %s", entry.key, recipe.get("name"), verdict.status, verdict.detail, steps)
            if verdict.status != "verified":
                continue
            code = await learn_code(stick, entry, table, state, ev, lounge=lounge, sleep=sleep, now=now,
                                    timeout=timeout)
        name = str(recipe.get("name"))
        if code:
            state.set_channel(entry.key, code, "verified", now(), f"found by in-app search ({name})", how="in-app search")
            state.recipes.pop(entry.key, None)
            state.save()
            return Outcome(entry.key, entry.name, "verified", code, f"found by in-app search ({name})")
        state.recipes[entry.key] = {"recipe": name, "query": entry.search, "at": now(), "evidence": ev.summary()}
        state.save()
        return Outcome(entry.key, entry.name, "search_only", None, f"recipe '{name}' lands on it")
    return Outcome(entry.key, entry.name, "not_found", None, "no recipe landed on it")


def missing(table: Table, state: VerifyState, keys=None, now: float | None = None) -> list[Entry]:
    """Channels with no working code and no fresh search recipe, most useful first."""
    now = now or time.time()
    out = []
    for e in table.ordered():
        if keys and e.key not in keys:
            continue
        if state.code_for(e.key):
            continue
        rec = (state.channels.get(e.key) or {}).get("status")
        if e.candidates and rec != "failed":
            continue                     # still has untested codes: verification goes first
        recipe = state.recipes.get(e.key) or {}
        if not keys and now - float(recipe.get("at") or 0) < RECIPE_FRESH_S:
            continue
        out.append(e)
    return out


async def search_missing(stick, table: Table | None = None, state: VerifyState | None = None, *, keys=None,
                         lounge: LoungeFn | None = None, budget_s: float = 900.0,
                         may_run: Callable[[], bool] | None = None, lock: asyncio.Lock | None = None,
                         on_progress: Callable[[str], None] | None = None, sleep=asyncio.sleep,
                         now=time.time) -> VerifyReport:
    """Search for every channel still missing a code. Time-boxed, resumable, never raises."""
    table = table or load_table()
    state = state or VerifyState()
    report = VerifyReport()
    t0 = time.monotonic()
    try:
        for entry in missing(table, state, keys, now()):
            if time.monotonic() - t0 > budget_s:
                report.stopped = "time"
                break
            if may_run is not None and not may_run():
                report.stopped = "stick needed"
                break
            out = await find_channel(stick, entry, table, state, lounge=lounge, lock=lock, sleep=sleep, now=now)
            report.outcomes.append(out)
            report.checked += 1
            if on_progress:
                on_progress(f"  {entry.name}: {out.status} - {out.detail}")
    except Exception as e:   # a bug here must never take the app down
        log.exception("search_missing crashed")
        report.stopped = f"error: {e}"
    state.save()
    return report


async def tune_by_search(stick, key: str, table: Table | None = None, state: VerifyState | None = None, *,
                         sleep=asyncio.sleep) -> bool:
    """Replay the saved recipe for a channel that has no code. True if the right channel plays."""
    table = table or load_table()
    state = state or VerifyState()
    entry, saved = table.channels.get(key), state.recipes.get(key)
    recipe = next((r for r in table.recipes if saved and r.get("name") == saved.get("recipe")), None)
    if not (entry and recipe):
        return False
    try:
        pkg = stickio.yttv_package(stick)
        await run_recipe(stick, recipe, entry, pkg, sleep)
        return judge(entry, await stickio.read_evidence(stick, pkg), table).status == "verified"
    except Exception as e:
        log.warning("tune_by_search %s failed: %s", key, e)
        return False
