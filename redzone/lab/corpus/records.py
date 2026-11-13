"""One game as the corpus sees it, plus the leak-fence verdict for its night and the tags every shard carries.

Why: B-02 corpus rules 2 to 5 (tracks/B-foundation-model.md): wall-clock tier, provider tag, fenced nights kept out of training,
dedup across providers. Every source adapter (sources.py) produces GameRec; everything after it reads only GameRec.

Fence verdicts for a (league, night):
  locked    inside redzone/lab/fence.py LOCKED (2025-12-12 to 2026-01-05): never read into a shard, never counted beyond a tally
  tuning    inside fence.py TUNING (October 2026): tuned on, never trained on; shards for them exist only in `hold` mode and are
            flagged train_ok=false
  fresh     on or after the league's first evaluation night (scripts/history/leagues.EVAL_FIRST_NIGHT, equal to
            agent/kaggle/sports_common.EVAL_FIRST_NIGHT): never written, same rule the B-01 fetcher applies
  undated   no night at all: cannot prove it is outside the fence, so it is dropped
  clear     everything else; only these reach train, valid or holdout shards
A game whose night came from a date column only (night_exact false) is judged on the night before, the night and the night after.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from redzone.lab import fence
from scripts.history.leagues import EVAL_FIRST_NIGHT as CORPUS_EVAL_FIRST_NIGHT

CORPUS_PROVIDERS = ("espn", "hoopr", "wehoop")          # licence source keys (scripts/history/licences.py)
CORPUS_WALL_MIN = 0.9                                    # a game is in the wall-clock tier when this share of its plays has a stamp
CORPUS_MIN_EVENTS = 10                                   # shorter games are dropped, as scripts/noggin_tokens.build does
CORPUS_WINTER = ("nba", "cbb", "wcbb", "nhl")            # season label = the year the season ends in (scripts/pbp/schema.py)
CORPUS_CLASSES = ("clear", "tuning", "locked", "fresh", "undated")
CORPUS_TRAIN_ROLES = ("train", "valid", "holdout")
CORPUS_ROLES = CORPUS_TRAIN_ROLES + ("tuning",)
CORPUS_TIERS = ("wall", "nowall")


@dataclass
class GameRec:
    """A game read from one provider: identity, night, teams and the ESPN-shaped plays the vocab v1 tokeniser reads."""
    provider: str            # licence source key: espn, hoopr or wehoop
    origin: str              # where it was read: history-shard, history-games, dataset or archive
    league: str
    season: int
    gid: str                 # the provider's game id (hoopR and wehoop use ESPN's event id)
    night: str | None        # YYYYMMDD, America/New_York with the 5 AM boundary; None when unknown
    night_exact: bool        # false when the night came from a date column only
    home: str
    away: str
    plays: list[dict] = field(default_factory=list, repr=False)
    ref: str = ""            # relative path of the file it came from (provenance; never an absolute path)

    @property
    def wall_frac(self) -> float:
        return wall_fraction(self.plays)


def _night_shift(night: str, days: int) -> str:
    d = dt.datetime.strptime(night, "%Y%m%d") + dt.timedelta(days=days)
    return d.strftime("%Y%m%d")


def night_class(league: str, night: str | None, exact: bool = True) -> str:
    """The fence verdict for a game (see the module doc). Unknown leagues raise: a league with no cut would have no fence."""
    if league not in CORPUS_EVAL_FIRST_NIGHT:
        raise ValueError(f"league {league!r} has no evaluation cut in leagues.EVAL_FIRST_NIGHT; refusing to guess a fence")
    if not night:
        return "undated"
    try:
        k = fence.night_key(night)
    except ValueError:
        return "undated"
    nights = [k] if exact else [_night_shift(k, -1), k, _night_shift(k, 1)]
    found: set[str] = set()
    for n in nights:
        w = fence.fenced(n)
        if w is not None:
            found.add(w.name)
        elif n >= CORPUS_EVAL_FIRST_NIGHT[league]:
            found.add("fresh")
    for name in ("locked", "tuning", "fresh"):
        if name in found:
            return name
    return "clear"


def season_of(league: str, night: str) -> int:
    """Season label of a night: the year the season ends in for winter sports, the year it starts in for football and baseball."""
    y, m = int(night[:4]), int(night[4:6])
    if league in CORPUS_WINTER:
        return y + 1 if m >= 8 else y
    if league in ("nfl", "cfb"):
        return y if m >= 7 else y - 1
    return y


def era_of(season: int) -> str:
    """The decade of the season label (a mixing-swarm domain is league x era x provider, Track B section 5)."""
    return f"{season // 10 * 10}s"


def wall_fraction(plays: list[dict]) -> float:
    """Share of plays with a non-empty wallclock stamp."""
    if not plays:
        return 0.0
    return sum(1 for p in plays if isinstance(p, dict) and p.get("wallclock")) / len(plays)


def tier_of(wall_frac: float) -> str:
    return "wall" if wall_frac >= CORPUS_WALL_MIN else "nowall"


def score_signature(plays: list[dict]) -> tuple[str, int]:
    """(digest, number of score changes) of the home and away score trajectory. Two providers' copies of one game share it;
    different games almost never do once there are a handful of changes."""
    from redzone.sources import espn
    prev = (0, 0)
    changes: list[tuple[int, int]] = []
    for p in plays:
        h, a = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        cur = (prev[0] if h is None else h, prev[1] if a is None else a)
        if cur != prev:
            changes.append(cur)
            prev = cur
    blob = json.dumps(changes, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:20], len(changes)


@dataclass(frozen=True)
class Light:
    """What dedup and the split planner need from a game, without its plays."""
    provider: str
    origin: str
    league: str
    season: int
    gid: str
    night: str | None
    night_exact: bool
    klass: str
    n_plays: int
    wall_frac: float
    signature: str
    n_changes: int
    ref: str

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """One physical copy of a game: the same id read from two files is two copies (a season mislabelled in one shard, a game filed twice)."""
        return (self.league, self.provider, self.origin, self.gid, self.ref)


def light_of(rec: GameRec) -> Light:
    sig, n = score_signature(rec.plays)
    return Light(rec.provider, rec.origin, rec.league, rec.season, rec.gid, rec.night, rec.night_exact,
                 night_class(rec.league, rec.night, rec.night_exact), len(rec.plays), round(rec.wall_frac, 4), sig, n, rec.ref)


def rec_summary(rec: GameRec) -> dict[str, Any]:
    return {"provider": rec.provider, "league": rec.league, "season": rec.season, "gid": rec.gid, "night": rec.night}
