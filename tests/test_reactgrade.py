"""[EXP-301] The delay-and-react grader: time-to-see on one ruler, honesty faults, paired verdicts."""
import math

from redzone.grading import SwitchLog
from redzone.lab import reactgrade as R
from redzone.lab.backtest import Replay
from redzone.models import League

LAG = 40.0      # real life -> screen
TUNE = 5.0


def nfl_game(gid, t0, scores=()):
    """A play every 40 s for an hour; the plays at `scores` (indexes) are touchdowns."""
    plays, wall = [], []
    for i in range(90):
        td = i in scores
        plays.append({"type": {"text": "Passing Touchdown" if td else "Pass Reception"}, "scoringPlay": td,
                      "period": {"number": 1}, "clock": {"displayValue": "10:00"}, "text": "play"})
        wall.append(t0 + 40.0 * i)
    return Replay(League.NFL, gid, {}, plays, wall, matchup=f"AAA @ {gid.upper()}")


def row(arm, switches, windows=(), holds=(), dvr=(), buffer_s=0.0, night="20261101"):
    reps = [nfl_game("g1", 1000.0, scores=(10,)), nfl_game("g2", 1000.0, scores=(30,))]
    return R.night_row(arm, night, "fit", reps, switches, {"WSH"}, LAG, TUNE, 1000.0, 1000.0 + 3600,
                       list(windows), 8.0, list(holds), list(dvr), oracle_big=2, buffer_s=buffer_s)


def test_live_play_is_zero_and_unseen_is_never():
    # On g1 all night: its touchdown (play 10) is live, g2's (play 30) is never seen.
    r = row("A", [SwitchLog(900.0, None, "g1", "manual", "")], holds=[(900.0, "s1", "g1")])
    assert r["big"] == 2 and r["big_live"] == 1
    assert sorted(r["tts"]) == [-1.0, 0.0]            # grader v14's encoding: -1 = never
    assert sum(r["faults"].values()) == 0


def test_a_replay_counts_from_live_end_to_clip_end_when_a_stick_held_it():
    td = 1000.0 + 40 * 30            # g2's touchdown, wall clock
    start, end = td + 30.0, td + 30.0 + 25.0
    sw = [SwitchLog(900.0, None, "g1", "manual", ""), SwitchLog(start, "g1", "g2", "replay", ""),
          SwitchLog(end, "g2", "g1", "return", "")]
    r = row("B", sw, windows=[("g2", td, start, end)], holds=[(900.0, "s1", "g1"), (900.0, "s2", "g2")])
    assert sum(r["faults"].values()) == 0
    seen = sorted(x for x in r["tts"] if x >= 0)
    assert seen[0] == 0.0 and math.isclose(seen[1], end - (td + LAG), abs_tol=0.05)
    # Nobody held g2 and it isn't on YouTube TV: there was nothing to rewind.
    r = row("B", sw, windows=[("g2", td, start, end)], holds=[(900.0, "s1", "g1")])
    assert r["faults"]["replay_unheld"] == 1
    assert row("B", sw, windows=[("g2", td, start, end)], holds=[(900.0, "s1", "g1")], dvr=["g2"])["faults"]["replay_unheld"] == 0
    # A replay that starts before the feed could have told anyone: it knew too soon.
    early = td + 2.0
    sw2 = [sw[0], SwitchLog(early, "g1", "g2", "replay", ""), SwitchLog(early + 25, "g2", "g1", "return", "")]
    r = row("B", sw2, windows=[("g2", td, early, early + 25)], holds=[(900.0, "s1", "g1"), (900.0, "s2", "g2")])
    assert r["faults"]["replay_early"] == 1


def test_a_buffered_arm_is_live_on_the_north_star_ruler_and_late_on_the_broadcast_one():
    sw = [SwitchLog(900.0, None, "g1", "manual", "")]
    r = row("A", sw, holds=[(900.0, "s1", "g1")], buffer_s=90.0)
    assert r["big_live"] == 1 and 0.0 in r["tts"]      # live on its own screen: backtest.time_to_see, grader v14
    m = R.metrics([r])
    assert m["broadcast_le_90_pct"] == 50.0 and m["broadcast_le_30_pct"] == 0.0 and m["seen_le_0_pct"] == 50.0
    assert min(r["behind"]) == 90.0                    # and the whole night runs 90 s behind (spoiler line)


