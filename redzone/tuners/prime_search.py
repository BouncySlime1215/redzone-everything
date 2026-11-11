"""Bounded Prime replay-search proof, separate from the live switching engine.

Why: unknown (needs owner)

Uses a device-observed search entry path and re-reads visible focus before any
selection. It does not silently fall back to blind Play presses or other dates.
The six-column keyboard traversal is a tested-layout hypothesis, not a universal
Prime API. Changed layouts stop at the focus check. No hardware is used at import.

Why: first added in f98e81ba (2026-11-02) as "TV input flip via HOME (CEC Active Source) with stick-side verification; YouTube TV codes verifie..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
import re
import shlex
import time

from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
from redzone.tuners.adb import playback_ready
from redzone.tuners.profiles import is_profile_screen, preference, select_profile


@dataclass
class SearchProof:
    status: str = "stopped"
    reason: str = ""
    steps: list[dict] = field(default_factory=list)
    requested_date: str = ""
    selected_card: str = ""
    player_title: str = ""
    video_verified: bool = False
    elapsed_s: float = 0.0
    last_completed_stage: str = "none"
    event_date_verified: bool = False


@dataclass(frozen=True)
class CapturedScreen:
    ocr: object
    sequence: int
    captured_at: float

    def __getattr__(self, name):
        return getattr(self.ocr, name)


def _words(value):
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def has_teams(text, home, away):
    text = " " + _words(text) + " "
    return all(len(name := _words(team)) >= 4 and f" {name} " in text for team in (home, away))


def replay_card(text: str, home: str, away: str, requested: date) -> bool:
    normalized = normalize_text(text)
    wanted = rf"\b{requested.strftime('%b').lower()}\.?\s+0?{requested.day}\b"
    years = re.findall(r"\b20\d{2}\b", normalized)
    return (has_teams(text, home, away) and "replay" in normalized
            and "upcoming" not in normalized and bool(re.search(wanted, normalized))
            and (not years or all(int(year) == requested.year for year in years)))


def detail_date(text: str, requested: date) -> bool:
    return bool(re.search(rf"\b{requested.strftime('%b').lower()}\s+0?{requested.day}\s+{requested.year}\b",
                          _words(text)))


def empty_search(observation) -> bool:
    text = normalize_text(observation.text)
    query_text = [box.text for box in observation.observations if box.x < 0.30 and box.y < 0.13]
    return ("clear local history" in text and "movie picks" in text and "top sports" not in text
            and not query_text and getattr(observation, "focus_status", "none") == "none")


def unique_focus(observation):
    boxes = getattr(observation, "focus_boxes", ())
    if getattr(observation, "focus_status", "none") != "unique" or len(boxes) != 1:
        return None
    return boxes[0]


def panel_context(observation, focus) -> str:
    """Only text in the detail column above the focused playback button.

    Prime keeps search results visible behind its detail panel. Those must never
    supply a missing team, date, or entitlement for the foreground selection.
    """
    if focus is None or getattr(focus, "source", "") != "neutral_filled_button":
        return ""
    return "\n".join(box.text for box in observation.observations
                     if focus.x - .025 <= box.x and box.x + box.width <= focus.x + focus.width + .025
                     and max(0, focus.y - 3 * focus.height) <= box.y < focus.y)


def detail_verified(observation, home, away, requested):
    focus = unique_focus(observation)
    label = normalize_text(focus.text) if focus else ""
    if (is_purchase_screen(label) or not re.fullmatch(r"(?:watch replay|resume|resume watching|watch from beginning)"
                                                     r"(?: [a-z0-9 ]+ broadcast)?", label)):
        return False
    context = panel_context(observation, focus)
    return (has_teams(context, home, away) and detail_date(context, requested)
            and "included with" in normalize_text(context))


def player_overlay(observation, home, away):
    text = normalize_text(observation.text)
    if any(marker in text for marker in ("top sports", "watch replay", "more details", "add to watchlist",
                                         "included with", "clear local history")):
        return False
    title = " ".join(box.text for box in observation.observations if box.y < .35)
    progress = " ".join(box.text for box in observation.observations if box.y > .65)
    times = re.findall(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b", progress)
    return has_teams(title, home, away) and len(times) >= 2


async def run_prime_search(stick, observe, *, query: str, home: str, away: str,
                           requested: date, allow_play: bool = False,
                           sleep=asyncio.sleep, max_cards: int = 12,
                           clock=time.monotonic, proof: SearchProof | None = None,
                           may_run=None) -> SearchProof:
    """`observe` returns a freshly captured local OCRResult; callers own capture.

    Names are explicit displayed team names/mascots, not inferred abbreviations.
    Intended for a supervised replay trial on an already authorized selected stick.
    A successful proof means visible player title + media state, never video frames.
    """
    result = proof if proof is not None else SearchProof()
    result.requested_date = requested.isoformat()
    started = clock()
    last_mutation = started
    last_sequence = 0

    def record(action, detail=""):
        result.steps.append({"at_s": round(clock() - started, 3),
                             "action": action, "detail": detail})

    def allowed():
        if may_run is not None and not may_run():
            raise RuntimeError("recorded playback authorization changed; no further device commands")
        return True

    async def read(allow_empty=False):
        nonlocal last_sequence
        observation = await observe()
        if (not isinstance(observation, CapturedScreen) or observation.sequence <= last_sequence
                or not last_mutation <= observation.captured_at <= clock()
                or clock() - observation.captured_at > 10):
            raise RuntimeError("screen capture is stale or out of order")
        last_sequence = observation.sequence
        if allow_empty and observation.status == "empty":
            record("read", "empty captured frame; waiting for requested screen")
            return observation
        if not observation.read_ok:
            raise RuntimeError("screen text could not be read")
        verdict = classify_screen(observation.text)
        # Search recommendations can include an unrelated "Rent or buy" row.
        # No selection is allowed from those rows: CENTER separately requires an
        # exact replay card or included Watch Replay control with observed focus.
        if verdict and verdict.stops:
            raise RuntimeError("account, payment, or unavailable-content screen")
        record("read", observation.text[:1400])
        return observation

    async def key(value):
        nonlocal last_mutation
        allowed()
        sent = await stick.key(value)
        last_mutation = clock()
        record("key", value)
        if not sent.ok:
            raise RuntimeError("key delivery uncertain; no retry")
        await sleep(0.35)

    try:
        if not re.fullmatch(r"[A-Za-z0-9 .-]{3,70}", query):
            raise RuntimeError("query must be simple visible team text")
        if min(len(_words(home)), len(_words(away))) < 4:
            raise RuntimeError("use unambiguous displayed team names, not short abbreviations")
        # Cards without a year cannot distinguish a recording from another year.
        if requested.year != date.today().year:
            raise RuntimeError("this observed card layout does not establish an older recording year")
        package = stick.packages.get("prime")
        if not package:
            raise RuntimeError("Prime package has not been identified on the selected stick")
        allowed()
        woke = await stick.ensure_awake(may_cut=allowed) if may_run is not None else await stick.ensure_awake()
        record("wake", "WAKEUP sent" if woke else "already awake")
        if woke:
            await sleep(0.5)
        power = await stick.shell("dumpsys power | grep -m1 'mWakefulness='", timeout=4)
        if not power.ok or not re.search(r"mWakefulness=Awake\b", power.out):
            raise RuntimeError("stick did not confirm Awake after wake check; search not sent")
        allowed()
        launched = await stick.view("https://app.primevideo.com/search", package, wait=True)
        last_mutation = clock()
        record("open_search", f"ok={launched.ok}; command_s={getattr(launched, 'took', 0):.3f}; "
                              + str(getattr(launched, "out", ""))[:350])
        if not launched.ok:
            raise RuntimeError("search launch unconfirmed; no retry")
        await sleep(0.5)
        reset_deadline = clock() + 12
        profile_notes = []
        while True:
            initial = await read(allow_empty=True)
            if is_profile_screen(initial.text):
                try:
                    await select_profile(stick, initial.text, preference(stick, "prime"),
                                         profile_notes, before_key=allowed)
                finally:
                    last_mutation = clock()
                    for note in profile_notes:
                        record("profile", note)
                # A successful exact-profile choice can consume the original
                # search intent and land on Home. Re-enter search once after
                # that observed state change; never repeat a profile selection.
                allowed()
                reopened = await stick.view("https://app.primevideo.com/search", package, wait=True)
                last_mutation = clock()
                record("open_search_after_profile", f"ok={reopened.ok}")
                if not reopened.ok:
                    raise RuntimeError("search launch after profile selection unconfirmed; no retry")
                reset_deadline = clock() + 12
                await sleep(.5)
                continue
            if empty_search(initial):
                break
            if clock() >= reset_deadline:
                raise RuntimeError("search did not reset to the observed empty-query layout; no typing attempted")
            # Prime can show keyboard/history before recommendation rows finish
            # loading. Wait for the complete observed signature without typing.
            await sleep(.5)
        result.last_completed_stage = "empty_search_verified"
        allowed()
        typed = await stick.shell("input text " + shlex.quote(query.replace(" ", "%s")))
        last_mutation = clock()
        record("query", query)
        if not typed.ok:
            raise RuntimeError("query delivery unconfirmed; no retry")
        await sleep(0.5)
        results_deadline = clock() + 12
        while True:
            observation = await read(allow_empty=True)
            if "top sports" in normalize_text(observation.text) and _words(query) in _words(observation.text):
                break
            if clock() >= results_deadline:
                raise RuntimeError("expected Prime search-results layout not observed")
            # Suggestions arrive before cards on a cold search. Do not navigate
            # into a row that is still absent or issue the query a second time.
            await sleep(.5)
        result.last_completed_stage = "sports_results_observed"
        # Observed Prime layout: six keyboard columns lead into suggestions;
        # Down enters Top Sports. Never select until fresh outlined-card evidence.
        for _ in range(6):
            await key("KEYCODE_DPAD_RIGHT")
        await key("KEYCODE_DPAD_DOWN")
        seen = set()
        for _ in range(max(1, min(max_cards, 20))):
            observation = await read()
            focus = unique_focus(observation)
            if focus is None:
                raise RuntimeError("sports card focus is absent or ambiguous")
            card = normalize_text(focus.text)
            if card in seen:
                raise RuntimeError("search focus repeated; requested recording was not found")
            seen.add(card)
            if replay_card(card, home, away, requested):
                # Re-read immediately before CENTER; an animation can move focus.
                confirmation = await read()
                confirmed = unique_focus(confirmation)
                if confirmed is None or normalize_text(confirmed.text) != card:
                    raise RuntimeError("focused recording changed before selection")
                if is_purchase_screen(confirmed.text):
                    raise RuntimeError("focused card offers a purchase")
                result.selected_card = focus.text
                await key("KEYCODE_DPAD_CENTER")
                result.last_completed_stage = "matching_replay_card_selected"
                break
            await key("KEYCODE_DPAD_RIGHT")
        else:
            raise RuntimeError("requested recording not found within bounded search")
        observation = await read()
        if not has_teams(observation.text, home, away):
            raise RuntimeError("opened page does not identify both requested teams")
        if not allow_play:
            result.status, result.reason = "recording_opened", "matching replay card selected; playback not requested"
            return result
        if not detail_verified(observation, home, away, requested):
            raise RuntimeError("focused playback button and its detail panel do not verify teams, date and year, and included access")
        confirmation = await read()
        if (not detail_verified(confirmation, home, away, requested)
                or normalize_text(unique_focus(confirmation).text) != normalize_text(unique_focus(observation).text)):
            raise RuntimeError("replay detail or button focus changed before Watch selection")
        result.event_date_verified = True
        result.last_completed_stage = "replay_detail_verified"
        await key("KEYCODE_DPAD_CENTER")
        watch_at = clock()
        result.last_completed_stage = "watch_replay_selected"
        observation = await read(allow_empty=True)
        overlay_requests = 0
        while clock() - watch_at < 30:
            playback = await stick.playback(package)
            record("media_report", {key: playback.get(key) for key in ("state", "position", "updated", "speed")})
            # We reach this check only after the single verified Watch action.
            # Scheduled airing times in a lingering detail page are not progress.
            if playback_ready(playback) and player_overlay(observation, home, away):
                result.status, result.reason = "player_title_observed", "requested matchup on player overlay and media state playing; video unverified"
                result.player_title = observation.text
                result.last_completed_stage = "player_title_observed"
                return result
            text = normalize_text(observation.text)
            menu_visible = any(marker in text for marker in ("top sports", "watch replay", "more details", "add to watchlist", "included with"))
            # Rating/startup overlays can suppress the title for ~10 seconds on
            # the observed build. At most two nonselect probes, spaced apart,
            # after the detail menu disappears; never repeat Watch/CENTER.
            if (not menu_visible and overlay_requests < 2
                    and clock() - watch_at >= (6 if overlay_requests == 0 else 13)):
                await key("KEYCODE_DPAD_DOWN")
                overlay_requests += 1
            await sleep(0.5)
            observation = await read(allow_empty=True)
        raise RuntimeError("player overlay was not observed after selection")
    except RuntimeError as exc:
        result.reason = str(exc)
        return result
    finally:
        result.elapsed_s = round(clock() - started, 3)
