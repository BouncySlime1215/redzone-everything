"""Answer "why?" from the records RedZone already keeps. Stdlib only, read-only.

    python scripts/why.py 20:42                      # what happened around 8:42 PM today (±2 min): every state
    python scripts/why.py 20:42 --date 2026-11-07    #   move with its reason, jumps with their vetoes, standby rows
    python scripts/why.py --machine control.standby  # recent moves of one machine, with why (survives restarts)
    python scripts/why.py redzone/decider/cuts.py    # decisions and lessons that mention a file or config key
    python scripts/why.py buffered_jump.keep_standby_s
    python scripts/why.py --night 2026-11-07          # the night in numbers: jumps, why they failed or were vetoed,
                                                      #   what held the TV, which state moves happened most and why

Sources: data/transitions.jsonl (redzone/fsm.py), data/jumps.jsonl (each jump and its veto), data/jump_status.jsonl
(who owned the TV, holds), data/buffer.jsonl (standby scrub/hold), docs/memory/decisions.md, docs/memory/lessons.md.
Rows carry `sha` (the running commit) when the app wrote it, so a live surprise maps to exact code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ("transitions.jsonl", "jumps.jsonl", "jump_status.jsonl", "buffer.jsonl")


def rows(path: Path, t0: float, t1: float) -> list[dict]:
    out = []
    for p in (Path(str(path) + ".1"), path):
        try:
            for line in p.read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ts = r.get("ts")
                if isinstance(ts, (int, float)) and t0 <= ts <= t1:
                    r["_src"] = path.name
                    out.append(r)
        except OSError:
            continue
    return out


def describe(r: dict) -> str:
    src = r.get("_src")
    sha = f" @{r['sha']}" if r.get("sha") else (f" @{r['commit']}" if r.get("commit") else "")
    if src == "transitions.jsonl":
        key = f"[{r['key']}]" if r.get("key") else ""
        return f"{r.get('machine')}{key}: {r.get('old')} -> {r.get('new')}  because {r.get('why') or '?'}{sha}"
    if src == "jumps.jsonl":
        if r.get("ok"):
            what = f"JUMP {r.get('from')} -> {r.get('to')} ok in {r.get('took_s')} s"
        else:
            what = f"JUMP {r.get('from')} -> {r.get('to')} FAILED ({r.get('fail')}: {r.get('fail_detail', '')})"
        veto = f"; veto {r['veto']}" if r.get("veto") else ""
        r_ = r.get("reason") or r.get("why") or r.get("kind")
        reason = f"; reason {r_}" if r_ else ""
        return f"{what}{veto}{reason}{sha}"
    if src == "jump_status.jsonl":
        holds = ", ".join(f"{k}={v}" for k, v in (r.get("holds") or {}).items())
        return (f"status: screen {r.get('on_screen')}, owner {r.get('owner', '?')} ({r.get('owner_why', '')}), "
                f"standby {r.get('standby')} ready={r.get('standby_ready')}, pending {r.get('pending')}"
                f"{'; holds ' + holds if holds else ''}{sha}")
    if src == "buffer.jsonl":
        return f"buffer: {r.get('event') or r.get('kind') or r.get('what')} {r.get('target', '')} " \
               f"{r.get('behind_s', r.get('held_s', ''))}".rstrip()
    return json.dumps(r)


def timeline(data: Path, at: float, window: float) -> list[str]:
    allrows = []
    for name in TIMELINE:
        allrows += rows(data / name, at - window, at + window)
    allrows.sort(key=lambda r: r["ts"])
    return [f"{datetime.fromtimestamp(r['ts']).strftime('%H:%M:%S')}  {describe(r)}" for r in allrows]


def entries(path: Path) -> list[str]:
    """Split a memory file into entries: a '- ' line (or '## ' heading block) plus its indented continuation."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out, cur = [], []
    for ln in lines:
        if ln.startswith("- ") or ln.startswith("## ") or ln.startswith("### "):
            if cur:
                out.append("\n".join(cur))
            cur = [ln]
        elif cur and (ln.startswith("  ") or not ln.strip()):
            cur.append(ln)
        else:
            if cur:
                out.append("\n".join(cur))
            cur = []
    if cur:
        out.append("\n".join(cur))
    return [e.strip() for e in out if e.strip()]


