#!/usr/bin/env python3
"""ui_12 YTTV web player reference clock: getMediaReferenceTime() in a muted Mac Chrome tab.

HYPOTHESIS (overseer 7:35 AM Nov 5): the YouTube TV WEB player (tv.youtube.com in Chrome on the Mac,
already signed in) exposes the epoch of the frame it is playing:
    document.getElementById('movie_player').getMediaReferenceTime()   (= getProgressState().ingestionTime)
plus isAtLiveHead(). At the live head on ESPN, now - mediaReferenceTime read 23.13 / 23.21 / 23.21 /
23.21 s, steady to 0.1 s. That is a reference clock for the channel's ingest, with no picture capture
and so nothing DRM-related. Two numbers per read; no frames are ever taken.

PROCEDURE (read-only; never presses a stick key, never navigates the tab):
  0. Nick (or the overseer) opens tv.youtube.com/watch/<channel> in Chrome on the Mac, muted, on the same
     channel the sticks are tested on. Chrome needs View > Developer > "Allow JavaScript from Apple
     Events" ON; the test prints that hint and stops if it is off. It does not change any setting.
  1. every --every s (default 1.0): osascript runs one JS expression in the first tab whose URL is on
     tv.youtube.com: wall (Date.now, read twice and averaged), ref = getMediaReferenceTime(), ingest =
     getProgressState().ingestionTime, at_live_head, the video title (for program-boundary flips),
     muted. Logged as kind "sample" with lag = wall - ref.
  2. title changes are logged as kind "web_flip" (wall, ref at the flip) for fusion_13's alignment.

PASS: >= 60 samples at the live head, and the lag's MAD <= 0.25 s (it read steady to 0.1 s by hand).
The Mac clock cancels in fusion_13, because the stick E reads use the same Mac wall clock.
"""
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_12_web_reference"

JS = ("(function(){var p=document.getElementById('movie_player');var a=Date.now();"
      "if(!p||!p.getMediaReferenceTime)return JSON.stringify({err:'no player'});"
      "var r=p.getMediaReferenceTime();var s=p.getProgressState?p.getProgressState():{};"
      "var v=p.getVideoData?p.getVideoData():{};var b=Date.now();"
      "return JSON.stringify({wall:(a+b)/2000,ref:r,ingest:s.ingestionTime,cur:s.current,"
      "live:p.isAtLiveHead?p.isAtLiveHead():null,muted:p.isMuted?p.isMuted():null,"
      "title:(v&&v.title)||document.title||''});})()")

APPLESCRIPT = """
on run argv
  set js to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if URL of t contains "tv.youtube.com" then
          return execute t javascript js
        end if
      end repeat
    end repeat
  end tell
  return "{\\"err\\":\\"no tv.youtube.com tab\\"}"
end run
"""
HINT = ("Chrome blocks AppleScript JavaScript: turn on View > Developer > 'Allow JavaScript from Apple "
        "Events' in Chrome (ask Nick first; this test never changes it).")


def extra(ap):
    ap.add_argument("--every", type=float, default=1.0)


def read_tab() -> dict:
    try:
        r = subprocess.run(["osascript", "-e", APPLESCRIPT, JS], capture_output=True, text=True, timeout=5)
    except Exception as e:  # noqa: BLE001
        return dict(err=repr(e))
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        return dict(err=err[:200], hint=HINT if "JavaScript" in err or "Apple Events" in err else None)
    try:
        return json.loads(r.stdout.strip())
    except ValueError:
        return dict(err="unparsed: " + r.stdout.strip()[:120])


class Track:
    """Folds reads into samples and title flips."""

    def __init__(self):
        self.lags: list[float] = []
        self.n_reads = self.n_live = 0
        self.title = None
        self.flips: list[dict] = []

    def feed(self, row: dict) -> tuple[dict | None, dict | None]:
        if row.get("err") or row.get("ref") in (None, 0):
            return None, None
        self.n_reads += 1
        ref = float(row["ref"])
        ref = ref / 1000.0 if ref > 1e11 else ref           # ms or s epoch: store seconds
        lag = float(row["wall"]) - ref
        s = dict(wall=round(float(row["wall"]), 3), ref=round(ref, 3), lag=round(lag, 3),
                 ingest=row.get("ingest"), live=row.get("live"), muted=row.get("muted"))
        if row.get("live"):
            self.n_live += 1
            self.lags.append(lag)
        flip = None
        title = (row.get("title") or "").strip()
        if self.title is not None and title and title != self.title:
            flip = dict(t=s["wall"], ref=s["ref"], lag=s["lag"], old=self.title[:80], new=title[:80])
            self.flips.append(flip)
        if title:
            self.title = title
        return s, flip

    def summary(self) -> dict:
        st = U.summarize(self.lags)
        ok = self.n_live >= 60 and st.get("mad", 9) <= 0.25
        return dict(reads=self.n_reads, at_live_head=self.n_live, lag=st, flips=len(self.flips), pass_=bool(ok))


def live(args, g, out):
    first = read_tab()
    if first.get("err"):
        out.write("err", **first)
        return dict(pass_=None, skipped=first.get("hint") or first["err"])
    if first.get("muted") is False:
        out.write("warn", why="the tab is not muted: mute it by hand (the test never touches the tab)")
    tr, t_end = Track(), time.time() + args.minutes * 60
    while time.time() < t_end:
        row = read_tab()
        s, flip = tr.feed(row)
        if s:
            out.write("sample", **s)
        elif row.get("err"):
            out.write("err", **row)
        if flip:
            out.write("web_flip", **flip)
        U.sleep_poll(args.every)
    return tr.summary()


def dry(args, out):
    """Synthetic: a tab 23.2 s behind with 0.05 s read jitter and one program flip; plus the parser."""
    import random
    random.seed(12)
    tr, t0 = Track(), 1_793_000_000.0
    for i in range(120):
        w = t0 + i
        row = dict(wall=w, ref=(w - 23.2 + random.gauss(0, 0.05)) * 1000, live=True, muted=True,
                   title="SportsCenter" if i < 70 else "NBA Basketball")
        s, flip = tr.feed(row)
        if flip:
            out.write("web_flip", **flip)
    summ = tr.summary()
    out.write("synthetic", truth_lag=23.2, est=summ["lag"], flips=len(tr.flips))
    out.write("would", steps=["osascript: execute JS in the tv.youtube.com tab every %.1f s" % args.every,
                              "log wall/ref/lag/live/title", "never navigate, mute or change a setting"])
    err = abs(summ["lag"].get("median", 0) - 23.2)
    return dict(synthetic_median_err=round(err, 3), synthetic_flips=len(tr.flips),
                pass_=bool(err < 0.1 and len(tr.flips) == 1))


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
