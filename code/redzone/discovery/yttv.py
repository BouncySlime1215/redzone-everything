"""YouTube TV channel codes with zero pasting: a researched default table, self-verification on a
Fire TV stick, and an in-app search fallback.

A YouTube TV channel is opened with https://tv.youtube.com/watch/<11-char code>. Codes are the same
for every account, but they change now and then and East/West feeds differ, so:
  1. yttv_channels.json ships every code we could find in public posts (with source + date), and
     says "unknown" where nobody published one (locals, NFL RedZone, NBCSN, Sunday Ticket 8/16).
  2. verify_codes() plays each candidate on a stick and reads what the stick reports (media
     session title/subtitle, screen text, optionally the app's own now-playing id) to decide
     whether it is really that channel. Results go to data/yttv_verified.json with timestamps.
     Failures are retried later with backoff; verified codes are re-checked weekly.
  3. search.py finds channels with no working code by searching inside the YouTube TV app.
  4. apply_verified() copies the results into the app's ChannelMap (data/channels.json).

Safe on a hidden stick (two-stick mode): only the stick you pass is touched, `may_run()` is asked
before every code so the app can take the stick back, an optional lock is held only while one code
is being checked, and every run is time-boxed and resumable (progress is saved after each code).

    data/venv/bin/python -m redzone.discovery.yttv --show
    data/venv/bin/python -m redzone.discovery.yttv --verify [--stick 192.168.1.42] [--search] [--apply]
    data/venv/bin/python -m redzone.discovery.yttv --probe  [--stick 192.168.1.42]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from redzone.discovery import stickio

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
TABLE_PATH = Path(__file__).resolve().with_name("yttv_channels.json")
STATE_PATH = ROOT / "data" / "yttv_verified.json"
DEVICES_PATH = ROOT / "data" / "devices.json"

REVERIFY_S = 7 * 86400                # "mostly stable, occasionally changes": re-check weekly
RETRY_S = (3600, 4 * 3600, 24 * 3600)  # failed channel: try again in 1h, 4h, then daily
DEAD_SKIP_S = 24 * 3600               # a dead code isn't re-tried for a day unless forced
PER_CODE_TIMEOUT_S = 20.0
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
FEED_RANK = {"national": 0, "east": 0, "unknown": 1, "west": 2}   # Raleigh-Durham is Eastern
OK_STATUSES = ("verified",)

LoungeFn = Callable[[str], Awaitable[str | None]]


# ============================================================================ table

@dataclass
class Candidate:
    code: str
    seen: str = ""
    confidence: str = "low"
    sources: list[str] = field(default_factory=list)
    feed: str = "unknown"
    note: str = ""


@dataclass
class Entry:
    key: str
    name: str
    kind: str = "national"            # national | local | sunday_ticket
    priority: int = 99
    aliases: list[str] = field(default_factory=list)
    search: str = ""
    browse_id: str | None = None
    carried: str = "yes"              # "yes" | "unlikely"
    slot: int | None = None
    note: str = ""
    candidates: list[Candidate] = field(default_factory=list)

    def all_aliases(self) -> list[str]:
        """Names to look for on screen. Sunday Ticket slots also answer to the generic name."""
        names = list(self.aliases) + [self.name]
        if self.kind == "sunday_ticket" and self.slot:
            names += [f"nfl sunday ticket {self.slot}", f"sunday ticket {self.slot}", f"ynfls{self.slot}",
                      "sunday ticket"]
        return names


@dataclass
class Table:
    channels: dict[str, Entry]
    sources: dict[str, dict]
    recipes: list[dict]
    updated: str = ""

    def code_owner(self, code: str) -> str | None:
        """Which channel the table lists this code under (None if unknown)."""
        return next((k for k, e in self.channels.items() if any(c.code == code for c in e.candidates)), None)

    def ordered(self) -> list[Entry]:
        return sorted(self.channels.values(), key=lambda e: (e.priority, e.slot or 0, e.key))


def _candidate(d: dict) -> Candidate | None:
    code = str(d.get("code") or "")
    if not stickio.CODE_RE.match(code):
        log.warning("yttv table: skipping malformed code %r", code)
        return None
    return Candidate(code, str(d.get("seen") or ""), str(d.get("confidence") or "low"),
                     list(d.get("sources") or []), str(d.get("feed") or "unknown"), str(d.get("note") or ""))


def _date_num(s: str) -> int:
    """'2025-04-15' -> 20250415 (0 if unparseable), for newest-first sorting."""
    digits = re.sub(r"\D", "", s or "")
    return int(digits[:8]) if len(digits) >= 8 else 0


def _entry(key: str, d: dict) -> Entry:
    cands = [c for c in (_candidate(x) for x in d.get("codes") or [] if isinstance(x, dict)) if c]
    # Best first: confidence, then East/national over West (Raleigh-Durham), then newest sighting.
    cands.sort(key=lambda c: (CONFIDENCE_RANK.get(c.confidence, 3), FEED_RANK.get(c.feed, 1), -_date_num(c.seen)))
    return Entry(key=key, name=str(d.get("name") or key), kind=str(d.get("kind") or "national"),
                 priority=int(d.get("priority") or 99), aliases=[str(a) for a in d.get("aliases") or []],
                 search=str(d.get("search") or d.get("name") or key), browse_id=d.get("browse_id"),
                 carried=str(d.get("carried") or "yes"), slot=d.get("slot"), note=str(d.get("note") or ""),
                 candidates=cands)


def load_table(path: Path = TABLE_PATH) -> Table:
    """Read the packaged code table. A broken file gives an empty table, never an exception."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        log.error("yttv table unreadable (%s): %s", path, e)
        return Table({}, {}, [])
    chans = {k: _entry(k, v) for k, v in (d.get("channels") or {}).items() if isinstance(v, dict)}
    return Table(chans, d.get("sources") or {}, list((d.get("search") or {}).get("recipes") or []),
                 str(d.get("updated") or ""))


