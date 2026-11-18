"""Load a corpus for training or evaluation: verify it against its manifest first, then hand out shards by role, and refuse a shard that is
not allowed to train.

Why: B-02 done-when 2 and F-09 (WORKPLAN): "a fenced night in the input never reaches a training shard" has to hold on the reading side as
well as the writing side. `load_corpus` re-hashes every file (like datamanifest.load_pinned) and re-runs the leakage proofs; `shards(...,
for_training=True)` then refuses any `tuning` shard and re-asserts the fence on every game night it is about to return. `train_stamp` gives the
three fields a model record needs (training cutoff, train-night digest, data manifest hash; F-02 registry, F-09 fence).

    c = load_corpus("data/corpus/corpus-v1/corpus-v1.manifest.json")
    for row, tokens in c.games(roles=("train",), leagues=("nba",)):      # tokens: uint16 array, BOS home away ... EOS
        ...
    stamp = c.train_stamp(("train",))      # {"training_cutoff": "2024-03-01", "train_nights_sha256": "...", "data_manifest_hash": "..."}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from redzone.lab import datamanifest as dm
from redzone.lab import fence
from redzone.lab.corpus import manifest, shardio
from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.records import CORPUS_ROLES


@dataclass
class Corpus:
    doc: dict
    root: Path

    def rows(self, roles: Iterable[str] = ("train",), leagues: Iterable[str] | None = None, tiers: Iterable[str] | None = None) -> list[dict]:
        roles, lg, tr = set(roles), set(leagues) if leagues else None, set(tiers) if tiers else None
        bad = roles - set(CORPUS_ROLES)
        if bad:
            raise ValueError(f"unknown role(s) {sorted(bad)}; known: {list(CORPUS_ROLES)}")
        return [r for r in self.doc["corpus"]["shards"] if r["role"] in roles and (not lg or r["league"] in lg) and (not tr or r["tier"] in tr)]

    def shards(self, roles: Iterable[str] = ("train",), leagues: Iterable[str] | None = None, tiers: Iterable[str] | None = None,
               for_training: bool = True) -> Iterator[shardio.Shard]:
        """The shards of those roles. for_training=True refuses a tuning shard and any shard with a fenced night, before reading a token."""
        rows = self.rows(roles, leagues, tiers)
        if for_training:
            held = [r["path"] for r in rows if not r["train_ok"]]
            if held:
                raise fence.FenceError(f"REFUSED {self.doc['tag']} for training: {len(held)} shard(s) are tuning-window data, flagged train_ok=false "
                                       f"({held[0]}...); pick roles from {list(CORPUS_ROLES[:3])}")
        for r in rows:
            sh = shardio.read_shard(self.root.joinpath(*r["path"].split("/")))
            if for_training:
                fence.assert_train_nights({x[3] for x in sh.header["index"]})
            yield sh

    def games(self, roles: Iterable[str] = ("train",), leagues: Iterable[str] | None = None, tiers: Iterable[str] | None = None,
              for_training: bool = True) -> Iterator[tuple[dict[str, Any], np.ndarray]]:
        for sh in self.shards(roles, leagues, tiers, for_training):
            for row, toks in sh.iter_games():
                yield {**row, "league": sh.header["league"], "season": sh.header["season"], "tier": sh.header["tier"],
                       "role": sh.header["role"]}, toks

    def names(self) -> tk.NameTables:
        return tk.NameTables.from_json(json.loads((self.root / manifest.CORPUS_NAMES_FILE).read_text()))

    def train_stamp(self, roles: Iterable[str] = ("train",), leagues: Iterable[str] | None = None) -> dict[str, Any]:
        """The model-record fields for a run trained on these roles: its training cutoff (the last night it saw), the sorted nights and
        their digest (what fence.guard_model checks), and the manifest hash that pins the data."""
        nights = sorted({x[3] for sh in self.shards(roles, leagues) for x in sh.header["index"]})
        if not nights:
            raise ValueError("no games in that selection")
        n = fence.night_key(nights[-1])
        return {"training_cutoff": f"{n[:4]}-{n[4:6]}-{n[6:]}", "train_nights": nights, "train_nights_sha256": fence.nights_sha256(nights),
                "data_manifest_hash": self.doc["manifest_sha256"]}


def load_corpus(manifest_path: str | Path, root: str | Path | None = None, verify: bool = True) -> Corpus:
    """The corpus a manifest pins. Unless verify=False it re-hashes every file and re-runs the leakage proofs first and raises
    ManifestError listing what failed. `root` defaults to the manifest's own folder."""
    path = Path(manifest_path)
    doc = dm.read_manifest(path)
    base = Path(root) if root else path.parent
    problems = manifest.verify_corpus(doc, base) if verify else manifest.check_corpus_manifest(doc)
    if problems:
        more = f" (+{len(problems) - manifest.CORPUS_SHOWN} more)" if len(problems) > manifest.CORPUS_SHOWN else ""
        raise dm.ManifestError(f"REFUSED {doc.get('tag', path.name)}: the corpus does not match its manifest: "
                               + "; ".join(problems[:manifest.CORPUS_SHOWN]) + more)
    return Corpus(doc, base)
