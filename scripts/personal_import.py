"""Import Nick's own downloaded exports (Apple privacy export, YouTube watch history, Spotify extended history) into the local store.

Why: decisions.md 2026-11-09 (personal data): metadata only, local only. Only the allow-listed fields (timestamp, service,
title, tags, duration) are kept; every other column or key is dropped, never stored (redzone/personal/store.py).

    python scripts/personal_import.py PATH [--data-dir data] [--dry-run]     # --data-dir must be git-ignored in this repo

PATH is a file or a folder he unzipped himself. Files are untrusted: parsed with json/csv only (no eval, no links followed,
no zips opened, symlinks skipped), each file capped at 50 MB, rows capped, malformed rows skipped and counted.
Nothing is printed from the data; the summary is counts only.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redzone.personal import store  # noqa: E402

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_FILES = 500
# Apple's export is CSV with varying columns; these header names (lowercased) are the only ones read
APPLE_TS = ("event start timestamp", "event timestamp", "date", "timestamp", "start time", "play date", "activity date")
APPLE_TITLE = ("item name", "title", "item description", "content name", "track description", "app name")
APPLE_SERVICE = ("service", "product", "media type", "app name")
APPLE_DUR_MS = ("play duration milliseconds", "media duration in milliseconds")


def _first(row: dict, names) -> str | None:
    for n in names:
        v = row.get(n)
        if isinstance(v, str) and v.strip():
            return v
    return None


def parse_youtube(data) -> tuple[list[dict], int]:
    out, bad = [], 0
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("title"), str):
            bad += 1
            continue
        title = e["title"]
        if title.startswith("Watched "):
            title = title[8:]
        subs = e.get("subtitles")
        chan = subs[0].get("name") if isinstance(subs, list) and subs and isinstance(subs[0], dict) else None
        out.append({"ts": e.get("time"), "service": "youtube", "title": title,
                    "tags": [chan] if isinstance(chan, str) else [], "source": "youtube"})
    return out, bad


def parse_spotify(data) -> tuple[list[dict], int]:
    out, bad = [], 0
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict):
            bad += 1
            continue
        title = e.get("master_metadata_track_name") or e.get("episode_name")
        tags = [t for t in (e.get("master_metadata_album_artist_name"), e.get("episode_show_name")) if isinstance(t, str)]
        ms = e.get("ms_played")
        out.append({"ts": e.get("ts"), "service": "spotify", "title": title if isinstance(title, str) else "", "tags": tags,
                    "duration_s": ms / 1000 if isinstance(ms, (int, float)) and not isinstance(ms, bool) else None,
                    "source": "spotify"})
    return out, bad


def parse_apple_csv(fh) -> tuple[list[dict], int]:
    out, bad = [], 0
    rd = csv.DictReader(fh)
    for n, row in enumerate(rd):
        if n >= MAX_ROWS:
            break
        row = {(k or "").strip().lower(): v for k, v in row.items() if isinstance(v, str)}
        ts = _first(row, APPLE_TS)
        if not ts:
            bad += 1
            continue
        ms = _first(row, APPLE_DUR_MS)
        try:
            dur = float(ms) / 1000 if ms else None
        except ValueError:
            dur = None
        out.append({"ts": ts, "service": (_first(row, APPLE_SERVICE) or "apple")[:64], "title": _first(row, APPLE_TITLE) or "",
                    "duration_s": dur, "source": "apple"})
    return out, bad


def import_file(path: Path) -> tuple[list[dict], int, str]:
    """(records, malformed_count, note) for one file. Never raises on bad content."""
    try:
        if path.is_symlink() or not path.is_file():
            return [], 0, "skipped (not a plain file)"
        if path.stat().st_size > MAX_BYTES:
            return [], 0, "skipped (over 50 MB)"
        name, suf = path.name.lower(), path.suffix.lower()
        if suf == ".json":
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if "streaming_history" in name or "endsong" in name:
                recs, bad = parse_spotify(data)
            elif "watch-history" in name or "watch history" in name:
                recs, bad = parse_youtube(data)
            else:
                return [], 0, "skipped (unrecognized json)"
        elif suf == ".csv":
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
                recs, bad = parse_apple_csv(fh)
        else:
            return [], 0, "skipped (unsupported type)"
    except (OSError, ValueError, RecursionError, csv.Error) as e:
        return [], 1, f"skipped ({type(e).__name__})"
    return recs[:MAX_ROWS], bad, "ok"


def find_files(p: Path) -> list[Path]:
    if p.is_file():
        return [p]
    out = []
    for f in sorted(p.rglob("*")):
        if len(out) >= MAX_FILES:
            break
        if f.suffix.lower() in (".json", ".csv") and not f.is_symlink() and f.is_file():
            out.append(f)
    return out


def store_is_git_ignored(data_dir: Path) -> bool:
    """Is the store's path git-ignored in this repo? The repo is public: a folder git could add, or one it can't vouch for, is refused."""
    try:
        return subprocess.run(["git", "check-ignore", "-q", str(store.store_path(data_dir.resolve()))], cwd=ROOT, capture_output=True).returncode == 0
    except OSError:
        return False


def run(path: Path, data_dir: Path, dry_run: bool = False) -> dict:
    stats = {"files": 0, "read": 0, "malformed": 0, "written": 0, "skipped_files": 0}
    for f in find_files(path):
        recs, bad, note = import_file(f)
        stats["files"] += 1
        stats["malformed"] += bad
        stats["skipped_files"] += note != "ok"
        stats["read"] += len(recs)
        if not dry_run:
            stats["written"] += store.append(store.store_path(data_dir), recs, dedupe=True)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", type=Path, nargs="?")
    ap.add_argument("--profile", action="store_true", help="print what the local store adds up to, then stop")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.profile:
        from redzone.personal.profile import profile
        print(json.dumps(profile(a.data_dir), indent=1, sort_keys=True))
        return 0
    if a.path is None:
        ap.error("path is required unless --profile")
    if not a.path.exists():
        print("path not found")
        return 2
    if not store_is_git_ignored(a.data_dir):
        print("refused: --data-dir is not git-ignored in this repo, and personal data never goes where git can add it")
        return 2
    s = run(a.path, a.data_dir, a.dry_run)
    print(" ".join(f"{k}={v}" for k, v in s.items()) + (" (dry run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