# ============================================================================ matching

def norm(s: str) -> str:
    """Lowercase, punctuation to spaces: 'ESPN2' -> 'espn2', 'FOX-50' -> 'fox 50', 'truTV' -> 'trutv'."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def alias_score(text: str, aliases) -> tuple[int, str]:
    """Length of the longest alias found as whole words in text (longer = more specific)."""
    t = f" {norm(text)} "
    best, hit = 0, ""
    for a in aliases:
        a = norm(a)
        if a and f" {a} " in t and len(a) > best:
            best, hit = len(a), a
    return best, hit


@dataclass
class Verdict:
    status: str           # verified | plays | wrong_channel | dead | unknown
    channel: str = ""     # for wrong_channel: the channel the stick actually showed
    detail: str = ""


def _scores(text: str, table: Table) -> dict[str, tuple[int, str]]:
    return {k: alias_score(text, e.all_aliases()) for k, e in table.channels.items()}


def judge(entry: Entry, ev: stickio.Evidence, table: Table, *, code: str | None = None,
          before: stickio.Evidence | None = None) -> Verdict:
    """Is the stick showing `entry`? Conservative: a tie or a stale screen is only 'plays'."""
    if not ev.read_ok:
        return Verdict("unknown", "", "the stick didn't answer")       # never blame the code for that
    if not ev.playing:
        return Verdict("dead", "", ev.error or f"video never started ({ev.state})")
    scores = _scores(ev.text, table)
    mine, hit = scores.get(entry.key, (0, ""))
    top = max((s for s, _h in scores.values()), default=0)
    leaders = [k for k, (s, _h) in scores.items() if s == top and s > 0 and k != entry.key]
    # Sunday Ticket slots share the generic name "sunday ticket": tying with other slots is fine.
    only_st_ties = entry.kind == "sunday_ticket" and all(table.channels[k].kind == "sunday_ticket" for k in leaders)
    confirmed = bool(code and ev.lounge_id == code)
    stale = bool(before and before.meta_text and before.meta_text == ev.meta_text and not confirmed)
    if mine and mine == top and (not leaders or only_st_ties):
        if stale:
            return Verdict("plays", "", f"shows '{hit}', but the screen didn't change, so not proven")
        return Verdict("verified", entry.key, f"saw '{hit}'" + (" + app confirmed the id" if confirmed else ""))
    if top >= 3 and top > mine and len(leaders) == 1 and not stale:
        return Verdict("wrong_channel", leaders[0], f"shows '{scores[leaders[0]][1]}' instead")
    why = "the screen didn't change" if stale else ("it matches several channels" if len(leaders) > 1
                                                     else "the channel name wasn't shown")
    return Verdict("plays", "", f"video plays but {why}" + (" (app confirmed the id)" if confirmed else ""))


# ============================================================================ state

class VerifyState:
    """What verification has learned (data/yttv_verified.json). Written atomically after each code."""

    def __init__(self, path: Path = STATE_PATH):
        self.path = Path(path)
        self.channels: dict[str, dict] = {}   # key -> {code, status, at, detail, how, fails, next_try}
        self.codes: dict[str, dict] = {}      # code -> {status, channel, at, detail}
        self.recipes: dict[str, dict] = {}    # key -> working search recipe when no code was found
        self.meta: dict = {}                  # url_style, lounge, last_run
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        self.channels = dict(d.get("channels") or {})
        self.codes = dict(d.get("codes") or {})
        self.recipes = dict(d.get("recipes") or {})
        self.meta = dict(d.get("meta") or {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "channels": self.channels, "codes": self.codes,
                                       "recipes": self.recipes, "meta": self.meta, "saved": time.time()},
                                      indent=1, sort_keys=True))
            tmp.replace(self.path)
        except OSError as e:   # a full disk must not stop the app
            log.warning("couldn't save %s: %s", self.path, e)

    @property
    def url_style(self) -> str:
        return self.meta.get("url_style", "path")

    def code_for(self, key: str) -> str | None:
        rec = self.channels.get(key) or {}
        return rec.get("code") if rec.get("status") in OK_STATUSES else None

    def record_code(self, code: str, key: str, verdict: Verdict, now: float, evidence: str = "") -> None:
        self.codes[code] = {"status": verdict.status, "channel": verdict.channel or key, "tested_for": key,
                            "at": now, "detail": verdict.detail, "evidence": evidence[:160]}

    def set_channel(self, key: str, code: str | None, status: str, now: float, detail: str = "",
                    how: str = "") -> None:
        rec = self.channels.setdefault(key, {})
        rec.update({"code": code, "status": status, "at": now, "detail": detail, "how": how or rec.get("how", "")})
        if status in OK_STATUSES:
            rec.update({"fails": 0, "next_try": 0})

    def fail_channel(self, key: str, now: float, detail: str) -> None:
        """Every candidate was tried and none plays this channel: forget the code, back off."""
        rec = self.channels.setdefault(key, {})
        fails = int(rec.get("fails") or 0) + 1
        rec.update({"code": None, "status": "failed", "at": now, "detail": detail, "fails": fails,
                    "next_try": now + RETRY_S[min(fails, len(RETRY_S)) - 1]})
        rec.pop("suspect", None)

    def due(self, key: str, now: float, force: bool = False) -> bool:
        rec = self.channels.get(key) or {}
        if force or rec.get("suspect"):
            return True
        if rec.get("status") == "verified" and now - float(rec.get("at") or 0) < REVERIFY_S:
            return False
        return now >= float(rec.get("next_try") or 0)

    def code_recently_dead(self, code: str, now: float) -> bool:
        rec = self.codes.get(code) or {}
        return rec.get("status") == "dead" and now - float(rec.get("at") or 0) < DEAD_SKIP_S


def note_tune_failure(code: str | None, path: Path = STATE_PATH, now: float | None = None) -> bool:
    """The app failed to tune this code in real use: flag its channel so the next verification
    re-checks it first. Returns True if a channel was flagged. Never raises."""
    try:
        if not code:
            return False
        st = VerifyState(path)
        hit = False
        for rec in st.channels.values():
            if rec.get("code") == code:
                rec["suspect"] = now or time.time()
                hit = True
        if hit:
            st.save()
        return hit
    except Exception as e:
        log.debug("note_tune_failure failed: %s", e)
        return False


# ============================================================================ verification

@dataclass
class CheckResult:
    code: str
    verdict: Verdict
    evidence: stickio.Evidence
    steps: list[str]
    style: str


@dataclass
class Outcome:
    key: str
    name: str
    status: str               # verified | plays | failed | no_code | skipped
    code: str | None = None
    detail: str = ""


@dataclass
class VerifyReport:
    outcomes: list[Outcome] = field(default_factory=list)
    checked: int = 0
    stopped: str = ""         # "" | "time" | "stick needed" | "stick offline"
    style_probed: bool = False

    def lines(self) -> list[str]:
        """Plain-English summary, one line per channel."""
        words = {"verified": "works", "plays": "unconfirmed (video plays, channel identity unknown)",
                 "failed": "no working code yet - will retry", "no_code": "no known code - search can find it",
                 "skipped": "skipped", "search_only": "reachable through in-app search (its code couldn't be read)",
                 "not_found": "in-app search didn't land on it"}
        out = [f"{o.name}: {words.get(o.status, o.status)}" + (f" [{o.code}]" if o.code else "")
               + (f" - {o.detail}" if o.detail else "") for o in self.outcomes]
        if self.stopped:
            out.append(f"Stopped early ({self.stopped}); run again to continue where it left off.")
        return out


async def check_code(stick, entry: Entry, code: str, table: Table, *, style: str = "path",
                     lounge: LoungeFn | None = None, timeout: float = PER_CODE_TIMEOUT_S,
                     sleep=asyncio.sleep) -> CheckResult:
    """Play one code on the stick and judge what shows up."""
    pkg = stickio.yttv_package(stick)
    before = await stickio.read_evidence(stick, pkg, with_screen=False)
    await stickio.stop_playback(stick, pkg, sleep=sleep)
    _ok, steps = await stickio.tune(stick, code, pkg, style=style, timeout=timeout, sleep=sleep)
    # YouTube TV first reports PLAYING at speed 0 while the live stream loads, then speed 1 a second
    # or two later: one read right after the tune marked every working channel "dead".
    ev = await stickio.read_evidence(stick, pkg, with_screen=False)
    for _ in range(8):
        if ev.playing and ev.meta_text and ev.meta_text != before.meta_text:
            break
        await sleep(1.0)
        ev = await stickio.read_evidence(stick, pkg, with_screen=False)
    if not ev.meta_text:
        ev = await stickio.read_evidence(stick, pkg)
    if lounge and ev.playing:
        ev.lounge_id = await safe_lounge(lounge, stick)
    return CheckResult(code, judge(entry, ev, table, code=code, before=before), ev, steps, style)


async def safe_lounge(lounge: LoungeFn, stick) -> str | None:
    try:
        return await lounge(stick.host)
    except Exception as e:
        log.debug("lounge failed: %s", e)
        return None


def _plan(table: Table, state: VerifyState, keys, force: bool, now: float) -> list[Entry]:
    entries = [e for e in table.ordered() if not keys or e.key in keys]
    return [e for e in entries if state.due(e.key, now, force)]


def _candidates(entry: Entry, state: VerifyState) -> list[str]:
    """Codes to try for a channel: the last one that worked first, then the table's, best first."""
    codes = [c.code for c in entry.candidates]
    last = (state.channels.get(entry.key) or {}).get("code")
    if last:
        codes = [last] + [c for c in codes if c != last]
    return codes


