"""Replay a real slate of past games through the scorer and decider, then grade it.

The simulation is honest about time:
  - the app only "sees" a play feed_lag seconds after it happened (wallclock)
  - your screen shows it delay seconds after the app saw it
  - the decider only knows what it has seen, never the future

Report card (what "good" means):
  mid_play_cuts      cut away while a play was on screen       -> must be 0
  big_plays_live     TDs/turnovers you saw live, all games      -> higher is better
  dead_air_pct       time spent watching a break on screen      -> lower is better
  switches_per_hour  how busy it felt                           -> 8-20 feels like RedZone
"""
from __future__ import annotations

import json
import random
import zlib
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from redzone.config import Config
from redzone.decider.decider import Candidate, Decider, FocusMode
from redzone.decider.screen import DelayBook
from redzone.models import REAL_BREAKS, League, Phase
from redzone.scoring.engine import Personal, Scorer
from redzone.sources import espn

ET = ZoneInfo("America/New_York")


@dataclass
class Replay:
    league: League
    gid: str
    data: dict
    plays: list[dict]
    wall: list[float]
    matchup: str = ""
    _cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, league: League, path: Path) -> "Replay | None":
        data = json.loads(path.read_text())
        plays = espn.flatten_plays(league, data)
        if not plays:
            return None
        wall, last = [], None
        for p in plays:
            t = espn._ts(p.get("wallclock"))
            t = t if t is not None else (last + 5 if last else None)
            wall.append(t)
            last = t if t is not None else last
        if wall[0] is None:
            return None
        # Make wallclock monotonic (feeds occasionally go backwards by a second).
        for i in range(1, len(wall)):
            wall[i] = max(wall[i], wall[i - 1])
        r = cls(league, path.stem.split("_")[-1], data, plays, wall)
        g = espn.parse_summary(league, data, upto=0, plays=plays)
        r.matchup = g.matchup if g else r.gid
        return r

    def state_at(self, idx: int):
        if idx not in self._cache:
            g = espn.parse_summary(self.league, self.data, upto=idx, plays=self.plays)
            if g:
                # Final records would leak the future; a backtest doesn't get them.
                for t in (g.home, g.away):
                    t.wins = t.losses = t.ties = None
            self._cache[idx] = g
        return self._cache[idx]


@dataclass
class SwitchLog:
    t: float
    frm: str | None
    to: str
    kind: str
    reason: str


@dataclass
class Report:
    switches: list[SwitchLog]
    mid_play_cuts: list[tuple[float, str]]
    big_plays: int
    big_plays_live: int
    my_big_plays: int
    my_big_plays_live: int
    dead_air_s: float
    watched_s: float
    hours: float
    missed: list[tuple[float, str, str]]
    decisions: list = field(default_factory=list)
    big_plays_caught: int = 0
    crunch: int = 0
    crunch_live: int = 0
    my_crunch: int = 0
    my_crunch_live: int = 0

    @property
    def summary(self) -> dict:
        return {
            "mid_play_cuts": len(self.mid_play_cuts),
            "big_plays_seen": f"{self.big_plays_caught}/{self.big_plays}"
                              f" ({100 * self.big_plays_caught / max(1, self.big_plays):.0f}%)",
            "big_plays_live": f"{self.big_plays_live}/{self.big_plays}"
                              f" ({100 * self.big_plays_live / max(1, self.big_plays):.0f}%)",
            "your_team_big_plays_seen": f"{self.my_big_plays_live}/{self.my_big_plays}",
            "replays": sum(1 for s in self.switches if s.kind == "replay"),
            "crunch_live": f"{self.crunch_live}/{self.crunch}",
            "your_team_crunch_live": f"{self.my_crunch_live}/{self.my_crunch}",
            "dead_air_pct": round(100 * self.dead_air_s / max(1, self.watched_s), 1),
            "switches_per_hour": round(len(self.switches) / max(0.1, self.hours), 1),
            "switches": len(self.switches),
        }


