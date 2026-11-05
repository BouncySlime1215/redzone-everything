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
