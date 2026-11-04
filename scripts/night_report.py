#!/usr/bin/env python3
"""Night health report: one page per night, written every morning to docs/nights/YYYY-MM-DD.md
(cycle 04 build (a), lane 4). No input from Nick.

    python scripts/night_report.py                      # last night (ET), from docs/live-nights/
    python scripts/night_report.py --night 2026-11-04 --live-dir /mnt/project-files/live-nights
    python scripts/night_report.py --force              # ignore the report.nightly flag (manual run)

Reads the night's summary.json (the Mac's supervisor writes data/live_nights/<night>/ after each
night; scripts/publish_live_nights.py puts the aggregates in docs/live-nights/) and writes:
  - last night's North Star lines that live logs can grade (mid-play, landings in dead time,
    press-to-TV), each with its target and PASS / FAIL, and the lines they can't grade yet as PENDING
  - every miss: when (ET), which game, which end of the cut, the grade's reason
  - sync anchor error p50 / p95 per stick, TV switch p95, press-to-TV p50 / p95 and where it goes
  - what the supervisor fixed on its own (rule, times, how many didn't work)
No night collected -> a PENDING page. These lines are one night: they inform, the release gate still
judges the locked set plus fresh nights (release_gate.py).

Behind report.nightly (default OFF): with it off the script writes nothing unless --force.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIVE = ROOT / "docs" / "live-nights"
OUT = ROOT / "docs" / "nights"
DEAD_TIME_BAR = 98.0      # North Star: landings in dead time >= 98% (Nick, Nov 4 10:55)
PRESS_BAR_S = 3.0         # lane 3: press-to-TV p95 <= 3 s on real hardware


def last_night(now: datetime | None = None) -> date:
    """The night that ended this morning (a night runs into the early hours ET)."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("America/New_York"))
    return (now - timedelta(hours=12)).date()


def enabled() -> bool:
    from redzone.config import Config
    try:
        return bool(Config().get("report.nightly", False))
    except Exception:
        return False


def load(night: date, dirs: list[Path]) -> dict | None:
    for d in dirs:
        f = d / night.isoformat() / "summary.json"
        if f.exists():
            try:
                s = json.loads(f.read_text())
                return s if isinstance(s, dict) else None
            except (OSError, ValueError):
                return None
    return None


def _v(x, unit=" s"):
    return "–" if x is None else f"{x}{unit}"


def lines_graded(s: dict) -> list[tuple[str, str, str, str]]:
    """(line, target, value, status) for the North Star lines one live night can grade."""
    out = []
    mid = (s.get("entries_mid_play") or 0) + (s.get("leaves_mid_play") or 0)
    graded = s.get("cuts_graded") or 0
    out.append(("Mid-play cuts + entries", "0", f"{mid} of {graded} cuts",
                ("✅ PASS" if mid == 0 else "❌ FAIL") if graded else "⏳ PENDING"))
    lg, ld = s.get("landings_graded"), s.get("landings_dead")
    if lg:
        pct = round(100.0 * (ld or 0) / lg, 1)
        out.append(("Landings in dead time", f">= {DEAD_TIME_BAR:g}%", f"{pct}% ({ld}/{lg})",
                    "✅ PASS" if pct >= DEAD_TIME_BAR else "❌ FAIL"))
    else:
        out.append(("Landings in dead time", f">= {DEAD_TIME_BAR:g}%", "–", "⏳ PENDING"))
    p = s.get("press_to_tv_p95_s")
    out.append(("Press-to-TV p95", f"<= {PRESS_BAR_S:g} s", f"{_v(p)} (n={s.get('press_samples', 0)})",
                "⏳ PENDING" if p is None else ("✅ PASS" if p <= PRESS_BAR_S else "❌ FAIL")))
    for name in ("Big plays seen live (per league, % of oracle)", "Big plays live or replayed within 3 min"):
        out.append((name, "see North Star", "needs the night's play-by-play graded", "⏳ PENDING"))
    return out