def _learn_other(state: VerifyState, table: Table, res: CheckResult, now: float) -> None:
    """A code showed a different channel: if that channel has nothing proven yet, now it does."""
    other = res.verdict.channel
    if other in table.channels and state.channels.get(other, {}).get("status") != "verified":
        state.set_channel(other, res.code, "verified", now, f"seen while checking another channel: "
                          f"{res.verdict.detail}", how="deep link (found by accident)")


class _Stop(Exception):
    pass


async def _check_one(stick, entry, code, table, state, report, *, lounge, lock, timeout, sleep, guard) -> CheckResult:
    async with (lock or contextlib.nullcontext()):
        guard()  # ownership may have changed while we waited for the controller
        res = await check_code(stick, entry, code, table, style=state.url_style, lounge=lounge,
                               timeout=timeout, sleep=sleep)
        if res.verdict.status == "dead" and not report.style_probed and res.evidence.read_ok:
            report.style_probed = True       # once per run: does this stick want ?v= links instead?
            alt = "query" if state.url_style == "path" else "path"
            res2 = await check_code(stick, entry, code, table, style=alt, lounge=lounge, timeout=timeout,
                                    sleep=sleep)
            if res2.verdict.status != "dead":
                state.meta["url_style"] = alt
                res = res2
    report.checked += 1
    return res


