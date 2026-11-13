"""Local layout for fetched history: atomic writes, a per-game ledger, a run lock, the report and the integrity check.

Why: B-01 must be resumable after a kill at any moment with no duplicates and no half-written files (WORKPLAN B-01:
"manifest of what is fetched, per game, atomic writes, skip-existing"). The data lives under data/history/, which is
git-ignored; `ensure_untracked` refuses a root inside a repo that would track it ("NEVER committed").

Layout under <root>:
  espn/<league>/<season>/<game id>.json.gz     one compact record per game (written atomically)
  espn/<league>/ledger.jsonl                   one line per finished game: ok / no_plays / not_found (append-only)
  calendars/espn_<league>_<season>.json        a season's list of scoreboard units
  slates/espn/<league>/<season>/<unit>.json    a unit's game list (final games only)
  datasets/<source>/<file>                     whole-file downloads (hoopR, wehoop) and datasets/<source>/ledger.jsonl
  shards/espn_<league>_<season>.jsonl.gz       packed shards (pack.py)
  runs/<utc>.json                              one summary per run
  MANIFEST.json                                licences, robots findings and the shard index
  .lock                                        one writer at a time

A game is done when its ledger line says ok, no_plays or not_found; a game file without a ledger line (a kill between the
rename and the append) is adopted on the next run. A torn last ledger line is ignored.
"""
from __future__ import annotations

