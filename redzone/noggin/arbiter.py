"""The arbiter: one pure choice among `legal_options()`, with a must-show floor no brain can lower.

Why: build plan WP-17 / brain-vs-code S3. Code lists what is legal (legal.py), a brain scores it, and this picks. Nick's
promise (every touchdown and every major play) has to hold whatever a brain says, so the floor lives in code, inside
the L1 class, and only ever chooses among options that are already legal. Rule #1 outranks every must-show: an option
that isn't legal is never in the pool, and the commit gate still judges every cut at the flip.

Flag `arbiter.enabled` (code default off, read by the decider; nothing here reads config unless handed a getter).
Numbers are read as `arbiter.<name>` (`Params.from_config`), so each copied rule can be turned off alone.

Order of `choose`, first match wins:
  1. fail closed: a verb with no safe kind, or kind resume/skip, is dropped
  2. hard stops: cut ceiling, last game standing
  3. L1: only the lowest class in the pool (your team, crunch, open), and stay counts at the screen's own class
  4. floor: an unseen must-show still reachable by its route's deadline beats everything else in that class,
     ordered by trigger rank, then brain score
  5. hold one replay after a score on screen, unless a higher trigger is waiting
  6. late narrowing and the visit budget thin out everything that is not floor
  7. no usable brain opinion (null, invalid, stale): today's decider, untouched. Otherwise the best brain score

The RedZone rules (trigger order, decide last, late narrowing, a visit budget that keeps one for the final two minutes,
stay for one replay, stop whipping when one game is left) come from reporting about the show, not from measurement:
docs/agents/data/2026-11-09-redzone-viewers-production.md. That is why each is its own parameter.

`clip_key` and `pick_clip` order the clips a free hidden stick could prepare (MUST-SHOW-2: live picks first, then the
catch-up replays Nick accepted, and never parked waiting while a clip that can air first expires). The max-rate sim
(scripts/buffered_jump_sim.py policy="arbiter") measures them; the live decider does not call them yet.

Pure: no clock, no I/O, no decider import. `now` is passed in, so a replay gives the same choice.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields

from redzone.contracts import brain
from redzone.moments import tiers
from redzone.noggin import legal

STAY_ID = legal.STAY.id
NOT_FLOOR = 99        # trigger rank of anything the floor does not cover

# RedZone's reported order: score, red-zone entry, turnover, late one-possession game. Nick's major plays follow it.
# Other leagues use the same shape through their own scoring kinds.
TRIGGER_ORDER: tuple[str, ...] = (
    "td", "goal", "hr", "clutch_fg", "penalty_shot", "run_scored", "fg",   # a score
    "rz_trip",                                                              # about to score
    "turnover",
    "ot_start", "late_1goal", "wp10", "4th_try", "big3rd", "gain10", "wp5",  # late and big plays
)
SCORE_KINDS = frozenset({"td", "goal", "hr", "clutch_fg", "run_scored", "fg"})


@dataclass(frozen=True)
class Params:
    """Every copied rule is one number or switch. Read as arbiter.<name>; `enabled` itself stays with the decider."""
    trigger_order: bool = True        # False: must-shows rank by tier only
    floor_extra: tuple[str, ...] = ("rz_trip",)   # N5: 95% of red-zone trips live, though tiers.yaml files them as setup
    decide_last: bool = True          # an option whose window is not open yet (earliest_s > 0) is not committed
    late_narrow: bool = True          # in the last stretch, non-floor cuts go only to close games
    visit_budget: int = 6             # non-floor visits per game per visit_window_s; 0 turns the budget off
    visit_window_s: float = 1800.0
    visit_reserve: int = 1            # of that budget, kept for the game's final two minutes
    stay_replay_s: float = 0.0        # after a score on screen, hold this long for one replay (soccer's average is 6.8).
                                      # Off: in the show sim a hold after a score rolled into breaks, 6 ad seconds per 10 min
    repick_max_rank: int = 0            # decide last: a parked stick moves only for this rank or better (0: a touchdown)
    last_game: bool = True            # one game left: stop whipping
    ceiling_per_min: int = 3          # RedZone's reported peak, a hard ceiling; 0 turns it off
    brain_margin: float = 3.0         # a brain's cut must beat stay by this many points
    flip_margin_s: float = 1.0        # seconds of slack on every route's deadline

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "Params":
        """Each field from `arbiter.<name>`, or its default here. A bad value keeps the default."""
        kw = {}
        for f in fields(cls):
            d = f.default
            v = get(f"arbiter.{f.name}", d)
            if isinstance(d, tuple):
                kw[f.name] = tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else d
            elif isinstance(d, bool):
                kw[f.name] = v if isinstance(v, bool) else d
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                kw[f.name] = type(d)(v)
            else:
                kw[f.name] = d
        return cls(**kw)


DEFAULT_PARAMS = Params()


@dataclass(frozen=True)
class Must:
    """One must-show moment still owed. Built by code from facts in the feed, never by a brain."""
    id: str
    game_id: str
    kind: str
    tier: int
    rank: int
    air_at: float          # epoch s it starts airing on that game's picture


@dataclass(frozen=True)
class Show:
    """What the arbiter needs to know about the screen and the night. Built by the decider."""
    current: str | None = None
    current_class: int = 2                         # L1 class of the game on screen
    live_games: int = 2
    cuts_60s: int = 0
    replay_rank: int = NOT_FLOOR                   # trigger rank of a score on screen still in its replay hold
    late: bool = False                             # the slate's last stretch
    close: frozenset[str] = frozenset()            # games close enough to stay on the list when late
    visits: Mapping[str, int] = field(default_factory=dict)     # visits inside visit_window_s
    final_two: frozenset[str] = frozenset()        # games in their final two minutes


@dataclass(frozen=True)
class Choice:
    """The arbiter's answer. `option` None: the app's own decision isn't a legal option; the arbiter has no say."""
    option: brain.Option | None
    why: str


