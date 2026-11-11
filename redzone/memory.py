"""Project memory for the AI that repairs RedZone (watchdog, auto-fix brain). Stdlib only.

Reads `docs/memory/lessons.md` (bugs we hit and why) and points at `docs/memory/runbooks/`.
The watchdog uses it two ways:

  before acting  `Memory.hints(errors)` returns the few lessons whose `Signals:` appear in the
                 error signatures, as a short brief for the local model's prompt or a phone note.
  after a fix    `Memory.record(...)` appends a *pending* lesson to data/memory/pending_lessons.jsonl
                 (local, at most one per error per day). Lessons reach `lessons.md` only through a PR;
                 `scripts/context_pack.py` lists the pending ones so the next session can write it.

Why: first added in dcf32544 (2026-11-03) as "Memory systems: CLAUDE.md, docs/memory/, context pack, CI check, watchdog memory hook"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = Path("docs/memory/lessons.md")
RUNBOOKS = Path("docs/memory/runbooks")
PENDING = Path("data/memory/pending_lessons.jsonl")
MAX_HINTS = 3
MAX_BRIEF_CHARS = 1200     # the local model's context is small; keep the brief tiny
RECORD_EVERY_S = 86400.0   # the same error at most once a day in the pending file

_FIELD = re.compile(r"^- \*\*(Symptom|Cause|Fix|Signals):\*\*\s*(.*)$")


@dataclass
class Lesson:
    title: str
    symptom: str = ""
    cause: str = ""
    fix: str = ""
    signals: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"- {self.title}: {self.cause} Fix: {self.fix}".strip()


def parse_lessons(text: str) -> list[Lesson]:
    """`## Title` followed by `- **Symptom:** ...`, `Cause`, `Fix`, `Signals` (comma separated)."""
    out: list[Lesson] = []
    cur: Lesson | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            cur = Lesson(line[3:].strip())
            out.append(cur)
            continue
        m = _FIELD.match(line)
        if cur is None or not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "signals":
            cur.signals = [s.strip() for s in val.split(",") if len(s.strip()) >= 3]
        else:
            setattr(cur, key, val)
    return out


def match(lessons: list[Lesson], errors, k: int = MAX_HINTS) -> list[Lesson]:
    """Lessons whose signals appear (case-insensitive) in any error text, most signals first."""
    texts = [str(e).lower() for e in errors if e]
    if not texts:
        return []
    scored = []
    for i, ls in enumerate(lessons):
        n = sum(1 for s in ls.signals if any(s.lower() in t for t in texts))
        if n:
            scored.append((-n, i, ls))
    return [ls for _, _, ls in sorted(scored)[:k]]


class Memory:
    def __init__(self, root: Path = ROOT, clock=time.time):
        self.root = Path(root)
        self._clock = clock
        self._lessons: list[Lesson] = []
        self._mtime = -1.0
        self._recorded: dict[str, float] = {}

    @property
    def lessons(self) -> list[Lesson]:
        """Re-read lessons.md only when it changed (a deploy brings new lessons)."""
        p = self.root / LESSONS
        try:
            mt = p.stat().st_mtime
            if mt != self._mtime:
                self._lessons, self._mtime = parse_lessons(p.read_text(encoding="utf-8")), mt
        except OSError:
            self._lessons, self._mtime = [], -1.0
        return self._lessons

    def hints(self, errors, max_chars: int = MAX_BRIEF_CHARS) -> str:
        """A short brief of the matching lessons, or '' when none match."""
        found = match(self.lessons, errors)
        if not found:
            return ""
        brief = "Lessons from past fixes (docs/memory/lessons.md):\n" + "\n".join(ls.line() for ls in found)
        return brief[:max_chars]

    def titles(self, errors) -> list[str]:
        return [ls.title for ls in match(self.lessons, errors)]

    def record(self, signature: str, action: str, result: str, why: str = "") -> bool:
        """Append one pending lesson after a fix that worked. Never raises."""
        key = re.sub(r"\d+", "N", signature or why)[:160]
        now = self._clock()
        if now - self._recorded.get(key, -1e18) < RECORD_EVERY_S:
            return False
        row = {"ts": round(now, 1), "signature": (signature or "")[:300], "action": action,
               "result": (result or "")[:200], "why": (why or "")[:300],
               "known": self.titles([signature, why])}
        try:
            p = self.root / PENDING
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            return False
        self._recorded[key] = now
        return True


def read_pending(root: Path = ROOT, limit: int = 20) -> list[dict]:
    """The newest pending lessons (for scripts/context_pack.py)."""
    try:
        lines = (Path(root) / PENDING).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for ln in lines[-limit:]:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return rows


# Results of Watchdog.act() that mean the action did not happen or did not work.
_NOT_DONE = ("fail", "skipped", "dry run", "waiting", "already", "nothing", "no answer",
             "deploy in progress", "noted", "unable", "freed 0 mb")


def worked(action: str, result: str) -> bool:
    return action in ("restart_service", "reconnect_stick", "prune_disk") and not any(
        w in (result or "").lower() for w in _NOT_DONE)