def _is_big(p: dict) -> bool:
    t = (espn._get(p, "type", "text") or "").lower()
    return bool(p.get("scoringPlay")) or "interception" in t or "fumble recovery (opponent)" in t \
        or bool(p.get("isTurnover"))


@dataclass
class _Feed:
    """How one game's data reaches the app in a backtest (the realism knobs, per game)."""
    arrive: list[float]                                       # when each play shows up in the feed
    drops: list[tuple[float, float]] = field(default_factory=list)  # windows where the source is silent
    no_wall: set[int] = field(default_factory=set)            # plays delivered without a wallclock
    late: list[int] = field(default_factory=list)             # plays that can arrive out of order
    fresh_at: float = 0.0                                     # last time this game's data was fresh

    def __post_init__(self):
        self.late_set = set(self.late)


def _feed(r: Replay, rng: random.Random, feed_lag: float, step: float, lag_jitter, drop: float,
          late_plays: float, no_wallclock: float, t0: float, t1: float) -> _Feed:
    if lag_jitter:
        lo, hi = lag_jitter
        arrive, prev = [], float("-inf")
        for w in r.wall:
            prev = max(prev, w + rng.uniform(lo, hi))   # feeds deliver in order, at a varying lag
            arrive.append(prev)
    else:
        arrive = [w + feed_lag for w in r.wall]
    late = []
    if late_plays:
        for i in range(len(arrive)):
            if rng.random() < late_plays:
                arrive[i] += step      # a poll late: later plays can now land before it
                late.append(i)
    drops = []
    if drop:
        t = t0
        while True:
            t += rng.expovariate(drop / 60.0)   # `drop` = silences per game-minute
            if t >= t1:
                break
            dur = rng.uniform(60.0, 120.0)
            drops.append((t, t + dur))
            t += dur
    no_wall = {i for i in range(len(arrive)) if no_wallclock and rng.random() < no_wallclock}
    return _Feed(arrive, drops, no_wall, late)


def _deliver(r: Replay, f: _Feed, i: int, now: float):
    """The state the app would see: plays not yet arrived are missing, some lack a wallclock."""
    g = r.state_at(i)
    if g is None:
        return None
    missing = {j for j in f.late if j <= i and f.arrive[j] > now}
    strip = f.no_wall
    if missing or strip:
        lo = max(0, i - 7)                       # parse_summary keeps the last 8 plays
        ids = {str(r.plays[j].get("id")): j for j in range(lo, i + 1)}

        def fix(p):
            j = ids.get(p.id)
            if j is not None and j in strip:
                return dc_replace(p, wallclock=None, seen_at=None)
            return p
        recent = [fix(p) for p in g.recent_plays if ids.get(p.id) not in missing]
        last = fix(g.last_play) if g.last_play else None
        g = dc_replace(g, recent_plays=recent, last_play=last)
    g.updated_at = f.fresh_at
    return g


