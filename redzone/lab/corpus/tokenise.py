"""Vocab v1 tokenise and detokenise for corpus games, with the coverage counters and the name tables that make tokens readable.

Why: B-02 needs `detokenise(tokenise(game))` to reproduce a game's event sequence (WORKPLAN done-when 3) and a vocab-coverage number
(done-when 4). The token layout itself is scripts/noggin_tokens.py (vocab v1, 10,511 ids, six tokens per event); this module wraps it
without changing one id, and fingerprints the layout so a corpus built under a different layout is refused at verify time.

What a token group keeps, and what it cannot:
  kept     league, event kind (hashed into 2048 slots per base league), wall-clock gap bucket (32), period, clock bucket (16 per
           period), score margin (-20..20), points and side that scored (0..7), what the game is doing after the play
  lost     team names (512 slots), exact seconds, player ids, play text. The kind and team name tables (NameTables) record which
           names landed in each slot, so a slot can be read back as a name when it is not shared with another name.
Unknown, in the coverage report: a wall-clock gap that is unknown on any event but the first (the first has no previous event), a
stoppage the rules table could not classify, and an event with no kind at all. A shared kind slot is counted separately.
"""
from __future__ import annotations

import hashlib
import json
import math
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np

from redzone.lab.corpus.records import GameRec
from scripts import noggin_tokens as NT

CORPUS_COUNTERS = ("events", "unknown_events", "unk_dt", "unk_dt_after_first", "stop_unknown", "kind_blank", "clock_missing",
                   "margin_clipped", "crunch_events")


def vocab_layout() -> dict[str, Any]:
    """Every constant that fixes a token id in vocab v1."""
    return {
        "version": NT.VOCAB_VERSION, "size": NT.VOCAB_SIZE, "tokens_per_event": NT.TOKENS_PER_EVENT,
        "bases": list(NT.BASES), "leagues": list(NT.LEAGUES), "stops": list(NT.STOPS),
        "offsets": {k: int(getattr(NT, k)) for k in ("PAD", "EOS", "UNK_DT", "BOS0", "TEAM0", "KIND0", "DT0", "CLOCK0", "MARGIN0",
                                                      "PTS0", "STOP0")},
        "slots": {"kind": NT.KIND_SLOTS, "team": NT.TEAM_SLOTS, "dt": NT.DT_BUCKETS, "clock": NT.CLOCK_BUCKETS,
                  "periods": NT.MAX_PERIOD, "margin_cap": NT.MARGIN_CAP},
    }


