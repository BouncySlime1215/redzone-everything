"""The corpus build: read the local history folder and archive, keep fenced nights out, drop duplicate games, assign walk-forward roles,
tokenise with vocab v1 and write hashed shards with a manifest.

Why: B-02 (WORKPLAN): "tokenise with vocab v1 into shards; manifest and hash per shard; league, era, provider tags; dedup across providers;
walk-forward splits; fence". One command (scripts/corpus_build.py build) does all of it, deterministically: the same input folders and the same
options give the same shard bytes and the same manifest.

Two passes over the sources, so memory stays at one league-season:
  pass 1  read every game that the fence admits, keep only its Light record (identity, night, play count, wall-clock share, score
          signature); drop games under 10 events; dedup across providers and folders; plan the walk-forward cuts from the surviving clear nights
  pass 2  read the games again, tokenise only the survivors, group them by (league, season, tier, role), write one shard per group when the
          league-season is complete; write the name table, the dedup list and the stats; assemble and write the manifest

Fence modes: `exclude` (default) drops locked, tuning, fresh and undated nights. `hold` also tokenises tuning-window nights into `tuning`
shards flagged train_ok=false (for evaluating on tuning nights); locked, fresh and undated nights are still dropped. Nothing is written
into the source folders. The output folder must be git-ignored (datamanifest.assert_not_committable) and is replaced only if empty or
if it already holds the identical manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from redzone.lab import datamanifest as dm
from redzone.lab import evaldata, fence
from redzone.lab.corpus import dedup, manifest, shardio, stats, walkforward
from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.records import CORPUS_MIN_EVENTS, CORPUS_TRAIN_ROLES, Light, era_of, light_of, tier_of
from redzone.lab.corpus.sources import ArchiveSource, FenceGate, HistorySource, SourceUnit
from scripts.history.leagues import EVAL_FIRST_NIGHT as CORPUS_EVAL_NIGHTS

CORPUS_FENCE_MODES = ("exclude", "hold")


@dataclass
class BuildConfig:
    tag: str
    out: Path
    history_root: Path | None = None
    archive_root: Path | None = None
    leagues: set[str] | None = None
    seasons: set[int] | None = None
    archive_nights: set[str] | None = None
    fenced: str = "exclude"
    valid_frac: float = walkforward.CORPUS_VALID_FRAC
    holdout_frac: float = walkforward.CORPUS_HOLDOUT_FRAC
    gap_days: int = 0
    min_events: int = CORPUS_MIN_EVENTS


@dataclass
class _Entry:
    night: str
    provider: str
    origin: str
    gid: str
    tokens: np.ndarray = field(repr=False)
    flags: int = 0
    counters: dict[str, int] = field(default_factory=dict)


def _units(cfg: BuildConfig) -> list[tuple[Any, SourceUnit]]:
    srcs: list[Any] = []
    if cfg.history_root:
        srcs.append(HistorySource(cfg.history_root))
    if cfg.archive_root:
        srcs.append(ArchiveSource(cfg.archive_root, cfg.archive_nights))
    if not srcs:
        raise dm.ManifestError("give a history root, an archive root, or both")
    out = [(s, u) for s in srcs for u in s.units(cfg.leagues, cfg.seasons)]
    return sorted(out, key=lambda su: (su[1].league, su[1].season, su[1].provider, su[1].origin, su[1].path))


def _shard_name(league: str, season: int, tier: str, role: str) -> str:
    return f"shards/{league}/{season}-{tier}-{role}{shardio.CORPUS_SHARD_EXT}"


def build_corpus(cfg: BuildConfig, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Build the corpus under cfg.out and return its manifest document. Builds in a sibling `.building` folder and moves it into place only
    when the finished corpus verifies, so a failed or refused build leaves nothing behind."""
    dm.check_tag(cfg.tag)
    if cfg.fenced not in CORPUS_FENCE_MODES:
        raise dm.ManifestError(f"fenced must be one of {CORPUS_FENCE_MODES}")
    out = Path(cfg.out)
    dm.assert_not_committable(out, directory=True)
    tmp = out.with_name(out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        return _build(cfg, out, tmp, log)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)         # already moved after a successful publish


