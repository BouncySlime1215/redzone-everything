"""Fill: what the show does when nothing must be shown (ladder), the day's montage and the off-night plan.

Why: on small slates every game is on break 35-54% of the time, and a night can have no game at all
(planner product-per-league, "When nothing is happening"; build plan WP-19). The fill ladder is code and
must never wait on a model. Pure: it reads game facts, moments and clips handed to it and returns a choice;
it never calls a stick, the TV, the network or the clock, so the decider owns every action.

    pick = choose_fill(games, moments, now=t, clips_ready=True)   # FillChoice(step, action, ...)
    m = plan_montage(clips, games, now=t)                         # the night's biggest plays, oldest first
    o = plan_off_night(games, clips, now=t)                       # nothing live: wait, replay yesterday, or off

The ladder, top wins:
    1 join       a live game that is at a dead ball and not on a break (never mid-play, never a commercial)
    2 reel       unseen Tier 1/2 moments from any game
    3 highlights Tier 3 moments from live games
    4 scores     finished games' results (never an unfinished game's)
    5 ask_nick   nothing left: decision 3 is Nick's, so the ladder returns the question, not a guess

Spoiler rule: a game flagged `watchable` (Nick can still watch it in full: delayed, saved, or his own game in
progress) feeds nothing into steps 2-4, the montage or the off-night reel; only an unfinished game that is not
watchable may feed moments, and no unfinished game ever feeds a result. A game or clip the ladder knows nothing
about is left out (fail closed).

Flag: `fill.enabled` (code default OFF, read through `enabled(get)`). Off, `plan` returns None and nothing
here is called, so reel behaviour is today's. reel.py is not changed: the montage reuses `plan_reel` on copies.
"""
from __future__ import annotations

import dataclasses as dc
from collections.abc import Callable, Iterable, Mapping

from redzone.moments.detect import Moment
from redzone.models import league_index
from redzone.reel import Clip, plan_reel

STEP_JOIN, STEP_REEL, STEP_HIGHLIGHTS, STEP_SCORES, STEP_ASK = 1, 2, 3, 4, 5
ASK_OPTIONS = ("recap on the hidden stick", "score card", "leave it blank")   # decision 3; first is recommended


@dc.dataclass(frozen=True)
class FillParams:
    """Every number fill uses. Read as `fill.<name>`; a bad value keeps its default."""
    enabled: bool = False
    max_age_s: float = 1800.0        # unseen moments older than this are stale for the ladder
    montage_s: float = 600.0         # the day's montage, about 10 minutes
    montage_min_clips: int = 6
    montage_max_clips: int = 40
    montage_min_points: float = 24.0  # a touchdown-style montage: scores and turnovers, not every slam
    montage_max_age_s: float = 86400.0
    montage_per_game: int = 3
    off_night_s: float = 180.0       # yesterday's best on an off night
    off_wait_s: float = 10800.0      # a game starting within 3 h means wait, not replay

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> FillParams:
        base, out = cls(), {}
        for f in dc.fields(cls):
            old, new = getattr(base, f.name), get(f"fill.{f.name}", None)
            try:
                if isinstance(old, bool):
                    out[f.name] = old if new is None else bool(new)
                elif new is not None:
                    out[f.name] = type(old)(new)
            except (TypeError, ValueError):
                continue
        return dc.replace(base, **out)


def enabled(get: Callable[..., object]) -> bool:
    """The flag, `fill.enabled`; off unless config says True."""
    return FillParams.from_config(get).enabled


@dc.dataclass(frozen=True)
class GameInfo:
    game_id: str
    league: str
    status: str                    # "pre" | "live" | "final"
    on_break: bool = False         # commercial or timeout: nothing playing
    joinable: bool = False         # at a dead ball right now (Rule #1: only then may we join)
    mine: bool = False             # WSH or UNC
    crunch: bool = False           # close and late, or overtime
    watchable: bool = False        # Nick can still watch it in full: its results must not leak
    start: float | None = None     # epoch start for a "pre" game


@dc.dataclass(frozen=True)
class FillChoice:
    step: int
    action: str                    # join | reel | highlights | scores | ask_nick
    game_id: str | None = None
    moments: tuple[Moment, ...] = ()
    games: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    reason: str = ""


def _league_pos(league: str) -> int:
    """Position in THE league order (models.league_index); unlisted leagues come last."""
    return league_index(str(league).lower())


def moment_key(m: Moment) -> tuple[str, str]:
    return (m.game_id, m.play_id or f"{m.t:.0f}:{m.kind}")


def _index(games: Iterable[GameInfo]) -> dict[str, GameInfo]:
    return {g.game_id: g for g in games}


def _fresh_unseen(moments, by_id, seen, now, max_age_s, tiers, status) -> list[Moment]:
    """Unseen moments of these tiers from known, non-watchable games in these statuses, not stale."""
    out = []
    for m in moments:
        g = by_id.get(m.game_id)
        if g is None or g.watchable or g.status not in status or m.tier not in tiers:
            continue
        if moment_key(m) in seen or not (0 <= now - m.t <= max_age_s):
            continue
        out.append(m)
    return out


def _line_up(ms: list[Moment], by_id: Mapping[str, GameInfo]) -> tuple[Moment, ...]:
    return tuple(sorted(ms, key=lambda m: (not by_id[m.game_id].mine, m.tier, _league_pos(by_id[m.game_id].league), -m.t)))


