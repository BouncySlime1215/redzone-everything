"""Local store for Nick's own phone metadata: one JSONL under data/personal/, allow-listed fields only.

Why: decisions.md 2026-11-09 (personal data): metadata only (service, when, how long, title), local only, never committed;
the allow-list lives here so no caller can put a message body, URL or identifier in the file.

Every record has exactly: ts (UTC ISO), service, title, tags, duration_s, source. `clean()` builds a new dict from those
keys, so anything else on the input is dropped, never stored. Stdlib only. data/ is git-ignored (.gitignore).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("ts", "service", "title", "tags", "duration_s", "source")
EVENT_SOURCES = ("shortcut", "apple", "youtube", "spotify")
MAX_TEXT = 200
MAX_TAGS = 5
STORE_NAME = "events.jsonl"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def store_path(data_dir: Path) -> Path:
    """data_dir is the folder that holds config.yaml (data/); the store sits in its personal/ subfolder."""
    return Path(data_dir) / "personal" / STORE_NAME


def text(v, limit: int = MAX_TEXT) -> str:
    if not isinstance(v, str):
        return ""
    return _CTRL.sub(" ", v).strip()[:limit]


def parse_ts(v) -> str | None:
    """A timestamp as UTC ISO text, or None. Accepts ISO 8601 text or epoch seconds/milliseconds."""
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            x = float(v) / 1000 if v > 1e11 else float(v)
            d = datetime.fromtimestamp(x, tz=timezone.utc)
        elif isinstance(v, str) and 8 <= len(v.strip()) <= 40:
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            d = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    if not (2000 <= d.year <= 2100):
        return None
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(rec) -> dict | None:
    """The allow-listed form of one record, or None when it has no usable timestamp or service."""
    if not isinstance(rec, dict):
        return None
    ts, service = parse_ts(rec.get("ts")), text(rec.get("service"), 64)
    if not ts or not service:
        return None
    tags = rec.get("tags") if isinstance(rec.get("tags"), (list, tuple)) else []
    dur = rec.get("duration_s")
    dur = round(float(dur), 1) if isinstance(dur, (int, float)) and not isinstance(dur, bool) and 0 <= dur < 86400 * 2 else None
    source = rec.get("source") if rec.get("source") in EVENT_SOURCES else "shortcut"
    return {"ts": ts, "service": service, "title": text(rec.get("title")),
            "tags": [t for t in (text(x, 80) for x in tags[:MAX_TAGS]) if t], "duration_s": dur, "source": source}


def _key(r: dict) -> str:
    return f"{r['source']}|{r['ts']}|{r['service']}|{r['title']}"


def read_all(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = clean(json.loads(line))
                except ValueError:
                    continue
                if r:
                    out.append(r)
    except OSError:
        pass
    return out


def append(path: Path, recs, dedupe: bool = False) -> int:
    """Clean and append records; returns how many were written. With dedupe, a record already in the file is skipped."""
    seen = {_key(r) for r in read_all(path)} if dedupe else set()
    rows = []
    for rec in recs:
        r = clean(rec)
        if r is None or (dedupe and _key(r) in seen):
            continue
        seen.add(_key(r))
        rows.append(json.dumps(r, separators=(",", ":"), sort_keys=True))
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
    return len(rows)
