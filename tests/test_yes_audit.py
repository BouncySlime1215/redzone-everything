"""[YES-1, audit] The veto audit's yes-rate, gate:leave's sub-causes, the held-cut fix and the delay-error arm. No product behaviour.

Why: Nick wants the code's "no" to be rare (95% yes), and five inspections found most leave refusals false; a fix can only be judged with a
count of what the brain got, a name for which part of the leave rule refused, and a run where the app's delay estimate is wrong.
"""
import json
import sys
from collections import Counter
from types import SimpleNamespace as NS

import pytest

from redzone.decider import gate
from redzone.decider.decider import State
from redzone.models import Phase
from redzone.noggin import arbiter, legal
from scripts import veto_audit as va
from tests.test_exits_catchup import cand, host


# ----- yes-rate ---------------------------------------------------------------------------------------------------------------------

def test_yes_needs_the_wanted_games_picture_up_within_20_s():
    screen = [(105.0, "b"), (300.0, "a"), (421.0, "b")]
    wants = [(100.0, "b"), (290.0, "b"), (400.0, "b"), (401.0, "b"), (100.0, "c")]
    assert va.yes_counts(wants, screen) == (2, 0)          # 105 and 421 (from 401); 421 is 21 s after 400; c never came up


def test_a_picture_already_up_before_the_tick_is_not_a_yes():
    assert va.yes_counts([(100.0, "b")], [(100.0, "b")]) == (0, 0)       # only a picture that comes up AFTER the brain asked


def test_a_clip_counts_only_for_the_wanted_game_within_60_s_and_never_twice():
    wants = [(100.0, "b"), (100.0, "c"), (200.0, "b")]
    clips = [(150.0, "b"), (161.0, "c"), (205.0, "a")]
    assert va.yes_counts(wants, [(210.0, "b")], clips) == (1, 1)         # b: clip at 150; c: 61 s is late; b at 200: live, not a clip


def test_the_rate_is_summed_over_rows_and_empty_rows_print_nothing():
    rows = [{"league": "nfl", "brain": "hindsight", "yes": {"wanted": 10, "live": 4, "clip": 1}},
            {"league": "nba", "brain": "hindsight", "yes": {"wanted": 10, "live": 6, "clip": 0}},
            {"league": "nba", "brain": "heuristic", "yes": {}}]
    assert va.yes_rate(rows) == {"wanted": 20, "live": 10, "clip": 1, "yes_pct": 50.0, "clip_pct": 5.0}
    lines = va.yes_lines(rows)
    assert lines[3].split() == ["nfl/hindsight", "10", "4", "40.0%", "1", "10.0%", "50.0%"]
    assert lines[-1].split() == ["TOTAL", "20", "10", "50.0%", "1", "5.0%", "55.0%"] and lines[-3].startswith("TOTAL hindsight")
    assert va.yes_lines([{"league": "nfl", "brain": "hindsight"}]) == [] and va.yes_rate([])["yes_pct"] == 0.0


def test_a_night_writes_its_yes_counts_to_the_row():
    t = va.Truth()
    t.tune_s, t.moments = 5.0, {}
    row = va.new_row()
    va.charge_night(va.Tally(wants=[(100.0, "b"), (200.0, "b")], screen=[(105.0, "b")]), t, row)
    assert row["yes"] == Counter({"wanted": 2, "live": 1})


# ----- gate:leave names its sub-cause -----------------------------------------------------------------------------------------------

def _refusal(d, phase, now=200.0, need=8.0):
    return gate.leave_refusal(d, "on", "yt:on", phase, now, 8.0, need)


def test_the_leave_refusal_names_each_check_in_order():
    d = host()
    d.replay_until = d.replay_exit_after = 0.0
    assert d._ending_still_airing("on", 200.0) > 0
    assert _refusal(d, Phase.LIVE) == "unaired_row"                      # cause A: a live game's newest row has not aired
    assert _refusal(d, Phase.POST) == _refusal(d, None) == "ended_airing"
    assert gate.leave_refusal(d, "reel", None, None, 200.0, 8.0) == "" and gate.leave_refusal(d, None, None, None, 200.0, 8.0) == ""
    late = 165.0 + 35.0 + 2.0 + 5.0 + 1.0                               # past the row's airing: the window is asked
    assert d._ending_still_airing("on", late) == 0
    assert _refusal(d, Phase.LIVE, late) == "window" and _refusal(d, Phase.POST, late) == ""
    d.timelines.pop("on")
    assert _refusal(d, Phase.LIVE) == "no_timeline"
    d.replaying, d.replay_until = True, 250.0
    assert _refusal(d, Phase.LIVE) == "replay" and _refusal(d, Phase.LIVE, 250.0) == ""


