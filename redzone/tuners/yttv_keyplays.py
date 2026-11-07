"""[YTTV key plays, item 8] Catch up on a missed play with YouTube TV's own Key plays reel.

Measured (overseer, Nov 6-7, real stick; not yet seen by this code):
  * UP then CENTER opens a panel over the player with tabs Scores, Stats, Key plays. Key plays has a
    'Catch up via key plays' button and a list of plays with the game clock (filters by team and player).
  * The reel plays about 10 s per play and shows 'N of M key plays'. A deep link returns to live in ~0.4 s.
  * The first use asks 'Auto-play key plays?'. We ALWAYS answer Ask later and never change that setting:
    CENTER is pressed on the prompt only while the focus box reads 'Ask later'. Anything else: no CENTER.
  * After a tune a LIVE/title banner can sit up 25+ s and keys do nothing there (#377): an open that shows no
    panel is retried after a wait, a panel we can't read fails closed.
  * Nov 7 live: the panel opens with a card focused and OCR can't see focus on the tabs, so the Key plays tab is
    recognised by its CONTENT. Three states: 'Key plays currently unavailable' (ESPN) = fail closed; spoiler mode
    'Catch up via key plays' + 'N hidden key plays', no rows (Big Ten Network) = only Want(from_start=True), the reel
    from 1 of N; rows with the game clock (ACC Network) = pick by period + clock.
  * CATCH-UP, NOT A LIVE JUMP: the newest row was ~12 min behind live on ESPN (one read, Nov 7). The reel is old
    footage; the caller deep-links back to live afterwards and never treats it as the live picture.

What it does (each call never raises; it returns a KeyPlaysResult with steps and timings):
  read_key_plays(stick, ...)   open the panel, reach the Key plays tab, read the rows, return to live
  catch_up(stick, want, ...)   open, reach the tab, find the row whose period + clock match `want`, focus it,
                               CENTER, answer the prompt with Ask later if it shows, confirm it plays
  return_to_live(stick, relink, ...)   the caller-supplied deep link (tuners.YouTubeTVTuner), timed

Safety (same model as yttv_seek.enter_buffer, #377):
  * Flag yttv_keyplays.enabled ships OFF: off = nothing is pressed or read.
  * Keys only go to a stick `still_hidden()` says is hidden, re-checked before every key batch; YouTube TV
    must be in front and playing before the first key. Rule #1 is unchanged: the caller still flips only at a
    dead ball, and a replay is never used as a live picture.
  * Every press after the first is decided from a fresh screen read (focus box + text). Unknown focus = no
    CENTER. "Fail closed" = no selection; the hidden stick is re-linked to live by `relink()` and the result
    says why. A stick that became visible is never pressed again (and not re-linked: that would change the screen).
  * Screen reads: one screencap into a 0700 temp dir, read by the Mac's local Vision OCR, the PNG deleted at once.
    Only text and numbers leave this module. YouTube TV video is DRM-black in screencaps; only its UI is read.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("redzone.yttv_keyplays")

YTTV_PKG_HINT = "youtube.tv"
KEYPLAYS_DEFAULTS = {"enabled": False, "open_tries": 3, "open_wait_s": 1.2, "reopen_wait_s": 6.0, "read_wait_s": 0.9,
            "tab_tries": 4, "row_fix_tries": 3, "scroll_max": 20, "prompt_tries": 6, "clock_tol_s": 20.0,
            "settle_s": 6.0, "poll_s": 0.25, "relink_settle_s": 6.0, "key_gap_s": 0.35,
                     "fast_tabs": True, "fast_tab_reads": 2, "hidden_center_by_position": True}

TABS = ("scores", "stats", "key plays")
CATCH_UP = "catch up via key plays"
ASK_LATER = "ask later"
# Filled-button labels screen_ocr may report as focus for this module (it drops unknown labels otherwise).
FOCUS_LABELS = (r"(?:Ask later|Not now|No thanks|Turn on|Yes|No|On|Off|Scores|Stats|Key plays"
                r"|Catch up via key plays|.{0,80}\b\d{1,2}:\d{2}\b.{0,200})")

_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])(?!\s*[AaPp]\.?[Mm])")
_PERIOD = re.compile(r"\b(?:Q([1-4])|([1-4])(?:st|nd|rd|th)(?:\s+(?:qtr|quarter|half|period))?|(OT)(\d?)|([12])H)\b",
                     re.IGNORECASE)
_REEL = re.compile(r"(\d{1,3})\s+of\s+(\d{1,3})\s+key\s+plays", re.IGNORECASE)
_PROMPT = re.compile(r"auto[- ]?play\s+key\s+plays", re.IGNORECASE)
# Key plays tab states seen live (Nov 7): ESPN 'Key plays currently unavailable, Try again later'; Big Ten Network
# spoiler mode 'Catch up via key plays' + '8 hidden key plays' with no rows; ACC Network rows with the game clock.
_KP_UNAVAILABLE = re.compile(r"key\s+plays\s+(?:are\s+)?(?:currently\s+)?(?:not\s+available|unavailable)", re.IGNORECASE)
_KP_HIDDEN = re.compile(r"(\d{1,3})\s+hidden\s+key\s+plays", re.IGNORECASE)

Reader = Callable[[Any], Awaitable["PanelRead | None"]]


def _cfg(cfg, key: str):
    v = cfg.get(f"yttv_keyplays.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return KEYPLAYS_DEFAULTS[key] if v is None else v


def enabled(cfg) -> bool:
    return bool(_cfg(cfg, "enabled"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'")).strip().lower()


def parse_clock(text: str) -> tuple[int | None, float | None]:
    """(period, seconds left on the game clock) from a row's text: 'Q4 2:31', '2:31 4th', '3rd Qtr 0:45',
    'OT 1:02' (period 5), 'OT2' (6), '2H 11:20' (2). Clock times of day ('9:00 PM') are not clocks."""
    p = None
    m = _PERIOD.search(text or "")
    if m:
        q, nth, ot, otn, half = m.groups()
        if q or nth:
            p = int(q or nth)
        elif ot:
            p = 4 + (int(otn) if otn else 1)
        elif half:
            p = int(half)
    c = _CLOCK.search(text or "")
    secs = None
    if c and int(c.group(2)) <= 59:
        secs = float(int(c.group(1)) * 60 + int(c.group(2)))
    return p, secs


@dataclass
class KeyPlay:
    index: int                 # top to bottom as read on this screen
    period: int | None
    clock_s: float | None
    clock_text: str
    text: str                  # the row's description (public UI text, kept short)
    y0: float = 0.0
    y1: float = 0.0

    def brief(self) -> dict:
        return {"i": self.index, "period": self.period, "clock_s": self.clock_s, "clock": self.clock_text,
                "text": self.text[:80]}


@dataclass
class PanelRead:
    """What one screen read says (text and numbers only)."""
    tabs: list[str] = field(default_factory=list)
    tabs_y: float | None = None
    rows: list[KeyPlay] = field(default_factory=list)
    catch_up: bool = False
    prompt: bool = False
    prompt_buttons: list[str] = field(default_factory=list)
    reel: tuple[int, int] | None = None
    focus_text: str = ""
    focus_kind: str = ""       # "" none/ambiguous | tab | row | prompt | catch_up | other
    focus_row: int | None = None
    n_text: int = 0
    unavailable: bool = False  # 'Key plays currently unavailable' (ESPN, Nov 7)
    hidden_n: int | None = None  # 'N hidden key plays' (spoiler mode, Big Ten Network, Nov 7): no rows to pick
    catch_up_y: float | None = None
    hidden_y: float | None = None

    @property
    def panel(self) -> bool:
        return "key plays" in self.tabs and len(self.tabs) >= 2

    @property
    def mode(self) -> str:
        """What the Key plays tab shows, from its CONTENT (OCR can't see focus on the tabs, Nov 7):
        unavailable | hidden (spoiler mode: only Catch up) | rows | "" (not the Key plays tab, or unreadable).
        Rows count only next to a key-plays-only sign (Catch up button, or the focused Key plays tab): the Scores
        tab carries game clocks too."""
        if not self.panel:
            return ""
        if self.unavailable:
            return "unavailable"
        if self.hidden_n is not None and self.catch_up:
            return "hidden"
        if self.rows and (self.catch_up or (self.focus_kind == "tab" and self.focus_text == "key plays")):
            return "rows"
        return ""

    @property
    def on_key_plays(self) -> bool:
        return self.mode != ""

    def brief(self) -> dict:
        return {"panel": self.panel, "tabs": self.tabs, "rows": len(self.rows), "catch_up": self.catch_up,
                "prompt": self.prompt, "reel": self.reel, "focus": self.focus_text[:60], "kind": self.focus_kind,
                "focus_row": self.focus_row, "n_text": self.n_text, "mode": self.mode, "hidden_n": self.hidden_n}


def parse_panel(obs: list[dict], focus: list[dict] | None = None) -> PanelRead:
    """The panel from OCR rows ({text, x, y, w, h}, normalized top-left). Rows are lines below the tab bar that
    carry a game clock; the lines under a clock line (until the next one) are its description."""
    r = PanelRead()
    items = sorted(({"text": str(o.get("text", "")).strip(), "x": float(o.get("x", 0)), "y": float(o.get("y", 0)),
                     "h": float(o.get("h", 0.02) or 0.02)} for o in (obs or []) if str(o.get("text", "")).strip()),
                   key=lambda o: (o["y"], o["x"]))
    r.n_text = len(items)
    for o in items:
        t = _norm(o["text"])
        if t in TABS and t not in r.tabs:
            r.tabs.append(t)
            r.tabs_y = o["y"] if r.tabs_y is None else min(r.tabs_y, o["y"])
        if CATCH_UP in t:
            r.catch_up = True
            r.catch_up_y = o["y"] if r.catch_up_y is None else min(r.catch_up_y, o["y"])
        if _PROMPT.search(o["text"]):
            r.prompt = True
        m = _REEL.search(o["text"])
        if m:
            r.reel = (int(m.group(1)), int(m.group(2)))
        if _KP_UNAVAILABLE.search(o["text"]):
            r.unavailable = True
        h = _KP_HIDDEN.search(o["text"])
        if h:
            r.hidden_n = int(h.group(1))
            r.hidden_y = o["y"]
    if r.prompt:
        r.prompt_buttons = [_norm(o["text"]) for o in items
                            if _norm(o["text"]) in (ASK_LATER, "not now", "no thanks", "turn on", "yes", "no", "on", "off")]
    # lines (same y within half a text height)
    lines: list[list[dict]] = []
    for o in items:
        if lines and abs(lines[-1][0]["y"] - o["y"]) < max(0.012, 0.5 * o["h"]):
            lines[-1].append(o)
        else:
            lines.append([o])
    top = (r.tabs_y + 0.03) if r.tabs_y is not None else 0.0
    cur: KeyPlay | None = None
    for ln in lines:
        y = ln[0]["y"]
        text = " ".join(o["text"] for o in sorted(ln, key=lambda o: o["x"]))
        n = _norm(text)
        if y < top or not r.panel or _REEL.search(text) or CATCH_UP in n or n in TABS:
            cur = None
            continue
        p, secs = parse_clock(text)
        if secs is not None:
            c = _CLOCK.search(text)
            desc = (text[:c.start()] + text[c.end():]).strip(" -·•|")
            desc = _PERIOD.sub("", desc).strip(" -·•|")
            cur = KeyPlay(len(r.rows), p, secs, text[:24], desc, y, y + max(o["h"] for o in ln))
            r.rows.append(cur)
        elif cur is not None and y - cur.y1 < 0.08:
            cur.text = (cur.text + " " + text).strip()[:200]
            cur.y1 = y + max(o["h"] for o in ln)
    # focus: only a single, unambiguous box counts
    if focus and len(focus) == 1:
        f = focus[0]
        ft = _norm(str(f.get("text", "")))
        r.focus_text = ft
        fy0, fy1 = float(f.get("y", 0)), float(f.get("y", 0)) + float(f.get("h", 0))
        if ft in TABS:
            r.focus_kind = "tab"
        elif r.prompt and ft in (ASK_LATER, "not now", "no thanks", "turn on", "yes", "no", "on", "off"):
            r.focus_kind = "prompt"
        elif CATCH_UP in ft:
            r.focus_kind = "catch_up"
        else:
            hits = [k for k in r.rows if k.y0 < fy1 and k.y1 > fy0]
            if len(hits) == 1:
                r.focus_kind, r.focus_row = "row", hits[0].index
            else:
                r.focus_kind = "other"
    return r


async def ocr_panel(stick, timeout_s: float = 8.0) -> PanelRead | None:
    """Default reader: one screencap -> local Vision OCR -> PNG deleted at once (Nick's rule). None = unreadable."""
    from redzone.screen_ocr import recognize
    d = tempfile.mkdtemp(prefix="rz-kp-")
    os.chmod(d, 0o700)
    path = os.path.join(d, "s.png")
    try:
        try:
            if not await stick.screenshot(path):
                return None
        except asyncio.TimeoutError:
            return None
        res = await asyncio.to_thread(recognize, path, timeout=timeout_s, focus_labels=FOCUS_LABELS)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    if not res.read_ok:
        return None
    obs = [{"text": b.text, "x": b.x, "y": b.y, "w": b.width, "h": b.height} for b in res.observations]
    focus = [{"text": f.text, "x": f.x, "y": f.y, "w": f.width, "h": f.height} for f in res.focus_boxes]
    return parse_panel(obs, focus)


def match_play(rows: list[KeyPlay], period: int | None, clock_s: float, tol_s: float = 20.0) -> KeyPlay | None:
    """The row for a missed play: same period (when both know it), nearest clock within tol_s. A tie = None."""
    cands = [k for k in rows if k.clock_s is not None and (period is None or k.period is None or k.period == period)
             and abs(k.clock_s - clock_s) <= tol_s]
    if not cands:
        return None
    cands.sort(key=lambda k: abs(k.clock_s - clock_s))
    if len(cands) > 1 and abs(cands[0].clock_s - clock_s) == abs(cands[1].clock_s - clock_s) \
            and cands[0].clock_s != cands[1].clock_s:
        return None
    return cands[0]


@dataclass
class Want:
    period: int | None = None
    clock_s: float | None = None
    index: int | None = None   # or a row number as read (probe use)
    from_start: bool = False   # select 'Catch up via key plays' (the reel from 1 of N); the only pick in spoiler mode


@dataclass
class KeyPlaysResult:
    ok: bool = False
    stage: str = "start"       # open | tab | list | find | focus | play | prompt | playing | live
    reason: str = ""
    presses: int = 0
    rows: list[dict] = field(default_factory=list)
    chosen: dict | None = None
    prompt_seen: bool = False
    prompt_answer: str | None = None    # only ever "ask later"
    reel: tuple[int, int] | None = None
    mode: str = ""              # unavailable | hidden | rows (what the Key plays tab showed)
    hidden_n: int | None = None
    playing: bool = False
    relinked: bool | None = None
    timings: dict = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    took_s: float = 0.0

    def row(self) -> dict:
        d = asdict(self)
        d["took_s"] = round(self.took_s, 2)
        d["timings"] = {k: round(v, 2) for k, v in self.timings.items()}
        return d


class _Closed(Exception):
    """Fail closed with this reason."""


class _Run:
    def __init__(self, stick, cfg, pkg, still_hidden, read, relink, clock, res: KeyPlaysResult):
        self.stick, self.cfg, self.pkg = stick, cfg, pkg
        self.still_hidden = still_hidden or (lambda: True)
        self.read_fn = read or ocr_panel
        self.relink, self.clock, self.res = relink, clock, res
        self.t0 = clock()
        self.fast_used = False
        self.positional = False

    def mark(self, name: str) -> None:
        self.res.timings[name] = self.clock() - self.t0

    async def key(self, code: str, n: int = 1) -> None:
        if not self.still_hidden():
            raise _Closed("stick is not hidden")
        for i in range(n):
            r = await self.stick.key(f"KEYCODE_{code}")
            if not getattr(r, "ok", False):
                raise _Closed(f"{code} not sent")
            self.res.presses += 1
            if n > 1 and i < n - 1:
                await asyncio.sleep(float(_cfg(self.cfg, "key_gap_s")))
        self.res.steps.append(code if n == 1 else f"{code} x{n}")

    async def read(self, wait: bool = True) -> PanelRead | None:
        if wait:
            await asyncio.sleep(float(_cfg(self.cfg, "read_wait_s")))
        t = self.clock()
        r = await self.read_fn(self.stick)
        took = self.clock() - t
        self.res.timings["reads_s"] = self.res.timings.get("reads_s", 0.0) + took
        self.res.timings["reads_n"] = self.res.timings.get("reads_n", 0) + 1
        self.res.steps.append(f"read {took:.1f}s " + ("unreadable" if r is None else
                                         f"tabs={','.join(r.tabs) or '-'} rows={len(r.rows)} focus={r.focus_kind}:"
                                         f"{r.focus_text[:30]!r}{' PROMPT' if r.prompt else ''}"
                                         f"{' reel=%d/%d' % r.reel if r.reel else ''}"))
        if r is not None and r.prompt:
            self.res.prompt_seen = True
        return r

    # -- stages ------------------------------------------------------------------------------------
    async def preconditions(self) -> None:
        if not self.still_hidden():
            raise _Closed("stick is not hidden")
        front = await self.stick.foreground()
        if YTTV_PKG_HINT not in (front or "") and not (self.pkg and self.pkg in (front or "")):
            raise _Closed(f"YouTube TV is not in front ({front or 'unknown'})")
        pb = await self.stick.playback(self.pkg)
        if pb.get("state") != "playing":
            raise _Closed(f"player is {pb.get('state')}, not playing")

    async def open(self) -> PanelRead:
        self.res.stage = "open"
        for attempt in range(int(_cfg(self.cfg, "open_tries"))):
            await self.key("DPAD_UP")
            await asyncio.sleep(float(_cfg(self.cfg, "open_wait_s")))
            await self.key("DPAD_CENTER")
            if _cfg(self.cfg, "fast_tabs"):
                # Speed (Nov 7: open 6 s + tab 16 s, every read ~5 s): the live path is known (the panel opens with a
                # card focused; UP reaches the tabs; Key plays is the rightmost tab), so send UP, RIGHT, RIGHT without
                # reads between. Navigation keys only: nothing is selected; extra RIGHTs stay on the rightmost tab.
                # One read then must show Key plays content, else fail closed (to_tab re-reads, presses nothing).
                await asyncio.sleep(float(_cfg(self.cfg, "open_wait_s")))
                await self.key("DPAD_UP")
                await asyncio.sleep(float(_cfg(self.cfg, "key_gap_s")))
                await self.key("DPAD_RIGHT", 2)
                self.fast_used = True
            r = await self.read()
            if r is not None and r.prompt:
                r = await self.answer_prompt(r)
            if r is not None and r.panel:
                self.mark("open_s")
                return r
            if r is not None and (r.rows or r.focus_kind in ("row", "catch_up", "prompt")):
                raise _Closed("something opened that is not the Key plays panel")
            # nothing recognisable (the post-tune banner swallows keys): wait and try again
            await asyncio.sleep(float(_cfg(self.cfg, "reopen_wait_s")))
        raise _Closed("panel did not open")

    async def answer_prompt(self, r: PanelRead) -> PanelRead | None:
        """'Auto-play key plays?': move focus until the box reads Ask later, then CENTER. Never CENTER otherwise."""
        self.res.stage = "prompt"
        seq = ["DPAD_DOWN"] * 3 + ["DPAD_RIGHT"] * 3
        for i in range(int(_cfg(self.cfg, "prompt_tries")) + 1):
            if r is None:
                raise _Closed("prompt unreadable")
            if not r.prompt:
                return r
            if r.focus_kind == "prompt" and r.focus_text == ASK_LATER:
                await self.key("DPAD_CENTER")
                self.res.prompt_answer = ASK_LATER
                r = await self.read()
                if r is not None and r.prompt:
                    raise _Closed("prompt still up after Ask later")
                return r
            if i >= int(_cfg(self.cfg, "prompt_tries")):
                break
            await self.key(seq[i % len(seq)])
            r = await self.read()
        raise _Closed("could not focus Ask later on the auto-play prompt (nothing selected)")

    async def to_tab(self, r: PanelRead) -> PanelRead:
        self.res.stage = "tab"
        if self.fast_used:
            for _ in range(int(_cfg(self.cfg, "fast_tab_reads"))):
                if r is not None and r.on_key_plays:
                    return self._on_tab(r)
                r = await self.read()
                if r is not None and r.prompt:
                    r = await self.answer_prompt(r)
            if r is not None and r.on_key_plays:
                return self._on_tab(r)
            raise _Closed("Key plays content not seen after UP, RIGHT, RIGHT (nothing selected)")
        up_used, rights = False, 0
        for _ in range(int(_cfg(self.cfg, "tab_tries"))):
            if r.on_key_plays:
                return self._on_tab(r)
            if r.focus_kind == "tab" and r.focus_text == "key plays":
                await self.key("DPAD_CENTER")       # the tab is focused: selecting it is safe
            elif r.focus_kind == "tab":
                order = list(TABS)
                await self.key("DPAD_RIGHT" if order.index(r.focus_text) < 2 else "DPAD_LEFT")
            elif r.panel and not up_used:
                up_used = True                       # live Nov 6: the panel opens with a card focused; UP reaches the tabs
                await self.key("DPAD_UP")
            elif r.panel and up_used and not r.focus_text and rights < 2:
                rights += 1                          # live Nov 6: OCR can't see focus on the tabs; RIGHT only changes tab,
                await self.key("DPAD_RIGHT")         # it selects nothing, and rows must appear before anything is picked
            else:
                raise _Closed(f"focus is not on a tab ({r.focus_kind or 'unknown'}: {r.focus_text[:30]!r})")
            nr = await self.read()
            if nr is not None and nr.prompt:
                nr = await self.answer_prompt(nr)
            if nr is None or not nr.panel:
                raise _Closed("panel lost while moving to Key plays")
            r = nr
        if r.on_key_plays:
            return self._on_tab(r)
        raise _Closed("Key plays tab not reached (no Key plays content read)")

    def _on_tab(self, r: PanelRead) -> PanelRead:
        self.mark("tab_s")
        self.res.mode, self.res.hidden_n = r.mode, r.hidden_n
        self.res.rows = [k.brief() for k in r.rows]
        self.res.steps.append(f"key plays: {r.mode}" + (f" ({r.hidden_n} hidden)" if r.hidden_n is not None else ""))
        if r.mode == "unavailable":
            raise _Closed("key plays unavailable on this channel (nothing selected)")
        return r

    async def focus_catch_up(self, r: PanelRead) -> PanelRead:
        """Focus 'Catch up via key plays': DOWN off the tab bar, verified by the focus box. Nothing else is selected."""
        self.res.stage = "focus"
        if (r.focus_kind == "" and r.mode == "hidden" and not r.rows and _cfg(self.cfg, "hidden_center_by_position")
                and r.catch_up_y is not None and (r.hidden_y is None or r.catch_up_y < r.hidden_y)):
            # Nov 7, BTN: OCR can't see focus on Catch up. In spoiler mode Catch up is the first item under the tabs,
            # so one DOWN off the tab bar lands on it by position (overseer's call). The select is then proven by the
            # reel's '1 of N key plays' label in play(); anything else fails closed (re-link to live).
            await self.key("DPAD_DOWN")
            self.positional = True
            self.mark("focus_s")
            self.res.chosen = {"catch_up": True, "hidden_n": r.hidden_n, "by_position": True}
            return r
        for _ in range(int(_cfg(self.cfg, "row_fix_tries")) + 1):
            if r.focus_kind == "catch_up":
                self.mark("focus_s")
                self.res.chosen = {"catch_up": True, "hidden_n": r.hidden_n}
                return r
            if r.focus_kind in ("", "tab"):
                await self.key("DPAD_DOWN")      # off the tab bar: moves focus, selects nothing
            elif r.focus_kind == "row":
                await self.key("DPAD_UP")
            else:
                raise _Closed(f"focus unknown near Catch up ({r.focus_kind}: {r.focus_text[:30]!r})")
            nr = await self.read()
            if nr is None or not nr.panel:
                raise _Closed("panel lost while focusing Catch up")
            r = nr
        raise _Closed("could not see focus on 'Catch up via key plays' (nothing selected)")

    async def focus_row(self, r: PanelRead, want: Want) -> tuple[PanelRead, KeyPlay]:
        """Focus the wanted row, scrolling down while it isn't on screen. Verified by the focus box before return."""
        self.res.stage = "find"
        tol = float(_cfg(self.cfg, "clock_tol_s"))
        seen: list[dict] = []

        def locate(rr: PanelRead) -> KeyPlay | None:
            if want.index is not None:
                return rr.rows[want.index] if 0 <= want.index < len(rr.rows) else None
            return match_play(rr.rows, want.period, float(want.clock_s), tol)

        target = locate(r)
        scrolls = 0
        while target is None:
            seen.extend(k.brief() for k in r.rows)
            if want.index is not None or scrolls >= int(_cfg(self.cfg, "scroll_max")):
                self.res.rows = seen[:60]
                raise _Closed("wanted play not in the Key plays list")
            # scroll: focus moves into / down the list; the read after each press says where it is
            await self.key("DPAD_DOWN")
            scrolls += 1
            nr = await self.read()
            if nr is None or not nr.panel:
                raise _Closed("panel lost while scrolling")
            r = nr
            target = locate(r)
        self.res.stage = "focus"
        blind_down = False
        for _ in range(int(_cfg(self.cfg, "row_fix_tries")) + 2):
            if r.focus_kind == "" and not blind_down:
                # Nov 7: focus on the tabs is invisible to OCR. One DOWN leaves the tab bar (selects nothing); the read
                # after it must then show focus on Catch up or a row.
                blind_down = True
                await self.key("DPAD_DOWN")
                nr = await self.read()
                if nr is None or not nr.panel:
                    raise _Closed("panel lost while focusing the row")
                r = nr
                t2 = locate(r)
                if t2 is None:
                    raise _Closed("wanted play scrolled out of view")
                target = t2
                continue
            if r.focus_kind == "row" and r.focus_row is not None:
                cur = r.rows[r.focus_row]
                if cur.clock_s == target.clock_s and cur.period == target.period:
                    self.res.chosen = cur.brief()
                    self.mark("focus_s")
                    return r, cur
                steps = target.index - cur.index
            elif r.focus_kind in ("tab", "catch_up"):
                steps = target.index + 1       # DOWN off the tab bar (and the catch-up button, read next) to the rows
            else:
                raise _Closed(f"row focus unknown ({r.focus_kind or 'none'}: {r.focus_text[:30]!r})")
            if steps == 0:
                raise _Closed("focused row does not match the wanted play")
            await self.key("DPAD_DOWN" if steps > 0 else "DPAD_UP", abs(steps))
            nr = await self.read()
            if nr is None or not nr.panel:
                raise _Closed("panel lost while focusing the row")
            r = nr
            t2 = locate(r)
            if t2 is None:
                raise _Closed("wanted play scrolled out of view")
            target = t2
        raise _Closed("could not focus the wanted row")

    async def play(self) -> None:
        self.res.stage = "play"
        await self.key("DPAD_CENTER")
        self.mark("select_s")
        r = await self.read()
        if r is not None and r.prompt:
            r = await self.answer_prompt(r)
        self.res.stage = "playing"
        end = self.clock() + float(_cfg(self.cfg, "settle_s"))
        while self.clock() < end:
            pb = await self.stick.playback(self.pkg)
            if pb.get("state") == "playing":
                self.res.playing = True
                break
            await asyncio.sleep(float(_cfg(self.cfg, "poll_s")))
        if not self.res.playing:
            raise _Closed("reel not playing after the select")
        self.mark("playing_s")
        r2 = r if (r is not None and r.reel) else await self.read()
        if r2 is not None and r2.prompt:
            r2 = await self.answer_prompt(r2)
        if r2 is not None and r2.reel:
            self.res.reel = r2.reel
        if self.positional:
            # a select by position counts only if the reel says it started at 1 of N (N = the hidden count)
            if self.res.reel is None:
                r3 = await self.read()
                self.res.reel = r3.reel if r3 is not None else None
            n = self.res.chosen.get("hidden_n") if self.res.chosen else None
            if self.res.reel is None or self.res.reel[0] != 1 or (n is not None and self.res.reel[1] != n):
                raise _Closed(f"Catch up by position not confirmed (reel {self.res.reel}, want 1 of {n})")

    async def fail_closed(self) -> None:
        if self.relink is None or not self.still_hidden():
            self.res.relinked = False
            return
        t = self.clock()
        try:
            self.res.relinked = bool(await self.relink())
        except Exception as e:
            log.warning("relink after a failed key-plays run failed: %r", e)
            self.res.relinked = False
        self.res.timings["relink_s"] = self.clock() - t


async def _drive(stick, body, *, cfg, pkg, still_hidden, read, relink, clock) -> KeyPlaysResult:
    clock = clock or time.monotonic
    res = KeyPlaysResult()
    if not enabled(cfg):
        res.reason = "flag yttv_keyplays.enabled is off"
        return res
    pkg = pkg or getattr(stick, "packages", {}).get("youtubetv")
    run = _Run(stick, cfg, pkg, still_hidden, read, relink, clock, res)
    try:
        await run.preconditions()
        await body(run)
        res.ok = True
    except _Closed as e:
        res.ok, res.reason = False, str(e)
        res.steps.append(f"fail: {e}")
        if res.presses:
            await run.fail_closed()
    except Exception as e:      # never raise into the switch path
        log.warning("yttv_keyplays failed: %r", e)
        res.ok, res.reason = False, f"exception: {e!r}"[:160]
        if res.presses:
            await run.fail_closed()
    res.took_s = clock() - run.t0
    return res


async def read_key_plays(stick, *, cfg=None, pkg: str | None = None, still_hidden=None, read: Reader | None = None,
                         relink: Callable[[], Awaitable[bool]] | None = None, clock=None) -> KeyPlaysResult:
    """Open the panel, reach Key plays, read the visible rows, then return to live with relink()."""
    async def body(run: _Run):
        await run.to_tab(await run.open())
        run.res.stage = "list"
        run.mark("list_s")
        if relink is not None:
            t = run.clock()
            run.res.relinked = bool(await relink())
            run.res.timings["relink_s"] = run.clock() - t
            run.res.stage = "live"
    return await _drive(stick, body, cfg=cfg, pkg=pkg, still_hidden=still_hidden, read=read, relink=relink,
                        clock=clock)


async def catch_up(stick, want: Want, *, cfg=None, pkg: str | None = None, still_hidden=None,
                   read: Reader | None = None, relink: Callable[[], Awaitable[bool]] | None = None,
                   clock=None) -> KeyPlaysResult:
    """Start the Key plays reel at the wanted play (or from 1 of N with want.from_start) on a HIDDEN stick. The caller flips at a dead ball, and calls
    return_to_live() when the clip(s) are done (~10 s per play). Fails closed (re-linked to live) on any doubt."""
    if want.index is None and want.clock_s is None and not want.from_start:
        return KeyPlaysResult(reason="nothing wanted")

    async def body(run: _Run):
        r = await run.to_tab(await run.open())
        run.mark("list_s")
        if want.from_start:
            if not r.catch_up:
                raise _Closed("no 'Catch up via key plays' button")
            await run.focus_catch_up(r)
        elif r.mode == "hidden":
            raise _Closed("spoiler mode: key plays are hidden, no rows to pick (only Catch up from 1 of N)")
        else:
            r, _ = await run.focus_row(r, want)
        await run.play()
    return await _drive(stick, body, cfg=cfg, pkg=pkg, still_hidden=still_hidden, read=read, relink=relink,
                        clock=clock)


async def return_to_live(stick, relink: Callable[[], Awaitable[bool]], *, cfg=None, pkg: str | None = None,
                         clock=None) -> tuple[bool, float | None]:
    """Deep link back to live (relink, e.g. YouTubeTVTuner.tune) and time it to PLAYING. (ok, seconds)."""
    clock = clock or time.monotonic
    pkg = pkg or getattr(stick, "packages", {}).get("youtubetv")
    t0 = clock()
    try:
        if not await relink():
            return False, None
    except Exception as e:
        log.warning("return to live failed: %r", e)
        return False, None
    end = t0 + float(_cfg(cfg, "relink_settle_s"))
    while clock() < end:
        pb = await stick.playback(pkg)
        if pb.get("state") == "playing":
            return True, clock() - t0
        await asyncio.sleep(float(_cfg(cfg, "poll_s")))
    return False, None
