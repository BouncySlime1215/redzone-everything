#!/usr/bin/env python3
"""SessionStart (startup, resume, after /compact): print a few facts so a fresh or compacted session
starts from the current state. Facts, not orders (hooks docs: imperative injected text can trip
prompt-injection defenses). Stdlib only; any error prints nothing."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])


def git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def main() -> None:
    lines = ["RedZone session facts (from .claude/hooks/session_facts.py):"]
    lines.append(f"- branch {git('rev-parse', '--abbrev-ref', 'HEAD') or '?'} at {git('rev-parse', '--short', 'HEAD') or '?'}")
    h = ROOT / "docs" / "handoff.md"
    if h.exists():
        lines.append(f"- docs/handoff.md last changed {(time.time() - h.stat().st_mtime) / 3600:.0f} h ago")
    claims = []
    for p in sorted((ROOT / "docs" / "claims").glob("*.json")):
        try:
            c = json.loads(p.read_text())
            if float(c.get("expires", 0)) > time.time():
                claims.append(f"{c.get('card', p.stem)} ({', '.join(c.get('files', [])[:3])})")
        except Exception:
            continue
    lines.append(f"- open claims: {'; '.join(claims) if claims else 'none'} (docs/claims/, scripts/claims.py)")
    lines.append("- hard rules are enforced by .claude/hooks/guard.py (G1-G7) and CI; docs/coordination.md has the protocol")
    lines.append("- Rule #1 (never join mid-play) is checked by CI counters, not by the author's table")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
