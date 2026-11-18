"""Pack per-game records into deterministic per-league-season shards and write MANIFEST.json (licences, robots, shard index).

Why: B-02 (corpus v1) needs "a manifest with a sha256 per shard, per league and season" (tracks/B-foundation-model.md corpus
rule 1) and a licence line on every shard (rule 6, scripts/license_check.py). A shard is one gzip'd JSONL file, one game per
line, ordered by game id, with a fixed gzip timestamp, so packing the same games twice gives the same sha256. Each line has
id, plays, matchup and date, which is what scripts/noggin_tokens.games_of reads from a *.jsonl.gz.

The manifest lists espn shards (from this module) and dataset files (from datasets.py ledgers). Every row names its source,
licence line and the tag "personal-use, not for commercial release", so `python scripts/license_check.py <root>/MANIFEST.json`
passes for sources classed restrictive and fails if a tag is ever dropped.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
from typing import Any

from .licences import PERSONAL_TAG, SOURCES, manifest_licences
from .store import Store, atomic_write, dumps, read_game


def pack_league_season(store: Store, league: str, season: int) -> dict[str, Any] | None:
    d = store.root / "espn" / league / str(season)
    files = sorted(d.glob("*.json.gz"), key=lambda p: int(p.name.split(".")[0])) if d.exists() else []
    if not files:
        return None
    lines, events, wall = [], 0, 0
    for f in files:
        rec = read_game(f)
        lines.append(dumps(rec))
        events += len(rec["plays"])
        wall += 1 if (rec.get("wall_frac") or 0) >= 0.9 else 0
    raw = gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), compresslevel=6, mtime=0)
    rel = f"shards/espn_{league}_{season}.jsonl.gz"
    atomic_write(store.root / rel, raw)
    return {"source": "espn", "league": league, "season": season, "file": rel, "games": len(files), "events": events, "wall_games": wall,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "license": SOURCES["espn"].licence, "tag": PERSONAL_TAG}


def dataset_rows(store: Store) -> list[dict[str, Any]]:
    rows = []
    base = store.root / "datasets"
    for d in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        from .datasets import DATASETS
        ds = DATASETS.get(d.name)
        if ds is None:
            continue
        for e in store.read_ledger("datasets", d.name).values():
            if e.get("status") == "ok":
                rows.append({"source": ds.source, "league": ds.league, "season": e["season"], "file": f"datasets/{d.name}/play_by_play_{e['season']}.parquet",
                             "rows": e.get("rows"), "games": e.get("games"), "bytes": e.get("bytes"), "sha256": e.get("sha256"), "etag": e.get("etag"),
                             "license": SOURCES[ds.source].licence, "tag": PERSONAL_TAG})
    return sorted(rows, key=lambda r: (r["source"], r["league"], r["season"]))


def write_manifest(store: Store, shard_rows: list[dict[str, Any]], accepted: bool = False) -> dict[str, Any]:
    robots = store.read_json("robots_espn.json")
    manifest = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "tag": PERSONAL_TAG,
                "note": "Local only; never committed. Every row keeps the personal-use tag (decisions.md 2026-11-05 11:20 PM).",
                "shards": shard_rows + dataset_rows(store), "licences": manifest_licences(accepted), "robots": {"espn": robots} if robots else {}}
    store.write_json("MANIFEST.json", manifest)
    return manifest


def pack(store: Store, leagues: list[str] | None = None, accepted: bool = False) -> dict[str, Any]:
    """(Re)pack every league-season with games, then rewrite the manifest. Existing shards of other seasons are kept in the index."""
    idx = {(r["league"], r["season"]): r for r in (store.read_json("shards/index.json") or {}).get("shards", [])}
    base = store.root / "espn"
    for lg in sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        if leagues and lg not in leagues:
            continue
        for sd in sorted((p for p in (base / lg).iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)):
            row = pack_league_season(store, lg, int(sd.name))
            if row:
                idx[(lg, int(sd.name))] = row
    rows = [idx[k] for k in sorted(idx)]
    store.write_json("shards/index.json", {"shards": rows})
    return write_manifest(store, rows, accepted)
