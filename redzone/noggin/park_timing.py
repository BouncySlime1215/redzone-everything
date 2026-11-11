"""PARK timing: where a game's feed time sits on a stick's picture, how sure that is, and each game's padding.

Three clocks. FEED time: the stamp ESPN puts on a play row (`wallclock`). CONTENT time: YouTube's own clock for a frame
(the clock the per-tune read and Eyes see; a stick's media-session position is content time less that tune's anchor).
WALL time: when the frame is on the TV.

    K = content time of a frame  -  feed time of the game-clock second shown on it          (ledger L29, L30, L36)

THREE NUMBERS HAVE BEEN CALLED "K" (PARK-UNIFY); each has its own name, and only the first is PARK's K:
  GAME K     content time of a frame - feed stamp of the second it shows (above). PARK's K: this module, k_table (the
             "K" / "k_s" fields of every K file), decider/tune_offset, Eyes' K map, and a Prime game's K
             (park_prime.k_sample: the same quantity with Prime's frame EPOCH as content time).
  FRAME K    arena time - a Prime frame's own clock (about 9.6 s on Nov 4): a property of a Prime STREAM, fused by
             sync_edge.EdgeTracker on the old sync path (engine.sync_probe). Not a game's offset; PARK never pads with it.
             (park_prime borrows EdgeTracker's fusing only, in its own tracker, with the epoch shifted so it returns
             a GAME K.)
  FEED LEAD  how far the feed runs ahead of a stream's live edge on the wall clock (park_replay.FEED_LEAD,
             `prior_delay`, the planner core's feed_lead_s): GAME K + the live lag less the feed's own lag. Not a K.

so the frame showing feed time F is at content time F + K. K belongs to ONE game on ONE night (Nov 10: espn2 -2.78 s,
espn +2.85 s, nbc +24.77 s; 27 s apart), so it is looked up by channel, league, game and date and is never borrowed
from another game or defaulted. A game with no K of its own tonight is UNKNOWN: it gets the wide prior (`prior_k`,
`prior_sd`, padding about 48 s), never zero.

Four error parts per game, kept apart (ledger L18, L29-L30): the position readout (0.29 s after one read of the tune,
1.5 s for a tune not read), K's own spread (about 0.5 s from 5-11 reads; never trusted under `k_sd_min`), the feed
stamp (NBA 0.33 s, college 0.5 s), the keys (0.13 s). Padding = pad_k x the root of their summed squares, per game.

Pure: no device, no network. K files are read by `noggin/k_table` (one reader for all three shapes, which
`decider/tune_offset.GameOffsets` uses too). Nothing in the app imports this module yet: the PARK controller (a later
unit) will, behind `park.enabled`. Numbers are fields of `TimingParams`, read from `park.timing.*`
(and `park.pad_k`) by `TimingParams.from_config` (rule K2).

Why: the local show runner (`tools/park_live`) keeps this model inside a script; the app needs the same model as plain
data and functions, with the sign of K pinned by a test, so a jump is padded by what is really known of each game.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import NamedTuple

from redzone.noggin.k_table import K_SD_MIN, KRow, k_rows, read_k_table  # noqa: F401  (read_k_table: the file read, kept here)


@dataclass(frozen=True)
class TimingParams:
    """The measured error parts and priors (seconds). Defaults are the Nov 10 measurements (ledger L18, L29-L30, L36)."""
    pad_k: float = 4.0              # padding = pad_k x the root sum of the four parts
    read_sd: float = 0.29           # position readout after one read per tune (the label is a whole-second floor)
    tune_sd: float = 1.5            # a tune that was not read: a fresh tune lands 1.2-2.0 s (sd) off its nominal lag
    k_sd_min: float = K_SD_MIN      # K is never trusted tighter than this (5-11 reads in two clumps 0.9 s apart)
    stamp_sd_nba: float = 0.33      # ESPN NBA play stamps vs the game clock
    stamp_sd_college: float = 0.5   # college stat crews: looser
    stamp_sd_football: float = 0.40  # [PARK-NFL] football play stamp vs the snap on the picture (Eyes, Prime TNF replay JAX @ BAL,
    #                                 Nov 11: sd 0.40 s, n=41, worst +1.39 s): padding about 3 s. One replay: the live sd is
    #                                 UNKNOWN until Thursday
    key_sd: float = 0.13            # a key's landing
    eyes_sd: float = 0.3            # an edge Eyes saw itself (content time, no K and no feed stamp in it)
    prior_delay: float = 33.0       # no K: feed stamp to a stick's live edge on the wall clock
    prior_sd: float = 12.0          # ... and its doubt (the three Nov 10 K's span 27 s)
    live_lag: float = 20.0          # a stick's live edge sits this far behind content time (19-27 s measured): nominal only
    k_stale: float = 1800.0         # Eyes's K keeper calls a read older than this stale (kkeeper.STALE_S)

    @property
    def prior_k(self) -> float:
        """The K a game with none of its own is placed at: the prior delay less the live lag. Never zero."""
        return self.prior_delay - self.live_lag

    def stamp_sd(self, league: str) -> float:
        lg = str(league).lower()
        if lg in ("nfl", "cfb"):
            return self.stamp_sd_football
        return self.stamp_sd_nba if lg == "nba" else self.stamp_sd_college

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "TimingParams":
        """Each field from `park.timing.<name>` (`pad_k` from `park.pad_k`), or its default. A bad value keeps the default."""
        kw = {}
        for f in fields(cls):
            d = f.default
            v = get("park.pad_k" if f.name == "pad_k" else f"park.timing.{f.name}", d)
            kw[f.name] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else d
        return cls(**kw)


TIMING = TimingParams()


# ---------------------------------------------------------------------------------------------- K per game
@dataclass(frozen=True)
class GameK:
    """One game's K tonight: K (content minus feed, seconds), its spread, how many reads, where it came from."""
    k: float
    spread: float
    n: int = 0
    source: str = "table"


