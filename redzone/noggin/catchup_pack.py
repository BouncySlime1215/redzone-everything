"""Catch-up packages (park.catchup, off): the 3-5 plays a viewer missed on a game, each from its start, then live.

Why: Nick, Nov 11: "the last time we checked in on this Browns game ... we're going to give you like 4 or 5 plays back to
back, and then we'll go back and join live" (phase 2 plan, 2C item 3). Prime's Rapid Recap (2 min or less, then live) is
the closest product; NFL RedZone queues a missed touchdown "to the snap".

Pure: no clock, no I/O, no device. The caller passes `now`, so a replay gives the same answer. Three parts:

  select(plays, since, until, P)   which missed plays are worth a clip: scores, lead changes and ties, the basket that
                                   ends a run, the grader's big plays and graded value (value2: base x leverage x
                                   stakes). At most P.max_plays, under P.cap_s of screen time, in story order.
  plan(sel, ...)                   a SOURCE for each clip, in Nick's order of preference: (a) "rewind", the hidden stick
                                   sent back behind live to the play's start; (b) "reel", YouTube TV's Key plays reel
                                   (PR 382's driver, `redzone/tuners/yttv_keyplays.py`, not merged); (c) "moments",
                                   Prime Key Moments (no driver yet). Then when each clip airs, the gaps between them,
                                   and the hidden prep before the first. A clip that no source can show on time is
                                   dropped; a package worth less than P.min_value is no package.
  recap_line(plays, ...)           one templated line from feed data ("Since Q2 5:10: 3 plays, NO 10-0 run"), never an
                                   AI writer (AI recaps swap facts; outside research, phase 2 plan 2C item 5).

Each clip starts at its play's START ("queue it to the snap"), never mid-play: football at the snap (the grader's action
row covers the 7 s before its stamp), basketball at the event that gave the scoring team the ball (the row before the
score, at most a shot clock back), less P.lead_s. The live join after the package is NOT a clip: it is an ordinary PARK
landing at a dead ball, under Rule #1 and the frozen grader (redzone/noggin/park_catchup.py).

Times are grid seconds of the PARK show (park_show.FactsFeed): grid second p is what a stream at the show's edge shows
at wall second base + p; the live edge is `delay` (park.show.show_delay) ahead, so frame p first aired at grid p - delay.

Never 3 minutes late: every clip second airs less than P.max_age_s after it first aired. Its default is
catchup.HARD_CAP_S (Nick, Nov 9: "pls dont be 3 or more min late on the highlight"); a longer recap is a what-if only.

UNKNOWN until measured on the TV (each is a field here, never a constant):
  * rewind reach: 175 s behind live measured (Nov 9: 5 seeks 25 s after a tune, one channel, docs/handoff-2026-11-09-
    account.md 8.1). Beyond it unproven. Outside research: YouTube TV rewinds only to where the stick started watching
    unless the game is in the library (Nick's call #3); yttv_seek reads a scrub bar that runs to the program start.
    P.rewind_rule picks the model: "measured" (175 s only, the default), "watch" (also back to the stick's own tune),
    "library" (the whole game).
  * seek prep: 14.4-26.2 s for 30-180 s back (same test): about 12 + 0.079 s per second back. Deeper: extrapolated.
  * a re-seek between two clips while the replay is on screen: never timed (prior 8 s).
  * Key plays reel: one read put its newest row about 12 min behind live (Nov 7, PR 382); about 10 s a play
    (measured); its open time and whether a channel shows rows at all (ESPN: unavailable; Big Ten: spoiler mode;
    ACC Network: rows) are unknown.
  * Prime Key Moments / Rapid Recap: no driver and no measurement; modelled only (priors).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields

from redzone.noggin.catchup import HARD_CAP_S

CLIP_SOURCES = ("rewind", "reel", "moments")          # Nick's order of preference
REWIND_RULES = ("measured", "watch", "library")


@dataclass(frozen=True)
class PackParams:
    """The package's numbers (seconds unless named). `park.catchup.<name>` in config; each default is a measurement or a
    named prior (the module docstring says which)."""
    max_plays: int = 5              # Nick: "like 4 or 5 plays back to back"
    min_plays: int = 1              # one missed touchdown queued to the snap is still a catch-up (RedZone's own move)
    cap_s: float = 120.0            # screen time of the whole package: Prime's Rapid Recap is "two minutes or less"
    min_worth: float = 0.3          # a play worth less than this never gets a clip
    min_value: float = 0.8          # a package worth less than this in all is not worth leaving live play for
    lead_s: float = 2.0             # a clip starts this long before its play's start (the read lands within about 1 s)
    tail_s: float = 3.0             # ...and runs this long past the result
    possession_s: float = 24.0      # basketball: a play starts at most a shot clock before its result
    snap_s: float = 7.0             # football: the grader's action row covers the 7 s before its stamp
    merge_s: float = 12.0           # two rewind clips this close play straight through (no seek, no gap)...
    seg_max_s: float = 30.0         # ...while what plays through stays this short
    big_w: float = 2.0              # the grader's must-see weight counts this much more than graded value
    lead_w: float = 1.0             # a lead change (times the play's leverage)
    tie_w: float = 0.5              # a tie
    run_w: float = 1.0              # the score that makes a run of run_pts unanswered
    run_pts: int = 8
    max_age_s: float = HARD_CAP_S   # every clip second airs less than this after it first aired (Nick, Nov 9)
    delay_s: float = 35.0           # the show's own delay behind live (park.show.show_delay)
    # (a) rewind on the hidden stick
    rewind_rule: str = "measured"   # measured | watch | library (see the docstring)
    reach_s: float = 175.0          # MEASURED: the deepest seek read back, behind live (Nov 9)
    seek_base_s: float = 12.0       # MEASURED fit: prep = seek_base_s + seek_per_s x seconds behind live (14.4-26.2 s)
    seek_per_s: float = 0.079
    seek_gap_s: float = 8.0         # UNKNOWN prior: a re-seek between two clips while the replay is on screen
    # (b) YouTube TV Key plays reel (PR 382)
    reel: bool = False              # off: the driver is not merged and the reel's rows are channel-dependent
    reel_lag_s: float = 720.0       # ONE READ (Nov 7): the newest row was about 12 min behind live
    reel_play_s: float = 10.0       # MEASURED: the reel plays about 10 s a play
    reel_open_s: float = 20.0       # UNKNOWN prior: panel open, Key plays tab, row found and selected
    reel_gap_s: float = 8.0         # UNKNOWN prior: the next selected row when the reel would play others in between
    # (c) Prime Key Moments
    moments: bool = False           # off: no driver
    moments_lag_s: float = 60.0     # UNKNOWN prior
    moments_play_s: float = 12.0    # UNKNOWN prior
    moments_open_s: float = 20.0    # UNKNOWN prior
    moments_gap_s: float = 6.0      # UNKNOWN prior

    @classmethod
    def from_config(cls, get: Callable[..., object], prefix: str = "park.catchup.") -> PackParams:
        """Each field from `<prefix><name>`, or its default; a value of the wrong kind keeps the default."""
        kw = {}
        for f in fields(cls):
            d, v = f.default, get(prefix + f.name, f.default)
            if isinstance(d, bool):
                kw[f.name] = v if isinstance(v, bool) else d
            elif isinstance(d, str):
                kw[f.name] = v if isinstance(v, str) and (f.name != "rewind_rule" or v in REWIND_RULES) else d
            else:
                kw[f.name] = type(d)(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        return cls(**kw)


@dataclass(frozen=True)
class Play:
    """One play of a game as the feed reported it, on the show's grid."""
    game: int
    at: float                  # the result: the feed stamp moved onto the grid
    start: float               # the play's start (the snap; the event that gave the ball): a clip never starts later
    points: int = 0
    lead_change: bool = False
    tie: bool = False
    run: int = 0               # unanswered points of the run this play is part of (0 = none)
    big: float = 0.0           # the grader's must-see weight (park_core Arr.mom)
    value: float = 0.0         # the grader's graded value (value2, park_core Arr.v2)
    lev: float = 1.0           # leverage at the play
    text: str = ""
    period: int = 0
    clock: str = ""


