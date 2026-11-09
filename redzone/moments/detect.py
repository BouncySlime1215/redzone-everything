"""Moment detectors over one ESPN game summary (play-by-play): returns typed, timestamped moments.

Why: the planner's per-league rates (docs/agents/planner/2026-11-09-product-per-league.md section 3) came from
a throwaway script. This is the same logic as a pure, tested module so the queue, the fill ladder and the
slate mode read one definition of "something worth showing" (build plan WP-07). It reads a summary dict and
never the clock, the network or the TV, so it works on archived nights and on a live feed that is still growing.

    ms = detect("nfl", summary)          # [Moment(t, game_id, tier, kind, play_id), ...] in feed order
    lo, hi = game_span(ms.times)         # first and last play wall clock; moments outside are dropped by callers

Detectors never look past the play they fire on except where the kind says so (a red-zone trip is the first snap
inside the 20 of a drive). A play without a wall clock yields no moment (it cannot be placed in time).
"""
from __future__ import annotations

import collections
import datetime as dt
import re
from dataclasses import dataclass, field

from redzone.moments import tiers


@dataclass(frozen=True)
class Moment:
    t: float          # wall-clock epoch seconds of the play
    game_id: str
    tier: int         # 1 must show, 2 setup, 3 highlight (tiers.yaml)
    kind: str
    play_id: str = ""


@dataclass
class Detected:
    """All moments of one game plus every play timestamp (for the game's span and record gaps)."""
    moments: list[Moment] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    def __iter__(self):
        return iter(self.moments)

    def __len__(self):
        return len(self.moments)


def parse_ts(s) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clock_s(p: dict) -> float | None:
    v = (p.get("clock") or {}).get("displayValue") or ""
    m = re.match(r"^(\d+):(\d+)", v)
    if m:
        return int(m[1]) * 60 + int(m[2])
    try:
        return float(v)
    except ValueError:
        return None


def _wp_swings(d: dict) -> dict[str, float]:
    """playId -> absolute home win-probability change (0..1) that play caused."""
    out, prev = {}, None
    for x in d.get("winprobability") or []:
        h = x.get("homeWinPercentage")
        if h is None:
            continue
        if prev is not None:
            out[str(x.get("playId"))] = abs(h - prev)
        prev = h
    return out


def _emit(out: Detected, league: str, gid: str, t, kind: str, pid="") -> None:
    if t:
        out.moments.append(Moment(t, gid, tiers.tier_of(league, kind) or 3, kind, str(pid or "")))


def _football(league: str, gid: str, d: dict) -> Detected:
    out, wp = Detected(), _wp_swings(d)
    for dr in (d.get("drives") or {}).get("previous") or []:
        rz = False
        for p in dr.get("plays") or []:
            t = parse_ts(p.get("wallclock"))
            ty = (p.get("type") or {}).get("text") or ""
            tl, tx = ty.lower(), (p.get("text") or "").lower()
            if t:
                out.times.append(t)
            s = p.get("start") or {}
            down, y = s.get("down") or 0, s.get("yardsToEndzone")
            nopl = "no play" in tx
            td = "touchdown" in tl or bool(p.get("scoringPlay") and "touchdown" in tx and not nopl)
            pid = p.get("id")
            if td:
                _emit(out, league, gid, t, "td", pid)
            if not nopl and ("interception" in tl or "fumble recovery (opponent)" in tl or "blocked" in tl
                             or ty == "Safety" or "muffed" in tl):
                _emit(out, league, gid, t, "turnover", pid)
            if (not nopl and "kickoff" not in tl and "punt" not in tl and "timeout" not in tl
                    and down and y and y <= 20 and not rz):
                rz = True
                _emit(out, league, gid, t, "rz_trip", pid)
            if not nopl and "field goal" in tl:
                _emit(out, league, gid, t, "fg", pid)
            if down == 4 and not nopl and not any(k in tl for k in ("punt", "field goal", "timeout", "penalty", "kickoff")):
                _emit(out, league, gid, t, "4th_try", pid)
            gain = p.get("statYardage") or 0
            if not nopl and gain >= 10 and not td and any(k in tl for k in ("rush", "pass")):
                _emit(out, league, gid, t, "gain10", pid)
            dist = s.get("distance") or 0
            if not nopl and down == 3 and dist >= 5 and gain >= (dist or 99):
                _emit(out, league, gid, t, "big3rd", pid)
            if wp.get(str(pid), 0) >= 0.05 and not td:
                _emit(out, league, gid, t, "wp5", pid)
    return out


