"""[SYNC diamond B1] the night logger's report: epoch vs counter, flat E, pauses, unexplained jumps."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("pn", Path(__file__).resolve().parent.parent / "tools/rzseek/prime_night.py")
pn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pn)

W = 1_762_330_000.0


def row(t, up, pos_ms, upd, state="playing", stick="stick1", pkg="com.amazon.firebat", title="TNF"):
    return {"t": t, "stick": stick, "uptime_ms": up,
            "ms": {"state": state, "pkg": pkg, "position": pos_ms, "updated": upd, "speed": 1.0, "title": title}}


def test_epoch_stick_flat_with_a_pause_and_a_jump():
    rows, up0, upd = [], 500_000, 500_000
    pos0 = (W - 6.4) * 1000
    for k in range(60):                     # 60 s playing, one state update
        rows.append(row(W + k, up0 + k * 1000, pos0, upd))
    # paused 20 s at W+60 (position frozen at its value then)
    p = pos0 + 60_000
    for k in range(60, 80):
        rows.append(row(W + k, up0 + k * 1000, p, up0 + 60_000, state="paused"))
    for k in range(80, 120):               # playing again, 20 s further behind
        rows.append(row(W + k, up0 + k * 1000, p, up0 + 80_000))
    # an ad-pod jump: E moves 3 s with a new update while playing
    for k in range(120, 140):
        rows.append(row(W + k, up0 + k * 1000, p + 40_000 - 3_000, up0 + 120_000))
    rep = pn.report(rows)["stick1 com.amazon.firebat TNF"]
    assert rep["epoch"] is True and rep["E_flat_sd_p50"] < 0.01
    assert rep["pauses"] and abs(rep["pauses"][0]["dE"] - 20.0) < 1.01
    assert len(rep["jumps"]) == 1 and abs(rep["jumps"][0]["dE"] - 3.0) < 0.01
    assert 6.0 < rep["abs_behind_p50"] < 30.0


def test_counter_stick_is_not_epoch():
    rows = [row(W + k, 9_000_000 + k * 1000, 86_368_000, 9_000_000, stick="stick2") for k in range(30)]
    rep = pn.report(rows)["stick2 com.amazon.firebat TNF"]
    assert rep["epoch"] is False and "abs_behind_p50" not in rep and rep["unexplained_jumps_per_h"] == 0
