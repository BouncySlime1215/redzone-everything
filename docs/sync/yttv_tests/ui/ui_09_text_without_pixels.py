#!/usr/bin/env python3
"""ui_09 Can the overlay text (program window, scrub time, LIVE) be read WITHOUT screenshots/OCR?

HYPOTHESIS: Cobalt renders into one SurfaceView, so `uiautomator dump` probably has no text nodes, but
media_session metadata (description / extras, e.g. duration, program start) or the window title might
carry the program window. If any source exposes the program start or the scrub time as text, ui_02/03/05
become cheap 1 Hz reads with no OCR. This test inventories every read-only text source once per
--every s and greps it for times ('2:30 AM', 'MM:SS'), 'LIVE', program titles.

PROCEDURE (read-only; runs on the hidden stick by default):
  - `uiautomator dump /dev/tty` (only when the stick is NOT on screen: the dump can stutter playback)
  - `dumpsys media_session` full metadata block for the YTTV session (title, subtitle, description,
    duration, extras keys)
  - `dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp|Window #'` for the YTTV window title
  - `dumpsys activity top | head -60` (view hierarchy for the top activity)
  Each source: byte count, time-like strings, 'LIVE' hits, program-title hits.

PASS: some source other than pixels yields the program start/end OR the playhead time as text in >= 2 of
  3 reads (log which). Otherwise FAIL = pixels+OCR is the only UI path (still informative).
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_09_text_without_pixels"
TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AP]M)?\b", re.I)
SAMPLE_UIA = ('<?xml version="1.0"?><hierarchy rotation="0"><node index="0" text="" class="android.widget.FrameLayout" '
              'package="com.amazon.firetv.youtube.tv"><node index="0" text="" class="android.view.SurfaceView"/>'
              '</node></hierarchy>')


def extra(ap):
    ap.add_argument("--every", type=float, default=20.0)
    ap.add_argument("--reads", type=int, default=3)


def scan(name: str, txt: str, title: str = "") -> dict:
    times = sorted(set(m.group(0).strip() for m in TIME.finditer(txt)))[:12]
    return dict(source=name, bytes=len(txt), times=times, live=len(re.findall(r"\bLIVE\b", txt)),
                title_hit=bool(title and title in txt))


def media_meta(txt: str) -> str:
    """Just the YTTV session's block of dumpsys media_session."""
    i = txt.find(U.PKG)
    return txt[max(0, i - 200): i + 3000] if i >= 0 else ""


def live(args, g, out):
    vis = g.host in U.visible_hosts()
    good = 0
    for i in range(args.reads):
        title = (U.ms_raw(g.host) or {}).get("title", "")
        rows = []
        if not vis:
            rows.append(scan("uiautomator", U._sh(g.host, "uiautomator dump /dev/tty 2>/dev/null", timeout=30), title))
        rows.append(scan("media_session", media_meta(U._sh(g.host, "dumpsys media_session")), title))
        rows.append(scan("window", U._sh(g.host, "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp|Window #'"),
                         title))
        rows.append(scan("activity_top", U._sh(g.host, "dumpsys activity top | head -60"), title))
        for r in rows:
            out.write("source", read=i, **r)
        # a 'program time' hit = an AM/PM time or MM:SS anywhere outside media_session's own position fields
        if any(r["times"] for r in rows if r["source"] != "media_session") or \
                any(any(("AM" in t.upper() or "PM" in t.upper()) for t in r["times"]) for r in rows):
            good += 1
        if i < args.reads - 1:
            U.sleep_poll(args.every)
    return dict(reads=args.reads, reads_with_time_text=good, visible_skip_uia=vis, pass_=good >= 2)


def dry(args, out):
    rows = [scan("uiautomator(sample)", SAMPLE_UIA)]
    probe = U.load_probe(args.date, True)
    ms_fields = set()
    descs = set()
    for r in probe[:3000]:
        m = r.get("ms") or {}
        ms_fields |= set(m.keys())
        if m.get("description"):
            descs.add(m["description"][:80])
    rows.append(dict(source="recorded media_session fields", fields=sorted(ms_fields), descriptions=sorted(descs)[:5]))
    for r in rows:
        out.write("source", **r)
    out.write("would", cmds=["uiautomator dump /dev/tty (hidden only)", "dumpsys media_session",
                             "dumpsys window windows | grep", "dumpsys activity top | head -60"], reads=args.reads)
    return dict(recorded_fields=sorted(ms_fields), recorded_descriptions=len(descs), pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