def _verified(state: VerifyState, report: VerifyReport, entry: Entry, code: str, detail: str, now: float) -> None:
    state.set_channel(entry.key, code, "verified", now, detail, how="deep link")
    state.channels[entry.key].pop("suspect", None)
    report.outcomes.append(Outcome(entry.key, entry.name, "verified", code, detail))
    state.save()


async def _verify_entry(stick, entry, table, state, report, ctx) -> None:
    now = ctx["now"]
    was = state.code_for(entry.key) if (state.channels.get(entry.key) or {}).get("status") == "verified" else None
    codes = [c for c in _candidates(entry, state) if ctx["force"] or not state.code_recently_dead(c, now())]
    if not codes:
        report.outcomes.append(Outcome(entry.key, entry.name, "skipped", None, "its known codes failed in the last day"))
        return
    best_plays: CheckResult | None = None
    offline = 0
    for code in codes:
        ctx["guard"]()
        res = await _check_one(stick, entry, code, table, state, report, lounge=ctx["lounge"],
                               lock=ctx["lock"], timeout=ctx["timeout"], sleep=ctx["sleep"], guard=ctx["guard"])
        if res.verdict.status == "unknown":       # the stick, not the code, is the problem
            offline += 1
            if offline >= 2:
                raise _Stop("stick offline")
            continue
        state.record_code(code, entry.key, res.verdict, now(), res.evidence.summary())
        ctx["progress"](f"  {entry.name} {code}: {res.verdict.status} - {res.verdict.detail}")
        if res.verdict.status == "verified":
            _verified(state, report, entry, code, res.verdict.detail, now())
            return
        if res.verdict.status == "wrong_channel":
            _learn_other(state, table, res, now())
        if res.verdict.status == "plays" and best_plays is None:
            best_plays = res
        state.save()
    if best_plays:
        state.set_channel(entry.key, best_plays.code, "plays", now(), best_plays.verdict.detail, how="deep link")
        report.outcomes.append(Outcome(entry.key, entry.name, "plays", best_plays.code, best_plays.verdict.detail))
    else:
        state.fail_channel(entry.key, now(), "none of the known codes played this channel")
        report.outcomes.append(Outcome(entry.key, entry.name, "failed", None, "none of the known codes worked"))
    state.save()


