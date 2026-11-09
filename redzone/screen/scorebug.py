"""Stream delay measured from the broadcast's own score bug (no taps, no keys pressed).

Prime Video's live player (Monumental+, Sye) is not DRM-blanked in `screencap`, so a screenshot shows
the real picture, score bug included ('WASHINGTON WIZARDS 60 | 59 ORLANDO MAGIC  HALFTIME'). YouTube
TV and Peacock come out black and give nothing here.

While a Prime game is on screen we take a screenshot about every 2 s, read the lower third with the
Mac's local Vision OCR and parse the two scores. When the on-screen score changes (the same new score
in two frames in a row), the feed's play that produced exactly that score dates it:

    delay = (first frame with the new score - half the gap since the frame before) - play wallclock

The error is about half the capture gap. A bug that updates a beat after the basket biases the
sample late, the safe side (too low a delay is what cuts plays mid-play). Samples are rejected
when two scores land within 10 s in the feed (which one changed the bug?), when the frame before
didn't show the play's previous score, or when the result is outside 5..120 s.

[honest frame times, Nov 8] A frame's time is the midpoint of its capture request and done, with its own doubt
(duration/sqrt(12), a slow stick says so itself). [clock fit] The running game clock over a window of frames gives
one sample with a slope check, instead of single reads. [bug gone] Frames say why they showed no bug: a DRM-blank
frame is blindness (YouTube TV), a black frame on Prime is real (a break or a fade), a clean read with no bug is
evidence of a commercial or replay. Numbers only, as everywhere here: the PNG is dropped.

Why: first added in 11670ff3 (2026-11-02) as "ScoreBugWatcher: read the broadcast score bug in Prime screenshots, time score changes against th..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import logging
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("redzone.scorebug")

EVERY_S = 2.0             # capture cadence while a Prime game is on screen
LOWER_FROM = 2 / 3        # the score bug lives in the lower third
MIN_SAMPLE_S = 5.0
MAX_SAMPLE_S = 120.0
PENDING_S = 120.0         # [auto sync] a change / clock read waits this long for the feed to have its play
AMBIGUOUS_S = 10.0        # two feed scores this close: can't tell which one the bug showed
MAX_GAP_S = 9.0           # frames further apart than this time a change too loosely (HDMI1 stick: ~7 s per frame)
UNIFORM_BYTES_PER_PX = 0.03   # a PNG this compressible is black / near-uniform (DRM-blanked video)
PRIME = "prime"
GONE_FRAMES = 3           # [bug gone] this many frames in a row with no bug ...

_SCORES = re.compile(r"^\d{1,3}(?:\s*[|•·-]?\s*\d{1,3})?$")
_NUM = re.compile(r"\d{1,3}")
_PERIOD = re.compile(r"\b(HALFTIME|HALF|FINAL(?:/OT)?|END OF (?:THE )?(?:1ST|2ND|3RD|4TH|Q[1-4]|OT)"
                     r"|[1-4](?:ST|ND|RD|TH)|Q[1-4]|\d?OT)\b")
_CLOCK = re.compile(r"\b(\d{1,2}:\d{2}|\d{1,2}\.\d)\b")
# [Nick 9:25 AM Nov 5, TNF race] football's down & distance, e.g. "3RD & 7", "1ST & GOAL" (read before the period,
# which has the same "3RD" spelling)
_DOWN = re.compile(r"\b([1-4])(?:ST|ND|RD|TH)\s*(?:&|AND)\s*(\d{1,2}|GOAL|GL|INCHES)\b")


def clock_seconds(text: str) -> float | None:
    """'12:34' -> 754.0, '45.6' (under a minute) -> 45.6, else None."""
    m = _CLOCK.fullmatch((text or "").strip())
    if not m:
        return None
    t = m.group(1)
    if ":" in t:
        a, b = t.split(":")
        return int(a) * 60 + int(b)
    return float(t)


def period_number(text: str, clocks=()) -> int | None:
    """'2ND' / 'Q2' / '2' -> 2; 'OT' -> the feed's current period when it is past regulation."""
    t = (text or "").upper()
    m = re.search(r"\d", t)
    if m and "OT" not in t:
        return int(m.group())
    if "OT" in t:
        latest = max((p for _, p, _ in clocks if p is not None), default=None)
        return latest if latest is not None and latest >= 4 else None
    return None