def k_for(table: dict, *, channel: str, league: str, game_id: str, teams: tuple = (), today: str,
          eyes_k: dict | None = None, unnamed_ok: bool = False, params: TimingParams = TIMING) -> GameK | None:
    """This game's K tonight, or None (unknown). `table` is any K file's data (`read_k_table`; shapes in `k_rows`).
    A row counts only when it is dated `today` (YYYY-MM-DD, given by the caller), it is not marked `wide`, its league
    matches (or it names none), and it NAMES this game: on this channel, by its feed id or a team; or, with no channel
    (the per-game file), by exactly this game id. A row on this channel that names no game could be an earlier game's:
    it counts only with `unnamed_ok` (the operator says it is this game's). Eyes's live K map (`"<channel>|<league>|<game
    id>" -> {"k_s", "spread_s", "n"}`, no date) is read the same way. Never another game's K, never a default."""
    channel, league = str(channel).lower(), str(league).lower()
    names = {str(game_id).upper()} | {str(x).upper() for x in teams if x}
    names.discard("")

    def mine(tag: str) -> bool:
        if not tag:
            return unnamed_ok
        return bool(names & set(re.split(r"[^A-Z0-9]+", tag.upper())))

    def usable(r: KRow, dated: bool = True) -> bool:
        if r.wide or (dated and r.date != today) or (r.league and r.league != league):
            return False
        if r.channel:
            return r.channel == channel and mine(r.game)
        return bool(r.game) and r.game == str(game_id)

    rows = sorted((r for r in k_rows(table) if usable(r)), key=lambda r: not r.game)   # a named row beats an unnamed one
    if rows:
        r = rows[0]
        return GameK(r.k, max(params.k_sd_min, r.doubt(params.k_sd_min)), r.n, r.source)
    for r in k_rows(eyes_k):
        if r.shape == "eyes" and usable(r, dated=False):
            return GameK(r.k, max(params.k_sd_min, r.doubt(params.k_sd_min)), r.n, "eyes")
    return None


