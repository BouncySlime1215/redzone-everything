"""League-aware report card for backtests (NFL, NBA, NHL, MLB).

What counts, per league:
  big play   NFL: scores + turnovers   NHL: goals   MLB: runs scored / home runs
             NBA: clutch buckets (Q4/OT, last 2:00, within 5 before the shot)
  crunch     NFL: last 2:00 of a one-score game or OT     NBA: last 2:00 within 5, or OT
             NHL: last 2:00 of P3 within 1, or OT/SO      MLB: 8th inning on, within 2 runs
  clean cut  NFL/MLB: no play on screen.  NBA/NHL (continuous): only during a dead ball on screen
             (whistle, timeout, period break) or the first 4s after a basket/goal aired.

Uses TRUE wallclocks from the archive, never the app's own estimates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from redzone.decider.screen import CONTINUOUS, _is_marker
from redzone.lab import cutgrade, leverage
from redzone.lab.backtest import (CATCHUP_2_S, CATCHUP_3_S, CATCHUP_S, from_start, lag_of, leave_offsets, offsets_of, on_screen, replayed_by, time_to_see,
                                  tuning_blackout_seconds, watched_whole)
from redzone.grading import PLAY_LEN, RESUME_LEAD, is_big, is_crunch  # noqa: F401  [one brain] moved, step 3
from redzone.grading import marker as _marker, play_clock as _clock, play_type as _type  # noqa: F401
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.sources import espn


@dataclass
class Card:
    league: str
    games: int = 0
    hours: float = 0.0
    switches: int = 0
    replays: int = 0
    cuts: list = field(default_factory=list)
    cuts_high: list = field(default_factory=list)   # the mid-play leaves at a high-leverage moment (leverage.py)
    big: int = 0
    big_live: int = 0
    big_seen: int = 0
    big_seen_10m: int = 0      # live, or replayed within backtest.CATCHUP_S of airing
    my_big: int = 0
    my_big_seen_10m: int = 0
    big_seen_3m: int = 0       # live, or replayed within backtest.CATCHUP_3_S (the gate)
    big_seen_2m: int = 0       # ...within CATCHUP_2_S (stretch)
    my_big_seen_3m: int = 0
    big_see_s: list = field(default_factory=list)   # [time to see] per big play (backtest.time_to_see)
    crunch_on_replay: int = 0  # crunch plays that aired while a catch-up replay was on screen
    big_from_start: int = 0    # [from the start] big plays on screen from the previous dead ball through the end
    crunch_from_start: int = 0
    my_crunch_from_start: int = 0
    crunch: int = 0
    crunch_live: int = 0
    my_crunch: int = 0
    my_crunch_live: int = 0
    dead: float = 0.0
    watched: float = 0.0
    tuning_blackout_s: float = 0.0
    pp_goals: int = 0          # [nhl pp] goals scored on a power play
    pp_goals_live: int = 0     # ...of those, watched live start to finish
    pp_screen_s: float = 0.0   # seconds of power play (live play, not intermission) on screen
    cut: cutgrade.CutCard = field(default_factory=cutgrade.CutCard)

    def add(self, o: "Card") -> None:
        for k, v in vars(o).items():
            if isinstance(v, cutgrade.CutCard):
                self.cut.add(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(self, k, getattr(self, k) + v)
            elif isinstance(v, list):
                getattr(self, k).extend(v)

    def summary(self) -> dict:
        pct = lambda a, b: round(100 * a / max(1, b), 1)
        return {"league": self.league, "games": self.games, "mid_play_cuts": len(self.cuts), "mid_play_cuts_high": len(self.cuts_high),
                "dead_air_pct": pct(self.dead, self.watched), "big_seen_pct": pct(self.big_seen, self.big),
                "big_live_pct": pct(self.big_live, self.big), "crunch_live_pct": pct(self.crunch_live, self.crunch),
                "your_team_crunch_live_pct": pct(self.my_crunch_live, self.my_crunch),
                "your_team_crunch_live": f"{self.my_crunch_live}/{self.my_crunch}",   # 0/0 is "none", not 0 %
                "big_from_start_pct": pct(self.big_from_start, self.big),
                "crunch_from_start_pct": pct(self.crunch_from_start, self.crunch),
                "your_team_crunch_from_start": f"{self.my_crunch_from_start}/{self.my_crunch}",
                "tuning_blackout_s": round(self.tuning_blackout_s, 1),
                "tuning_blackout_pct": pct(self.tuning_blackout_s, self.hours * 3600),
                "switches_per_hour": round(self.switches / max(0.1, self.hours), 1),
                "big_plays": self.big, "crunch_plays": self.crunch,
                "big_seen_10m_pct": pct(self.big_seen_10m, self.big),
                "your_team_big_seen_10m": f"{self.my_big_seen_10m}/{self.my_big}",
                "crunch_on_replay": self.crunch_on_replay,
                **({"pp_minutes_on_screen": round(self.pp_screen_s / 60, 1),
                    "pp_goals_live_pct": pct(self.pp_goals_live, self.pp_goals),
                    "pp_goals": self.pp_goals} if self.league == League.NHL.value else {}),
                **self.cut.summary()}


def power_play_after(league: League, plays: list[dict]) -> list[bool]:
    """[nhl pp] Was a power play on after each play? The grader may look ahead: ESPN labels every
    team play (penalties too) with that team's strength before it, so the next labelled play says
    what was in effect."""
    out = [False] * len(plays)
    if league != League.NHL:
        return out
    nxt = False
    for i in range(len(plays) - 1, -1, -1):
        out[i] = nxt
        p = plays[i]
        st = espn._get(p, "strength", "abbreviation")
        if p.get("team") and st:
            nxt = st in ("power-play", "short-handed")
    return out


def is_pp_goal(league: League, p: dict) -> bool:
    return league == League.NHL and _type(p) == "goal" and espn._get(p, "strength", "abbreviation") == "power-play"


def _on_screen(watching: list[tuple[float, str]], t: float, tune_s: float) -> str | None:
    cur = None
    for at, g in watching:
        if at + tune_s <= t:
            cur = g
        elif at <= t:
            cur = None
        else:
            break
    return cur


def _clean_cut(league: League, wall: list[float], plays: list[dict], screen_t: float) -> tuple[bool, str]:
    i = -1
    for k, w in enumerate(wall):
        if w <= screen_t:
            i = k
        else:
            break
    if i == len(wall) - 1 or (i >= 0 and screen_t > wall[-1]):
        return True, ""  # the game is over on screen
    if league in CONTINUOUS:
        if i < 0:
            return True, ""
        last = plays[i]
        # The dead ball lasts until play restarts, which can be well before the next ACTION is logged
        # (basketball: up to a shot clock after an inbound). Free throws, subs, team rebounds in
        # between are part of the same dead ball.
        nxt = next((wall[j] for j in range(i + 1, len(wall))
                    if espn.stoppage_for(league, plays[j]) == Stoppage.NONE
                    and not _is_marker(espn._kind(league, plays[j]), league)), float("inf"))
        dead = espn.stoppage_for(league, last) != Stoppage.NONE or (
            last.get("scoringPlay") and screen_t - wall[i] <= 4.0)
        if dead and screen_t < nxt - RESUME_LEAD[league]:
            return True, ""
        return False, (last.get("text") or _type(last))[:70]
    plen = PLAY_LEN[league]
    for w, p in zip(wall, plays):
        if _marker(_type(p)) and not p.get("scoringPlay"):
            continue
        if w - plen <= screen_t <= w:
            return False, (p.get("text") or "")[:70]
    return True, ""


def grade(replays, switches, my_teams: set[str], lag_total: float, tune_s: float, t0: float, t1: float,
          replayed: set[tuple[str, int]] | None = None) -> Card:
    league = replays[0].league
    card = Card(league.value, games=len(replays), hours=(t1 - t0) / 3600)
    watching: list[tuple[float, str]] = []
    for s in switches:
        watching.append((s.t, f"replay:{s.to}" if s.kind == "replay" else s.to))
    card.switches = sum(1 for s in switches if s.kind != "replay")
    card.replays = sum(1 for s in switches if s.kind == "replay")
    by_id = {r.gid: r for r in replays}
    card.tuning_blackout_s = tuning_blackout_seconds(watching, tune_s, t0, t1)
    card.cut = cutgrade.grade(replays, switches, lag_total, tune_s)

    prev_kind = None
    offs = offsets_of(switches)
    for s, lo in zip(switches, leave_offsets(switches)):
        was_replay, prev_kind = prev_kind == "replay", s.kind
        r = by_id.get(s.frm or "")
        if r is None or was_replay:
            continue
        screen_t = s.t - lag_of(lag_total, r.gid) - lo
        ok, what = _clean_cut(league, r.wall, r.plays, screen_t)
        if not ok:
            card.cuts.append((s.t, f"{r.matchup}: {what}"))
            at = max((k for k, w in enumerate(r.wall) if w <= screen_t), default=0)
            if leverage.high_leverage(league, r.plays[at] if r.plays else None):
                card.cuts_high.append((s.t, f"{r.matchup}: {what}"))

    replayed = replayed or set()
    for r in replays:
        mine = bool({r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]} & my_teams)
        prev = None
        for k, (w, p) in enumerate(zip(r.wall, r.plays)):
            lag = lag_of(lag_total, r.gid)
            air = w + lag - PLAY_LEN[league]
            on = watched_whole(watching, r.gid, air, w + lag, tune_s, t1, offs)
            fs = on and from_start(watching, r, k, lag, tune_s, t1, offs)
            if is_pp_goal(league, p):
                card.pp_goals += 1
                card.pp_goals_live += on
            if is_big(league, p, prev):
                card.big += 1
                card.big_live += on
                card.big_from_start += fs
                card.big_seen += on or (r.gid, round(w)) in replayed
                soon = on or replayed_by(replayed, (r.gid, round(w)), w + lag + CATCHUP_S)
                card.big_seen_10m += soon
                soon3 = on or replayed_by(replayed, (r.gid, round(w)), w + lag + CATCHUP_3_S)
                card.big_seen_3m += soon3
                card.big_seen_2m += on or replayed_by(replayed, (r.gid, round(w)), w + lag + CATCHUP_2_S)
                card.big_see_s.append(time_to_see(on, replayed, (r.gid, round(w)), w + lag))
                if mine:
                    card.my_big += 1
                    card.my_big_seen_10m += soon
                    card.my_big_seen_3m += soon3
            t = _type(p)
            if is_crunch(league, p) and not _marker(t):
                card.crunch += 1
                card.crunch_live += on
                card.crunch_from_start += fs
                if not on and (_on_screen(watching, air, tune_s) or "").startswith("replay:"):
                    card.crunch_on_replay += 1
                if mine:
                    card.my_crunch += 1
                    card.my_crunch_live += on
                    card.my_crunch_from_start += fs
            prev = p

    pp_after = {r.gid: power_play_after(league, r.plays) for r in replays}
    t = t0
    while t <= t1:
        g, o = on_screen(watching, offs, t, tune_s)
        if g and g.startswith("replay:"):
            card.watched += 10
        elif g and g in by_id:
            r = by_id[g]
            st = t - lag_of(lag_total, g) - o
            i = max(0, max((k for k, w in enumerate(r.wall) if w <= st), default=0))
            if r.wall[0] <= st <= r.wall[-1]:
                card.watched += 10
                if espn.stoppage_for(league, r.plays[i]) in REAL_BREAKS:
                    card.dead += 10
                elif pp_after[g][i]:
                    card.pp_screen_s += 10
        t += 10
    return card