class NullBrain:
    """No opinion: until a brain exists, the arbiter runs the floor over today's decider."""

    def think(self, inp: brain.BrainInput) -> brain.BrainOutput:
        return brain.BrainOutput(tick=inp.tick, model_id="null", made_at=inp.now)


def trigger_rank(league: str, kind: str, p: Params = DEFAULT_PARAMS) -> int:
    """Lower goes first. NOT_FLOOR when this kind isn't owed in this league."""
    try:
        floor = tiers.must_show(league, kind) or tiers.tier_of(league, kind) == tiers.TIER_MUST or kind in p.floor_extra
    except KeyError:
        return NOT_FLOOR      # a league we do not show
    if not floor:
        return NOT_FLOOR
    if p.trigger_order and kind in TRIGGER_ORDER:
        return TRIGGER_ORDER.index(kind)
    return (tiers.tier_of(league, kind) or tiers.TIER_HIGHLIGHT) * 100   # after the named triggers, by tier


def clip_key(rank: int, late: bool, show: float, deadline: float, order: str = "rank", catchup: bool = False) -> tuple:
    """Sort key of a clip a free hidden stick could prepare; the lowest wins. `late` is True once it can only air as a
    catch-up, past the live window. Without catch-up, the trigger order rules and then the earliest air time. With it,
    every live clip goes first (Nick: stay on a few games, then catch up) and catch-up clips are next, in trigger order.
    order "edf": TDs first, then whichever must-show expires first."""
    if not catchup and order == "rank":
        return (rank, late, show, deadline)
    prefix = (rank == NOT_FLOOR, late) if catchup else (rank == NOT_FLOOR,)
    if order == "edf":
        return prefix + (0 if rank == 0 else 1, deadline, show)
    if order == "show":
        return (rank == NOT_FLOOR, show, rank, deadline)
    return prefix + (rank, show, deadline)


