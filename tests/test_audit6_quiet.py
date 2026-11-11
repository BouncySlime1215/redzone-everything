"""AUDIT-6: a quiet feed after a score means a try and a kickoff in football. In baseball the next batter is up."""
from redzone.decider.screen import _entry_label
from redzone.models import League, Play


def _scoring(**kw):
    return Play("p", "single scores a run", "single", scoring=True, **kw)


def test_a_run_in_baseball_does_not_open_a_quiet_break():
    assert _entry_label(_scoring(), League.MLB) == ""


def test_a_score_in_football_still_does():
    assert _entry_label(_scoring(), League.NFL) == "score"
    assert _entry_label(_scoring(), League.CFB) == "score"


def test_the_end_of_an_inning_still_does():
    assert _entry_label(Play("p", "", "end_inning", type_text="End Inning"), League.MLB) == "period_end"
