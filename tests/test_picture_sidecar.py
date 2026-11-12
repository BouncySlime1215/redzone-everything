"""[park.picture_grade] The picture grader sidecar: off by default and decision-identical; on, it starts the grader
program with the show and stops it with the show; its summary reaches the show's state only when its file exists."""
from __future__ import annotations

import json
import signal
import subprocess

from redzone.picture_sidecar import PictureSidecar, live_check_on, picture_summary


def getter(d: dict):
    return lambda key, default=None: d.get(key, default)


class FakeProc:
    def __init__(self):
        self.signals, self.alive = [], True

    def poll(self):
        return None if self.alive else 0

    def send_signal(self, s):
        self.signals.append(s)
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False


def test_off_by_default_starts_nothing(tmp_path):
    calls = []
    sc = PictureSidecar(getter({}), spawn=lambda *a, **k: calls.append(a))
    assert not sc.enabled() and sc.start(tmp_path) is False and calls == []
    sc.stop()                                                     # nothing to stop: no error
    assert live_check_on(getter({})) is False


def test_on_starts_at_background_priority_and_stops_with_sigint(tmp_path):
    tool = tmp_path / "picture_grade.py"
    tool.write_text("# stand-in")
    got = {}
    proc = FakeProc()

    def spawn(cmd, **kw):
        got.update(cmd=cmd, kw=kw)
        return proc
    sc = PictureSidecar(getter({"park.picture_grade.enabled": True, "park.picture_grade.tool": str(tool),
                                "park.picture_grade.python": "/usr/bin/python3"}), spawn=spawn)
    assert sc.start(tmp_path / "show") is True and sc.running()
    assert got["cmd"][:4] == ["nice", "-n", "15", "/usr/bin/python3"] and "--show-dir" in got["cmd"]
    assert got["kw"]["stdout"] is subprocess.DEVNULL and got["kw"]["start_new_session"] is True
    assert sc.start(tmp_path / "show") is False                  # one at a time
    sc.stop()
    assert proc.signals == [signal.SIGINT] and not sc.running()


def test_missing_tool_is_refused_with_a_reason(tmp_path):
    sc = PictureSidecar(getter({"park.picture_grade.enabled": True, "park.picture_grade.tool": str(tmp_path / "nope.py")}),
                        spawn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    assert sc.start(tmp_path) is False and "tool" in sc.why


def test_only_exactly_true_turns_it_on(tmp_path):
    for v in ("yes", 1, "true", None):
        assert PictureSidecar(getter({"park.picture_grade.enabled": v})).enabled() is False
        assert live_check_on(getter({"park.picture_check.live": v})) is False


def test_summary_only_when_the_file_exists(tmp_path):
    assert picture_summary(tmp_path) is None and picture_summary(None) is None
    (tmp_path / "picture.json").write_text(json.dumps({"clean": 3, "mid_play": 1, "unknown": 2, "jumps": 6,
                                                       "line": "picture: clean 3 / mid-play 1 / unknown 2", "updated": 1.0, "x": 9}))
    s = picture_summary(tmp_path)
    assert s["line"] == "picture: clean 3 / mid-play 1 / unknown 2" and "x" not in s
    (tmp_path / "picture.json").write_text("not json")
    assert picture_summary(tmp_path) is None


def test_board_state_gains_picture_only_with_the_file(tmp_path):
    from redzone.showboard import Board
    b = Board(tmp_path)
    assert b._picture() == {}
    (tmp_path / "picture.json").write_text(json.dumps({"clean": 1, "mid_play": 0, "unknown": 0, "jumps": 1, "line": "picture: clean 1 / mid-play 0 / unknown 0"}))
    assert b._picture()["picture"]["clean"] == 1
    assert Board(None)._picture() == {}