def pick_clip(cands: Sequence[tuple], now: float, flip_s: float, prep_worst_s: float, order: str = "rank", catchup: bool = False,
              max_wait_s: float = 8.0, protect_rank: int = -1):
    """The clip a free hidden stick should take: the first by `clip_key`, unless it can't air yet. Then the nearest clip that
    fits in front of it and still leaves the first its own window (the stick that clip frees needs up to prep_worst_s to set
    the first up). Why: a stick parked on a clip it must wait a minute for leaves the screen idle while a clip it could
    air first expires. A first clip of rank <= protect_rank always keeps the stick (nothing goes in front of it). Each cand is
    (id, rank, late, show, deadline, seconds, last legal flip of the live or deep window)."""
    if not cands:
        return None
    key = lambda c: clip_key(c[1], c[2], c[3], c[4], order, catchup)   # noqa: E731
    first = min(cands, key=key)
    if first[3] - now <= max_wait_s or first[1] <= protect_rank:
        return first[0]
    fits = [c for c in cands if c is not first and c[3] <= first[3] and (c[1] != NOT_FLOOR or first[1] == NOT_FLOOR)
            and c[3] + max(flip_s + c[5], prep_worst_s) + flip_s <= first[6]]
    return min(fits, key=lambda c: (c[3], key(c)))[0] if fits else first[0]


def must_of(league: str, kind: str, game_id: str, air_at: float, moment_id: str = "", p: Params = DEFAULT_PARAMS) -> Must | None:
    """The Must for this moment, or None when the floor doesn't cover it."""
    r = trigger_rank(league, kind, p)
    if r == NOT_FLOOR:
        return None
    t = tiers.tier_of(league, kind) or tiers.TIER_HIGHLIGHT
    return Must(moment_id or f"{game_id}:{kind}:{air_at:.0f}", game_id, kind, t, r, air_at)


def timeline_word(base: str, play_kind: str, big: bool) -> str:
    """The moments/tiers word for a live Timeline play, "" when it has none. The Timeline keeps only the play's kind and
    `big`, so a 10+ yard gain, a 3rd-down conversion, a 4th-down try or a win-probability swing reach the floor only as
    `big` plays, ranked with 10+ gains. Mirrors engine._moment_word for the kinds the floor reads."""
    k = play_kind or ""
    if base == "nfl":
        if k == "safety":
            return "turnover"
        return k if k in ("td", "turnover", "fg") else "gain10" if big else ""
    if base == "nba":
        return "clutch_fg" if big else ""
    if base == "nhl":
        return k if k == "goal" else "penalty_shot" if "penalty_shot" in k else ""
    if base == "mlb":
        return "hr" if k == "hr" else "run_scored" if big else ""
    return ""


def route_deadline(air_at: float, prep_s: float, p: Params = DEFAULT_PARAMS) -> float:
    """The last epoch second this route can start and still have the picture up when the moment airs."""
    return air_at - max(0.0, prep_s) - p.flip_margin_s


def owed(must: Must, prep_s: float, now: float, p: Params = DEFAULT_PARAMS) -> bool:
    """True while this route still gets there in time. Past it the moment is a rewind's job, not a cut's."""
    return now <= route_deadline(must.air_at, prep_s, p)


def _cuts(pool: Iterable[brain.Option]) -> list[brain.Option]:
    return [o for o in pool if o.verb != "stay"]


def lowest_l1_class(pool: Sequence[brain.Option], current_class: int) -> list[brain.Option]:
    """L1: only the options in the best class. Stay counts at the class of the game on screen."""
    best = min([o.l1_class for o in _cuts(pool)] + [current_class])
    return [o for o in pool if (current_class if o.verb == "stay" else o.l1_class) == best]


def must_show_floor(pool: Sequence[brain.Option], musts: Iterable[Must], current: str | None, now: float,
                    p: Params = DEFAULT_PARAMS) -> dict[str, tuple[int, Must]]:
    """{option id: (best rank, its must)} for every cut in `pool` that a reachable must-show justifies. A must-show on
    the game already on screen counts for stay, at no prep."""
    out: dict[str, tuple[int, Must]] = {}
    by_game = {o.game_id: o for o in _cuts(pool)}
    for m in musts:
        if m.game_id == current:
            o, prep = next((x for x in pool if x.verb == "stay"), None), 0.0
        else:
            o = by_game.get(m.game_id)
            prep = o.needs_prep_s if o is not None else 0.0
        if o is None or not owed(m, prep, now, p):
            continue
        if o.id not in out or m.rank < out[o.id][0]:
            out[o.id] = (m.rank, m)
    return out