def png_size(png: bytes) -> tuple[int, int] | None:
    if len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n") or png[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", png[16:24])


def is_uniform(png: bytes) -> bool:
    """Black or near-uniform frame (a DRM-blanked player): it compresses to almost nothing."""
    size = png_size(png)
    return size is None or len(png) / max(1, size[0] * size[1]) < UNIFORM_BYTES_PER_PX


@dataclass(frozen=True)
class Bug:
    home: int
    away: int
    period: str = ""
    clock: str = ""
    down: str = ""   # football: "3&7", "1&GOAL"; "" elsewhere / unread

    @property
    def score(self) -> tuple[int, int]:
        return self.home, self.away


def _tokens(team) -> set[str]:
    """Words that identify a team on a bug: its abbreviation and the words of its name."""
    words = {str(getattr(team, "abbr", "") or "").upper()}
    for part in (getattr(team, "name", "") or "").upper().replace(".", " ").split():
        if len(part) >= 4 or part in ("NETS", "HEAT", "JAZZ", "SUNS", "KINGS"):
            words.add(part)
    return {w for w in words if len(w) >= 2}


def _match(text: str, tokens: set[str]) -> bool:
    up = re.sub(r"[^A-Z0-9 ]", " ", text.upper())
    return any(re.search(rf"\b{re.escape(t)}", up) for t in tokens)


def parse_bug(observations, home, away, lower_from: float = LOWER_FROM) -> Bug | None:
    """The score bug in one OCR read (normalized, top-left boxes), or None.

    Needs both teams named in the lower third (a name, a nickname or the abbreviation) and exactly two
    big numbers near them: the larger digits on a bug are the scores; anything as tall is ambiguous.
    Each score belongs to the team on its side."""
    rows = [o for o in observations if o.y >= lower_from and o.confidence >= 0.5]
    ht, at = _tokens(home), _tokens(away)
    hs = [o for o in rows if _match(o.text, ht) and not _match(o.text, at)]
    aw = [o for o in rows if _match(o.text, at) and not _match(o.text, ht)]
    if not hs or not aw:
        return None
    hx = sum(o.x + o.width / 2 for o in hs) / len(hs)
    ax = sum(o.x + o.width / 2 for o in aw) / len(aw)
    if abs(hx - ax) < 0.05:
        return None
    top = min(o.y for o in hs + aw) - 0.1
    bottom = max(o.y + o.height for o in hs + aw) + 0.1
    nums = []   # (height, x centre, value)
    for o in rows:
        text = o.text.strip()
        if not (top <= o.y <= bottom and _SCORES.match(text)):
            continue
        for m in _NUM.finditer(text):
            cx = o.x + o.width * (m.start() + m.end()) / 2 / max(1, len(text))
            nums.append((o.height, cx, int(m.group())))
    nums.sort(reverse=True)
    if len(nums) < 2 or (len(nums) > 2 and nums[2][0] >= 0.75 * nums[1][0]):
        return None
    (_, x1, v1), (_, x2, v2) = nums[:2]
    left, right = (v1, v2) if x1 < x2 else (v2, v1)
    home_s, away_s = (left, right) if hx < ax else (right, left)
    text = " ".join(o.text.upper() for o in rows if top <= o.y <= bottom + 0.1)
    dn = _DOWN.search(text)
    if dn:
        text = text[:dn.start()] + " " + text[dn.end():]
    p, c = _PERIOD.search(text), _CLOCK.search(text)
    return Bug(home_s, away_s, p.group(1) if p else "", c.group(1) if c else "",
               f"{dn.group(1)}&{dn.group(2)}" if dn else "")


def frame_time(t_req: float, t_done: float, offset_s: float = 0.0, base_sd_s: float = 0.1) -> tuple[float, float]:
    """[honest frame times] (time, sd) of a frame captured between t_req and t_done: the midpoint and its doubt, a
    uniform over the capture (offset_s: the stick's calibrated capture offset against a visible clock, 0 until measured)."""
    dur = max(0.0, t_done - t_req)
    return t_req + dur / 2.0 + offset_s, math.hypot(dur / math.sqrt(12.0), base_sd_s)


