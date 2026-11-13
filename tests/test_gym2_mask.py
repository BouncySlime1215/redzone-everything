"""Gym v2 slice 3 (task A-01): the Rule #1 mask never allows a cut the FROZEN grader counts as a Rule #1 break; census
mixes are drawn and composed reproducibly from dev games only; the reward terms add up to what the judge saw.

The mask is checked against the grader's own scoring code on thousands of random cuts over a recorded dev night:
`cutgrade.grade` for joins mid-play (and, at the strict level, leaves mid-play), and sportsgrade's own clean-cut rule
(`_clean_cut`, a separate implementation from the `grading.clean_leave` the mask calls) with `leverage.high_leverage`
for high-leverage mid-play leaves, plus the replay judge's high-leverage play spans (park_ceiling.truth_of).

Why: the director ranks options and never approves a jump; the mask is what keeps every option it sees legal.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from redzone.lab.gym2 import env as gym
from redzone.lab.gym2 import slates
from redzone.lab.gym2.mask import Rule1Mask

ROOT = Path(__file__).resolve().parent.parent
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def _night(night="20261023", n=4):
    pr = gym.replay_module()
    import park_ceiling as pc
    rs, slate = pr.load_replay(night, n)
    base = int(min(g["t0"] for g in slate))
    return rs, slate, base, pc


def _candidates(rs, base, n, seed=0):
    rng, out = random.Random(seed), []
    spans = [(int(r.wall[0] - base) - 60, int(r.wall[-1] - base) + 60) for r in rs]
    while len(out) < n:
        g1, g2 = rng.sample(range(len(rs)), 2)
        out.append((g1, rng.randint(*spans[g1]), g2, rng.randint(*spans[g2])))
    return out


def _grade(rs, base, cuts):
    from redzone.grading import SwitchLog
    from redzone.lab import cutgrade
    log = sorted((SwitchLog(base + j, rs[g1].gid, rs[g2].gid, "jump", "gym2 mask test", leave_dt=i - j) for g1, i, g2, j in cuts),
                 key=lambda s: s.t)
    return cutgrade.grade(rs, log, 0.0, 0.0)


def _high_leaves(rs, base, cuts):
    """sportsgrade's cuts_high test, on its own code: an unclean leave whose feed row on screen is high leverage."""
    from redzone.lab import leverage
    from redzone.lab.sportsgrade import _clean_cut
    n = 0
    for g1, i, _, _ in cuts:
        r, st = rs[g1], base + i
        if not _clean_cut(r.league, r.wall, r.plays, st)[0]:
            at = max((k for k, w in enumerate(r.wall) if w <= st), default=0)
            n += bool(leverage.high_leverage(r.league, r.plays[at]))
    return n


@needs_archive
@pytest.mark.parametrize("level", ["strict", "rule"])
def test_the_mask_never_allows_a_cut_the_frozen_grader_counts_as_a_rule_1_break(level):
    rs, slate, base, pc = _night()
    cands = _candidates(rs, base, 4000)
    m = Rule1Mask(rs, base, level=level)
    ok = [c for c, a in zip(cands, m.mask(cands)) if a]
    assert 200 < len(ok) < len(cands) - 200                       # neither vacuous nor allow-all
    everything = _grade(rs, base, cands)
    assert everything.entries_mid_play > 100                      # the candidates do hold real breaks...
    card = _grade(rs, base, ok)
    assert card.entries_mid_play == 0                             # ...and none survives the mask
    assert _high_leaves(rs, base, ok) == 0
    if level == "strict":
        assert card.leaves - card.leaves_in_dead - card.leaves_between_plays == 0
        hl = [[(a - base, b - base) for a, b in g["hl"]] for g in (pc.truth_of(r) for r in rs)]
        assert not any(a <= i <= b for g1, i, _, _ in ok for a, b in hl[g1])


def test_draw_mix_is_census_weighted_capped_and_keeps_nfl_to_one_game():
    have = {"nba", "nfl", "cbb", "cfb"}
    assert slates.draw_mix(9, have=have) == slates.draw_mix(9, have=have)
    mixes = [slates.draw_mix(s, have=have) for s in range(3000)]
    assert all(2 <= sum(m.values()) <= slates.MIX_MAX_GAMES and m.get("nfl", 0) <= 1 for m in mixes)
    only = [slates.draw_mix(s, have={"nba", "nfl"}) for s in range(3000)]
    assert all(set(m) <= {"nba", "nfl"} for m in only)
    w = {k: v for k, v in slates.CENSUS_MINUTES.items() if set(slates.parse_mix(k)) <= {"nba", "nfl"}}
    share = sum(m == {"nba": 2} for m in only) / len(only)
    assert abs(share - w["nba:2"] / sum(w.values())) < 0.03
    with pytest.raises(ValueError):
        slates.draw_mix(1, have={"mlb"})


@needs_archive
def test_a_composed_slate_uses_dev_games_only_staggered_and_moved_whole():
    if not {"nba", "nfl"} <= slates.available():
        pytest.skip("pinned eval data not fetched (nba and nfl dev games)")
    mix = {"nba": 2, "nfl": 1}
    rs, slate = slates.compose(mix, 4)
    rs2, _ = slates.compose(mix, 4)
    assert [r.gid for r in rs] == [r.gid for r in rs2] and [r.wall[0] for r in rs] == [r.wall[0] for r in rs2]
    fit = {lg: {g for _, g in slates.fit_games(lg)} for lg in mix}
    assert all(r.gid in fit[r.league.value] for r in rs)
    starts = sorted(r.wall[0] for r in rs)
    assert starts[-1] - starts[0] <= slates.STAGGER[1]
    assert len(slate) == 3 and sorted(str(r.league.value) for r in rs) == ["nba", "nba", "nfl"]


@needs_archive
def test_reward_terms_add_up_to_what_the_judge_saw_and_rule_1_is_only_a_diagnostic():
    env = gym.GymV2("20261023", 4, minutes=10)
    env.reset()
    total, value = 0.0, 0.0
    while True:
        _, r, done, info = env.step()
        total += r
        value += info["terms"]["value"]
        assert info["terms"]["staleness"] <= 0.0 and set(info["rule1"]) == {"mid_entry", "mid_leave", "hl_leave"}
        if done:
            break
    assert value == pytest.approx(env.show.judge.v2) and total == pytest.approx(value)       # default weights: value only
    audit = info["mask_audit"]
    assert audit["cuts"] == len(env.show.judge.cuts) > 0


@needs_archive
def test_a_mixed_episode_runs_and_is_logged_with_its_mix(tmp_path):
    if "nfl" not in slates.available():
        pytest.skip("pinned eval data not fetched (nfl dev games)")
    log = tmp_path / "d.jsonl"
    env = gym.GymV2("mix-test", 2, seed=3, mix={"nba": 1, "nfl": 1}, minutes=10, draw_log=log)
    first = env.run().digest()
    import json
    row = json.loads(log.read_text().splitlines()[0])
    assert row["mix"] == {"nba": 1, "nfl": 1}
    assert gym.GymV2.from_draw(row, minutes=10).run().digest() == first
