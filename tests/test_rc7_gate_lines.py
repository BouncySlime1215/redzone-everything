"""RC7's three hard requirements (Nick, Nov 4 2026 9:45 PM) as scorecard lines:
press-to-TV <= 3 s incl. Prime, per-stick sync error p95 <= 2 s, never join at a break."""
import json

from redzone.lab import cutgrade
from scripts import northstar as ns


def _cards(n, **row):
    per = [{"night": f"n{i}", "entries": 40, "landings": 80, **row} for i in range(n)]
    return {lg: {"metrics": {"per_night": per, "entries_mid_break": row.get("entries_mid_break", 0) * n,
                             "entries_in_break": 3 * n, "entry_to_restart_hist": {"0-3": 5, "3-10": 2}}}
            for lg in ns.LEAGUES}


def _rows(cards):
    return {r["key"]: r for r in ns.table(cards)}


# ---- gate 3: never join at a break ---------------------------------------------------------
def test_break_line_is_pending_on_old_scorecards():
    assert _rows(_cards(9))["mid_break"]["status"] == ns.PENDING


def test_break_line_passes_at_zero_and_fails_on_one():
    assert _rows(_cards(9, entries_mid_break=0))["mid_break"]["status"] == ns.PASS
    assert _rows(_cards(1, entries_mid_break=0))["mid_break"]["status"] == ns.PENDING   # too few nights
    c = _cards(9, entries_mid_break=0)
    c["nba"]["metrics"]["per_night"][0] = c["nba"]["metrics"]["per_night"][0] | {"entries_mid_break": 1}
    r = _rows(c)["mid_break"]
    assert r["status"] == ns.FAIL and r["value"] > 0


def test_cutgrade_counts_only_landings_with_break_left():
    card = cutgrade.CutCard()
    assert "entries_mid_break" in card.summary() and cutgrade.BREAK_TAIL_BAR == 10.0
    a, b = cutgrade.CutCard(entries_mid_break=2), cutgrade.CutCard(entries_mid_break=1)
    a.add(b)
    assert a.summary()["entries_mid_break"] == 3


# ---- gate 2: sync dialed --------------------------------------------------------------------
def _truth(stick, n, err, nights=2):
    return [{"stick": stick, "night": f"n{i % nights}", "t": i, "est_behind_s": 40.0 + err, "true_behind_s": 40.0}
            for i in range(n)]


def test_sync_line_pending_without_truth_and_until_enough_rows():
    assert ns.sync_row([])["status"] == ns.PENDING
    assert ns.sync_row(_truth("hdmi1", 50, 0.5) + _truth("hdmi2", 50, 0.5))["status"] == ns.PENDING
    assert ns.sync_row(_truth("hdmi1", 300, 0.5))["status"] == ns.PENDING          # one stick only
    assert ns.sync_row(_truth("hdmi1", 300, 0.5, nights=1) + _truth("hdmi2", 300, 0.5, nights=1))["status"] == ns.PENDING


def test_sync_line_worst_stick_decides():
    good = _truth("hdmi1", 300, 0.5) + _truth("hdmi2", 300, -1.5)
    r = ns.sync_row(good)
    assert r["status"] == ns.PASS and r["value"] == 1.5
    bad = _truth("hdmi1", 300, 0.5) + _truth("hdmi2", 300, 2.5)
    assert ns.sync_row(bad)["status"] == ns.FAIL


def test_sync_samples_drop_manual_taps(tmp_path):
    rows = _truth("hdmi1", 3, 0.5) + [{"stick": "hdmi1", "est_behind_s": 1, "true_behind_s": 50, "source": "tap"}]
    (tmp_path / "n1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    got = ns.sync_samples([tmp_path])
    assert len(got) == 3 and all(r.get("source") != "tap" for r in got)


# ---- gate 1: switches dialed, Prime included -----------------------------------------------
def _presses(n, s, app="yttv"):
    return [{"i": i, "kind": "warm", "ok": True, "tap_to_tv_s": s, "app": app} for i in range(n)]


def test_prime_has_its_own_sub_gate():
    assert ns.real_press_row(_presses(25, 1.5))["status"] == ns.PENDING             # no Prime press yet
    assert ns.real_press_row(_presses(20, 1.5) + _presses(5, 2.5, "prime"))["status"] == ns.PASS
    # a fast YTTV p95 can't hide slow Prime presses
    r = ns.real_press_row(_presses(95, 1.5) + _presses(5, 26.0, "prime"))
    assert r["status"] == ns.FAIL and "Prime 5" in r["detail"]
    # a Prime press that never reached the TV fails it
    miss = _presses(24, 1.5) + [{"i": 99, "kind": "cold", "ok": False, "tap_to_tv_s": None, "app": "prime"}]
    assert ns.real_press_row(miss)["status"] == ns.FAIL
