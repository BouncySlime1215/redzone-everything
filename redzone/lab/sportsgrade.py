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
from redzone.lab import cutgrade
from redzone.lab.backtest import lag_of, leave_offsets, offsets_of, on_screen, tuning_blackout_seconds, watched_whole
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.sources import espn

PLAY_LEN = {League.NFL: 7.0, League.MLB: 6.0, League.NBA: 0.0, League.NHL: 0.0}
RESUME_LEAD = {League.NBA: 26.0, League.NHL: 5.0}   # play can restart this long before the next logged action


@dataclass
class Card:
    league: str
    games: int = 0
    hours: float = 0.0
    switches: int = 0
    replays: int = 0
    cuts: list = field(default_factory=list)
    big: int = 0
    big_live: int = 0
    big_seen: int = 0
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
        return {"league": self.league, "games": self.games, "mid_play_cuts": len(self.cuts),
                "dead_air_pct": pct(self.dead, self.watched), "big_seen_pct": pct(self.big_seen, self.big),
                "big_live_pct": pct(self.big_live, self.big), "crunch_live_pct": pct(self.crunch_live, self.crunch),
                "your_team_crunch_live_pct": pct(self.my_crunch_live, self.my_crunch),
                "your_team_crunch_live": f"{self.my_crunch_live}/{self.my_crunch}",   # 0/0 is "none", not 0 %
                "tuning_blackout_s": round(self.tuning_blackout_s, 1),
                "tuning_blackout_pct": pct(self.tuning_blackout_s, self.hours * 3600),
                "switches_per_hour": round(self.switches / max(0.1, self.hours), 1),
                "big_plays": self.big, "crunch_plays": self.crunch,
                **({"pp_minutes_on_screen": round(self.pp_screen_s / 60, 1),
                    "pp_goals_live_pct": pct(self.pp_goals_live, self.pp_goals),
                    "pp_goals": self.pp_goals} if self.league == League.NHL.value else {}),
                **self.cut.summary()}


def _type(p: dict) -> str:
    if (espn._get(p, "alternativeType", "type") == "lineup-change"
            or (espn._get(p, "alternativeType", "text") or "").lower() == "lineup change"):
        return "lineup_change"
    return (espn._get(p, "type", "text") or "").lower()


def _clock(league: League, p: dict) -> float | None:
    c = espn._clock_s(espn._get(p, "clock", "displayValue"))
    if c is not None and league == League.NHL:
        per = espn._int(espn._get(p, "period", "number")) or 1
        c = max(0.0, (300 if per >= 4 else 1200) - c)
    return c


def is_big(league: League, p: dict, prev: dict | None) -> bool:
    t = _type(p)
    if league == League.NFL:
        return bool(p.get("scoringPlay")) or "interception" in t or "fumble recovery (opponent)" in t \
            or bool(p.get("isTurnover"))
    if league == League.NHL:
        return t == "goal" or bool(p.get("scoringPlay"))
    if league == League.MLB:
        return bool(p.get("scoringPlay")) or "home run" in t
    if league == League.NBA:
        if not p.get("scoringPlay") or "free throw" in t:
            return False
        per = espn._int(espn._get(p, "period", "number")) or 0
        c = _clock(league, p)
        if per < 4 or c is None or c > 120 or prev is None:
            return False
        hs, as_ = espn._int(prev.get("homeScore")), espn._int(prev.get("awayScore"))
        return hs is not None and as_ is not None and abs(hs - as_) <= 5
    return False


def is_crunch(league: League, p: dict) -> bool:
    per = espn._int(espn._get(p, "period", "number")) or 0
    c = _clock(league, p)
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    if hs is None or as_ is None:
        return False
    d = abs(hs - as_)
    if league == League.NFL:
        return per >= 5 or (per == 4 and c is not None and c <= 120 and d <= 8)
    if league == League.NBA:
        return per >= 5 or (per == 4 and c is not None and c <= 120 and d <= 5)
    if league == League.NHL:
        return per >= 4 or (per == 3 and c is not None and c <= 120 and d <= 1)
    if league == League.MLB:
        return per >= 8 and d <= 2
    return False


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


def _marker(t: str) -> bool:
    return any(k in t for k in ("timeout", "end ", "end of", "two-minute", "coin toss", "period start",
                                "start inning", "start batter", "end batter", "end inning", "lineup_change"))


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
        ok, what = _clean_cut(league, r.wall, r.plays, s.t - lag_of(lag_total, r.gid) - lo)
        if not ok:
            card.cuts.append((s.t, f"{r.matchup}: {what}"))

    replayed = replayed or set()
    for r in replays:
        mine = bool({r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]} & my_teams)
        prev = None
        for w, p in zip(r.wall, r.plays):
            lag = lag_of(lag_total, r.gid)
            air = w + lag - PLAY_LEN[league]
            on = watched_whole(watching, r.gid, air, w + lag, tune_s, t1, offs)
            if is_pp_goal(league, p):
                card.pp_goals += 1
                card.pp_goals_live += on
            if is_big(league, p, prev):
                card.big += 1
                card.big_live += on
                card.big_seen += on or (r.gid, round(w)) in replayed
            t = _type(p)
            if is_crunch(league, p) and not _marker(t):
                card.crunch += 1
                card.crunch_live += on
                if mine:
                    card.my_crunch += 1
                    card.my_crunch_live += on
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
