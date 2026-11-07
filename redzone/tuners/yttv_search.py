"""Bounded YouTube TV library inspection using observed menu geometry.

This is a navigation helper, not a live-game tuner. Relative recording ages and
the thumbnail's 'Playing' badge never establish an event date or player identity.
Captures and device authorization belong to the caller; import has no side effects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
import re
import time
from urllib.parse import quote

from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
from redzone.screen_ocr import focus_caption
from redzone.tuners.prime_search import CapturedScreen, has_teams, unique_focus, _words
from redzone.tuners.profiles import (ProfileSelectionRequired, is_profile_screen,
                                    preference, select_profile)
from redzone.tuners.adb import playback_ready
from redzone.tuners.tuners import NewStream


@dataclass
class YTTVSearchProof:
    status: str = "stopped"
    reason: str = ""
    requested_date: str = ""
    candidate_caption: str = ""
    date_evidence: str = ""
    event_date_verified: bool = False
    date_matches_request: bool = False
    year_inferred: bool = False
    source_year_visible: bool = False
    transport_ready: bool = False
    player_title: str = ""
    selected_card: str = ""
    last_completed_stage: str = "none"
    video_verified: bool = False
    elapsed_s: float = 0.0
    steps: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class WatchEvidence:
    matches: bool = False
    reason: str = ""
    event_date: str = ""
    year_inferred: bool = False
    source_year_visible: bool = False


def recording_age_hours(caption: str) -> float | None:
    """Keep the provider's age separate from the event date."""
    matches = re.findall(r"\b(?:recorded|new)\s+(\d+)\s+(minute|hour|day)s?\s+ago\b", caption, re.I)
    if len(matches) != 1:
        return None
    value, unit = matches[0]
    return int(value) * {"minute": 1 / 60, "hour": 1, "day": 24}[unit.lower()]


def dated_watch_evidence(label: str, *, home_aliases: tuple[str, ...], away_aliases: tuple[str, ...],
                         requested: date, observed_on: date, recent_recording_caption: str = "") -> WatchEvidence:
    """Validate the observed Watch month/day action without inventing a source year.

    Aliases are supplied by the caller for the requested teams; WAS/PHI are never
    guessed from logos. A missing year may be inferred only for a recent request
    in the current local year, corroborated by a recent scoped recording caption.
    This validates text, not focus. A caller still needs fresh action geometry.
    """
    label = normalize_text(label).strip()
    if _purchase(label):
        return WatchEvidence(reason="dated Watch action offers a purchase")
    match = re.fullmatch(r"watch\s+([a-z]{3,9})\.?\s+(\d{1,2})(?:,?\s+(20\d{2}))?"
                         r"\s*[•·|—-]\s*(.+?)\s+(?:vs\.?|at)\s+(.+)", label)
    if not match:
        return WatchEvidence(reason="exact dated Watch label is missing")
    month, day, year, left, right = match.groups()
    home = {_words(alias) for alias in home_aliases if len(_words(alias)) >= 2}
    away = {_words(alias) for alias in away_aliases if len(_words(alias)) >= 2}
    if not home or not away or home & away:
        return WatchEvidence(reason="team aliases are missing or overlap")
    if not ((_words(left) in home and _words(right) in away)
            or (_words(left) in away and _words(right) in home)):
        return WatchEvidence(reason="dated Watch action identifies another matchup")
    parsed = None
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            parsed = datetime.strptime(f"{month} {day} {year or requested.year}", fmt).date()
            break
        except ValueError:
            continue
    if parsed != requested:
        return WatchEvidence(reason="dated Watch action does not match requested calendar date")
    if year:
        return WatchEvidence(True, "explicit matchup and full event date", parsed.isoformat(), False, True)
    age = recording_age_hours(recent_recording_caption)
    same_recording = (any(len(alias) >= 4 and _contains(recent_recording_caption, alias) for alias in home)
                      and any(len(alias) >= 4 and _contains(recent_recording_caption, alias) for alias in away))
    # The Library's team tile itself shows "New ... • Watch Nov 1...". Its
    # freshly opened action supplies both teams; the tile supplies recency.
    catalog_date = re.search(rf"\bnew\s+\d+\s+(?:minute|hour|day)s?\s+ago\b.*?\bwatch\s+"
                             rf"{requested.strftime('%b').lower()}\s+0?{requested.day}\b",
                             normalize_text(recent_recording_caption))
    same_recording = same_recording or (bool(catalog_date) and any(
        len(alias) >= 4 and _contains(recent_recording_caption, alias) for alias in home | away))
    if (requested.year != observed_on.year or not 0 <= (observed_on - requested).days <= 7
            or age is None or not 0 <= age <= 72 or not same_recording):
        return WatchEvidence(reason="Watch month/day is explicit but year lacks recent recording evidence")
    return WatchEvidence(True, "Watch month/day is explicit; year inferred from current local year and recent recording evidence",
                         parsed.isoformat(), True, False)


