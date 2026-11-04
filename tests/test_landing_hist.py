"""[landing offsets] The histogram of dead time left at each landing, and its gate rows."""
import importlib.util
from pathlib import Path

from redzone.lab import cutgrade

ROOT = Path(__file__).resolve().parents[1]


def test_buckets():
    assert cutgrade.hist_bucket(False, 50) == "mid-play"
    assert cutgrade.hist_bucket(True, 0) == "0-3"
    assert cutgrade.hist_bucket(True, 3) == "3-10"
    assert cutgrade.hist_bucket(True, 59.9) == "30-60"
    assert cutgrade.hist_bucket(True, 500) == "120+"


def test_card_adds_histograms_and_landing_share():
    a, b = cutgrade.CutCard(), cutgrade.CutCard()
    a.landed("entry", True, 12)
    a.entries, a.leaves, a.leaves_in_dead = 1, 1, 1
    a.landed("leave", True, 2)
    b.landed("entry", False, 0)
    b.entries, b.entries_mid_play = 1, 1
    a.add(b)
    s = a.summary()
    assert s["entry_to_restart_hist"] == {"mid-play": 1, "10-30": 1}
    assert s["leave_to_restart_hist"] == {"0-3": 1}
    assert s["landings_in_dead_pct"] == round(100 * 2 / 3, 1)
    assert list(s["entry_to_restart_hist"]) == ["mid-play", "10-30"]   # ordered buckets


def test_gate_rows():
    spec = importlib.util.spec_from_file_location("rg", ROOT / "scripts/release_gate.py")
    rg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rg)
    card = {"eval_data": rg.HELDOUT["eval_data"], "mid_play_cuts": 0, "entries": 20, "entries_mid_play": 1,
            "landings_in_dead_pct": 96.0}
    rows = {(g, s): st for g, s, st, _ in rg.gates({"nba": card})}
    assert rows[("entries mid-play = 0", "nba")] == rg.FAIL
    assert rows[("landings in dead time >= 95%", "nba")] == rg.PASS
