"""Hype v2 inputs: dozens of per-league variables, all from data the feeds already carry.

`Tracker` is a game's running memory (score timeline, win-probability history, lead changes,
runs, player box lines built from the play-by-play). `features()` turns a GameState plus that
memory into a flat {name: value} dict. The SAME function feeds the live scorer and the training
set (redzone/lab/hypetrain.py replays archived games through it), so what the model learned on
is exactly what it sees live.

Every value is bounded (roughly 0..1, a few up to 3) and every input is optional: a missing
field gives the variable's neutral value, never an exception.
"""
from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field

from redzone.models import PERIOD_S, REAL_BREAKS, REGULATION, GameState, League, Stoppage, pro_period

# Final-margin standard deviation per league (scale for "how big is this lead, given the time left").
SD = {League.NFL: 13.5, League.NBA: 12.0, League.NHL: 1.6, League.MLB: 3.2}
# One typical score (a TD, a basket, a goal, a run) in margin units.
UNIT = {League.NFL: 7.0, League.NBA: 2.3, League.NHL: 1.0, League.MLB: 1.0}
# Overtime length in seconds (regular season), for the time left once regulation is over.
OT_S = {League.NFL: 600, League.NBA: 300, League.NHL: 300}
LONG_BREAKS = {Stoppage.HALFTIME, Stoppage.INTERMISSION, Stoppage.END_PERIOD}
_SQRT2PI = math.sqrt(2 * math.pi)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _fade(at: float | None, now: float, span: float) -> float:
    if at is None:
        return 0.0
    return _clip(1.0 - (now - at) / span)


@dataclass
class Tracker:
    """Per-game memory the features need beyond a single snapshot."""
    diffs: list[tuple[float, int]] = field(default_factory=list)    # (now, home - away) on every change
    totals: list[tuple[float, int]] = field(default_factory=list)   # (now, home + away) on every change
    wps: list[tuple[float, float]] = field(default_factory=list)    # (now, home win prob) on every change
    leader: int | None = None
    lead_changes: int = 0
    ties: int = 0
    flip_at: float | None = None
    score_at: float | None = None
    run_side: int = 0          # +1 home, -1 away
    run_pts: int = 0
    max_lead: dict[int, int] = field(default_factory=lambda: {1: 0, -1: 0})  # biggest lead each side held
    seen: set[str] = field(default_factory=set)
    last_play_score: tuple[int, int] | None = None
    player_pts: dict[str, int] = field(default_factory=dict)   # basketball points / hockey goals by name
    star: tuple[str, int] | None = None
    last_id: str | None = None
    changed_at: float | None = None   # when the feed last showed a new play (freshness)

    def observe(self, g: GameState, now: float) -> None:
        lid = g.last_play.id if g.last_play else None
        if lid != self.last_id or self.changed_at is None:
            self.last_id, self.changed_at = lid, now
        hs, as_ = g.home.score, g.away.score
        if hs is not None and as_ is not None:
            d, tot = hs - as_, hs + as_
            if not self.diffs or self.diffs[-1][1] != d or self.totals[-1][1] != tot:
                if self.diffs:
                    prev = self.diffs[-1][1]
                    dh = d - prev
                    side = 1 if dh > 0 else -1 if dh < 0 else 0
                    pts = abs(dh)
                    if side and side == self.run_side:
                        self.run_pts += pts
                    elif side:
                        self.run_side, self.run_pts = side, pts
                    self.score_at = now
                self.diffs.append((now, d))
                self.totals.append((now, tot))
                lead = (d > 0) - (d < 0)
                if self.leader is not None and self.leader != 0 and lead != self.leader:
                    if lead == 0:
                        self.ties += 1
                    else:
                        self.lead_changes += 1
                    self.flip_at = now
                elif self.leader == 0 and lead != 0 and self.ties:
                    self.flip_at = now  # breaking a tie is a go-ahead score: fresh, but not a new lead change
                self.leader = lead
                if lead:
                    self.max_lead[lead] = max(self.max_lead[lead], abs(d))
        wp = g.home_win_prob
        if wp is not None and (not self.wps or self.wps[-1][1] != wp):
            self.wps.append((now, wp))
        self._players(g)
        # Keep memory bounded (a few hours of polling at most).
        for lst in (self.diffs, self.totals, self.wps):
            if len(lst) > 600:
                del lst[: len(lst) - 600]

    def _players(self, g: GameState) -> None:
        lg = g.league.base
        if lg not in (League.NBA, League.NHL):
            return
        for p in g.recent_plays or ([g.last_play] if g.last_play else []):
            if not p or p.id in self.seen:
                continue
            self.seen.add(p.id)
            prev, self.last_play_score = self.last_play_score, p.score or self.last_play_score
            if not p.scoring or not p.text:
                continue
            if lg == League.NBA:
                m = re.match(r"^(.+?) makes ", p.text)
                if not m:
                    continue
                pts = 1 if "free throw" in p.text else 3 if "three point" in p.text else 2
                name = m.group(1)
            else:
                m = re.match(r"^(.+?) Goal\b", p.text) or re.match(r"^Goal - (.+?)[:(,]", p.text)
                if not m:
                    continue
                pts, name = 1, m.group(1).strip()
            self.player_pts[name] = self.player_pts.get(name, 0) + pts
            if self.star is None or self.player_pts[name] > self.star[1]:
                self.star = (name, self.player_pts[name])

    @staticmethod
    def _ago(series: list, now: float, secs: float):
        """Value of a (time, value) change series as of `secs` ago (its first value if older)."""
        if not series:
            return None
        i = bisect.bisect_right(series, (now - secs, float("inf"))) - 1
        return series[max(0, i)][1]

    def diff_ago(self, now: float, secs: float) -> int | None:
        """Home margin `secs` ago."""
        return self._ago(self.diffs, now, secs)

    def total_ago(self, now: float, secs: float) -> int | None:
        return self._ago(self.totals, now, secs)

    def wp_vol(self, now: float, secs: float = 300.0) -> float:
        """Sum of win-probability moves over the last `secs` (how much this game is swinging)."""
        w = self.wps
        if len(w) < 2:
            return 0.0
        i = max(0, bisect.bisect_left(w, (now - secs, float("-inf"))) - 1)
        return sum(abs(w[j + 1][1] - w[j][1]) for j in range(i, len(w) - 1))