async def verify_codes(stick, table: Table | None = None, state: VerifyState | None = None, *,
                       keys: list[str] | None = None, budget_s: float = 900.0,
                       may_run: Callable[[], bool] | None = None, lock: asyncio.Lock | None = None,
                       lounge: LoungeFn | None = None, force: bool = False,
                       on_progress: Callable[[str], None] | None = None,
                       now: Callable[[], float] = time.time, sleep=asyncio.sleep,
                       timeout: float = PER_CODE_TIMEOUT_S) -> VerifyReport:
    """Play candidate codes on `stick` and keep the ones that show the right channel.

    Time-boxed (`budget_s`), resumable (state saved after every code; finished channels are skipped
    next time), and polite in two-stick mode: `may_run()` is asked before every code and `lock` is
    held only while one code is checked. Never raises; the report says why it stopped early.
    """
    table = table or load_table()
    state = state or VerifyState()
    report = VerifyReport()
    t0 = time.monotonic()

    def guard() -> None:
        if time.monotonic() - t0 > budget_s:
            raise _Stop("time")
        if may_run is not None and not may_run():
            raise _Stop("stick needed")

    ctx = {"now": now, "force": force, "guard": guard, "lounge": lounge, "lock": lock, "timeout": timeout,
           "sleep": sleep, "progress": on_progress or (lambda _m: None)}
    try:
        for entry in _plan(table, state, keys, force, now()):
            if not _candidates(entry, state):
                report.outcomes.append(Outcome(entry.key, entry.name, "no_code", None,
                                               entry.note or "nobody has published its code"))
                continue
            await _verify_entry(stick, entry, table, state, report, ctx)
    except _Stop as why:
        report.stopped = str(why)
    except Exception as e:   # a bug here must never take the app down
        log.exception("verify_codes crashed")
        report.stopped = f"error: {e}"
    state.meta["last_run"] = {"at": now(), "checked": report.checked, "stopped": report.stopped}
    state.save()
    return report


# ============================================================================ apply to the app

def sunday_ticket_codes(table: Table, state: VerifyState, include_defaults: bool = False) -> list[str]:
    """Verified slot codes in slot order; published candidates require explicit opt-in."""
    out = []
    for e in sorted((e for e in table.channels.values() if e.kind == "sunday_ticket"), key=lambda e: e.slot or 0):
        code = state.code_for(e.key) or (next(
            (c.code for c in e.candidates if (state.codes.get(c.code) or {}).get("status") not in ("dead", "wrong_channel")),
            None) if include_defaults else None)
        if code and code not in out:
            out.append(code)
    return out


def _known_codes(table: Table, state: VerifyState) -> set[str]:
    return {c.code for e in table.channels.values() for c in e.candidates} | set(state.codes)


