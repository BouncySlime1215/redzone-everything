"""[park.picture_grade] The picture grader as a sidecar of every PARK show (flag off by default).

The picture grader judges each jump on what YouTube TV actually showed: was the clock on the score bug stopped when the
show left a game, and when it landed on the next one? It is a separate program (the Eyes tool
`redzone-agents/tools/eyes/picture_grade.py`): Eyes' own windowless, muted Chrome on its own signed-in profile. It
reads the show's state folder and writes two files there, `picture_report.jsonl` (one line per jump) and
`picture.json` (the summary "picture: clean n / mid-play n / unknown n"). It never touches the TV or the sticks.

With `park.picture_grade.enabled` on and `park.picture_grade.tool` naming that program, `ParkRun` starts it when a
show starts (at background priority, `nice -n 15`) and stops it when the show stands down. Off, nothing here runs and
no park.picture_* key changes a decision: the show's state gains a `picture` field only when the summary file exists.

[park.picture_check.live] The live check (off, and not wired to a decision yet): the same picture question asked
BEFORE a jump. `live_check_on` is the one place a future guard asks whether it may use it.

Why: tonight the show is judged before a jump by the feed plus padding, and afterwards by the frozen grader against
the feed; nobody checked the picture (Nick, Nov 11: "it should be grading on the yt tv... automatically").
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
SUMMARY = "picture.json"
LIVE_CHECK_KEY = "park.picture_check.live"


def picture_summary(folder) -> dict | None:
    """The grader's summary in the show's state folder, or None (no grader ran, or its file is unreadable)."""
    if not folder:
        return None
    try:
        d = json.loads((Path(folder) / SUMMARY).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return {k: d.get(k) for k in ("clean", "mid_play", "unknown", "jumps", "line", "updated")}


def live_check_on(get) -> bool:
    """[park.picture_check.live] Off unless set to exactly True. Nothing calls the live check yet."""
    return get(LIVE_CHECK_KEY, False) is True


class PictureSidecar:
    """Starts and stops the grader program around a show. `spawn` is the seam for tests (subprocess.Popen shape)."""

    def __init__(self, get, spawn=subprocess.Popen):
        self.get, self.spawn = get, spawn
        self.proc = None
        self.why = ""

    def enabled(self) -> bool:
        return self.get("park.picture_grade.enabled", False) is True

    def start(self, show_dir) -> bool:
        if not self.enabled() or self.running():
            return False
        tool = str(self.get("park.picture_grade.tool", "") or "")
        if not tool or not Path(tool).is_file():
            self.why = "park.picture_grade.tool does not name the grader program"
            log.warning("picture grader: %s", self.why)
            return False
        python = str(self.get("park.picture_grade.python", "") or sys.executable)
        extra = [str(x) for x in (self.get("park.picture_grade.args", None) or [])]
        cmd = ["nice", "-n", "15", python, tool, "--show-dir", str(Path(show_dir).resolve()), *extra]
        try:
            self.proc = self.spawn(cmd, cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            self.proc, self.why = None, f"could not start ({type(exc).__name__})"
            log.warning("picture grader: %s", self.why)
            return False
        self.why = "running"
        log.info("picture grader: started")
        return True

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        """SIGINT first: the grader closes its own Chrome; then terminate, then kill."""
        p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        try:
            p.send_signal(signal.SIGINT)
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        except OSError:
            pass
        self.why = "stopped"
