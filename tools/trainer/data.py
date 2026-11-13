"""Manifest-pinned token corpus for the trainer: shards, fence checks, walk-forward split, (seed, epoch) order.

Why: WORKPLAN B-03 ("manifest-pinned data loader", "refuse fenced nights") and TRAINING-PLAN section 2 "Resume
design" (data order is a function of (seed, epoch)) and rule 8 (tuning and locked nights fenced in code).

Shards are the format of scripts/noggin_tokens.py: `<league>/<year>.npy` (uint16 tokens, games back to back)
next to `<year>.json` with `index` rows `[gid, night, offset, length, ...]` and `vocab_version`. Both files must
be pinned in the same manifest (redzone/lab/datamanifest.py); an unpinned index is refused.

    corpus = load_pinned_corpus("corpus-v1", root=DATA)        # re-hashes every file, refuses fenced manifests
    train, held = split_walk_forward(corpus, train_through="20250630", valid_from="20251001")
    ws = WindowSet(train, ctx=4096)                            # one game per window, never two games mixed
    ids, cur = take(Cursor(), 8, len(ws), seed=1)              # resumable: the cursor is two integers

A season shard has no single night, so the manifest marks it fence 'unknown'; this module then checks every
game's own night against the fence (stricter than the manifest-level check) and refuses the whole corpus when
any training or evaluation game sits in a fenced night. Numpy only (datamanifest and fence are stdlib).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TRAINER_REPO = Path(__file__).resolve().parents[2]
TOY_VOCAB_VERSION = "toy-1"
TOY_VOCAB_SIZE = 56          # PAD 0, EOS 1, BOS 2, home 3-4, away 5-6, six fields x 8 ids from 8
TOY_FIELD_BASE, TOY_FIELD_IDS = 8, 8
TOY_SOURCE = "synthetic"


class CorpusError(ValueError):
    """The token shards are malformed, unpinned, or do not match the vocabulary the run expects."""


def repo_module(name: str):
    """Import redzone.<...> from this checkout (the job may run from a bundle where it is not installed)."""
    if str(TRAINER_REPO) not in sys.path:
        sys.path.insert(0, str(TRAINER_REPO))
    return importlib.import_module(name)


def vocab_v1() -> dict:
    """The vocab v1 layout from scripts/noggin_tokens.py (the one tokenizer; loaded by path, scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("_rz_noggin_tokens", TRAINER_REPO / "scripts" / "noggin_tokens.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"vocab_size": int(mod.VOCAB_SIZE), "vocab_version": mod.VOCAB_VERSION, "pad": int(mod.PAD),
            "eos": int(mod.EOS), "tokens_per_event": int(mod.TOKENS_PER_EVENT), "header": 3}


@dataclass(frozen=True)
class Doc:
    league: str
    gid: str
    night: str
    tokens: np.ndarray            # 1-D integer tokens of one game: BOS HOME AWAY (6 per event)... EOS


@dataclass
class Corpus:
    docs: list
    tag: str
    manifest_sha256: str
    vocab_size: int
    vocab_version: object

    @property
    def nights(self) -> list[str]:
        return sorted({d.night for d in self.docs})


def _fence():
    return repo_module("redzone.lab.fence")


def read_shards(npy_paths: list[Path], pinned_paths: set[Path], vocab_size: int, vocab_version) -> list[Doc]:
    """Every game of every shard, checked: index pinned, vocab version, bounds, ids below vocab_size."""
    docs: list[Doc] = []
    for p in sorted(npy_paths):
        idx_path = p.with_suffix(".json")
        if idx_path not in pinned_paths:
            raise CorpusError(f"{p.name}: its index {idx_path.name} is not pinned in the manifest")
        info = json.loads(idx_path.read_text())
        if info.get("vocab_version") != vocab_version:
            raise CorpusError(f"{p.name}: vocab_version {info.get('vocab_version')!r} != expected {vocab_version!r}")
        arr = np.load(p, allow_pickle=False)
        if arr.ndim != 1 or arr.dtype.kind not in "ui":
            raise CorpusError(f"{p.name}: tokens must be a 1-D integer array")
        if "tokens" in info and int(info["tokens"]) != len(arr):
            raise CorpusError(f"{p.name}: {len(arr)} tokens on disk, index says {info['tokens']}")
        league = str(info.get("league") or p.parent.name)
        for row in info.get("index") or []:
            gid, night, off, length = str(row[0]), str(row[1]), int(row[2]), int(row[3])
            if off < 0 or length < 2 or off + length > len(arr):
                raise CorpusError(f"{p.name}: game {gid} slice {off}+{length} is outside the shard")
            t = arr[off: off + length]
            if int(t.max()) >= vocab_size:
                raise CorpusError(f"{p.name}: game {gid} has token id {int(t.max())} >= vocab_size {vocab_size}")
            docs.append(Doc(league, gid, _fence().night_key(night), t.astype(np.int64)))
    if not docs:
        raise CorpusError("no games in the pinned shards")
    return docs


def load_corpus(pinned, vocab_size: int, vocab_version) -> Corpus:
    """A Corpus from a datamanifest.PinnedData (already verified): its .npy shards and their pinned .json indexes."""
    all_paths = {Path(x) for x in pinned.paths()}
    npy = [Path(x) for x in pinned.paths(suffix=".npy")]
    if not npy:
        raise CorpusError(f"{pinned.tag}: the manifest pins no .npy token shards")
    docs = read_shards(npy, all_paths, vocab_size, vocab_version)
    return Corpus(docs=docs, tag=pinned.tag, manifest_sha256=pinned.manifest_sha256, vocab_size=vocab_size,
                  vocab_version=vocab_version)


def load_pinned_corpus(tag_or_path, *, root=None, manifests_dir=None, vocab_size: int | None = None,
                       vocab_version=None) -> Corpus:
    """datamanifest.load_pinned(for_training=True) then load_corpus. Every file is re-hashed; a manifest holding a
    fenced night is refused there, and season shards (night unknown) are checked game by game in split_walk_forward."""
    dm = repo_module("redzone.lab.datamanifest")
    pinned = dm.load_pinned(tag_or_path, root=root, manifests_dir=manifests_dir, for_training=True,
                            allow_unknown_nights=True)
    if vocab_size is None or vocab_version is None:
        v = vocab_v1()
        vocab_size = v["vocab_size"] if vocab_size is None else vocab_size
        vocab_version = v["vocab_version"] if vocab_version is None else vocab_version
    return load_corpus(pinned, vocab_size, vocab_version)


def split_walk_forward(corpus: Corpus, train_through, valid_from, valid_through=None) -> tuple[list, list]:
    """(train docs, held-out docs) by whole night: train <= train_through < valid_from <= held-out <= valid_through.
    Raises fence.FenceError when any game on either side sits in a fenced (locked or tuning) night."""
    f = _fence()
    tt, vf = f.night_key(train_through), f.night_key(valid_from)
    vt = f.night_key(valid_through) if valid_through not in (None, "") else None
    if not tt < vf:
        raise ValueError(f"train_through {tt} must be before valid_from {vf} (walk-forward)")
    train = [d for d in corpus.docs if d.night <= tt]
    held = [d for d in corpus.docs if d.night >= vf and (vt is None or d.night <= vt)]
    if not train:
        raise CorpusError(f"no training games on or before {tt}")
    f.assert_train_nights([d.night for d in train])
    bad = f.train_problems([d.night for d in held])
    if bad:
        raise f.FenceError("REFUSED evaluation set: " + "; ".join(bad))
    return train, held


# --- windows and data order -------------------------------------------------------------------------------------

class WindowSet:
    """Fixed-length training windows, one game per window (no packing: a window never mixes two games, so no game
    attends to another game's tokens). Window i is (doc, start); x = tokens[start:start+ctx], y shifted by one."""

    def __init__(self, docs: list, ctx: int, pad: int = 0, tokens_per_event: int = 6, header: int = 3):
        if ctx < 2:
            raise ValueError("ctx must be at least 2")
        self.docs, self.ctx, self.pad, self.tpe, self.header = docs, ctx, pad, tokens_per_event, header
        w = [(i, s) for i, d in enumerate(docs) for s in range(0, max(1, len(d.tokens) - 1), ctx)]
        self.windows = np.asarray(w, dtype=np.int64).reshape(-1, 2)

    def __len__(self) -> int:
        return len(self.windows)

    def batch(self, ids) -> dict:
        """x, y, mask (float64, 1 where y is a real target) and field (0..5 for event fields, -1 otherwise)."""
        ids = np.asarray(ids, dtype=np.int64)
        B, T = len(ids), self.ctx
        x = np.full((B, T), self.pad, np.int64)
        y = np.full((B, T), self.pad, np.int64)
        mask = np.zeros((B, T), np.float64)
        field = np.full((B, T), -1, np.int64)
        for r, wi in enumerate(ids):
            di, s = self.windows[wi]
            t = self.docs[di].tokens
            L = len(t)
            n = min(T, L - 1 - s)
            x[r, :n] = t[s: s + n]
            y[r, :n] = t[s + 1: s + 1 + n]
            mask[r, :n] = 1.0
            p = np.arange(s + 1, s + 1 + n)            # document position of each target
            ev = (p >= self.header) & (p < L - 1)
            field[r, :n] = np.where(ev, (p - self.header) % self.tpe, -1)
        return {"x": x, "y": y, "mask": mask, "field": field}


def epoch_order(seed: int, epoch: int, n: int) -> np.ndarray:
    """The window order of one epoch: a pure function of (seed, epoch), so a resumed run replays it exactly."""
    return np.random.default_rng([int(seed), int(epoch)]).permutation(n)


@dataclass(frozen=True)
class Cursor:
    epoch: int = 0
    pos: int = 0


def take(cur: Cursor, k: int, n: int, seed: int) -> tuple[np.ndarray, Cursor]:
    """The next k window ids from cursor `cur` (rolling into the next epoch), and the cursor after them."""
    if n <= 0:
        raise ValueError("no windows")
    out: list[np.ndarray] = []
    e, p = cur.epoch, cur.pos
    need = k
    while need:
        order = epoch_order(seed, e, n)
        got = order[p: p + need]
        out.append(got)
        need -= len(got)
        p += len(got)
        if p >= n:
            e, p = e + 1, 0
    return np.concatenate(out), Cursor(e, p)


# --- a synthetic corpus for tests and the smoke job -----------------------------------------------------------------

def toy_game(rng: np.random.Generator, n_events: int) -> np.ndarray:
    """One synthetic game in the noggin layout with the toy vocabulary: a Markov chain over 'kinds' where every
    later field depends on the kind and the previous event, so a model can learn real structure."""
    home, away = int(rng.integers(2)), int(rng.integers(2))
    out = [2, 3 + home, 5 + away]
    kind = int(rng.integers(TOY_FIELD_IDS))
    clock = 0
    for _ in range(n_events):
        kind = (kind + 1 + int(rng.integers(2))) % TOY_FIELD_IDS          # next kind: +1 or +2
        dt = (kind * 3 + int(rng.integers(2))) % TOY_FIELD_IDS
        clock = min(TOY_FIELD_IDS - 1, clock + int(rng.integers(2)))
        margin = (kind + clock) % TOY_FIELD_IDS
        pts = kind % 4
        stop = (pts + dt) % TOY_FIELD_IDS
        vals = (kind, dt, clock, margin, pts, stop)
        out += [TOY_FIELD_BASE + f * TOY_FIELD_IDS + v for f, v in enumerate(vals)]
    out.append(1)
    return np.asarray(out, np.uint16)


def write_toy_corpus(root, *, tag: str = "toy-corpus-v1", nights=None, games_per_night: int = 2,
                     events: tuple = (6, 14), seed: int = 0, league: str = "nba", season: int = 2024) -> Path:
    """Write a synthetic shard + index under root/tokens/<league>/ and a real data manifest pinning both
    (redzone/lab/datamanifest format, kind 'history', source 'synthetic'). Returns the manifest path."""
    dm = repo_module("redzone.lab.datamanifest")
    root = Path(root)
    d = root / "tokens" / league
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    nights = list(nights) if nights is not None else [f"2024{m:02d}{dd:02d}" for m in (1, 2, 3) for dd in range(1, 11)]
    parts, index, off = [], [], 0
    for night in nights:
        for g in range(games_per_night):
            t = toy_game(rng, int(rng.integers(events[0], events[1] + 1)))
            index.append([f"{night}{g:02d}", night, off, int(len(t)), 0])
            parts.append(t)
            off += len(t)
    arr = np.concatenate(parts).astype(np.uint16)
    np.save(d / f"{season}.npy", arr)
    (d / f"{season}.json").write_text(json.dumps({"league": league, "season": season, "tokens": int(len(arr)),
                                                  "games": len(index), "vocab_version": TOY_VOCAB_VERSION,
                                                  "index": index}, sort_keys=True))
    files = []
    for name in (f"{season}.json", f"{season}.npy"):
        p = d / name
        files.append({"bytes": p.stat().st_size, "fence": dm.fence_status(None), "league": league,
                      "license": TOY_SOURCE, "night": None, "path": f"tokens/{league}/{name}", "rows": None,
                      "sha256": dm._digest(str(p), False)[0], "sources": [TOY_SOURCE]})
    files.sort(key=lambda f: f["path"])
    doc = {"format": dm.MANIFEST_FORMAT, "tag": tag, "kind": "history", "private": False, "license": TOY_SOURCE,
           "licences": {TOY_SOURCE: {"licence": "synthetic test data", "status": "ok", "tag": TOY_SOURCE,
                                     "terms_class": "open"}},
           "selection": {"end": None, "leagues": None, "start": None}, "notes": [], **dm._derive(files), "files": files}
    doc["manifest_sha256"] = dm.manifest_hash(doc)
    problems = dm.check_manifest(doc)
    if problems:
        raise CorpusError("toy manifest does not check: " + "; ".join(problems))
    out = root / f"{tag}.manifest.json"
    out.write_text(dm.render(doc))
    return out
