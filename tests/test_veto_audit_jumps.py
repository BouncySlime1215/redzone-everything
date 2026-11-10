"""[NBA-YES, audit] The veto audit's jump counts: cuts that went, per minute and per sliding window. No product behaviour.

Why: Nick wants the yes-rate to rise together with the number of jumps, never by the brain asking for less, so every audit table
has to show how often the screen really switched, next to the yes-rate.
"""
from types import SimpleNamespace as NS

from scripts import veto_audit as va
from tests.test_yes_audit import _main, _row


def _tally(**kw):
    ticks = [float(x) for x in range(0, 3600, 10)]                 # an hour of 10 s ticks
    return va.Tally(**{"tick_at": ticks, "live2": ticks, **kw})


def test_a_nights_jumps_sit_on_the_time_two_games_were_live():
    ticks = [float(x) for x in range(0, 1200, 10)]
    live = [t for t in ticks if t < 300 or t >= 900]                # one game alone from minute 5 to minute 15
    n = va.jump_night(va.Tally(tick_at=ticks, live2=live, jumps=[60.0, 600.0, 900.0, 1190.0], clips=2))
    assert n == {"live_min": 10.0, "at_min": [1.0, 5.0, 9.833], "clips": 2}     # the cut at 600 s had one game live: not a jump here
    assert va.jump_night(va.Tally()) == {"live_min": 0.0, "at_min": [], "clips": 0}


def test_windows_slide_by_a_minute_and_a_short_night_is_skipped():
    assert va.window_counts(12.0, [0.5, 4.9, 5.0, 11.5], 5) == [2, 2, 2, 2, 2, 1, 0, 1]      # starts 0..7; [0, 5) holds 0.5 and 4.9, not 5.0
    assert va.window_counts(12.0, [0.5], 10) == [1, 0, 0] and va.window_counts(4.9, [0.5], 5) == []
    assert va.window_counts(5.0, [], 5) == [0]


def test_the_quantile_is_nearest_rank():
    xs = list(range(1, 11))
    assert (va.quantile(xs, 0.10), va.quantile(xs, 0.5), va.quantile(xs, 0.9), va.quantile(xs, 1.0)) == (1, 5, 9, 10)
    assert va.quantile([], 0.5) == 0.0 and va.quantile([7], 0.10) == 7


def test_the_stats_pool_nights_and_count_the_nights_long_enough():
    long_night = {"live_min": 60.0, "at_min": [float(m) for m in range(0, 60, 6)], "clips": 3}       # a jump every 6 minutes
    short_night = {"live_min": 8.0, "at_min": [1.0, 2.0, 3.0], "clips": 0}
    j = va.jump_stats([{"jumps": {"nights": [long_night, short_night]}}])
    assert (j["live_min"], j["jumps"], j["clips"]) == (68.0, 13, 3) and j["per_min"] == round(13 / 68, 4) and j["clips_per_min"] == round(3 / 68, 4)
    w = j["windows"]
    assert w["5"]["nights"] == 2 and w["10"]["nights"] == 1 and w["60"] == {"median": 10, "p10": 10, "max": 10, "nights": 1, "windows": 1}
    assert (w["5"]["max"], w["5"]["p10"]) == (3, 0) and w["30"]["median"] == 5 and w["30"]["windows"] == 31
    empty = va.jump_stats([{}])
    assert empty["per_min"] == 0.0 and empty["windows"]["5"] == {"median": 0.0, "p10": 0.0, "max": 0, "nights": 0, "windows": 0}


def test_the_lines_print_each_row_and_the_brains_total_and_nothing_without_nights():
    assert va.jump_lines([_row()]) == [] and "jumps:" not in va.table([_row()])
    row = _row()
    row["jumps"] = {"nights": [{"live_min": 60.0, "at_min": [float(m) for m in range(0, 60, 6)], "clips": 3}]}
    lines = va.jump_lines([row])
    assert lines[1].startswith("jumps: cuts that went while two or more games were live") and "SIMULATED" in lines[1]
    assert lines[2].split()[:8] == ["league/brain", "live", "min", "jumps", "/min", "/hour", "clips", "/min"]
    cells = lines[3].split()
    assert cells[:7] == ["nfl/hindsight", "60", "10", "0.167", "10.0", "3", "0.050"]
    assert " ".join(cells[7:13]) == "1 / 0 / 1 (1)" and lines[3].rstrip().endswith("10 / 10 / 10 (1)") and lines[4].startswith("TOTAL hindsight")
    assert va.table([row]).splitlines()[-3:] == lines[2:]              # after the yes-rate, at the end of the table


def test_a_cut_that_went_is_a_jump_at_its_tick():
    a = va.AuditDecider.__new__(va.AuditDecider)
    a.tally, a.truth, a.land_kind = va.Tally(), NS(tune_s=5.0), ""
    a._commit, a._land_behind = lambda target, kind=None: None, lambda target, rewind_s: None
    a._go(NS(target="b", kind="better", rewind_s=0.0), 100.0)
    assert a.tally.jumps == [100.0] and a.tally.went == 1


def test_a_night_adds_its_jumps_and_clips_to_the_row():
    t = va.Truth()
    t.tune_s, t.moments = 5.0, {}
    row = va.new_row()
    va.charge_night(_tally(jumps=[0.0, 1800.0]), t, row)
    assert row["jumps"]["nights"] == [{"live_min": 60.0, "at_min": [0.0, 30.0], "clips": 0}]
    assert va.jump_stats([row])["windows"]["60"]["median"] == 2 and va.jump_stats([row])["per_min"] == round(2 / 60, 4)


def test_each_delay_error_arm_says_its_jumps_per_hour(monkeypatch, tmp_path, capsys):
    def rows_for(job):
        row = _row()
        row["jumps"] = {"nights": [{"live_min": 60.0, "at_min": [1.0, 2.0] if job[2][va.DELAY_ERROR_KEY] > 0 else [1.0], "clips": 0}]}
        return row
    text, out, _ = _main(monkeypatch, tmp_path, capsys, rows_for, "--delay-error", "2")
    assert "true delay +2 s:" in text and ", jumps per hour 2.0" in text and ", jumps per hour 1.0" in text
    assert out["arms"]["+2"]["jumps"]["jumps"] == 2 and out["arms"]["-2"]["jumps"]["per_min"] == round(1 / 60, 4)