def choose_fill(games: Iterable[GameInfo], moments: Iterable[Moment], *, now: float,
                seen: Iterable[tuple[str, str]] = (), clips_ready: bool = False, scores_shown: bool = False,
                params: FillParams | None = None) -> FillChoice:
    """The first ladder step that has something safe to show. Never a commercial, never mid-play,
    never a result that would spoil a game Nick can still watch."""
    p, seen_set = params or FillParams(), set(seen)
    glist = list(games)
    by_id, ms = _index(glist), list(moments)
    # 1. a live game at a dead ball and not on a break: the best one (mine, crunch, league order)
    live = [g for g in glist if g.status == "live" and g.joinable and not g.on_break]
    if live:
        g = min(live, key=lambda g: (not g.mine, not g.crunch, _league_pos(g.league), g.game_id))
        return FillChoice(STEP_JOIN, "join", g.game_id, reason="live game at a dead ball")
    # 2. unseen Tier 1/2 from anywhere (a reel needs clips ready)
    if clips_ready:
        t12 = _fresh_unseen(ms, by_id, seen_set, now, p.max_age_s, (1, 2), ("live", "final"))
        if t12:
            return FillChoice(STEP_REEL, "reel", moments=_line_up(t12, by_id), reason="unseen tier 1/2")
        # 3. Tier 3 from live games
        t3 = _fresh_unseen(ms, by_id, seen_set, now, p.max_age_s, (3,), ("live",))
        if t3:
            return FillChoice(STEP_HIGHLIGHTS, "highlights", moments=_line_up(t3, by_id), reason="unseen tier 3")
    # 4. results of finished games only, once
    finals = sorted((g for g in glist if g.status == "final" and not g.watchable),
                    key=lambda g: (not g.mine, _league_pos(g.league), g.game_id))
    if finals and not scores_shown:
        return FillChoice(STEP_SCORES, "scores", games=tuple(g.game_id for g in finals),
                          reason="finished games only")
    # 5. decision 3 belongs to Nick
    return FillChoice(STEP_ASK, "ask_nick", options=ASK_OPTIONS, reason="nothing safe left to show")


@dc.dataclass(frozen=True)
class Montage:
    clips: tuple[Clip, ...]
    seconds: float
    reason: str = ""


def plan_montage(clips: Iterable[Clip], games: Iterable[GameInfo], *, now: float, seen=lambda c: False,
                 moments: Iterable[Moment] = (), target_s: float | None = None,
                 params: FillParams | None = None) -> Montage:
    """The day's biggest plays, oldest first, about montage_s long. Only clips from finished games that
    Nick cannot still watch; an unknown game's clip is left out. A Tier 1 moment within 90 s of a clip's
    play lifts it. No montage (empty) when fewer than montage_min_clips qualify."""
    p, by_id = params or FillParams(), _index(games)
    t1 = {}
    for m in moments:
        if m.tier == 1:
            t1.setdefault(m.game_id, []).append(m.t)
    pool = []
    for c in clips:
        g = by_id.get(c.game_id)
        if g is None or g.status != "final" or g.watchable or seen(c) or c.points < p.montage_min_points:
            continue
        pts = c.points + (10.0 if c.play_at is not None and any(abs(c.play_at - t) <= 90 for t in t1.get(c.game_id, ())) else 0.0)
        pool.append(dc.replace(c, points=pts, mine=c.mine or g.mine))
    cap = float(target_s if target_s is not None else p.montage_s)
    picked = plan_reel(pool, now=now, seen=lambda c: False, min_clips=p.montage_min_clips,
                       min_s=min(60.0, cap), max_s=cap, max_age_s=p.montage_max_age_s, per_game=p.montage_per_game)
    picked = sorted(picked, key=lambda c: -(c.points))[: p.montage_max_clips]
    if len(picked) < p.montage_min_clips:
        return Montage((), 0.0, "too few finished-game plays")
    picked.sort(key=lambda c: (c.published, c.id))
    return Montage(tuple(picked), round(sum(c.seg for c in picked), 1), "finished games only")


@dc.dataclass(frozen=True)
class OffNight:
    kind: str                      # not_off | wait | reel | off
    clips: tuple[Clip, ...] = ()
    seconds: float = 0.0
    next_start: float | None = None
    reason: str = ""


def plan_off_night(games: Iterable[GameInfo], clips: Iterable[Clip], *, now: float, seen=lambda c: False,
                   params: FillParams | None = None) -> OffNight:
    """Nothing live: a game starting soon means wait; otherwise a short reel of the last night's best (finished
    games only), or off when there is not enough for one. Never blank-with-a-commercial: off is the screen off."""
    p, glist = params or FillParams(), list(games)
    if any(g.status == "live" for g in glist):
        return OffNight("not_off", reason="a game is live: the ladder decides")
    soon = sorted(g.start for g in glist if g.status == "pre" and g.start is not None and 0 <= g.start - now <= p.off_wait_s)
    if soon:
        return OffNight("wait", next_start=soon[0], reason="a game starts soon")
    m = plan_montage(clips, glist, now=now, seen=seen, target_s=p.off_night_s, params=p)
    if m.clips:
        return OffNight("reel", m.clips, m.seconds, reason="yesterday's best")
    return OffNight("off", reason="nothing to replay")


def plan(get: Callable[..., object], games, moments, *, now: float, **kw) -> FillChoice | None:
    """Flag-gated entry for the decider: None (do nothing, today's behaviour) unless `fill.enabled`."""
    p = FillParams.from_config(get)
    return choose_fill(games, moments, now=now, params=p, **kw) if p.enabled else None