@pytest.mark.parametrize("phase", [Phase.LIVE, Phase.POST, None])
@pytest.mark.parametrize("now", [200.0, 208.0, 400.0])
def test_leave_open_is_exactly_no_refusal(phase, now):
    d = host()
    assert gate.leave_open(d, "on", "yt:on", phase, now, 8.0, 8.0) is (not _refusal(d, phase, now))


def _flip(d, cands, *, held=False, now=200.0):
    dec = va.Decision(now, "switch", "a", "", State.WATCHING, kind="better", leave_held=held)
    return gate.Flip(gate.DeciderHost(d, cands, now), dec, "on", "yt:a", cands, None, now)


def test_the_cause_is_named_only_when_the_leave_rule_says_no():
    cands = {"on": cand(), "a": cand()}
    d = host(())
    assert gate.leave_cause(_flip(d, cands)) == "unaired_row"
    assert gate.leave_cause(_flip(d, cands, now=208.0)) == "window"
    d = host(("nfl",))
    assert gate._leave(_flip(d, cands, held=True)) is False and gate.leave_cause(_flip(d, cands, held=True)) == ""    # the held exit opened it


def test_the_charge_files_gate_leave_under_its_sub_cause():
    a = va.AuditDecider.__new__(va.AuditDecider)
    a.tally, a._current = va.Tally(), "on"
    a.truth = NS(lands_mid_play=lambda g, n: False, mid_play_on_screen=lambda g, n: True, high_leverage_on_screen=lambda g, n: False)
    type(a).current = property(lambda self: self._current)
    try:
        a.leave_sub = "unaired_row"
        a._charge("b", ["gate:leave"], 100.0)
        a.leave_sub = "window"
        a._charge("b", ["legal:no_landing", "gate:leave"], 110.0)
        a._charge("b", ["legal:no_landing"], 120.0)
    finally:
        del type(a).current
    sub = a.tally.leave_sub
    assert sub["any"] == Counter({"gate:leave_unaired_row": 1, "gate:leave_window": 1})
    assert sub["first"] == Counter({"gate:leave_unaired_row": 1, "gate:leave_window": 0}) and sub["real"]["gate:leave_window"] == 1
    assert [e[4] for e in a.tally.log["b"]] == ["gate:leave_unaired_row", "gate:leave_window", ""]


def test_lost_value_follows_the_sub_cause_and_the_lines_show_it():
    air = va.Air(1000.0, "b", "td", 5.0)
    t = va.Truth()
    t.tune_s, t.moments = 5.0, {"b": [air]}
    row = va.new_row()
    va.charge_night(va.Tally(log={"b": [(950.0, "gate:leave", True, False, "gate:leave_unaired_row")]}), t, row)
    assert row["lost"] == Counter({"gate:leave": 5.0}) and row["leave_sub"]["lost"] == Counter({"gate:leave_unaired_row": 5.0})
    row["leave_sub"]["any"]["gate:leave_unaired_row"], row["leave_sub"]["first"]["gate:leave_unaired_row"] = 4, 3
    row["leave_sub"]["real"]["gate:leave_unaired_row"] = 1
    lines = va.leave_lines([row])
    assert lines[-1].split() == ["gate:leave_unaired_row", "4", "3", "25%", "5.0"]
    assert va.leave_lines([va.new_row()]) == []


# ----- fix H: the audit judges the cut the product would make ------------------------------------------------------------------------

def test_judge_builds_a_held_cut_exactly_when_leave_at_flip_is_on(monkeypatch):
    seen = []

    class Flip:
        replan = (False, None)

        def __init__(self, host, d, *a):
            seen.append(d)

    monkeypatch.setattr(va.gate, "Flip", Flip)
    monkeypatch.setattr(va.gate, "GATE_RULES", ())
    monkeypatch.setattr(va, "ReplayHost", lambda h, cands: None)
    for on in (False, True):
        h = NS(legal_facts=lambda c, n: legal.Facts(now=n, current="a", games=(legal.GameRow("a"), legal.GameRow("b"))),
               cfg=NS(get=lambda k, d=None: d), _arbiter_params=lambda: arbiter.DEFAULT_PARAMS, tally=NS(ticks=1), brain=NS(name="t"),
               pick_scores={"stay": 0.0, "cut:b": 50.0}, _arbiter_show=lambda c, n, p: arbiter.Show(current="a", live_games=2),
               _arbiter_musts=lambda c, n, p: (), base_state=State.WATCHING, base_owner="", current="a", _leave_at_flip=lambda: on)
        named, ch, vetoes, d = va.judge(h, "b", {"b": NS(target="yt:b")}, 1000.0)
        assert (named, vetoes, ch.option.id, h.leave_sub) == ([], [], "cut:b", "")
    assert [d.leave_held for d in seen] == [False, True]


