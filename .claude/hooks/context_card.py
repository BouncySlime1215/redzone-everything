#!/usr/bin/env python3
"""PostToolUse on Read: a short card saying why a source file exists, what it routes to, what relies on it.

Why: Nick's requirement, "each time an AI views code it should understand why that code exists, where it
routes to, and what relies on it" (process gate G-07, finding W1-CONTEXT, docs/agents/context/2026-11-08-code-context-on-read.md).

Stdlib only, no network, never imports redzone. Any error prints nothing and exits 0, and only
additionalContext is returned (never a permission decision), so the card cannot block or approve a tool.
Fires once per file per session (per agent inside a session) for *.py under redzone/, scripts/, agent/.
The text comes from committed repo files, so it is treated as data: control characters and tag-like
markup are removed.

    (hook)  stdin = the PostToolUse JSON              -> prints the hook JSON or nothing
    python3 .claude/hooks/context_card.py --show redzone/control.py    # print one card, for a person
    python3 .claude/hooks/context_card.py --reset     # SessionStart "compact": forget what was carded

Kill switch: the file data/.ctx_cards_off. Canary (does the model really receive the card? W1-CONTEXT T1):
set RZ_CARD_CANARY=1 or create data/.ctx_cards_canary; the card gains "CANARY  CANARY-<nonce>" and the
same nonce is logged to data/ctx_cards.jsonl, so a fresh session can be asked to quote it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

MAX_CHARS = 1800
MAX_LINE = 200
MAX_LINES = 15
MIN_FILE_LINES = 20
MIN_STEM = 5
MAX_LOOSE_HITS = 8
SOURCE_DIRS = ("redzone/", "scripts/", "agent/")
RULE1_PATHS = ("redzone/decider/", "redzone/control.py", "redzone/standby.py", "redzone/engine.py",
               "redzone/lab/leverage.py")
RULE1_TAGS = ("Mid-play entries", "Landings in dead time", "Entries at a possession start")
# Lines dropped first when a card runs over budget, last label dropped first. The others always stay.
DROP_ORDER = ("MAP", "GIT", "DECIDED", "USES", "DATA", "MACHINE", "USED-BY", "TESTS")
MEMORY_FILES = ("docs/memory/decisions.md", "docs/memory/lessons.md")
_DOC = re.compile(r"""\A(?:\s*(?:\#[^\n]*)?\n)*\s*[rRuU]?("{3}|'{3})(.*?)\1""", re.S)
_TAG = re.compile(r"</?[A-Za-z][\w:-]*(?:\s[^<>]*)?/?>")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DATE = re.compile(r"20\d\d-\d\d-\d\d")
_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import[ \t]+(\([^)]*\)|[^\n#]+)|import[ \t]+([^\n#]+))", re.M)


def clean(text: str) -> str:
    """One safe line: no tags, no control characters, no markdown emphasis."""
    return " ".join(_CTRL.sub(" ", _TAG.sub("", text)).replace("**", "").split())


def clip(text: str, n: int) -> str:
    text = clean(text)
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def repo_root(path: Path) -> Path | None:
    """The nearest ancestor that is a RedZone checkout, so a card also works inside a git worktree."""
    for p in path.parents:
        if (p / "docs" / "citymap.json").is_file() and (p / "scripts" / "citymap.py").is_file():
            return p
    return None


def docstring(src: str) -> str:
    """The module docstring without parsing the file: a 5,000-line ast.parse would eat the latency budget."""
    m = _DOC.match(src)
    return m.group(2).strip() if m else ""


def why_line(doc: str) -> str | None:
    lines = doc.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Why:"):
            parts = [ln.strip()[4:].strip()]
            for nxt in lines[i + 1:]:      # a Why may wrap until the next blank line or Label:
                if not nxt.strip() or re.match(r"\s*[A-Z][\w -]{0,20}:\s", nxt):
                    break
                parts.append(nxt.strip())
            return " ".join(parts)
    return None


def short(path: str) -> str:
    """A path that stays unique: only the redzone/ prefix goes, so demo/engine.py and engine.py differ."""
    return path[len("redzone/"):] if path.startswith("redzone/") else path


def ranked(paths: list[str]) -> list[str]:
    """Production modules before operator scripts, then shortest (closest to the core) first."""
    return sorted(paths, key=lambda p: (p.startswith("scripts/"), p.count("/"), p))


def listing(paths: list[str], show: int) -> str:
    items = [short(p) for p in ranked(paths)]
    return ", ".join(items[:show]) + (f" +{len(items) - show}" if len(items) > show else "")


def importers(module: dict) -> list[str]:
    return [o for o in module.get("outputs", []) if o.endswith(".py")]


def transitive(mods: dict, path: str) -> int:
    seen, frontier = {path}, [path]
    while frontier:
        frontier = [i for f in frontier if f in mods for i in importers(mods[f]) if i not in seen]
        seen.update(frontier)
    return len(seen) - 1


def memory_hits(root: Path, rel: str) -> list[tuple[str, str]]:
    """Newest (date, text) entries about this file. Records rarely name files, so also `stem.attr` code
    references, and the bare stem only when it is rare enough to mean this module ("control" is noise)."""
    from importlib import util
    spec = util.spec_from_file_location("rz_why", root / "scripts" / "why.py")
    why = util.module_from_spec(spec)
    spec.loader.exec_module(why)
    stem = Path(rel).stem
    exact = re.compile(rf"(?<![\w/]){re.escape(rel)}\b|(?<![\w.]){re.escape(Path(rel).name)}\b|"
                       rf"(?<![\w.]){re.escape(stem)}\.[A-Za-z_]\w")
    word = re.compile(rf"\b{re.escape(stem)}\b", re.I)
    entries = [e for name in MEMORY_FILES for e in why.entries(root / name)]
    picked = [e for e in entries if exact.search(e)]
    if not picked and len(stem) >= MIN_STEM:
        loose = [e for e in entries if word.search(e)]
        picked = loose if len(loose) <= MAX_LOOSE_HITS else []
    hits = []
    for entry in picked:
        found = _DATE.search(entry)
        text = entry.lstrip("-# *")
        if found and text.startswith(found.group(0)):       # the date is printed once, on its own
            text = text[len(found.group(0)):].lstrip(" :*")
        hits.append((found.group(0) if found else "", text))
    hits.sort(key=lambda h: h[0], reverse=True)
    return hits


def cache_dir() -> Path:
    d = Path(os.environ.get("TMPDIR") or "/tmp") / "rz_cards"      # tempfile's import costs ~10 ms
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_index(root: Path, names: dict[str, str]) -> dict[str, list[str]]:
    """module path -> tests that import it directly (regex over imports; cached until a test file changes)."""
    files = [p for p in sorted((root / "tests").rglob("test_*.py")) if "fixtures" not in p.parts]
    sig = hashlib.sha1(repr([(p.name, p.stat().st_mtime_ns) for p in files]).encode()).hexdigest()
    cached = cache_dir() / f"tests-{hashlib.sha1(str(root).encode()).hexdigest()[:12]}.json"
    try:
        data = json.loads(cached.read_text())
        if data["sig"] == sig:
            return data["index"]
    except (OSError, ValueError, KeyError):
        pass
    index: dict[str, set[str]] = {}
    for p in files:
        rel = p.relative_to(root).as_posix()
        for src, members, plain in _IMPORT.findall(p.read_text(encoding="utf-8", errors="replace")):
            targets = [s.strip().split(" ")[0] for s in plain.split(",")] if plain else [src]
            if src:
                targets += [f"{src}.{m.strip().split(' ')[0]}" for m in members.strip("()").replace("\n", " ").split(",")]
            for t in targets:
                if t in names:
                    index.setdefault(names[t], set()).add(rel)
    result = {k: sorted(v) for k, v in index.items()}
    atomic_write(cached, json.dumps({"sig": sig, "index": result}))
    return result


def covering_tests(root: Path, mods: dict, rel: str) -> tuple[int, list[str]]:
    names = {}
    for p in mods:
        parts = p[:-3].split("/")
        names[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = p
    direct = test_index(root, names).get(rel, [])
    stem = Path(rel).stem
    named = sorted(p.relative_to(root).as_posix() for p in (root / "tests").glob(f"test_{stem}*.py")
                   if p.stem == f"test_{stem}" or p.stem.startswith(f"test_{stem}_"))
    picks = named + [t for t in direct if t not in named]
    return len(set(direct) | set(named)), picks[:3]


def protected(root: Path, rel: str) -> bool | None:
    """Read G6's own list from guard.py, so this never drifts from the rule. None when it can't be read."""
    try:
        text = (root / ".claude" / "hooks" / "guard.py").read_text()
        found = re.search(r"^PROTECTED = (\[.*?^\])", text, re.S | re.M)
        return any(fnmatch(rel, pat) for pat in re.findall(r'"([^"]+)"', found.group(1)))
    except Exception:
        return None


def git_lines(root: Path, rel: str, n: int = 2) -> list[str]:
    """Latest non-merge commits on the file, with the Why: body line when the commit has one."""
    try:
        out = subprocess.run(["git", "log", f"-{n}", "--no-merges", "--format=%h%x1f%s%x1f%b%x1e", "--", rel],
                             cwd=root, capture_output=True, text=True, timeout=1).stdout
    except Exception:
        return []
    lines = []
    for rec in filter(str.strip, out.split("\x1e")):
        sha, subject, body = (rec.strip().split("\x1f") + ["", ""])[:3]
        why = next((b.split(":", 1)[1] for b in body.splitlines() if b.startswith("Why:")), "")
        lines.append(f"GIT     {sha} {clip(subject, 90)}" + (f" | Why: {clip(why, 80)}" if why else ""))
    return lines


def map_stale(root: Path, rel: str) -> bool:
    """True when the newest commit touching the file or the map left the map alone (mtimes lie in worktrees)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--name-only", "--format=", "--", rel, "docs/citymap.json"], cwd=root,
                             capture_output=True, text=True, timeout=1).stdout.split()
    except Exception:
        return False
    return bool(out) and "docs/citymap.json" not in out


def is_rule1(rel: str, serves: list[str]) -> bool:
    return rel.startswith(RULE1_PATHS) or any(s.startswith(RULE1_TAGS) for s in serves)


def fit(lines: list[str]) -> list[str]:
    """Clip each line and drop whole lines from the bottom of DROP_ORDER until the card fits."""
    lines = [ln if len(ln) <= MAX_LINE else ln[: MAX_LINE - 3] + "..." for ln in lines]

    def size() -> int:
        return sum(len(ln) + 1 for ln in lines)

    for label in DROP_ORDER:
        while (size() > MAX_CHARS or len(lines) > MAX_LINES) and any(ln.startswith(label) for ln in lines):
            last = max(i for i, ln in enumerate(lines) if ln.startswith(label))
            del lines[last]
    return lines


def build(root: Path, rel: str, nonce: str | None = None) -> str | None:
    """The card text for one repo-relative module path, or None when it isn't a card-worthy source file."""
    if not rel.endswith(".py") or not rel.startswith(SOURCE_DIRS):
        return None
    src = (root / rel).read_text(encoding="utf-8", errors="replace")
    if src.count("\n") < MIN_FILE_LINES:
        return None
    doc = docstring(src)
    purpose = clip(doc.split("\n\n")[0], 150) if doc else "NO PURPOSE HEADER (add a module docstring)"
    why = why_line(doc)
    why_text = clip(why, 185) if why else "no Why: header (add one; docs/STANDARD.md section 5)"
    try:
        graph = json.loads((root / "docs" / "citymap.json").read_text())
    except (OSError, ValueError):
        return clip(f"CONTEXT {rel} | map unavailable: run python scripts/citymap.py", MAX_LINE)
    mods = graph.get("modules", {})
    m = mods.get(rel)
    if m is None:
        return "\n".join(fit([f"CONTEXT {rel} | {purpose}", f"WHY     {why_text}",
                              "MAP     new file: not in citymap yet; run python scripts/citymap.py"]))
    lines = [f"CONTEXT {rel} | owner {m.get('owner') or '-'} | {purpose}", f"WHY     {why_text}"]
    for date, text in memory_hits(root, rel)[:2]:
        lines.append(f"DECIDED {date} {clip(text, 165)}")
    used = importers(m)
    if used:
        lines.append(f"USED-BY {len(used)} direct, {transitive(mods, rel)} transitive: {listing(used, 6)}")
    uses = [i for i in m.get("inputs", []) if i.endswith(".py")]
    if uses:
        lines.append(f"USES    {listing(uses, 8)}")
    data = m.get("data", {})
    parts = [f"{k[0]}:{','.join(data[k][:4])}" for k in ("reads", "writes", "uses") if data.get(k)]
    if parts:
        lines.append("DATA    " + " ".join(parts))
    machines = m.get("machines", {})
    running = sorted(set(machines.get("declares", [])) | set(machines.get("drives", [])))
    if running:
        lines.append("MACHINE " + ", ".join(running[:10]) + (f" +{len(running) - 10}" if len(running) > 10 else ""))
    total, picks = covering_tests(root, mods, rel)
    lines.append(f"TESTS   {total} direct; run: python -m pytest -q {' '.join(picks)}" if picks
                 else "TESTS   none import this directly (name one before editing)")
    serves = m.get("serves", [])
    rule1 = is_rule1(rel, serves)
    tags = sorted(serves, key=lambda s: (not s.startswith(RULE1_TAGS), s))[:4]
    prot = protected(root, rel)
    bits = (["RULE#1 never join mid-play"] if rule1 else []) + [clip(t, 34) for t in tags]
    if prot is not None:
        bits.append(f"protected: {'yes (G6)' if prot else 'no'}")
    lines.append("RULES   " + " | ".join(bits or ["none tagged"]))
    lines += git_lines(root, rel)
    stem = Path(rel).stem
    lines.append(f"BEFORE  edit checklist: test named? invariant stated? open PR on it (gh pr list --search {stem})? "
                 "default or gate change: flag OFF + decisions.md line")
    lines.append("DONE    check: " + ("before/after entries_mid_play, plays_missed_entry, dead_air_pct; pytest; ruff"
                                      if rule1 else "the tests above; ruff check"))
    more = f"MORE    python scripts/citymap.py --impact {rel} | python scripts/why.py {rel}"
    if running:
        more += f" | why.py --machine {running[0].split('.')[0]}"
    lines.append(more)
    if map_stale(root, rel):
        lines.append("MAP     stale: this file has a commit newer than the map; run python scripts/citymap.py")
    if nonce:
        lines.insert(1, f"CANARY  CANARY-{nonce}")
    return "\n".join(fit(lines))


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def seen_path(event: dict) -> Path:
    session = re.sub(r"[^\w-]", "_", str(event.get("session_id") or "nosession"))
    agent = re.sub(r"[^\w-]", "_", str(event.get("agent_id") or "main"))
    return cache_dir() / f"seen-{session}__{agent}.json"


def first_time(event: dict, rel: str) -> bool:
    """Record the file and say whether it is new for this session and agent. Old session files are swept."""
    path = seen_path(event)
    try:
        seen = set(json.loads(path.read_text()))
    except (OSError, ValueError):
        seen = set()
    if rel in seen:
        return False
    atomic_write(path, json.dumps(sorted(seen | {rel})))
    cutoff = time.time() - 2 * 86400
    for old in cache_dir().glob("seen-*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue
    return True


def log_row(root: Path, row: dict) -> None:
    try:
        with (root / "data" / "ctx_cards.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def nonce_for(root: Path, event: dict, rel: str) -> str | None:
    if not (os.environ.get("RZ_CARD_CANARY") or (root / "data" / ".ctx_cards_canary").exists()):
        return None
    return hashlib.sha256(f"{event.get('session_id')}:{rel}".encode()).hexdigest()[:8]


def handle(event: dict) -> str | None:
    """The hook JSON to print for one PostToolUse event, or None."""
    t0 = time.perf_counter()
    if event.get("tool_name") != "Read":
        return None
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not file_path:
        return None
    path = Path(file_path)
    path = (path if path.is_absolute() else Path(event.get("cwd") or ".") / path).resolve()
    root = repo_root(path)
    if root is None or (root / "data" / ".ctx_cards_off").exists():
        return None
    rel = path.relative_to(root).as_posix()
    if not rel.endswith(".py") or not rel.startswith(SOURCE_DIRS) or not first_time(event, rel):
        return None
    nonce = nonce_for(root, event, rel)
    text = build(root, rel, nonce)
    if not text:
        return None
    log_row(root, {"ts": round(time.time(), 1), "session": event.get("session_id"), "agent": event.get("agent_id"),
                   "path": rel, "chars": len(text), "ms": round((time.perf_counter() - t0) * 1000), "nonce": nonce})
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})


def reset(event: dict) -> None:
    session = re.sub(r"[^\w-]", "_", str(event.get("session_id") or "nosession"))
    for p in cache_dir().glob(f"seen-{session}__*.json"):
        p.unlink(missing_ok=True)


def main(argv: list[str]) -> None:
    if len(argv) == 2 and argv[0] == "--show":
        path = Path(argv[1]).resolve()
        root = repo_root(path)
        if root:
            print(build(root, path.relative_to(root).as_posix()) or "no card for this file")
        return
    event = json.load(sys.stdin)
    if argv[:1] == ["--reset"]:
        reset(event)
        return
    out = handle(event)
    if out:
        print(out)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        pass
    sys.exit(0)