def test_a_rewind_needs_a_buffer():
    sw = [SwitchLog(900.0, None, "g1", "manual", ""), SwitchLog(2000.0, "g1", "g2", "cut", "", offset=30.0)]
    r = row("D", sw, holds=[(900.0, "s1", "g1"), (1990.0, "s2", "g2")])
    assert r["faults"]["rewind_unbuffered"] == 1       # s2 had g2 for 10 s, not 30
    r = row("D", sw, holds=[(900.0, "s1", "g1"), (1900.0, "s2", "g2")])
    assert r["faults"]["rewind_unbuffered"] == 0


def _fake(arm, night, seen, entries=0, faults=0, sw=4, crunch_live=5, dead=100.0):
    return {"arm": arm, "night": night, "split": "fit", "league": "nfl", "cond": "", "big": 5,
            "big_live": seen, "tts": [0.0] * seen + [-1.0] * (5 - seen), "crunch": 5, "crunch_live": crunch_live,
            "my_crunch": 0, "my_crunch_live": 0, "entries_mid_play": entries, "mid_play_cuts_high": 0,
            "dead_s": dead, "watched_s": 3600.0, "switches": sw, "replays": 0, "hours": 1.0, "behind": [0.0] * 10,
            "faults": {"replay_unheld": faults}, "oracle_big_live": 5, "buffer_s": 0.0}


def test_verdicts_are_paired_and_the_hard_zeros_win_over_any_lift():
    nights = [f"2026100{i}" for i in range(1, 10)]
    rows = [_fake("A", n, 2) for n in nights] + [_fake("B", n, 4) for n in nights] \
        + [_fake("C", n, 5, entries=1 if n == nights[0] else 0) for n in nights] \
        + [_fake("D", n, 2) for n in nights]
    rep = R.report(rows, "fit")["nfl|"]
    assert rep["nights"] == 9
    assert rep["arms"]["B"]["verdict"] == "WIN" and rep["winner"] == "B"
    assert rep["arms"]["C"]["verdict"] == "FAIL"       # one mid-play entry: no lift buys it back
    assert rep["arms"]["D"]["verdict"] == "NO_WIN"
    # Too few nights: PENDING, never a win.
    few = [x for x in rows if x["night"] in nights[:3]]
    assert R.report(few, "fit")["nfl|"]["arms"]["B"]["verdict"] == "PENDING"


def test_guardrails_block_a_win():
    nights = [f"2026100{i}" for i in range(1, 10)]
    rows = [_fake("A", n, 2) for n in nights] + [_fake("B", n, 4, sw=20) for n in nights] \
        + [_fake("C", n, 4, crunch_live=0) for n in nights]
    arms = R.report(rows, "fit")["nfl|"]["arms"]
    assert arms["B"]["verdict"] == "NO_WIN"            # 5x the switching
    assert arms["C"]["verdict"] == "NO_WIN"            # crunch live down 100 points


def test_seeds_of_one_night_are_one_cluster():
    nights = [f"2026100{i}" for i in range(1, 10)]
    rows = [_fake(a, n, 2) for a in "AB" for n in nights for _ in range(3)]
    rep = R.report(rows, "fit")["nfl|"]
    assert rep["nights"] == 9 and rep["arms"]["A"]["big"] == 9 * 3 * 5


def test_ratchet_ladder_is_reported():
    m = R.metrics([_fake("A", "n", 3)])
    assert m["seen_le_0_pct"] == 60.0 and m["seen_le_180_pct"] == 60.0 and math.isinf(m["tts_p90"])
    assert R.ratchet()["ladder_s"] == [180, 90, 30, 0] and m["window_s"] == 180.0
    r = _fake("A", "n", 3)
    r["tts"] = [0.0, None, -1.0, 40.0, 200.0]          # None = replayed at an unknown time: never counted as seen
    m = R.metrics([r])
    assert m["tts_unknown"] == 1 and m["seen_le_90_pct"] == 40.0 and m["seen_le_180_pct"] == 40.0
