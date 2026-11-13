"""Versioned data manifests: pin exactly which files a training run saw, by tag, window, sha256 and fence status.

Why: WORKPLAN v2 task F-05 (data lake) and TRAINING-PLAN "pin data by manifest hash": a run records one
`data_manifest_hash` in the model registry (F-02), so that hash must name files, not a folder that grows every night.
The form follows eval-data.lock.json (a tag, a window, a tree digest over path + sha256), extended with per-file
size, row count, source, licence line and the leak-fence status of each night (redzone/lab/fence.py).

    data = load_pinned("archive-nba-202610-v1")       # ManifestError unless every file re-hashes to the manifest
    data.paths(night="20261020")                       # absolute Paths of that night's files
    load_pinned(tag, for_training=True)                # fence.FenceError when any file sits in a fenced night

Kinds (what a manifest can pin; every path in it is relative to the kind's data folder):
    archive   <league>/scoreboard_YYYYMMDD.json and <league>/summary_<id>.json   (the recorded nights, data/archive)
    history   espn/<league>/<season>/<id>.json.gz, datasets/<key>/*.parquet, shards/*.jsonl.gz   (B-01, data/history)
    feedlag   YYYY-MM-DD.jsonl feed-arrival logs                                  (C-02, data/feedlag)
    frames    every file of the picture data                                      (data/vision-v1; PRIVATE)

A manifest is deterministic: sorted keys, sorted paths, no clock, so the same files give the same bytes and the same
`manifest_sha256`. It holds relative paths only, never an absolute path, address, URL or credential (`scrub_problems`
runs on every build, verify and load). Symlinks inside a data folder are never followed; file names that look like
secrets are skipped without being opened. A manifest of private data is written only to a git-ignored place
(`assert_not_committable`) and `plan_bundle` refuses it unless asked explicitly. Stdlib only; reads files, writes
only the manifest (and, for `write_bundle_skeleton`, three small files).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from redzone.lab import evaldata, fence

MANIFEST_FORMAT = "redzone-data-manifest/1"
MANIFEST_SUFFIX = ".manifest.json"
PRIVATE_MANIFEST_SUFFIX = ".private.manifest.json"       # also in .gitignore
DATA_KINDS = ("archive", "history", "feedlag", "frames")
PRIVATE_DATA_KINDS = ("frames",)
FRAMES_SOURCE = "tv_frames"
# feedlag rows name their feed (redzone/lab/feedlag.FEED_SOURCES); the licence table knows the provider.
FEEDLAG_SOURCE_NAMES = {"espn_summary": "espn", "nba_cdn": "nba_cdn"}
DEFAULT_SETTLE_SECONDS = 600       # a file written more recently than this is still being written: not pinned
SHOWN_PROBLEMS = 20
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
FILE_KEYS = ("bytes", "fence", "league", "license", "night", "path", "rows", "sha256", "sources")
LICENCE_KEYS = ("licence", "status", "tag", "terms_class")
REFUSED_STATUSES = ("blocked", "unverified")

_REPO_DIR = Path(__file__).resolve().parents[2]
_KIND_DIRS = {"archive": "archive", "history": "history", "feedlag": "feedlag", "frames": "vision-v1"}
_SHA = re.compile(r"^[0-9a-f]{64}$")

# --- what a data folder may hold ------------------------------------------------------------------------------
_SCOREBOARD_NAME = re.compile(r"^scoreboard_(\d{8})\.json$")
_SUMMARY_NAME = re.compile(r"^summary_(\d{4,15})\.json$")
_LEAGUE_DIR = re.compile(r"^[a-z][a-z0-9]{1,9}$")
_FEEDLAG_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")
_GAME_FILE = re.compile(r"^(\d{4,15})\.json\.gz$")
_DATASET_FILE = re.compile(r"^play_by_play_(\d{4})\.parquet$")
_SHARD_FILE = re.compile(r"^espn_([a-z0-9]+)_(\d{4})\.jsonl\.gz$")
_FRAME_NIGHT = re.compile(r"^(\d{8})-")
# Names that look like secrets are skipped unopened (CLAUDE.md: never read tokens, passwords or keys).
SECRET_FILE_NAME = re.compile(r"(\.env($|\.)|token|secret|passw|credential|oauth|api[_-]?key|cookie|id_rsa|\.pem$|\.key$|\.p12$)", re.I)

# --- what a manifest may not contain --------------------------------------------------------------------------
_OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
_STRING_RULES = (
    ("an absolute or home path", re.compile(r"(^|[^\w.-])(/(Users|home|private|var|tmp|Volumes|opt|etc|root|mnt|Library|usr)/|~/|[A-Za-z]:[\\/]|\\\\\w)")),
    ("a path starting with a slash", re.compile(r"^/")),
    ("a URL", re.compile(r"\b[a-z][a-z0-9+.-]*://|\bwww\.", re.I)),
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("an IPv4 address", re.compile(rf"(?<![\d.]){_OCTET}(?:\.{_OCTET}){{3}}(?!\d|\.\d)")),
    ("an IPv6 or MAC address", re.compile(r"\b(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I)),
    ("a credential", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|sk-ant-|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                                r"-----BEGIN [A-Z ]*PRIVATE KEY|xox[abprs]-[A-Za-z0-9-]{10,}|"
                                r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.|Bearer\s+\S{16,}|"
                                r"(password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+", re.I)),
)
_SECRET_KEY_NAME = re.compile(r"(token|secret|passw|credential|api[_-]?key|authorization|cookie|oauth)", re.I)


class ManifestError(ValueError):
    """A manifest cannot be built, does not verify, or a request would put private data somewhere it may not go."""


# --- small helpers ----------------------------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest_hash(doc: Mapping) -> str:
    """The value recorded as `data_manifest_hash`: sha256 of the manifest without its own hash field."""
    return hashlib.sha256(canonical_json({k: v for k, v in doc.items() if k != "manifest_sha256"}).encode()).hexdigest()


def render(doc: Mapping) -> str:
    """The text written to disk: pretty header, one line per file, sorted keys. Same manifest, same bytes."""
    head = json.dumps({k: v for k, v in doc.items() if k != "files"}, indent=1, sort_keys=True, ensure_ascii=True)
    files = doc.get("files") or []
    tail = ',\n "files": [\n' + ",\n".join("  " + canonical_json(f) for f in files) + "\n ]" if files else ',\n "files": []'
    return head[:-2] + tail + "\n}\n"


def fence_status(night: str | None) -> str:
    """'locked' or 'tuning' when the night sits in that fence, 'none' when it is clear, 'unknown' without a night."""
    if night is None:
        return "unknown"
    w = fence.fenced(night)
    return w.name if w else "none"


def check_tag(tag: object) -> str:
    if not isinstance(tag, str) or not TAG_PATTERN.match(tag) or ".." in tag or tag.endswith(".json"):
        raise ManifestError(f"bad tag {tag!r}: 3-80 characters of a-z 0-9 . _ - (it becomes a file name)")
    return tag


def default_root(kind: str) -> Path:
    """Where a kind's data lives: $RZ_DATA_ROOT_<KIND> if set, else data/<folder> in the repo."""
    if kind not in DATA_KINDS:
        raise ManifestError(f"unknown kind {kind!r}; known: {', '.join(DATA_KINDS)}")
    return Path(os.environ.get(f"RZ_DATA_ROOT_{kind.upper()}") or _REPO_DIR / "data" / _KIND_DIRS[kind])


