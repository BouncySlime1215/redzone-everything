"""[EXP-026] replay.urgent_clears_top (off): past half its due time a missed play clears the `top` bar.
North Star: big plays live or replayed within 3 min. Owner: lane 2 (noggin logic). Failed its bar; ships off."""
from redzone.decider.decider import Missed
from tests.test_decider import cands, dec  # noqa: F401


def _setup(dec, clock, on):
    dec.cfg.update({"replay": {"redzone_way": True, "urgent_clears_top": on}}, record=False)
    m = Missed("b", "p1", "goal", "score", clock.t - 150, 10.0, due_s=180.0)
    dec.queue.append(m)
    dec._replayable = lambda x: True
    dec._crunch_on = lambda c: False
    return m


def test_off_by_default_a_low_value_play_does_not_beat_live_play(dec, clock):
    _setup(dec, clock, False)
    assert dec._best_replay(cands(a=90, b=10), clock.t, None, False, top=90.0, margin=10.0, yours=False) is None


def test_on_a_play_past_half_its_due_time_clears_the_bar(dec, clock):
    m = _setup(dec, clock, True)
    assert dec._best_replay(cands(a=90, b=10), clock.t, None, False, top=90.0, margin=10.0, yours=False) is m