async def finish_yttv_watch(stick, observe, *, home_aliases: tuple[str, ...], away_aliases: tuple[str, ...],
                            requested: date, observed_on: date, recent_recording_caption: str,
                            sleep=asyncio.sleep, clock=time.monotonic,
                            proof: YTTVSearchProof | None = None, may_run=None) -> YTTVSearchProof:
    """Complete the observed dated-Watch -> full-replay chooser path, once.

    The caller reaches the Library action first. This function never guesses a
    highlighted action or chooses Watch key plays. Playback evidence remains
    separate from visual video-frame verification, including when DRM hides video.
    """
    result = proof if proof is not None else YTTVSearchProof()
    result.requested_date = requested.isoformat()
    started, last_sequence = clock(), 0
    last_mutation = started

    def record(action, detail=""):
        result.steps.append({"at_s": round(clock() - started, 3), "action": action, "detail": detail})

    def allowed():
        if may_run is not None and not may_run():
            raise RuntimeError("recorded playback authorization changed; no further device commands")
        if clock() - started > 45:
            raise RuntimeError("replay verification time budget exhausted")

    async def read(empty=False):
        nonlocal last_sequence
        allowed()
        try:
            frame = await asyncio.wait_for(observe(), timeout=max(.001, min(12, 45 - (clock() - started))))
        except asyncio.TimeoutError:
            raise RuntimeError("fresh replay capture timed out") from None
        allowed()
        if (not isinstance(frame, CapturedScreen) or frame.sequence <= last_sequence
                or not last_mutation <= frame.captured_at <= clock() or clock() - frame.captured_at > 10):
            raise RuntimeError("replay capture is stale or out of order")
        last_sequence = frame.sequence
        if empty and frame.status == "empty":
            record("read", "empty captured frame; video remains unverified")
            return frame
        if not frame.read_ok:
            raise RuntimeError("replay menu text could not be read")
        verdict = classify_screen(frame.text)
        if is_purchase_screen(frame.text) or (verdict and verdict.stops):
            raise RuntimeError("account, payment, or unavailable-content screen")
        record("read", frame.text[:1400])
        return frame

    async def key(code):
        nonlocal last_mutation
        allowed()
        sent = await stick.key(code)
        last_mutation = clock()
        record("key", code)
        if not sent.ok:
            raise RuntimeError("replay key delivery uncertain; no retry")
        await sleep(.35)

    def action(frame):
        focus = unique_focus(frame)
        if focus is None or focus.source != "neutral_filled_button":
            raise RuntimeError("replay action focus is absent or ambiguous")
        return normalize_text(focus.text).strip()

    def watch(frame):
        return dated_watch_evidence(action(frame), home_aliases=home_aliases, away_aliases=away_aliases,
                                    requested=requested, observed_on=observed_on,
                                    recent_recording_caption=recent_recording_caption)

    def title_matches(text):
        return (any(len(_words(a)) >= 4 and _contains(text, a) for a in home_aliases)
                and any(len(_words(a)) >= 4 and _contains(text, a) for a in away_aliases))

    def player_title(frame):
        text = normalize_text(frame.text)
        if any(s in text for s in ("how would you like", "watch key plays", "start from beginning",
                                   "top picks", "completed", "recently recorded", "new for you",
                                   "choose a version", "library")):
            return ""
        title = " ".join(b.text for b in frame.observations if b.y < .20)
        progress = " ".join(b.text for b in frame.observations if b.y < .20 or b.y > .65)
        return title if title_matches(title) and len(re.findall(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b", progress)) >= 2 else ""

    try:
        pkg = stick.packages.get("youtubetv")
        if not pkg:
            raise RuntimeError("YouTube TV package is not identified")
        frame = await read()
        evidence = watch(frame)
        if not evidence.matches:
            raise RuntimeError(evidence.reason)
        before = await stick.playback(pkg)
        confirmed = await read()
        if action(confirmed) != action(frame) or not watch(confirmed).matches:
            raise RuntimeError("dated Watch action changed before selection")
        result.date_matches_request = True
        result.year_inferred = evidence.year_inferred
        result.source_year_visible = evidence.source_year_visible
        result.event_date_verified = evidence.source_year_visible
        result.date_evidence = evidence.reason
        result.selected_card = action(confirmed)
        await key("KEYCODE_DPAD_CENTER")
        result.last_completed_stage = "dated_watch_selected"
        frame = await read()
        if "how would you like to begin watching" not in normalize_text(frame.text):
            raise RuntimeError("expected full-replay chooser was not observed; no second Select sent")
        label = action(frame)
        if label == "watch key plays":
            if not any(normalize_text(b.text).strip() == "start from beginning" for b in frame.observations):
                raise RuntimeError("full-replay option is not readable")
            await key("KEYCODE_DPAD_DOWN")
            frame = await read()
            label = action(frame)
        if label != "start from beginning":
            raise RuntimeError("Start from beginning is not uniquely focused")
        frame = await read()
        if action(frame) != "start from beginning" or "how would you like to begin watching" not in normalize_text(frame.text):
            raise RuntimeError("full-replay focus changed before selection")
        await key("KEYCODE_DPAD_CENTER")
        result.last_completed_stage = "replay_start_sent"
        watch_transition = NewStream(before)
        playback_started, overlay_requests = clock(), 0
        for _ in range(16):
            report = await stick.playback(pkg)
            transitioned = watch_transition.check(report)
            ready = playback_ready(report)
            result.transport_ready = ready
            record("playback", {k: report.get(k) for k in ("state", "speed", "position", "updated", "read_ok")})
            frame = await read(empty=True)
            title = player_title(frame)
            if ready and transitioned and title:
                result.player_title = title
                result.status = "player_title_observed"
                result.reason = "dated recording selected from beginning; new playback and matchup overlay observed; video frames unverified"
                result.last_completed_stage = "player_identity_observed"
                return result
            text = normalize_text(frame.text)
            elapsed = clock() - playback_started
            if elapsed > 25:
                break
            if (overlay_requests < 2 and elapsed >= (3 if overlay_requests == 0 else 10)
                    and not any(s in text for s in ("how would you like", "watch key plays", "start from beginning",
                                                    "completed", "new for you", "library"))):
                await key("KEYCODE_DPAD_DOWN")
                overlay_requests += 1
            await sleep(.5)
        raise RuntimeError("full replay was requested but new player identity was not confirmed")
    except RuntimeError as exc:
        result.status, result.reason = "stopped", str(exc)
        return result
    finally:
        result.elapsed_s = round(clock() - started, 3)


def focused_row(observation, focus) -> str:
    """The nearest recognized row heading above this card, never a page-wide match."""
    headings = []
    for box in observation.observations:
        label = normalize_text(box.text).strip()
        if (label in {"top picks", "from your library", "completed", "from youtube"}
                and 0 <= focus.y - (box.y + box.height) <= .10
                and box.x <= focus.x + .03):
            headings.append((box.y, label))
    return max(headings)[1] if headings else ""


def explicit_event_date(text: str) -> date | None:
    """Only a labeled event/air date, not the age/date of a recording or upload.

    No relative-age arithmetic or assumption that a bare date means the game date.
    Additional provider layouts require their own observed evidence and validation.
    """
    matches = re.findall(r"\b(?:game date|event date|aired|played)\s*:?\s*"
                         r"(\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\b",
                         text, re.I)
    dates = set()
    for value in matches:
        value = value.replace(".", "").replace(",", "")
        parsed = False
        for fmt in ("%Y-%m-%d", "%b %d %Y", "%B %d %Y"):
            try:
                dates.add(datetime.strptime(value, fmt).date())
                parsed = True
                break
            except ValueError:
                continue
        if not parsed:
            return None
    return next(iter(dates)) if len(dates) == 1 else None


def _contains(text, phrase):
    return f" {_words(phrase)} " in f" {_words(text)} "


def _purchase(text):
    return is_purchase_screen(text) or bool(re.search(r"\badd .{1,60} to watch\b", normalize_text(text)))


async def run_yttv_search(stick, observe, *, query: str, home: str, away: str,
                          requested: date, allow_select: bool = False, max_cards: int = 8,
                          sleep=asyncio.sleep, clock=time.monotonic,
                          proof: YTTVSearchProof | None = None, may_run=None) -> YTTVSearchProof:
    """Inspect search/library cards; optionally select one with explicit event proof.

    ``observe`` must return fresh CapturedScreen envelopes. A team-only query may
    open its exact focused team catalog. A matchup query can move from Top picks
    down into the observed Library row. Neither action claims playback success.
    ``home`` and ``away`` are explicit readable team names/mascots, not inferred
    abbreviations. The caller must authorize this selected device before invocation.
    """
    result = proof if proof is not None else YTTVSearchProof()
    result.requested_date = requested.isoformat()
    started = clock()
    last_mutation, last_sequence = started, 0

    def record(action, detail=""):
        result.steps.append({"at_s": round(clock() - started, 3), "action": action, "detail": detail})

    def within_budget():
        if may_run is not None and not may_run():
            raise RuntimeError("recorded playback authorization changed; no further device commands")
        if clock() - started > 60:
            raise RuntimeError("navigation time budget exhausted; no further keys sent")
        return True

    async def read():
        nonlocal last_sequence
        within_budget()
        try:
            observation = await asyncio.wait_for(observe(), timeout=min(12, 60 - (clock() - started)))
        except asyncio.TimeoutError:
            raise RuntimeError("fresh menu capture timed out; no further keys sent") from None
        within_budget()
        if (not isinstance(observation, CapturedScreen) or observation.sequence <= last_sequence
                or not last_mutation <= observation.captured_at <= clock()
                or clock() - observation.captured_at > 10):
            raise RuntimeError("screen capture is stale or out of order")
        last_sequence = observation.sequence
        if not observation.read_ok:
            raise RuntimeError("menu text could not be read")
        verdict = classify_screen(observation.text)
        if verdict and verdict.stops:
            raise RuntimeError("account, payment, or unavailable-content screen")
        record("read", observation.text[:1400])
        return observation

    async def key(code):
        nonlocal last_mutation
        within_budget()
        sent = await stick.key(code)
        last_mutation = clock()
        record("key", code)
        if not sent.ok:
            raise RuntimeError("key delivery uncertain; no retry")
        await sleep(.35)

    def card(observation, expected_title=None):
        focus = unique_focus(observation)
        if focus is None or getattr(focus, "source", "") != "neutral_rectangle_border":
            raise RuntimeError("card focus is absent or ambiguous")
        caption = focus_caption(observation, focus, expected_title=expected_title)
        if caption.status != "matched":
            raise RuntimeError("focused card caption is missing or ambiguous")
        return focus, caption.text, focused_row(observation, focus)

    async def confirm(observation, caption, row, expected_title=None):
        fresh = await read()
        _, new_caption, new_row = card(fresh, expected_title)
        if normalize_text(new_caption) != normalize_text(caption) or row != new_row:
            raise RuntimeError("focused card changed before selection")
        if _purchase(new_caption):
            raise RuntimeError("focused card offers a purchase")
        return fresh

    try:
        if not re.fullmatch(r"[A-Za-z0-9 .-]{3,90}", query):
            raise RuntimeError("query must be simple visible team text")
        if min(len(_words(home)), len(_words(away))) < 4 or _words(home) == _words(away):
            raise RuntimeError("use two distinct unambiguous team names")
        pkg = stick.packages.get("youtubetv")
        if not pkg:
            raise RuntimeError("YouTube TV package is not identified on the selected stick")
        within_budget()
        woke = await stick.ensure_awake(may_cut=within_budget) if may_run is not None else await stick.ensure_awake()
        last_mutation = clock()
        record("wake", "WAKEUP sent" if woke else "already awake")
        power = await stick.shell("dumpsys power | grep -m1 'mWakefulness='", timeout=4)
        if not power.ok or not re.search(r"mWakefulness=Awake\b", power.out):
            raise RuntimeError("stick did not confirm Awake; search not sent")
        within_budget()
        launched = await stick.view("https://tv.youtube.com/search?q=" + quote(query, safe=""), pkg, wait=True)
        last_mutation = clock()
        record("open_search", f"ok={launched.ok}")
        if not launched.ok:
            raise RuntimeError("search launch unconfirmed; no retry")
        await sleep(.5)
        observation = await read()
        if is_profile_screen(observation.text):
            notes = []
            try:
                await select_profile(stick, observation.text, preference(stick, "youtubetv"), notes,
                                     before_key=within_budget)
            finally:
                last_mutation = clock()
                for note in notes:
                    record("profile", note)
            observation = await read()
        heading = " ".join(box.text for box in observation.observations if .13 <= box.y < .30)
        if _words(query) != _words(heading):
            raise RuntimeError("requested search heading was not observed")
        top_picks = [b for b in observation.observations
                     if normalize_text(b.text).strip() == "top picks" and .30 <= b.y < .60]
        if len(top_picks) != 1:
            raise RuntimeError("observed Top picks entry layout is missing")
        result.last_completed_stage = "search_opened"
        is_team_query = sum(_contains(query, name) for name in (home, away)) == 1
        focus = unique_focus(observation)
        evidence = focus_caption(observation, focus, expected_title=query if is_team_query else None)
        caption = evidence.text if evidence.status == "matched" else ""
        row = focused_row(observation, focus) if focus else ""
        if is_team_query and row == "top picks" and _words(caption) == _words(query) and not _purchase(caption):
            await confirm(observation, caption, row, expected_title=query)
            await key("KEYCODE_DPAD_CENTER")
            result.last_completed_stage = "team_catalog_opened"
            observation = await read()
            expected_row = "completed"
        else:
            # One nonselect DOWN is allowed from this observed search layout even
            # if bright thumbnail artwork obscures its outline. The next capture
            # must prove unique Library focus before any further navigation.
            library = [b for b in observation.observations
                       if normalize_text(b.text).strip() == "from your library"
                       and b.y > top_picks[0].y + .25]
            if len(library) != 1:
                raise RuntimeError("no observed Library row below Top picks; no result selected")
            await key("KEYCODE_DPAD_DOWN")
            observation = await read()
            expected_row = "from your library"
        seen = set()
        incomplete = ""
        for _ in range(max(1, min(max_cards, 12))):
            focus, caption, row = card(observation)
            if row != expected_row:
                raise RuntimeError("focus left the expected recording row")
            signature = normalize_text(caption)
            if signature in seen:
                raise RuntimeError(incomplete or "recording focus repeated; requested game not found")
            seen.add(signature)
            if _purchase(caption):
                raise RuntimeError("focused recording offers a purchase")
            if has_teams(caption, home, away):
                result.candidate_caption = caption
                result.last_completed_stage = "matching_caption_observed"
                observed_date = explicit_event_date(caption)
                result.date_evidence = observed_date.isoformat() if observed_date else "displayed recording age/date does not establish event date"
                if observed_date is None:
                    result.status = "candidate_found"
                    result.reason = "matchup caption found; exact event date is unverified, so recording was not selected"
                    return result
                if observed_date == requested:
                    result.event_date_verified = True
                    result.status = "recording_identified"
                    result.reason = "focused recording caption matches both teams and explicit event date"
                    if allow_select:
                        await confirm(observation, caption, row)
                        await key("KEYCODE_DPAD_CENTER")
                        result.selected_card = caption
                        result.status = "selection_sent"
                        result.reason = "identified recording selection sent; playback and visible game remain unverified"
                        result.last_completed_stage = "recording_selection_sent"
                    return result
            elif any(_contains(caption, name) for name in (home, away)):
                incomplete = "recording captions omit a requested team; no identity inferred from logos or background text"
                result.candidate_caption = caption
            await key("KEYCODE_DPAD_RIGHT")
            observation = await read()
        raise RuntimeError(incomplete or "requested recording not found within bounded search")
    except (RuntimeError, ProfileSelectionRequired) as exc:
        result.status = "stopped"
        result.event_date_verified = False
        result.reason = str(exc)
        return result
    finally:
        result.elapsed_s = round(clock() - started, 3)


# ============================================================================
# Channel hunt: find a live channel we have no watch code for, by searching
# inside YouTube TV and reading the screen with Vision OCR (Cobalt's UI is
# visible in screencap even though uiautomator sees nothing). Used by
# scripts/find_yttv_code.py on a spare stick, never by the live engine.
#
# Guardrails: no WAKEUP or HOME (either can flip the TV over CEC); a stick that
# isn't Awake stops the hunt. Every key follows a fresh capture; Select is sent
# only after two consecutive captures agree the channel's tile has focus; any
# account/purchase text stops it. Channel identity comes from the app's media
# session (judge()), never from the tile alone. A recovered code is trusted only
# after a clean re-tune from a different channel shows the same channel again.

HUNT_BUDGET_S = 120.0
WATCH_BUTTONS = ("watch live", "watch", "play", "watch now", "resume", "live")

# Read-only places on the stick that may name the watch code of what is playing,
# on top of stickio.CAPTURE_PROBES (media session, activity intents, logcat, watch-next).
# All UNVERIFIED on Fire OS until run; the raw output is kept so it can be read by hand.
EXTRA_CODE_PROBES = (
    ("activity recents", "dumpsys activity recents 2>/dev/null | grep -E 'dat=|intent=|baseIntent' | head -n 60"),
    ("cobalt logcat", "logcat -d -t 8000 2>/dev/null | grep -i -E 'cobalt|starboard|youtube|unplugged' "
                      "| grep -i -E 'watch|video|v=|docid|cpn' | tail -n 200"),
    ("activity intents (full)", "dumpsys activity intents 2>/dev/null | grep -i -E 'youtube|dat=' | head -n 60"),
    ("media session (full)", "dumpsys media_session"),
)
# Looser than stickio.codes_in: '/watch/<id>', 'v=<id>', 'docid=<id>', '"video_id":"<id>"'.
_LOOSE_ID_RE = re.compile(
    r"(?:/watch/|[?&]v=|\bdocid=|\bvideo_?id[\"']?\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", re.I)


@dataclass
class ChannelHunt:
    key: str
    status: str = "stopped"          # confirmed | stopped
    reason: str = ""
    tile: str = ""                   # OCR text of the tile that was selected
    description: str = ""            # media-session text that confirmed the channel
    candidates: list[dict] = field(default_factory=list)   # [{code, source}], best first
    probes: dict[str, str] = field(default_factory=dict)   # probe label -> raw output (kept for review)
    steps: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0


# Channel-page search tune (Nov 7, hidden stick): search -> 3 s -> DOWN -> 1.5 s -> Select tuned 4/4 locals
# (Select alone from the search page 0/4 by hand); Select at the starting focus is kept as the fallback
# (it worked once in the --blind hunt, dce27c18, after a reopened search).
# Nov 7 12:23 (live app, UCLA@MINN on FOX 50): DURING A LIVE GAME the page differs: focus starts on the header
# search icon, DOWN 1 is 'Top picks' (recorded newscasts), DOWN 2 is 'On now & upcoming' whose first tile is the
# LIVE game; Select opens a 'How would you like to begin watching?' sheet with 'Join live' focused (above 'Watch
# in multiview'), CENTER plays the game. ABC 11 tuned live with 1 DOWN (12:29), so 1 DOWN stays first (FOX's
# 1 DOWN lands on a Top picks recording, dropped in ~5 s), then 2, then 3, then Select at the start. Every
# path is checked by the media session; OCR row titles can't tell the layouts apart (both show 'Top picks').
_D = "KEYCODE_DPAD_DOWN"
CHANNEL_PAGE_PATHS = ((_D,), (_D, _D), (_D, _D, _D), ())
JOIN_LIVE_RE = re.compile(r"\bjoin live\b")
BEGIN_SHEET_RE = re.compile(r"how would you like to begin watching")
CHANNEL_PAGE_SHEET_S = 1.5
CHANNEL_PAGE_RECORDING_S = 5.0
# A live YouTube TV channel's playhead is its stream clock (ESPN ~46,800 s on Nov 6); a recording starts near 0.
LIVE_MIN_MS = 30 * 60 * 1000
CHANNEL_PAGE_DOWN_S = 3.0
CHANNEL_PAGE_SELECT_S = 1.5
CHANNEL_PAGE_WAIT_S = 15.0
# Nov 7 11:00:00 (Nick, raw recipe with no checks): at a program change the channel page's live tile shifted
# and DOWN -> Select landed on 'Jaguars at Ravens, NFL Network'. Near :00/:30 (wall clock, local time),
# let the page settle a little longer before any key; the subtitle station check stays mandatory regardless.
CHANNEL_PAGE_BOUNDARY_WINDOW_S = 90.0
CHANNEL_PAGE_BOUNDARY_SETTLE_S = 3.0


def near_program_change(wall_s: float, window_s: float = CHANNEL_PAGE_BOUNDARY_WINDOW_S) -> bool:
    """Within window_s of a :00 or :30 boundary (either side), local time."""
    lt = time.localtime(wall_s)
    into = (lt.tm_min % 30) * 60 + lt.tm_sec + (wall_s % 1)
    return into <= window_s or 1800 - into <= window_s


_PKG_RE = re.compile(r"\b(com\.[\w.]*youtube[\w.]*)")
_PLAIN_YT_RE = re.compile(r"(?<!tv\.)\b(?:www\.|m\.|music\.)?youtube\.com/(?:watch|shorts)|\byoutu\.be/", re.I)


def yttv_lines(text: str, pkg: str) -> str:
    """Probe output minus lines about PLAIN YouTube (Nov 7: a Lofi Girl id in activity recents came from
    the YouTube app, not YouTube TV): a youtube.com/youtu.be link that isn't tv.youtube.com, or a line
    naming a YouTube package other than this stick's YouTube TV package."""
    keep = []
    for line in (text or "").splitlines():
        if _PLAIN_YT_RE.search(line):
            continue
        pkgs = {m.rstrip(".") for m in _PKG_RE.findall(line)}
        if pkgs and pkg and all(not p.startswith(pkg) for p in pkgs):
            continue
        keep.append(line)
    return "\n".join(keep)


def loose_codes_in(text: str) -> list[str]:
    """Watch codes mentioned anywhere in a probe's output, in order, no duplicates."""
    from redzone.discovery import stickio
    out: list[str] = []
    for code in stickio.codes_in(text) + _LOOSE_ID_RE.findall(text or ""):
        if code not in out:
            out.append(code)
    return out


def _same_focus(a, b, tol: float = 0.012) -> bool:
    """The same focused tile in two reads: the ring's box moves a pixel or two between screenshots
    (Nov 3, the NBCSN tile), so positions match within tol; confidence and text may differ."""
    if a is None or b is None:
        return False
    return all(abs(getattr(a, k) - getattr(b, k)) <= tol for k in ("x", "y", "width", "height"))


def _center(box) -> tuple[float, float]:
    return box.x + box.width / 2, box.y + box.height / 2


def _channel_scores(text: str, table) -> dict[str, int]:
    from redzone.discovery.yttv import alias_score
    return {k: alias_score(text, e.all_aliases())[0] for k, e in table.channels.items()}


def names_channel(text: str, key: str, table) -> bool:
    """Does this text name `key` more specifically than any other channel? ('NBC Sports' is NBCSN, not WRAL.)"""
    scores = _channel_scores(text, table)
    mine = scores.get(key, 0)
    return mine > 0 and all(s < mine for k, s in scores.items() if k != key)


def channel_tiles(ocr, key: str, table, query: str = "") -> list[tuple[float, object]]:
    """OCR boxes that look like `key`'s live channel tile, best first: [(score, box)].

    The search heading (which repeats the query) and anything offering a purchase are skipped;
    a 'LIVE' badge in the same tile adds weight. The layout is a hypothesis until seen on a stick.
    """
    out = []
    boxes = list(getattr(ocr, "observations", ()))
    for box in boxes:
        text = box.text or ""
        if box.y < .30 and query and _words(text) == _words(query):
            continue                      # the search heading / typed query
        if _purchase(text) or not names_channel(text, key, table):
            continue
        cx, cy = _center(box)
        live = bool(re.search(r"\blive\b", normalize_text(text))) or any(
            b is not box and re.search(r"\blive\b", normalize_text(b.text))
            and abs(_center(b)[0] - cx) < .12 and abs(_center(b)[1] - cy) < .15 for b in boxes)
        out.append((_channel_scores(text, table)[key] + (5 if live else 0) + box.confidence, box))
    return sorted(out, key=lambda pair: -pair[0])


def focus_text(ocr, focus) -> str:
    """Text inside the focused tile or in its caption just below it."""
    if focus is None:
        return ""
    texts = [focus.text] if getattr(focus, "text", "") else []
    for box in getattr(ocr, "observations", ()):
        cx, cy = _center(box)
        if (focus.x - .01 <= cx <= focus.x + focus.width + .01
                and focus.y - .01 <= cy <= focus.y + focus.height + .14):
            texts.append(box.text)
    return " ".join(texts)


def step_toward(focus, target) -> str:
    """One D-pad key that moves focus toward the target box (rows first)."""
    fx, fy = _center(focus)
    tx, ty = _center(target)
    if ty > focus.y + focus.height + .14 or ty < focus.y - .01:
        return "KEYCODE_DPAD_DOWN" if ty > fy else "KEYCODE_DPAD_UP"
    return "KEYCODE_DPAD_RIGHT" if tx > fx else "KEYCODE_DPAD_LEFT"


async def hunt_channel(stick, observe, *, key: str, table, query: str = "", profile: str = "",
                       sleep=asyncio.sleep, clock=time.monotonic, wall=time.time, max_moves: int = 14,
                       budget_s: float = HUNT_BUDGET_S, lounge=None, collect_codes: bool = True,
                       channel_page: bool = False) -> ChannelHunt:
    """Search YouTube TV for one channel, select its live tile, confirm it plays, collect code candidates.

    ``observe`` returns a fresh CapturedScreen (screencap + Vision OCR). ``lounge`` is an optional
    async host -> now-playing video id (discovery.lounge.now_playing). Candidates still need
    prove_code(). Never raises.

    ``channel_page`` (ABC 11, Nov 7): the search opens the station's channel page, but OCR can't read
    the focus ring there. Then, per path in CHANNEL_PAGE_PATHS (DOWN, Select; else reopen the
    search and Select at the starting focus): accept only when the media session's SUBTITLE contains the
    query ('..., ABC 11') AND the playhead is live (>= LIVE_MIN_MS, a recording starts near 0);
    a miss presses BACK. Waits: >= 3 s after the search link (+3 s within 90 s of :00/:30, when
    the live tile can shift), >= 1.5 s after DOWN. The station check is never skipped.
    """
    from redzone.discovery import stickio
    from redzone.discovery.yttv import judge

    hunt = ChannelHunt(key)
    entry = table.channels.get(key)
    started, last_sequence = clock(), 0
    search_at = started

    def record(action, detail=""):
        hunt.steps.append({"at_s": round(clock() - started, 2), "action": action, "detail": str(detail)[:400]})

    def budget():
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
            raise RuntimeError(f"account, payment or unavailable screen: {frame.text[:120]!r}")
        record("read", frame.text[:600])
        return frame

    async def press(code):
        budget()
        sent = await stick.key(code)
        record("key", code)
        if not getattr(sent, "ok", False):
            raise RuntimeError(f"{code} delivery uncertain; no retry")
        await sleep(.6)

    async def open_search():
        nonlocal search_at
        search_at = clock()
        launched = await stick.view("https://tv.youtube.com/search?q=" + quote(query, safe=""), pkg, wait=True)
        record("open_search", f"{query!r} ok={launched.ok}")
        if not launched.ok:
            raise RuntimeError("search link was refused; no retry")
        await sleep(2.0)
        return await read()

    async def clear_profile(frame):
        """'Who's watching?' after an app restart: select the configured profile by OCR focus."""
        if not profile:
            raise RuntimeError("profile picker shown; pass --profile <name> (e.g. Serge) or pick it on the stick")
        wanted = normalize_text(profile).strip()
        for _ in range(6):
            names = [b for b in frame.observations if normalize_text(b.text).strip() == wanted]
            focus = unique_focus(frame)
            if len(names) != 1 or focus is None:
                raise RuntimeError(f"profile picker: '{profile}' or the focus ring isn't readable; pick it on the stick")
            nx = _center(names[0])[0]
            if focus.x - .01 <= nx <= focus.x + focus.width + .01:
                again = await read()
                if not _same_focus(unique_focus(again), focus):
                    raise RuntimeError("profile focus changed before selection")
                await press("KEYCODE_DPAD_CENTER")
                record("profile", f"selected {profile}")
                await sleep(2.0)
                return await read()
            await press("KEYCODE_DPAD_RIGHT" if nx > _center(focus)[0] else "KEYCODE_DPAD_LEFT")
            frame = await read()
        raise RuntimeError("profile focus did not reach the configured profile")

    async def open_network_live(frame) -> bool:
        """The tile opened the channel's NETWORK page (NBCSN: a hero carousel, then Live / Upcoming /
        Series / News with the live tile beside Live), not the channel (Nov 3, by hand on the 4K Plus):
        DOWN to the Live row, RIGHT onto its live tile, Select."""
        t = normalize_text(frame.text)
        # NBCSN: Live / Upcoming / Series / News. NBC (local WRAL): Live / Series / Popular / ...
        # with the station's NOW tile beside Live.
        menu = sum(w in t for w in ("upcoming", "series", "news", "popular"))
        if not ("live" in t and menu >= 2):
            return False
        await press("KEYCODE_DPAD_DOWN")
        await sleep(.8)
        await press("KEYCODE_DPAD_RIGHT")
        await sleep(.8)
        await press("KEYCODE_DPAD_CENTER")
        record("network_page", "Live row -> live tile")
        return True

    async def wait_confirmed(before, seconds=16.0):
        pressed_watch = pressed_network = False
        t0 = clock()
        while clock() - t0 < seconds:
            ev = await stickio.read_evidence(stick, pkg, with_screen=False)
            verdict = judge(entry, ev, table, before=before)
            record("media", f"{ev.summary()} -> {verdict.status}")
            if verdict.status == "verified" or names_now(ev, before, key, table):
                return ev
            if verdict.status == "wrong_channel":
                raise RuntimeError(f"selected tile plays another channel ({verdict.detail})")
            if not ev.playing and not pressed_network and clock() - t0 >= 3:
                frame = await read()
                if await open_network_live(frame):
                    pressed_network = True
                    t0 = clock()   # the live tile gets its own full wait
                    await sleep(1.0)
                    continue
            if not ev.playing and not pressed_watch and clock() - t0 >= 4:
                # A channel/detail page instead of playback: one Select, on a readable Watch button only.
                frame = await read()
                focus = unique_focus(frame)
                label = normalize_text(focus.text if focus else "").strip()
                if focus is not None and focus.source == "neutral_filled_button" and label in WATCH_BUTTONS:
                    await press("KEYCODE_DPAD_CENTER")
                    pressed_watch = True
                    record("watch_button", label)
            await sleep(1.0)
        return None

    async def subtitle_confirmed(before, joined=False, seconds=CHANNEL_PAGE_WAIT_S):
        """Only the subtitle counts (the channel field, 'UCLA at Minnesota, FOX 50'), and it must contain
        the query itself ('ABC 11', not 'ABC News Live'); the session must have changed. Live: a stream-clock
        playhead (>= LIVE_MIN_MS), or 'Join live' was pressed on this attempt and it is playing (right after
        Join live the position reads 0, Nov 7). A same-station recording (Top picks) is dropped after
        CHANNEL_PAGE_RECORDING_S. Returns (evidence or None, why not)."""
        t0, why, rec_at = clock(), "nothing played", None
        while clock() - t0 < seconds:
            ev = await stickio.read_evidence(stick, pkg, with_screen=False)
            record("media", f"{ev.summary()} pos={ev.position}")
            if (ev.read_ok and ev.state in LOADED_STATES and ev.meta_text != getattr(before, "meta_text", None)
                    and ev.subtitle and names_channel(ev.subtitle, key, table) and _contains(ev.subtitle, query)):
                if ev.position is not None and ev.position >= LIVE_MIN_MS:
                    return ev, ""
                if joined and ev.state == "playing":
                    return ev, ""
                why = f"{ev.meta_text!r} at {ev.position} ms is a recording, not live"
                rec_at = rec_at if rec_at is not None else clock()
                if not joined and clock() - rec_at >= CHANNEL_PAGE_RECORDING_S:
                    return None, why
            elif ev.meta_text and ev.meta_text != getattr(before, "meta_text", None):
                why = f"plays {ev.meta_text!r}"
            await sleep(1.0)
        return None, why

    async def join_live_sheet() -> bool:
        """After Select: the 'How would you like to begin watching?' sheet opens with 'Join live' focused.
        CENTER once on it; anything else (no sheet, unreadable video frame) presses nothing. Never multiview."""
        await sleep(CHANNEL_PAGE_SHEET_S)
        try:
            frame = await read()
        except RuntimeError as exc:          # playing video is DRM-black: no sheet to answer
            record("channel_page", f"no sheet read ({exc})")
            return False
        t = normalize_text(frame.text)
        if not (BEGIN_SHEET_RE.search(t) and JOIN_LIVE_RE.search(t)):
            return False
        focus = unique_focus(frame)
        if focus is not None and "multiview" in normalize_text(focus_text(frame, focus)):
            raise RuntimeError("begin-watching sheet has 'Watch in multiview' focused; not pressing it")
        await press("KEYCODE_DPAD_CENTER")
        record("channel_page", "begin-watching sheet: Join live")
        return True

    async def channel_page_select(before):
        misses = []
        for i, path in enumerate(CHANNEL_PAGE_PATHS):
            if i:
                await open_search()                # focus resets on a fresh search link
            settle = CHANNEL_PAGE_DOWN_S
            if near_program_change(wall()):
                settle += CHANNEL_PAGE_BOUNDARY_SETTLE_S
                record("channel_page", f"near a :00/:30 program change: +{CHANNEL_PAGE_BOUNDARY_SETTLE_S:.0f} s settle")
            await sleep(max(0.0, settle - (clock() - search_at)))
            for code in path:
                await press(code)
                await sleep(CHANNEL_PAGE_SELECT_S)
            await press("KEYCODE_DPAD_CENTER")
            label = "+".join(c.replace("KEYCODE_DPAD_", "") for c in path) or "start"
            record("channel_page", f"ring unread: {label} -> Select")
            joined = await join_live_sheet()
            ev, why = await subtitle_confirmed(before, joined=joined)
            if ev is not None:
                hunt.tile = f"(channel page: {label}{' + Join live' if joined else ''})"
                return ev
            misses.append(f"{label}: {why}")
            record("channel_page", f"miss {misses[-1]}; BACK")
            await press("KEYCODE_BACK")
        raise RuntimeError(f"channel page: no live session naming {query!r} ({'; '.join(misses)})")

    try:
        if entry is None:
            raise RuntimeError(f"unknown channel key {key!r}")
        query = query or entry.search or entry.name
        if not re.fullmatch(r"[A-Za-z0-9 .-]{2,60}", query):
            raise RuntimeError("query must be plain letters/digits")
        pkg = (getattr(stick, "packages", None) or {}).get("youtubetv") or stickio.yttv_package(stick)
        power = await stick.shell("dumpsys power | grep -m1 'mWakefulness='", timeout=4)
        if not power.ok or not re.search(r"mWakefulness=Awake\b", power.out):
            raise RuntimeError("stick is not Awake; wake it yourself (WAKEUP can flip the TV over CEC)")
        before = await stickio.read_evidence(stick, pkg, with_screen=False)
        frame = await open_search()
        if is_profile_screen(frame.text):
            frame = await clear_profile(frame)
            if is_profile_screen(frame.text):
                raise RuntimeError("profile picker is still showing after one selection")
            if not channel_tiles(frame, key, table, query):
                frame = await open_search()   # the picker can swallow the deep link: once more
        moved_down, last_focus, still = False, None, 0
        for _ in range(max_moves):
            focus = unique_focus(frame)
            here = focus_text(frame, focus)
            if focus is not None and names_channel(here, key, table) and not _purchase(here):
                again = await read()
                if not _same_focus(unique_focus(again), focus) or not names_channel(focus_text(again, focus), key, table):
                    raise RuntimeError("focused tile changed before selection")
                hunt.tile = focus_text(again, focus)
                await press("KEYCODE_DPAD_CENTER")
                break
            tiles = channel_tiles(frame, key, table, query)
            if not tiles:
                raise RuntimeError(f"no tile naming {entry.name} is visible in the search results")
            if focus is None:
                if channel_page:
                    break                          # channel_page_select takes it from here
                if moved_down:
                    raise RuntimeError("focus ring isn't readable on the results page")
                await press("KEYCODE_DPAD_DOWN")   # focus can start on the search box/keyboard
                moved_down = True
            else:
                still = still + 1 if last_focus == focus else 0
                if still >= 2:
                    raise RuntimeError("focus stopped moving toward the channel tile")
                last_focus = focus
                await press(step_toward(focus, tiles[0][1]))
            frame = await read()
        else:
            raise RuntimeError("channel tile not reached within the move limit")
        if channel_page and not hunt.tile:
            ev = await channel_page_select(before)
        else:
            ev = await wait_confirmed(before)
        if ev is None:
            raise RuntimeError("selected the tile, but the media session never named the channel")
        hunt.description = ev.meta_text
        hunt.status, hunt.reason = "confirmed", f"media session says {ev.meta_text!r}"
        if not collect_codes:
            return hunt   # tuning by search: the channel is on, no code hunt needed

        found: list[dict] = []

        def add(code, source):
            if code and stickio.CODE_RE.match(code) and all(c["code"] != code for c in found):
                found.append({"code": code, "source": source})

        if lounge is not None:
            try:
                add(await lounge(stick.host), "YouTube Lounge now-playing id")
            except Exception as exc:   # optional network source
                record("lounge", f"failed: {exc}")
        for code in ev.ids:
            add(code, "media session id")
        for label, cmd in stickio.CAPTURE_PROBES + EXTRA_CODE_PROBES:
            r = await stick.shell(cmd, timeout=10)
            out = r.out if r.ok else ""
            hunt.probes[label] = out[-20000:]
            codes = loose_codes_in(yttv_lines(out, pkg))
            if "logcat" in label:
                codes.reverse()            # newest mention first
            for code in codes:
                add(code, label)
        try:
            frame = await read()
            hunt.probes["screen (OCR)"] = frame.text
            for code in loose_codes_in(frame.text):
                add(code, "screen text")
        except RuntimeError as exc:
            record("read", f"post-play OCR skipped: {exc}")
        hunt.candidates = found
        return hunt
    except Exception as exc:   # a hunt never takes the caller down
        hunt.status, hunt.reason = "stopped", str(exc)
        return hunt
    finally:
        hunt.elapsed_s = round(clock() - started, 2)


async def prove_code(stick, *, key: str, code: str, table, decoy: str, sleep=asyncio.sleep,
                     clock=time.monotonic, seconds: float = 20.0) -> tuple[bool, str]:
    """Tune a different channel (decoy), then deep-link `code`: proven only if the media session
    moves from the decoy to `key`'s channel. No WAKEUP is sent. Returns (proven, detail)."""
    from redzone.discovery import stickio
    from redzone.discovery.yttv import judge

    entry = table.channels.get(key)
    if entry is None or not stickio.CODE_RE.match(code or "") or not stickio.CODE_RE.match(decoy or ""):
        return False, "bad channel or code"
    if code == decoy:
        return False, "decoy is the same code"
    pkg = (getattr(stick, "packages", None) or {}).get("youtubetv") or stickio.yttv_package(stick)

    async def tune(c, before=None):
        r = await stick.view(f"https://tv.youtube.com/watch/{c}", pkg)
        if not r.ok:
            return None, "deep link refused"
        t0 = clock()
        while clock() - t0 < seconds:
            await sleep(1.0)
            ev = await stickio.read_evidence(stick, pkg, with_screen=False)
            if before is None:
                # The decoy counts once the session names something that isn't the hunted channel.
                if ev.meta_text and ev.state in LOADED_STATES and not names_channel(ev.meta_text, key, table):
                    return ev, ""
                continue
            verdict = judge(entry, ev, table, code=code, before=before)
            if verdict.status == "verified" or names_now(ev, before, key, table):
                return ev, f"decoy {before.meta_text!r} -> {ev.meta_text!r} ({ev.state})"
            if verdict.status == "wrong_channel":
                return None, verdict.detail
        return None, "the code did not bring the channel back"

    first, why = await tune(decoy)
    if first is None:
        return False, f"decoy channel did not show up ({why or 'timeout'}), so a re-tune proves nothing"
    ev, why = await tune(code, before=first)
    return ev is not None, why


# A stick that isn't the TV's current input can pause its app (seen tonight): a paused session
# whose metadata changed to this channel still identifies it. Video frames stay unverified.
LOADED_STATES = ("playing", "paused", "buffering")


def names_now(ev, before, key: str, table) -> bool:
    """The media session changed since `before` and now names `key` (playing or paused)."""
    return bool(ev.read_ok and ev.state in LOADED_STATES and ev.meta_text
                and ev.meta_text != getattr(before, "meta_text", None)
                and names_channel(ev.meta_text, key, table))