def apply_verified(cm, state: VerifyState | None = None, table: Table | None = None, *,
                   include_defaults: bool = False, save: bool = True) -> list[str]:
    """Copy proven codes into a ChannelMap. Returns plain-English changes.

    - Only verified codes are copied. Unrecognized owner-entered codes are preserved.
    - include_defaults: also fill empty slots with the table's high-confidence codes (unproven).
    - Sunday Ticket slot codes go to cm.st_codes unless that list holds codes we've never heard of
      (someone set it by hand on purpose).
    """
    table = table or load_table()
    state = state or VerifyState()
    changes: list[str] = []
    for e in table.ordered():
        if e.kind == "sunday_ticket":
            continue
        rec = state.channels.get(e.key) or {}
        code, status = rec.get("code"), rec.get("status")
        cur = cm.codes.get(e.key)
        if cur and cur not in _known_codes(table, state):
            continue
        cur_status = (state.codes.get(cur) or {}).get("status") if cur else None
        if status == "verified" and code and cur != code and cur_status != "verified":
            cm.codes[e.key] = code
            changes.append(f"{e.name}: using {code} (checked on the stick)")
        elif include_defaults and not cur and e.candidates and e.candidates[0].confidence == "high":
            if (state.codes.get(e.candidates[0].code) or {}).get("status") not in ("dead", "wrong_channel"):
                cm.codes[e.key] = e.candidates[0].code
                changes.append(f"{e.name}: using {e.candidates[0].code} (published, not yet checked)")
    st = sunday_ticket_codes(table, state, include_defaults=include_defaults)
    if st and st != list(cm.st_codes) and set(cm.st_codes) <= _known_codes(table, state):
        cm.st_codes = st
        changes.append(f"Sunday Ticket: {len(st)} slot channels set")
    if changes and save:
        cm.save()
    return changes


# ============================================================================ Apple TV check

def youtube_links(links) -> list[dict]:
    """Event-link rows (eventlinks.fetch_event_links) that point at YouTube / YouTube TV.
    As of 2026-11-02 Apple lists none for NFL games (only NFL+, Prime Video, ESPN, NBC/Peacock),
    so Sunday Ticket can't be mapped this way; kept so a re-check is one command."""
    out = []
    for it in links or []:
        blob = " ".join(str(it.get(k) or "") for k in ("provider", "url", "raw", "network", "title"))
        if "youtube" in blob.lower():
            m = stickio.WATCH_RE.search(blob)
            out.append({**it, "yttv_code": m.group(1) if m else None})
    return out


async def check_apple(date: str) -> list[dict]:
    import httpx

    from redzone.sources import eventlinks
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return youtube_links(await eventlinks.fetch_event_links(client, date))


# ============================================================================ CLI

def _fmt_when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "-"


def show(table: Table, state: VerifyState) -> list[str]:
    """Table + verification status, one line per channel."""
    lines = [f"YouTube TV channels (table updated {table.updated or '?'}; results in {state.path})",
             f"{'key':11} {'channel':24} {'status':10} {'code':12} {'checked':16} candidates"]
    for e in table.ordered():
        rec = state.channels.get(e.key) or {}
        status = rec.get("status") or ("untested" if e.candidates else "unknown")
        code = rec.get("code") or (e.candidates[0].code if e.candidates else "-")
        cands = ", ".join(f"{c.code}({c.confidence[0]},{c.seen[:7]})" for c in e.candidates) or "none published"
        lines.append(f"{e.key:11} {e.name[:24]:24} {status:10} {code:12} {_fmt_when(rec.get('at')):16} {cands}")
    if state.recipes:
        lines.append("Reachable by search only: " + ", ".join(sorted(state.recipes)))
    return lines


def _load_sticks(only: str | None):
    from redzone.tuners.adb import Stick
    sticks = []
    try:
        d = json.loads(DEVICES_PATH.read_text()) if DEVICES_PATH.exists() else {}
    except ValueError:
        d = {}
    for s in d.get("sticks", []):
        st = Stick(s["host"], name=s.get("name", ""), model=s.get("model", ""), os=s.get("os", ""))
        st.packages = s.get("packages", {})
        sticks.append(st)
    if only:
        host = only if ":" in only else f"{only}:5555"
        sticks = [s for s in sticks if s.host == host] or [Stick(host)]
    return sticks


