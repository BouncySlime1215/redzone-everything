"""Gym v2 slice 3 (task A-01): the Rule #1 mask never allows a cut the FROZEN grader counts as a Rule #1 break, the strict
level is never looser than the grader level and holds under the measured landing error; census mixes are drawn and
composed reproducibly from dev games only; the reward terms add up to what the judge saw.

The mask is checked with the grader's own scoring code, called as it is: `cutgrade.grade` (joins mid-play, leaves
mid-play) and `sportsgrade.grade` (cuts_high: unclean leaves at a high-leverage row). Cuts come from random seconds AND
from targeted spots (late close-game rows, free throws, steals and defensive rebounds that start transition), on two
recorded dev nights, plus the verifier's two adversarial cases of Nov 13 (joins landed late by the measured landing
error; dead-ball leaves just after a high-leverage row).

Why: the director ranks options and never approves a jump; the mask is what keeps every option it sees legal.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from redzone.lab.gym2 import env as gym
from redzone.lab.gym2 import slates
from redzone.lab.gym2.mask import LAND_MARGIN, Rule1Mask

ROOT = Path(__file__).resolve().parent.parent
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")
NIGHTS = ("20261023", "20261028")


def _night(night):
    pr = gym.replay_module()
    rs, slate = pr.load_replay(night, 4)
    return rs, int(min(g["t0"] for g in slate))


def _random(rs, base, n, seed=0):
    rng, out = random.Random(seed), []
    spans = [(int(r.wall[0] - base) - 60, int(r.wall[-1] - base) + 60) for r in rs]
    while len(out) < n:
        g1, g2 = rng.sample(range(len(rs)), 2)
        out.append((g1, rng.randint(*spans[g1]), g2, rng.randint(*spans[g2])))
    return out


def _targeted(rs, base, seed=1):
    """Cuts at -6..+8 s around late close-game rows, free throws, steals and defensive rebounds, as a leave and as a join."""
    from redzone.grading import raw_type
    from redzone.lab import leverage
    rng, out = random.Random(seed), []
    for g, r in enumerate(rs):
        for w, p in zip(r.wall, r.plays):
            kind = raw_type(p)
            if not (leverage.high_leverage(r.league, p) or "free throw" in kind or "steal" in kind or "defensive rebound" in kind):
                continue
            for d in range(-6, 9, 2):
                o = rng.choice([x for x in range(len(rs)) if x != g])
                other = int(rs[o].wall[rng.randrange(len(rs[o].wall))] - base)
                out.append((g, int(w - base) + d, o, other))          # leave here
                out.append((o, other, g, int(w - base) + d))          # join here
    return out


def _joins_mid(rs, base, cuts, shift=0.0):
    from redzone.grading import SwitchLog
    from redzone.lab import cutgrade
    log = sorted((SwitchLog(base + j + shift, rs[g1].gid, rs[g2].gid, "jump", "gym2 mask test", leave_dt=i - j - shift)
                  for g1, i, g2, j in cuts), key=lambda s: s.t)
    return cutgrade.grade(rs, log, 0.0, 0.0)


def _cuts_high(rs, base, cuts):
    """sportsgrade.grade's own cuts_high: it reads a leave at the switch time, so each switch is stamped at the leave."""
    from redzone.grading import SwitchLog
    from redzone.lab import sportsgrade
    log = sorted((SwitchLog(base + i, rs[g1].gid, rs[g2].gid, "jump", "gym2 mask test") for g1, i, g2, j in cuts),
                 key=lambda s: s.t)
    t0, t1 = min(r.wall[0] for r in rs), max(r.wall[-1] for r in rs)
    return sportsgrade.grade(rs, log, set(), 0.0, 0.0, t0, t1).cuts_high