# ---------------------------------------------------------------------------------------------- one tune
@dataclass(frozen=True)
class Tune:
    """Where one tune of a stick sits on content time: content time = anchor + media-session position. `read` = the
    anchor came from a read of this tune (label or Stats for nerds); otherwise it is the nominal guess (the tune landed
    `live_lag` behind content time), good only to `tune_sd`."""
    anchor: float
    read: bool

    @classmethod
    def from_read(cls, content_at: float, position: float) -> "Tune":
        """A read: at media-session position `position` the stick showed content time `content_at`."""
        return cls(float(content_at) - float(position), True)

    @classmethod
    def unread(cls, wall_at: float, position: float, params: TimingParams = TIMING) -> "Tune":
        """No read: at wall time `wall_at` the stick was at `position`, assumed at its live edge."""
        return cls(float(wall_at) - params.live_lag - float(position), False)

    def content(self, position: float) -> float:
        return self.anchor + float(position)

    def position(self, content: float) -> float:
        return float(content) - self.anchor


# ---------------------------------------------------------------------------------------------- the error parts
class ErrorParts(NamedTuple):
    """The four independent error parts of one game on one stick (standard deviations, seconds)."""
    position: float
    k: float
    stamp: float
    keys: float

    @property
    def total(self) -> float:
        """The root of the summed squares."""
        return math.sqrt(sum(e * e for e in self))

    def padding(self, pad_k: float) -> float:
        return float(pad_k) * self.total

    def grid_padding(self, pad_k: float) -> int:
        """The padding on PARK's whole-second grid: at least 1 s (park_sim's own rounding)."""
        return max(1, int(round(self.padding(pad_k))))


@dataclass(frozen=True)
class GameTiming:
    """What is known of one game's clock tonight: its league and its K (None = unknown)."""
    league: str
    k: GameK | None
    params: TimingParams = TIMING

    @property
    def known(self) -> bool:
        return self.k is not None

    @property
    def k_value(self) -> float:
        """K, or the wide prior's centre when this game has none (never zero, never another game's)."""
        return self.k.k if self.k is not None else self.params.prior_k

    @property
    def k_sd(self) -> float:
        return max(self.params.k_sd_min, self.k.spread) if self.k is not None else self.params.prior_sd

    def errors(self, read: bool = True) -> ErrorParts:
        """The four parts for this game on a stick whose tune was read (`read`) or not."""
        p = self.params
        return ErrorParts(p.read_sd if read else p.tune_sd, self.k_sd, p.stamp_sd(self.league), p.key_sd)

    def padding(self, read: bool = True) -> float:
        return self.errors(read).padding(self.params.pad_k)

    def grid_padding(self, read: bool = True) -> int:
        return self.errors(read).grid_padding(self.params.pad_k)

    # -- conversions (the sign is pinned by tests/test_park_facts.py with the three K's of Nov 10)
    def content_of_feed(self, feed: float) -> float:
        """Content time of the frame that shows feed time `feed`: feed + K."""
        return float(feed) + self.k_value

    def feed_of_content(self, content: float) -> float:
        """Feed time of the game-clock second shown at content time `content`: content - K."""
        return float(content) - self.k_value

    def position_of_feed(self, feed: float, tune: Tune) -> float:
        """The media-session position on a stick (this tune) that shows feed time `feed`."""
        return tune.position(self.content_of_feed(feed))

    def feed_of_position(self, position: float, tune: Tune) -> float:
        """The feed time shown at media-session position `position` of this tune."""
        return self.feed_of_content(tune.content(position))

    def air_delay(self) -> float:
        """Believed seconds from a feed stamp to its frame on a stick at its live edge (K + live lag; the prior if unknown)."""
        return self.k_value + self.params.live_lag if self.known else self.params.prior_delay


def eyes_padding(params: TimingParams = TIMING) -> int:
    """A stoppage edge Eyes saw is on content time like a read stream: no K and no feed stamp in it."""
    return ErrorParts(params.read_sd, params.eyes_sd, 0.0, params.key_sd).grid_padding(params.pad_k)
