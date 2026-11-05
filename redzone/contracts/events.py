"""[EXP-003] One event language for every sport: each play of any league becomes one typed Event built
from a few shared primitives (what kind of thing happened, the clock, the score, possession, and what the
game does right after), and the same Event packs into the NFM's vocab v1 tokens (#169,
scripts/noggin_tokens.py: 6 ids per event).

Serves: the NFM main track (one vocab, every sport) and contract_violations (one definition of an
event). Owner: lane 4 (contracts), with lane 1 (the tokenizer reads TOKENS_V1 from here).

    ev = from_play(League.NBA, play, prev)      # prev: the Event before it (None for the first)
    ev.primitive                                # Primitive.SHOT, .SCORE, .STOPPAGE, ...
    tokens_v1(ev)                               # the 6 vocab v1 ids, exactly what #169 writes
    unmapped_share(League.NHL, plays)           # share of plays no primitive covers (EXP-003 bar: < 1%)

The primitive comes from the play's kind (sources.espn._kind) by keyword rules shared across sports; a
play that scored is a SCORE whatever its kind. A kind no rule covers is Primitive.UNMAPPED, which is the
EXP-003 measure. Nothing here decides a cut: it describes events, it doesn't grade or choose.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from enum import Enum

from redzone.models import PERIOD_S, League, Stoppage


class Primitive(str, Enum):
    """What kind of thing an event is, the same words in every sport."""
    SCORE = "score"              # points went up on this play (TD, FG, basket, goal, run)
    SHOT = "shot"                # a try at the goal that didn't score (miss, block, save, incompletion is PLAY)
    PLAY = "play"                # live action that keeps the ball (rush, pass, hit, offensive rebound)
    POSSESSION = "possession"    # the ball changes hands or restarts (turnover, defensive rebound, punt, faceoff)
    FOUL = "foul"                # a foul or penalty
    STOPPAGE = "stoppage"        # play stops: timeout, review, whistle, delay
    PERIOD = "period"            # the clock's frame: period start / end, half, game end, two-minute warning
    ADMIN = "admin"              # bookkeeping: substitutions, lineup changes
    UNMAPPED = "unmapped"        # no rule covers this kind (the EXP-003 measure)


# Keyword rules, checked in order: the first rule whose word appears in the kind wins. Shared by every sport;
# a word only one sport uses is harmless to the others.
RULES: tuple[tuple[Primitive, tuple[str, ...]], ...] = (
    (Primitive.ADMIN, ("substitution", "lineup")),
    (Primitive.PERIOD, ("end_period", "end_of_half", "end_of_game", "end_game", "end_of_regulation", "period_start",
                        "period_end", "shootout_end", "two-minute", "two_minute", "start_period")),
    (Primitive.STOPPAGE, ("timeout", "stoppage", "review", "challenge", "delay", "injury", "broken_stick")),
    (Primitive.POSSESSION, ("turnover", "turno", "turn", "defensive_rebound", "team_rebound", "takeaway", "giveaway",
                            "steal", "punt", "kickoff", "face_off", "faceoff", "jumpball", "jump_ball", "recovery",
                            "safety", "traveling", "kicked_ball", "shot_clock", "offensive_charge", "3-second",
                            "goaltending")),
    (Primitive.FOUL, ("foul", "penalty", "technical", "tripping", "roughing", "interference", "hooking", "slashing",
                      "holding", "high-sticking", "high_sticking", "fighting", "cross_checking", "too_many_men",
                      "misconduct", "instigator", "boarding", "infraction", "unsportsmanlike", "elbowing",
                      "embellishment", "abuse", "kneeing", "throwing", "thowing", "removing", "closing_hand",
                      "charging", "spearing", "butt_ending", "clipping")),
    (Primitive.SHOT, ("shot", "missed", "blocked", "block", "free_throw", "field_goal", "layup", "dunk", "jump",
                      "hook", "tip", "three", "heave", "finger_roll", "alley_oop", "fade", "putback", "goal")),
    (Primitive.PLAY, ("rush", "pass", "sack", "hit", "offensive_rebound", "rebound", "kneel", "spike", "scramble",
                      "pitch", "strike", "ball", "foul_ball", "single", "double", "triple", "out", "walk")),
)


def primitive_of(kind: str, scored: bool = False) -> Primitive:
    """The primitive of a play kind (sources.espn._kind). A play that scored is a SCORE."""
    if scored:
        return Primitive.SCORE
    k = (kind or "").lower()
    if not k:
        return Primitive.UNMAPPED
    for prim, words in RULES:
        if any(w in k for w in words):
            return prim
    return Primitive.UNMAPPED


@dataclass(frozen=True)
class Event:
    """One play in the shared language. Times in seconds; margin = home minus away after the play."""
    league: str                  # the league value ("cbb"); base is its pro league ("nba")
    base: str
    kind: str                    # sources.espn._kind
    primitive: Primitive
    period: int                  # 1.. (OT continues the count)
    clock_frac: float            # 0 at the period's start .. <1 at its end (0 when untimed or unknown)
    dt_s: float | None           # wall seconds since the previous event (game-clock delta without a wall clock)
    home: int                    # score after the play
    away: int
    pts_home: int                # points on this play
    pts_away: int
    stop: Stoppage               # what the game is doing right after
    wall: float | None           # epoch s of the play, when the feed has it
    clock_s: float | None        # game clock left in the period, when the feed has it

    @property
    def margin(self) -> int:
        return self.home - self.away


def from_play(league: League, play: dict, prev: Event | None = None) -> Event:
    """An ESPN-format play dict (the live parser's shape) -> Event; prev carries the running score and times."""
    from redzone.sources import espn
    lg = League(league)
    w = espn._ts(play.get("wallclock"))
    per = max(1, espn._int(espn._get(play, "period", "number")) or 1)
    c = espn._clock_s(espn._get(play, "clock", "displayValue"))
    last_w = prev.wall if prev else None
    last_c = prev.clock_s if prev else None
    dt = (w - last_w) if w is not None and last_w is not None else (
        abs(last_c - c) if c is not None and last_c is not None else None)
    per_s = float(PERIOD_S.get(lg) or 0) or None
    frac = 0.0 if c is None or not per_s else max(0.0, min(0.999, 1.0 - c / per_s))
    ph, pa = (prev.home, prev.away) if prev else (0, 0)
    hs, as_ = espn._int(play.get("homeScore")), espn._int(play.get("awayScore"))
    hs = ph if hs is None else hs
    as_ = pa if as_ is None else as_
    dh, da = max(0, hs - ph), max(0, as_ - pa)
    kind = espn._kind(lg, play)
    return Event(league=lg.value, base=lg.base.value, kind=kind, primitive=primitive_of(kind, bool(dh or da)),
                 period=per, clock_frac=frac, dt_s=dt, home=hs, away=as_, pts_home=dh, pts_away=da,
                 stop=espn.stoppage_for(lg, play), wall=w if w is not None else last_w,
                 clock_s=c if c is not None else last_c)


def events(league: League, plays: list[dict]) -> list[Event]:
    out: list[Event] = []
    for p in plays:
        out.append(from_play(league, p, out[-1] if out else None))
    return out


def unmapped_share(league: League, plays: list[dict]) -> float:
    """Share of plays no primitive covers (0.0 for no plays)."""
    evs = events(league, plays)
    return sum(e.primitive is Primitive.UNMAPPED for e in evs) / len(evs) if evs else 0.0


# ---- vocab v1 (#169, scripts/noggin_tokens.py): the id layout, defined once here --------------------------
VOCAB_VERSION = 1
BASES = ("nfl", "nba", "nhl", "mlb")
BOS_LEAGUES = ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb")   # BOS slots: fixed by v1 (wcbb keeps its id)
KIND_SLOTS, TEAM_SLOTS, DT_BUCKETS, CLOCK_BUCKETS, MAX_PERIOD, MARGIN_CAP = 2048, 512, 32, 16, 10, 20
STOPS = ("none", "timeout", "two_minute_warning", "end_period", "halftime", "intermission", "review", "injury",
         "pitching_change", "score_break", "whistle", "unknown")
PAD, EOS, UNK_DT = 0, 1, 2
BOS0 = 3
TEAM0 = BOS0 + len(BOS_LEAGUES)
KIND0 = TEAM0 + len(BASES) * TEAM_SLOTS
DT0 = KIND0 + len(BASES) * KIND_SLOTS
CLOCK0 = DT0 + DT_BUCKETS
MARGIN0 = CLOCK0 + MAX_PERIOD * CLOCK_BUCKETS
PTS0 = MARGIN0 + 2 * MARGIN_CAP + 1
STOP0 = PTS0 + 16
VOCAB_SIZE = STOP0 + len(STOPS)
TOKENS_PER_EVENT = 6


def _slot(s: str, n: int) -> int:
    return zlib.crc32(s.encode()) % n


def dt_token(dt: float | None) -> int:
    if dt is None or not math.isfinite(dt) or dt < 0:
        return UNK_DT
    return DT0 + min(DT_BUCKETS - 1, int(math.log2(1.0 + dt) * 2.6))     # 0 s .. ~2 h


def tokens_v1(e: Event) -> tuple[int, int, int, int, int, int]:
    """KIND, DT, CLOCK, MARGIN, PTS, STOP: the 6 vocab v1 ids of one event."""
    per = min(MAX_PERIOD, e.period)
    pts = PTS0 + (min(7, e.pts_home) if e.pts_home else 8 + min(7, e.pts_away) if e.pts_away else 0)
    stop = e.stop.value
    return (KIND0 + BASES.index(e.base) * KIND_SLOTS + _slot(e.kind, KIND_SLOTS), dt_token(e.dt_s),
            CLOCK0 + (per - 1) * CLOCK_BUCKETS + int(e.clock_frac * CLOCK_BUCKETS),
            MARGIN0 + MARGIN_CAP + max(-MARGIN_CAP, min(MARGIN_CAP, e.margin)),
            pts, STOP0 + (STOPS.index(stop) if stop in STOPS else STOPS.index("unknown")))
