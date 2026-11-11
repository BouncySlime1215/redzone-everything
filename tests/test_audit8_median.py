"""AUDIT-8: the table of typical stoppage lengths names the kinds the tracker actually produces."""
from redzone.decider.cuts import MEDIAN_S, DeadTracker, League

T0 = 10_000.0


def _add(dt, pid, at, ttext, clock, period, text=""):
    dt.add(pid, at, at, ttext.lower().replace(" ", "_"), text or ttext, ttext, clock, period)


def test_a_quarters_first_timeout_reads_as_the_long_commercial_it_is():
    """The tracker names NBA timeouts timeout_long / timeout_short; the table only ever had "timeout", so every one read
    as a 30 s stop: _short_stop rode out each 3-minute ad (unless Timing's model answered first)."""
    dt = DeadTracker(League.NBA)
    _add(dt, "shot", T0 - 5, "Jump Shot", 715.0, 1)
    _add(dt, "to", T0, "Timeout", 700.0, 1, text="Team timeout")
    w = dt.windows()[-1]
    assert w.kind == "timeout_long"
    assert dt.resume_eta(T0 + 1) - (T0 + 1) > 90.0


def test_a_college_media_timeout_is_a_long_stop():
    dt = DeadTracker(League.CBB)
    _add(dt, "to", T0, "Official TV Timeout", 700.0, 1, text="Official TV timeout")
    w = dt.windows()[-1]
    assert w.kind == "media_timeout"
    assert dt.resume_eta(T0 + 1) - (T0 + 1) > 90.0


def test_the_table_names_each_basketball_kind_a_timeout_becomes():
    assert {"timeout_long", "media_timeout"} <= set(MEDIAN_S[League.NBA])
