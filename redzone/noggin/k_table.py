"""The per-game offset K: one reader for every K file, whatever its shape.

    K = YouTube-clock time of a frame  -  feed time of the game-clock second shown on it

A game whose picture shows each feed stamp 24.30 s later on YouTube's clock has K = +24.30 s. K belongs to ONE game on
ONE night (Nov 10: espn2 -2.78 s, espn +2.85 s, nbc +24.77 s), so a row that names a game id applies to that game only, a
row that names no game is never another game's K, and a game with no row is unknown (wide), never K = 0.

Three shapes were written on Nov 10 (`k_rows` reads all three; tests/test_k_table.py has one made-up row of each):
  table  {"<channel>": {"channel", "league", "game"?, "date", "K", "spread", "n", "source", "wide", ...}}
         (tools/calibrate and the watcher, keyed by channel)
  games  {"games": {"<game id>": {"night", "k_s", "sd_s", "spread_s", "n"}}}   (timing.game_offset_file)
  eyes   {"<channel>|<league>|<game id>": {"k_s", "spread_s", "n", "date"?}}  (the watcher's K map)

Callers: the PARK timing model's `k_for` (by channel, league, game and date) and `decider/tune_offset.GameOffsets` (by
game id and night). Pure apart from the one file read; numbers only (game ids are public ESPN ids).

Why: three modules read K in three shapes and nothing converted between them (integration plan S6).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

K_SD_MIN = 0.5   # K is never trusted tighter than this (5-11 reads in two clumps 0.9 s apart, ledger L29-L30)


def read_k_table(path: str | Path) -> dict:
    """The one K file reader (every K file goes through here, then `k_rows`). A missing or unreadable file is an empty
    table: every game unknown. The data is returned as written; `k_rows` turns any of the three shapes into rows."""
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class KRow:
    """One K reading in the common form, whatever file shape it came from."""
    channel: str          # "" = the row names no channel (the per-game file)
    league: str           # "" = the row names no league
    game: str             # "" = the row names no game: it is never another game's K
    date: str             # the night it was measured (YYYY-MM-DD)
    k: float              # content time of a frame minus feed time of the game-clock second on it, seconds
    sd: float | None      # the K's doubt as the file gives it (None = not given)
    n: int
    source: str
    wide: bool            # the tool itself says this K is not tight enough to use
    shape: str            # "table" (calibration / watcher, keyed by channel) | "games" (per game id) | "eyes" ("ch|lg|id")

    def doubt(self, k_sd_min: float) -> float:
        """The per-game file's doubt is used as written (its spread is already carried in); a calibration or watcher
        spread is never trusted tighter than `k_sd_min`."""
        if self.shape == "games" and self.sd is not None:
            return self.sd
        return max(k_sd_min, self.sd if self.sd else k_sd_min)


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _count(v) -> int:
    x = _num(v)
    return int(x) if x is not None and x >= 0 else 0


def k_rows(data) -> list[KRow]:
    """Every usable K row in `data`, in any of the three shapes written on Nov 10:
      table  {"<channel>": {"channel", "league", "game"?, "date", "K", "spread", "n", "source", "wide", ...}}
             (tools/calibrate and the watcher; a row with "game" applies to that game only)
      games  {"games": {"<game id>": {"night", "k_s", "sd_s", "spread_s", "n"}}}   (timing.game_offset_file)
      eyes   {"<channel>|<league>|<game id>": {"k_s", "spread_s", "n", "date"?}}  (the watcher's K map)
    Rows with no number for K are dropped. Nothing is defaulted: a missing date or game stays missing."""
    out: list[KRow] = []
    if not isinstance(data, dict):
        return out
    games = data.get("games")
    for gid, r in (games.items() if isinstance(games, dict) else ()):
        if not isinstance(r, dict) or not str(gid):
            continue
        k, sd, spread = _num(r.get("k_s")), _num(r.get("sd_s", 0.5)), _num(r.get("spread_s", 0.0))
        if k is None or sd is None or spread is None or sd < 0 or spread < 0:
            continue
        out.append(KRow(str(r.get("channel") or "").lower(), str(r.get("league") or "").lower(), str(gid),
                        str(r.get("night") or r.get("date") or ""), k, round(math.sqrt(sd * sd + (spread / 2.0) ** 2), 3),
                        _count(r.get("n")), str(r.get("source") or "game_offset_file"), bool(r.get("wide")), "games"))
    for key, r in data.items():
        if key == "games" or not isinstance(r, dict):
            continue
        part = str(key).split("|") if "|" in str(key) else []
        k = _num(r["K"]) if r.get("K") is not None else _num(r.get("k_s"))
        if k is None:
            continue
        sd = _num(r.get("spread")) if r.get("spread") is not None else _num(r.get("spread_s"))
        out.append(KRow(str(r.get("channel") or (part[0] if part else "")).lower(),
                        str(r.get("league") or (part[1] if len(part) > 1 else "")).lower(),
                        str(r.get("game") or (part[2] if len(part) > 2 else "")),
                        str(r.get("date") or r.get("night") or ""), k, sd if sd is None or sd >= 0 else None,
                        _count(r.get("n")), str(r.get("source") or ("eyes" if part else "table")), bool(r.get("wide")),
                        "eyes" if part else "table"))
    return out