@dataclass(frozen=True)
class Clip:
    """One stretch of the package on screen."""
    plays: tuple               # the plays it shows (two close rewind clips play through as one)
    source: str                # rewind | reel | moments
    lo: float                  # first grid second it shows (the first play's start less lead_s)
    hi: float                  # last grid second it shows
    air: float                 # grid second it is on screen
    secs: float                # seconds on screen
    gap: float                 # seconds on screen before it (a re-seek or the next row), 0 for the first


@dataclass(frozen=True)
class Package:
    game: int
    clips: tuple
    prep_s: float              # hidden, before the leave: the first clip's seek (or the reel's open)
    screen_s: float            # first clip up to the last clip's end, gaps included
    worth: float
    dropped: tuple = ()        # (play, why) the selection wanted and no source could show in time
    line: str = ""

    @property
    def plays(self) -> list:
        return [p for c in self.clips for p in c.plays]

    @property
    def sources(self) -> dict:
        out: dict = {}
        for c in self.clips:
            out[c.source] = out.get(c.source, 0) + len(c.plays)
        return out


@dataclass(frozen=True)
class Stick:
    """What the plan knows of the hidden stick and the game's app."""
    app: str = "yttv"          # yttv | prime
    on_since: float = -math.inf  # grid second this stick tuned to this game (for the "watch" rewind rule)