def _hoops(league: str, gid: str, d: dict) -> Detected:
    out, wp, halves = Detected(), _wp_swings(d), league == "cbb"
    prev_h = prev_a = lead = 0
    run, fired = [0, 0], [False, False]
    for p in d.get("plays") or []:
        t = parse_ts(p.get("wallclock"))
        if not t:
            continue
        out.times.append(t)
        ty = (p.get("type") or {}).get("text") or ""
        tyl, pid = ty.lower(), p.get("id")
        h, a = p.get("homeScore") or 0, p.get("awayScore") or 0
        per, c = (p.get("period") or {}).get("number") or 1, clock_s(p)
        last, ot = (per >= 2, per >= 3) if halves else (per >= 4, per >= 5)
        late = ot or (last and c is not None and c <= 300)
        margin_before = abs(prev_h - prev_a)
        sc = p.get("scoringPlay")
        is_fg = bool(p.get("shootingPlay")) and "free throw" not in tyl and "freethrow" not in tyl
        lead_change = False
        if sc and h + a > prev_h + prev_a:
            nl = (h > a) - (h < a)
            lead_change = nl != lead
            lead = nl
            side = 0 if h > prev_h else 1
            run[side] += h + a - prev_h - prev_a
            run[1 - side], fired[1 - side] = 0, False
            if run[side] >= 8 and not fired[side]:
                fired[side] = True
                _emit(out, league, gid, t, "run8", pid)
        second_half = per >= 2 if halves else per >= 3
        clutch = False
        if sc and is_fg and late and margin_before <= 5:
            _emit(out, league, gid, t, "clutch_fg", pid)
            clutch = True
        elif sc and is_fg and lead_change and second_half:
            _emit(out, league, gid, t, "lead_change", pid)
        if sc and is_fg and "dunk" in tyl:
            _emit(out, league, gid, t, "dunk", pid)
        if sc and is_fg and (p.get("scoreValue") or 0) == 3:
            _emit(out, league, gid, t, "three", pid)
        w = wp.get(str(pid), 0)
        if w >= 0.10 and not clutch:
            _emit(out, league, gid, t, "wp10", pid)
        elif w >= 0.05 and not clutch:
            _emit(out, league, gid, t, "wp5", pid)
        if late and margin_before <= 5 and ("Turnover" in ty or "Steal" in ty):
            _emit(out, league, gid, t, "clutch_tov", pid)
        prev_h, prev_a = h, a
    return out


_PEN_EXCLUDED = ("Fighting", "Misconduct", "Instigator", "Delay Game", "Shootout")
_HOCKEY_NOISE = ("Period Start", "Period End", "End of Game", "Shot", "Blocked", "Missed", "Hit", "Giveaway",
                 "Takeaway", "Stoppage", "Face Off")


