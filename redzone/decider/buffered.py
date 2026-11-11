"""[buffered jump] Nick, Nov 7 20:45 ET: "you need to go play by play moment from moment / several jumps across a
minute (5-10) whatever makes sense". The overseer's fix (Nov 8 01:50Z): run BOTH sticks ~60-90 s behind live.

Why: Nick Nov 7 20:45 ET asked for several jumps a minute; running both sticks ~60-90 s behind live makes the jump land before its snap (overseer fix, Nov 8).

Why it works: the play-by-play runs ~10-30 s behind real life and YouTube TV's live edge runs 34.9 s behind it, so
at the live edge the data never covers the picture and the jump gates mostly wait (live tonight: 715 "ready_not_dead"
holds against 2 jumps). A stick scrubbed 60-90 s behind live shows real time now - (34.9 + behind): every snap,
whistle and big play on screen for the next ~60 s is already in the data. Each cut point is KNOWN in advance, like an
editor with a one-minute look-ahead.

The editor (cut_now, pure, shared by the decider and scripts/buffered_jump_sim.py):
  * leave V (the stick on screen) only while its picture is surely dead for the whole flip (Rule #1 both ways);
  * land on H (the hidden stick, still playing behind live: the sticks' HDMI-CEC is off, so the stick we leave keeps
    playing) only on a dead ball, and only when H's next snap shows within lead_max_s of the picture coming up:
    the viewer lands on the line-up, not in a 30 s huddle;
  * stay when V's own next snap comes first (play by play: whichever game snaps next is on screen);
  * a long dead stretch on V (a TV timeout, a punt, a score) is left for H right away (H still lands dead).
Rotation (rotate_due): the hidden stick re-tunes to another game (tune + scrub, ~55 s) when its game is in a long
stoppage or the pair has been on for rotate_every_s. Every time is real time (feed wallclock); the screen shows real
time `t - mu` at wall time t, mu = live edge + label-read behind, give or take margin_sigmas x sigma.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from redzone.decider.cuts import DeadTracker

BJ_DEFAULTS = {
    "enabled": False,
    "leagues": ["nfl", "cfb", "nba", "cbb"],   # every entry has a wallclock; basketball's dead balls are fouls,
                                 # free throws, timeouts, violations, reviews, period ends (cuts.after_kind)
    "behind_s": 75.0,            # scrub depth asked of each fresh tune (join landing 16.8 k + 28.5: k=3 -> ~79 s)
    "min_behind_s": 45.0,        # a stick less behind than this (read off the label) is not a buffered picture
    "flip_s": 3.2,               # input flip to the hidden stick (the slowest usual)
    "flip_fast_s": 2.0,          # ...the fastest: the picture can come up this soon
    "margin_sigmas": 2.0,        # delay doubt each side, in sigmas
    "lead_min_s": 2.0,           # the picture comes up at least this long before the target's snap
    "lead_max_s": 8.0,           # ...and at most this long past the delay margin (else wait for a later moment)
    "long_dead_s": 45.0,         # V's next snap further away than this: leave now for any dead-ball landing
    "park": True,                # [Nick 21:05 via overseer] the hidden stick PAUSES just before its next snap and every dead
                                 # ball on screen cuts to it (the flip un-pauses it): throughput first
    "park_max_age_s": 45.0,      # a park older than this is rolled on the hidden stick and re-parked at the next snap
    "max_delay_s": 270.0,        # each park adds its pause to the screen delay: past this the stick re-tunes (rotation)
    "eager": False,              # cut at every shared dead ball (more cuts, many land in a huddle) instead of snap-timed
    "min_plays": 1,              # plays V shows before a cut that isn't a long dead stretch
    "rotate_every_s": 600.0,     # the hidden stick re-tunes to another game after the pair has been on this long
    "rotate_dead_s": 120.0,       # ...or right away when its game's next snap is this far off (a break)
    "park_guard_s": 1.5,         # cuts.landing calls a play on screen from 1 s before its snap: park a little earlier
    "stuck_s": 60.0,             # a standby not ready (nothing running on it) this long is let go for the next pick
    "prep_s": 60.0,              # tune + scrub + label read before a re-tuned hidden stick is ready (sim only)
}


def cfg_get(cfg, key: str):
    v = cfg.get(f"buffered_jump.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return BJ_DEFAULTS[key] if v is None else v


@dataclass
class Pic:
    """One stick's picture: its game's dead windows and how far the screen runs behind real life."""
    dt: DeadTracker
    mu: float          # screen delay: live edge + behind live (label read)
    sigma: float       # its doubt (1 sd)
    gid: str = ""
    walk: tuple | None = None   # [landing bars] cuts.walk_params: basketball may land in the walk after a shot
    leave_walk: tuple | None = None   # [slate] the same walk, as a way to LEAVE: set only at low leverage (Rule #1)


@dataclass
class Cut:
    go: bool
    why: str
    info: dict = field(default_factory=dict)


def _m(p: Pic, k: float) -> float:
    return k * max(0.0, p.sigma)


def dead_through(p: Pic, now: float, until_wall: float, k: float) -> bool:
    """Is p's picture surely dead from now until wall time until_wall?"""
    m = _m(p, k)
    u = p.dt.dead_until(now - p.mu - m)
    return u is not None and u >= until_wall - p.mu + m