def run(replays: list[Replay], cfg: Config, my_teams: set[str], *, feed_lag: float = 8.0,
        delay: float = 35.0, play_len: float = 7.0, tune_s: float = 5.0, step: float | None = None,
        focus: str | None = None, start: float | None = None, end: float | None = None,
        true_delay: float | None = None, sigma: float = 3.0, lag_jitter: tuple[float, float] | None = None,
        drop: float = 0.0, late_plays: float = 0.0, no_wallclock: float = 0.0, seed: int = 0) -> Report:
    """Realism knobs (all off by default, so the scorecard is unchanged):
      true_delay    the stream's real delay; `delay` is only the app's estimate of it
      sigma         the uncertainty the app states for that estimate (seconds)
      lag_jitter    (lo, hi): each play reaches the feed lo..hi s after it happened (seeded)
      drop          source goes silent 60-120 s, this many times per game-minute on average
      late_plays    fraction of plays that arrive one poll late (so out of order)
      no_wallclock  fraction of plays delivered without a wallclock
    """
    step = step or cfg.get("polling.live_s", 10)
    clock = _Clock()
    lag_total = feed_lag + delay   # real life -> your screen, as the app believes it
    lag_true = feed_lag + (delay if true_delay is None else true_delay)  # ...and as it really is
    delays = DelayBook({"youtubetv": lag_total}, sigma_default=sigma, clock=clock)
    scorer = Scorer(cfg, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    me = Personal(my_teams={League.NFL: my_teams})
    if focus:
        dec.user.focus_game, dec.user.focus_mode = focus, FocusMode.FOCUS
    t0 = start or min(r.wall[0] for r in replays) - 60
    t1 = end or max(r.wall[-1] for r in replays) + 60

    for r in replays:
        d = delays.get(f"youtubetv:{r.gid}")
        d.calibrated_at = t0
        d.sigma = sigma
    feeds = {r.gid: _feed(r, random.Random(seed * 1_000_003 + zlib.crc32(r.gid.encode())), feed_lag, step,
                          lag_jitter, drop, late_plays, no_wallclock, t0, t1) for r in replays}

    switches: list[SwitchLog] = []
    decisions = []
    replayed: set[tuple[str, int]] = set()
    watching: list[tuple[float, str | None]] = []  # (time cut happens on screen, game)
    idx = {r.gid: -1 for r in replays}
    clock.t = t0
    while clock.t <= t1:
        cands = {}
        for r in replays:
            f = feeds[r.gid]
            silent = any(a <= clock.t < b for a, b in f.drops)
            i = idx[r.gid]
            if not silent:
                f.fresh_at = clock.t
                # Newest play that has arrived. A late play doesn't hold back the ones after it
                # (out-of-order delivery); _deliver leaves it out until it lands.
                k = i + 1
                while k < len(r.plays):
                    if f.arrive[k] <= clock.t:
                        i = k
                    elif k not in f.late_set:
                        break
                    k += 1
            idx[r.gid] = i
            if i < 0:
                continue
            g = _deliver(r, f, i, f.fresh_at)
            if g is None:
                continue
            # parse_summary already stamps plays with their real wallclock.
            dec.timeline(r.gid, r.league).observe(g)
            if g.phase != Phase.LIVE:
                continue
            cands[r.gid] = Candidate(scorer.score(g, me), f"youtubetv:{r.gid}",
                                     stale=clock.t - f.fresh_at > 60)
        before = dec.current
        decision = dec.decide(cands)
        decisions.append(decision)
        # Any "switch" that survives decide() is a real tune. One back to the same game after a replay
        # (kind "home"/"break_visit", not just "live") used to go unlogged: the grader then thought the
        # replay ran on forever AND skipped mid-play checks on the next cut from that game.
        if decision.action == "switch" and decision.target:
            switches.append(SwitchLog(clock.t, before, decision.target, decision.kind, decision.reason))
            watching.append((clock.t, decision.target))
        elif decision.action == "replay" and decision.replay:
            switches.append(SwitchLog(clock.t, before, decision.target, "replay", decision.reason))
            watching.append((clock.t, f"replay:{decision.target}"))
            replayed.add((decision.target, round(decision.replay.t)))
        clock.t += step

    rep = _grade(replays, switches, watching, my_teams, lag_true, play_len, tune_s, t0, t1, replayed)
    rep.decisions = decisions
    return rep


def _on_screen_game(watching: list[tuple[float, str | None]], t: float, tune_s: float) -> str | None:
    cur = None
    for at, g in watching:
        if at + tune_s <= t:
            cur = g
        elif at <= t:
            cur = None  # mid-tune: black screen
        else:
            break
    return cur


def _grade(replays, switches, watching, my_teams, lag_total, play_len, tune_s, t0, t1, replayed) -> Report:
    by_id = {r.gid: r for r in replays}
    cuts = []
    prev_kind = None
    for s in switches:
        r = by_id.get(s.frm or "")
        was_replay = prev_kind == "replay"
        prev_kind = s.kind
        if not r or was_replay:
            continue
        screen_t = s.t - lag_total  # game time showing on screen when we cut
        for w, p in zip(r.wall, r.plays):
            t = (espn._get(p, "type", "text") or "").lower()
            if t in ("timeout", "official timeout", "end period", "end of half", "two-minute warning",
                     "end of regulation", "end of game", "coin toss") or espn.stoppage_for(r.league, p) in REAL_BREAKS and not p.get("scoringPlay"):
                continue
            if w - play_len <= screen_t <= w:
                cuts.append((s.t, f"{r.matchup}: {(p.get('text') or '')[:70]}"))
                break

    big = live = mine = mine_live = caught = 0
    missed = []
    for r in replays:
        for w, p in zip(r.wall, r.plays):
            if not _is_big(p):
                continue
            big += 1
            air = w + lag_total
            on = _on_screen_game(watching, air - play_len, tune_s) == r.gid
            live += on
            rep_seen = (r.gid, round(w)) in replayed
            caught += on or rep_seen
            ours = any(a in my_teams for a in r.matchup.replace("@", " ").split())
            if ours:
                mine += 1
                mine_live += on or rep_seen
            if not (on or rep_seen):
                missed.append((air, r.matchup, (p.get("text") or "")[:90]))

    # Crunch time: final 2:00 of a one-score game (or OT). Did you see those plays live?
    crunch = crunch_live = my_crunch = my_crunch_live = 0
    for r in replays:
        for w, p in zip(r.wall, r.plays):
            per = espn._int(espn._get(p, "period", "number")) or 0
            clk = espn._clock_s(espn._get(p, "clock", "displayValue"))
            hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
            t = (espn._get(p, "type", "text") or "").lower()
            if any(k in t for k in ("timeout", "end ", "end of", "two-minute", "coin toss", "kickoff")) \
                    and not p.get("scoringPlay"):
                continue
            close = hs is not None and as_ is not None and abs(hs - as_) <= 8
            if not ((per == 4 and clk is not None and clk <= 120 and close) or per >= 5):
                continue
            crunch += 1
            on = _on_screen_game(watching, w + lag_total - play_len, tune_s) == r.gid
            crunch_live += on
            if any(a in my_teams for a in r.matchup.replace("@", " ").split()):
                my_crunch += 1
                my_crunch_live += on

    dead = watched = 0.0
    t = t0
    while t <= t1:
        g = _on_screen_game(watching, t, tune_s)
        if g and (g.startswith("replay:") or g == "redzone"):
            watched += 10
        elif g:
            r = by_id[g]
            screen_t = t - lag_total
            i = max(0, _bisect(r.wall, screen_t))
            st = r.state_at(i)
            if st and st.phase == Phase.LIVE:
                watched += 10
                if st.stoppage in REAL_BREAKS:
                    dead += 10
        t += 10
    rep = Report(switches, cuts, big, live, mine, mine_live, dead, watched, (t1 - t0) / 3600, missed)
    rep.big_plays_caught = caught
    rep.crunch, rep.crunch_live, rep.my_crunch, rep.my_crunch_live = crunch, crunch_live, my_crunch, my_crunch_live
    return rep


def _bisect(wall: list[float], t: float) -> int:
    lo, hi = 0, len(wall)
    while lo < hi:
        mid = (lo + hi) // 2
        if wall[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET).strftime("%-I:%M:%S %p")


def timeline_markdown(rep: Report, replays: list[Replay], title: str) -> str:
    names = {r.gid: r.matchup for r in replays}
    lines = [f"# {title}", "", "| Grade | Value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in rep.summary.items()]
    lines += ["", "## Would have switched", "", "| Time (ET) | To | Why |", "|---|---|---|"]
    for s in rep.switches:
        lines.append(f"| {fmt_time(s.t)} | {names.get(s.to, s.to)} | {s.kind}: {s.reason} |")
    if rep.mid_play_cuts:
        lines += ["", "## Mid-play cuts (bugs)", ""] + [f"- {fmt_time(t)} {w}" for t, w in rep.mid_play_cuts]
    lines += ["", "## Big plays it missed (first 25)", ""]
    lines += [f"- {fmt_time(t)} {m}: {txt}" for t, m, txt in rep.missed[:25]]
    return "\n".join(lines) + "\n"