def mentions(root: Path, needle: str, limit: int) -> list[str]:
    pat = re.compile(re.escape(needle), re.I)
    alt = Path(needle).stem if "/" in needle or needle.endswith(".py") else None
    pat2 = re.compile(rf"\b{re.escape(alt)}\b", re.I) if alt and len(alt) > 3 else None
    hits = []
    for name in ("docs/memory/decisions.md", "docs/memory/lessons.md"):
        for e in entries(root / name):
            if pat.search(e) or (pat2 and pat2.search(e)):
                hits.append(f"[{name.split('/')[-1]}] {e}")
    hits.sort(key=lambda h: re.search(r"20\d\d-\d\d-\d\d", h).group(0) if re.search(r"20\d\d-\d\d-\d\d", h) else "",
              reverse=True)
    return hits[:limit]


def night(data: Path, date: str) -> list[str]:
    """One night (6 AM to 6 AM) summarised from the same logs: what happened and the main reasons."""
    from collections import Counter
    d = datetime.strptime(date, "%Y-%m-%d")
    t0 = d.replace(hour=6).timestamp()
    t1 = t0 + 86400
    jumps = rows(data / "jumps.jsonl", t0, t1)
    ok = [r for r in jumps if r.get("ok")]
    fails = Counter(r.get("fail", "other") for r in jumps if not r.get("ok"))
    vetoes = Counter(r["veto"] for r in jumps if r.get("veto"))
    holds: Counter = Counter()
    prev: dict = {}
    for r in rows(data / "jump_status.jsonl", t0, t1):
        cur = r.get("holds") or {}
        for k, v in cur.items():
            p = prev.get(k, 0)
            holds[k] += v - p if v >= p else v       # counters reset at each landing
        prev = cur
    owners = Counter(r.get("owner", "?") for r in rows(data / "jump_status.jsonl", t0, t1))
    moves = rows(data / "transitions.jsonl", t0, t1)
    by_machine = Counter(r.get("machine") for r in moves)
    reasons = Counter(f"{r.get('machine')}: -> {r.get('new')} because {r.get('why')}" for r in moves)
    shas = sorted({r.get("sha") or r.get("commit") for r in jumps + moves if r.get("sha") or r.get("commit")})
    out = [f"Night of {date} (6 AM to 6 AM)",
           f"- jumps: {len(ok)} ok, {sum(fails.values())} failed"
           + (f" ({', '.join(f'{k} {v}' for k, v in fails.most_common())})" if fails else ""),
           "- vetoed at the flip: " + (", ".join(f"{k} {v}" for k, v in vetoes.most_common()) or "none"),
           "- what held the TV (hold ticks): " + (", ".join(f"{k} {v}" for k, v in holds.most_common(6)) or "none"),
           "- who owned the TV (status rows): " + (", ".join(f"{k} {v}" for k, v in owners.most_common()) or "none"),
           f"- state moves: {len(moves)}; busiest: " + (", ".join(f"{k} {v}" for k, v in by_machine.most_common(5)) or "none"),
           "- most common reasons:"]
    out += [f"    {v:>4}  {k}" for k, v in reasons.most_common(8)] or ["    none"]
    out.append(f"- code versions seen: {', '.join(shas) if shas else 'unknown (rows written before sha stamping)'}")
    return out


def parse_at(hhmm: str, date: str | None) -> float:
    d = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.now().date()
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return datetime(d.year, d.month, d.day, h, m).timestamp()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", help="HH:MM, a file path, or a config key")
    ap.add_argument("--date")
    ap.add_argument("--window", type=float, default=120.0, help="seconds either side (default 120)")
    ap.add_argument("--machine")
    ap.add_argument("--night", metavar="YYYY-MM-DD", help="summarise one night (6 AM to 6 AM)")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)

    if a.night:
        print("\n".join(night(a.data, a.night)))
        return 0
    if a.machine:
        now = time.time()
        rs = [r for r in rows(a.data / "transitions.jsonl", 0, now + 1)
              if r.get("machine") == a.machine or str(r.get("machine", "")).startswith(a.machine + ".")]
        for r in rs[-a.limit * 4:]:
            print(f"{datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M:%S')}  {describe(r)}")
        if not rs:
            print(f"no saved moves for {a.machine} (data/transitions.jsonl is written by the running app)")
        return 0
    if not a.what:
        ap.print_help()
        return 2
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", a.what):
        lines = timeline(a.data, parse_at(a.what, a.date), a.window)
        print("\n".join(lines) if lines else "nothing recorded in that window")
        return 0
    hits = mentions(a.root, a.what, a.limit)
    print("\n\n".join(hits) if hits else f"no decision or lesson mentions {a.what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
