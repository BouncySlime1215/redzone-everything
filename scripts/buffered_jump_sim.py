"""[buffered jump] Jumps per minute, plays shown and Rule #1 on recorded NFL slates (data/archive, ESPN wallclocks).

Two sticks, both scrubbed behind live (screen delay = edge + behind), the stick we leave keeps playing (HDMI-CEC
off). The editor (redzone/decider/buffered.py cut_now) is called every `step` seconds on what the play-by-play
holds at that moment (each game's entries arrive feed_lag after their wallclock); the hidden stick re-tunes per
rotate_due (unready prep_s). Truth: the same entries, all of them, at the TRUE screen delay (belief + a per-tune
error of err_sd). Counts a mid-play JOIN when the true picture at the landing is inside a play (snap - 10 s ..
whistle + 3 s), a mid-play LEAVE when a play is on screen during the flip. Basketball: a landing inside the walk up
court right after a made or missed shot (Nick, Nov 8; the same DeadTracker.walks the landing uses) is allowed, and is
counted as joins_walk, not joins_mid_play (which stays the strict Rule #1 counter).

  python scripts/buffered_jump_sim.py                      # every Sunday slate in data/archive/nfl
  python scripts/buffered_jump_sim.py --sigma 4 --lag 30   # stated sigma (4 s floor), slower feed
  python scripts/buffered_jump_sim.py --json out.json
  python scripts/buffered_jump_sim.py --maxrate --mr-sticks 3 --mr-prep 8    # the rate ceiling model (see run_maxrate)
  python scripts/buffered_jump_sim.py --maxrate-sweep --archive data/archive/nfl --json out.json   # the whole P1-MAXRATE table
  python scripts/buffered_jump_sim.py --maxrate --mr-mix prime-heavy --league nba   # per-app prep on a real app mix (WP-11)
  python scripts/buffered_jump_sim.py --maxrate-mix --archive data/archive/nba --league nba --json mix.json   # the mix table
  --split fit|train|valid reads dev nights only (redzone/lab/splits.py); `test` (the locked set) is refused.
  --maxrate --mr-policy arbiter   the must-show floor picks (Arb knobs; show_scorecard.py --policy arbiter has the table)

Numbers only; no play text leaves this script.
Why: Tests the buffered-jump pass bar (jumps per minute, Rule #1) on recorded slates (docstring). The mix model (WP-11)
exists because a flat prep time hides that a Prime tune costs ~45 s and a YouTube TV tune ~4 s, and half a real slate is Prime.
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redzone.decider import buffered as bj  # noqa: E402
from redzone.noggin.gamequeue import Params as QueueParams  # noqa: E402
from redzone.noggin import arbiter as arb_rules  # noqa: E402
from redzone.decider.cuts import AFTER_PLAY_S, PLAY_S, DeadTracker, _is_action, walk_params  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources.espn import _kind, _ts, flatten_plays  # noqa: E402

EDGE_S = 34.9
LG = League.NFL   # --league
# [Nick 22:17 Nov 7 pass bar] break windows that cut to commercials, and how long after the whistle the ads start /
# how long before the restart they end (an assumption: the network stays on the field ~10 s each side)
AD_KINDS = {"tv_timeout", "media_timeout", "timeout", "timeout_long", "two_minute", "end_period", "halftime",
            "intermission", "review"}
AD_EDGE_S = 10.0
PASS_JUMPS, PASS_WINDOW_S = 75, 600.0


def load_game(path: str, lg: League | None = None, d: dict | None = None):
    lg = lg or LG
    d = json.load(open(path)) if d is None else d
    out = []
    for p in flatten_plays(lg, d):
        t = _ts(p.get("wallclock"))
        if t is None or not p.get("id"):
            continue
        out.append((t, str(p["id"]), _kind(lg, p), p.get("text") or "", (p.get("type") or {}).get("text") or "",
                    ((p.get("period") or {}).get("number"))))
    out.sort()
    return out


def dev_game_ids(archive: str, split: str) -> set[str] | None:
    """Game ids of one dev split of this archive (None = every game, today's behaviour). The locked `test` set is
    refused: nothing here may read it (CLAUDE.md release gate)."""
    if split == "all":
        return None
    if split == "test":
        raise SystemExit("--split test is the LOCKED release set: this sim never reads it")
    from redzone.lab import splits
    nights = splits.nights_for(archive, split)
    by_night = splits.archive_nights(archive)
    return {g for n in (nights or ()) for g in by_night.get(n, ())}


def slates(archive: str, min_games: int, only: set[str] | None = None):
    """Groups of games that overlap in time (a Sunday window), by scoreboard date. `only` keeps those game ids."""
    by_day = defaultdict(list)
    for f in glob.glob(os.path.join(archive, "summary_*.json")):
        if only is not None and os.path.basename(f)[8:-5] not in only:
            continue
        try:
            g = load_game(f)
        except (OSError, ValueError):
            continue
        if len(g) < 60:
            continue
        gid = os.path.basename(f)[8:-5]
        by_day[int(g[0][0] // 86400)].append((gid, g))
    for day, games in sorted(by_day.items()):
        games.sort(key=lambda z: z[1][0][0])
        # one slate = games starting within 30 min of each other
        cur = []
        for gid, g in games:
            if cur and g[0][0] - cur[0][1][0][0] > 1800:
                if len(cur) >= min_games:
                    yield day, cur
                cur = []
            cur.append((gid, g))
        if len(cur) >= min_games:
            yield day, cur


def truth_tracker(plays, lg: League | None = None) -> DeadTracker:
    dt = DeadTracker(lg or LG)
    for t, pid, kind, text, tt, per in plays:
        dt.add(pid, t, t, kind, text, tt, None, per)
    dt.seen(1e12, 0.0)
    return dt


def play_spans(plays, lg: League | None = None) -> list[tuple[float, float]]:
    lg = lg or LG
    dt = truth_tracker(plays, lg)
    if lg.base == League.NBA:
        # basketball: the ball is live between dead windows, so a "play" is each live stretch (whistle to whistle)
        out, t = [], plays[0][0]
        for w in dt.windows():
            if w.start > t:
                out.append((t, w.start))
            t = max(t, w.end if w.end is not None else plays[-1][0])
        return out
    out = []
    for m in dt._order():
        if _is_action(lg, m):
            out.append((m.lo - PLAY_S[lg], m.hi + AFTER_PLAY_S))
    return out


def in_play(spans, a: float, b: float) -> bool:
    return any(s < b and a < e for s, e in spans)


def walk_spans(plays, walk, lg: League | None = None) -> list[tuple[float, float]]:
    """[Nick, Nov 8] Truth's walks after a made or missed shot: the windows a basketball landing may use."""
    if not walk:
        return []
    return [(w.start, w.end) for w in truth_tracker(plays, lg).walks(walk)]


def in_walk(walks, a: float, b: float) -> bool:
    return any(s <= a and b <= e for s, e in walks)


def ad_windows(plays, lg: League | None = None) -> list[tuple[float, float]]:
    """Real-time spans that are commercials on TV: a break window's inside, AD_EDGE_S in from each end."""
    out = []
    for w in truth_tracker(plays, lg).windows():
        if w.kind in AD_KINDS and w.end is not None and w.end - w.start > 2 * AD_EDGE_S:
            out.append((w.start + AD_EDGE_S, w.end - AD_EDGE_S))
    return out


def pass_bar(segs, spans, ads, cut_at, mid_at, t0: float, t_end: float) -> dict:
    """[Nick 22:15/22:17 Nov 7] Each whole 10-minute window of the slate: PASS = at least 75 on-screen jumps AND 0
    commercial seconds on screen AND every play shown in full (none cut off at a cut) AND 0 mid-play joins/leaves."""
    cut_plays = []   # wall time of each cut that cut a play off (at its entry or at its exit)
    for gid, a, b, mu in segs:
        x0, x1 = a - mu, b - mu
        for s, e in spans[gid]:
            if s < x1 and x0 < e:
                if s < x0 and a > t0:
                    cut_plays.append(a)
                if e > x1 and b < t_end:
                    cut_plays.append(b)
    wins, w0 = [], t0
    while w0 + PASS_WINDOW_S <= t_end:
        w1 = w0 + PASS_WINDOW_S
        ad_s = 0.0
        for gid, a, b, mu in segs:
            a, b = max(a, w0), min(b, w1)
            if b > a:
                ad_s += sum(max(0.0, min(b - mu, e) - max(a - mu, s)) for s, e in ads[gid])
        jumps = sum(1 for t in cut_at if w0 <= t < w1)
        partial = sum(1 for t in cut_plays if w0 <= t < w1)
        mid = sum(1 for t in mid_at if w0 <= t < w1)
        ok = jumps >= PASS_JUMPS and ad_s == 0 and partial == 0 and mid == 0
        wins.append({"jumps": jumps, "ad_s": round(ad_s, 1), "partial_plays": partial, "mid_play": mid, "pass": ok})
        w0 = w1
    return {"windows": len(wins), "windows_pass": sum(w["pass"] for w in wins),
            "best_window_jumps": max((w["jumps"] for w in wins), default=0),
            "ad_s": round(sum(w["ad_s"] for w in wins), 1), "ad_free_windows": sum(w["ad_s"] == 0 for w in wins),
            "partial_plays": len(cut_plays), "window_rows": wins}


def run_slate(games, args, rng: random.Random, cfg) -> dict:
    lag = {gid: max(5.0, rng.gauss(args.lag, args.lag_sd)) for gid, _ in games}
    plays = dict(games)
    spans = {gid: play_spans(p) for gid, p in plays.items()}
    belief = {gid: DeadTracker(LG) for gid in plays}
    cursor = {gid: 0 for gid in plays}
    first = {gid: p[0][0] for gid, p in plays.items()}
    last = {gid: p[-1][0] for gid, p in plays.items()}
    t0 = max(sorted(first.values())[:2]) + 120
    t_end = sorted(last.values())[-2]   # while at least two games are on
    if t_end - t0 < 1800:
        return {}

    def depth() -> float:
        k = max(1, round((args.behind - 28.5) / 16.8))
        return 16.8 * k + 28.5

    def new_stick(gid, at_wall):
        b = depth()
        return {"gid": gid, "mu": EDGE_S + b, "err": rng.gauss(0.0, args.err_sd), "ready_at": at_wall, "paused_at": None}

    order = sorted(plays, key=lambda g: first[g])
    vis, hid = new_stick(order[0], t0), new_stick(order[1], t0)
    pair_since, landed_at, left_at = t0, t0, defaultdict(lambda: t0 - 300)
    cuts, joins_mid, joins_walk, leaves_mid, rot, snap_cuts, parks, expired = 0, 0, 0, 0, 0, 0, 0, 0
    reasons = defaultdict(int)
    segs = []   # (gid, wall0, wall1, mu_true)
    cut_at, mid_at = [], []   # wall times of each cut / each mid-play join or leave
    seg0 = t0
    flip = float(bj.cfg_get(cfg, "flip_s"))
    walk = walk_params(cfg, LG)   # basketball's walk up court after a shot, like the live decider's Pic
    walks = {gid: walk_spans(p, walk) for gid, p in plays.items()}
    t = t0
    while t < t_end:
        for gid, p in plays.items():   # the play-by-play as it stands at t
            i = cursor[gid]
            while i < len(p) and p[i][0] + lag[gid] <= t:
                w, pid, kind, text, tt, per = p[i]
                belief[gid].add(pid, w, w, kind, text, tt, None, per)
                i += 1
            cursor[gid] = i
            belief[gid].seen(t, lag[gid])
        V = bj.Pic(belief[vis["gid"]], vis["mu"], args.sigma, vis["gid"], walk)
        H = bj.Pic(belief[hid["gid"]], hid["mu"], args.sigma, hid["gid"], walk) if t >= hid["ready_at"] else None
        park = bool(bj.cfg_get(cfg, "park"))
        if H is not None and park:
            if hid["paused_at"] is None:
                pin = bj.park_in(H, t, cfg)
                if pin is not None and pin <= args.step:
                    hid["paused_at"] = t + max(0.0, pin)
                    parks += 1
            elif t - hid["paused_at"] > float(bj.cfg_get(cfg, "park_max_age_s")):
                hid["mu"] += t - hid["paused_at"]   # rolled on the hidden stick: re-parks at its next snap
                hid["paused_at"] = None
                expired += 1
        if H is not None and (not park or (hid["paused_at"] is not None and hid["paused_at"] <= t)):
            mu_v = vis["mu"] + vis["err"]
            seen = sum(1 for s, e in spans[vis["gid"]] if s >= landed_at - mu_v and e <= t - mu_v)
            if park:
                c = bj.cut_parked(V, bj.frozen(H, t, hid["paused_at"]), t, cfg, plays_seen=seen)
            else:
                c = bj.cut_now(V, H, t, cfg, plays_seen=seen)
            reasons[c.why] += 1
            if c.go:
                f = rng.uniform(float(bj.cfg_get(cfg, "flip_fast_s")), flip)
                if hid["paused_at"] is not None:   # PLAY at the cut: the pause is now part of its delay
                    hid["mu"] += t - hid["paused_at"]
                    hid["paused_at"] = None
                mu_h = hid["mu"] + hid["err"]
                if in_play(spans[vis["gid"]], t - mu_v, t + f - mu_v):
                    leaves_mid += 1
                    mid_at.append(t)
                if in_play(spans[hid["gid"]], t + f - mu_h, t + f + 0.5 - mu_h):
                    if in_walk(walks[hid["gid"]], t + f - mu_h, t + f + 0.5 - mu_h):
                        joins_walk += 1   # allowed: the walk up court right after a made or missed shot
                    else:
                        joins_mid += 1
                        mid_at.append(t)
                cut_at.append(t)
                segs.append((vis["gid"], seg0, t + f, mu_v))
                seg0 = t + f
                left_at[vis["gid"]] = t
                vis, hid = hid, vis
                landed_at = t + f
                cuts += 1
                snap_cuts += c.why in ("snap_next", "parked_snap")
                t += f
                continue
        why = bj.rotate_due(H, t, t - pair_since, cfg) if t >= hid["ready_at"] and hid["paused_at"] is None else None
        if why:
            # the rotation's next game: live now, not on a stick, longest away
            prep = float(bj.cfg_get(cfg, "prep_s"))
            pool = [g for g in plays if g not in (vis["gid"], hid["gid"])
                    and first[g] <= t - EDGE_S - depth() and last[g] > t + 600
                    and bj.next_snap_in(bj.Pic(belief[g], EDGE_S + depth(), args.sigma), t + prep)
                    < float(bj.cfg_get(cfg, "rotate_dead_s"))]   # round-robin, skipping a game in a break
            if pool:
                g = max(pool, key=lambda z: t - left_at[z])
                hid = new_stick(g, t + float(bj.cfg_get(cfg, "prep_s")))
                pair_since = hid["ready_at"]
                rot += 1
            else:
                pair_since = t
        t += args.step
    segs.append((vis["gid"], seg0, t_end, vis["mu"] + vis["err"]))
    shown = 0
    for gid, a, b, mu in segs:
        shown += sum(1 for s, e in spans[gid] if s >= a - mu and e <= b - mu)
    bar = pass_bar(segs, spans, {g: ad_windows(p) for g, p in plays.items()}, cut_at, mid_at, t0, t_end)
    total = sum(1 for g in plays for s, e in spans[g] if t0 - 95 <= s and e <= t_end - 95)
    mins = (t_end - t0) / 60.0
    return {"games": len(plays), "minutes": round(mins, 1), "cuts": cuts, "cuts_per_min": round(cuts / mins, 2),
            "snap_cuts": snap_cuts, "plays_shown": shown, "plays_shown_per_min": round(shown / mins, 2),
            "plays_all_games_per_min": round(total / mins, 2), "joins_mid_play": joins_mid, "joins_walk": joins_walk,
            "leaves_mid_play": leaves_mid, "rotations": rot, "parks": parks, "parks_expired": expired, "holds": dict(reasons),
            **bar}


# ------------------------------------------------------------------------------------- [P1-MAXRATE] the rate ceiling
# Why this is a separate model: cut_now is the editor we run; this asks what ANY editor could reach. A hidden stick is
# a machine that needs a job (one play at one stream position), the TV is one machine that shows them one at a time.
# Every play's picture must come up inside Nick's 60-90 s behind window, so each job has a release and a deadline.

@dataclass(frozen=True)
class MR:
    """One combination of the model. Every knob the report varies; the defaults are today's measured sticks."""
    sticks: int = 2
    flip_s: float = 2.5
    skip: bool = True               # (a) the hidden stick seeks over the huddle; False = it rolls 1x to the next snap
    seek_s: float = 8.0             # (a) same-game seek, whole cost (the press run, the picture settling, the label read)
    seek_step_s: float = 15.0       # (a) YouTube TV's step: the seek lands up to a step before the park point, then rolls
    prep_s: float = 15.0            # (b) another game on this stick (tune + buffer + park)
    games: int = 0                  # (b) stay on this many games at a time (0 = every game on)
    block_s: float = 300.0          # ...for this long, then the next set of live games (0 = the first N, all slate)
    replay_depth_s: float = 0.0     # (d) a play not yet shown may air this far behind the edge (0 = behind_max only)
    replay_repeats: int = 0         # (d) a shown play may air this many more times, only when nothing new is ready
    truth: str = "strict"           # (e) strict: snap-10..whistle+3 (the repo's Rule #1 span); tight: real snap-to-whistle
    snap_known: bool = False        # (e) tight only: a detector gives the true snap +/- snap_sd
    snap_sd: float = 0.5            # (e)
    pre_s: float = PLAY_S[League.NFL]    # (e) snap unknown: land this long before the entry's wallclock
    post_s: float = AFTER_PLAY_S    # (e) leave this long after it
    err_sd: float = 1.5             # true position error of a parked stick (the delay book's err_sd)
    guard_sigmas: float = 4.5       # margin each side of a play: 0 joins is hard, so the tail has to be out of reach
    behind_min: float = 60.0        # Nick, Nov 8: 60-90 s behind live
    behind_max: float = 90.0
    fail_p: float = 0.0             # a seek or prep fails and is done again
    policy: str = "fifo"            # which ready job a free stick takes: earliest showable, or earliest deadline
    lag_s: float = 20.0             # feed lag mean / sd, as in the editor sim
    lag_sd: float = 6.0
    mix: tuple[tuple[str, float], ...] = ()   # (WP-11) (app, share) of the slate; () = off, every game costs prep_s
    arb: "Arb | None" = None        # (MUST-SHOW-2) knobs for policy="arbiter"; None there = Arb()


@dataclass
class MRItem:
    gid: str
    land: float        # stream position the picture comes up at (real time)
    leave: float       # stream position the flip starts at
    start: float       # the play's true first second (for "complete" and Rule #1)
    end: float         # ...and last
    known_at: float    # wall time the feed has it (the whistle entry + lag)
    shown: int = 0
    taken: bool = False
    clean: bool = True    # False: too close to its neighbours to show alone (a hurry-up)


# Tight truth only: ball-in-play seconds per NFL play. The archive has no snap times (ESPN logs one wallclock near the
# whistle), so this is an ASSUMPTION, not a measurement; the report says so and tonight's test measures it.
MR_PLAY_MEAN_S, MR_PLAY_SD_S, MR_PLAY_MIN_S, MR_PLAY_MAX_S = 6.5, 2.0, 3.0, 12.0
MR_TIGHT_AFTER_S = 1.0      # the whistle
CATCHUP_STEP_S, CATCHUP_PRESS_S = 15.0, 0.35     # catch-up: one seek step back per 15 s of depth; an assumption, 0.35 s a press
MR_LAND_LOOK_S = 0.5        # the Rule #1 check looks at the first half second of the picture
MR_WINDOW_S = 600.0
# (league dir, min games, max games, name) for --maxrate-sweep; filled by MR_GRID's rows
MR_GROUPS = [("nfl", 7, 0, "nfl7+"), ("nfl", 3, 6, "nfl3-6"), ("nba", 3, 0, "nba")]
MR_FOOTBALL = ("nfl7+", "nfl3-6")    # tight dwell is about a snap: basketball has none
TIGHT = {"truth": "tight", "snap_known": True, "snap_sd": 0.3, "err_sd": 0.5}    # (e): needs a snap detector we do not have
MR_GRID: list[tuple[str, dict]] = [
    (f"{n}g {name}", {"games": n, **kw}) for n in (2, 3, 4) for name, kw in (
        ("roll 1x, prep 60 (editor today)", {"skip": False, "prep_s": 60.0, "seek_step_s": 0.0}),
        ("sticks today: seek 40, prep 60, 74% fail", {"seek_s": 40.0, "prep_s": 60.0, "fail_p": 0.74}),
        ("skip seek 30, prep 60", {"seek_s": 30.0, "prep_s": 60.0}),
        ("skip seek 15, prep 30", {"seek_s": 15.0, "prep_s": 30.0}),
        ("skip seek 8, prep 15", {"seek_s": 8.0, "prep_s": 15.0}),
        ("skip seek 8, prep 30", {"seek_s": 8.0, "prep_s": 30.0}),
        ("skip seek 4, prep 8", {"seek_s": 4.0, "prep_s": 8.0}),
        ("skip seek 4, prep 4, flip 1", {"seek_s": 4.0, "prep_s": 4.0, "flip_s": 1.0}),
        ("skip seek 8, prep 15, err 0.5", {"seek_s": 8.0, "prep_s": 15.0, "err_sd": 0.5}),
        ("skip seek 8, prep 15, tight dwell", {"seek_s": 8.0, "prep_s": 15.0, "_only": MR_FOOTBALL, **TIGHT}),
        ("skip seek 8, prep 15, stay 2 min", {"seek_s": 8.0, "prep_s": 15.0, "block_s": 120.0}),
        ("skip seek 8, prep 15, stay 10 min", {"seek_s": 8.0, "prep_s": 15.0, "block_s": 600.0}),
        ("skip seek 8, prep 15, replays 600 s x1", {"seek_s": 8.0, "prep_s": 15.0, "replay_depth_s": 600.0, "replay_repeats": 1}),
    )] + [
    ("all games, 4 sticks, seek 4, prep 8, flip 1", {"sticks": 4, "seek_s": 4.0, "prep_s": 8.0, "flip_s": 1.0}),
    ("all games, 4 sticks, same, tight dwell", {"sticks": 4, "seek_s": 4.0, "prep_s": 8.0, "flip_s": 1.0, "_only": MR_FOOTBALL, **TIGHT}),
]


@dataclass(frozen=True)
class Arb:
    """policy="arbiter": the WP-17 floor over the max-rate model, one switch per rule, plus MUST-SHOW-2's catch-up.
    Defaults are the WP-17 build ("best" in its harness) plus MUST-SHOW-3's plan; plan=False, save_leave=False is the
    MUST-SHOW-2 table's before row."""
    floor: bool = True        # a must-show clip outranks everything else a free stick could take
    reach: bool = True        # ...on any live game, not only the block's few
    repick: bool = True       # a stick mid-prep drops a lesser clip for a touchdown
    late: bool = True         # late in the slate, non-floor clips only from close games
    budget: bool = True       # non-floor visits per game (arbiter.visit_budget)
    ceiling: bool = True      # arbiter.ceiling_per_min
    catchup_s: float = 0.0    # (MUST-SHOW-2) must-shows may still air this deep behind live, after the live picks
    order: str = "rank"       # must-show order among fresh clips: "rank" (trigger order), "edf" (TD first, then earliest deadline)
    merge_max_s: float = 0.0  # (MUST-SHOW-2) hurry-up plays that cannot be landed between air as one clip, up to this long
    save_leave: bool = True   # (MUST-SHOW-3) a must-show about to die may leave a low-leverage, not-owed play mid-way (the rule allows those)
    fill: bool = False        # (MUST-SHOW-2) a clip that can air first goes in front of a best clip still waiting for its window
    protect: int = -1         # (MUST-SHOW-2) fill never goes in front of a clip of this trigger rank or better (0: a TD)
    cuts_cap: float = 0.0     # (MUST-SHOW-2) catch-up clips stop while the last 10 min already hold this many cuts (0: off)
    plan: bool = True         # (MUST-SHOW-3) a free stick takes the first clip of the best known run (arbiter.plan_clips), as late as it can


# arbiter policy: MR(policy="arbiter", arb=Arb(...)) replaces the fifo/edf key with the must-show floor. fifo and edf stay
# byte-for-byte; every branch that reads `arb` is guarded. Why: reproduce WP-17's number and break down what it misses.
def arbiter_facts(league_dir: str, lg: League, gids) -> dict:
    """{gid: (floor [(t, rank, kind)], leverage [(wall, high)])} from each game's summary.
    What the arbiter policy may know once a play's feed entry has arrived; opened only for the ids the caller allows."""
    from redzone.lab.leverage import high_leverage
    from redzone.moments.detect import detect
    league = lg.value
    out = {}
    for g in gids:
        with open(os.path.join(league_dir, f"summary_{g}.json")) as fh:
            d = json.load(fh)
        floor = sorted((m.t, arb_rules.trigger_rank(league, m.kind), m.kind) for m in detect(league, d, g).moments
                       if arb_rules.trigger_rank(league, m.kind) != arb_rules.NOT_FLOOR)
        out[g] = (floor, sorted((t, bool(high_leverage(lg, x))) for x in flatten_plays(lg, d) if (t := _ts(x.get("wallclock")))))
    return out


def mr_guard(p: MR) -> float:
    sd = math.hypot(p.err_sd, p.snap_sd) if (p.truth == "tight" and p.snap_known) else p.err_sd
    return p.guard_sigmas * sd


def mr_items(gid: str, plays, lg: League, p: MR, rng: random.Random, lag: float) -> list[MRItem]:
    """One job per complete thing the TV could show. Discrete sports: a play. Basketball: the live stretch between two
    legal entry/exit points (a dead window or, per Nick Nov 8, the walk up court after a shot)."""
    guard = mr_guard(p)
    lead = guard + MR_LAND_LOOK_S
    out: list[MRItem] = []
    dt = truth_tracker(plays, lg)
    if lg.base == League.NBA:
        # land in a dead ball or the walk after a shot, leave only in a dead ball (cuts.walks: nothing may LEAVE on a
        # walk). One job per live stretch: its last legal entry, the shortest clip that still ends at the whistle.
        walk = walk_params({}, lg)
        dead = sorted((w.start, w.end) for w in dt.windows() if w.end is not None)
        entries = sorted(dead + [(w.start, w.end) for w in (dt.walks(walk) if walk else [])])
        for (a0, a1), (b0, b1) in zip(dead, dead[1:]):
            if b0 <= a1:
                continue    # no live ball between them
            legal = [(x0, x1) for x0, x1 in entries if a1 <= x1 <= b0 and x1 - x0 >= lead + guard]
            x0, x1 = max(legal, default=(a0, a1))
            land = max(x0, x1 - lead)
            out.append(MRItem(gid, land, min(b1, b0 + guard), x1, b0, b0 + lag, clean=x1 - x0 >= lead + guard))
        return out
    for m in dt._order():
        if not _is_action(lg, m):
            continue
        wall = m.hi
        if p.truth == "strict":
            s, e = m.lo - PLAY_S[lg], m.hi + AFTER_PLAY_S
            out.append(MRItem(gid, s - lead, e + guard, s, e, wall + lag))
            continue
        length = min(MR_PLAY_MAX_S, max(MR_PLAY_MIN_S, rng.gauss(MR_PLAY_MEAN_S, MR_PLAY_SD_S)))
        s, e = wall - length, wall + MR_TIGHT_AFTER_S
        if p.snap_known:
            out.append(MRItem(gid, s - lead, e + guard, s, e, wall + lag))
        else:
            out.append(MRItem(gid, wall - p.pre_s - lead, wall + p.post_s + guard, s, e, wall + lag))
    for it in out:
        it.clean = True
    # a clip whose margins touch the plays beside it (a hurry-up) can't be shown clean: never a job, still a play in truth
    for i, it in enumerate(out):
        before = out[i - 1].end if i else -math.inf
        after = out[i + 1].start if i + 1 < len(out) else math.inf
        it.clean = it.land - guard >= before and it.leave + guard <= after
    return out


def mr_truth_spans(gid: str, plays, lg: League, items: list[MRItem], p: MR) -> list[tuple[float, float]]:
    """What a landing may not touch. Basketball: the repo's own live stretches; tight: each item's true play."""
    return [(it.start, it.end) for it in items] if p.truth == "tight" and lg.base != League.NBA else play_spans(plays, lg)


# (WP-11) Real slate app mixes, shares of games. plan-file is Nov 8's plan (15 Prime, 12 YouTube TV, 3 ESPN).
MR_MIXES: dict[str, tuple[tuple[str, float], ...]] = {
    "plan-file": (("prime", 0.5), ("youtubetv", 0.4), ("espn", 0.1)),
    "prime-heavy": (("prime", 0.6), ("youtubetv", 0.3), ("espn", 0.1)),
    "all-youtubetv": (("youtubetv", 1.0),),
}


def parse_mix(spec: str) -> tuple[tuple[str, float], ...]:
    """'prime-heavy' or 'prime=0.6,youtubetv=0.4' -> ((app, share), ...). '' = off. A bad spec raises ValueError."""
    if not spec:
        return ()
    if spec in MR_MIXES:
        return MR_MIXES[spec]
    out = []
    for part in spec.split(","):
        app, _, share = part.partition("=")
        if not app.strip() or float(share) < 0:
            raise ValueError(f"bad mix part {part!r}")
        out.append((app.strip(), float(share)))
    if sum(w for _, w in out) <= 0:
        raise ValueError("mix shares sum to 0")
    return tuple(out)


def assign_apps(gids: list[str], mix: tuple[tuple[str, float], ...]) -> dict[str, str]:
    """Give each game an app so the counts follow the shares (largest remainder). Deterministic and no rng, so a
    mix never shifts the random stream of a run without one. Games in start order take apps in a spread order."""
    if not mix:
        return {}
    tot = sum(w for _, w in mix)
    quota = [(a, len(gids) * w / tot) for a, w in mix]
    counts = {a: int(q) for a, q in quota}
    for a, q in sorted(quota, key=lambda z: -(z[1] - int(z[1])))[: len(gids) - sum(counts.values())]:
        counts[a] += 1
    pool = [a for a, _ in mix for _ in range(counts[a])]
    # spread: take the app with the most games left each time, so one app does not hold every early game
    out, left = {}, dict(counts)
    for g in gids:
        a = max((x for x in pool if left[x] > 0), key=lambda x: (left[x], -[m for m, _ in mix].index(x)))
        out[g] = a
        left[a] -= 1
    return out


def app_prep_s(app: str, q: QueueParams | None = None) -> float:
    """Prep for one app, from the queue's own numbers (gamequeue.Params.tune_s; unknown apps use `other`)."""
    t = (q or QueueParams()).tune_s
    return float(t.get(app, t["other"]))


def mr_cost(st: dict, it: MRItem, t: float, p: MR, rng: random.Random, prep: float | None = None) -> float | None:
    """Seconds until this stick can be parked at this play's landing, or None if it can't get there.
    `prep` is this game's app prep (WP-11); None = the flat p.prep_s."""
    if st["gid"] == it.gid and not p.skip:
        pos = st["pos"] + (t - st["pos_wall"] if not st["paused"] else 0.0)
        return None if it.land < pos else it.land - pos   # rolling 1x, no rewind
    base = p.seek_s if st["gid"] == it.gid else (p.prep_s if prep is None else prep)
    return base + (rng.uniform(0.0, p.seek_step_s) if p.seek_step_s > 0 else 0.0)


def mr_merge(its: list[MRItem], guard: float, max_s: float) -> None:
    """Hurry-up plays too close to land between (not clean) become one clip from the first's landing to the last's leave,
    in place (the show scorecard reads this same list). Only when both outer ends are clean and it fits in max_s."""
    out: list[MRItem] = []
    i = 0
    while i < len(its):
        j = i
        while j + 1 < len(its) and (its[j + 1].land - guard < its[j].end or its[j].leave + guard > its[j + 1].start):
            j += 1
        a, b = its[i], its[j]
        if j > i and b.leave - a.land <= max_s and (not i or a.land - guard >= its[i - 1].end) \
                and (j + 1 == len(its) or b.leave + guard <= its[j + 1].start):
            out.append(MRItem(a.gid, a.land, b.leave, a.start, b.end, b.known_at))
        else:
            out.extend(its[i:j + 1])
        i = j + 1
    its[:] = out


def run_maxrate(games, p: MR, rng: random.Random, lg: League | None = None, facts: dict | None = None) -> dict:
    """The TV shows one clip at a time; each hidden stick seeks or tunes to the next play and parks. Rule #1 is checked
    against truth at every landing and leave: the true picture is the parked position + a per-seek error.
    policy="arbiter" needs `facts` (arbiter_facts): each clip's must-show rank, causal through known_at like every pick."""
    lg = lg or LG
    is_arb = p.policy == "arbiter"
    arb = (p.arb or Arb()) if is_arb else None
    if is_arb and facts is None:
        raise ValueError('policy="arbiter" needs facts=arbiter_facts(...)')
    plays = dict(games)
    first = {g: q[0][0] for g, q in plays.items()}
    last = {g: q[-1][0] for g, q in plays.items()}
    t0 = max(sorted(first.values())[:2]) + 120
    t_end = sorted(last.values())[-2]
    if t_end - t0 < 1800:
        return {}
    order = sorted(plays, key=lambda g: first[g])
    used = order[:p.games] if p.games and not p.block_s else order

    def active(at: float) -> set[str]:
        """The games the editor is staying on at wall time `at`: all of them, or N live ones for this block."""
        if not p.games:
            return set(used)
        live = [g for g in used if first[g] <= at and last[g] > at + 60.0] or used
        if not p.block_s:
            return set(live[:p.games])
        off = int((at - t0) // p.block_s) * p.games
        return {live[(off + i) % len(live)] for i in range(min(p.games, len(live)))}
    lag = {g: max(5.0, rng.gauss(p.lag_s, p.lag_sd)) for g in plays}
    items = {g: mr_items(g, plays[g], lg, p, rng, lag[g]) for g in used}
    if arb and arb.merge_max_s > 0:
        for g in used:
            mr_merge(items[g], mr_guard(p), arb.merge_max_s)
    spans = {g: mr_truth_spans(g, plays[g], lg, items[g], p) for g in used}
    app_of = assign_apps(order, p.mix)
    prep_of = {g: app_prep_s(a) for g, a in app_of.items()}     # {} when no mix: every call falls back to p.prep_s
    walk = walk_params({}, lg)
    walks = {g: walk_spans(plays[g], walk, lg) for g in used}
    ads = {g: ad_windows(plays[g], lg) for g in used}
    jobs = sorted((it for g in used for it in items[g] if it.clean), key=lambda it: it.land)
    lands = [it.land for it in jobs]
    by_known = sorted(it.known_at for it in jobs)
    mu_min, mu_max = EDGE_S + p.behind_min, EDGE_S + max(p.behind_max, p.replay_depth_s, arb.catchup_s if arb else 0.0)
    mu_max_fresh = EDGE_S + p.behind_max
    guard = mr_guard(p)
    sd = guard / p.guard_sigmas if p.guard_sigmas else p.err_sd
    f = p.flip_s

    def deadline(it: MRItem) -> float:
        return it.land + mu_max

    def stick(g: str, pos: float, wall: float) -> dict:
        return {"gid": g, "pos": pos, "pos_wall": wall, "paused": False, "target": None, "ready_at": wall, "free_at": wall, "err": 0.0}

    sticks = [stick(used[i % len(used)], t0 - EDGE_S - 75.0, t0) for i in range(p.sticks)]
    vis, clip_end, seg_wall, seg_pos = sticks[0], t0, t0, t0 - EDGE_S - 75.0   # the first picture is whatever is on
    freeze_at = None
    cut_at, mid_at, segs = [], [], []
    ap = arb_rules.DEFAULT_PARAMS
    _late_ids: set[int] = set()
    rank_of: dict[int, tuple[int, str]] = {}
    weight_of: dict[int, float] = {}                   # (MUST-SHOW-3) what airing this clip is worth to the plan
    visits: dict[str, int] = {}
    screen_log: list[tuple[float, float, int]] = []    # (cut, clip end, floor rank) of every clip aired
    hidden_log: list[list] = []                        # [assign, ready or drop, floor rank]: the hidden stick's jobs
    stall_log: list[tuple[float, float]] = []          # cuts the ceiling held back
    leaves_high, leaves_low, missed_entry, frozen_s = 0, 0, 0, 0.0
    if arb:
        for g in used:
            fl = facts[g][0]
            ts = [x[0] for x in fl]
            for it in items[g]:
                rank_of[id(it)] = min(fl[bisect.bisect_left(ts, it.land):bisect.bisect_right(ts, it.leave)],
                                      key=lambda x: (x[1], x[2]), default=(0.0, arb_rules.NOT_FLOOR, ""))[1:]
                weight_of[id(it)] = arb_rules.clip_weight([x[1] for x in fl[bisect.bisect_left(ts, it.land):bisect.bisect_right(ts, it.leave)]])

    def high_at(g: str, at: float) -> bool:
        """Is the game close and late as of the plays at or before wall time `at`?"""
        rows = facts[g][1]
        i = bisect.bisect_right(rows, (at, True)) - 1
        return rows[i][1] if i >= 0 else False

    def is_floor(it: MRItem) -> bool:
        return rank_of[id(it)][0] != arb_rules.NOT_FLOOR
    joins_mid, joins_walk, leaves_mid, late_jumps, repeats, flips_held, shown_clip_s = 0, 0, 0, 0, 0, 0, 0.0
    save_leaves = 0

    def vis_pos(at: float) -> float:
        return seg_pos + ((min(at, freeze_at) if freeze_at is not None else at) - seg_wall)

    def set_freeze(at: float):
        """The hidden rule of 0 commercial seconds: when the picture would roll into a break it holds on the last frame."""
        nonlocal freeze_at
        pos = vis_pos(at)
        if any(a - guard <= pos < e for a, e in ads[vis["gid"]]):
            freeze_at = at     # already at a break: hold right here
            return
        nxt = min((a for a, _ in ads[vis["gid"]] if a >= pos), default=None)
        if nxt is None:
            freeze_at = None
            return
        stop = nxt - guard
        if arb and arb.plan:      # a play ending just before the break: don't freeze so near its whistle that the error lands inside it
            ends = [e for _, e in spans[vis["gid"]] if stop - guard < e <= stop]
            if ends:
                stop = min(max(ends) + guard, nxt - 0.5)
        freeze_at = at + max(0.0, stop - pos)

    set_freeze(t0)

    def plan_pool(st: dict, t: float) -> list[tuple]:
        """(MUST-SHOW-3) Every clip this stick could still air, as arbiter.plan_clips reads it. Same filters as assign; the
        cost is the expected one, so asking again doesn't change the answer."""
        out = []
        now_on = active(t)
        for it in jobs[bisect.bisect_left(lands, t - mu_max - 1.0):bisect.bisect_right(lands, t + 1.0)]:
            if it.known_at > t or it.taken or it.shown or (it.gid not in now_on and not (arb.reach and is_floor(it))):
                continue
            floor = is_floor(it)
            if not floor:
                if arb.late and any(high_at(g, t) for g in used) and not high_at(it.gid, t):
                    continue
                if arb.budget and ap.visit_budget > 0 and visits.get(it.gid, 0) >= (
                        ap.visit_budget if high_at(it.gid, t) else ap.visit_budget - ap.visit_reserve):
                    continue
            base = p.seek_s if st["gid"] == it.gid else prep_of.get(it.gid, p.prep_s)
            out.append((id(it), weight_of[id(it)], it.land + mu_min - f, (deadline(it) if floor else it.land + mu_max_fresh) - f,
                        it.leave - it.land, max(t + base + p.seek_step_s / 2.0, it.land + EDGE_S + 1.0), it, base))
        return out

    def plan_pick(st: dict, t: float, keep: MRItem | None = None):
        """(first clip of the best run or None, the wall time this stick has to start it by)."""
        pool = plan_pool(st, t)
        if keep is not None:
            pool.append((id(keep), weight_of[id(keep)], keep.land + mu_min - f, deadline(keep) - f, keep.leave - keep.land, st["ready_at"], keep, 0.0))
        first = arb_rules.plan_clips([c[:6] for c in pool], max(t, clip_end), f, p.prep_s + p.seek_step_s / 2.0, id(keep) if keep is not None else None, p.prep_s + p.seek_step_s)
        c = next((x for x in pool if x[0] == first), None)
        return (None, None) if c is None else (c[6], c[2] - c[7] - p.seek_step_s - 1.0)

    def plan_assign(st: dict, t: float) -> bool:
        """(MUST-SHOW-3) Decide last: the stick stays free until the run's first clip needs it, so a play the feed reports
        in the meantime can still change the pick."""
        it, by = plan_pick(st, t)
        st["wait_until"] = 0.0
        if it is None:
            return False
        if t < by:
            st["wait_until"] = by
            return False
        ready = max(t + mr_cost(st, it, t, p, rng, prep_of.get(it.gid)), it.land + EDGE_S + 1.0)
        show = max(ready, it.land + mu_min - f)
        if show + f > it.land + mu_max_fresh:
            ready += (show - it.land - mu_max_fresh + f) / CATCHUP_STEP_S * CATCHUP_PRESS_S
            show = max(ready, it.land + mu_min - f)
        if show + f > deadline(it) or (not is_floor(it) and show + f > it.land + mu_max_fresh):
            return False
        if p.fail_p and rng.random() < p.fail_p:
            ready += p.seek_s if st["gid"] == it.gid else prep_of.get(it.gid, p.prep_s)
        it.taken = True
        hidden_log.append([t, ready, rank_of[id(it)][0], st])
        st.update(target=it, ready_at=ready, gid=it.gid, pos=it.land, pos_wall=ready, paused=True, err=rng.gauss(0.0, sd))
        return True

    def assign(st: dict, t: float):
        if arb and arb.plan:
            return plan_assign(st, t)
        lo = bisect.bisect_left(lands, t - mu_max - 1.0)
        hi = bisect.bisect_right(lands, t + 1.0)
        best, best_key, best_cost, repeat_pool = None, None, None, []
        cands: list[tuple] = []
        now_on = active(t)
        for it in jobs[lo:hi]:
            if it.known_at > t or it.taken or (it.gid not in now_on and not (arb and arb.reach and is_floor(it))):
                continue
            if arb and not is_floor(it):
                if arb.late and any(high_at(g, t) for g in used) and not high_at(it.gid, t):
                    continue
                if arb.budget and ap.visit_budget > 0 and visits.get(it.gid, 0) >= (
                        ap.visit_budget if high_at(it.gid, t) else ap.visit_budget - ap.visit_reserve):
                    continue
            if it.shown:
                if it.shown <= p.replay_repeats:
                    repeat_pool.append(it)
                continue
            c = mr_cost(st, it, t, p, rng, prep_of.get(it.gid))
            if c is None:
                continue
            ready = max(t + c, it.land + EDGE_S + 1.0)
            show = max(ready, it.land + mu_min - f)
            if show + f > deadline(it):
                continue
            if arb and show + f > it.land + mu_max_fresh:         # past the live window: catch-up
                if not is_floor(it) or (arb.cuts_cap and sum(c > t - MR_WINDOW_S for c in cut_at[-int(arb.cuts_cap):])
                                        >= arb.cuts_cap):
                    continue
                ready += (show - it.land - mu_max_fresh + f) / CATCHUP_STEP_S * CATCHUP_PRESS_S     # the longer press run back
                show = max(ready, it.land + mu_min - f)
                if show + f > deadline(it):
                    continue
            fresh = show + f > it.land + mu_max_fresh       # False sorts first: a play inside the window before a late one
            if arb and arb.fill:
                cands.append((id(it), rank_of[id(it)][0], fresh, show, deadline(it), it.leave - it.land,
                              (deadline(it) if fresh else it.land + mu_max_fresh) - f, it, ready))
            if arb:
                key = arb_rules.clip_key(rank_of[id(it)][0] if arb.floor else arb_rules.NOT_FLOOR, fresh, show, deadline(it),
                                         arb.order, arb.catchup_s > 0)
            else:
                key = (fresh, show, deadline(it)) if p.policy == "fifo" else (fresh, deadline(it), show)
            if best_key is None or key < best_key:
                best, best_key, best_cost = it, key, ready
        if best is None:
            for it in repeat_pool:     # (d) nothing new: show a play again
                c = mr_cost(st, it, t, p, rng, prep_of.get(it.gid))
                if c is None:
                    continue
                ready = max(t + c, it.land + EDGE_S + 1.0)
                if ready + f <= deadline(it):
                    key = (True, ready, -it.land)
                    if best_key is None or key < best_key:
                        best, best_key, best_cost = it, key, ready
        if cands:
            pick = arb_rules.pick_clip(cands, t, f, p.prep_s + p.seek_step_s, arb.order, arb.catchup_s > 0, protect_rank=arb.protect)
            best, best_cost = next(((c[7], c[8]) for c in cands if c[0] == pick), (best, best_cost))
        if best is None:
            return False
        if p.fail_p and rng.random() < p.fail_p:
            best_cost += p.seek_s if st["gid"] == best.gid else prep_of.get(best.gid, p.prep_s)
        best.taken = True
        if arb:
            hidden_log.append([t, best_cost, rank_of[id(best)][0], st])
        st.update(target=best, ready_at=best_cost, gid=best.gid, pos=best.land, pos_wall=best_cost, paused=True,
                  err=rng.gauss(0.0, sd))
        return True

    def drop(st: dict, t: float):
        st["target"].taken = False
        if arb:
            for h in reversed(hidden_log):
                if h[3] is st:
                    h[1] = t
                    break
        st.update(target=None, free_at=t)

    t = t0
    while t < t_end:
        hidden = [s for s in sticks if s is not vis]
        for st in sorted(hidden, key=lambda s: s["free_at"]):
            if st["target"] is not None and t + f > deadline(st["target"]):
                drop(st, t)
            if arb and arb.plan and st["target"] is not None:        # a play the feed just reported can change the run
                known = bisect.bisect_right(by_known, t)
                if known != st.get("planned_at"):
                    st["planned_at"] = known
                    if plan_pick(st, t, st["target"])[0] is not st["target"]:
                        drop(st, t)
            elif arb and arb.repick and arb.floor and st["target"] is not None and st["ready_at"] > t:
                held = rank_of[id(st["target"])][0]
                lo_ = bisect.bisect_left(lands, t - mu_max - 1.0)
                if any(j.known_at <= t and not j.taken and not j.shown and rank_of[id(j)][0] < held
                       and rank_of[id(j)][0] <= ap.repick_max_rank and j.land + mu_max - f > t + p.prep_s
                       for j in jobs[lo_:bisect.bisect_right(lands, t + 1.0)]):
                    drop(st, t)
            if st["target"] is None and t >= st["free_at"]:
                assign(st, t)
        wakes = []
        if t >= clip_end:
            ready = [s for s in hidden if s["target"] is not None and s["ready_at"] <= t
                     and t + f >= s["target"].land + mu_min]
            if ready:
                st = min(ready, key=lambda s: (t + f > s["target"].land + mu_max_fresh, deadline(s["target"])))
                pv = vis_pos(t)
                frozen = freeze_at is not None and t >= freeze_at
                busy = [] if frozen else [e for s_, e in spans.get(vis["gid"], []) if s_ - guard < pv + f and pv - guard < e - 0.01]
                if arb and arb.ceiling and ap.ceiling_per_min > 0 and len(cut_at) >= ap.ceiling_per_min \
                        and t - cut_at[-ap.ceiling_per_min] < 60.0:
                    stall_log.append((t, cut_at[-ap.ceiling_per_min] + 60.0))
                    t = cut_at[-ap.ceiling_per_min] + 60.0
                    continue
                if busy and arb and arb.plan and arb.save_leave and st["target"] is not None and is_floor(st["target"]):
                    # The clip on the stick would die waiting for this whistle: leave it, if that's a leave the rules allow.
                    hold_to = t + max(0.05, max(busy) - pv + guard)
                    tgt = st["target"]
                    td_behind = any(j.known_at <= t and not j.taken and not j.shown and weight_of[id(j)] >= arb_rules.TD_WEIGHT
                                    and deadline(j) - f < hold_to + f + (tgt.leave - tgt.land) + f
                                    for j in jobs[bisect.bisect_left(lands, t - mu_max - 1.0):bisect.bisect_right(lands, t + 1.0)])
                    if (hold_to + f > deadline(tgt) or td_behind) and not (high_at(vis["gid"], pv) or high_at(vis["gid"], pv + f + guard)) \
                            and not any(is_floor(x) or x.known_at > t for x in items[vis["gid"]]
                                    if x.start - guard < pv + f and pv - guard < x.end):
                        busy = []
                        save_leaves += 1
                if busy:                          # the picture rolled into a play: hold the flip to its whistle
                    flips_held += 1
                    t = min(t_end, t + max(0.05, max(busy) - pv + guard))
                    continue
                it = st["target"]
                true_pos = it.land + st["err"]
                # truth at the leave and the landing
                seg_err = vis["err"]
                pv_true = pv + seg_err
                if in_play(spans.get(vis["gid"], []), pv_true, pv_true + f):
                    leaves_mid += 1
                    mid_at.append(t)
                    if arb:
                        if high_at(vis["gid"], pv_true):
                            leaves_high += 1
                        else:
                            leaves_low += 1
                if arb and in_play(spans[it.gid], true_pos - f, true_pos):
                    missed_entry += 1
                if in_play(spans[it.gid], true_pos, true_pos + 0.5):
                    if in_walk(walks[it.gid], true_pos, true_pos + 0.5):
                        joins_walk += 1
                    else:
                        joins_mid += 1
                        mid_at.append(t)
                landed = t + f
                b = min(t, freeze_at) if freeze_at is not None else t
                if b > seg_wall:
                    segs.append((vis["gid"], seg_wall, b, seg_wall - (seg_pos + vis["err"])))
                cut_at.append(t)
                if arb and freeze_at is not None and t > freeze_at:
                    frozen_s += t - max(freeze_at, seg_wall)
                if arb:
                    screen_log.append((t, t + f + (it.leave - it.land), rank_of[id(it)][0]))
                    visits[it.gid] = visits.get(it.gid, 0) + 1
                late_jumps += landed - it.land > mu_max_fresh
                repeats += it.shown > 0
                if arb and landed - it.land > mu_max_fresh:
                    _late_ids.add(id(it))
                it.shown += 1
                it.taken = False
                vis.update(free_at=t, target=None, paused=False, pos=pv, pos_wall=t)
                vis = st
                st.update(target=None, paused=False)
                seg_wall, seg_pos = landed, it.land
                clip_end = landed + (it.leave - it.land)
                shown_clip_s += it.leave - it.land
                freeze_at = None
                set_freeze(clip_end)
                continue    # same t: the stick we just left is free to prepare now
        else:
            wakes.append(clip_end)
        for st in hidden:
            if arb and arb.repick and arb.floor:
                i = bisect.bisect_right(by_known, t)
                if i < len(by_known):
                    wakes.append(by_known[i])
            if st["target"] is not None:
                wakes += [st["ready_at"], st["target"].land + mu_min - f, deadline(st["target"]) - f + 0.01]
            else:
                wakes.append(st["free_at"])
                wakes.append(st.get("wait_until", 0.0))
                i = bisect.bisect_right(by_known, t)
                if i < len(by_known):
                    wakes.append(by_known[i])
        wakes = [w for w in wakes if w > t]
        t = min(wakes + [t_end]) if wakes else t + 0.5
    if arb and freeze_at is not None and t_end > freeze_at:
        frozen_s += t_end - max(freeze_at, seg_wall)
    b = min(t_end, freeze_at) if freeze_at is not None else t_end
    if b > seg_wall:
        segs.append((vis["gid"], seg_wall, b, seg_wall - (seg_pos + vis["err"])))
    item_spans = {g: [(it.start, it.end) for it in items[g]] for g in used}
    shown = sum(1 for g, a, b_, mu in segs for s, e in item_spans[g] if s >= a - mu and e <= b_ - mu)
    ad_s = sum(max(0.0, min(b_ - mu, e) - max(a - mu, s)) for g, a, b_, mu in segs for s, e in ads[g])
    mins = (t_end - t0) / 60.0
    windows, w0 = [], t0
    while w0 + MR_WINDOW_S <= t_end:
        windows.append(sum(1 for c in cut_at if w0 <= c < w0 + MR_WINDOW_S))
        w0 += MR_WINDOW_S
    supply = sum(1 for g in used for it in items[g] if t0 <= it.start and it.end <= t_end and g in active(it.start + EDGE_S + 75.0))
    out = {"games": len(plays), "games_used": len(used), "minutes": round(mins, 1), "jumps": len(cut_at), "windows": windows,
           "plays_shown": shown, "supply": supply, "joins_mid_play": joins_mid, "joins_walk": joins_walk,
           "leaves_mid_play": leaves_mid, "ad_s": round(ad_s, 1), "clip_s": round(shown_clip_s, 1),
           "flips_held": flips_held, "repeats": repeats, "late_jumps": late_jumps}
    if arb:
        break_s = sum(max(0.0, min(b_ - mu, e) - max(a - mu, s)) for g, a, b_, mu in segs for s, e in ads[g])
        late_shown = sum(1 for it in jobs if it.shown and it.gid and is_floor(it) and id(it) in _late_ids)
        out.update(leaves_high=leaves_high, leaves_low=leaves_low, plays_missed_entry=missed_entry, save_leaves=save_leaves,
                   dead_air_pct=round(100.0 * (break_s + frozen_s) / max(1.0, (t_end - t0)), 1), frozen_s=round(frozen_s, 1),
                   catchup_clips=late_shown,
                   misses=_arb_misses(lg, items, used, facts, t0, t_end, screen_log, hidden_log, stall_log,
                                      p, arb, mu_min, mu_max, f, prep_of, _late_ids))
    return out


MISS_WHYS = ("rule_never_a_clean_clip", "rule_ceiling", "already_past", "prep_time", "two_sticks", "another_must_on", "lesser_or_idle")


def must_ceiling(its: dict[str, list[MRItem]], moments, t0: float, t_end: float, p: MR, catchup_s: float) -> tuple[int, int, int]:
    """Hindsight ceiling for one TV and two sticks: the most must-show moments that could air in full with no feed lag, no
    prep and every pick perfect. Every clip's window is the same width, so airing clips in landing order costs almost
    nothing (it only misorders the rare long merged clip): a near-exact bound, one pass. (moments total, in any clean clip, airable)."""
    total, reachable, by_it = 0, 0, {}
    for g, t in moments:
        if not (t0 <= t <= t_end):
            continue
        total += 1
        it = next((x for x in its[g] if x.land <= t <= x.leave), None)
        if it is not None and it.clean:
            reachable += 1
            by_it[id(it)] = (it, by_it.get(id(it), (it, 0))[1] + 1)
    f = p.flip_s
    width = max(p.behind_max, catchup_s) - p.behind_min
    dp = [0.0]         # dp[w]: the earliest wall time the screen is free having aired w must-show moments
    for it, w in sorted(by_it.values(), key=lambda x: x[0].land):
        r = it.land + EDGE_S + p.behind_min - f
        d = it.leave - it.land + f
        grown = dp + [math.inf] * w
        for done in range(len(dp) - 1, -1, -1):
            if dp[done] == math.inf:
                continue
            end = max(dp[done], r) + d
            if end <= r + width + d and end < grown[done + w]:
                grown[done + w] = end
        dp = grown
    return total, reachable, max((w for w, v in enumerate(dp) if v < math.inf), default=0)


def _covered(iv: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Seconds of [lo, hi] inside the union of the intervals `iv`."""
    tot, cur = 0.0, lo
    for a, b in sorted(iv):
        a, b = max(a, cur), min(b, hi)
        if b > a:
            tot += b - a
            cur = b
    return tot


def _gaps(iv: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """The parts of [lo, hi] outside the union of the intervals `iv`."""
    out, cur = [], lo
    for a, b in sorted(iv):
        if a > cur:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return [(a, b) for a, b in out if b > a]


def _arb_misses(lg, items, used, facts, t0, t_end, screen_log, hidden_log, stall_log, p, arb, mu_min, mu_max, f, prep_of, late_ids):
    """Every must-show moment the sim did not air, with the reason from what the screen and the hidden stick were doing
    through its whole legal window; a seen moment that aired as a catch-up reads "seen_catchup". Report only: read after
    the run, never by a pick. [(kind, why)] (see MISS_WHYS)."""
    from redzone.moments import tiers
    must_iv = [(a, b) for a, b, r in screen_log if r != arb_rules.NOT_FLOOR]
    hid_must = [(a, b) for a, b, r, _ in hidden_log if r != arb_rules.NOT_FLOOR]
    out = []
    for g in used:
        for t, _rank, kind in facts[g][0]:
            if not (tiers.must_show(lg.value, kind) or kind in arb_rules.DEFAULT_PARAMS.floor_extra) or not (t0 <= t <= t_end):
                continue
            it = next((x for x in items[g] if x.land <= t <= x.leave), None)
            if it is not None and it.shown:
                if id(it) in late_ids:
                    out.append((kind, "seen_catchup"))
                continue
            if it is None or not it.clean:
                out.append((kind, "rule_never_a_clean_clip"))
                continue
            w_end = it.land + mu_max - f
            w_start = max(it.known_at, it.land + mu_min - f, t0)
            min_prep = min(p.seek_s, prep_of.get(it.gid, p.prep_s))
            if w_start > w_end - min_prep:
                out.append((kind, "already_past"))
                continue
            if _covered(must_iv, w_start, w_end) >= (w_end - w_start) - 1.0:
                out.append((kind, "another_must_on"))
                continue
            gaps = _gaps(must_iv, w_start, w_end)
            if _covered(stall_log, w_start, w_end) > 0 and any(a < gap[1] and gap[0] < b for gap in gaps for a, b in stall_log):
                out.append((kind, "rule_ceiling"))
                continue
            if all(_covered(hid_must, a, b) >= (b - a) - 1.0 for a, b in gaps):
                out.append((kind, "two_sticks"))
                continue
            free = [gap for gap in gaps if _covered(hid_must, gap[0], gap[1]) < (gap[1] - gap[0]) - 1.0]
            u = min(a for a, _ in free)
            out.append((kind, "prep_time" if u + prep_of.get(g, p.prep_s) > w_end else "lesser_or_idle"))
    return out


def mr_summary(rows: list[dict], ten: float = 10.0) -> dict:
    wins = [w for r in rows for w in r["windows"]]
    mins = sum(r["minutes"] for r in rows)
    jumps = sum(r["jumps"] for r in rows)
    return {"slates": len(rows), "minutes": round(mins), "jumps_per_10": round(jumps / mins * ten, 1),
            "p90_per_10": sorted(wins)[int(0.9 * (len(wins) - 1))] if wins else 0,
            "worst_per_10": min(wins, default=0), "windows_50plus_pct": round(100.0 * sum(w >= 50 for w in wins) / max(1, len(wins)), 1),
            "plays_shown_per_10": round(sum(r["plays_shown"] for r in rows) / mins * ten, 1),
            "supply_per_10": round(sum(r["supply"] for r in rows) / mins * ten, 1),
            "joins_mid_play": sum(r["joins_mid_play"] for r in rows), "joins_walk": sum(r["joins_walk"] for r in rows),
            "leaves_mid_play": sum(r["leaves_mid_play"] for r in rows),
            "ad_s_per_10": round(sum(r["ad_s"] for r in rows) / mins * ten, 2),
            "clip_pct_of_screen": round(100.0 * sum(r["clip_s"] for r in rows) / (mins * 60.0), 1),
            "repeats_per_10": round(sum(r["repeats"] for r in rows) / mins * ten, 1),
            "late_jumps_per_10": round(sum(r["late_jumps"] for r in rows) / mins * ten, 1),
            "clip_mean_s": round(sum(r["clip_s"] for r in rows) / max(1, jumps), 1)}


def maxrate_slates(archive: str, lg: League, min_games: int, max_games: int = 0, only: set[str] | None = None):
    """[(day, games)]: the slates this many games big, the same grouping as the editor sim."""
    global LG
    LG = lg
    return [(d, g) for d, g in slates(archive, min_games, only) if not max_games or len(g) <= max_games]


def maxrate_run(group, p: MR, seed: int, lg: League, archive: str | None = None) -> dict:
    """One combination over one group of slates: the same seed per slate, so rows differ only by their knobs.
    policy="arbiter" reads each game's summary from `archive`, which is required then."""
    rows = [r for n, (_, games) in enumerate(group)
            if (r := run_maxrate(games, p, random.Random(seed + n), lg,
                                 arbiter_facts(archive, lg, [g for g, _ in games]) if p.policy == "arbiter" else None))]
    s = mr_summary(rows) if rows else {}
    if s and s.get("clip_mean_s"):
        s["screen_cap_per_10"] = round(MR_WINDOW_S / (s["clip_mean_s"] + p.flip_s), 1)
    return s


def maxrate_params(a) -> MR:
    return MR(sticks=a.mr_sticks, flip_s=a.mr_flip, skip=not a.mr_no_skip, seek_s=a.mr_seek, seek_step_s=a.mr_seek_step,
              prep_s=a.mr_prep, games=a.mr_games, block_s=a.mr_block, replay_depth_s=a.mr_replay_depth, replay_repeats=a.mr_replay_repeats,
              truth=a.mr_truth, snap_known=a.mr_snap_known, snap_sd=a.mr_snap_sd, pre_s=a.mr_pre, post_s=a.mr_post,
              err_sd=a.mr_err_sd, guard_sigmas=a.mr_guard_sigmas, behind_min=a.mr_behind_min, behind_max=a.mr_behind_max,
              fail_p=a.mr_fail, policy=a.mr_policy, lag_s=a.lag, lag_sd=a.lag_sd, mix=parse_mix(a.mr_mix))


# (WP-11) rows of --maxrate-mix: the same editor settings, only the app mix changes. Flat = today's one prep for all.
MR_MIX_BASE = {"games": 4, "seek_s": 8.0}
MR_MIX_ROWS: list[tuple[str, dict]] = [
    ("flat prep 15 (today's sim)", {"prep_s": 15.0, "mix": ()}),
    ("all YouTube TV (prep 4)", {"mix": MR_MIXES["all-youtubetv"]}),
    ("plan-file mix (50% Prime)", {"mix": MR_MIXES["plan-file"]}),
    ("Prime-heavy (60% Prime)", {"mix": MR_MIXES["prime-heavy"]}),
    ("all Prime (prep 45)", {"mix": (("prime", 1.0),)}),
]


def mix_table(archive: str, lg: League, a, only: set[str] | None) -> list[dict]:
    """One row per MR_MIX_ROWS over the dev slates of one league. Small: one seed, no sweep."""
    group = maxrate_slates(archive, lg, a.min_games, a.mr_slate_max, only)
    group = group[: a.max_slates] if a.max_slates else group
    out = []
    for label, kw in MR_MIX_ROWS:
        s = maxrate_run(group, replace(maxrate_params(a), **MR_MIX_BASE, **kw), a.seed, lg)
        if s:
            out.append({"league": lg.value, "row": label, "slates": len(group), **s})
            print(f"{lg.value:4s} {label:28s} {s['jumps_per_10']:5.1f}/10 min  p90 {s['p90_per_10']:3d} worst {s['worst_per_10']:3d} "
                  f"50+ {s['windows_50plus_pct']:5.1f}%  mid {s['joins_mid_play']} leave {s['leaves_mid_play']} "
                  f"ads {s['ad_s_per_10']:.1f} late {s['late_jumps_per_10']:.1f}")
    return out


def maxrate_main(a) -> int:
    lg = League(a.league)
    only = dev_game_ids(a.archive, a.split)
    if a.maxrate_mix:
        rows = mix_table(a.archive, lg, a, only)
        if a.json:
            json.dump(rows, open(a.json, "w"), indent=1)
        return 0 if rows else 1
    if a.maxrate:
        group = maxrate_slates(a.archive, lg, a.min_games, a.mr_slate_max, only)
        group = group[: a.max_slates] if a.max_slates else group
        p = maxrate_params(a)
        s = maxrate_run(group, p, a.seed, lg, a.archive)
        print(json.dumps(s, indent=1))
        if a.json:
            json.dump({"params": asdict(p), "summary": s}, open(a.json, "w"), indent=1)
        return 0 if s else 1
    base = os.path.dirname(a.archive.rstrip("/"))
    out = []
    for lgv, min_g, max_g, name in MR_GROUPS:
        sub = os.path.join(base, lgv)
        group = maxrate_slates(sub, League(lgv), min_g, max_g, dev_game_ids(sub, a.split))
        for label, kw in MR_GRID:
            if kw.get("_only") and name not in kw["_only"]:
                continue
            p = replace(MR(), **{k: v for k, v in kw.items() if not k.startswith("_")})
            s = maxrate_run(group, p, a.seed, League(lgv))
            if s:
                row = {"group": name, "row": label, **s}
                out.append(row)
                print(f"{name:7s} {label:34s} {s['jumps_per_10']:5.1f} p90 {s['p90_per_10']:3d} worst {s['worst_per_10']:3d} "
                      f"50+ {s['windows_50plus_pct']:5.1f}%  shown {s['plays_shown_per_10']:5.1f}/{s['supply_per_10']:5.1f}  "
                      f"mid {s['joins_mid_play']} walk {s['joins_walk']} leave {s['leaves_mid_play']} ads {s['ad_s_per_10']:.1f} "
                      f"repeat {s['repeats_per_10']:.1f} late {s['late_jumps_per_10']:.1f} clip {s['clip_mean_s']:.1f}s")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", default="nfl", help="nfl or nba (the archive defaults to data/archive/<league>)")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--min-games", type=int, default=3)
    ap.add_argument("--max-slates", type=int, default=0)
    ap.add_argument("--behind", type=float, default=75.0)
    ap.add_argument("--sigma", type=float, default=4.0, help="stated delay sigma used for margins (s)")
    ap.add_argument("--err-sd", type=float, default=1.5, help="true per-tune delay error sd (s)")
    ap.add_argument("--lag", type=float, default=20.0, help="feed lag mean (s)")
    ap.add_argument("--lag-sd", type=float, default=6.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--set", action="append", default=[], help="buffered_jump.KEY=VALUE")
    # [P1-MAXRATE] the rate ceiling model: off unless --maxrate or --maxrate-sweep (run_maxrate; live code is untouched)
    ap.add_argument("--maxrate", action="store_true", help="run the max-rate model on the slates instead of the editor")
    ap.add_argument("--maxrate-sweep", action="store_true", help="run the whole MR_GRID table (docs/agents/audit/2026-11-09-max-rate.md)")
    ap.add_argument("--mr-sticks", type=int, default=MR().sticks, help="HDMI inputs, 2-4")
    ap.add_argument("--mr-flip", type=float, default=MR().flip_s, help="input flip, s")
    ap.add_argument("--mr-no-skip", action="store_true", help="(a) off: the hidden stick rolls 1x to the next snap instead of seeking")
    ap.add_argument("--mr-seek", type=float, default=MR().seek_s, help="(a) hidden seek to the next snap in the same game, s")
    ap.add_argument("--mr-seek-step", type=float, default=MR().seek_step_s, help="(a) seek granularity, s (YouTube TV 15; 0 = exact)")
    ap.add_argument("--mr-prep", type=float, default=MR().prep_s, help="(b) per-jump prep to another game on a stick, s")
    ap.add_argument("--mr-games", type=int, default=MR().games, help="(b) stay on this many games at a time (0 = every game on)")
    ap.add_argument("--mr-block", type=float, default=MR().block_s, help="(b) ...for this many seconds, then the next set (0 = the first N all slate)")
    ap.add_argument("--mr-replay-depth", type=float, default=MR().replay_depth_s, help="(d) plays not yet shown may air this far behind live, s (0 = only the behind window)")
    ap.add_argument("--mr-replay-repeats", type=int, default=MR().replay_repeats, help="(d) a play already shown may air this many more times, only when nothing new is ready")
    ap.add_argument("--mr-truth", choices=("strict", "tight"), default=MR().truth, help="(e) strict = snap-10..whistle+3; tight = the real snap-to-whistle (lengths assumed)")
    ap.add_argument("--mr-snap-known", action="store_true", help="(e) tight only: the snap is known to --mr-snap-sd (needs a detector we do not have)")
    ap.add_argument("--mr-snap-sd", type=float, default=MR().snap_sd, help="(e) snap detector error sd, s")
    ap.add_argument("--mr-pre", type=float, default=MR().pre_s, help="(e) tight, snap unknown: land this long before the entry's wallclock, s")
    ap.add_argument("--mr-post", type=float, default=MR().post_s, help="(e) leave this long after the entry's wallclock, s")
    ap.add_argument("--mr-err-sd", type=float, default=MR().err_sd, help="true position error of a parked stick, sd s")
    ap.add_argument("--mr-guard-sigmas", type=float, default=MR().guard_sigmas, help="margin each side of a play, in sds")
    ap.add_argument("--mr-behind-min", type=float, default=MR().behind_min, help="Nick's window: at least this far behind the live edge, s")
    ap.add_argument("--mr-behind-max", type=float, default=MR().behind_max, help="...and at most this far")
    ap.add_argument("--mr-fail", type=float, default=MR().fail_p, help="chance a seek or prep fails and is repeated")
    ap.add_argument("--maxrate-mix", action="store_true", help="(WP-11) the app-mix table: flat, all YouTube TV, plan-file, Prime-heavy, all Prime")
    ap.add_argument("--mr-mix", default="", help="(WP-11) per-app prep on a slate mix: plan-file, prime-heavy, all-youtubetv or app=share,... (default: off)")
    ap.add_argument("--split", choices=("all", "train", "valid", "fit", "test"), default="fit",
                    help="read dev nights only (redzone/lab/splits.py). test is the locked set and is refused. Default fit: the locked nights are never read by accident")
    ap.add_argument("--mr-slate-max", type=int, default=0, help="only slates of at most this many games")
    ap.add_argument("--mr-policy", choices=("fifo", "edf", "arbiter"), default=MR().policy, help="which ready play a free stick takes")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    global LG
    LG = League(a.league)
    a.archive = a.archive or f"data/archive/{a.league}"
    if (a.maxrate_mix or a.mr_mix) and a.split == "all":
        a.split = "fit"      # a mix run is new: it starts on dev nights, never the locked ones
    cfg = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        cfg[f"buffered_jump.{k}"] = float(v)
    if a.maxrate or a.maxrate_sweep or a.maxrate_mix:
        return maxrate_main(a)
    rng = random.Random(a.seed)
    rows = []
    for n, (day, games) in enumerate(slates(a.archive, a.min_games, dev_game_ids(a.archive, a.split))):
        if a.max_slates and n >= a.max_slates:
            break
        r = run_slate(games, a, rng, cfg)
        if r:
            r["slate"] = n
            rows.append(r)
            print(f"slate {n:3d}: {r['games']:2d} games {r['minutes']:6.1f} min  cuts/min {r['cuts_per_min']:.2f}  "
                  f"plays shown/min {r['plays_shown_per_min']:.2f} (all games {r['plays_all_games_per_min']:.2f})  "
                  f"mid-play joins {r['joins_mid_play']} walk joins {r['joins_walk']} leaves {r['leaves_mid_play']}  "
                  f"rotations {r['rotations']}  "
                  f"| 10-min PASS {r['windows_pass']}/{r['windows']} (best {r['best_window_jumps']} jumps, ads "
                  f"{r['ad_s']:.0f} s, cut-off plays {r['partial_plays']})")
    if not rows:
        print("no slates")
        return 1
    mins = sum(r["minutes"] for r in rows)
    summ = {"slates": len(rows), "minutes": round(mins),
            "cuts_per_min": round(sum(r["cuts"] for r in rows) / mins, 2),
            "cuts_per_min_p10_p90": [round(x, 2) for x in statistics.quantiles([r["cuts_per_min"] for r in rows], n=10)[::8]]
            if len(rows) >= 2 else None,
            "snap_cuts_per_min": round(sum(r["snap_cuts"] for r in rows) / mins, 2),
            "plays_shown_per_min": round(sum(r["plays_shown"] for r in rows) / mins, 2),
            "single_game_plays_per_min": round(sum(r["plays_all_games_per_min"] * r["minutes"] / r["games"] for r in rows) / mins, 2),
            "joins_mid_play": sum(r["joins_mid_play"] for r in rows),
            "joins_walk": sum(r["joins_walk"] for r in rows),
            "leaves_mid_play": sum(r["leaves_mid_play"] for r in rows),
            "cuts": sum(r["cuts"] for r in rows),
            "pass_bar": {"rule": f">= {PASS_JUMPS} jumps per 10 min, 0 ad seconds, 0 cut-off plays, 0 mid-play joins/leaves",
                         "windows": sum(r["windows"] for r in rows), "windows_pass": sum(r["windows_pass"] for r in rows),
                         "best_window_jumps": max(r["best_window_jumps"] for r in rows),
                         "median_window_jumps": statistics.median([w["jumps"] for r in rows for w in r["window_rows"]] or [0]),
                         "ad_free_windows": sum(r["ad_free_windows"] for r in rows),
                         "ad_s_per_10min": round(sum(r["ad_s"] for r in rows) / max(1, sum(r["windows"] for r in rows)), 1),
                         "cut_off_plays": sum(r["partial_plays"] for r in rows)},
            "args": {k: v for k, v in vars(a).items() if k != "json"}}
    print(json.dumps(summ, indent=1))
    if a.json:
        json.dump({"summary": summ, "slates": rows}, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
