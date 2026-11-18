"""The corpus shard file: one league-season-tier-role of tokenised games in one self-describing file, written atomically and byte-for-byte
the same for the same games.

Why: B-02 needs a shard a trainer can memory-map and a verifier can re-hash and cross-check (WORKPLAN B-02: "manifest and hash per shard",
"same input gives byte-identical shards"). One file holds both the games index and the tokens, so the two can never disagree on disk.

Layout (all integers little endian):
    8 bytes   magic  RZSHARD1
    4 bytes   header length H (uint32), a multiple of 8 once the 12 prefix bytes are counted
    H bytes   header: canonical JSON (sorted keys, no spaces), padded with spaces
    rest      the tokens, uint16, games back to back; each game is BOS home away [six tokens per event] EOS

Header keys: format, vocab {version, size, sha256, tokens_per_event}, league, season, era, tier (wall or nowall), role (train, valid,
holdout or tuning), fence (clear or tuning), train_ok, providers, license, games, events, tokens, coverage, and `index`: one row per game
[gid, provider, origin, night, offset, length, flags, sha16] ordered by night then provider then id. offset and length count tokens
from the start of the token body; sha16 is the first 16 hex digits of the sha256 of that game's token bytes.
Reading memory-maps the body; nothing is copied.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from redzone.lab.corpus import tokenise as tk
from scripts import noggin_tokens as NT
from scripts.history.store import atomic_write

CORPUS_SHARD_MAGIC = b"RZSHARD1"
CORPUS_SHARD_FORMAT = "redzone-corpus-shard/1"
CORPUS_SHARD_EXT = ".rzs"
CORPUS_INDEX_FIELDS = ("gid", "provider", "origin", "night", "offset", "length", "flags", "sha16")
CORPUS_HEADER_MAX = 1 << 28


class ShardError(ValueError):
    """A file that is not a corpus shard, or one whose header and body disagree."""


def game_sha16(tokens: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(tokens, dtype="<u2").tobytes()).hexdigest()[:16]


def encode_shard(header: Mapping[str, Any], body: np.ndarray) -> bytes:
    """The exact bytes of a shard. `header["tokens"]` must equal len(body)."""
    if int(header.get("tokens", -1)) != len(body):
        raise ShardError(f"header says {header.get('tokens')} tokens, body has {len(body)}")
    raw = json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    pad = -(12 + len(raw)) % 8
    raw += b" " * pad
    return CORPUS_SHARD_MAGIC + struct.pack("<I", len(raw)) + raw + np.ascontiguousarray(body, dtype="<u2").tobytes()


def write_shard(path: Path, header: Mapping[str, Any], body: np.ndarray) -> tuple[int, str]:
    """Write atomically; returns (bytes, sha256)."""
    data = encode_shard(header, body)
    atomic_write(Path(path), data)
    return len(data), hashlib.sha256(data).hexdigest()


@dataclass
class Shard:
    path: Path
    header: dict
    tokens: np.ndarray                 # uint16, read-only memory map of the body

    @property
    def games(self) -> int:
        return len(self.header["index"])

    def row(self, i: int) -> dict[str, Any]:
        return dict(zip(CORPUS_INDEX_FIELDS, self.header["index"][i]))

    def game(self, i: int) -> np.ndarray:
        r = self.row(i)
        return self.tokens[r["offset"]:r["offset"] + r["length"]]

    def iter_games(self) -> Iterator[tuple[dict[str, Any], np.ndarray]]:
        for i in range(self.games):
            yield self.row(i), self.game(i)


def read_header(path: Path) -> tuple[dict, int]:
    """(header, byte offset of the token body)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:8] != CORPUS_SHARD_MAGIC:
            raise ShardError("not a corpus shard (bad magic)")
        (n,) = struct.unpack("<I", head[8:])
        if n > CORPUS_HEADER_MAX or (12 + n) % 8:
            raise ShardError("bad header length")
        raw = f.read(n)
    if len(raw) != n:
        raise ShardError("truncated header")
    try:
        header = json.loads(raw.decode("ascii"))
    except ValueError as e:
        raise ShardError("header is not JSON") from e
    if not isinstance(header, dict) or header.get("format") != CORPUS_SHARD_FORMAT:
        raise ShardError(f"header format is not {CORPUS_SHARD_FORMAT}")
    return header, 12 + n


def read_shard(path: Path) -> Shard:
    path = Path(path)
    header, off = read_header(path)
    n = int(header.get("tokens", -1))
    size = os.path.getsize(path)
    if n < 0 or size != off + 2 * n:
        raise ShardError(f"file is {size} bytes, header says {off} + 2 x {n}")
    body = np.memmap(path, dtype="<u2", mode="r", offset=off, shape=(n,)) if n else np.zeros(0, "<u2")
    return Shard(path, header, body)


def shard_problems(shard: Shard) -> list[str]:
    """Every way the shard disagrees with itself; [] when sound. Checks the games index against the body (offsets contiguous, lengths
    right, BOS and EOS in place, every id inside vocab v1, game hashes), the vocab stamp and the counters that follow from tokens."""
    h, t = shard.header, shard.tokens
    out: list[str] = []
    v = h.get("vocab") or {}
    if v.get("sha256") != tk.vocab_fingerprint() or v.get("size") != NT.VOCAB_SIZE or v.get("version") != NT.VOCAB_VERSION:
        out.append("vocab stamp does not match the vocab v1 layout in this checkout (the token ids may mean something else)")
    index = h.get("index")
    if not isinstance(index, list) or any(not isinstance(r, list) or len(r) != len(CORPUS_INDEX_FIELDS) for r in index):
        return out + ["games index is malformed"]
    if len(t) and int(t.max()) >= NT.VOCAB_SIZE:
        out.append(f"token id {int(t.max())} is outside vocab v1 (size {NT.VOCAB_SIZE})")
    expect, starts, ev, sound = 0, [], 0, True
    for i in range(len(index)):
        r = shard.row(i)
        if r["offset"] != expect or r["length"] < 4 or (r["length"] - 4) % NT.TOKENS_PER_EVENT:
            out.append(f"game {i} ({r['gid']}): offset or length is wrong")
            sound = False
            break
        g = t[r["offset"]:r["offset"] + r["length"]]
        if len(g) != r["length"] or not NT.BOS0 <= int(g[0]) < NT.BOS0 + len(NT.LEAGUES) or int(g[-1]) != NT.EOS:
            out.append(f"game {i} ({r['gid']}): missing BOS or EOS")
            sound = False
            break
        if NT.LEAGUES[int(g[0]) - NT.BOS0] != h.get("league"):
            out.append(f"game {i} ({r['gid']}): BOS says {NT.LEAGUES[int(g[0]) - NT.BOS0]}, shard is {h.get('league')}")
        if game_sha16(g) != r["sha16"]:
            out.append(f"game {i} ({r['gid']}): token hash differs from the index")
        starts.append(r["offset"])
        ev += (r["length"] - 4) // NT.TOKENS_PER_EVENT
        expect += r["length"]
    if not sound:
        return out
    if expect != len(t):
        out.append(f"index covers {expect} tokens, body has {len(t)}")
        return out
    if ev != h.get("events"):
        out.append(f"header says {h.get('events')} events, tokens hold {ev}")
    if len(index) != h.get("games"):
        out.append(f"header says {h.get('games')} games, index has {len(index)}")
    cov = h.get("coverage") or {}
    for k, val in tk.counters_from_tokens(t, starts).items():
        if cov.get(k) != val:
            out.append(f"coverage {k}: header says {cov.get(k)}, tokens say {val}")
    return out