# ---------------------------------------------------------------------------------------------- selection
def worth(p: Play, P: PackParams) -> float:
    """What a clip of this play is worth: graded value, the grader's must-see weight, and the story (lead change, tie,
    a run) weighted by leverage."""
    story = P.lead_w * p.lead_change + P.tie_w * p.tie + P.run_w * (p.run >= P.run_pts)
    return max(0.0, p.value) + P.big_w * p.big + max(1.0, p.lev) * story


def clip_span(p: Play, P: PackParams) -> tuple[float, float]:
    """The grid seconds a clip of this play shows: from its start (less the lead) to its result (plus the tail)."""
    return p.start - P.lead_s, p.at + P.tail_s


def _segments(plays: Sequence[Play], P: PackParams, merge: bool = True) -> list[list[Play]]:
    """Plays in story order, grouped where two clips are close enough to play straight through."""
    out: list[list[Play]] = []
    for p in sorted(plays, key=lambda x: (x.at, x.start)):
        lo, hi = clip_span(p, P)
        if (out and merge and lo - clip_span(out[-1][-1], P)[1] <= P.merge_s
                and hi - clip_span(out[-1][0], P)[0] <= P.seg_max_s):
            out[-1].append(p)
        else:
            out.append([p])
    return out


def screen_len(plays: Sequence[Play], P: PackParams, gap: float | None = None) -> float:
    """Screen seconds of these plays as rewind clips (close ones played through), with a re-seek between groups."""
    segs = _segments(plays, P)
    g = P.seek_gap_s if gap is None else gap
    return sum(clip_span(s[-1], P)[1] - clip_span(s[0], P)[0] for s in segs) + g * max(0, len(segs) - 1)


def select(plays: Sequence[Play], since: float, until: float, P: PackParams) -> list[Play]:
    """The missed plays worth a clip: result after `since` (the last second the viewer saw) and known by `until` (the
    feed's edge). The most worth first, while the package stays under P.cap_s and P.max_plays; story order out."""
    cands = [p for p in plays if since < p.at <= until and p.start <= p.at and worth(p, P) >= P.min_worth]
    cands.sort(key=lambda p: (-worth(p, P), p.at))
    chosen: list[Play] = []
    for p in cands:
        if len(chosen) >= P.max_plays:
            break
        if screen_len(chosen + [p], P) <= P.cap_s:
            chosen.append(p)
    return sorted(chosen, key=lambda p: p.at)


# ---------------------------------------------------------------------------------------------- sources and plan
def rewind_floor(seek_at: float, s: Stick, P: PackParams) -> float:
    """The earliest grid second the hidden stick can be sent back to by a seek at grid second `seek_at`."""
    if P.rewind_rule == "library":
        return -math.inf
    measured = seek_at + P.delay_s - P.reach_s
    if P.rewind_rule == "watch":
        return min(measured, s.on_since + P.delay_s)
    return measured


def age_ok(lo: float, air: float, P: PackParams) -> bool:
    """Never P.max_age_s late: the clip's first second (its oldest) airs less than that after it first aired."""
    return air + P.delay_s - lo < P.max_age_s


def _can(src: str, p: Play, lo: float, seek_at: float, s: Stick, P: PackParams) -> bool:
    """Can this source show this play when its clip is set up at grid second `seek_at`?"""
    if src == "rewind":
        return s.app == "yttv" and lo >= rewind_floor(seek_at, s, P)
    if src == "reel":
        return P.reel and s.app == "yttv" and p.at - P.delay_s + P.reel_lag_s <= seek_at
    return P.moments and s.app == "prime" and p.at - P.delay_s + P.moments_lag_s <= seek_at


def _source_secs(src: str, seg: list[Play], P: PackParams) -> float:
    if src == "rewind":
        return clip_span(seg[-1], P)[1] - clip_span(seg[0], P)[0]
    per = P.reel_play_s if src == "reel" else P.moments_play_s
    return per * len(seg)