async def _connect(only: str | None):
    from redzone.tuners.adb import adb_available
    if not await adb_available():
        print("adb is missing - run: brew install --cask android-platform-tools")
        return None
    for st in _load_sticks(only):
        if await st.connect():
            if not st.packages.get("youtubetv"):
                await st.identify()
            if "Vega" in (st.os or ""):
                continue
            return st
        print(f"Couldn't reach {st.host} ({st.state}). On the stick: Developer Options -> ADB debugging ON.")
    if not only:
        print("No stick found. Run scripts/doctor.py, or pass --stick <ip>.")
    return None


async def _probe(stick, use_lounge: bool) -> None:
    from redzone.discovery import lounge
    ev = await stickio.read_evidence(stick)
    print(f"stick {stick.host}  YouTube TV package: {stickio.yttv_package(stick)}")
    print(f"  playing: {ev.playing} ({ev.state})  title: {ev.title!r}  subtitle: {ev.subtitle!r}  "
          f"description: {ev.description!r}")
    print(f"  screen text: {ev.screen[:300]!r}")
    print(f"  ids in media session: {ev.ids or 'none'}")
    print(f"  codes the stick leaks: {await stickio.capture_codes(stick) or 'none'}")
    if use_lounge:
        print(f"  YouTube Lounge now-playing id: {await lounge.now_playing(stick.host) or 'not available'}")


async def _run_cli(a) -> int:
    from redzone.discovery import lounge as lounge_mod
    table, state = load_table(), VerifyState()
    if a.check_apple:
        rows = await check_apple(a.check_apple)
        print(f"Apple TV lists {len(rows)} YouTube / YouTube TV links for {a.check_apple}.")
        for r in rows:
            print(f"  {r.get('league')} {r.get('away')} @ {r.get('home')}: {r.get('url')} code={r.get('yttv_code')}")
        return 0
    if a.verify or a.search or a.probe:
        stick = await _connect(a.stick)
        if stick is None:
            return 2
        lounge_fn = None if a.no_lounge else lounge_mod.now_playing
        if a.probe:
            await _probe(stick, not a.no_lounge)
            return 0
        keys = [k.strip() for k in a.channels.split(",")] if a.channels else None
        print(f"Using {stick.name or stick.host}. If this stick is on screen you'll see channels flip.")
        if a.verify:
            rep = await verify_codes(stick, table, state, keys=keys, budget_s=a.budget_min * 60,
                                     lounge=lounge_fn, force=a.force, on_progress=print)
            print("\n".join(["", "Verification:"] + rep.lines()))
        if a.search:
            from redzone.discovery import search
            rep2 = await search.search_missing(stick, table, state, keys=keys, lounge=lounge_fn,
                                               budget_s=a.budget_min * 60, on_progress=print)
            print("\n".join(["", "Search:"] + rep2.lines()))
    if a.apply:
        from redzone.channels import ChannelMap
        changes = apply_verified(ChannelMap(), state, table, include_defaults=a.defaults)
        print("\n".join(["", "Saved to data/channels.json:"] + (changes or ["nothing new"])))
    if a.show or not (a.verify or a.search or a.apply):
        print("\n".join(show(table, state)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m redzone.discovery.yttv",
                                 description="YouTube TV channel codes: show, verify on a stick, search, apply.")
    ap.add_argument("--show", action="store_true", help="print the table and what's been verified")
    ap.add_argument("--verify", action="store_true", help="play candidate codes on a stick and check them")
    ap.add_argument("--search", action="store_true", help="find channels with no working code by in-app search")
    ap.add_argument("--probe", action="store_true", help="print what the stick reports is playing now")
    ap.add_argument("--apply", action="store_true", help="write proven codes into data/channels.json")
    ap.add_argument("--defaults", action="store_true", help="with --apply: also fill gaps with published codes")
    ap.add_argument("--stick", help="stick IP (default: first stick in data/devices.json)")
    ap.add_argument("--channels", help="comma-separated keys, e.g. espn,fs1,st1")
    ap.add_argument("--budget-min", type=float, default=15.0, help="stop after this many minutes (resumable)")
    ap.add_argument("--force", action="store_true", help="re-check even recently verified channels")
    ap.add_argument("--no-lounge", action="store_true", help="don't ask the app for its now-playing id")
    ap.add_argument("--check-apple", metavar="YYYY-MM-DD", help="does Apple TV list YouTube TV links that day?")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    return asyncio.run(_run_cli(a))


if __name__ == "__main__":
    sys.exit(main())
