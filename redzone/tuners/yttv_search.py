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
