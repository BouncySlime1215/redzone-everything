"""[EXP-1725] reachable crunch: off by default; with poss_s a decided margin is not crunch."""
from redzone import crunch as C


def test_off_by_default_rows_have_no_poss_s():
    assert all(r.poss_s is None for k in C.CRUNCH_KINDS for r in C.TABLE[k].values())


def test_reachable_rule():
    r = C.Row(4, 180, 7, 5, False, 12.0)
    assert not C.inside(r, 4, 6.5, 7)       # up 7 with 6.5 s left: decided
    assert C.inside(r, 4, 60, 7)            # up 7 with a minute left: still crunch
    assert C.inside(r, 5, 3.9, 3)           # OT, a three ties it
    assert not C.inside(r, 5, 3.9, 4)       # OT, two scores down with 4 s left
    assert C.inside(C.Row(4, 180, 7, 5), 4, 6.5, 7)   # rule off: unchanged
