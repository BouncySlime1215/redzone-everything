"""[EXP-728/736] break tail: the restart table lookup and the flag's default."""
from redzone import config
from redzone.decider import break_tail as bt


def test_lengths_by_network_then_league_row():
    assert bt.network_of("youtubetv:nbatv") == "NBATV" and bt.network_of(None) == ""
    assert bt.length("nba", "end_period", "youtubetv:nbatv", "q25") == 164.0     # the network's own row (n >= 8)
    assert bt.length("nba", "end_period", "youtubetv:nope", "q25") == 157.0      # else the league x kind row
    assert bt.length("nba", "nonsense", "youtubetv:espn", "q25") is None
    assert bt.WINDOW_KIND["timeout_long"] == "timeout" and "timeout_short" not in bt.WINDOW_KIND and "shooting_foul" not in bt.WINDOW_KIND


def test_ships_off():
    assert config.default("decider.entry.break_tail") == []
    assert config.default("decider.entry.break_tail_s") == 10 and config.default("decider.entry.break_tail_q") == "q25"


def test_exp803_arms_ship_off():
    """[EXP-803 A1/A3] the arms' flags ship off; the A3 length is an early-side prior (Data F's cdn q10)."""
    import dataclasses

    from redzone.decider.decider import Decision
    assert config.default("decider.entry.break_tail_defer") is False
    assert config.default("decider.entry.break_tail_tv_split") is False
    assert 150 <= config.default("decider.entry.break_tail_tv_timeout_s") <= 180
    assert {f.name: f.default for f in dataclasses.fields(Decision)}["defer_s"] == 0.0