# ----- --delay-error ----------------------------------------------------------------------------------------------------------------

def _row(mid=0, yes=5):
    row = va.new_row()
    row["league"], row["brain"], row["wanted"], row["went"] = "nfl", "hindsight", 10, yes
    row["card"] = {"entries_mid_play": mid, "entries": 9, "cuts_high": 0, "mid_play_leaves": 2}
    row["yes"].update(wanted=10, live=yes)
    return json.loads(json.dumps(row))


def _main(monkeypatch, tmp_path, capsys, rows_for, *args):
    jobs = []
    monkeypatch.setattr(va, "run_one", lambda job: jobs.append(job) or rows_for(job))
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["veto_audit.py", "--leagues", "nfl", "--brains", "hindsight", "--workers", "1", "--json", str(out), *args])
    assert va.main() == 0
    return capsys.readouterr().out, json.loads(out.read_text()), jobs


def test_without_the_option_one_run_and_no_new_key(monkeypatch, tmp_path, capsys):
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row())
    assert jobs == [("nfl", "hindsight", {}, None, 15.0)] and sorted(out) == ["avoidable_lost", "rows", "rules", "sets"]
    assert "delay error" not in text and text == va.table([_row()]) + "\n"
    assert _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--delay-error", "0")[0] == text


def test_the_option_runs_both_signs_and_reports_the_worse_arm(monkeypatch, tmp_path, capsys):
    def rows_for(job):
        return _row(mid=3, yes=4) if job[2][va.DELAY_ERROR_KEY] < 0 else _row()
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, rows_for, "--delay-error", "3", "--set", "gate.x=true")
    assert [j[2] for j in jobs] == [{"gate.x": True, va.DELAY_ERROR_KEY: 3.0}, {"gate.x": True, va.DELAY_ERROR_KEY: -3.0}]
    assert out["worse_arm"] == "-3" and out["delay_error_s"] == 3.0 and out["sets"] == {"gate.x": True}
    assert out["rows"] == out["arms"]["-3"]["rows"] and out["arms"]["-3"]["rule1"]["entries_mid_play"] == 3
    assert out["arms"]["+3"]["yes"]["yes_pct"] == 50.0
    assert "the table above is the worse arm (-3 s)" in text and "true delay +3 s: entries_mid_play 0" in text
    assert "true delay -3 s: entries_mid_play 3, mid_play_leaves 2, high-leverage leaves 0, yes 40.0%" in text


def test_with_equal_harm_the_lower_yes_rate_is_the_worse_arm():
    assert va.worse_key([_row(yes=3)]) > va.worse_key([_row(yes=5)])
    assert va.worse_key([_row(mid=1, yes=9)]) > va.worse_key([_row(yes=1)])


def test_the_error_shifts_only_the_true_delay(monkeypatch):
    """The wrapper hands backtest.run a true delay off by the error; the believed delay is untouched, and no error means no key."""
    got = []
    monkeypatch.setattr(va.backtest, "run", lambda replays, cfg, mine, **kw: got.append(kw) or "rep")
    monkeypatch.setattr(va.Truth, "bind", lambda self, replays, lag, tune_s: got.append(lag))
    monkeypatch.setattr(va, "charge_night", lambda t, truth, row: None)
    for sets in ({}, {va.DELAY_ERROR_KEY: 3.0}, {va.DELAY_ERROR_KEY: -6.0}):
        with va.replay("hindsight", sets, va.new_row(), None):
            assert va.backtest.run([], None, set(), feed_lag=8.0, delay=35.0, true_delay=None) == "rep"
    assert got == [43.0, {"feed_lag": 8.0, "delay": 35.0, "true_delay": None},
                   46.0, {"feed_lag": 8.0, "delay": 35.0, "true_delay": 38.0},
                   37.0, {"feed_lag": 8.0, "delay": 35.0, "true_delay": 29.0}]