def leaves(p: Pic, now: float, until_wall: float, k: float) -> bool:
    """[slate] May the picture be left from now until until_wall: a dead ball, or (leave_walk set, low leverage only) the
    walk up court after a shot. Transition plays are never walks, so this never leaves one."""
    if dead_through(p, now, until_wall, k):
        return True
    if p.leave_walk is None:
        return False
    m = _m(p, k)
    return p.dt.landing(now - p.mu - m, until_wall - p.mu + m, walk=p.leave_walk).state == "dead"


def _here_snap(p: Pic, now: float) -> float:
    """Seconds until V's next action on screen, counting the walk's end when we would be leaving in a walk."""
    x = now - p.mu
    w = _closed_window(p, x) if p.leave_walk is not None else None
    return max(next_snap_in(p, now), w.end - x if w is not None and w.kind == "walk" else 0.0)


def next_snap_in(p: Pic, at: float) -> float:
    """Seconds from wall time `at` until p's next restart shows on screen (0: play is live there now). The logged
    snap when the data has it (always, 60+ s behind live), else the window's usual length."""
    x = at - p.mu
    return max(0.0, p.dt.resume_eta(x) - x)


def lands(p: Pic, now: float, flip_fast: float, flip: float, lead_min: float, k: float) -> tuple[bool, float]:
    """Does p's picture come up on a dead ball (anywhere from the fastest to the slowest flip, plus lead_min of
    line-up before its snap), and how long after it comes up (slowest flip) does the snap show?"""
    if not math.isfinite(flip):   # an invalid cost estimate is no evidence of a safe landing
        return False, -1.0
    m = _m(p, k)
    x0 = now + flip_fast - p.mu - m
    x1 = now + flip + lead_min - p.mu + m
    ok = p.dt.landing(x0, x1).state == "dead"
    if not ok and p.walk:   # [landing bars] the walk up court after a shot: no line-up to reach, so no lead_min
        ok = p.dt.landing(x0, x1 - lead_min, walk=p.walk).state == "dead"
    return ok, next_snap_in(p, now + flip)


def cut_now(v: Pic, h: Pic, now: float, cfg=None, plays_seen: int = 0, land_s: float = 0.0) -> Cut:
    """The editor's call for this instant: cut from V (on screen) to H (hidden, ready, playing behind live)?
    land_s: the slowest arrival the flip will check H with (the measured cost, when it is above flip_s)."""
    k = float(cfg_get(cfg, "margin_sigmas"))
    flip, fast = float(cfg_get(cfg, "flip_s")), float(cfg_get(cfg, "flip_fast_s"))
    lead_min, lead_max = float(cfg_get(cfg, "lead_min_s")), float(cfg_get(cfg, "lead_max_s"))
    if not leaves(v, now, now + flip, k):
        return Cut(False, "play_on_screen")
    ok, h_snap = lands(h, now, fast, max(flip, land_s), lead_min, k)
    here = _here_snap(v, now)
    info = {"h_snap_s": round(h_snap, 1), "here_snap_s": round(here, 1), "plays_seen": plays_seen}
    if not ok:
        return Cut(False, "lands_live", info)
    long_dead = here >= float(cfg_get(cfg, "long_dead_s"))
    if long_dead and h_snap + flip < here and h_snap < float(cfg_get(cfg, "long_dead_s")):
        return Cut(True, "long_dead", info)   # V is in a break and H snaps sooner (never from one break into another)
    if plays_seen < int(cfg_get(cfg, "min_plays")):
        return Cut(False, "min_plays", info)
    if bool(cfg_get(cfg, "eager")):
        return Cut(True, "eager", info)   # every dead ball on both pictures (huddle to huddle)
    if here <= h_snap + flip:
        return Cut(False, "own_snap_first", info)
    if h_snap > lead_max + _m(h, k):
        return Cut(False, "wait_for_snap", info)   # the earliest safe landing is a margin before the snap
    return Cut(True, "snap_next", info)


