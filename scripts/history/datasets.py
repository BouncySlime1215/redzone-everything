"""Whole-file public datasets (hoopR, wehoop, nflverse) with the licence gate: one season per file, sha256 recorded.

Why: B-01 also grows the corpus from the sportsdataverse play-by-play files "where their licences allow". Those are
ESPN rows already flattened (hoopR NBA 2002+, men's college 2003+, wehoop women's college 2005+), one parquet per season on
raw.githubusercontent.com (robots.txt there: 404, no rules). github.com release URLs are closed by github.com/robots.txt, so
nflverse is registered but blocked (licences.py explains). A season file that touches a fenced night (the locked window, or
the evaluation window) is skipped whole: B-02 may not use rows after the fence, and a file cannot be cut here without opening it.

Stored under <root>/datasets/<key>/play_by_play_<season>.parquet; the ledger line holds bytes, sha256, ETag, Last-Modified,
row count and distinct games (when pyarrow is importable; null otherwise, and the report says so). `main` is mutable, so the
sha256 and ETag are what pin a file.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

from .leagues import season_touches_fence
from .licences import PERSONAL_TAG, SOURCES, gate
from .net import Blocked, FetchError, NotFound, PoliteClient, check_robots
from .store import Store

RAW = "https://raw.githubusercontent.com/sportsdataverse"


@dataclass(frozen=True)
class Dataset:
    key: str
    source: str        # key in licences.SOURCES
    league: str
    url: str           # with {y}
    first: int
    last: int


DATASETS: dict[str, Dataset] = {d.key: d for d in (
    Dataset("hoopr_nba", "hoopr", "nba", RAW + "/hoopR-nba-data/main/nba/pbp/parquet/play_by_play_{y}.parquet", 2002, 2026),
    Dataset("hoopr_mbb", "hoopr", "cbb", RAW + "/hoopR-mbb-data/main/mbb/pbp/parquet/play_by_play_{y}.parquet", 2003, 2026),
    Dataset("wehoop_wbb", "wehoop", "wcbb", RAW + "/wehoop-wbb-data/main/wbb/pbp/parquet/play_by_play_{y}.parquet", 2005, 2026),
    Dataset("nflverse_pbp", "nflverse", "nfl", "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{y}.parquet", 1999, 2025),
    Dataset("cfbfastr_pbp", "cfbfastr", "cfb", RAW + "/cfbfastR-data/main/pbp/parquet/play_by_play_{y}.parquet", 2002, 2020),
)}


def parquet_facts(path) -> dict[str, Any]:
    """Row count and distinct game ids from the parquet file, or nulls when pyarrow is not installed."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return {"rows": None, "games": None, "note": "pyarrow not installed: counts unknown"}
    pf = pq.ParquetFile(path)
    rows = pf.metadata.num_rows
    games = None
    if "game_id" in pf.schema_arrow.names:
        import pyarrow.compute as pc
        games = len(pc.unique(pq.read_table(path, columns=["game_id"]).column("game_id")))
    return {"rows": rows, "games": games}


def fetch_dataset(client: PoliteClient, store: Store, key: str, seasons: list[int], *, accepted: bool,
                  max_files: int | None = None, log: Callable[[str], None] = print,
                  facts: Callable[[Any], dict[str, Any]] = parquet_facts) -> dict[str, Any]:
    ds = DATASETS[key]
    gate(ds.source, accepted)          # SystemExit with the finding for blocked, unverified or un-acknowledged sources
    rob = check_robots(client, ds.url.format(y=2000), "/" + ds.url.split("/", 3)[3].format(y=2000))
    if rob["verdict"] == "deny":
        raise SystemExit(f"robots.txt of {rob['host']} disallows the path: not fetching {key}")
    if rob["verdict"] == "unavailable" and not SOURCES[ds.source].robots_ok_when_unavailable:
        raise SystemExit(f"robots.txt of {rob['host']} is unavailable ({rob.get('note')}): not fetching {key}")
    out = {"key": key, "files_ok": 0, "files_not_found": 0, "skipped_done": 0, "skipped_fenced": 0, "errors": [], "stop_reason": "complete",
           "robots": rob}
    ledger = store.read_ledger("datasets", key)
    for season in seasons:
        if season < ds.first or season > ds.last:
            continue
        if season_touches_fence(ds.league, season):
            out["skipped_fenced"] += 1
            log(f"{key} {season}: touches a fenced night, skipped whole")
            continue
        dest = store.root / "datasets" / key / f"play_by_play_{season}.parquet"
        prior = ledger.get(str(season))
        if prior and prior.get("status") in ("ok", "not_found") and (prior["status"] == "not_found" or dest.exists()):
            out["skipped_done"] += 1
            continue
        if max_files is not None and out["files_ok"] >= max_files:
            out["stop_reason"] = "max_files"
            break
        url = ds.url.format(y=season)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        try:
            info = client.download(url, dest)
        except NotFound:
            store.append_ledger("datasets", key, {"id": str(season), "season": season, "status": "not_found", "url": url, "t": now})
            out["files_not_found"] += 1
            continue
        except Blocked as e:
            out["stop_reason"] = "blocked"
            out["errors"].append(str(e))
            break
        except FetchError as e:
            out["errors"].append(f"{key} {season}: {e}")
            log(f"ERROR {key} {season}: {e}")
            continue
        entry = {"id": str(season), "season": season, "league": ds.league, "status": "ok", "url": url, "t": now, "tag": PERSONAL_TAG, **info, **facts(dest)}
        store.append_ledger("datasets", key, entry)
        out["files_ok"] += 1
        log(f"{key} {season}: {info['bytes']:,} bytes, rows={entry.get('rows')}")
    return out