def _gap(prev: str, src: str, P: PackParams) -> float:
    if prev != src:                                   # a source switch on screen: its whole open again
        return P.reel_open_s if src == "reel" else P.moments_open_s if src == "moments" else P.seek_gap_s
    return P.seek_gap_s if src == "rewind" else P.reel_gap_s if src == "reel" else P.moments_gap_s


def _prep(src: str, lo: float, now: float, P: PackParams) -> float:
    if src == "rewind":
        return P.seek_base_s + P.seek_per_s * max(0.0, now + P.delay_s - lo)
    return P.reel_open_s if src == "reel" else P.moments_open_s


def _schedule(segs: list[list[Play]], now: float, air0: float, s: Stick, P: PackParams) -> tuple[list[Clip], float, list]:
    """Each group's source (first that works, in Nick's order), when it airs, and the hidden prep; (clips, prep, misses).
    A group no source can show on time is a miss (it is not scheduled; the caller drops its least-worth play)."""
    clips: list[Clip] = []
    misses: list = []
    prep = 0.0
    t = air0
    queue = [list(x) for x in segs]
    while queue:
        seg = queue.pop(0)
        lo, hi = clip_span(seg[0], P)[0], clip_span(seg[-1], P)[1]
        prev = clips[-1].source if clips else ""
        chosen = None
        for src in CLIP_SOURCES:
            if src != "rewind" and len(seg) > 1:
                continue                              # a reel or moments list plays one row at a time
            gap = _gap(prev, src, P) if clips else 0.0
            seek_at = now if not clips else t         # the first is set up hidden, now; the rest on screen, in the gap
            air = t + gap
            if _can(src, seg[0], lo, seek_at, s, P) and age_ok(lo, air, P):
                chosen = (src, gap, air)
                break
        if chosen is None and len(seg) > 1:
            queue[:0] = [[p] for p in seg]            # played through it cannot go: one play at a time, other sources
            continue
        if chosen is None:
            misses.append(seg)
            continue
        src, gap, air = chosen
        secs = _source_secs(src, seg, P)
        if not clips:
            prep = _prep(src, lo, now, P)
        clips.append(Clip(tuple(seg), src, lo, hi, air, secs, gap))
        t = air + secs
    return clips, prep, misses


def plan(sel: Sequence[Play], now: float, s: Stick, P: PackParams, *, wait_s: float = 0.0, flip_s: float = 3.0,
         game: int | None = None) -> Package | None:
    """The package for these selected plays, set up from grid second `now`: the first clip's prep runs hidden, the leave
    comes at most `wait_s` after it, and the flip to the replay takes `flip_s`. Plays that cannot be shown on time go,
    least worth first, until the rest fit (P.cap_s, sources, never P.max_age_s late). None: nothing worth it is left."""
    keep = sorted(sel, key=lambda p: p.at)
    dropped: list = []
    while keep:
        rewindable = s.app == "yttv"
        segs = _segments(keep, P, merge=rewindable)
        first_lo = clip_span(segs[0][0], P)[0]
        src0 = next((x for x in CLIP_SOURCES if _can(x, segs[0][0], first_lo, now, s, P)), "rewind")
        air0 = now + _prep(src0, first_lo, now, P) + wait_s + flip_s
        clips, prep, misses = _schedule(segs, now, air0, s, P)
        if not clips and not misses:
            break
        screen = (clips[-1].air + clips[-1].secs - clips[0].air) if clips else 0.0
        if not misses and screen <= P.cap_s and len(keep) <= P.max_plays:
            total = sum(worth(p, P) for p in keep)
            if len(keep) < P.min_plays or total < P.min_value:
                return None
            return Package(game if game is not None else keep[0].game, tuple(clips), prep, screen, total, tuple(dropped))
        pool = [p for seg in misses for p in seg] if misses else keep
        worst = min(pool, key=lambda p: (worth(p, P), -p.at))
        dropped.append((worst, "no source in time" if misses else "over the cap"))
        keep.remove(worst)
    return None


# ---------------------------------------------------------------------------------------------- the recap line
def recap_line(plays: Sequence[Play], since_label: str, P: PackParams) -> str:
    """One templated line from feed data only: how many plays, and the biggest run."""
    n = len(plays)
    top = max((p.run for p in plays), default=0)
    run = f", a {top}-0 run" if top >= P.run_pts else ""
    leads = sum(1 for p in plays if p.lead_change)
    lead = f", {leads} lead change{'s' if leads != 1 else ''}" if leads else ""
    return f"Since {since_label}: {n} play{'s' if n != 1 else ''}{run}{lead}"
