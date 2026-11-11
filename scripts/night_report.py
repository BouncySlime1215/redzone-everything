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
Each miss links into phone v2's night replay viewer (--phone-url, or the #night=..&at=.. fragment).
No night collected, or a night with no games -> a PENDING page. These lines are one night: they inform, the release gate still
judges the locked set plus fresh nights (release_gate.py).

Behind report.nightly (default OFF): with it off the script writes nothing unless --force.
Why: Writes the nightly health report to docs/nights each morning, with no input from Nick (docstring).
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
    bp = s.get("big_plays") or {}
    for lg, b in sorted(bp.items()):
        n, seen = b.get("n") or 0, b.get("seen") or 0
        out.append((f"Big plays seen live, {lg.upper()}", ">= 95% of oracle",
                    f"{round(100.0 * seen / n, 1)}% ({seen}/{n})" if n else "–",
                    "ℹ️ raw (no live oracle yet)" if n else "⏳ PENDING"))
    if not bp:
        out.append(("Big plays seen live (per league)", ">= 95% of oracle", "no big plays logged", "⏳ PENDING"))
    out.append(("Big plays live or replayed within 3 min", ">= 99%", "replays aren't in the live log yet", "⏳ PENDING"))
    return out


REPLAY_HASH = "#night={night}&at={at}"   # redzone/web/static/rz-v2.js nightFromHash (phone v2's whole-night viewer)


def replay_link(night: date, at: str | None, phone_url: str | None = None) -> str:
    """A miss's link into the phone's night replay viewer. With no phone URL (a page on GitHub: the
    Mac's address stays off the public repo) it is the fragment to add to the phone page's address."""
    if not at:
        return "–"
    frag = REPLAY_HASH.format(night=night.isoformat(), at=at)
    return f"[replay]({phone_url.rstrip('/')}/{frag})" if phone_url else f"`{frag}`"


def no_games(s: dict) -> bool:
    return not any(s.get(k) for k in ("cuts_graded", "switch_samples", "press_samples", "landings_graded"))


def render(night: date, s: dict | None, phone_url: str | None = None) -> str:
    md = [f"# Night health: {night.isoformat()}", "",
          "Written by `scripts/night_report.py` from the night's live log (no input from Nick). One night "
          "informs; the release gate judges the locked set plus fresh nights.", ""]
    if not s:
        return "\n".join(md + ["⏳ PENDING: no live night collected for this date "
                               f"(docs/live-nights/{night.isoformat()}/summary.json).", ""])
    if no_games(s):
        return "\n".join(md + ["⏳ PENDING: no games ran this night (the app was up, nothing was switched or graded).", ""])
    md += ["## North Star lines, last night", "", "| line | target | last night | status |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in lines_graded(s)] + [""]

    md += ["## Misses", ""]
    ms = s.get("misses") or []
    if not ms:
        md += ["None: no cut left or entered a play." if s.get("cuts_graded") else "No cuts graded.", ""]
    else:
        md += ["| when (ET) | game | end | reason | replay |", "|---|---|---|---|---|"]
        md += [f"| {m.get('at') or '?'} | {m.get('game') or '?'} | {m.get('end') or '?'} | {m.get('why') or '–'} | "
               f"{replay_link(night, m.get('at') if m.get('at') != '?' else None, phone_url)} |" for m in ms]
        if not phone_url:
            md.append("\nReplay: add the fragment to the phone page's address (phone v2's Whole night viewer opens at that miss).")
        md.append("")

    md += ["## Sync and switching", ""]
    sy = s.get("sync_anchor_err") or {}
    if sy:
        md += ["| stick | anchors | error p50 | error p95 |", "|---|---:|---:|---:|"]
        md += [f"| {k} | {v.get('n', 0)} | {_v(v.get('p50_s'))} | {_v(v.get('p95_s'))} |" for k, v in sorted(sy.items())]
        md.append("")
    else:
        md += ["Sync anchor error: no anchors logged.", ""]
    md += [f"- TV switch (SmartThings, confirmed): {s.get('switch_samples', 0)} switches, p50 {_v(s.get('switch_p50_s'))}, "
           f"p95 {_v(s.get('switch_p95_s'))}, "
           f"max {_v(s.get('switch_max_s'))}, unconfirmed {s.get('tv_unverified', 0)}",
           f"- Picture within 3 s of the cut: {s.get('picture_within_3s') or '–'}; cut to picture p95 "
           f"{_v(s.get('picture_p95_s'))}",
           f"- Phone press to TV: {s.get('press_samples', 0)} presses, p50 {_v(s.get('press_to_tv_p50_s'))}, "
           f"p95 {_v(s.get('press_to_tv_p95_s'))}, {s.get('press_no_picture', 0)} never reached a picture"]
    bf = s.get("buffer") or {}
    if bf.get("detected"):
        md.append(f"- Hidden-stick buffer (STK-06): {bf.get('detected')} lost or drifting, {bf.get('recovered', 0)} rebuilt, "
                  f"{bf.get('rebuild_failed', 0)} not rebuilt; error p95 {_v(bf.get('err_p95_s'))}")
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


def write(night: date, dirs: list[Path], out: Path = OUT, phone_url: str | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{night.isoformat()}.md"
    p.write_text(render(night, load(night, dirs), phone_url))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--night", type=date.fromisoformat, help="YYYY-MM-DD (default: last night, ET)")
    ap.add_argument("--live-dir", type=Path, action="append", default=None,
                    help="folders of <night>/summary.json (default docs/live-nights)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--phone-url", help="the phone page (http://<mac>.local:8777): misses link straight into its "
                    "night replay viewer. Never on a page committed to the public repo")
    ap.add_argument("--force", action="store_true", help="write even with report.nightly off")
    a = ap.parse_args(argv)
    if not (a.force or enabled()):
        print("report.nightly is off: nothing written (use --force for a manual run)")
        return 0
    p = write(a.night or last_night(), a.live_dir or [LIVE], a.out, a.phone_url)
    print(p.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
