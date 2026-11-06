"""YTTV test bank (docs/sync/yttv_tests): Nick's Nov 6 item 4.

- mechanics_01 must drop zero reads: a PLAYING media_session update at position 0 while the stream
  attaches is a placeholder, never a landing (live tune_measure and the judge).
- ui_10 focus: the YTTV player row is filled buttons; the bank must still find the focused one.
No stick, no macOS: everything here is pure functions or fakes.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

BANK = Path(__file__).resolve().parents[1] / "docs" / "sync" / "yttv_tests"


@pytest.fixture()
def mech(monkeypatch):
    monkeypatch.setenv("RZ_SYNC", str(BANK.parent))           # repo t1_retune.py
    monkeypatch.setenv("RZ_REPO", str(BANK.parents[2]))
    monkeypatch.syspath_prepend(str(BANK / "mechanics"))
    for m in ("mech_common", "mechanics_01_landing_repeat"):
        sys.modules.pop(m, None)
    M = importlib.import_module("mech_common")
    m01 = importlib.import_module("mechanics_01_landing_repeat")
    return M, m01


def test_judge_drops_zero_landings(mech):
    _, m01 = mech
    groups = {("h", "nbatv"): [0.0, 46792.7, 46792.9, 0.0, 46793.1, 46792.6, 46792.8, 0.4]}
    kept, n = m01.drop_zero(groups)
    assert n == 3
    assert kept[("h", "nbatv")] == [46792.7, 46792.9, 46793.1, 46792.6, 46792.8]


def test_tune_measure_skips_position_zero(mech, monkeypatch):
    M, _ = mech
    seq = iter([
        dict(st="playing", sub="NBA TV", upd=1, pos=46000.0, t=0.0, E=0.0, title="old", rtt=0.1),  # before
        dict(st="playing", sub="NBA TV", upd=2, pos=0.0, t=1.0, E=1.0, title="x", rtt=0.1),        # placeholder
        dict(st="playing", sub="NBA TV", upd=3, pos=0.0, t=1.5, E=1.5, title="x", rtt=0.1),        # placeholder
        dict(st="playing", sub="NBA TV", upd=4, pos=46792.7, t=2.0, E=9.0, title="MIA @ GS", rtt=0.1),
    ])
    monkeypatch.setattr(M, "read", lambda host: next(seq))
    monkeypatch.setattr(M, "stick_clock", lambda host: {"boot_epoch": None})
    monkeypatch.setattr(M.T, "sh", lambda host, cmd, timeout=8: "")
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    g = types.SimpleNamespace(host="h", check=lambda: None, out=lambda *a, **k: None)
    res = M.tune_measure(g, "nbatv")
    assert res["ok"] and res["L"] == 46792.7 and res["zero_reads_skipped"] == 2


@pytest.fixture()
def ui(monkeypatch):
    monkeypatch.setenv("RZ_SYNC", str(BANK.parent))
    monkeypatch.setenv("RZ_REPO", str(BANK.parents[2]))
    monkeypatch.syspath_prepend(str(BANK / "ui"))
    sys.modules.pop("ui_common", None)
    return importlib.import_module("ui_common")


def _row():
    # the YTTV player row as OCR rows (normalized coords)
    labels = ["HD", "CC", "Networks", "Multiview", "Stats"]
    return [dict(text=t, conf=0.95, x=0.10 + 0.12 * i, y=0.86, w=0.06, h=0.03) for i, t in enumerate(labels)]


def test_pick_focus_filled_button_row(ui):
    obs = _row()
    cands = [dict(x=0.33, y=0.85, width=0.10, height=0.05, confidence=0.81, source="neutral_filled_button")]
    foc, status = ui.pick_focus(cands, obs)
    assert status == "bank_unique" and foc[0]["text"] == "Networks"
    assert ui.focused_text(dict(focus=foc, obs=obs)) == "Networks"


def test_pick_focus_prefers_border_and_flags_ties(ui):
    obs = _row()
    cands = [dict(x=0.33, y=0.85, width=0.10, height=0.05, confidence=0.80, source="neutral_filled_button"),
             dict(x=0.45, y=0.85, width=0.10, height=0.05, confidence=0.95, source="neutral_rectangle_border")]
    foc, status = ui.pick_focus(cands, obs)
    assert foc[0]["text"] == "Multiview" and status == "bank_unique"
    two = [dict(x=0.33, y=0.85, width=0.10, height=0.05, confidence=0.80, source="neutral_filled_button"),
           dict(x=0.57, y=0.85, width=0.10, height=0.05, confidence=0.79, source="neutral_filled_button")]
    _, status = ui.pick_focus(two, obs)
    assert status.startswith("bank_best_of_")


def test_pick_focus_rejects_weak_or_bad(ui):
    obs = _row()
    bad = [dict(x=0.33, y=0.85, width=0.10, height=0.05, confidence=0.5, source="neutral_filled_button"),
           dict(x=0.33, y=0.85, width=0.10, height=0.05, confidence=0.99, source="other"),
           dict(x="?", source="neutral_filled_button")]
    assert ui.pick_focus(bad, obs) == ([], "none")


def test_nov5_mechanics01_landings_regrade(mech):
    """Nov 5 night, hidden stick, nbatv x6: three landings read L = 0.0 (placeholders) and the group
    'failed' with MAD 34,685 s. Without them the three real landings agree to 0.01 s."""
    _, m01 = mech
    Ls = [0.0, 0.0, 46790.147, 46790.135, 0.0, 46790.134]
    kept, n = m01.drop_zero({("STICK_HDMI2", "nbatv"): Ls})
    assert n == 3
    assert m01.mad(kept[("STICK_HDMI2", "nbatv")]) <= 0.02


class _FakeGuard:
    """Replays a scripted focus sequence; records every key."""

    def __init__(self, labels):
        self.labels, self.keys = list(labels), []

    def key(self, *codes, gap=0.7):
        self.keys += codes
        return True


def _patch_focus(ui, monkeypatch, g):
    monkeypatch.setattr(ui, "snap", lambda g_, tag: (None, 0.0, {"_ft": g.labels.pop(0) if g.labels else ""}))
    monkeypatch.setattr(ui, "focused_text", lambda o: o.get("_ft", ""))


def test_nov5_ui10_unknown_focus_presses_no_left_right(ui, monkeypatch):
    """Nov 5 night: overlay revealed with no focus box; LEFT x9 then seeked the stick 60 s. Now an unknown
    focus after DOWN x3 must end navigation with no LEFT/RIGHT pressed."""
    g = _FakeGuard(["", "", "", ""])
    _patch_focus(ui, monkeypatch, g)
    nav = ui.navigate_to(g, "stats")
    assert not nav["found"]
    assert "DPAD_LEFT" not in g.keys and "DPAD_RIGHT" not in g.keys
    assert g.keys == ["DPAD_DOWN", "DPAD_DOWN", "DPAD_DOWN", "DPAD_DOWN"]


def test_navigate_scrubber_then_row(ui, monkeypatch):
    # bar focused ('23:36' scrub label) -> DOWN -> HD -> RIGHT -> CC -> RIGHT -> Stats
    g = _FakeGuard(["23:36", "HD", "CC", "Stats"])
    _patch_focus(ui, monkeypatch, g)
    nav = ui.navigate_to(g, "stats")
    assert nav["found"] and nav["text"] == "Stats"
    assert g.keys == ["DPAD_DOWN", "DPAD_DOWN", "DPAD_RIGHT", "DPAD_RIGHT"]


def test_navigate_stops_when_focus_leaves_row(ui, monkeypatch):
    g = _FakeGuard(["HD", "", "Stats"])
    _patch_focus(ui, monkeypatch, g)
    nav = ui.navigate_to(g, "stats")
    assert not nav["found"] and g.keys == ["DPAD_DOWN", "DPAD_RIGHT"]