def _hockey(league: str, gid: str, d: dict) -> Detected:
    out, plays = Detected(), d.get("plays") or []
    pens: dict = collections.defaultdict(list)
    for p in plays:
        t = parse_ts(p.get("wallclock"))
        if not t:
            continue
        out.times.append(t)
        ty = (p.get("type") or {}).get("text") or ""
        per, c, pid = (p.get("period") or {}).get("number") or 1, clock_s(p), p.get("id")
        if ty == "Goal":
            _emit(out, league, gid, t, "goal", pid)
        elif ty in _HOCKEY_NOISE:
            if ty == "Shot":
                _emit(out, league, gid, t, "sog", pid)
        elif ty == "Fighting":
            _emit(out, league, gid, t, "fight", pid)
        elif "Penalty Shot" in ty or "on Breakaway" in ty:
            _emit(out, league, gid, t, "penalty_shot", pid)
        elif not any(k in ty for k in _PEN_EXCLUDED) and p.get("team"):
            pens[(per, c)].append((t, (p.get("team") or {}).get("id"), pid))
    for v in pens.values():
        # penalties at one clock tick: a one-team group is a power play; both teams (coincidental) is not
        if len(v) == 1 or len({x[1] for x in v}) == 1:
            _emit(out, league, gid, v[0][0], "pp_start", v[0][2])
    fired: set = set()
    for p in plays:
        t, per, c = parse_ts(p.get("wallclock")), (p.get("period") or {}).get("number") or 1, clock_s(p)
        if t and per >= 3 and c is not None and per not in fired:
            margin = abs((p.get("homeScore") or 0) - (p.get("awayScore") or 0))
            if per == 3 and 1200 - c <= 120 and margin <= 1:
                _emit(out, league, gid, t, "late_1goal", p.get("id"))
                fired.add(per)
            if per >= 4:
                _emit(out, league, gid, t, "ot_start", p.get("id"))
                fired.add(per)
    return out


def _baseball(league: str, gid: str, d: dict) -> Detected:
    out, wp = Detected(), _wp_swings(d)
    at_bats: dict = collections.OrderedDict()
    for p in d.get("plays") or []:
        t = parse_ts(p.get("wallclock"))
        if t:
            out.times.append(t)
        if p.get("atBatId"):
            at_bats.setdefault(p["atBatId"], []).append(p)
    prev_end = (0, 0)
    for ps in at_bats.values():
        t0, t1 = parse_ts(ps[0].get("wallclock")), parse_ts(ps[-1].get("wallclock"))
        parts = {x.get("type") for x in ps[0].get("participants") or []}
        risp = "onSecond" in parts or "onThird" in parts
        loaded = {"onFirst", "onSecond", "onThird"} <= parts
        inn = (ps[0].get("period") or {}).get("number") or 1
        h1, a1 = ps[-1].get("homeScore") or 0, ps[-1].get("awayScore") or 0
        h0, a0 = prev_end
        prev_end = (max(h0, h1), max(a0, a1))
        runs = max(0, h1 + a1 - h0 - a0)
        hr = any("home run" in ((x.get("type") or {}).get("text") or "").lower()
                 or "homered" in (x.get("text") or "").lower() for x in ps)
        late = inn >= 7 and abs(h0 - a0) <= 3
        last_id, first_id = ps[-1].get("id"), ps[0].get("id")
        if hr:
            _emit(out, league, gid, t1, "hr", last_id)
        elif runs:
            _emit(out, league, gid, t1, "run_scored", last_id)
        if not hr and not runs and (loaded or (risp and late)):
            _emit(out, league, gid, t0, "big_pa", first_id)
        swing = max([wp.get(str(x.get("id")), 0) for x in ps] + [0])
        if swing >= 0.05 and not hr and not runs:
            _emit(out, league, gid, t1, "wp5", last_id)
        if not hr and not runs and risp:
            _emit(out, league, gid, t0, "risp_pa", first_id)
    return out


_BY_FAMILY = {"football": _football, "hoops": _hoops, "hockey": _hockey, "baseball": _baseball}


def detect(league: str, summary: dict, game_id: str = "") -> Detected:
    """All moments of one game summary, with the play timestamps that frame the game."""
    lg = str(league).lower()
    return _BY_FAMILY[tiers.family(lg)](lg, game_id, summary)


def game_span(times, min_plays: int = 30, min_h: float = 1.2, max_h: float = 7.0) -> tuple[float, float] | None:
    """(first, last) play time when the feed looks like a whole game (30+ timed plays, 1.2-7 h), else None."""
    ta = sorted(times)
    if len(ta) < min_plays:
        return None
    s, e = ta[0], ta[-1]
    return (s, e) if min_h * 3600 < e - s < max_h * 3600 else None