def frame_kind(png: bytes, app: str) -> str:
    """[bug gone] picture | black | drm_blank. A uniform frame from an app the capture can read (Prime) is a real
    black screen, a break or a fade; from any other app it is the capture being blanked, and says nothing."""
    if not is_uniform(png):
        return "picture"
    return "black" if app == PRIME else "drm_blank"


@dataclass
class Frame:
    wall: float          # when the screenshot started (the frame is from about then)
    score: tuple[int, int] | None
    sd: float = 0.0      # [honest frame times] the frame time's doubt (0: not bracketed)


@dataclass
class Sample:
    target: str
    sample: float        # delay, s
    play_wall: float
    onset: float         # when the new score reached the screen (estimate)
    error_s: float       # half the gap between the two frames
    score: tuple[int, int]
    label: str = ""


def match_change(scores, prev_score: tuple[int, int], new_score: tuple[int, int], onset: float
                 ) -> tuple[object | None, str]:
    """The feed's score event that changed the bug from prev_score to new_score, or (None, why).

    `scores` are the timeline's "score" events (seen = wallclock, spread 0 when exact), oldest first."""
    exact = [e for e in scores if e.spread == 0]
    hits = [i for i, e in enumerate(exact) if tuple(e.score) == tuple(new_score)]
    if len(hits) != 1:
        return None, "no feed play made that score" if not hits else "that score appears twice in the feed"
    i = hits[0]
    e = exact[i]
    before = exact[i - 1].score if i > 0 else None
    if before is None or tuple(before) != tuple(prev_score):
        return None, "the frame before didn't show the play's previous score"
    if any(abs(o.seen - e.seen) < AMBIGUOUS_S for j, o in enumerate(exact) if j != i):
        return None, "another score within 10 s in the feed"
    if any(abs(o.seen - e.seen) < AMBIGUOUS_S for o in scores if o.spread > 0):
        return None, "an untimed score nearby"
    if onset < e.seen:
        return None, "the bug changed before the play happened"
    return e, ""