def default_manifest_dir() -> Path:
    """Where manifests are kept: $RZ_DATA_MANIFESTS if set, else data/manifests (git-ignored with the rest of data/)."""
    return Path(os.environ.get("RZ_DATA_MANIFESTS") or _REPO_DIR / "data" / "manifests")


def _string_problems(s: str) -> list[str]:
    return [what for what, rx in _STRING_RULES if rx.search(s)]


def scrub_problems(doc: Any, limit: int = 200) -> list[str]:
    """Every string (and key) in the manifest that looks like an absolute path, address, URL or credential. [] when clean."""
    out: list[str] = []

    def walk(x: Any, where: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(x, str):
            out.extend(f"{where}: looks like {w}" for w in _string_problems(x))
        elif isinstance(x, Mapping):
            for k, v in x.items():
                if isinstance(k, str) and _SECRET_KEY_NAME.search(k):
                    out.append(f"{where}.{k}: key name looks like a credential")
                if isinstance(k, str):
                    out.extend(f"{where}.<key {k[:24]}>: looks like {w}" for w in _string_problems(k))
                walk(v, f"{where}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}]")

    walk(doc, "manifest")
    return out[:limit]


def _digest(path: str, count_rows: bool) -> tuple[str, int, int | None]:
    """(sha256, bytes, lines) in one pass; lines only when asked (a last line without a newline counts)."""
    h = hashlib.sha256()
    size = lines = 0
    last = b""
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
            if count_rows:
                lines += chunk.count(b"\n")
                last = chunk[-1:]
    if count_rows and size and last != b"\n":
        lines += 1
    return h.hexdigest(), size, lines if count_rows else None


def _within(root_real: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def _safe_rel(path: object) -> bool:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    p = PurePosixPath(path)
    return not p.is_absolute() and str(p) == path and all(part not in ("..", ".") for part in p.parts)


# --- scanning a data folder ----------------------------------------------------------------------------------

@dataclass
class _Cand:
    rel: str
    path: str
    size: int
    mtime: float
    night: str | None = None
    league: str | None = None
    sources: tuple[str, ...] = ()
    rows: int | None = None
    count_rows: bool = False
    want_sha: str | None = None
    feed_sources: bool = False


def _entries(path: str, skips: dict) -> tuple[list, list]:
    """(dirs, files) of one folder, sorted. Symlinks, hidden names, secret-looking names and odd files are counted, not returned."""
    dirs, files = [], []
    with os.scandir(path) as it:
        for e in sorted(it, key=lambda x: x.name):
            if e.is_symlink():
                skips["symlinks"] += 1
            elif e.name.startswith("."):
                skips["hidden"] += 1
            elif SECRET_FILE_NAME.search(e.name):
                skips["secret_names"] += 1
            elif e.is_dir(follow_symlinks=False):
                dirs.append(e)
            elif e.is_file(follow_symlinks=False):
                files.append(e)
            else:
                skips["other_files"] += 1
    return dirs, files


def _cand(e: os.DirEntry, rel: str, **kw: Any) -> _Cand:
    st = e.stat(follow_symlinks=False)
    return _Cand(rel=rel, path=e.path, size=st.st_size, mtime=st.st_mtime, **kw)


def _night_or_none(raw: Any, rel: str, notes: list[str]) -> str | None:
    if raw in (None, ""):
        return None
    try:
        return fence.night_key(raw)
    except ValueError:
        notes.append(f"{rel}: night {str(raw)[:12]!r} is not a date; its fence status is unknown")
        return None


def _board_ids(path: str, rel: str, notes: list[str]) -> list[str]:
    try:
        with open(path, "rb") as f:
            events = json.load(f).get("events", [])
        return [str(ev["id"]) for ev in events if isinstance(ev, dict) and ev.get("id")]
    except (OSError, ValueError, AttributeError):
        notes.append(f"{rel}: scoreboard unreadable; the games on it have no night")
        return []


def _scan_archive(root: str, leagues: set[str] | None, skips: dict, notes: list[str]) -> list[_Cand]:
    """<league>/scoreboard_*.json and summary_*.json. A summary takes the night of the first scoreboard that lists its game."""
    out: list[_Cand] = []
    top_dirs, _ = _entries(root, skips)
    for d in top_dirs:
        if not _LEAGUE_DIR.match(d.name) or (leagues and d.name not in leagues):
            skips["other_dirs"] += 1
            continue
        sub_dirs, files = _entries(d.path, skips)
        skips["other_dirs"] += len(sub_dirs)
        game_night: dict[str, str] = {}
        summaries: list[tuple[os.DirEntry, str]] = []
        for e in files:
            rel = f"{d.name}/{e.name}"
            sb, sm = _SCOREBOARD_NAME.match(e.name), _SUMMARY_NAME.match(e.name)
            if sb:
                night = _night_or_none(sb.group(1), rel, notes)
                for gid in _board_ids(e.path, rel, notes):
                    if night:
                        game_night.setdefault(gid, night)
                out.append(_cand(e, rel, night=night, league=d.name, sources=("espn",)))
            elif sm:
                summaries.append((e, sm.group(1)))
            else:
                skips["other_files"] += 1
        for e, gid in summaries:
            out.append(_cand(e, f"{d.name}/{e.name}", night=game_night.get(gid), league=d.name, sources=("espn",)))
    return out


def _scan_feedlag(root: str, leagues: set[str] | None, skips: dict, notes: list[str]) -> list[_Cand]:
    out: list[_Cand] = []
    _, files = _entries(root, skips)
    for e in files:
        m = _FEEDLAG_NAME.match(e.name)
        if not m:
            skips["other_files"] += 1          # logger.out, logger.pid: raw logs stay out
            continue
        night = _night_or_none("".join(m.groups()), e.name, notes)
        out.append(_cand(e, e.name, night=night, count_rows=True, feed_sources=True))
    return out


def _read_ledger(path: str) -> dict[str, dict]:
    """{id: entry} from a history ledger.jsonl: the last valid line for an id wins, a torn line is skipped."""
    out: dict[str, dict] = {}
    try:
        with open(path, "rb") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("id") is not None:
                    out[str(e["id"])] = e
    except FileNotFoundError:
        pass
    return out


def _scan_history(root: str, leagues: set[str] | None, skips: dict, notes: list[str]) -> list[_Cand]:
    out: list[_Cand] = []
    top = {d.name: d for d in _entries(root, skips)[0]}
    for lg_dir in _entries(top["espn"].path, skips)[0] if "espn" in top else []:
        if leagues and lg_dir.name not in leagues:
            continue
        ledger = _read_ledger(os.path.join(lg_dir.path, "ledger.jsonl"))
        unledgered = 0
        for sd in _entries(lg_dir.path, skips)[0]:
            if not sd.name.isdigit():
                skips["other_dirs"] += 1
                continue
            for e in _entries(sd.path, skips)[1]:
                m = _GAME_FILE.match(e.name)
                if not m:
                    skips["other_files"] += 1
                    continue
                led = ledger.get(m.group(1))
                rel = f"espn/{lg_dir.name}/{sd.name}/{e.name}"
                if led is None:
                    unledgered += 1
                plays = led.get("plays") if led else None
                out.append(_cand(e, rel, night=_night_or_none(led.get("night") if led else None, rel, notes), league=lg_dir.name,
                                 sources=("espn",), rows=plays if isinstance(plays, int) else None,
                                 want_sha=led.get("sha256") if led and led.get("status") == "ok" else None))
        if unledgered:
            notes.append(f"espn/{lg_dir.name}: {unledgered} game file(s) have no ledger line, so no night")
    for key_dir in _entries(top["datasets"].path, skips)[0] if "datasets" in top else []:
        src = key_dir.name.split("_", 1)[0]          # hoopr_nba -> hoopr (the key prefix is the licence source)
        ledger = _read_ledger(os.path.join(key_dir.path, "ledger.jsonl"))
        for e in _entries(key_dir.path, skips)[1]:
            m = _DATASET_FILE.match(e.name)
            if not m:
                skips["other_files"] += 1
                continue
            led = ledger.get(m.group(1)) or {}
            lg = led.get("league") if isinstance(led.get("league"), str) else None
            if leagues and lg not in leagues:
                continue
            rows = led.get("rows")
            out.append(_cand(e, f"datasets/{key_dir.name}/{e.name}", league=lg, sources=(src,), rows=rows if isinstance(rows, int) else None,
                             want_sha=led.get("sha256") if led.get("status") == "ok" else None))
    for e in _entries(top["shards"].path, skips)[1] if "shards" in top else []:
        m = _SHARD_FILE.match(e.name)
        if not m:
            skips["other_files"] += 1
            continue
        if leagues and m.group(1) not in leagues:
            continue
        out.append(_cand(e, f"shards/{e.name}", league=m.group(1), sources=("espn",)))
    skips["other_dirs"] += sum(1 for n in top if n not in ("espn", "datasets", "shards"))
    return out


def _scan_frames(root: str, leagues: set[str] | None, skips: dict, notes: list[str]) -> list[_Cand]:
    """Every regular file under the picture-data folder; the night comes from the game folder's YYYYMMDD- prefix."""
    out: list[_Cand] = []
    stack = [("", root)]
    while stack:
        rel_dir, path = stack.pop()
        dirs, files = _entries(path, skips)
        stack.extend((f"{rel_dir}{d.name}/", d.path) for d in dirs)
        for e in files:
            rel = rel_dir + e.name
            top = rel.split("/", 1)[0]
            m = _FRAME_NIGHT.match(top) if "/" in rel else None
            out.append(_cand(e, rel, night=_night_or_none(m.group(1), rel, notes) if m else None, sources=(FRAMES_SOURCE,),
                             count_rows=e.name.endswith(".jsonl")))
    return out


_SCANNERS = {"archive": _scan_archive, "history": _scan_history, "feedlag": _scan_feedlag, "frames": _scan_frames}


def _feedlag_sources(path: str, rel: str) -> tuple[str, ...]:
    """The providers behind a feed-arrival log: from each row's `src` and the start row's `sources`; both when it has neither."""
    found: set[str] = set()
    with open(path, "rb") as f:
        for raw in f:
            if b'"src"' not in raw and b'"sources"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            names = [row["src"]] if isinstance(row.get("src"), str) else []
            if row.get("ev") == "start" and isinstance(row.get("sources"), list):
                names += [x for x in row["sources"] if isinstance(x, str)]
            for n in names:
                if n not in FEEDLAG_SOURCE_NAMES:
                    raise ManifestError(f"{rel}: feed source {n[:30]!r} has no provider mapping (FEEDLAG_SOURCE_NAMES)")
                found.add(FEEDLAG_SOURCE_NAMES[n])
    return tuple(sorted(found or set(FEEDLAG_SOURCE_NAMES.values())))


# --- building ---------------------------------------------------------------------------------------------------

def _derive(files: list[dict]) -> dict:
    """Everything in a manifest that follows from its file list; build writes it and verify recomputes it."""
    counts = {"locked": 0, "tuning": 0, "none": 0, "unknown": 0}
    nights: dict[str, dict] = {}
    for f in files:
        counts[f["fence"]] = counts.get(f["fence"], 0) + 1
        if f["night"]:
            n = nights.setdefault(f["night"], {"fence": f["fence"], "files": 0})
            n["files"] += 1
    known = sorted(nights)
    rows = [f["rows"] for f in files if f["rows"] is not None]
    return {
        "fence": {"counts": counts, "train_ok": not (counts["locked"] or counts["tuning"] or counts["unknown"]),
                  "windows": {w.name: [w.start, w.end] for w in fence.FENCES}},
        "leagues": sorted({f["league"] for f in files if f["league"]}),
        "nights": nights,
        "sources": sorted({s for f in files for s in f["sources"]}),
        "totals": {"bytes": sum(f["bytes"] for f in files), "files": len(files), "rows": sum(rows) if rows else None},
        "tree_sha256": evaldata.tree_digest({f["path"]: f["sha256"] for f in files}),
        "window": {"start": known[0], "end": known[-1]} if known else None,
    }


def _licence_block(sources: list[str], licences: Mapping[str, Mapping]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in sources:
        e = licences.get(s)
        if e is None:
            raise ManifestError(f"source {s!r} has no licence entry; add it to docs/datasources/terms.json or scripts/history/licences.py")
        entry = {k: str(e.get(k) or "") for k in LICENCE_KEYS}
        problems = _licence_problems(s, entry)
        if problems:
            raise ManifestError("; ".join(problems))
        out[s] = entry
    return out


def _licence_problems(source: str, entry: Mapping) -> list[str]:
    out = []
    if entry.get("terms_class") == "red":
        out.append(f"source {source} is red (its terms ban bots or AI use): never pinned")
    if entry.get("status") in REFUSED_STATUSES:
        out.append(f"source {source} is {entry.get('status')}: not pinned until its licence is settled")
    if not (entry.get("tag") or entry.get("licence")):
        out.append(f"source {source} has no licence line")
    if entry.get("terms_class") in ("restrictive", "private") and not entry.get("tag"):
        out.append(f"source {source} is {entry.get('terms_class')} and needs its tag")
    return out


def _file_license(sources: tuple[str, ...], block: Mapping[str, Mapping]) -> str:
    return "; ".join(sorted({block[s]["tag"] or block[s]["licence"] for s in sources}))


def build_manifest(kind: str, root: str | Path, tag: str, *, licences: Mapping[str, Mapping], start: object = None, end: object = None,
                   leagues: Any = None, settle_seconds: float = DEFAULT_SETTLE_SECONDS, now: float | None = None) -> tuple[dict, dict]:
    """(manifest, report) for the files of `kind` under `root` that fall in the window.

    start/end are nights (inclusive, any form fence.night_key reads); leagues narrows by league code. `licences` maps each
    source to {terms_class, status, tag, licence}; a source that is missing, red, blocked or unverified is refused.
    The report (skip counts, not part of the manifest) says what was left out. Files younger than settle_seconds are
    refused, because a file still being written would break the pin the moment it grows."""
    if kind not in DATA_KINDS:
        raise ManifestError(f"unknown kind {kind!r}; known: {', '.join(DATA_KINDS)}")
    check_tag(tag)
    lo = fence.night_key(start) if start not in (None, "") else None
    hi = fence.night_key(end) if end not in (None, "") else None
    lgs = {str(x).lower() for x in leagues} if leagues else None
    root_real = os.path.realpath(root)
    if not os.path.isdir(root_real):
        raise ManifestError(f"the {kind} data folder is missing or not a folder")
    skips = {"symlinks": 0, "hidden": 0, "secret_names": 0, "other_files": 0, "other_dirs": 0}
    notes: list[str] = []
    cands = _SCANNERS[kind](root_real, lgs, skips, notes)
    if lo or hi:
        before = len(cands)
        cands = [c for c in cands if c.night and (not lo or c.night >= lo) and (not hi or c.night <= hi)]
        skips["outside_window"] = before - len(cands)
    if not cands:
        raise ManifestError(f"no {kind} files matched (window {lo or '-'}..{hi or '-'}, leagues {sorted(lgs) if lgs else 'all'})")
    cands.sort(key=lambda c: c.rel)
    bad = [f"{c.rel}: {w}" for c in cands for w in _string_problems(c.rel)]
    if bad:
        raise ManifestError("file names that look like addresses or paths cannot be pinned: " + "; ".join(bad[:5]))
    clock = time.time() if now is None else now
    young = [c.rel for c in cands if settle_seconds and c.mtime > clock - settle_seconds]
    if young:
        raise ManifestError(f"{len(young)} file(s) changed in the last {int(settle_seconds)} s and may still be written "
                            f"(first: {young[0]}); wait, or lower settle_seconds if they are finished")
    files: list[dict] = []
    for c in cands:
        sha, size, lines = _digest(c.path, c.count_rows)
        if size != c.size:
            raise ManifestError(f"{c.rel}: changed while it was being hashed")
        if c.want_sha and c.want_sha != sha:
            raise ManifestError(f"{c.rel}: sha256 differs from the fetch ledger (the file was damaged after it was written)")
        sources = _feedlag_sources(c.path, c.rel) if c.feed_sources else c.sources
        files.append({"bytes": size, "fence": fence_status(c.night), "league": c.league, "license": "", "night": c.night, "path": c.rel,
                      "rows": c.rows if c.rows is not None else lines, "sha256": sha, "sources": list(sources)})
    block = _licence_block(sorted({s for f in files for s in f["sources"]}), licences)
    for f in files:
        f["license"] = _file_license(tuple(f["sources"]), block)
    doc: dict[str, Any] = {
        "format": MANIFEST_FORMAT, "tag": tag, "kind": kind, "private": kind in PRIVATE_DATA_KINDS,
        "license": "; ".join(sorted({block[s]["tag"] or block[s]["licence"] for s in block})), "licences": block,
        "selection": {"end": hi, "leagues": sorted(lgs) if lgs else None, "start": lo},
        "notes": sorted(notes)[:SHOWN_PROBLEMS], **_derive(files), "files": files}
    doc["manifest_sha256"] = manifest_hash(doc)
    bad = scrub_problems(doc)
    if bad:
        raise ManifestError("the manifest would hold something that must never be committed: " + "; ".join(bad[:5]))
    return doc, {"skipped": skips, "files": len(files)}


# --- checking ---------------------------------------------------------------------------------------------------

def check_manifest(doc: Any) -> list[str]:
    """Problems found in the manifest itself (no disk): format, hashes, derived sections, fence status, licences, content."""
    if not isinstance(doc, dict) or doc.get("format") != MANIFEST_FORMAT:
        return [f"not a {MANIFEST_FORMAT} document"]
    out: list[str] = []
    for k in ("tag", "kind", "private", "files", "licences", "tree_sha256", "manifest_sha256", "sources", "fence", "totals"):
        if k not in doc:
            out.append(f"missing field {k!r}")
    if out:
        return out
    try:
        check_tag(doc["tag"])
    except ManifestError as e:
        out.append(str(e))
    if doc["kind"] not in DATA_KINDS:
        out.append(f"unknown kind {doc['kind']!r}")
    elif doc["private"] != (doc["kind"] in PRIVATE_DATA_KINDS):
        out.append(f"private flag {doc['private']!r} is wrong for kind {doc['kind']}")
    files = doc["files"]
    if not isinstance(files, list) or not files:
        return out + ["no files listed"]
    seen: set[str] = set()
    good: list[dict] = []
    for i, f in enumerate(files):
        if not isinstance(f, dict) or sorted(f) != sorted(FILE_KEYS):
            out.append(f"files[{i}]: wrong fields (want {', '.join(FILE_KEYS)})")
            continue
        if not _safe_rel(f["path"]):
            out.append(f"files[{i}]: unsafe path {str(f['path'])[:60]!r}")
            continue
        if f["path"] in seen:
            out.append(f"{f['path']}: listed twice")
        seen.add(f["path"])
        if not (isinstance(f["sha256"], str) and _SHA.match(f["sha256"])) or not isinstance(f["bytes"], int) or f["bytes"] < 0:
            out.append(f"{f['path']}: bad sha256 or size")
            continue
        if f["night"] is not None and not _is_night(f["night"]):
            out.append(f"{f['path']}: night is not YYYYMMDD")
            continue
        if f["fence"] != fence_status(f["night"]):
            out.append(f"{f['path']}: fence status {f['fence']!r} is stale (fence.py says {fence_status(f['night'])!r}); rebuild under a new tag")
        if not f["sources"] or any(s not in doc["licences"] for s in f["sources"]) or not f["license"]:
            out.append(f"{f['path']}: no licence line for its source")
        good.append(f)
    if [f["path"] for f in good] != sorted(f["path"] for f in good):
        out.append("files are not sorted by path")
    if len(good) == len(files):
        for k, want in _derive(files).items():
            if doc.get(k) != want:
                out.append(f"{k} does not match the file list" + (" (the fence windows changed; rebuild under a new tag)" if k == "fence" else ""))
    for s, e in doc["licences"].items():
        out.extend(_licence_problems(s, e))
    if doc["manifest_sha256"] != manifest_hash(doc):
        out.append("manifest_sha256 does not match the content (the manifest was edited)")
    out.extend(scrub_problems(doc, limit=SHOWN_PROBLEMS))
    return out


def _is_night(x: object) -> bool:
    """True for a canonical YYYYMMDD night."""
    try:
        return isinstance(x, str) and fence.night_key(x) == x
    except ValueError:
        return False


def verify_files(doc: Mapping, root: str | Path) -> list[str]:
    """Problems found re-hashing every listed file under root: missing, symlink, outside the folder, wrong size, wrong sha256."""
    root_real = os.path.realpath(root)
    if not os.path.isdir(root_real):
        return ["the data folder is missing or not a folder"]
    out: list[str] = []
    for f in doc["files"]:
        if not (isinstance(f, dict) and _safe_rel(f.get("path")) and isinstance(f.get("bytes"), int) and isinstance(f.get("sha256"), str)):
            continue                      # check_manifest reports a malformed entry; it cannot be hashed
        rel = f["path"]
        full = os.path.join(root_real, *rel.split("/"))
        try:
            st = os.lstat(full)
        except (FileNotFoundError, NotADirectoryError):
            out.append(f"{rel}: missing")
            continue
        if stat.S_ISLNK(st.st_mode):
            out.append(f"{rel}: is a symlink (pinned files are regular files)")
        elif not stat.S_ISREG(st.st_mode):
            out.append(f"{rel}: is not a regular file")
        elif not _within(root_real, full):
            out.append(f"{rel}: resolves outside the data folder")
        elif st.st_size != f["bytes"]:
            out.append(f"{rel}: size {st.st_size} != pinned {f['bytes']}")
        else:
            sha = _digest(full, False)[0]
            if sha != f["sha256"]:
                out.append(f"{rel}: sha256 {sha[:12]}.. != pinned {f['sha256'][:12]}..")
    return out


def verify_manifest(doc: Any, root: str | Path) -> list[str]:
    """check_manifest + verify_files: [] only when the manifest is intact and every file re-hashes to it."""
    out = check_manifest(doc)
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        return out
    return out + verify_files(doc, root)


# --- reading, writing, finding ------------------------------------------------------------------------------

def read_manifest(path: str | Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read manifest {Path(path).name}: {type(e).__name__}") from e
    if not isinstance(doc, dict):
        raise ManifestError(f"{Path(path).name} is not a manifest")
    return doc


def find_manifest(tag: str, directory: str | Path | None = None) -> Path:
    check_tag(tag)
    d = Path(directory) if directory else default_manifest_dir()
    found = [p for p in (d / f"{tag}{MANIFEST_SUFFIX}", d / f"{tag}{PRIVATE_MANIFEST_SUFFIX}") if p.is_file()]
    if not found:
        raise ManifestError(f"no manifest for tag {tag!r} in the manifests folder; build one with scripts/data_manifest.py build")
    if len(found) > 1:
        raise ManifestError(f"tag {tag!r} has both a public and a private manifest; remove one")
    return found[0]


def assert_not_committable(path: str | Path, *, directory: bool = False) -> None:
    """ManifestError unless git would ignore `path` (or it sits outside any git repo). Private data lists must never be committable."""
    p = Path(path).absolute()
    probe = p / "LAYOUT.json" if directory else p
    start = p if directory else p.parent
    while not start.exists():
        start = start.parent
    try:
        top = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if top.returncode != 0:
            return
        ign = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", str(probe)], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise ManifestError(f"cannot tell whether {p.name} would be committed ({type(e).__name__}); refusing") from e
    if ign.returncode != 0:
        raise ManifestError(f"refusing to write {p.name}: it is inside a git repo and not git-ignored, so it could be committed. "
                            "Use data/manifests/ (ignored) or a folder outside the repo.")


def write_manifest(doc: Mapping, directory: str | Path | None = None) -> tuple[Path, bool]:
    """(path, created). A tag is a pin: writing it again with different content is refused; identical content is a no-op."""
    check_tag(doc.get("tag"))
    d = Path(directory) if directory else default_manifest_dir()
    dest = d / f"{doc['tag']}{PRIVATE_MANIFEST_SUFFIX if doc.get('private') else MANIFEST_SUFFIX}"
    if doc.get("private"):
        assert_not_committable(dest)
    text = render(doc)
    if dest.exists():
        if dest.read_text(encoding="utf-8") == text:
            return dest, False
        raise ManifestError(f"tag {doc['tag']!r} is already pinned to different content; a new version needs a new tag")
    d.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest, True


# --- loading for a notebook ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class PinnedData:
    tag: str
    kind: str
    manifest_sha256: str
    tree_sha256: str
    window: dict | None
    root: Path
    records: tuple

    def paths(self, *, night: object = None, league: str | None = None, source: str | None = None, suffix: str | None = None) -> list[Path]:
        """Absolute paths of the pinned files, optionally narrowed by night, league, source or file suffix."""
        want = fence.night_key(night) if night is not None else None
        return [self.root.joinpath(*r["path"].split("/")) for r in self.records
                if (want is None or r["night"] == want) and (league is None or r["league"] == league)
                and (source is None or source in r["sources"]) and (suffix is None or r["path"].endswith(suffix))]

    @property
    def files(self) -> list[Path]:
        return self.paths()

    @property
    def nights(self) -> list[str]:
        return sorted({r["night"] for r in self.records if r["night"]})


def load_pinned(tag_or_path: str | Path, *, root: str | Path | None = None, manifests_dir: str | Path | None = None,
                for_training: bool = False, allow_unknown_nights: bool = False) -> PinnedData:
    """The pinned version, or ManifestError. Every file is re-hashed against the manifest first: a changed, missing or
    extra-linked file refuses the whole tag. A manifest file path may be given instead of a tag (a bundle on Kaggle).

    for_training=True also refuses (fence.FenceError) a version holding any night in a fence (locked or tuning), and
    (ManifestError) one with files whose night is unknown unless allow_unknown_nights=True."""
    given = str(tag_or_path)
    path = Path(given) if given.endswith(".json") else find_manifest(given, manifests_dir)
    doc = read_manifest(path)
    kind = doc.get("kind")
    data_root = Path(root) if root else (default_root(kind) if kind in DATA_KINDS else None)
    problems = verify_manifest(doc, data_root) if data_root is not None else check_manifest(doc)
    if problems:
        more = f" (+{len(problems) - SHOWN_PROBLEMS} more)" if len(problems) > SHOWN_PROBLEMS else ""
        raise ManifestError(f"REFUSED {doc.get('tag', given)}: the data does not match its manifest: " + "; ".join(problems[:SHOWN_PROBLEMS]) + more)
    if for_training:
        fence.assert_train_nights([r["night"] for r in doc["files"] if r["night"]])
        unknown = sum(1 for r in doc["files"] if r["night"] is None)
        if unknown and not allow_unknown_nights:
            raise ManifestError(f"REFUSED {doc['tag']} for training: {unknown} file(s) have no night, so nothing proves they are outside "
                                "the fences (allow_unknown_nights=True if the fetcher already kept fenced nights out)")
    return PinnedData(tag=doc["tag"], kind=doc["kind"], manifest_sha256=doc["manifest_sha256"], tree_sha256=doc["tree_sha256"],
                      window=doc.get("window"), root=Path(os.path.realpath(data_root)), records=tuple(doc["files"]))


# --- comparing --------------------------------------------------------------------------------------------------

def diff_manifests(a: Mapping, b: Mapping) -> dict:
    """What changed from manifest a to manifest b: files added, removed, changed (same path, new content) and moved (same content, new path)."""
    fa, fb = {f["path"]: f for f in a["files"]}, {f["path"]: f for f in b["files"]}
    added, removed = sorted(set(fb) - set(fa)), sorted(set(fa) - set(fb))
    changed = sorted(p for p in set(fa) & set(fb) if fa[p]["sha256"] != fb[p]["sha256"])
    old_by_sha = {fa[p]["sha256"]: p for p in removed}
    moved = sorted([old_by_sha[fb[p]["sha256"]], p] for p in added if fb[p]["sha256"] in old_by_sha)
    gone = {m[0] for m in moved}
    new = {m[1] for m in moved}
    meta = [k for k in ("kind", "private", "sources", "license", "licences", "window", "selection") if a.get(k) != b.get(k)]
    return {"tags": [a.get("tag"), b.get("tag")], "same": a.get("tree_sha256") == b.get("tree_sha256") and not meta,
            "added": [p for p in added if p not in new], "removed": [p for p in removed if p not in gone], "changed": changed, "moved": moved,
            "unchanged": sum(1 for p in set(fa) & set(fb) if fa[p]["sha256"] == fb[p]["sha256"]), "metadata_changed": meta}


# --- upload bundle plan (dry run) ------------------------------------------------------------------------------

def plan_bundle(doc: Mapping, *, include_private: bool = False) -> dict:
    """The layout of an upload bundle for Kaggle and Drive, as data. Nothing is uploaded or copied; no credentials are read.
    A manifest of private data (frames, picture data) is refused unless include_private is set."""
    if doc.get("private") and not include_private:
        raise ManifestError(f"REFUSED package {doc.get('tag')}: kind {doc.get('kind')} is private data (frames and picture data stay on "
                            "this Mac; nothing in the repo uploads them). Pass --include-private-frames only for a plan Nick asked for.")
    tag = check_tag(doc["tag"])
    slug = re.sub(r"[^a-z0-9]+", "-", f"redzone-{tag}").strip("-")[:50].strip("-")
    dirs: dict[str, dict] = {}
    for f in doc["files"]:
        top = f["path"].split("/", 1)[0] if "/" in f["path"] else "."
        d = dirs.setdefault(top, {"bytes": 0, "files": 0})
        d["bytes"] += f["bytes"]
        d["files"] += 1
    suffix = PRIVATE_MANIFEST_SUFFIX if doc.get("private") else MANIFEST_SUFFIX
    plan = {
        "dry_run": True, "uploaded": False, "needs_login": False, "tag": tag, "kind": doc["kind"], "private": bool(doc.get("private")),
        "manifest_sha256": doc["manifest_sha256"], "files": doc["totals"]["files"], "bytes": doc["totals"]["bytes"], "dirs": dict(sorted(dirs.items())),
        "layout": {"manifest": f"{tag}{suffix}", "metadata": "dataset-metadata.json", "data_prefix": "data/",
                   "load": f"load_pinned('{tag}', manifests_dir=<bundle folder>, root=<bundle folder>/data)"},
        "kaggle": {"dataset-metadata.json": {
            "title": f"RedZone {tag}"[:50], "id": f"KAGGLE_USERNAME/{slug}", "licenses": [{"name": "other"}],
            "subtitle": "RedZone data-lake version, private",
            "description": f"{doc['license']}. Version pinned by manifest {doc['manifest_sha256'][:12]}; verify with load_pinned before use."}},
        "drive": {"folder": f"RedZone/data-lake/{doc['kind']}/{tag}"},
        "next_steps": ["Kaggle: needs Nick's API credential (decision N1); Drive: needs the sync decision (N6). Neither is used here.",
                       "Create the dataset private; copy data/ and the manifest into the bundle folder; upload by hand."],
    }
    bad = scrub_problems(plan)
    if bad:
        raise ManifestError("the bundle plan would hold something that must never be committed: " + "; ".join(bad[:5]))
    return plan


def write_bundle_skeleton(doc: Mapping, plan: Mapping, out: str | Path) -> list[str]:
    """Write dataset-metadata.json, the manifest copy and LAYOUT.json into `out` (a git-ignored folder). No data is copied."""
    out = Path(out)
    assert_not_committable(out, directory=True)
    out.mkdir(parents=True, exist_ok=True)
    meta = plan["kaggle"]["dataset-metadata.json"]
    (out / plan["layout"]["metadata"]).write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / plan["layout"]["manifest"]).write_text(render(doc), encoding="utf-8")
    (out / "LAYOUT.json").write_text(json.dumps(plan, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return [plan["layout"]["metadata"], plan["layout"]["manifest"], "LAYOUT.json"]