def _build(cfg: BuildConfig, out: Path, tmp: Path, log: Callable[[str], None]) -> dict[str, Any]:
    units = _units(cfg)
    log(f"{len(units)} source units")

    # ---- pass 1: inventory -------------------------------------------------------------------------------------------
    gate = FenceGate(hold_tuning=cfg.fenced == "hold")
    lights: list[Light] = []
    short: Counter[str] = Counter()
    for src, unit in units:
        n = 0
        for rec in src.games(unit, gate):
            if len(rec.plays) < cfg.min_events:
                short[rec.league] += 1
                continue
            lights.append(light_of(rec))
            n += 1
        log(f"  pass 1 {unit.name} {unit.league} {unit.season}: {n} games")
    res = dedup.resolve(lights)
    keep = res.keep
    nights: dict[str, set[str]] = {}
    for g in keep.values():
        if g.klass == "clear" and g.night:
            nights.setdefault(g.league, set()).add(g.night)
    plan = walkforward.plan(nights, cfg.valid_frac, cfg.holdout_frac, cfg.gap_days)
    log(f"{len(lights)} games in, {len(keep)} after dedup ({len(res.pairs)} copies removed)")

    # ---- pass 2: tokenise and write ----------------------------------------------------------------------------------
    silent = FenceGate(hold_tuning=cfg.fenced == "hold", count=False)
    tmp.mkdir(parents=True)
    names = tk.NameTables()
    rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    embargo: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    notes: list[str] = []
    groups: dict[tuple, list[_Entry]] = {}
    providers_seen: set[str] = set()
    block: dict[str, dict] = {}

    def flush(league: str, season: int) -> None:
        for key in sorted(k for k in groups if k[0] == league and k[1] == season):
            entries = groups.pop(key)
            row, frow = _write_shard(tmp, key, entries, block)
            rows.append(row)
            file_rows.append(frow)
            log(f"  wrote {row['path']}: {row['games']} games, {row['events']} events")

    current: tuple[str, int] | None = None
    block.update(manifest.licence_block(sorted({u.provider for _, u in units})))
    for src, unit in units:
        if current is not None and current != (unit.league, unit.season):
            flush(*current)
        current = (unit.league, unit.season)
        reads: dict[str, str] = {}
        for rec in src.games(unit, silent, reads):
            light = keep.get((rec.league, rec.provider, rec.origin, rec.gid))
            if light is None or len(rec.plays) < cfg.min_events:
                continue
            role = "tuning" if light.klass == "tuning" else plan[rec.league].role_of(rec.night)
            if role is None:
                embargo[rec.league] += 1
                continue
            try:
                tok = tk.tokenise_game(rec, names)
            except (KeyError, TypeError, ValueError, AttributeError, IndexError) as e:
                errors[rec.league] += 1
                if len(notes) < 5:
                    notes.append(f"tokenise failed for {rec.league} game {rec.gid} ({type(e).__name__})")
                continue
            groups.setdefault((rec.league, unit.season, tier_of(light.wall_frac), role), []).append(
                _Entry(rec.night, rec.provider, rec.origin, rec.gid, tok.tokens, tok.flags, tok.counters))
            providers_seen.add(rec.provider)
        inputs.append({"provider": unit.provider, "origin": unit.origin, "league": unit.league, "season": unit.season, "path": unit.path,
                       "files": len(reads), "bytes": sum(os.path.getsize(os.path.join(unit.root, *p.split("/"))) for p in reads),
                       "tree_sha256": evaldata.tree_digest(reads)})
    if current is not None:
        flush(*current)

    # ---- sidecars, stats, manifest -----------------------------------------------------------------------------------
    srcs = sorted(providers_seen)
    if not rows:
        raise dm.ManifestError("no game survived the fence, dedup and length rules: nothing to write (see the counts above)")
    sidecar = _write_sidecars(tmp, names, res.pairs, srcs, block)
    pseudo = {"tag": cfg.tag, "files": file_rows + sidecar, "corpus": {"shards": rows}}
    st = stats.compute(pseudo, tmp)
    st_bytes = (json.dumps(st, indent=1, sort_keys=True) + "\n").encode()
    (tmp / manifest.CORPUS_STATS_FILE).write_bytes(st_bytes)
    sidecar.append(_sidecar_entry(tmp, manifest.CORPUS_STATS_FILE, None, srcs, block))
    corpus = {
        "vocab": {"version": tk.NT.VOCAB_VERSION, "size": tk.NT.VOCAB_SIZE, "ids_per_event": tk.NT.TOKENS_PER_EVENT,
                  "sha256": tk.vocab_fingerprint()},
        "shards": rows,
        "splits": _split_section(plan, keep, rows),
        "dedup": {**res.summary(len(lights)), "pairs_file": manifest.CORPUS_DEDUP_FILE},
        "excluded": {"fence": gate.tally(), "short_games": dict(sorted(short.items())), "embargo_games": dict(sorted(embargo.items())),
                     "encode_errors": dict(sorted(errors.items()))},
        "inputs": inputs,
        "params": {"fenced": cfg.fenced, "gap_days": cfg.gap_days, "holdout_frac": cfg.holdout_frac, "min_events": cfg.min_events,
                   "valid_frac": cfg.valid_frac},
        "fence": {"mode": cfg.fenced, "windows": {w.name: [w.start, w.end] for w in fence.FENCES},
                  "eval_first_night": {lg: CORPUS_EVAL_NIGHTS[lg] for lg in sorted({r["league"] for r in rows})}},
    }
    selection = {"leagues": sorted(cfg.leagues) if cfg.leagues else None, "seasons": sorted(cfg.seasons) if cfg.seasons else None,
                 "archive_nights": sorted(cfg.archive_nights) if cfg.archive_nights else None, "fenced": cfg.fenced}
    doc = manifest.assemble(cfg.tag, file_rows + sidecar, corpus, selection=selection, notes=notes)
    probs = manifest.verify_corpus(doc, tmp)
    if probs:
        raise dm.ManifestError("the corpus just built does not verify: " + "; ".join(probs[:5]))
    dm.write_manifest(doc, tmp)
    _publish(tmp, out, cfg.tag, doc)
    return doc


