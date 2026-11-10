"""Value v2: graded moments of one ESPN game summary, each worth base x leverage x stakes.

Why: value v1 is 3 or 4 kinds per sport at fixed 5 / 2 / 0.5 points, so a garbage-time touchdown in a 38-3 game equals a
go-ahead one with 0:40 left (docs/plans/2026-11-09-value-v2.md). This is that plan's build step 1 for football and
basketball: the base tables of section 1, the interim margin-by-time leverage table of section 3 and the stakes of
section 4 that the summary really carries. v1 (detect.py, tiers.yaml) is not touched and nothing reads this by default.

    for m in grade("nfl", summary, game_id):      # [Graded(t, play_id, kind, base, leverage, stakes, value, ...), ...]
        m.value == m.base * m.leverage * m.stakes  # dead value (kneel-downs, non-clutch free throws) is flat and negative
        m.replay == m.base * m.leverage ** a * m.stakes   # a = value2.yaml replay_leverage_exponent; dead moments 0

`value` is the LIVE value (worth cutting to now; the training label's source). `replay` is the value of airing the play
later as a short catch-up clip (Nick, Nov 9: a garbage-time interception is "replay worthy" though never worth a live cut).

Pure: no clock, no network, no TV. Every number comes from value2.yaml so it can be fitted later (section 5).
Published win probability never enters: `winprobability` is not read (it was fit across seasons, see value_shards.py).
Rows of the spec that play-by-play cannot show are listed in value2.yaml `not_detectable`, not guessed.
A play without a wall clock yields no moment, the same rule as v1. The function is named grade, not score, because the
contract check (K4) keeps that name for the one decision path, and this ranks nothing: it labels plays.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from redzone.moments.detect import clock_s, football_drives, parse_ts

_PATH = Path(__file__).with_name("value2.yaml")
_TEXT_KEEP = 170
_TD_YARDS = re.compile(r"for (-?\d+) yards?,? touchdown")
_RETURN_YARDS = re.compile(r"(?:intercepted by|recovered by).*? for (\d+) yards?")
_FG_YARDS = re.compile(r"(\d+) yard field goal")
_DPI_YARDS = re.compile(r"defensive pass interference, (\d+) yards?")
_KICK_FROM = re.compile(r"from ([a-z]+) \d+")
_RECOVERED_BY = re.compile(r"recovered by ([a-z]+)-")
_FEET = re.compile(r"(\d+)-foot")
_ST_TD = ("kickoff", "punt", "blocked", "field goal")
_NO_TRY = ("punt", "field goal", "timeout", "penalty", "kickoff")       # v1's rule for "not a 4th-down try"
_SOFT_TECH = ("3-second", "delay", "flopping", "hanging", "free throw")


@dataclass(frozen=True)
class Graded:
    t: float                 # wall-clock epoch seconds of the play
    play_id: str
    kind: str
    base: float              # table points plus grading bonuses
    leverage: float          # 0.3 garbage time .. 3.0 one score late or overtime
    stakes: float            # 1.0 .. 2.0
    value: float             # base x leverage x stakes; dead value is its flat negative base
    margin: int              # points between the teams before the play
    margin_after: int
    seconds_left: float      # regulation seconds left (the overtime period's clock in overtime); -1 unknown
    period: int
    game_id: str = ""
    grades: tuple[str, ...] = ()
    dead: bool = False
    text: str = ""
    replay: float = 0.0      # base x leverage**a x stakes (a = replay_leverage_exponent); 0 for dead value. Default keeps old callers
                             # and pickles working (a pickle from before this field reads 0.0 from the class default)


@lru_cache(maxsize=1)
def config() -> dict:
    with _PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def family(league: str, cfg: dict | None = None) -> str:
    """'football' or 'hoops'. KeyError for a league value v2 does not grade yet (hockey and baseball: build step 3)."""
    return (cfg or config())["leagues"][str(league).lower()]


def seconds_left(league: str, period: int, clock: float | None, cfg: dict | None = None) -> tuple[float, bool]:
    """(regulation seconds left, in overtime). In overtime the seconds are the overtime period's own clock."""
    periods, length = (cfg or config())["regulation"][str(league).lower()]
    if clock is None:
        return -1.0, period > periods
    if period > periods:
        return float(clock), True
    return (periods - period) * float(length) + float(clock), False