def _thin(pool: Sequence[brain.Option], floor: Mapping[str, object], show: Show, p: Params) -> list[brain.Option]:
    """Late narrowing and the visit budget. Stay and floor options are never thinned."""
    out = []
    for o in pool:
        if o.verb == "stay" or o.id in floor:
            out.append(o)
            continue
        if p.late_narrow and show.late and o.game_id not in show.close:
            continue
        if p.visit_budget > 0 and o.game_id is not None:
            room = p.visit_budget if o.game_id in show.final_two else p.visit_budget - p.visit_reserve
            if show.visits.get(o.game_id, 0) >= room:
                continue
        out.append(o)
    return out


def choose(inp: brain.BrainInput, out: brain.BrainOutput | None, baseline: str = STAY_ID, *, musts: Iterable[Must] = (),
           show: Show = Show(), p: Params = DEFAULT_PARAMS) -> Choice:
    """One legal option, or `option=None` when today's decider picked something outside the list (see Choice)."""
    pool = [o for o in inp.options if o.verb == "stay" or legal.kind_of(o.verb)]
    if p.decide_last:
        pool = [o for o in pool if o.verb == "stay" or o.earliest_s <= 0.0]
    stay = next((o for o in pool if o.verb == "stay"), legal.STAY)
    if stay not in pool:
        pool.insert(0, stay)
    if not _cuts(pool):
        return Choice(_base(baseline, pool, stay), "no_cuts")
    if p.ceiling_per_min > 0 and show.cuts_60s >= p.ceiling_per_min:
        return Choice(stay, "ceiling")
    if p.last_game and show.live_games <= 1:
        return Choice(stay, "last_game")
    musts = tuple(musts)
    l1 = lowest_l1_class(pool, show.current_class)
    floor = must_show_floor(l1, musts, show.current, inp.now, p)
    if floor:
        scored = out.scores if out is not None and brain.valid(out, inp) else {}
        byid = {o.id: o for o in l1}
        best = min(floor, key=lambda i: (floor[i][0], -float(scored.get(i, 0.0)), i))
        rank, m = floor[best]
        if p.stay_replay_s > 0 and rank >= show.replay_rank and best != stay.id:
            return Choice(stay, "stay_replay")
        return Choice(byid[best], f"floor:{m.kind}")
    if show.replay_rank != NOT_FLOOR and p.stay_replay_s > 0:
        return Choice(stay, "stay_replay")
    thin = _thin(pool, {}, show, p)
    if out is None or not brain.valid(out, inp) or not out.scores:
        chosen = _base(baseline, thin, stay, pool)
        return Choice(chosen, "baseline" if chosen is None or chosen.id == baseline else "thinned")
    l1thin = [o for o in lowest_l1_class(thin, show.current_class)] or [stay]
    best = max(l1thin, key=lambda o: (out.scores.get(o.id, -float("inf")), o.id))
    if best.verb != "stay":
        s_best, s_stay = out.scores.get(best.id, -float("inf")), out.scores.get(stay.id, -float("inf"))
        if s_best - s_stay < p.brain_margin:
            return Choice(stay, "margin")
    return Choice(best, "brain")


def _base(baseline: str, kept: Sequence[brain.Option], stay: brain.Option,
          full: Sequence[brain.Option] | None = None) -> brain.Option | None:
    """Today's decider's own pick. None: it's not on the legal list at all (its call). Thinned off the list: stay."""
    if any(o.id == baseline for o in kept):
        return next(o for o in kept if o.id == baseline)
    return stay if full is not None and any(o.id == baseline for o in full) else None


def preload_target(musts: Iterable[Must], prep_of: Mapping[str, float], standby: str | None, now: float,
                   p: Params = DEFAULT_PARAMS) -> str | None:
    """The game the hidden stick must take for the floor, or None to leave it to the usual pick. Decide last: asked
    every tick. A stick already parked on a game only moves for a strictly higher trigger, and only one of rank
    repick_max_rank or better: throwing away prep for a lesser play cost more plays than it found (show sim)."""
    best: tuple[int, str] | None = None
    held = NOT_FLOOR
    for m in musts:
        if m.game_id == standby:
            held = min(held, m.rank)
            continue
        if m.game_id in prep_of and owed(m, prep_of[m.game_id], now, p) and (best is None or (m.rank, m.game_id) < best):
            best = (m.rank, m.game_id)
    if best is None or best[0] >= held:
        return None
    return best[1] if standby is None or best[0] <= p.repick_max_rank else None
