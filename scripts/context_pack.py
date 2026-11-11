"""Build ONE markdown context pack for pasting into a brand-new AI session.

    python scripts/context_pack.py                 # to stdout
    python scripts/context_pack.py --out pack.md   # to a file
    python scripts/context_pack.py --offline       # skip the GitHub API (open PRs)

Holds: the rules (CLAUDE.md), the live handoff (docs/handoff.md, newest copy from
origin/codex/resume-redzone when this checkout has it), the status board, every decision, the
lessons (titles) plus pending ones from the watchdog, open PRs, the latest scorecards and the
Kaggle state. Stdlib only; every part degrades to a one-line note instead of failing. Never
reads secrets: the GitHub call is unauthenticated (the repo is public).
Why: Builds one context pack to brief a brand-new AI session (docstring, memory systems commit).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from redzone.memory import LESSONS, parse_lessons, read_pending  # noqa: E402

REPO = "BouncySlime1215/redzone-everything"
LIVE_REF = "origin/codex/resume-redzone"   # where the director updates docs/handoff.md
METRICS = ("mid_play_cuts", "entries_mid_play", "plays_missed_entry", "dead_air_pct", "big_seen_pct",
           "big_live_pct", "crunch_live_pct", "your_team_crunch_live_pct", "your_team_big_seen_pct")
STATUS_LINES = 120


def _read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def _git_show(ref: str, rel: str) -> str:
    try:
        r = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def handoff() -> tuple[str, str]:
    live = _git_show(LIVE_REF, "docs/handoff.md")
    return (live, LIVE_REF) if live else (_read("docs/handoff.md"), "working tree")


def status_block(text: str) -> str:
    m = re.search(r"<!-- STATUS:START -->(.*?)<!-- STATUS:END -->", text, re.S)
    return m.group(1).strip() if m else ""


def open_prs(offline: bool) -> str:
    if offline:
        return "_skipped (--offline)_"
    url = f"https://api.github.com/repos/{REPO}/pulls?state=open&per_page=50"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "redzone-context-pack"})
        with urllib.request.urlopen(req, timeout=15) as r:
            prs = json.load(r)
    except Exception as e:   # no network, rate limit: say so, keep going
        return f"_could not list PRs ({type(e).__name__}); run `gh api repos/{REPO}/pulls`_"
    if not prs:
        return "_none_"
    return "\n".join(f"- #{p['number']} {p['title']} (`{p['head']['ref']}` → `{p['base']['ref']}`"
                     f"{', draft' if p.get('draft') else ''})" for p in prs)


def _metrics(card: dict) -> dict:
    for key in ("metrics", "head", "baseline"):
        if isinstance(card.get(key), dict):
            return card[key]
    return card


def scorecards() -> str:
    out = []
    files = sorted((ROOT / "docs").glob("scorecards-*.json"))
    sources = [("docs/gate_baseline.json", "cards")] + [(str(f.relative_to(ROOT)), None) for f in files[-1:]]
    for rel, key in sources:
        try:
            data = json.loads(_read(rel) or "null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            continue
        cards = data.get(key, {}) if key else data
        rows = []
        for league, card in cards.items():
            if not isinstance(card, dict):
                continue
            m = _metrics(card)
            rows.append(f"| {league} | " + " | ".join(str(m.get(k, "")) for k in METRICS) + " |")
        if rows:
            out.append(f"From `{rel}`:\n\n| league | " + " | ".join(METRICS) + " |\n|" + "---|" * (len(METRICS) + 1)
                       + "\n" + "\n".join(rows))
    return "\n\n".join(out) or "_no scorecard files found_"


def kaggle(handoff_text: str) -> str:
    """Bullets (with their wrapped lines) that mention Kaggle, from the live handoff and the board."""
    found: list[str] = []
    for text in (status_block(handoff_text), _read("docs/status.md")):
        bullets: list[str] = []
        for ln in text.splitlines():
            if re.match(r"^\s*([-*]|\d+\.|\|)\s", ln) or not bullets:
                bullets.append(ln.strip())
            else:
                bullets[-1] += " " + ln.strip()
        found += [b for b in bullets if "kaggle" in b.lower() and b not in found]
    return "\n".join(found[:12]) or "_no Kaggle lines in handoff/status_"


def lessons() -> str:
    ls = parse_lessons(_read(str(LESSONS)))
    body = "\n".join(f"- {x.title}: {x.fix}" for x in ls) or "_none_"
    pend = read_pending(ROOT)
    if pend:
        body += "\n\n**Pending lessons from the watchdog** (turn into a lessons.md PR):\n" + "\n".join(
            f"- {p.get('action')}: {p.get('signature') or p.get('why')} → {p.get('result')}" for p in pend)
    return body


def build(offline: bool = False) -> str:
    h, src = handoff()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "\n".join(_read("docs/status.md").splitlines()[:STATUS_LINES])
    parts = [
        f"# RedZone context pack ({now})\n\nPaste this whole message into a new AI session. Repo `{REPO}`; "
        "PRs target `integration/next`. Read the rules first; they are not optional.",
        "## Rules (CLAUDE.md)\n\n" + (_read("CLAUDE.md") or "_missing_"),
        f"## Handoff (docs/handoff.md, from {src})\n\n" + (h or "_missing_"),
        f"## Status board (docs/status.md, first {STATUS_LINES} lines)\n\n" + (status or "_missing_"),
        "## Decisions (docs/memory/decisions.md)\n\n" + (_read("docs/memory/decisions.md") or "_missing_"),
        "## Lessons (docs/memory/lessons.md)\n\n" + lessons(),
        "## Open PRs\n\n" + open_prs(offline),
        "## Latest scorecards\n\n" + scorecards(),
        "## Kaggle state\n\n" + kaggle(h),
    ]
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    text = build(offline=a.offline)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out} ({len(text) // 1024} KB)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
