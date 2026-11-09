"""AUDIT-7: a score-bug change queued on one tune is not timed against the next tune's stream."""
from redzone.screen.scorebug import ScoreBugWatcher

T = "prime:wiz"


def test_pending_samples_do_not_survive_a_new_tune():
    w = ScoreBugWatcher()
    tune_a, tune_b = object(), object()
    w.observe("k", T, tune_a, 100.0, None, None)
    w.pending.append(("score", T, 100.0, 10, 12, 99.0, 1.0, None))
    w.pending.append(("score", "prime:other", 100.0, 1, 3, 99.0, 1.0, None))
    w.observe("k", T, tune_b, 110.0, None, None)
    assert [p[1] for p in w.pending] == ["prime:other"]