def time_left_frac(g: GameState) -> float:
    """Share of the game still to play (regulation = 1.0 total). OT counts its own clock."""
    lg = g.league
    reg = REGULATION[lg]
    p = g.period or 0
    if p <= 0:
        return 1.0
    if lg == League.MLB:
        done = (min(p, 9) - 1) + (0.5 if g.half == "bottom" else 0.0)
        return max(0.06, (9 - done) / 9) if p <= 9 else 0.06
    length = PERIOD_S[lg]
    total = reg * length
    clk = g.clock_s if g.clock_s is not None else length / 2
    if p > reg:
        ot = OT_S.get(lg.base, 300)
        return max(0.004, min(clk, ot) / total)
    return max(0.004, ((reg - p) * length + min(clk, length)) / total)


def features(g: GameState, t: Tracker, now: float, *, is_garbage: bool = False,
             legacy: float = 0.0) -> dict[str, float]:
    lg = g.league.base
    f: dict[str, float] = {}
    rem = time_left_frac(g)
    prog = 1.0 - rem if (g.period or 0) <= REGULATION[g.league] else 1.0
    q = pro_period(g.league, g.period, g.clock_s)
    reg_q = REGULATION[lg]
    ot = (g.period or 0) > REGULATION[g.league]
    d = g.score_diff
    ad = abs(d) if d is not None else None
    clk = g.clock_s
    sd, unit = SD[lg], UNIT[lg]

    from redzone.scoring.engine import crunch_time
    f["legacy"] = legacy / 100.0
    f["legacy2"] = f["legacy"] ** 2          # the hand-tuned score's top end matters most
    f["crunch"] = 1.0 if crunch_time(g) else 0.0
    f["prog"] = prog
    f["prog2"] = prog * prog
    f["ot"] = 1.0 if ot else 0.0

    # Margin x time left: z = how many "remaining-game standard deviations" the lead is.
    if ad is not None:
        z = ad / (sd * math.sqrt(rem))
        coin = math.exp(-0.5 * z * z)
        f["z_margin"] = _clip(z, 0, 4) / 4
        f["coin"] = coin
        f["close_late"] = coin * prog * prog
        f["one_score"] = 1.0 if ad <= unit + 0.5 else 0.0
        f["one_score_late"] = f["one_score"] * prog * prog
        f["tied"] = 1.0 if ad == 0 else 0.0
        # WP leverage of the next score: how far one more score moves the win probability.
        f["lev_next"] = _clip(unit * coin / (sd * math.sqrt(rem) * _SQRT2PI) * 2.0)
    else:
        f["z_margin"], f["coin"], f["close_late"] = 0.5, 0.5, 0.25 * prog * prog
        f["one_score"] = f["one_score_late"] = f["tied"] = f["lev_next"] = 0.0

    # Live win probability (NFL/NBA/college from ESPN; NHL has none): coin flip, and how much it swings.
    wp = g.home_win_prob
    f["wp_avail"] = 1.0 if wp is not None else 0.0
    f["wp_coin"] = (1 - abs(2 * wp - 1)) if wp is not None else f["coin"]
    f["wp_coin_late"] = f["wp_coin"] * prog * prog
    f["wp_vol5"] = _clip(t.wp_vol(now, 300) / 0.5)
    f["wp_vol2"] = _clip(t.wp_vol(now, 120) / 0.3)

    # Momentum: margin tightening, unanswered runs, comebacks, pace.
    if d is not None:
        d5 = t.diff_ago(now, 300)
        d10 = t.diff_ago(now, 600)
        f["tighten5"] = _clip(((abs(d5) - ad) / unit) / 3, -1, 1) if d5 is not None else 0.0
        f["tighten10"] = _clip(((abs(d10) - ad) / unit) / 3, -1, 1) if d10 is not None else 0.0
        trailing = -1 if d > 0 else 1 if d < 0 else 0
        # Comeback: the side now within one score (or ahead) once trailed by a lot.
        cb = 0.0
        for side in (1, -1):
            was_down = t.max_lead[-side]
            if was_down and (side == -trailing or ad <= unit + 0.5 or trailing == 0):
                cb = max(cb, (was_down - ad) / unit)
        f["comeback"] = _clip(cb / 4, 0, 1)
        f["comeback_late"] = f["comeback"] * prog
    else:
        f["tighten5"] = f["tighten10"] = f["comeback"] = f["comeback_late"] = 0.0
    f["run"] = _clip(t.run_pts / (unit * 4))
    # A run by the trailing team is the one that matters.
    f["run_trailing"] = f["run"] if (d is not None and t.run_side and t.run_side * d <= 0) else 0.0
    tot, t10 = (g.home.score or 0) + (g.away.score or 0), t.total_ago(now, 600)
    f["pace10"] = _clip(((tot - t10) / unit) / 4) if t10 is not None else 0.0
    f["lead_changes"] = _clip(t.lead_changes / 8)
    f["ties"] = _clip(t.ties / 6)
    f["fresh_flip"] = _fade(t.flip_at, now, 300)
    f["fresh_score"] = _fade(t.score_at, now, 120)

    # Breaks: nothing happens in a commercial; long breaks are worse than a timeout.
    f["brk_long"] = 1.0 if g.stoppage in LONG_BREAKS else 0.0
    f["brk_short"] = 1.0 if (g.stoppage in REAL_BREAKS and g.stoppage not in LONG_BREAKS) else 0.0
    f["garbage"] = 1.0 if is_garbage else 0.0
    # Freshness: a feed that has shown nothing new for minutes (outside a long break) is either
    # stuck or in a stoppage nobody logged; either way nothing is about to happen ON SCREEN.
    f["quiet"] = _clip((now - t.changed_at) / 600) if t.changed_at is not None else 0.0

    # Pregame expectation: a close spread, and the underdog leading late.
    sp = g.spread
    f["spread_close"] = (1 - _clip(abs(sp) / 14)) if sp is not None else 0.5
    f["upset"] = 0.0
    if sp is not None and d is not None and abs(sp) >= 3 and prog >= 0.5:
        dog_margin = d if sp > 0 else -d
        if dog_margin >= 0:
            f["upset"] = _clip(abs(sp) / 10) * (0.5 + 0.5 * prog)
    f["national_tv"] = 1.0 if g.national_tv else 0.0
    ranks = [x.rank for x in (g.home, g.away) if x.rank]
    f["ranked"] = _clip(len(ranks) / 2)

    # Stars: a player going off this game (basketball points, hockey goals) from the play-by-play.
    f["star"] = 0.0
    if t.star:
        n = t.star[1]
        f["star"] = _clip((n - 25) / 20) if lg == League.NBA else (_clip((n - 1) / 2) if lg == League.NHL else 0.0)

    if lg == League.NFL:
        _nfl(g, f, prog, q, clk, d)
    elif lg == League.NBA:
        _nba(g, f, q, reg_q, clk, ad)
    elif lg == League.NHL:
        _nhl(g, f, clk, d, ad)
    elif lg == League.MLB:
        _mlb(g, f, d)
    return f


