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
"""
from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("redzone.scorebug")

EVERY_S = 2.0             # capture cadence while a Prime game is on screen
LOWER_FROM = 2 / 3        # the score bug lives in the lower third
MIN_SAMPLE_S = 5.0
MAX_SAMPLE_S = 120.0
AMBIGUOUS_S = 10.0        # two feed scores this close: can't tell which one the bug showed
MAX_GAP_S = 9.0           # frames further apart than this time a change too loosely (HDMI1 stick: ~7 s per frame)
UNIFORM_BYTES_PER_PX = 0.03   # a PNG this compressible is black / near-uniform (DRM-blanked video)
PRIME = "prime"

_SCORES = re.compile(r"^\d{1,3}(?:\s*[|•·-]?\s*\d{1,3})?$")
_NUM = re.compile(r"\d{1,3}")
_PERIOD = re.compile(r"\b(HALFTIME|HALF|FINAL(?:/OT)?|END OF (?:THE )?(?:1ST|2ND|3RD|4TH|Q[1-4]|OT)"
                     r"|[1-4](?:ST|ND|RD|TH)|Q[1-4]|\d?OT)\b")
_CLOCK = re.compile(r"\b(\d{1,2}:\d{2}|\d{1,2}\.\d)\b")


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
    p, c = _PERIOD.search(text), _CLOCK.search(text)
    return Bug(home_s, away_s, p.group(1) if p else "", c.group(1) if c else "")


@dataclass
class Frame:
    wall: float          # when the screenshot started (the frame is from about then)
    score: tuple[int, int] | None


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
    last: dict = field(default_factory=dict)      # stick key -> (tune, [Frame...])
    samples: list = field(default_factory=list)
    rejected: int = 0
    frames: int = 0
    read: int = 0
    last_bug: dict = field(default_factory=dict)  # target -> Bug (latest read)

    def reset(self, key: str | None = None) -> None:
        """A switch, flip or preload: frames before it don't pair with frames after it."""
        if key is None:
            self.last.clear()
        else:
            self.last.pop(key, None)

    def observe(self, key: str, target: str, tune: object, wall: float, bug: Bug | None,
                timeline) -> Sample | None:
        self.frames += 1
        if not target or not str(target).startswith(PRIME + ":"):
            self.reset(key)
            return None
        held = self.last.get(key)
        if held is None or held[0] is not tune or held[1] != target:
            held = (tune, target, [])
            self.last[key] = held
        frames: list[Frame] = held[2]
        if bug is not None:
            self.read += 1
            self.last_bug[target] = bug
        frames.append(Frame(wall, bug.score if bug else None))
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
            self.rejected += 1
            log.info("score bug %s -> %s on %s not timed: %s", a.score, b.score, target, why)
            return None
        sample = onset - e.seen
        if not MIN_SAMPLE_S <= sample <= MAX_SAMPLE_S:
            self.rejected += 1
            log.info("score bug %s -> %s on %s: %.1fs behind is out of range; ignored", a.score, b.score,
                     target, sample)
            return None
        s = Sample(target, sample, e.seen, onset, gap / 2.0, b.score, e.label)
        self.samples.append(s)
        del self.samples[:-50]
        log.info("delay %.1fs (±%.1f) on %s from the score bug %s -> %s (%s)", sample, s.error_s, target,
                 a.score, b.score, e.label[:40])
        self.on_sample(s)
        return s

    def status(self) -> dict:
        last = self.samples[-1] if self.samples else None
        return {"samples": len(self.samples), "rejected": self.rejected, "frames": self.frames,
                "read": self.read,
                "last": None if last is None else {"target": last.target, "delay_s": round(last.sample, 1),
                                                   "at": last.onset, "score": list(last.score)}}


def read_png(png: bytes, home, away, ocr, timeout: float = 8.0) -> Bug | None:
    """OCR one screenshot (blocking: run via asyncio.to_thread) and parse its score bug.
    Black/uniform frames are skipped without OCR. `ocr(path, timeout=)` -> OCRResult."""
    import os
    import tempfile
    if is_uniform(png):
        return None
    with tempfile.TemporaryDirectory(prefix="rz-bug-") as d:
        path = os.path.join(d, "screen.png")
        with open(path, "wb") as f:
            f.write(png)
        result = ocr(path, timeout=timeout)
    if not getattr(result, "read_ok", False):
        return None
    return parse_bug(result.observations, home, away)