def _write_shard(root: Path, key: tuple, entries: list[_Entry], block: dict) -> tuple[dict, dict]:
    league, season, tier, role = key
    entries.sort(key=lambda e: (e.night, e.provider, e.gid, e.origin))
    index, off, cov = [], 0, {}
    for e in entries:
        index.append([e.gid, e.provider, e.origin, e.night, off, len(e.tokens), e.flags, shardio.game_sha16(e.tokens)])
        off += len(e.tokens)
        tk.add_counters(cov, e.counters)
    body = np.concatenate([e.tokens for e in entries])
    providers = sorted({e.provider for e in entries})
    train_ok = role in CORPUS_TRAIN_ROLES
    header = {
        "format": shardio.CORPUS_SHARD_FORMAT,
        "vocab": {"version": tk.NT.VOCAB_VERSION, "size": tk.NT.VOCAB_SIZE, "sha256": tk.vocab_fingerprint(), "tokens_per_event": tk.NT.TOKENS_PER_EVENT},
        "league": league, "season": season, "era": era_of(season), "tier": tier, "role": role, "fence": "clear" if train_ok else "tuning",
        "train_ok": train_ok, "providers": providers, "license": manifest.file_license(providers, block), "games": len(entries),
        "events": cov.get("events", 0), "tokens": int(len(body)), "coverage": cov, "index": index}
    rel = _shard_name(league, season, tier, role)
    size, sha = shardio.write_shard(root.joinpath(*rel.split("/")), header, body)
    nights = [entries[0].night, entries[-1].night]
    row = {"path": rel, "league": league, "season": season, "era": header["era"], "tier": tier, "role": role, "fence": header["fence"],
           "train_ok": train_ok, "games": len(entries), "events": header["events"], "ids": header["tokens"], "nights": nights,
           "providers": providers}
    frow = manifest.file_entry(rel, size, sha, league=league, rows=header["events"], sources=providers,
                               fence_status="none" if train_ok else "tuning", block=block)
    return row, frow


def _sidecar_entry(root: Path, name: str, rows: int | None, srcs: list[str], block: dict) -> dict[str, Any]:
    data = (root / name).read_bytes()
    return manifest.file_entry(name, len(data), hashlib.sha256(data).hexdigest(), league=None, rows=rows, sources=srcs, fence_status="none",
                               block=block)


def _write_sidecars(root: Path, names: tk.NameTables, pairs: list[dict], srcs: list[str], block: dict) -> list[dict]:
    (root / manifest.CORPUS_NAMES_FILE).write_text(json.dumps(names.to_json(), indent=1, sort_keys=True) + "\n")
    lines = [json.dumps(p, sort_keys=True, separators=(",", ":")) for p in pairs]
    (root / manifest.CORPUS_DEDUP_FILE).write_text("".join(x + "\n" for x in lines))
    n_names = sum(len(n) for slots in names.kinds.values() for n in slots.values())
    return [_sidecar_entry(root, manifest.CORPUS_NAMES_FILE, n_names, srcs, block),
            _sidecar_entry(root, manifest.CORPUS_DEDUP_FILE, len(lines), srcs, block)]


def _split_section(plan: dict, keep: dict, rows: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lg, sp in sorted(plan.items()):
        roles: dict[str, set[str]] = {}
        for g in keep.values():
            if g.league == lg and g.klass == "clear" and g.night and sp.role_of(g.night):
                roles.setdefault(sp.role_of(g.night), set()).add(g.night)
        info = sp.to_json()
        info["roles"] = {r: {"nights": len(ns), "first": min(ns), "last": max(ns)} for r, ns in sorted(roles.items())}
        info["folds"] = walkforward.fold_ranges(roles.get("train", set()) | roles.get("valid", set()))
        out[lg] = info
    for r in rows:                                    # a league with only tuning shards has no split to record
        out.setdefault(r["league"], {"mode": "all-train", "valid_from": None, "holdout_from": None, "gap_days": 0, "nights": 0,
                                     "roles": {}, "folds": []})
    return out


def _publish(tmp: Path, out: Path, tag: str, doc: dict) -> None:
    if out.exists():
        existing = out / f"{tag}{dm.MANIFEST_SUFFIX}"
        if existing.is_file() and existing.read_text(encoding="utf-8") == dm.render(doc):
            shutil.rmtree(tmp)                         # the identical corpus is already there
            return
        if any(out.iterdir()):
            shutil.rmtree(tmp)
            raise dm.ManifestError(f"{out.name} already holds different content; a new version needs a new tag and folder")
        out.rmdir()
    out.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, out)

