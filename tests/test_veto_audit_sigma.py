"""[NBA-YES, audit] The veto audit's --sigma and --sigma-hold: the timing doubt the replayed app states. No product behaviour.

Why: the join right after a made basket only fires when the stated doubt is small, and every audit so far ran at 3 s; to learn how well
the TV must know its picture delay, the audit has to be run at other doubts, with the default run unchanged.
"""
from types import SimpleNamespace as NS

from scripts import veto_audit as va
from tests.test_yes_audit import _main, _row


def test_the_default_doubt_is_the_replays_own_and_adds_nothing(monkeypatch, tmp_path, capsys):
    import inspect

    from redzone.lab import backtest
    assert va.DEFAULT_SIGMA_S == inspect.signature(backtest.run).parameters["sigma"].default == 3.0
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row())
    again = _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--sigma", "3")
    assert again[0] == text == va.table([_row()]) + "\n" and again[1] == out and again[2] == jobs == [("nfl", "hindsight", {}, None, 15.0)]
    assert sorted(out) == ["avoidable_lost", "rows", "rules", "sets"] and "doubt" not in text


def test_the_doubt_rides_on_the_job_and_is_said(monkeypatch, tmp_path, capsys):
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--sigma", "1.5", "--set", "gate.x=true")
    assert [j[2] for j in jobs] == [{"gate.x": True, va.SIGMA_KEY: 1.5}]
    assert out["sets"] == {"gate.x": True} and out["sigma_s"] == 1.5 and out["sigma_hold"] is False
    assert "stated timing doubt 1.5 s at the night's start (default 3 s), growing 1 s per 15 minutes" in text


def test_hold_rides_on_the_job_even_at_the_default_doubt(monkeypatch, tmp_path, capsys):
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--sigma-hold")
    assert [j[2] for j in jobs] == [{va.SIGMA_HOLD_KEY: True}] and out["sigma_s"] == 3.0 and out["sigma_hold"] is True
    assert "stated timing doubt 3 s at the night's start (default 3 s), held all night" in text


def test_both_delay_error_arms_keep_the_doubt(monkeypatch, tmp_path, capsys):
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--sigma", "1", "--sigma-hold", "--delay-error", "1")
    assert [j[2] for j in jobs] == [{va.SIGMA_KEY: 1.0, va.SIGMA_HOLD_KEY: True, va.DELAY_ERROR_KEY: 1.0},
                                    {va.SIGMA_KEY: 1.0, va.SIGMA_HOLD_KEY: True, va.DELAY_ERROR_KEY: -1.0}]
    assert out["sets"] == {} and out["delay_error_s"] == 1.0 and "held all night" in text


def test_a_doubt_of_zero_or_less_is_refused(monkeypatch, tmp_path, capsys):
    import pytest
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit):
            _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--sigma", bad)


def test_the_doubt_reaches_the_replay_only_when_asked(monkeypatch):
    """The wrapper hands backtest.run the stated doubt; the believed and the true delay are untouched, and no option means no change."""
    got = []
    monkeypatch.setattr(va.backtest, "run", lambda replays, cfg, mine, **kw: got.append(kw) or "rep")
    monkeypatch.setattr(va.Truth, "bind", lambda self, replays, lag, tune_s: got.append(lag))
    monkeypatch.setattr(va, "charge_night", lambda t, truth, row: None)
    for sets in ({}, {va.SIGMA_KEY: 1.5}, {va.SIGMA_KEY: 0.5, va.DELAY_ERROR_KEY: 0.5}):
        with va.replay("hindsight", sets, va.new_row(), None):
            assert va.backtest.run([], None, set(), feed_lag=8.0, delay=35.0, true_delay=None, sigma=3.0) == "rep"
    assert got == [43.0, {"feed_lag": 8.0, "delay": 35.0, "true_delay": None, "sigma": 3.0},
                   43.0, {"feed_lag": 8.0, "delay": 35.0, "true_delay": None, "sigma": 1.5},
                   43.5, {"feed_lag": 8.0, "delay": 35.0, "true_delay": 35.5, "sigma": 0.5}]


def test_hold_stops_the_doubt_growing_and_nothing_else():
    from redzone.decider.screen import Delay
    d, other = Delay(100.0, 1.0, calibrated_at=0.0), Delay(100.0, 1.0, calibrated_at=0.0)
    assert d.current_sigma(3600.0) == 5.0                          # the replay's own drift: 1 s per 15 minutes
    a = va.AuditDecider.__new__(va.AuditDecider)
    a.delays = NS(get=lambda target: {"yt:a": d}[target])
    a._hold_sigma({"a": NS(target="yt:a"), "reel": NS(target=None)})
    assert d.current_sigma(3600.0) == d.current_sigma(0.0) == 1.0 and (d.mu, d.sigma) == (100.0, 1.0)
    assert other.current_sigma(3600.0) == 5.0


# ----- the join right after a made basket is counted, and only shown when one happened ------------------------------------------------

def test_a_cut_on_a_made_basket_landing_is_counted():
    a = va.AuditDecider.__new__(va.AuditDecider)
    a.tally, a.truth = va.Tally(), NS(tune_s=5.0)
    a._commit, a._land_behind = lambda target, kind=None: None, lambda target, rewind_s: None
    for kind in ("bucket", "foul", ""):
        a.land_kind = kind
        a._go(NS(target="b", kind="better", rewind_s=0.0), 100.0)
    assert (a.tally.went, a.tally.bucket) == (3, 1) and a.tally.screen == [(105.0, "b")] * 3


def test_the_row_line_names_bucket_joins_only_when_the_grader_or_the_app_saw_one():
    plain = _row()
    assert "bucket" not in va.table([plain])
    row = _row()
    row["bucket"] = 4
    row["card"].update(entries_after_bucket=3, entries_mid_play_strict=3)
    line = next(ln for ln in va.table([row]).splitlines() if ln.startswith("nfl/hindsight"))
    assert line.endswith("bucket joins 4 (grader: 3 entries right after a made basket, strict mid-play 3)")


def test_the_graders_made_basket_entries_ride_beside_the_high_leverage_leaves():
    g = va.Graded()
    g.extend([(1.0, "x")])
    g.after_bucket += 2
    assert len(g) == 1 and g.after_bucket == 2 and va.Graded().after_bucket == 0