import fcntl
import gzip
import json
import os
import re
import statistics
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ID_RE = re.compile(r"^\d{4,15}$")      # ids come from untrusted JSON and become file names
TERMINAL = ("ok", "no_plays", "not_found")
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "history"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def atomic_write(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, fsync, rename over `path`. A kill leaves either nothing or the whole file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_untracked(root: Path) -> None:
    """Refuse a data root inside a git repo that does not ignore it. No repo around it (Kaggle) is fine."""
    here = root if root.exists() else root.parent
    while not here.exists():
        here = here.parent
    try:
        top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return
    if top.returncode != 0:
        return
    probe = root / "MANIFEST.json"
    ign = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", str(probe)], capture_output=True, timeout=20)
    if ign.returncode != 0:
        raise SystemExit(f"refusing to write fetched data to {root}: it is inside the git repo {top.stdout.strip()} and not git-ignored. "
                         "Use the default data/history/ (ignored) or a folder outside the repo.")


class Store:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)

    # ---- paths -------------------------------------------------------------
    def game_path(self, league: str, season: int, gid: str) -> Path:
        if not ID_RE.match(str(gid)):
            raise ValueError(f"bad game id {gid!r}")
        return self.root / "espn" / league / str(season) / f"{gid}.json.gz"

    def ledger_path(self, kind: str, name: str) -> Path:
        return self.root / kind / name / "ledger.jsonl"

    # ---- lock and clean-up -------------------------------------------------
    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        f = open(self.root / ".lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise SystemExit(f"another fetch is running on {self.root} (lock held); one writer at a time keeps the rate at 1 request/second")
        try:
            self.clean_tmp()
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()

    def clean_tmp(self) -> int:
        """Remove temp files a killed run left behind. Only called while holding the lock."""
        n = 0
        if self.root.exists():
            for p in self.root.rglob(".*.tmp"):
                p.unlink(missing_ok=True)
                n += 1
        return n

    # ---- ledger ------------------------------------------------------------
    def read_ledger(self, kind: str, name: str) -> dict[str, dict]:
        """{id: entry}: the last valid line for each id wins; a torn or garbled line is skipped."""
        out: dict[str, dict] = {}
        p = self.ledger_path(kind, name)
        if not p.exists():
            return out
        with open(p, "rb") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("id") is not None:
                    out[str(e["id"])] = e
        return out

    def append_ledger(self, kind: str, name: str, entry: dict) -> None:
        p = self.ledger_path(kind, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = (dumps(entry) + "\n").encode()
        with open(p, "ab+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":          # a torn last line: start a fresh one
                    f.write(b"\n")
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    # ---- small json files (calendars, slates, runs, manifest) ----------------
    def read_json(self, rel: str) -> Any | None:
        p = self.root / rel
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def write_json(self, rel: str, obj: Any) -> None:
        atomic_write(self.root / rel, (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode())


def read_game(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.loads(f.read())


def write_game(path: Path, record: dict) -> tuple[int, str]:
    """Atomically write one gzip'd record; returns (bytes on disk, sha256 of those bytes)."""
    import hashlib
    raw = gzip.compress(dumps(record).encode("utf-8"), compresslevel=6, mtime=0)
    atomic_write(path, raw)
    return len(raw), hashlib.sha256(raw).hexdigest()


# ---- report and verify --------------------------------------------------------

def espn_leagues(store: Store) -> list[str]:
    d = store.root / "espn"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def summarize(store: Store, by_season: bool = False) -> dict[str, Any]:
    """Per-league counts from the ledgers (fast; no game file is opened)."""
    out: dict[str, Any] = {"root": str(store.root), "leagues": {}, "total_events": 0, "total_games": 0}
    for lg in espn_leagues(store):
        led = store.read_ledger("espn", lg)
        rows = list(led.values())
        ok = [r for r in rows if r.get("status") == "ok"]
        seasons: dict[int, dict] = {}
        for r in rows:
            s = seasons.setdefault(int(r.get("season") or 0), {"games": 0, "events": 0, "no_plays": 0, "not_found": 0, "wall_games": 0})
            if r.get("status") == "ok":
                s["games"] += 1
                s["events"] += int(r.get("plays") or 0)
                s["wall_games"] += 1 if (r.get("wall") or 0) >= 0.9 else 0
            elif r.get("status") in ("no_plays", "not_found"):
                s[r["status"]] += 1
        ev = [int(r.get("plays") or 0) for r in ok]
        entry: dict[str, Any] = {
            "games": len(ok), "events": sum(ev), "no_plays": sum(1 for r in rows if r.get("status") == "no_plays"),
            "not_found": sum(1 for r in rows if r.get("status") == "not_found"),
            "wall_games": sum(1 for r in ok if (r.get("wall") or 0) >= 0.9),
            "events_per_game_mean": round(sum(ev) / len(ev), 1) if ev else None,
            "events_per_game_median": statistics.median(ev) if ev else None,
            "seasons": [min(seasons), max(seasons)] if seasons else None,
            "gb_on_disk": round(sum(int(r.get("bytes") or 0) for r in ok) / 1e9, 4),
        }
        if by_season:
            entry["by_season"] = {str(k): v for k, v in sorted(seasons.items(), reverse=True)}
        out["leagues"][lg] = entry
        out["total_events"] += entry["events"]
        out["total_games"] += entry["games"]
    ds = store.root / "datasets"
    if ds.exists():
        out["datasets"] = {}
        for d in sorted(p for p in ds.iterdir() if p.is_dir()):
            rows = list(store.read_ledger("datasets", d.name).values())
            out["datasets"][d.name] = {"files": sum(1 for r in rows if r.get("status") == "ok"),
                                       "rows": sum(int(r.get("rows") or 0) for r in rows if r.get("status") == "ok"),
                                       "gb": round(sum(int(r.get("bytes") or 0) for r in rows) / 1e9, 4)}
    return out


def verify(store: Store) -> list[str]:
    """Integrity problems, [] when clean: ledger 'ok' without a file, a game file without a ledger line, files with a wrong
    name, duplicate ids across seasons, record id/league/season disagreeing with the ledger or its path, play count mismatch."""
    problems: list[str] = []
    for lg in espn_leagues(store):
        led = store.read_ledger("espn", lg)
        seen: dict[str, Path] = {}
        for f in sorted((store.root / "espn" / lg).glob("*/*.json.gz")):
            gid = f.name[: -len(".json.gz")]
            if not ID_RE.match(gid):
                problems.append(f"{lg}: bad file name {f.name}")
                continue
            if gid in seen:
                problems.append(f"{lg}: game {gid} stored twice ({seen[gid].parent.name} and {f.parent.name})")
            seen[gid] = f
            e = led.get(gid)
            if e is None:
                problems.append(f"{lg}: file {gid} has no ledger line (the next run adopts it)")
                continue
            try:
                rec = read_game(f)
            except (OSError, ValueError, EOFError) as ex:
                problems.append(f"{lg}: file {gid} unreadable: {ex}")
                continue
            if str(rec.get("id")) != gid or rec.get("league") != lg or str(rec.get("season")) != f.parent.name:
                problems.append(f"{lg}: file {gid} header disagrees with its path")
            if len(rec.get("plays") or []) != int(e.get("plays") or -1):
                problems.append(f"{lg}: file {gid} has {len(rec.get('plays') or [])} plays, ledger says {e.get('plays')}")
        for gid, e in led.items():
            if e.get("status") == "ok" and gid not in seen:
                problems.append(f"{lg}: ledger says {gid} is ok but its file is missing")
    return problems


def format_report(summary: dict[str, Any], target: int = 30_000_000) -> str:
    lines = [f"history root: {summary['root']}",
             f"{'league':<6} {'games':>8} {'events':>12} {'ev/game':>8} {'median':>7} {'wall':>7} {'noplay':>7} {'404':>5} {'seasons':>11} {'GB':>7}"]
    for lg, e in summary["leagues"].items():
        se = f"{e['seasons'][0]}-{e['seasons'][1]}" if e["seasons"] else "-"
        lines.append(f"{lg:<6} {e['games']:>8,} {e['events']:>12,} {e['events_per_game_mean'] or 0:>8} {e['events_per_game_median'] or 0:>7} "
                     f"{e['wall_games']:>7,} {e['no_plays']:>7,} {e['not_found']:>5,} {se:>11} {e['gb_on_disk']:>7}")
        for s, v in (e.get("by_season") or {}).items():
            lines.append(f"  {s}: {v['games']:,} games, {v['events']:,} events, {v['wall_games']:,} with wall clocks, {v['no_plays']:,} without plays, {v['not_found']:,} not found")
    pct = 100.0 * summary["total_events"] / target if target else 0.0
    lines.append(f"{'total':<6} {summary['total_games']:>8,} {summary['total_events']:>12,}   = {pct:.2f}% of the {target:,}-event B0a target")
    for name, d in (summary.get("datasets") or {}).items():
        lines.append(f"dataset {name}: {d['files']} files, {d['rows']:,} rows, {d['gb']} GB")
    return "\n".join(lines)