def _nfl(g, f, prog, q, clk, d):
    s = g.situation
    ytg = s.yards_to_goal
    has = bool(s.possession) and ytg is not None and not (g.stoppage in LONG_BREAKS)
    off = None
    if d is not None and s.possession:
        off = d if s.possession == g.home.abbr else -d
    f["field"] = (1 - ytg / 100) if has else 0.0
    f["red_zone"] = 1.0 if has and ytg <= 20 else 0.0
    f["inside10"] = 1.0 if has and ytg <= 10 else 0.0
    f["goal_to_go"] = 1.0 if has and s.goal_to_go else 0.0
    f["third_short"] = 1.0 if has and s.down == 3 and (s.distance or 99) <= 3 else 0.0
    f["fourth"] = 1.0 if has and s.down == 4 else 0.0
    f["red_zone_late"] = f["red_zone"] * prog * prog
    trail_close = off is not None and -8 <= off <= 0
    f["trail_ball"] = 1.0 if has and trail_close else 0.0
    f["trail_ball_late"] = f["trail_ball"] * prog * prog
    f["trail_in_range_late"] = f["trail_ball_late"] * (1.0 if ytg is not None and ytg <= 45 else 0.0)
    two = clk is not None and clk <= 120 and q in (2, 4)
    f["two_minute"] = 1.0 if two else 0.0
    f["drill"] = 1.0 if (q >= 4 and two and trail_close) else 0.0
    if s.possession and s.timeouts_home is not None and s.timeouts_away is not None:
        mine = s.timeouts_home if s.possession == g.home.abbr else s.timeouts_away
        f["off_timeouts_late"] = (mine / 3) * prog * prog
    else:
        f["off_timeouts_late"] = 0.0


