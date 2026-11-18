"""Package-pick label interface (redzone/lab/taste/package_pick.py), shared by D-01 and E-03."""
from __future__ import annotations

import pytest

from redzone.lab.taste.package_pick import PackageClip, PackagePickLabel


def _clips(n=3):
    return tuple(PackageClip(clip_id=f"c{i}", game_id=f"g{i}", league="nba", age_s=30.0 * i) for i in range(n))


def test_full_order_gives_sequential_choices():
    lab = PackagePickLabel("p1", "2026-11-13T20:00:00Z", "picker", _clips(3), order=(2, 0, 1))
    lab.validate()
    assert lab.first_clip().clip_id == "c2"
    sets = lab.choice_sets()
    assert [(len(o), k) for o, k in sets] == [(3, 2), (2, 0)]
    assert [c.clip_id for c in sets[1][0]] == ["c0", "c1"]


def test_prefix_order_gives_one_choice():
    lab = PackagePickLabel("p2", "2026-11-13T20:00:00Z", "show", _clips(4), order=(3,),
                           propensities=(0.25, 0.25, 0.25, 0.25))
    assert lab.choice_sets() == [(list(_clips(4)), 3)]


@pytest.mark.parametrize("kw", [
    {"source": "tv"},
    {"clips": _clips(1), "order": (0,)},
    {"order": (0, 0)},
    {"order": (5,)},
    {"order": ()},
    {"propensities": (0.5, 0.5)},
    {"propensities": (0.5, 0.4, 0.3)},
    {"clips": (PackageClip("x", "g", "nba"), PackageClip("x", "h", "nba"))},
])
def test_bad_labels_are_refused(kw):
    base = {"label_id": "p", "at": "2026-11-13T20:00:00Z", "source": "picker", "clips": _clips(3), "order": (0,)}
    base.update(kw)
    with pytest.raises(ValueError):
        PackagePickLabel(**base).validate()
