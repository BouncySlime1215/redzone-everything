"""[POSITION-FIX items 4 and 7] A YouTube TV picture's delay as three numbers kept apart, not one.

Why: on the TV on Nov 10 a fresh tune did NOT land at a fixed lag (espn sd 1.24 s n=21, fs1 sd 1.98 s n=10, range 5 s on
both), and the feed-to-YouTube offset K turned out to be per GAME (espn2 -2.78 s, espn +2.85 s, two hours apart; ledger
L29, L30). Flags timing.per_tune_offset and timing.game_offset, both OFF.

    delay behind real life  =  U (this tune's lag behind YouTube's clock)  +  K (this game's feed time - YouTube's clock)

  * U has two parts: the channel's usual landing lag and this tune's own offset. The tune's offset is UNKNOWN after a
    fresh tune (spread `tune_sd_s`, measured 1.2-2.0 s) until one absolute read on that tune: the overlay label (0.29 s,
    yttv_label with timing.label_stamp_at_pause) or Stats for nerds' Live Latency (0.19 s, yttv_nerds). After the read
    the readout doubt is all that is left. A seek or pause on the same tune keeps it (the tune's counter holds).
  * K is read from a small JSON file (timing.game_offset_file; empty = off), one row per game, valid for that game on
    that night only. Its spread goes into the doubt. No K for a game means the delay is UNKNOWN: never K = 0.

File format (numbers only; game ids are public ESPN ids):
    {"games": {"<game id>": {"night": "2026-11-10", "k_s": -2.78, "sd_s": 0.28, "spread_s": 0.50, "n": 5}}}
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from redzone.config import default as _shipped
from redzone.tuners.yttv_nerds import LL_SD_S
from redzone.tuners.yttv_timing import LABEL_SD_S

log = logging.getLogger("redzone.tune_offset")

# the larger measured spread (fs1 1.98 s; espn 1.24 s): default_config.yaml timing.per_tune_offset_sd_s.default
TUNE_SD_DEFAULT_S = float(_shipped("timing.per_tune_offset_sd_s")["default"])
READ_SD_S = {"label": LABEL_SD_S, "live_latency": LL_SD_S}   # each reader's own doubt, defined by that reader


@dataclass(frozen=True)
class TuneRead:
    u_s: float                # the tune's landing lag behind YouTube's clock (label units)
    sd_s: float
    kind: str                 # label | live_latency
    t: float


@dataclass
class TuneOffsets:
    """Per target: the current tune, and its absolute read if one was taken. Pure bookkeeping, no I/O."""
    tune_sd_s: dict = field(default_factory=dict)       # channel -> spread of the per-tune offset; "default" key
    reads: dict = field(default_factory=dict)           # target -> TuneRead | None (None: a tune with no read yet)
    tuned_at: dict = field(default_factory=dict)        # target -> wall of the fresh tune

    def spread(self, target: str) -> float:
        ch = target.split(":", 1)[-1]
        v = self.tune_sd_s.get(target, self.tune_sd_s.get(ch, self.tune_sd_s.get("default", TUNE_SD_DEFAULT_S)))
        return float(v)

    def new_tune(self, target: str, t: float) -> float:
        """A fresh tune: its offset is unknown again. Returns the doubt it adds (1 sd)."""
        self.reads[target] = None
        self.tuned_at[target] = t
        return self.spread(target)

    def read(self, target: str, u_s: float, kind: str, t: float, sd_s: float | None = None) -> TuneRead | None:
        """One absolute read on the current tune. A read from before the tune is refused."""
        if t < self.tuned_at.get(target, float("-inf")) or not math.isfinite(float(u_s)):
            return None
        r = TuneRead(round(float(u_s), 2), float(sd_s if sd_s is not None else READ_SD_S.get(kind, 0.5)), kind, t)
        self.reads[target] = r
        return r

    def known(self, target: str) -> TuneRead | None:
        return self.reads.get(target)

    def doubt(self, target: str) -> float:
        """The tune's part of the doubt: the read's own doubt once read, else the per-tune spread."""
        r = self.reads.get(target)
        return r.sd_s if r is not None else self.spread(target)


@dataclass(frozen=True)
class GameK:
    k_s: float
    sd_s: float               # the K's own doubt with its spread carried in


class GameOffsets:
    """timing.game_offset_file: per-game K for one night. Missing file, bad rows, another night: no K (unknown)."""

    def __init__(self, rows: dict | None = None):
        self.rows = rows or {}

    @classmethod
    def load(cls, path: str | Path | None) -> "GameOffsets":
        if not path:
            return cls()
        try:
            data = json.loads(Path(path).expanduser().read_text())
        except (OSError, ValueError) as e:
            log.warning("game offsets: could not read %s (%s): every K unknown", path, type(e).__name__)
            return cls()
        games = data.get("games") if isinstance(data, dict) else None
        return cls(games if isinstance(games, dict) else {})

    def k(self, game_id: str | None, night: str) -> GameK | None:
        row = self.rows.get(str(game_id)) if game_id else None
        if not isinstance(row, dict) or row.get("night") != night:
            return None
        try:
            k = float(row["k_s"])
            sd = float(row.get("sd_s", 0.5))
            spread = float(row.get("spread_s", 0.0))
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(k) and math.isfinite(sd) and sd >= 0 and spread >= 0):
            return None
        return GameK(k, round(math.sqrt(sd * sd + (spread / 2.0) ** 2), 3))


def delay_sample(read: TuneRead | None, k: GameK | None, behind_s: float = 0.0) -> tuple[float, float] | None:
    """(delay, sd) of the picture behind real life: the tune's lag + how far the stick plays behind this tune's live
    edge (a scrub, a hold) + the game's K. None when the tune's read or the game's K is unknown."""
    if read is None or k is None:
        return None
    return round(read.u_s + float(behind_s) + k.k_s, 2), round(math.sqrt(read.sd_s ** 2 + k.sd_s ** 2), 3)