def _closed_window(p: Pic, x: float):
    """The dead window (or, with p.walk, the walk after a shot) covering real time x if the data already logged its
    restart (the snap), else None."""
    for w in reversed(p.dt.entry_windows(p.walk)):
        if w.start > x:
            continue
        return w if w.end is not None and w.end > x else None
    return None


def park_lead_s(h: Pic, cfg=None) -> float:
    """How long before its snap the hidden stick's picture is paused: the flip, the delay margin, the line-up."""
    return (float(cfg_get(cfg, "flip_s")) + _m(h, float(cfg_get(cfg, "margin_sigmas")))
            + float(cfg_get(cfg, "lead_min_s")) + float(cfg_get(cfg, "park_guard_s")))


def park_in(h: Pic, now: float, cfg=None) -> float | None:
    """Seconds from now until the hidden stick should pause (its picture park_lead_s before its next LOGGED snap),
    or None: no logged snap ahead in a dead window (play on screen, or the restart isn't in the data yet), or the
    park point already passed for this window."""
    x = now - h.mu
    w = _closed_window(h, x)
    if w is None:
        return None
    m = _m(h, float(cfg_get(cfg, "margin_sigmas")))
    if w.kind == "walk":
        # the latest pause the whole doubt band and the flip still fit inside the walk (no line-up lead to keep)
        flip, fast = float(cfg_get(cfg, "flip_s")), float(cfg_get(cfg, "flip_fast_s"))
        # [P1-REVIEW-07] park_guard_s: key latency on the PAUSE, or it would land exactly at the walk's end and fail lands()
        pause_at = w.end - flip - m - float(cfg_get(cfg, "park_guard_s"))
        if pause_at + fast - m < w.start:
            return None
        wait = pause_at - x
        return wait if wait >= -0.5 else None
    wait = (w.end - park_lead_s(h, cfg)) - x
    if w.end - park_lead_s(h, cfg) < w.start + m:
        return None   # the window is too short to park inside it with the margin
    return wait if wait >= -0.5 else None


def frozen(h: Pic, now: float, paused_at: float) -> Pic:
    """The hidden stick paused at wall time paused_at: its picture stopped at paused_at - mu, so right now it runs
    mu + (now - paused_at) behind real life (PLAY rolls it on from there)."""
    return Pic(h.dt, h.mu + max(0.0, now - paused_at), h.sigma, h.gid, h.walk, h.leave_walk)


def cut_parked(v: Pic, h_parked: Pic, now: float, cfg=None, plays_seen: int = 0, land_s: float = 0.0) -> Cut:
    """[park] Throughput: cut at every dead ball on screen (after min_plays, or in a long dead stretch) to the hidden
    stick paused just before its snap. Rule #1 both ways: V surely dead through the flip, H lands dead."""
    k = float(cfg_get(cfg, "margin_sigmas"))
    flip, fast = float(cfg_get(cfg, "flip_s")), float(cfg_get(cfg, "flip_fast_s"))
    if not leaves(v, now, now + flip, k):
        return Cut(False, "play_on_screen")
    ok, h_snap = lands(h_parked, now, fast, max(flip, land_s), float(cfg_get(cfg, "lead_min_s")), k)
    here = _here_snap(v, now)
    info = {"h_snap_s": round(h_snap, 1), "here_snap_s": round(here, 1), "plays_seen": plays_seen, "parked": True}
    if not ok:
        return Cut(False, "park_lands_live", info)
    if plays_seen < int(cfg_get(cfg, "min_plays")) and here < float(cfg_get(cfg, "long_dead_s")):
        return Cut(False, "min_plays", info)
    return Cut(True, "parked_snap", info)


def rotate_due(h: Pic | None, now: float, pair_s: float, cfg=None) -> str | None:
    """Should the hidden stick re-tune to another game? None: keep it (ping-pong with the stick on screen)."""
    if h is None:
        return "empty"
    if next_snap_in(h, now) >= float(cfg_get(cfg, "rotate_dead_s")):
        return "hidden_in_break"
    if h.mu > float(cfg_get(cfg, "max_delay_s")):
        return "too_far_behind"
    if pair_s >= float(cfg_get(cfg, "rotate_every_s")):
        return "pair_age"
    return None