def leverage(league: str, margin: float, left: float, overtime: bool = False, cfg: dict | None = None) -> float:
    """The interim margin-by-time multiplier. Never rises with the margin; rises toward the end for a one-score game and falls
    toward the end for a blowout. Unknown clock (left < 0, not overtime) reads 1.0."""
    cfg = cfg or config()
    lev = cfg["leverage"]
    tab = lev[family(league, cfg)]
    if left < 0 and not overtime:
        return 1.0
    col = next((i for i, m in enumerate(tab["margin_max"]) if abs(margin) <= m), len(tab["margin_max"]))
    reg = cfg["regulation"][str(league).lower()]
    marks = [f * reg[0] * reg[1] for f in tab["left_frac"]] + [float(s) for s in tab["left_secs"]]
    row = len(marks) if overtime else sum(1 for m in marks if left <= m)
    return float(min(lev["cap"], max(lev["floor"], tab["table"][row][col])))


@dataclass(frozen=True)
class Stakes:
    """What the summary says about how much the game matters. Every field is from before the game."""
    season_type: int = 0
    postseason: bool = False
    ranked_vs_ranked: bool = False
    spread: float | None = None      # points the favourite gives, always >= 0
    underdog: str = ""               # "home" or "away" when a spread names one

    def fixed(self, cfg: dict | None = None) -> float:
        s = (cfg or config())["stakes"]
        return max(1.0, s["postseason"] if self.postseason else 1.0, s["ranked_vs_ranked"] if self.ranked_vs_ranked else 1.0)


def game_stakes(summary: dict, cfg: dict | None = None) -> Stakes:
    """Season type, college ranks and the pre-game spread, where the summary has them (missing means no stake, never a guess)."""
    s = (cfg or config())["stakes"]
    header = summary.get("header") or {}
    stype = int((header.get("season") or {}).get("type") or 0)
    comp = (header.get("competitions") or [{}])[0] or {}
    ranks = [c.get("rank") for c in comp.get("competitors") or []]
    ranked = len(ranks) == 2 and all(isinstance(r, int) and 1 <= r <= s["ranked_max"] for r in ranks)
    spread, dog = None, ""
    pick = (summary.get("pickcenter") or [{}])[0] or {}
    if isinstance(pick.get("spread"), (int, float)) and pick["spread"]:
        spread = abs(float(pick["spread"]))
        home_fav = (pick.get("homeTeamOdds") or {}).get("favorite")
        away_fav = (pick.get("awayTeamOdds") or {}).get("favorite")
        if home_fav is None and away_fav is None:
            home_fav = pick["spread"] < 0            # ESPN writes the spread from the home side: negative = home favoured
        dog = "away" if home_fav else "home"
    return Stakes(stype, stype in s["postseason_types"], ranked, spread, dog)


def _moment_stakes(st: Stakes, fam: str, home_margin_after: int, final_or_ot: bool, cfg: dict) -> float:
    s, out = cfg["stakes"], st.fixed(cfg)
    if st.underdog and st.spread is not None and st.spread >= s["upset_spread"] and final_or_ot:
        dog_margin = home_margin_after if st.underdog == "home" else -home_margin_after
        if dog_margin >= -cfg[fam]["limits"]["one_score"]:
            out = max(out, s["upset"])
    return float(out)


def _band(bands: list, x: float) -> float | None:
    hit = [pts for at_least, pts in bands if x >= at_least]
    return float(hit[-1]) if hit else None