@needs_archive
@pytest.mark.parametrize("night", NIGHTS)
@pytest.mark.parametrize("level", ["strict", "grader"])
def test_the_mask_never_allows_a_cut_the_frozen_grader_counts_as_a_rule_1_break(level, night):
    rs, base = _night(night)
    m = Rule1Mask(rs, base, level=level)
    for cands in (_random(rs, base, 3000), _targeted(rs, base)):
        ok = [c for c, a in zip(cands, m.mask(cands)) if a]
        assert 20 < len(ok) < len(cands) - 100                    # neither vacuous nor allow-all (strict keeps ~2% of random pairs)
        assert _joins_mid(rs, base, cands).entries_mid_play > 50  # the candidates do hold real breaks...
        card = _joins_mid(rs, base, ok)
        assert card.entries_mid_play == 0                         # ...and none survives the mask
        assert _cuts_high(rs, base, ok[:1500]) == []
        if level == "strict":
            assert card.leaves - card.leaves_in_dead - card.leaves_between_plays == 0


@needs_archive
@pytest.mark.parametrize("night", NIGHTS)
def test_strict_is_never_looser_than_the_grader_and_holds_under_the_landing_error(night):
    rs, base = _night(night)
    strict, grader = Rule1Mask(rs, base, level="strict"), Rule1Mask(rs, base, level="grader")
    cands = _random(rs, base, 3000, seed=5) + _targeted(rs, base, seed=6)
    s_ok, g_ok = strict.mask(cands), grader.mask(cands)
    assert not any(s and not g for s, g in zip(s_ok, g_ok))
    ok = [c for c, a in zip(cands, s_ok) if a]
    for shift in (-1.5, 1.5, -LAND_MARGIN, LAND_MARGIN):           # verifier, Nov 13: joins landed 1.5 s late broke
        assert _joins_mid(rs, base, ok, shift).entries_mid_play == 0, shift
    zero_margin = [c for c, a in zip(cands, g_ok) if a]
    assert _joins_mid(rs, base, zero_margin, 1.5).entries_mid_play > 0     # the grader level alone does not hold


@needs_archive
@pytest.mark.parametrize("night", NIGHTS)
def test_strict_never_leaves_just_after_a_high_leverage_play(night):
    """Verifier, Nov 13: dead-ball leaves 1-5 s after a high-leverage row were allowed (the frozen counters do not count
    them; CLAUDE.md's "never leave a high-leverage moment" and the replay's judge do)."""
    from redzone.lab import leverage
    rs, base = _night(night)
    m = Rule1Mask(rs, base, level="strict")
    after = [(g, int(w - base) + d) for g, r in enumerate(rs) for w, p in zip(r.wall, r.plays)
             if leverage.high_leverage(r.league, p) for d in range(1, 6)]
    if not after:
        pytest.skip("pinned eval data not fetched (no high-leverage rows on this night)")
    assert not any(m.leave_ok(g, i).ok for g, i in after)


@pytest.mark.parametrize("bad", ["missing", "broken"])
def test_a_mix_game_that_cannot_be_loaded_fails_loudly(tmp_path, bad):
    d = tmp_path / "data/archive/nba"
    d.mkdir(parents=True)
    if bad == "broken":
        (d / "summary_1.json").write_text("{not json")
    with pytest.raises((FileNotFoundError, ValueError)):
        slates.compose({"nba": 1}, 0, root=tmp_path, games=[["nba", "1", 0]])


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
    rs, slate, used = slates.compose(mix, 4)
    rs2, _, used2 = slates.compose(mix, 4)
    assert used == used2 and len(used) == 3
    again, _, _ = slates.compose(mix, 99, games=used)              # the logged games and shifts, exactly
    assert [r.gid for r in again] == [r.gid for r in rs] and [r.wall for r in again] == [r.wall for r in rs]
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
    row = json.loads(log.read_text().splitlines()[0])
    assert row["mix"] == {"nba": 1, "nfl": 1} and len(row["mix_games"]) == 2 and row["mask_level"] == "strict"
    assert row["weights"]["value"] == 1.0 and row["minutes"] == 10.0 and row["code"]
    assert gym.GymV2.from_draw(row).run().digest() == first