def vocab_fingerprint() -> str:
    return hashlib.sha256(json.dumps(vocab_layout(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- name tables ---------------------------------------------------------------------------------------------------

class NameTables:
    """Which kind names and team abbreviations landed in each hashed slot, with counts (kind slots are 2048 per base league, so
    two kinds can share one; the table is how that is measured and how a slot is read back as a name)."""

    def __init__(self) -> None:
        self.kinds: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
        self.teams: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))

    def add_kind(self, base: str, slot: int, name: str) -> None:
        d = self.kinds[base][slot]
        d[name] = d.get(name, 0) + 1

    def add_team(self, base: str, slot: int, abbr: str) -> None:
        d = self.teams[base][slot]
        d[abbr] = d.get(abbr, 0) + 1

    def merge(self, other: "NameTables") -> None:
        for mine, theirs in ((self.kinds, other.kinds), (self.teams, other.teams)):
            for base, slots in theirs.items():
                for slot, names in slots.items():
                    for n, c in names.items():
                        d = mine[base][slot]
                        d[n] = d.get(n, 0) + c

    def to_json(self) -> dict[str, Any]:
        def dump(t: Mapping) -> dict:
            return {b: {str(s): {n: c for n, c in sorted(names.items())} for s, names in sorted(slots.items())}
                    for b, slots in sorted(t.items())}
        return {"format": "redzone-corpus-names/1", "vocab_sha256": vocab_fingerprint(), "kinds": dump(self.kinds), "teams": dump(self.teams)}

    @classmethod
    def from_json(cls, doc: Mapping) -> "NameTables":
        t = cls()
        for attr in ("kinds", "teams"):
            tgt = getattr(t, attr)
            for base, slots in (doc.get(attr) or {}).items():
                for slot, names in slots.items():
                    tgt[base][int(slot)] = {str(n): int(c) for n, c in names.items()}
        return t

    def shared_kind_events(self) -> int:
        """Events whose kind slot is shared by two or more different kind names (their kind cannot be read back uniquely)."""
        return sum(sum(names.values()) for slots in self.kinds.values() for names in slots.values() if len(names) > 1)

    def kind_names(self, base: str, slot: int) -> list[str]:
        return sorted(self.kinds.get(base, {}).get(slot, {}))

    def team_names(self, base: str, slot: int) -> list[str]:
        return sorted(self.teams.get(base, {}).get(slot, {}))


# --- tokenise -------------------------------------------------------------------------------------------------------

@dataclass
class Tokenised:
    tokens: np.ndarray                     # uint16, BOS home away [6 per event] EOS
    flags: int                             # 1 overtime, 2 comeback, 4 blowout (scripts/noggin_tokens.game_tags)
    counters: dict[str, int]
    kinds: list[str] = field(repr=False, default_factory=list)

    @property
    def events(self) -> int:
        return (len(self.tokens) - 4) // NT.TOKENS_PER_EVENT


def _slot_of_kind(base: str, token: int) -> int:
    return int(token) - NT.KIND0 - NT.BASES.index(base) * NT.KIND_SLOTS


def tokenise_game(rec: GameRec, names: NameTables | None = None) -> Tokenised:
    """Vocab v1 tokens for one game, with the per-game coverage counters. Adds the game's kind and team names to `names`."""
    from redzone.models import League
    from redzone.sources import espn
    lg = League(rec.league)
    base = lg.base.value
    tokens = NT.encode_game(rec.league, rec.plays, rec.home, rec.away)
    tags = NT.game_tags(rec.league, tokens)
    ev = tokens[3:-1].reshape(-1, NT.TOKENS_PER_EVENT)
    kinds = [espn._kind(lg, p) for p in rec.plays]
    n = len(ev)
    unk_dt = ev[:, 1] == NT.UNK_DT
    stop_unk = ev[:, 5] == NT.STOP0 + NT.STOPS.index("unknown")
    blank = np.fromiter((not k for k in kinds), dtype=bool, count=n)
    clock_missing = sum(1 for p in rec.plays if espn._clock_s(espn._get(p, "clock", "displayValue")) is None)
    unk_after_first = unk_dt.copy()
    if n:
        unk_after_first[0] = False
    counters = {
        "events": n, "unknown_events": int((unk_after_first | stop_unk | blank).sum()), "unk_dt": int(unk_dt.sum()),
        "unk_dt_after_first": int(unk_after_first.sum()), "stop_unknown": int(stop_unk.sum()), "kind_blank": int(blank.sum()),
        "clock_missing": int(clock_missing), "margin_clipped": int((np.abs(ev[:, 3].astype(np.int64) - NT.MARGIN0 - NT.MARGIN_CAP)
                                                                     >= NT.MARGIN_CAP).sum()),
        "crunch_events": int(tags["crunch_events"]),
    }
    if names is not None:
        for k, tok in zip(kinds, ev[:, 0]):
            names.add_kind(base, _slot_of_kind(base, tok), k)
        names.add_team(base, int(tokens[1]) - NT.TEAM0 - NT.BASES.index(base) * NT.TEAM_SLOTS, rec.home or "?")
        names.add_team(base, int(tokens[2]) - NT.TEAM0 - NT.BASES.index(base) * NT.TEAM_SLOTS, rec.away or "?")
    flags = int(tags["ot"]) | int(tags["comeback"]) << 1 | int(tags["blowout"]) << 2
    return Tokenised(tokens, flags, counters, kinds)


def add_counters(total: dict[str, int], part: Mapping[str, int]) -> None:
    for k in CORPUS_COUNTERS:
        total[k] = total.get(k, 0) + int(part.get(k, 0))


def counters_from_tokens(tokens: np.ndarray, starts: Iterable[int]) -> dict[str, int]:
    """The coverage counters that follow from tokens alone (unk_dt, unk_dt_after_first, stop_unknown, margin_clipped, events), summed over
    the games that start at `starts`. The verifier recomputes these from the shard body and compares them with the shard header."""
    t = np.asarray(tokens)
    out = {"events": 0, "unk_dt": 0, "unk_dt_after_first": 0, "stop_unknown": 0, "margin_clipped": 0}
    stop_unk = NT.STOP0 + NT.STOPS.index("unknown")
    bounds = list(starts)
    ends = bounds[1:] + [len(t)]
    for s, e in zip(bounds, ends):
        ev = t[s + 3:e - 1].reshape(-1, NT.TOKENS_PER_EVENT)
        out["events"] += len(ev)
        u = ev[:, 1] == NT.UNK_DT
        out["unk_dt"] += int(u.sum())
        out["unk_dt_after_first"] += int(u.sum()) - (int(u[0]) if len(u) else 0)
        out["stop_unknown"] += int((ev[:, 5] == stop_unk).sum())
        out["margin_clipped"] += int((np.abs(ev[:, 3].astype(np.int64) - NT.MARGIN0 - NT.MARGIN_CAP) >= NT.MARGIN_CAP).sum())
    return out


# --- detokenise -----------------------------------------------------------------------------------------------------

def dt_bounds(bucket: int) -> tuple[float, float]:
    """Seconds a wall-clock gap bucket covers: [low, high); the last bucket is open-ended."""
    lo = 2 ** (bucket / 2.6) - 1.0
    hi = math.inf if bucket >= NT.DT_BUCKETS - 1 else 2 ** ((bucket + 1) / 2.6) - 1.0
    return max(0.0, lo), hi


class TokenError(ValueError):
    """A token stream that is not a vocab v1 game."""


def detokenise_game(tokens: Iterable[int], names: NameTables | None = None) -> dict[str, Any]:
    """The game a token stream says: league, team slots (and names when `names` knows them) and one dict per event.

    Event keys: kind_slot, kind (list of names in that slot when `names` is given), dt_bucket (None = unknown), period, clock_bucket,
    margin (home minus away, clipped to +-20), pts_side ('home', 'away' or None), pts, stop."""
    t = [int(x) for x in tokens]
    if len(t) < 4 or t[-1] != NT.EOS:
        raise TokenError("a game is BOS, home, away, events, EOS")
    if not NT.BOS0 <= t[0] < NT.BOS0 + len(NT.LEAGUES):
        raise TokenError(f"first token {t[0]} is not a league BOS")
    if (len(t) - 4) % NT.TOKENS_PER_EVENT:
        raise TokenError("event tokens are not a multiple of six")
    league = NT.LEAGUES[t[0] - NT.BOS0]
    from redzone.models import League
    base = League(league).base.value
    b = NT.BASES.index(base)
    slots = []
    for tok in (t[1], t[2]):
        s = tok - NT.TEAM0 - b * NT.TEAM_SLOTS
        if not 0 <= s < NT.TEAM_SLOTS:
            raise TokenError(f"team token {tok} is not a {base} team")
        slots.append(s)
    events = []
    for i in range(3, len(t) - 1, NT.TOKENS_PER_EVENT):
        kind, dtk, clock, margin, pts, stop = t[i:i + NT.TOKENS_PER_EVENT]
        ks = kind - NT.KIND0 - b * NT.KIND_SLOTS
        if not 0 <= ks < NT.KIND_SLOTS:
            raise TokenError(f"kind token {kind} is not a {base} kind")
        if dtk == NT.UNK_DT:
            dtb = None
        elif NT.DT0 <= dtk < NT.DT0 + NT.DT_BUCKETS:
            dtb = dtk - NT.DT0
        else:
            raise TokenError(f"dt token {dtk}")
        if not NT.CLOCK0 <= clock < NT.MARGIN0:
            raise TokenError(f"clock token {clock}")
        per, cb = divmod(clock - NT.CLOCK0, NT.CLOCK_BUCKETS)
        if not NT.MARGIN0 <= margin < NT.PTS0:
            raise TokenError(f"margin token {margin}")
        pv = pts - NT.PTS0
        if pv == 0:
            side, pn = None, 0
        elif 1 <= pv <= 7:
            side, pn = "home", pv
        elif 9 <= pv <= 15:
            side, pn = "away", pv - 8
        else:
            raise TokenError(f"points token {pts}")
        sv = stop - NT.STOP0
        if not 0 <= sv < len(NT.STOPS):
            raise TokenError(f"stop token {stop}")
        ev = {"kind_slot": ks, "dt_bucket": dtb, "period": per + 1, "clock_bucket": cb, "margin": margin - NT.MARGIN0 - NT.MARGIN_CAP,
              "pts_side": side, "pts": pn, "stop": NT.STOPS[sv]}
        if names is not None:
            ev["kind"] = names.kind_names(base, ks)
        events.append(ev)
    out: dict[str, Any] = {"league": league, "home_slot": slots[0], "away_slot": slots[1], "events": events}
    if names is not None:
        out["home"], out["away"] = names.team_names(base, slots[0]), names.team_names(base, slots[1])
    return out


def retokenise(game: Mapping[str, Any]) -> np.ndarray:
    """The inverse of detokenise_game: the exact token stream a decoded game came from."""
    from redzone.models import League
    league = game["league"]
    b = NT.BASES.index(League(league).base.value)
    out = [NT.BOS0 + NT.LEAGUES.index(league), NT.TEAM0 + b * NT.TEAM_SLOTS + int(game["home_slot"]),
           NT.TEAM0 + b * NT.TEAM_SLOTS + int(game["away_slot"])]
    for ev in game["events"]:
        pts = 0 if not ev["pts"] else (ev["pts"] if ev["pts_side"] == "home" else 8 + ev["pts"])
        out += [NT.KIND0 + b * NT.KIND_SLOTS + int(ev["kind_slot"]),
                NT.UNK_DT if ev["dt_bucket"] is None else NT.DT0 + int(ev["dt_bucket"]),
                NT.CLOCK0 + (int(ev["period"]) - 1) * NT.CLOCK_BUCKETS + int(ev["clock_bucket"]),
                NT.MARGIN0 + NT.MARGIN_CAP + int(ev["margin"]), NT.PTS0 + pts, NT.STOP0 + NT.STOPS.index(ev["stop"])]
    out.append(NT.EOS)
    return np.asarray(out, np.uint16)


def kind_slot_of(name: str) -> int:
    """The slot a kind name hashes to (the same crc32 rule as scripts/noggin_tokens)."""
    return zlib.crc32(name.encode()) % NT.KIND_SLOTS