@dataclass
class ScoreBugWatcher:
    """Turns successive score-bug reads of each stick into delay samples.

    on_sample(Sample) is called for each accepted sample. observe() gets one frame per call: the
    stick, the Prime target and tune it shows, the game (home/away Teams) and its timeline."""
    on_sample: Callable[[Sample], None] = lambda s: None
    # [auto sync] a new Prime tune on a stick: (target, wall); a delay sample from the bug's game clock
    # against the feed's play clocks: (target, frame wall, delay s). Both feed the filter (sync.py).
    on_tune: Callable[[str, float], None] = lambda t, w: None
    on_clock: Callable[[str, float, float], None] = lambda t, w, d: None
    # [clock fit] (target, frame wall, delay s, sd): one fitted window (None: on_clock gets it, without its sd)
    on_clock_fit: Callable[[str, float, float, float], None] | None = None
    # [clock stop] (target, stop wall, delay s, sd): where the bug's clock stopped against the feed's play there. Shadow
    on_stop: Callable[[str, float, float, float], None] | None = None
    honest_frames: bool = False                   # delay.scorebug_midpoint: observe(t_done=) brackets each frame
    capture_offset_s: float = 0.0                 # per-stick capture offset, once calibrated
    clock_fit: bool = False                       # delay.scorebug_clock_fit: window fit over single reads
    clock_every_s: float = 10.0                   # at most one clock sample per target this often
    fit_window_s: float = 30.0                    # [clock fit] how far back a fit looks
    fit_fallback_s: float = 60.0                  # a fit this recent: the single-read clock samples are not needed
    gone_min_s: float = 6.0                       # [bug gone] GONE_FRAMES frames over at least this long: a break or replay
    replay_clock_back_s: float = 5.0              # the bug's clock jumped back this far within a period: a replay
    replay_hold_s: float = 20.0                   # ... which counts this long
    stop_match_s: float = 1.5                     # [clock stop] a feed play within this much game clock of the stop
    last: dict = field(default_factory=dict)      # stick key -> (tune, [Frame...])
    _clock_prev: dict = field(default_factory=dict)   # stick key -> (wall, clock s, period) of the last read
    _clock_at: dict = field(default_factory=dict)     # target -> wall of the last clock sample
    # [auto sync] changes / clock reads the feed hadn't caught up with yet (its plays arrive 35-80 s
    # after they happen, often after the screen showed them): retried each frame for PENDING_S
    pending: list = field(default_factory=list)       # (kind, target, created wall, payload...)
    samples: list = field(default_factory=list)
    rejected: int = 0
    frames: int = 0
    read: int = 0
    last_bug: dict = field(default_factory=dict)  # target -> Bug (latest read)
    _window: dict = field(default_factory=dict)   # [clock fit] stick key -> (period, [(wall, clock s)...])
    _fit_at: dict = field(default_factory=dict)   # [clock fit] target -> wall of the last fit used
    fits: int = 0                                 # [clock fit] windows accepted
    fit_rejected: int = 0                         # ... and refused (slope, scatter, too few inliers)
    _absent: dict = field(default_factory=dict)   # [bug gone] target -> [first wall, frames, black frames]
    _seen_kinds: dict = field(default_factory=dict)   # [bug gone] target -> {kind: frames}
    _replay_at: dict = field(default_factory=dict)    # [bug gone] target -> wall the clock last jumped back
    stops: dict = field(default_factory=dict)         # [clock stop] target -> latest stop (see end_of_play)
    _run: dict = field(default_factory=dict)          # [clock stop] stick key -> (wall of the last change, clock, period)

    def reset(self, key: str | None = None) -> None:
        """A switch, flip or preload: frames before it don't pair with frames after it."""
        if key is None:
            self.last.clear()
            self._window.clear()
            self._run.clear()
        else:
            self.last.pop(key, None)
            self._window.pop(key, None)
            self._run.pop(key, None)

    def observe(self, key: str, target: str, tune: object, wall: float, bug: Bug | None,
                timeline, t_done: float | None = None, kind: str | None = None) -> Sample | None:
        """wall: when the capture was requested; t_done: when it came back (honest_frames); kind: read_frame's why."""
        self.frames += 1
        sd = 0.0
        if self.honest_frames and t_done is not None:
            wall, sd = frame_time(wall, t_done, self.capture_offset_s)
        if not target or not str(target).startswith(PRIME + ":"):
            self.reset(key)
            return None
        held = self.last.get(key)
        if held is None or held[0] is not tune or held[1] != target:
            held = (tune, target, [])
            self.last[key] = held
            self._clock_prev.pop(key, None)
            self._window.pop(key, None)
            self._run.pop(key, None)
            # a new tune is a new stream: a change or clock read queued against the old picture would time the new one
            self.pending = [p for p in self.pending if p[1] != target]
            try:
                self.on_tune(target, wall)
            except Exception:
                log.exception("sync hand-off failed for %s", target)
        self._note_frame(target, wall, bug, kind)
        if bug is not None:
            self._clock(key, target, wall, bug, timeline)
        self._retry(target, wall, timeline)
        frames: list[Frame] = held[2]
        if bug is not None:
            self.read += 1
            self.last_bug[target] = bug
        frames.append(Frame(wall, bug.score if bug else None, sd))
        del frames[:-4]
        # A change: [.., old, new, new] with both new frames read, and the old one right before.
        if len(frames) < 3:
            return None
        a, b, c = frames[-3:]
        if None in (a.score, b.score, c.score) or b.score != c.score or a.score == b.score:
            return None
        if any(x < y for x, y in zip(b.score, a.score)):
            self.rejected += 1   # a score went down: a misread, a replay graphic or another game
            return None
        gap = b.wall - a.wall
        if gap > MAX_GAP_S or gap <= 0:
            self.rejected += 1
            return None
        onset = b.wall - gap / 2.0
        e, why = match_change(getattr(timeline, "scores", ()) if timeline else (), a.score, b.score, onset)
        if e is None:
            if why == "no feed play made that score":
                # [auto sync] the feed may simply not have it yet: try again as frames come in
                self.pending.append(("score", target, wall, a.score, b.score, onset, gap, self._gap_sd(gap, a, b)))
                del self.pending[:-20]
                return None
            self.rejected += 1
            log.info("score bug %s -> %s on %s not timed: %s", a.score, b.score, target, why)
            return None
        return self._accept(target, e, onset, gap, a.score, b.score, self._gap_sd(gap, a, b))

    def _gap_sd(self, gap: float, a: Frame, b: Frame) -> float | None:
        """[honest frame times] The onset's doubt: uniform over the gap, plus the frames' own (None: not bracketed)."""
        return math.hypot(gap / math.sqrt(12.0), max(a.sd, b.sd)) if a.sd or b.sd else None

    def _retry(self, target: str, wall: float, timeline) -> None:
        """[auto sync] Pending score changes and clock reads for this target, now the feed may have them."""
        from redzone.decider.sync import clock_sample
        keep = []
        for p in self.pending:
            if p[1] != target:
                keep.append(p)
                continue
            if wall - p[2] > PENDING_S:
                if p[0] == "score":
                    self.rejected += 1
                    log.info("score bug %s -> %s on %s not timed: the feed never had it", p[3], p[4], target)
                continue
            if p[0] == "score":
                _, _, _, prev, new, onset, gap, err = p
                e, why = match_change(getattr(timeline, "scores", ()) if timeline else (), prev, new, onset)
                if e is None and why == "no feed play made that score":
                    keep.append(p)
                elif e is None:
                    self.rejected += 1
                    log.info("score bug %s -> %s on %s not timed: %s", prev, new, target, why)
                else:
                    self._accept(target, e, onset, gap, prev, new, err)
            elif p[0] in ("fit", "stop"):
                self._retry_timed(p, keep, timeline)
            else:
                _, _, _, frame_wall, c, per = p
                plays = sorted((w, cl) for w, pp, cl in (getattr(timeline, "clocks", ()) or ())
                               if pp == per and cl is not None)
                d = clock_sample(c, frame_wall, plays)
                if d is None and (not plays or plays[-1][1] >= c):
                    keep.append(p)        # the feed's clock hasn't passed this reading yet
                elif d is not None and MIN_SAMPLE_S <= d <= MAX_SAMPLE_S:
                    self._clock_at[target] = frame_wall
                    try:
                        self.on_clock(target, frame_wall, d)
                    except Exception:
                        log.exception("sync hand-off failed for %s", target)
        self.pending = keep

    def _accept(self, target: str, e, onset: float, gap: float, prev, new, err: float | None = None) -> Sample | None:
        a_score, b_score = prev, new
        sample = onset - e.seen
        if not MIN_SAMPLE_S <= sample <= MAX_SAMPLE_S:
            self.rejected += 1
            log.info("score bug %s -> %s on %s: %.1fs behind is out of range; ignored", a_score, b_score,
                     target, sample)
            return None
        s = Sample(target, sample, e.seen, onset, gap / 2.0 if err is None else err, b_score, e.label)
        self.samples.append(s)
        del self.samples[:-50]
        log.info("delay %.1fs (±%.1f) on %s from the score bug %s -> %s (%s)", sample, s.error_s, target,
                 a_score, b_score, e.label[:40])
        self.on_sample(s)
        return s

    def _clock(self, key: str, target: str, wall: float, bug: Bug, timeline) -> None:
        """[auto sync] The bug's game clock, running on screen, against the feed's running clock."""
        from redzone.decider.sync import clock_sample
        c = clock_seconds(bug.clock)
        per = period_number(bug.period, getattr(timeline, "clocks", ()) if timeline else ())
        prev = self._clock_prev.get(key)
        self._clock_prev[key] = (wall, c, per)
        self._track_stop(key, target, wall, c, per)
        if self.clock_fit and c is not None and per is not None:
            self._fit(key, target, wall, c, per)
        if c is None or per is None or prev is None or prev[1] is None or prev[2] != per:
            return
        if self.clock_fit and wall - self._fit_at.get(target, float("-inf")) < self.fit_fallback_s:
            return                                     # a fit covers this stretch: no single reads on top of it
        ran, took = prev[1] - c, wall - prev[0]
        if ran <= 0 or abs(ran - took) > 2.5:          # stopped, or the frames aren't one running clock
            return
        if wall - self._clock_at.get(target, float("-inf")) < self.clock_every_s:
            return
        plays = sorted((w, cl) for w, p, cl in (getattr(timeline, "clocks", ()) or ()) if p == per and cl is not None)
        d = clock_sample(c, wall, plays)
        if d is None and (not plays or plays[-1][1] >= c) and not any(
                p[0] == "clock" and p[1] == target for p in self.pending):
            self.pending.append(("clock", target, wall, wall, c, per))   # the feed is behind: later
            self._clock_at[target] = wall
            return
        if d is None or not MIN_SAMPLE_S <= d <= MAX_SAMPLE_S:
            return
        self._clock_at[target] = wall
        try:
            self.on_clock(target, wall, d)
        except Exception:
            log.exception("sync hand-off failed for %s", target)

    # ------------------------------------------------------------------ [clock fit]
    def _fit(self, key: str, target: str, wall: float, c: float, per: int) -> None:
        from redzone.decider.sync import fit_clock_window
        win = self._window.get(key)
        if win is None or win[0] != per:
            win = self._window[key] = (per, [])
        pts = win[1]
        if pts and pts[-1][1] == c and wall - pts[-1][0] >= 1.5:
            pts.clear()                                # the clock stopped: the running stretch starts over
        pts.append((wall, c))
        pts[:] = [p for p in pts if wall - p[0] <= self.fit_window_s]
        if wall - self._fit_at.get(target, float("-inf")) < self.clock_every_s or len(pts) < 5:
            return
        fit = fit_clock_window(pts)
        if fit is None:
            if pts[-1][0] - pts[0][0] >= self.fit_window_s - 2.0:
                self.fit_rejected += 1
                pts.pop(0)
            return
        pts.clear()                                    # each fit stands on its own frames
        self._fit_at[target] = wall
        self.pending.append(("fit", target, wall, fit, per))
        del self.pending[:-20]

    def _retry_timed(self, p: tuple, keep: list, timeline) -> None:
        """[clock fit / clock stop] A fitted window or a clock stop, now the feed may have its plays."""
        from redzone.decider.sync import fit_delay
        clocks = getattr(timeline, "clocks", ()) or ()
        if p[0] == "fit":
            _, target, _, fit, per = p
            plays = sorted((w, cl) for w, pp, cl in clocks if pp == per and cl is not None)
            d = fit_delay(fit, plays)
            if d is None and (not plays or plays[-1][1] >= fit.clock_lo):
                keep.append(p)                         # the feed hasn't passed this stretch yet
            elif d is not None and MIN_SAMPLE_S <= d <= MAX_SAMPLE_S:
                self.fits += 1
                self._clock_at[target] = fit.wall_ref
                try:
                    if self.on_clock_fit is not None:
                        self.on_clock_fit(target, fit.wall_ref, d, fit.sd)
                    else:
                        self.on_clock(target, fit.wall_ref, d)
                except Exception:
                    log.exception("sync hand-off failed for %s", target)
            return
        _, target, _, stop_wall, c, per, sd = p
        near = [w for w, pp, cl in clocks if pp == per and cl is not None and abs(cl - c) <= self.stop_match_s]
        if not near:
            if not any(pp == per and cl is not None and cl < c for _, pp, cl in clocks):
                keep.append(p)
            return
        d = stop_wall - max(near)
        self.stops[target] = {**self.stops.get(target, {}), "feed_delay_s": round(d, 2)}
        if self.on_stop is not None and MIN_SAMPLE_S <= d <= MAX_SAMPLE_S:
            try:
                self.on_stop(target, stop_wall, d, sd)
            except Exception:
                log.exception("clock stop hand-off failed for %s", target)

    # ------------------------------------------------------------------ [clock stop] end of play on Prime
    def _track_stop(self, key: str, target: str, wall: float, c: float | None, per: int | None) -> None:
        """The clock on the bug stopped: the on-screen end of the play. In the clock sports (basketball, hockey) a dead
        ball stops the clock; football's runs between plays, so there it only marks clock-stopping plays."""
        run = self._run.get(key)
        if c is None or per is None:
            return
        if run is None or run[2] != per:
            self._run[key] = (wall, c, per, False)
            return
        changed_at, last, _, stopped = run
        if c > last + self.replay_clock_back_s:
            self._replay_at[target] = wall             # the clock jumped back: a replay is on screen
        if c != last:
            self._run[key] = (wall, c, per, False)
            return
        if stopped or wall - changed_at < 1.5:
            return
        self._run[key] = (changed_at, c, per, True)
        sd = (wall - changed_at) / math.sqrt(12.0)
        self.stops[target] = {"wall": round(changed_at, 2), "sd": round(sd, 2), "clock": c, "period": per}
        if self.on_stop is not None:                   # shadow: never crowds out pending score samples when off
            self.pending.append(("stop", target, wall, changed_at, c, per, sd))
            del self.pending[:-20]

    def end_of_play(self, target: str) -> dict | None:
        """The latest on-screen clock stop for this target, {wall, sd, clock, period, feed_delay_s?}, or None."""
        return self.stops.get(target)

    # ------------------------------------------------------------------ [bug gone]
    def _note_frame(self, target: str, wall: float, bug: Bug | None, kind: str | None) -> None:
        if kind is None and bug is None:
            return                                     # the caller didn't say why: no evidence either way
        kind = "picture" if bug is not None else kind
        kinds = self._seen_kinds.setdefault(target, {})
        kinds[kind] = kinds.get(kind, 0) + 1
        if bug is not None:
            self._absent.pop(target, None)
        elif kind in ("black", "no_bug"):
            a = self._absent.setdefault(target, [wall, 0, 0])
            a[1] += 1
            a[2] += kind == "black"

    def evidence(self, target: str, now: float) -> dict:
        """[bug gone] What the picture on this Prime target says: bug_absent_s and frames with no bug (a read with
        nothing, or real black), break = gone long enough to be a commercial or replay, replay = the clock jumped back
        lately. A DRM-blank frame and an OCR failure are blindness and count for nothing. Shadow evidence: nothing acts on it."""
        a = self._absent.get(target)
        absent_s = max(0.0, now - a[0]) if a else 0.0
        frames = a[1] if a else 0
        return {"bug_absent_s": round(absent_s, 1), "absent_frames": frames, "black_frames": a[2] if a else 0,
                "break": frames >= GONE_FRAMES and absent_s >= self.gone_min_s,
                "replay": now - self._replay_at.get(target, float("-inf")) <= self.replay_hold_s}

    def status(self) -> dict:
        last = self.samples[-1] if self.samples else None
        return {"samples": len(self.samples), "rejected": self.rejected, "frames": self.frames,
                "read": self.read, "fits": self.fits, "fit_rejected": self.fit_rejected,
                "last": None if last is None else {"target": last.target, "delay_s": round(last.sample, 1),
                                                   "at": last.onset, "score": list(last.score)}}


def read_frame(png: bytes, home, away, ocr, app: str, timeout: float = 8.0) -> tuple[Bug | None, str]:
    """[bug gone] OCR one screenshot (blocking: run via asyncio.to_thread) and say why it has no bug:
    (bug, picture | no_bug | black | drm_blank | ocr_failed). Uniform frames skip OCR. `ocr(path, timeout=)` -> OCRResult."""
    kind = frame_kind(png, app)
    if kind != "picture":
        return None, kind
    import os
    import tempfile
    with tempfile.TemporaryDirectory(prefix="rz-bug-") as d:
        path = os.path.join(d, "screen.png")
        with open(path, "wb") as f:
            f.write(png)
        result = ocr(path, timeout=timeout)
    if not getattr(result, "read_ok", False):
        return None, "ocr_failed"
    bug = parse_bug(result.observations, home, away)
    return bug, "picture" if bug is not None else "no_bug"


def read_png(png: bytes, home, away, ocr, timeout: float = 8.0) -> Bug | None:
    """OCR one screenshot and parse its score bug (read_frame without the why)."""
    return read_frame(png, home, away, ocr, PRIME, timeout)[0]