class _Play:
    """One play's facts and the moments it earned, so every kind on a play shares one leverage and one stakes number."""

    def __init__(self, league: str, gid: str, cfg: dict, fam: str, st: Stakes, fixed_stakes: float | None, out: list):
        self.league, self.gid, self.cfg, self.fam, self.st, self.fixed, self.out = league, gid, cfg, fam, st, fixed_stakes, out
        self.c = cfg[fam]

    def at(self, p: dict, t: float | None, before: tuple[int, int], after: tuple[int, int], period: int, clock: float | None):
        self.p, self.t, self.period = p, t, period
        self.before, self.after = before, after
        self.left, self.ot = seconds_left(self.league, period, clock, self.cfg)
        m = min(abs(before[0] - before[1]), abs(after[0] - after[1]))
        self.lev = leverage(self.league, m, self.left, self.ot, self.cfg)
        final = period >= self.cfg["regulation"][self.league][0]
        self.stk = self.fixed if self.fixed is not None else _moment_stakes(self.st, self.fam, after[0] - after[1], final, self.cfg)

    def add(self, kind: str, base: float | None = None, grades: tuple = (), dead: bool = False):
        if not self.t:
            return
        if base is None:
            base = self.c["dead"][kind] if dead else self.c["base"][kind]
        base = float(base) + sum(float(self.c["bonus"][g]) for g in grades)
        lev, stk = (1.0, 1.0) if dead else (self.lev, self.stk)
        rep = 0.0 if dead else base * lev ** float(self.cfg["replay_leverage_exponent"]) * stk
        self.out.append(Graded(self.t, str(self.p.get("id") or ""), kind, base, lev, stk, base * lev * stk,
                               abs(self.before[0] - self.before[1]), abs(self.after[0] - self.after[1]), self.left, self.period,
                               self.gid, tuple(grades), dead, " ".join(str(self.p.get("text") or "").split())[:_TEXT_KEEP], rep))


def two_point_ties_or_leads(text: str, before: tuple[int, int], after: tuple[int, int]) -> bool:
    """True when the play text says the two-point try succeeded and it tied the game or took the lead.

    ESPN's wording on the archived NFL dev nights is "TWO-POINT CONVERSION ATTEMPT. ... ATTEMPT SUCCEEDS." or "... ATTEMPT
    FAILS.", and the row's score already includes the try (a made try moves one side by 8, a failed one by 6). A text with
    both words (a reviewed and reversed try) or neither, or a row where both sides or no side scored, is ambiguous: False.
    Ties or leads: the scoring side is level or ahead after the try and was level or behind without its 2 points."""
    tx = text.upper()
    if "ATTEMPT SUCCEEDS" not in tx or "ATTEMPT FAILS" in tx:
        return False
    dh, da = after[0] - before[0], after[1] - before[1]
    if (dh > 0) == (da > 0) or min(dh, da) < 0:
        return False
    mine = (after[0] - after[1]) * (1 if dh > 0 else -1)
    return 0 <= mine <= 2


def _row_score(p: dict, prev: tuple[int, int]) -> tuple[int, int]:
    h, a = p.get("homeScore"), p.get("awayScore")
    return (prev[0] if h is None else int(h), prev[1] if a is None else int(a))


def _football_score(p: dict, prev: tuple[int, int]) -> tuple[int, int]:
    """The score after this play. A scoring row is trusted; any other row may only raise it, because timeouts and penalties
    in the feed sometimes carry a stale score (34 of 37,499 archived NFL rows)."""
    row = _row_score(p, prev)
    return row if p.get("scoringPlay") else (max(prev[0], row[0]), max(prev[1], row[1]))


def _hoops_score(p: dict, prev: tuple[int, int], team, side_of: dict) -> tuple[tuple[int, int], int, int]:
    """(score after, points, side 0 home / 1 away) of a scoring row, kept as our own running count. The feed's score columns
    are wrong on about 2% of rows (stale on substitutions, swapped on free throws with equal stamps), so the count adds
    each basket's own value to the side its team was last seen scoring for, and falls back to the row's columns."""
    row = _row_score(p, prev)
    dh, da = row[0] - prev[0], row[1] - prev[1]
    val = int(p.get("scoreValue") or 0)
    one_sided = (dh > 0) != (da > 0) and min(dh, da) == 0 and (not val or max(dh, da) == val)
    if team is not None and team in side_of:
        side, pts = side_of[team], val or max(dh, da, 0)
    elif one_sided:
        side, pts = (0 if dh > 0 else 1), val or dh + da
        if team is not None:
            side_of[team] = side
    else:
        side, pts = (0 if dh >= da else 1), val or max(dh + da, 0)
    return ((prev[0] + pts, prev[1]) if side == 0 else (prev[0], prev[1] + pts)), pts, side