def render(night: date, s: dict | None) -> str:
    md = [f"# Night health: {night.isoformat()}", "",
          "Written by `scripts/night_report.py` from the night's live log (no input from Nick). One night "
          "informs; the release gate judges the locked set plus fresh nights.", ""]
    if not s:
        return "\n".join(md + ["⏳ PENDING: no live night collected for this date "
                               f"(docs/live-nights/{night.isoformat()}/summary.json).", ""])
    md += ["## North Star lines, last night", "", "| line | target | last night | status |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in lines_graded(s)] + [""]

    md += ["## Misses", ""]
    ms = s.get("misses") or []
    if not ms:
        md += ["None: no cut left or entered a play." if s.get("cuts_graded") else "No cuts graded.", ""]
    else:
        md += ["| when (ET) | game | end | reason |", "|---|---|---|---|"]
        md += [f"| {m.get('at') or '?'} | {m.get('game') or '?'} | {m.get('end') or '?'} | {m.get('why') or '–'} |"
               for m in ms] + [""]

    md += ["## Sync and switching", ""]
    sy = s.get("sync_anchor_err") or {}
    if sy:
        md += ["| stick | anchors | error p50 | error p95 |", "|---|---:|---:|---:|"]
        md += [f"| {k} | {v.get('n', 0)} | {_v(v.get('p50_s'))} | {_v(v.get('p95_s'))} |" for k, v in sorted(sy.items())]
        md.append("")
    else:
        md += ["Sync anchor error: no anchors logged.", ""]
    md += [f"- TV switch (SmartThings, confirmed): {s.get('switch_samples', 0)} switches, p95 {_v(s.get('switch_p95_s'))}, "
           f"max {_v(s.get('switch_max_s'))}, unconfirmed {s.get('tv_unverified', 0)}",
           f"- Picture within 3 s of the cut: {s.get('picture_within_3s') or '–'}; cut to picture p95 "
           f"{_v(s.get('picture_p95_s'))}",
           f"- Phone press to TV: {s.get('press_samples', 0)} presses, p50 {_v(s.get('press_to_tv_p50_s'))}, "
           f"p95 {_v(s.get('press_to_tv_p95_s'))}, {s.get('press_no_picture', 0)} never reached a picture"]
    sp = s.get("press_spans") or {}
    if sp:
        md.append("- Where a press's time goes (p50 / p95): " + ", ".join(
            f"{k} {_v(v.get('p50_s'))} / {_v(v.get('p95_s'))}" for k, v in sorted(sp.items())))
    md.append("")

    md += ["## Faults it recovered from on its own", ""]
    ff = s.get("faults_fixed") or {}
    if not ff:
        md += ["Nothing needed fixing.", ""]
    else:
        md += ["| rule | times | didn't work |", "|---|---:|---:|"]
        md += [f"| {r} | {v.get('n', 0)} | {v.get('failed', 0)} |" for r, v in sorted(ff.items(), key=lambda kv: -kv[1].get("n", 0))]
        md.append("")
    win = " to ".join(x for x in (s.get("since"), s.get("until")) if x)
    if win:
        md += [f"Window: {win} (Mac local time).", ""]
    return "\n".join(md)


def write(night: date, dirs: list[Path], out: Path = OUT) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{night.isoformat()}.md"
    p.write_text(render(night, load(night, dirs)))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--night", type=date.fromisoformat, help="YYYY-MM-DD (default: last night, ET)")
    ap.add_argument("--live-dir", type=Path, action="append", default=None,
                    help="folders of <night>/summary.json (default docs/live-nights)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--force", action="store_true", help="write even with report.nightly off")
    a = ap.parse_args(argv)
    if not (a.force or enabled()):
        print("report.nightly is off: nothing written (use --force for a manual run)")
        return 0
    p = write(a.night or last_night(), a.live_dir or [LIVE], a.out)
    print(p.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
