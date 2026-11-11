"""scripts/tv_test_runner.py: the permission gate, hidden-stick-only, stop and resume, and --plan.

Why: the runner will one day drive the real TV; these prove with a fake stick that without data/.agent_hw_ok
no device call is made, that a failure stops the walk and resumes there, and that --plan touches nothing.
"""
import importlib.util
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tv_test_runner", ROOT / "scripts" / "tv_test_runner.py")
tr = importlib.util.module_from_spec(spec)
sys.modules["tv_test_runner"] = tr
spec.loader.exec_module(tr)

GOOD = {"seek_latency_s": 5, "seek_fail_rate": 0.02, "park_error_sd_s": 1.0, "game_change_prep_s": 12,
        "play_to_picture_s": 2.0, "pause_hold_30s_ok": True, "prime_seek_in_steps": True,
        "labels_agree_with_offset": True, "rows_found": 3, "codes_resolve": True, "signed_in": True,
        "game_starts": True, "flip_success_rate": 1.0, "step_error_s": 1.0, "banner_clear_s": 2.0,
        "band_read_ok": True, "pause_holds": True, "inputs_listed": 2, "drift_s_per_5min": 0.5,
        "dial_reads": True}


class FakeStick:
    def __init__(self, role="hidden", bad=None, secret=False):
        self.role, self.calls, self.bad, self.secret = role, [], bad or {}, secret

    def measure(self, test_id):
        self.calls.append(test_id)
        d = dict(GOOD)
        d.update(self.bad.get(test_id, {}))
        if self.secret:
            d["addr"] = "192.168.1.50:5555"
        return d


def root_with_queue(tmp_path, permission=True):
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / tr.QUEUE_DOC, tmp_path / tr.QUEUE_DOC)
    (tmp_path / "data").mkdir()
    if permission:
        (tmp_path / tr.PERMISSION).write_text("")
    return tmp_path


GO = lambda: {"go": True, "failed": []}  # noqa: E731


def test_no_permission_file_means_no_device_call_and_no_preflight(tmp_path):
    root = root_with_queue(tmp_path, permission=False)
    stick, seen = FakeStick(), []
    factory_calls = []
    code = tr.run(root, stick_factory=lambda: factory_calls.append(1) or stick,
                  preflight=lambda: seen.append(1) or {"go": True}, nobody_watching=True, out=lambda s: None)
    assert code == 2 and stick.calls == [] and factory_calls == [] and seen == []
    assert not (root / tr.STATE).exists()


def test_preflight_no_go_stops_before_the_stick(tmp_path):
    root = root_with_queue(tmp_path)
    stick = FakeStick()
    code = tr.run(root, stick_factory=lambda: stick, preflight=lambda: {"go": False, "failed": ["power"]},
                  out=lambda s: None)
    assert code == 1 and stick.calls == []


def test_visible_stick_is_refused(tmp_path):
    root = root_with_queue(tmp_path)
    stick = FakeStick(role="visible")
    assert tr.run(root, stick_factory=lambda: stick, preflight=GO, out=lambda s: None) == 2
    assert stick.calls == []


def test_real_driver_fails_closed(tmp_path):
    root = root_with_queue(tmp_path)
    assert tr.run(root, preflight=GO, out=lambda s: None) == 2


def test_disruptive_test_waits_for_nobody_watching_and_progress_is_kept(tmp_path):
    root = root_with_queue(tmp_path)
    stick = FakeStick()
    assert tr.run(root, stick_factory=lambda: stick, preflight=GO, out=lambda s: None) == 2
    assert "T1-localflip" not in stick.calls and "3a" in stick.calls
    stick2 = FakeStick()
    assert tr.run(root, stick_factory=lambda: stick2, preflight=GO, nobody_watching=True, out=lambda s: None) == 0
    assert "3a" not in stick2.calls and stick2.calls[0] == "T1-localflip"


def test_failure_stops_records_numbers_and_resumes_at_the_failed_test(tmp_path):
    root = root_with_queue(tmp_path)
    stick = FakeStick(bad={"3b": {"labels_agree_with_offset": False}})
    msgs = []
    assert tr.run(root, stick_factory=lambda: stick, preflight=GO, nobody_watching=True, out=msgs.append) == 1
    assert stick.calls == ["3a", "3b"]
    state = tr._load(root / tr.STATE)
    assert state["3a"]["status"] == "pass" and state["3a"]["numbers"]["seek_latency_s"] == 5
    assert state["3b"]["status"] == "fail" and state["3b"]["failed_bars"]
    fixed = FakeStick()
    assert tr.run(root, stick_factory=lambda: fixed, preflight=GO, nobody_watching=True, out=lambda s: None) == 0
    assert fixed.calls[0] == "3b" and "3a" not in fixed.calls
    assert len((root / tr.RESULTS).read_text().splitlines()) == 2 + len(fixed.calls)


def test_missing_metric_and_a_raising_stick_both_fail(tmp_path):
    root = root_with_queue(tmp_path)

    class Dead(FakeStick):
        def measure(self, test_id):
            raise OSError("connect to 10.0.0.7:5555 failed")

    msgs = []
    assert tr.run(root, stick_factory=Dead, preflight=GO, out=msgs.append) == 1
    assert "10.0.0.7" not in " ".join(msgs) + (root / tr.STATE).read_text()
    ok, _, fails = tr.evaluate(tr.SPEC_BY_ID["3a"], {"seek_latency_s": 5})
    assert not ok and fails


def test_strings_from_the_stick_are_never_stored_or_printed(tmp_path):
    root = root_with_queue(tmp_path)
    msgs = []
    tr.run(root, stick_factory=lambda: FakeStick(secret=True), preflight=GO, nobody_watching=True, out=msgs.append)
    blob = " ".join(msgs) + (root / tr.STATE).read_text() + (root / tr.RESULTS).read_text()
    assert "192.168" not in blob


def test_plan_touches_nothing_and_adds_up(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(tr, "load_hidden_stick", lambda: called.append(1))
    before = {p for p in ROOT.joinpath("data").glob("*")} if (ROOT / "data").exists() else set()
    assert tr.main(["--plan"]) == 0
    text = capsys.readouterr().out
    after = {p for p in ROOT.joinpath("data").glob("*")} if (ROOT / "data").exists() else set()
    assert called == [] and before == after
    total = int(re.search(r"TOTAL: (\d+) min", text).group(1))
    assert total == sum(i.minutes for i in tr.build_items()) and total > 0
    for tid in ("1", "3a", "3b", "T1-L12"):
        assert f"[{tid}]" in text
    assert "(1 min" in text and "manual" in text


def test_queue_rows_parse_in_order():
    ids = [i.id for i in tr.build_items()]
    assert ids[:4] == ["1", "2", "3", "3a"] and ids[-1] == "T1-L12"
    assert next(i for i in tr.build_items() if i.id == "1").manual