def _football(league: str, gid: str, d: dict, cfg: dict, st: Stakes, fixed: float | None) -> list[Graded]:
    out: list[Graded] = []
    c, lim = cfg["football"], cfg["football"]["limits"]
    play = _Play(league, gid, cfg, "football", st, fixed, out)
    prev, ot_seen = (0, 0), set()
    regs = cfg["regulation"][league][0]
    for dr in football_drives(d):
        goal_snaps = 0
        for p in dr.get("plays") or []:
            ty = (p.get("type") or {}).get("text") or ""
            tl, tx = ty.lower(), " ".join(str(p.get("text") or "").lower().split())
            per, clk = (p.get("period") or {}).get("number") or 1, clock_s(p)
            after = _football_score(p, prev)
            play.at(p, parse_ts(p.get("wallclock")), prev, after, per, clk)
            prev = after
            s = p.get("start") or {}
            down, dist, y = s.get("down") or 0, s.get("distance") or 0, s.get("yardsToEndzone")
            gain = p.get("statYardage") or 0
            nopl = "no play" in tx
            if per > regs and per not in ot_seen:
                ot_seen.add(per)
                play.add("ot_start")
            if "kneels" in tx:
                play.add("dead_kneel", dead=True)
            if "the play was upheld" in tx:
                play.add("dead_review", dead=True)
            if (p.get("scoringType") or {}).get("name") == "safety" or (ty == "Safety" and p.get("scoringPlay")):
                play.add("safety")
            dpi = _DPI_YARDS.search(tx)
            if dpi and int(dpi[1]) >= lim["dpi_long"]:
                play.add("dpi_long")
            if "disqualification" in tx or "ejected" in tx:
                play.add("ejection")
            if "two-point conversion attempt" in tx and not nopl:
                tie = two_point_ties_or_leads(tx, play.before, play.after)
                play.add("two_point", grades=("two_point_ties_or_leads",) if tie else ())
            if nopl:
                continue
            last_play = clk == 0 and (per == regs // 2 or per >= regs)
            td = "touchdown" in tl or bool(p.get("scoringPlay") and "touchdown" in tx)
            turnover = bool(p.get("isTurnover"))
            snap = bool(down) and not any(k in tl for k in _NO_TRY)
            if snap and y is not None and y <= lim["goal_line"]:
                goal_snaps += 1
            # --- touchdowns
            if td:
                m = _TD_YARDS.findall(tx)
                yards = int(m[-1]) if m else gain
                if any(k in tl for k in _ST_TD) or turnover or "interception" in tl or "recovered kickoff" in tx:
                    play.add("td_def", grades=("td_def_50",) if yards >= lim["td_very_long"] else ())
                else:
                    g = ["td_off_50"] if yards >= lim["td_very_long"] else ["td_off_20"] if yards >= lim["td_long"] else []
                    g += ["td_off_4th"] if down == 4 else []
                    g += ["td_off_last_play"] if last_play else []
                    play.add("td_off", grades=tuple(g))
            # --- turnovers that were not returned for a touchdown
            interception = "interception" in tl
            lost = not interception and (("fumble" in tl and "opp" in tl) or ("muffed" in tl and "opponent" in tl)
                                         or (turnover and "fumbles" in tx and "blocked" not in tl))
            sacked = "sack" in tl or ("sacked" in tx and "fumble" in tl)
            if sacked and "fumbles" in tx:
                play.add("strip_sack")
            elif sacked:
                play.add("sack", grades=("sack_late_down",) if down in (3, 4) else ())
            if (interception or lost) and not td:
                g = ["turnover_inside_20"] if y is not None and (y <= lim["red_edge"] or y >= 100 - lim["red_edge"]) else []
                ret = _RETURN_YARDS.search(tx)
                g += ["turnover_return_20"] if ret and int(ret[1]) >= lim["return_long"] else []
                play.add("interception" if interception else "fumble_lost", grades=tuple(g))
            if "blocked punt" in tl and not td:
                play.add("blocked_punt")
            # --- kicks
            if "field goal" in tl and not td:
                fg = _FG_YARDS.search(tx)
                if "good" in tl:
                    play.add("fg_made", _band(c["bands"]["fg_made"], int(fg[1]) if fg else 0))
                else:
                    play.add("fg_missed")
            if "kickoff" in tl and "onside" in tx:
                kicker, rec = _KICK_FROM.search(tx), _RECOVERED_BY.search(tx)
                play.add("onside_recovered" if kicker and rec and kicker[1] == rec[1] else "onside")
            # --- downs
            fake = ("punt formation" in tx or "field goal formation" in tx) and snap
            scrimmage = snap and "kneels" not in tx and "spiked" not in tx
            converted = td or (dist > 0 and gain >= dist)
            if fake:
                play.add("fake_kick")
            if down == 4 and scrimmage and not td and not interception and not lost:
                if converted:
                    if not fake:
                        play.add("fourth_try")
                elif goal_snaps >= lim["stand_snaps"]:
                    play.add("goal_line_stand")
                else:
                    play.add("turnover_on_downs")
            if down == 3 and scrimmage and dist >= lim["third_long_distance"] and gain >= dist and not td and not turnover:
                play.add("third_long")
            if not td and not interception and not lost and any(k in tl for k in ("rush", "pass")):
                pts = _band(c["bands"]["explosive"], gain)
                if pts is not None:
                    play.add("explosive", pts)
            # --- last play of a half: a deep pass from far out, or a lateral
            if last_play and "pass" in tl and (("deep" in tx and y is not None and y >= lim["heave_from"]) or "lateral" in tx):
                play.add("last_play_heave")
    return out


def _feet(tx: str) -> int:
    m = _FEET.search(tx)
    return int(m[1]) if m else -1


def _hoops(league: str, gid: str, d: dict, cfg: dict, st: Stakes, fixed: float | None) -> list[Graded]:
    out: list[Graded] = []
    c, lim = cfg["hoops"], cfg["hoops"]["limits"]
    play = _Play(league, gid, cfg, "hoops", st, fixed, out)
    regs = cfg["regulation"][league][0]
    plays = [p for p in d.get("plays") or [] if parse_ts(p.get("wallclock"))]
    prev, ot_seen, side_of = (0, 0), set(), {}
    run, armed = [0, 0], [False, False]
    pts_by, reb_by, ast_by, tripled = collections.Counter(), collections.Counter(), collections.Counter(), set()
    steal = None        # (period, clock, team that lost the ball)
    last_three = None   # (period, clock, team id) of the last made three, for the 4-point play
    for i, p in enumerate(plays):
        ty = (p.get("type") or {}).get("text") or ""
        tl, tx = " ".join(ty.lower().split()), " ".join(str(p.get("text") or "").lower().split())
        per, clk = (p.get("period") or {}).get("number") or 1, clock_s(p)
        team = (p.get("team") or {}).get("id")
        after, points, side = _hoops_score(p, prev, team, side_of) if p.get("scoringPlay") else (prev, 0, 0)
        before, prev = prev, after
        play.at(p, parse_ts(p.get("wallclock")), before, after, per, clk)
        who = [(x.get("athlete") or {}).get("id") for x in p.get("participants") or []]
        last, ot = per == regs, per > regs
        late = ot or (last and clk is not None and clk <= lim["late_left"])
        gap = abs(before[0] - before[1])
        clutch = late and gap <= lim["clutch_margin"]
        if ot and per not in ot_seen:
            ot_seen.add(per)
            play.add("ot_start", c["base"]["ot_start"] + c["bonus"]["ot_each_further"] * (per - regs - 1))
        is_ft = "free throw" in tl
        made = points > 0
        # --- no score: fouls, reviews, steals, rebounds
        if "technical" in tl and not any(k in tl for k in _SOFT_TECH):
            play.add("technical")
        if "flagrant" in tl and not is_ft:
            play.add("flagrant")
        if "ejection" in tl:
            play.add("ejection")
        if ("review" in tl or "challenge" in tl) and ("stands" in tl or "supported" in tl):
            play.add("dead_review", dead=True)
        if "rebound" in tl and who:
            reb_by[who[0]] += 1
        if is_ft:
            if (last or ot) and clk is not None and clk <= lim["last_seconds"] and gap <= lim["clutch_ft_margin"]:
                play.add("clutch_ft")
            elif not clutch:
                play.add("dead_ft", dead=True)
        if p.get("shootingPlay") and not is_ft and not made:
            steal = None                                   # a missed shot ends the chance off the steal
        if "steals)" in tx:
            steal = (per, clk, team)
        if not made:
            _triple(play, who, pts_by, reb_by, ast_by, tripled, lim["triple_double_each"])
            continue
        mine_before = (before[0] - before[1]) * (1 if side == 0 else -1)
        mine_after = mine_before + points
        is_fg = not is_ft
        # --- one main kind per basket: the largest of game-winner, go-ahead, lead change, clutch basket
        flipped = (mine_before < 0 <= mine_after) or (mine_before == 0 and sum(before) > 0)
        mains: list[tuple[float, str]] = []
        if flipped:
            mains.append((c["base"]["lead_change_late"], "lead_change_late") if late else (c["base"]["lead_change"], "lead_change"))
        buzzer = clk is not None and clk <= lim["buzzer_clock"]
        if is_fg:
            closing = (last or ot) and clk is not None and flipped
            if closing and buzzer and mine_after > 0:
                mains.append((c["base"]["game_winner"], "game_winner"))
            elif closing and clk <= lim["last_seconds"]:
                mains.append((c["base"]["go_ahead_last10"], "go_ahead_last10"))
            elif closing and clk <= lim["last_minute"]:
                mains.append((c["base"]["go_ahead"], "go_ahead"))
            if clutch:
                mains.append((c["base"]["clutch_fg"], "clutch_fg"))
        three = is_fg and points == 3
        and1 = is_fg and any("free throw - 1 of 1" in " ".join(((q.get("type") or {}).get("text") or "").lower().split())
                             and (q.get("team") or {}).get("id") == team and clock_s(q) == clk
                             and ((q.get("period") or {}).get("number") or 1) == per for q in plays[i + 1:i + 5])
        main = ""
        if mains:
            main = max(mains)[1]
            g = (["clutch_and1"] if and1 else []) + (["clutch_three"] if three else []) if clutch and is_fg else []
            play.add(main, grades=tuple(g))
        # --- everything else a basket can also be
        if is_fg:
            feet = _feet(tx)
            if buzzer and per < regs:                       # ending a quarter or half; the last period's are the rows above
                far = feet >= lim["half_court_feet"] or "heave" in tl
                play.add("period_buzzer", grades=("buzzer_half_court",) if far else ())
            if "alley oop" in tl or "alley oop" in tx:
                play.add("alley_oop")
            elif "dunk" in tl:
                play.add("dunk")
            if three and feet >= lim["deep_three_feet"]:
                play.add("deep_three")
            if steal and steal[0] == per and steal[2] != team and steal[1] is not None and clk is not None \
                    and 0 <= steal[1] - clk <= lim["steal_score_within"]:
                play.add("steal_score")
            steal = None
            last_three = (per, clk, team) if three else None
        elif "1 of 1" in tl and last_three == (per, clk, team):
            play.add("four_point_play")
            last_three = None
        # --- runs and comebacks
        run[side] += points
        run[1 - side] = 0
        total_so_far = 0.0
        for line, total in c["runs"]:
            if run[side] - points < line <= run[side]:
                play.add(f"run{line}", total - total_so_far)
            total_so_far = total
        for k in (0, 1):
            behind = (after[1] - after[0]) * (1 if k == 0 else -1)
            if behind >= lim["comeback_from"]:
                armed[k] = True
        if armed[side] and -mine_after <= lim["comeback_to"]:
            armed[side] = False
            play.add("comeback")
        # --- player milestones, counted from the plays themselves
        if who:
            was = pts_by[who[0]]
            pts_by[who[0]] = was + points
            if any(was < line <= was + points for line in lim["star_points"]):
                play.add("star_points")
            if is_fg and len(who) > 1 and "assists)" in tx:
                ast_by[who[1]] += 1
        _triple(play, who, pts_by, reb_by, ast_by, tripled, lim["triple_double_each"])
    return out


def _triple(play: _Play, who: list, pts_by, reb_by, ast_by, tripled: set, each: int) -> None:
    for a in who:
        if a and a not in tripled and pts_by[a] >= each and reb_by[a] >= each and ast_by[a] >= each:
            tripled.add(a)
            play.add("triple_double")


_BY_FAMILY = {"football": _football, "hoops": _hoops}


def grade(league: str, summary: dict, game_id: str = "", *, cfg: dict | None = None,
          fixed_stakes: float | None = None) -> list[Graded]:
    """Every graded moment of one game summary, in feed order. `fixed_stakes` replaces the summary's stakes with one number
    for the whole game (the shard label passes the playoff flag, the one stake every source carries)."""
    cfg = cfg or config()
    lg = str(league).lower()
    st = game_stakes(summary, cfg) if fixed_stakes is None else Stakes()
    return _BY_FAMILY[family(lg, cfg)](lg, game_id, summary, cfg, st, fixed_stakes)