def _nba(g, f, q, reg_q, clk, ad):
    f["clutch5"] = 1.0 if (q >= 4 and clk is not None and clk <= 300 and ad is not None and ad <= 5) else 0.0
    f["clutch2"] = 1.0 if (q >= 4 and clk is not None and clk <= 120 and ad is not None and ad <= 5) else 0.0
    f["last_shot"] = 1.0 if (q >= 4 and clk is not None and clk <= 24 and ad is not None and ad <= 3) else 0.0
    # Possessions left vs the margin: each team gets about one trip per 28 s; 9 down with 4:24
    # left is ~1 point per remaining trip, which is very catchable.
    if q >= 4 and clk is not None and ad is not None:
        trips = max(1.0, clk / 28.0)
        f["margin_per_poss"] = _clip(ad / trips, 0, 3) / 3
        f["catchable"] = 1.0 if ad <= 0.6 * trips + 3 else 0.0
    else:
        f["margin_per_poss"], f["catchable"] = 0.5, 0.0


def _nhl(g, f, clk, d, ad):
    s = g.situation
    p = g.period or 0
    f["power_play"] = 1.0 if s.power_play else 0.0
    f["five_on_three"] = 1.0 if s.strength in ("5v3", "3v5") else 0.0
    f["empty_net"] = 1.0 if s.empty_net else 0.0
    f["pulled_window"] = 1.0 if (p == 3 and clk is not None and clk <= 150 and ad is not None and 1 <= ad <= 2) else 0.0
    f["late_one_goal"] = 1.0 if (p == 3 and clk is not None and clk <= 300 and ad is not None and ad <= 1) else 0.0
    f["shootout"] = 1.0 if g.period_type == "SO" or p >= 5 else 0.0


def _mlb(g, f, d):
    from redzone.scoring.engine import estimate_li
    s = g.situation
    runners = s.runners or (False, False, False)
    outs = s.outs if s.outs is not None else 0
    inning = g.period or 1
    diff = d or 0
    bat = diff if g.half == "bottom" else -diff
    on = sum(runners)
    over = outs >= 3
    f["runners"] = 0.0 if over else on / 3
    f["risp"] = 0.0 if over else (1.0 if runners[1] or runners[2] else 0.0)
    f["outs"] = min(outs, 2) / 2
    li = s.leverage_index if s.leverage_index is not None else estimate_li(inning, bat, outs, runners)
    f["li"] = 0.0 if over else _clip(li / 6)
    f["tying_run"] = 1.0 if (not over and bat <= 0 and -bat <= on + 1 and inning >= 7) else 0.0
    f["walkoff"] = 1.0 if (not over and g.half == "bottom" and inning >= 9 and bat <= 0 and -bat <= on + 1) else 0.0
    f["inning_over"] = 1.0 if over else 0.0
    f["no_hitter"] = 1.0 if s.no_hitter_for and inning >= 6 else 0.0


# Plain-English phrases for the factors, used in the reason string.
PHRASES = {
    "close_late": "close game, late", "coin": "anyone's game", "one_score_late": "one-score game, late",
    "tied": "tied", "lev_next": "next score swings it", "wp_coin_late": "win probability on a knife's edge",
    "wp_vol5": "win probability swinging", "wp_vol2": "win probability swinging", "tighten5": "the lead is shrinking",
    "tighten10": "the lead is shrinking", "comeback": "comeback on", "comeback_late": "comeback on", "run": "on a run",
    "run_trailing": "trailing team on a run", "pace10": "back-and-forth scoring", "lead_changes": "lead changes",
    "ties": "ties", "fresh_flip": "lead just changed hands", "fresh_score": "just scored", "upset": "upset brewing",
    "national_tv": "national TV", "ranked": "ranked matchup", "star": "a star is going off", "ot": "overtime",
    "spread_close": "pick'em game", "red_zone": "red zone", "inside10": "inside the 10", "goal_to_go": "goal to go",
    "third_short": "3rd & short", "fourth": "4th down", "red_zone_late": "late red-zone drive",
    "trail_ball": "trailing team has the ball", "trail_ball_late": "trailing team has the ball, late",
    "trail_in_range_late": "trailing team in scoring range", "two_minute": "two-minute window",
    "drill": "two-minute drill", "off_timeouts_late": "offense has timeouts", "field": "driving",
    "clutch5": "clutch time", "clutch2": "final two minutes", "last_shot": "last-shot possession",
    "catchable": "within reach", "power_play": "power play", "five_on_three": "5-on-3",
    "empty_net": "goalie pulled", "pulled_window": "extra-attacker window", "late_one_goal": "one-goal game, late",
    "shootout": "shootout", "runners": "runners on", "risp": "runners in scoring position",
    "li": "high-leverage at-bat", "tying_run": "tying run aboard", "walkoff": "walk-off spot",
    "no_hitter": "no-hitter watch", "legacy": "", "legacy2": "", "crunch": "crunch time", "prog": "", "prog2": "", "quiet": "",
}
