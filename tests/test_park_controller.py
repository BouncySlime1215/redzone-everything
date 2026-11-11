"""The in-app PARK controller (integration plan unit 8) on fake sticks: the hard guard (B), warrants and soft pace (C),
the planning lessons of the live runs of Nov 10 (D), the seams (F), the Show tab's state (E), and the controller's
own park steps as the show's sticks (A).

The fakes: `scripts/park_replay.FakeRig` (two streams and a TV whose steps take the times measured on Nov 10, with
failures made to order) on recorded dev night 20261023, judged where the pictures truly were; and, for the adapter,
the fake sticks and TV of tests/test_park_hands.py under the real `Controller.park_*` steps.

Replayed with fake sticks is not measured on the TV.

Why: the show is switched on by a flag; before that, every guard and every lesson the live runs paid for has to be
pinned by a test that fails when it is broken.
"""
from __future__ import annotations

import bisect
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone import config  # noqa: E402
from redzone.grading import dead_at  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin.park_core import Arr  # noqa: E402
from redzone.noggin.park_facts import GRID, FactsParams, GameFacts, pad_arr, refusal, stamps_of  # noqa: E402
from redzone.sources import espn  # noqa: E402
from redzone.noggin.park_show import NoModel, NoWatcher, ShowParams, ShowTiming, clip, engine_setting  # noqa: E402
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)

NIGHT = "20261023"
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")
P0 = ShowParams.from_config(config.default)
WARRANTS = {"that game is over", "left a break for live play", "left dead time for live play", "the better play is there"}


@pytest.fixture(scope="module")
def loaded():
    return pr.load_replay(NIGHT, 2)


def run(loaded, minutes=45, **kw):
    lines, rows = [], []
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=minutes, say=lines.append, emit=rows.append, **kw)
    return show, lines, rows


def rule1(show) -> tuple:
    return show.judge.mid_entry, show.judge.mid_leave, show.judge.hl_leave


# ---------------------------------------------------------------------------------------------- B. the hard guard
def game(dead, hl=()):
    return {"gid": "g", "t0": 10, "t1": 390, "dead": list(dead), "brk": [False] * len(dead), "shots": [], "big": [], "hl": list(hl),
            "lev": [], "v2": []}


def arrs(*games):
    return [Arr(g, 0, 400, False) for g in games]


def test_the_guard_refuses_each_unsafe_jump_in_words_and_passes_a_safe_one():
    A = arrs(game([(100, 160)]), game([(100, 160)]))
    ask = dict(pad=3, flip=3, know=10 ** 6, late_cap=145.0, g1=0, over=False, g2=1, sure=[10 ** 9] * 2, open_from=[None, None])
    assert refusal(A, t=140, i=130, j=130, **ask) == ""
    assert refusal(A, t=140, i=98, j=130, **ask) == "the leave is outside a padded legal window"
    assert refusal(A, t=140, i=102, j=130, **ask) == "the leave is outside a padded legal window"          # inside the dead ball, not by the padding
    assert refusal(A, t=140, i=130, j=170, **ask) == "the landing is outside a padded legal window"
    assert refusal(A, t=140, i=130, j=158, **ask) == "the landing is outside a padded legal window"
    assert refusal(A, t=300, i=130, j=130, **ask) == "the landing is more than 145 s behind live"
    hot = arrs(game([(100, 160)], hl=[(126, 133)]), game([(100, 160)]))
    assert refusal(hot, t=140, i=130, j=130, **ask) == "the leave is inside a padded high-leverage span"
    assert refusal(hot, t=140, i=137, j=130, **ask) == ""                                                   # 4 s past the span, padding 3
    assert refusal(A, t=140, i=390, j=130, **{**ask, "over": True}) == ""                                   # that game has ended


def test_the_guard_trusts_an_open_stoppage_only_as_far_as_it_is_sure_and_widens_for_an_unread_stream():
    A = arrs(game([(100, 160)]), game([(100, 160)]))
    ask = dict(pad=3, flip=3, know=10 ** 6, late_cap=145.0, g1=0, over=False, g2=1, t=140, i=130)
    assert refusal(A, j=130, sure=[10 ** 9, 132], open_from=[None, 100], **ask) == "the landing is outside a padded legal window"
    assert refusal(A, j=130, sure=[10 ** 9, 133], open_from=[None, 100], **ask) == ""                       # its whole padding ends by `sure`
    assert refusal(A, j=105, sure=[10 ** 9] * 2, open_from=[None, None], **ask) == ""
    assert refusal(A, j=105, sure=[10 ** 9] * 2, open_from=[None, None], x2=3, **ask) == "the landing is outside a padded legal window"
    assert refusal(A, j=130, sure=[10 ** 9] * 2, open_from=[None, None], x1=40, **ask) == "the leave is outside a padded legal window"
    assert refusal(A, j=130, sure=[10 ** 9] * 2, open_from=[None, None], **{**ask, "know": -20}) != ""     # the feed has not reported that far


@needs_archive
def test_a_failed_flip_means_no_jump_the_picture_stays_and_it_says_why(loaded):
    show, lines, rows = run(loaded, fail={"flip": 1})
    assert show.refused.get("the flip did not confirm") == 1
    assert any(x.startswith("NO JUMP: the flip did not confirm") and x.endswith("The picture that is up stays up.") for x in lines)
    flips = [x for x in show.rig.sent if x[:2] == ("tv", "set_input")]
    assert len(flips) == show.jumps_done + 1 and show.jumps_done >= 3          # one flip did not count; the show went on
    assert rule1(show) == (0, 0, 0) and not show.stop
    assert not any(show.rig.paused[k] for k in (0, 1))                         # finished: never a stick left paused


@needs_archive
@pytest.mark.parametrize("step, why", [("sound", "SOUND INCIDENT"), ("readback", "the TV could not be read back")])
def test_a_sound_stop_or_a_missing_read_back_is_final(loaded, step, why):
    show, lines, rows = run(loaded, fail={step: 1})
    assert show.stop.startswith(why)
    stopped_at = next(r["tick"] for r in rows if r.get("ev") == "refused" and r["why"].startswith(why))
    assert show.last_t <= stopped_at + 1 and show.jumps_done == 0              # nothing was done after it
    assert len([x for x in show.rig.sent if x[:2] == ("tv", "set_input")]) == 1
    assert any(x.startswith("STOPPED: ") for x in lines)


@needs_archive
def test_failed_tunes_pauses_and_reads_never_break_rule_1_and_an_unread_stream_is_cut_wider(loaded):
    show, lines, rows = run(loaded, minutes=60, fail={"tune": 2, "pause": 2, "read": 4, "resume": 1, "key": 1})
    assert rule1(show) == (0, 0, 0)
    assert {"a tune did not work", "a pause did not hold"} <= set(show.refused)
    wide = [r for r in rows if r.get("ev") == "padding" and not r["read"]]
    assert wide and all(r["pad_s"] == 7 and r["was_s"] == 3 for r in wide)     # exactly the padding the guard enforces for it
    assert show.jumps_done >= 3
    for rec in show.records:                                                   # each jump's record says what it was cut at
        assert rec["pad_s"]["to"] == (3 if rec["read"]["to"] else 7)


# ---------------------------------------------------------------------------------------------- B. Rule #1 as the frozen grader scores it
def one(g, pad_grid=(0, 0)):
    """One game's arrays as the SHOW builds them: the app's setting (no shot window), then its own cuts."""
    eng = engine_setting(3, 48, late_cap=109.0)
    assert eng.shots is False                                                  # the any-shot what-if is never on in the app
    return pad_arr(Arr(g, 0, 400, eng.shots), 0, None, pad_grid)


def ask(xs, xh, i, j, pad=3):
    return refusal([xs, xh], pad=pad, t=140, flip=3, know=10 ** 6, late_cap=10 ** 6, g1=0, i=i, over=False, g2=1, j=j,
                   sure=[10 ** 9] * 2, open_from=[None, None])


def test_a_missed_shot_two_seconds_before_a_whistle_makes_nothing_between_them_legal():
    g = {**game([(102, 160)]), "shots": [100.0]}                               # a miss at 100, the whistle at 102
    x, ok = one(g), one(game([(60, 200)]))
    assert not any(x.legal[98:102]) and x.since[102] == 1
    assert ask(ok, x, 130, 104) == "the landing is outside a padded legal window"   # 2 s after the whistle: inside the padding
    assert ask(ok, x, 130, 105) == "" and ask(x, ok, 105, 130) == "" and ask(x, ok, 104, 130) != ""
    show = one(g, GRID)                                                        # as the show cuts it (1 s more at the start for the grid)
    assert ask(ok, show, 130, 105) != "" and ask(ok, show, 130, 106) == ""
    old = Arr(g, 0, 400, True)                                                 # the any-shot what-if: the miss's 4 s JOIN the stoppage
    assert old.since[104] == 5 and ask(ok, old, 130, 104) == ""                # ...so a join 2 s after the whistle passed 3 s of padding


def test_a_join_after_a_missed_shot_is_refused_and_no_shot_of_any_kind_opens_a_window():
    """Legal = the frozen grader's dead time, for a leave and for a join. A missed shot opens nothing; a made basket
    opens nothing beyond the grader's own dead seconds after a scoring play; a made free throw opens nothing. (The
    made-field-goal JOIN window is a later, separately measured change: it is not in the show.)"""
    from test_park_facts import row
    rows = [row(0, "Jump Shot", "8:10", shooting=True), row(2, "Defensive Rebound", "8:08"),            # a miss
            row(30, "Jump Shot", "7:45", shooting=True, scoring=True), row(60, "Jump Shot", "7:20", shooting=True),   # a made basket
            row(90, "Personal Foul", "7:00"), row(105, "Free Throw - 1 of 1", "7:00", shooting=True, scoring=True),
            row(130, "Jump Shot", "6:40", shooting=True)]
    gf = GameFacts("nba", "g")
    for p in rows:
        gf.add(p)
    gf.set_final()
    view, _ = gf.planner_view(gf.wall[-1] + 60, with_value=False)
    assert len(view["shots"]) == 4                                             # the facts still LIST the shots (the what-if reads them)
    t0 = gf.wall[0]
    x = one({**view, "t0": view["t0"] - t0 + 100, "t1": view["t1"] - t0 + 100, "dead": [(a - t0 + 100, b - t0 + 100) for a, b in view["dead"]],
             "shots": [w - t0 + 100 for w in view["shots"]], "hl": [], "lev": [], "v2": [], "big": []})
    ok = one(game([(60, 399)]))
    assert x.legal == x.dead                                                   # nothing but dead time is ever legal in the show
    assert not any(x.legal[100:104])                                           # the 4 s after the MISS at 100
    for j in (101, 102, 103):
        assert ask(ok, x, 130, j, pad=1) == "the landing is outside a padded legal window"    # a join after a missed shot
        assert ask(x, ok, j, 130, pad=1) == "the leave is outside a padded legal window"
    assert not any(x.legal[136:160])                                           # 6 s and more after the MADE basket at 130: live play
    assert ask(ok, x, 130, 137, pad=1) != "" and ask(x, ok, 137, 130, pad=1) != ""            # inside the grader's 8 s join window: not built
    made_ft = 205                                                              # the made free throw: its own dead ball, nothing after it
    assert x.legal[made_ft - 5] and not any(x.legal[made_ft + 6:made_ft + 20])


def test_the_feed_hands_the_show_no_shots_so_only_dead_time_can_be_legal(monkeypatch):
    from redzone.noggin.park_show import FactsFeed, LiveNight
    from test_park_facts import row
    eng = engine_setting(3, 48, late_cap=109.0)
    night = LiveNight(1, 0, 4000, (500, 3000), eng)
    feed = FactsFeed([("nba", "g")], night, [0.0], [48.0])
    rows = [row(0, "Jump Shot", "8:10", shooting=True), row(2, "Personal Foul", "8:08"), row(40, "Jump Shot", "7:50", shooting=True)]
    feed.fx[0].wall, feed.fx[0].plays = [], []
    assert feed.offer_rows(0, rows, 100)
    (g, view, _), = feed.take(100)
    assert view["shots"] == [] and len(feed.fx[0].shots) == 2                  # the facts know the shots; the show's arrays never get them
    night.put(0, view)
    forced = Arr(view, night.base, night.n, True)                              # even with the what-if switch forced on
    assert forced.legal == forced.dead and all(d for ok_, d in zip(night.A[0].legal, night.A[0].dead) if ok_)   # legal only inside dead time


@pytest.mark.parametrize("grid, safe", [(GRID, True), ((0, 0), False)])
def test_a_position_error_just_under_the_padding_never_puts_a_cut_outside_the_dead_ball(grid, safe):
    """The padding is 4 x 0.678 s = 2.71 s. On the 1 s grid a stretch flagged dead on whole seconds is only KNOWN dead
    between its first and last flag, a game's flags sit at its own sub-second phase, and a believed position is cut
    down to its whole second. With the show's allowance every accepted cut holds for an error of 2.70 s either way;
    without it some do not (the tables simulator's "4 sd was really 1 to 2 s")."""
    tm = ShowTiming.case("tonight", 2)
    pad, err = tm.pad(0), 2.70
    assert pad == 3 and tm.errs(0).padding(tm.p.pad_k) == pytest.approx(2.71, abs=0.01)
    assert tm.pad(0, False) == 7 and ShowTiming.case("nok", 2).pad(0) == 49    # never rounded DOWN (6.48 s was 6, 48.4 s was 48)
    bad = 0
    for phase in (0.0, 0.3, 0.6, 0.95):                                        # the game's flags are read at t0 + k, t0 = 10 + phase
        for a_true, b_true in ((100.2, 131.4), (100.9, 130.05), (100.0, 130.0), (100.55, 130.5)):
            t0 = 10 + phase
            flagged = [t0 + k for k in range(300) if a_true <= t0 + k < b_true]
            g = {"gid": "g", "t0": t0, "t1": 390.0, "dead": [(flagged[0], flagged[-1] + 1.0)], "brk": [False], "shots": [], "big": [],
                 "hl": [], "lev": [], "v2": []}
            x, ok = one(g, grid), one(game([(60, 399)]))
            for step in range(0, 4000):
                pos = 90 + step * 0.01                                         # the believed position, to the hundredth
                p = int(pos)
                if ask(ok, x, 130, p, pad) == "" or ask(x, ok, p, 130, pad) == "":
                    bad += not (a_true <= pos - err and pos + err < b_true)
    assert (bad == 0) is safe


@needs_archive
def test_a_stream_held_past_the_park_age_cap_is_never_jumped_onto(loaded):
    show, _, rows = run(loaded, minutes=90, params=replace(P0, max_park=40))
    assert show.aged >= 1 and show.jumps_done >= 3                             # some holds aged out and were tuned afresh
    assert all(r["held_s"] <= 40 for r in show.records)
    assert P0.max_park == 180 and config.default("park.max_park_s") == 180     # the one key, and its default


# ---------------------------------------------------------------------------------------------- B. late-edit rows (live run 4, flip 4)
FLIPS = json.loads((ROOT / "tests" / "fixtures" / "park_live_flips_2026-11-10.json").read_text())


def _stamped(late):
    def stamped(ts):
        rows = [{"wallclock": datetime.fromtimestamp(1_794_000_000 + t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} for t in ts]
        wall, fixed = stamps_of(rows, late)
        return [w - 1_794_000_000 for w in wall], fixed
    return stamped


def test_a_row_stamped_far_later_than_the_rows_after_it_takes_the_stamp_of_the_row_before_it():
    fix, old = _stamped(60.0), _stamped(float("inf"))
    assert fix([0, 5, 1742, 10, 15, 20]) == ([0, 5, 5, 10, 15, 20], [2])      # the edit drags nothing
    assert old([0, 5, 1742, 10, 15, 20]) == ([0, 5, 1742, 1742, 1742, 1742], [])   # the never-backwards rule alone: half an hour of "dead ball"
    assert fix([0, 5, 4, 10]) == ([0, 5, 5, 10], [])                          # a feed that slips a second is only kept from going back
    assert fix([0, 5, 900, 905, 910]) == ([0, 5, 900, 905, 910], [])          # a real gap (halftime) is not an edit: nothing after it is earlier
    assert fix([0, 5, 1742, 1742, 10, 15]) == ([0, 5, 5, 5, 10, 15], [2, 3])  # two edits in a row
    rows = {name: g["rows"] for name, g in FLIPS["games"].items()}
    assert {name: len(stamps_of(r, 60.0)[1]) for name, r in rows.items()} == {"BOS@PHI": 2, "MSU@DUKE": 10, "BUCK@UVA": 4}


def test_the_seven_live_flips_of_nov_10_regrade_as_recorded_and_only_flip_4s_leave_changes():
    """The frozen grader's own dead_at on the public final play-by-play of that night, at the feed times the local
    runner cut at. With late-edit rows put back, flip 4 of run 4 LEFT a game in live play; on the loader's
    never-backwards stamps it read as a dead ball. The other thirteen ends do not move."""
    def verdict(name, st, late):
        g = FLIPS["games"][name]
        d, brk, _ = dead_at(League(g["league"]), stamps_of(g["rows"], late)[0], g["rows"], st)
        return "DEAD-BREAK" if d and brk else "DEAD" if d else "LIVE"
    assert len(FLIPS["flips"]) == 7
    changed = []
    for fl in FLIPS["flips"]:
        for end, name, at in (("leave", fl["left"], fl["leave_at"]), ("land", fl["joined"], fl["land_at"])):
            assert verdict(name, at, 60.0) == fl[end] and verdict(name, at, float("inf")) == fl[end + "_loader"]
            if fl[end] != fl[end + "_loader"]:
                changed.append((fl["run"], fl["n"], end, fl[end + "_loader"], fl[end]))
            elif fl[end] == "LIVE":
                raise AssertionError(fl)
    assert changed == [(4, 4, "leave", "DEAD", "LIVE")]


@pytest.mark.parametrize("late, caps, refused", [(60.0, True, True), (60.0, False, True), (float("inf"), True, True), (float("inf"), False, False)],
                         ids=["as shipped", "re-stamping only", "sanity gate only", "the old rule"])
def test_flip_4s_leave_is_refused_by_the_show_on_the_feed_it_had(late, caps, refused):
    """The feed as it stood at the flip (rows stamped up to 8 s before it, the late edits among them), through the
    show's own facts, arrays and guard. As shipped the leave is refused. So it is with the re-stamping alone, and with
    the sanity gate alone (the 1,853 s "dead ball" is cut to the 240 s a whistle can plausibly last). With neither,
    the leave passes: the defect of that night."""
    fl = next(x for x in FLIPS["flips"] if (x["run"], x["n"]) == (4, 4))
    g, wall_now, st = FLIPS["games"][fl["left"]], fl["wall"], fl["leave_at"]
    P = FactsParams.from_config(config.default)
    assert P.late_row_s == 60.0 and dict(P.stop_max)["whistle"] == 240.0
    gf = GameFacts(g["league"], "g", replace(P, late_row_s=late, stop_max=P.stop_max if caps else ()))
    for p in g["rows"]:
        if espn._ts(p["wallclock"]) <= wall_now - 8:
            gf.add(p)
    assert gf.restamped == (10 if late == 60.0 else 0)
    view, _ = gf.planner_view(wall_now + 600, edge=wall_now - 8, with_value=False)
    base = int(st) - 300
    eng = engine_setting(3, 10 ** 6, late_cap=10 ** 6)
    x = pad_arr(Arr(clip(view | {"shots": []}, base + 1), base, 900, eng.shots), 0, None, GRID)   # (clipped to the grid, as the feed does)
    q = int(st - base)
    assert bool(x.dead[q]) is (not refused)                                    # what the show's own arrays say of that second
    ok = pad_arr(Arr({**game([(base + 60, base + 899)]), "t0": base + 10, "t1": base + 899}, base, 900, False), 0, None, GRID)
    why = refusal([x, ok], pad=3, t=int(wall_now) - base, flip=3, know=10 ** 6, late_cap=10 ** 6, g1=0, i=int(st - base), over=False,
                  g2=1, j=400, sure=[10 ** 9] * 2, open_from=[None, None])
    assert (why == "the leave is outside a padded legal window") is refused and (why == "") is (not refused)
    row = bisect.bisect_right(gf.wall, st) - 1                                 # what the leave rested on
    assert gf.plays[row]["type"]["text"] == ("Defensive Rebound" if late == 60.0 else "Substitution")
    assert st - gf.wall[row] == pytest.approx(8.2 if late == 60.0 else 1742.2, abs=0.2)


def test_late_edits_that_arrive_in_the_middle_of_the_list_are_put_back_as_the_feed_is_read():
    fl = next(x for x in FLIPS["flips"] if (x["run"], x["n"]) == (4, 4))
    g = FLIPS["games"][fl["left"]]
    P = replace(FactsParams.from_config(config.default))
    gf = GameFacts(g["league"], "g", P)
    before = [p for p in g["rows"] if espn._ts(p["wallclock"]) <= fl["wall"] - 200]
    after = [p for p in g["rows"] if espn._ts(p["wallclock"]) <= fl["wall"] - 8]
    assert gf.update({"plays": before}, final=False) == len(before)
    first = gf.restamped
    gf.update({"plays": after}, final=False)                                   # two substitution rows have appeared far up the list
    assert len(after) > len(before) and after[:len(before)] != before and gf.restamped == 10 > first
    assert gf.wall == stamps_of(after, 60.0)[0] and not gf.dragged


# ---------------------------------------------------------------------------------------------- B. every look grades its own hold
@needs_archive
def test_every_look_has_its_place_read_again_after_the_resume_and_a_stream_that_holds_is_held(loaded):
    show, lines, rows = run(loaded, minutes=75)
    assert show.jumps_done >= 5 and sum(show.holds.values()) == show.jumps_done and not show.drifted
    for rec in show.records:
        h = rec["hold"]
        assert h["verdict"] in ("held", "unread") and [r["at_s"] for r in h["reads"]] == [10, 30, 60, 120][:len(h["reads"])]
        assert (h["verdict"] == "held") is bool(h["reads"])                    # a look shorter than 10 s was never read: unread
        assert all(abs(r["change_s"]) <= 1.0 and r["lag_s"] == pytest.approx(h["lag_at_resume_s"], abs=1.0) for r in h["reads"])
    assert show.holds["held"] >= 3
    pos = [r for r in rows if r.get("ev") == "position"]                       # each CHANGED position report, with its update time
    assert pos and all(isinstance(r["pos_s"], float) and isinstance(r["updated"], float) and r["n"] >= 1 for r in pos)
    assert len(pos) == len({(r["n"], r["pos_s"], r["updated"]) for r in pos}) <= sum(len(r["hold"]["reads"]) for r in show.records)


@needs_archive
def test_a_stream_that_drifts_after_its_resume_is_unread_at_once_and_not_cut_on_until_it_is_read_again(loaded):
    show, lines, rows = run(loaded, minutes=75, rig_kw={"drift": 6.0, "drift_n": 1})   # one resume creeps 6 s a minute
    first = next(r for r in rows if r.get("ev") == "hold" and r["verdict"] == "drifted")
    assert [r["at_s"] for r in first["reads"]] == [10, 30]                     # 10 s after the resume it had crept under 2 s...
    assert 0.5 < first["reads"][0]["change_s"] <= 2.0 < first["reads"][1]["change_s"]   # ...at 30 s over 2 s: drifted
    assert any(x.startswith("DRIFTED: the stream on screen moved") for x in lines)
    again = next(r for r in rows if r.get("ev") == "reread" and r["tick"] > first["tick"])
    assert again["ok"] == 1 and again["tick"] - first["tick"] >= P0.cool
    between = [r for r in rows if first["tick"] <= r.get("tick", -1) <= again["tick"]]
    assert not any(r.get("ev") == "jump" for r in between)                     # no cut on it until it is read again
    assert any(r.get("ev") == "padding" and not r["read"] and r["pad_s"] == 7 for r in between)   # unread AT ONCE: the wide padding
    assert show.records[first["n"] - 1]["hold"]["verdict"] == "drifted" and not show.drifted
    assert any(r.get("ev") == "jump" and r["tick"] > again["tick"] for r in rows)   # read again: the show goes on
    assert any(x.startswith("READ AGAIN") for x in lines)


# ---------------------------------------------------------------------------------------------- C. warrants; pace is soft
@needs_archive
def test_every_jump_carries_a_warrant_worked_out_before_it_and_never_lands_on_no_play(loaded):
    show, lines, rows = run(loaded, minutes=90)
    assert show.jumps_done >= 8 and len(show.records) == show.jumps_done
    for rec in show.records:
        w = rec["warrant"]
        assert w["ok"] is True and w["why"] in WARRANTS
        assert w["why"] == "that game is over" or w["live_sooner_s"] >= 5 or w["there"] > w["here"]
    assert show.judge.noplay == 0 and show.judge.brk_land == 0                 # no look without play; no landing in a long break
    assert all(r["warrant"]["ok"] for r in rows if r.get("ev") == "jump")      # the same record goes to the event log
    assert rule1(show) == (0, 0, 0)


@needs_archive
def test_pace_is_a_soft_pull_never_a_rule(loaded):
    assert not any("min_jumps" in f or "quota" in f for f in ShowParams.__dataclass_fields__)
    calm, _, _ = run(loaded, params=replace(P0, bonus=0.0))
    keen, _, _ = run(loaded, params=replace(P0, bonus=30.0))
    for show in (calm, keen):                                                  # however hard the pull, the same guards and warrants
        assert rule1(show) == (0, 0, 0)
        assert all(r["warrant"]["ok"] for r in show.records)
    assert keen.jumps_done >= calm.jumps_done


# ---------------------------------------------------------------------------------------------- D. the planning lessons
@needs_archive
def test_a_stream_already_on_its_game_is_not_tuned_again_and_tunes_do_not_thrash(loaded, monkeypatch):
    seen = []
    real = pr.FakeRig.tune

    async def tune(self, k, game, steps=None):
        seen.append((self.game[k] == game and not self.paused[k], self.lag[k], self.tune_lag))
        return await real(self, k, game, steps)
    monkeypatch.setattr(pr.FakeRig, "tune", tune)
    show, lines, rows = run(loaded, minutes=90)
    assert seen and show.tunes_sent == len(seen)
    assert not any(same and lag < tune_lag + P0.refresh for same, lag, tune_lag in seen)   # only one that has fallen well behind
    assert show.tunes_sent <= show.jumps_done + 3                              # live run 5 sent three tunes for every two jumps


@needs_archive
def test_nothing_is_parked_on_a_game_in_a_long_break(loaded):
    show, lines, rows = run(loaded, minutes=100)
    parks = [r for r in rows if r.get("ev") == "parked"]
    assert len(parks) >= 5
    for r in parks:
        x = show.judge.A[r["game"]]
        p = min(x.n - 1, max(0, r["at"]))
        assert not (x.dead[p] and x.left[p] > P0.long_break), "parked on a frame with a long break still to come"
    g = show.S.hg if show.S.hg >= 0 else 1 - show.S.sg                         # and the rule itself: a game the feed has shown
    show.open[g], show.feed.at[g] = (show.last_t - 200, None, None), show.last_t   # stopped for 90 s is no place to wait
    assert show.prospect(g) == 0.0
    show.open[g] = None
    assert show.prospect(g) == 1.0


@needs_archive
def test_a_place_that_no_tune_can_leave_is_not_believed(loaded):
    show, lines, rows = run(loaded, rig_kw={"bad_place": 200.0})
    assert any(x.startswith("NOTE: the hidden stick's place after its tune was not believable") for x in lines)
    assert any(r.get("ev") == "padding" and not r["read"] and r["pad_s"] == 7 for r in rows)   # and its tune counts as not read
    assert rule1(show) == (0, 0, 0)


@needs_archive
def test_the_plan_plans_with_the_step_times_the_real_sticks_took(loaded):
    show, _, rows = run(loaded, minutes=30)
    assert show.flip == 3 and show.s.tune_s == pytest.approx(13.5) and show.pl.confirm == 2
    assert show.pl.join_playing is False and show.pl.back == 0                 # the app's park steps: parked landings only
    took = [r["s"] for r in rows if r.get("ev") == "step" and r["kind"] == "tune" and r["ok"]]
    assert took and 12.5 <= min(took) and max(took) <= 21.5                    # a tune with its read: 13-19 s
    assert all(show.tune_cost(g, 3) >= 13 for g in range(show.N))
    assert max(r["behind_s"] for r in rows if r.get("ev") == "jump") <= P0.late_cap


@needs_archive
def test_a_first_stream_that_was_never_placed_is_never_left(loaded):
    """Why Controller.park_start exists. A stream at its live edge, whose place was never read, runs about 13 s behind
    the feed's edge: the rows that date what it shows may not be in yet (on this night a row arrives there carrying a
    stamp 19 minutes off, and before the guard below the show left that game in live play on it). So nothing is cut on
    within the grader's restart lead of the feed's edge. Placed by a read, the same half hour has its jumps."""
    lost, _, rows = run(loaded, minutes=30, first="live")
    placed, _, _ = run(loaded, minutes=30)
    assert lost.jumps_done == 0 and placed.jumps_done >= 2 and rule1(lost) == rule1(placed) == (0, 0, 0)
    # either the edge guard refuses, or (since the late-edit fix) the facts already re-stamped the row from the future
    assert lost.refused.get("the cut is too close to the feed's edge: the rows that date it may not be in yet", 0) >= 1 \
        or any(fx.restamped for fx in lost.feed.fx)
    assert "the cut is too close to the feed's edge: the rows that date it may not be in yet" not in placed.refused


# ---------------------------------------------------------------------------------------------- F. the seams
@needs_archive
def test_the_seams_are_no_ops_until_something_is_plugged_in(loaded):
    plain, _, _ = run(loaded, minutes=30)
    seamed, _, _ = run(loaded, minutes=30, model=NoModel(), eyes=NoWatcher())
    assert [r["tick"] for r in seamed.records] == [r["tick"] for r in plain.records]
    assert sum(seamed.eyes_says.values()) == 2 * seamed.jumps_done and seamed.eyes_says["leaves_bad"] == 0


@needs_archive
def test_a_stoppage_model_can_only_add_trust_and_a_watcher_rules_on_each_jump(loaded):
    class Model(NoModel):
        asked = 0

        def surely_left(self, league, features, elapsed_s, alpha=0.01):
            Model.asked += 1
            assert league == "nba" and elapsed_s >= 0 and features["rows"][0][0] == 0 and isinstance(features["rows"][0][1], str)
            return 0.0                                                         # "nothing more is sure": must change nothing

        def landing_soon(self, league, features, horizon_s):
            assert set(features) == {"period", "clock_s", "margin"} and horizon_s == 20
            return 1.0                                                         # "a landing is coming": the full weight, as without a model

    class Watcher(NoWatcher):
        def verdict(self, g, p):
            return "ok"
    plain, _, _ = run(loaded, minutes=30)
    show, _, rows = run(loaded, minutes=30, model=Model(), eyes=Watcher())
    assert Model.asked > 0 and [r["tick"] for r in show.records] == [r["tick"] for r in plain.records]
    assert show.eyes_says["leaves_ok"] == show.eyes_says["joins_ok"] == show.jumps_done > 0
    assert all(r["watcher"] == {"leave": "ok", "join": "ok"} for r in show.records)   # the verdict is in each jump's record


# ---------------------------------------------------------------------------------------------- E. the Show tab's state
@needs_archive
def test_the_show_writes_the_contract_the_show_tab_reads(loaded, tmp_path):
    import json

    from redzone.showboard import Board
    from redzone.web import show as tab
    board = Board(tmp_path)
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=30, board=board)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["mode"] == "stopped" and state["pace"]["jumps"] == show.jumps_done >= 1
    assert state["rule1"] == {"mid_play_joins": 0, "mid_play_leaves": 0, "high_leverage_leaves": 0, "judged": "replay"}
    assert set(tab.STATE_SPEC) <= set(state) | {"eyes"}
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    jumps = [e for e in events if e["kind"] == "jump"]
    assert len(jumps) == show.jumps_done and all(e["warrant"]["ok"] for e in jumps)
    assert board.latest == state and "/" not in json.dumps(state["on_screen"])
    served = tab.build_state(tmp_path, now=state["updated"] + 1.0)             # and the tab's own reader takes it whole
    assert served["on_screen"]["game"] == state["on_screen"]["game"] and served["pace"]["jumps"] == show.jumps_done
    assert served["rule1"]["judged"] == "replay" and served["stale_s"] == pytest.approx(1.0, abs=0.2)


def test_with_no_folder_set_the_show_tab_reads_where_the_apps_own_show_writes(cfg):
    from redzone.web import show as tab
    assert tab._folder(cfg) is None                                            # flag off: no folder, as before
    cfg.update({"park": {"enabled": True}})
    assert tab._folder(cfg) == Path("data/show") and tab._folder(cfg, "show.eyes_dir") is None
    cfg.update({"show": {"state_dir": "/tmp/elsewhere"}})
    assert tab._folder(cfg) == Path("/tmp/elsewhere")                          # a folder that is set always wins


# ---------------------------------------------------------------------------------------------- A. the controller's park steps as the show's sticks
async def test_the_show_takes_each_streams_place_from_the_controllers_own_steps(world):  # noqa: F811
    from redzone.park_run import ControllerRig, WallClock
    w, c = world, world.c
    c.on_target = "youtubetv:tnt"
    clock = WallClock(int(w.clk.now()) - 400, w.clk.now, w.clk.sleep)
    rig = ControllerRig(c, clock, ["youtubetv:tnt", "youtubetv:espn"], P0)
    assert rig.game == [None, None] and rig.join_playing is False and rig.steps == 2
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await c.park_start()                                                 # the stream on screen: read, never assumed live
    rig.sync()
    assert out.ok and out.step == "start" and rig.game[0] == 0 and rig.read[0]
    assert rig.lag[0] == pytest.approx(w.a.behind() - 20.0 - 35, abs=1.5) and 0 < rig.lag[0] < 12   # the read left it ~38 s behind live
    ok, took = await rig.tune(1, 1)
    assert ok and 9 <= took <= 22 and rig.game[1] == 1 and rig.read[1] and not rig.paused[1]
    assert rig.lag[1] == pytest.approx(w.b.behind() - 55, abs=1.5)
    at, lag = clock.now() + 2.0, rig.lag[1]
    ok, frozen = await rig.pause(1, at)
    assert ok and rig.paused[1] and w.b.ps == "paused"
    assert frozen == pytest.approx(at + 0.60 - lag, abs=1.2)                   # the grid second of the frozen frame
    ok, lag = await rig.resume(1)                                              # the plan moved on: it plays on, its place stands
    assert ok and not rig.paused[1] and w.b.ps == "playing" and rig.read[1] and lag > rig.tune_lag
    assert (await rig.jump(1, False, 0))[0] == "kept" and w.tv.sets == []      # never onto a stream that plays: nothing sent
    ok, _ = await rig.pause(1, None)
    assert ok
    assert await rig.jump(1, True, 0) == ("ok", "")
    assert c.active == 1 and w.tv.input == "HDMI2" and rig.game == [0, 1] and rig.paused == [True, False]
    ok, took = await rig.tune(0, 0)                                            # the stream left, far behind by now: a real reload
    assert ok and w.tuner.calls[-1] == "CCCCCCCCCCC" and not rig.paused[0] and rig.lag[0] < 12
    await rig.finish()
    assert w.a.ps == "playing" and w.b.ps == "playing"


async def test_through_the_controller_a_failed_flip_is_kept_and_a_sound_stop_is_final(world):  # noqa: F811
    from redzone.park_run import ControllerRig, WallClock
    w, c = world, world.c
    c.on_target = "youtubetv:tnt"
    rig = ControllerRig(c, WallClock(int(w.clk.now()) - 400, w.clk.now, w.clk.sleep), ["youtubetv:tnt", "youtubetv:espn"], P0)
    assert await w.tv.sound_guard.check("session start") == "ok"
    assert (await c.park_start()).ok and (await rig.tune(1, 1))[0] and (await rig.pause(1, None))[0]
    w.tv.flip_fails = True
    status, why = await rig.jump(1, True, 0)
    assert status == "kept" and why == "the flip did not confirm" and w.tv.input == "HDMI1" and w.a.ps == "playing"
    assert c.active == 0 and c.park_why() == ""
    w.tv.flip_fails, w.tv.unmute_on_flip = False, True
    if not rig.paused[1]:
        assert (await rig.pause(1, None))[0]
    status, why = await rig.jump(1, True, 0)
    assert status == "stop" and "sound" in why and "sound" in c.park_why()
    sent = (len(w.a.keys), len(w.b.keys), len(w.tv.sets))
    assert (await rig.tune(1, 0))[0] is False and (await rig.jump(1, True, 0))[0] != "ok"
    assert (len(w.a.keys), len(w.b.keys), len(w.tv.sets)) == sent              # nothing more is sent


# ---------------------------------------------------------------------------------------------- A. the engine's loop
def _engine(w, tmp_path):
    """A stand-in engine over the park steps' fake world: two live NBA games of a recorded fixture night on two channels."""
    import json
    from dataclasses import replace as dc_replace
    from types import SimpleNamespace

    from redzone.models import League, Phase
    from redzone.sources import espn
    fix = ROOT / "tests" / "fixtures" / "your_team_crunch"
    ids = [e["id"] for e in json.loads((fix / "espn_nba_scoreboard_20251212.json").read_text())["events"]][:2]
    data = {i: json.loads((fix / f"espn_nba_summary_{i}.json").read_text()) for i in ids}
    games = {i: dc_replace(espn.parse_summary(League.NBA, data[i]), phase=Phase.LIVE) for i in ids}
    targets = dict(zip(ids, ("youtubetv:tnt", "youtubetv:espn")))
    w.c.cfg.update({"show": {"state_dir": str(tmp_path / "show")}})
    w.c.on_target = "youtubetv:tnt"
    dec = SimpleNamespace(owner_m=SimpleNamespace(state="normal"), target_of=targets.get, park_show=None)
    return SimpleNamespace(cfg=w.c.cfg, control=w.c, decider=dec, merger=SimpleNamespace(games=games), summaries=data), ids


async def test_the_engines_loop_starts_a_show_stands_down_for_the_user_and_a_stop_is_final(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path)
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert not run_.owns()
    assert await w.tv.sound_guard.check("session start") == "ok"
    held, real_start = [], w.c.park_start

    async def start():
        held.append(run_.owns())                                               # while the stream on screen is being placed,
        return await real_start()                                              # no other decider may switch under the read
    w.c.park_start = start
    await run_.step()                                                          # places the stream on screen, then starts
    assert held == [True]
    assert run_.state == "running" and run_.owns() and run_.gids == ids, run_.why
    assert run_.rig.game[0] == 0 and run_.rig.read[0] and run_.show.on == 0
    for _ in range(3):
        await run_.step()
        await run_.clock.sleep(1.0)
    assert (tmp_path / "show" / "state.json").exists() and run_.view()["show"]["mode"] == "live"
    told = []
    eng.decider.current, eng.decider.adopt = ids[0], told.append
    run_.show.S.sg, run_.show.jumps_done = 1, 1                                # as after a jump onto the second game
    await run_.step()
    assert told == [ids[1]] and run_.told == 1                                 # the decider learns the screen's game, once
    run_.show.S.sg, run_.show.jumps_done = 0, 0
    run_.told = 0
    eng.decider.owner_m.state = "user"                                         # your tap, pause or lock: the show lets go
    await run_.step()
    assert run_.state == "waiting" and "user" in run_.why and not run_.owns() and run_.show is None
    kept = sorted((tmp_path / "show" / "plays").glob("*.json"))                # the play lists it polled, for regrading the run
    assert len(kept) == 2
    one_ = json.loads(kept[0].read_text())
    assert len(one_["rows"]) == len(one_["stamps"]) == len(one_["first_seen"]) > 100 and one_["league"] == "nba" and "game" in one_
    assert w.a.ps == "playing" and w.b.ps in ("playing", "none")               # never a stick left paused
    eng.decider.owner_m.state = "normal"
    run_.retry_at = 0.0
    await run_.step()
    assert run_.state == "running" and run_.shows == 2
    run_.show.stop = "SOUND INCIDENT: made for the test"
    run_.show.rig.sync()
    await run_.step()
    assert run_.final.startswith("SOUND INCIDENT") and not run_.owns()
    await run_.step()
    assert run_.state == "stopped" and run_.show is None                       # only turning the flag off and on clears it
    eng.cfg.update({"park": {"enabled": False}})
    assert not run_.on() and not run_.owns()


async def test_the_engines_loop_waits_with_fewer_than_two_games_or_when_the_park_steps_may_not_run(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path)
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    del eng.summaries[ids[1]]
    await run_.step()
    assert run_.state == "waiting" and run_.why == "fewer than two live games the show can take" and w.tuner.calls == []
    w.c.on_target = "youtubetv:fs1"
    await run_.step()
    assert run_.why == "the game on screen is not a live game the show can take"
    w.tv.settings["sound_guard.must_be_silent"] = False
    await run_.step()
    assert run_.state == "waiting" and "must_be_silent" in run_.why and w.a.keys == [] and w.b.keys == []


# ---------------------------------------------------------------------------------------------- A. looks, the focus guard, a pause BACK undid
async def _placed(world):  # noqa: F811
    from redzone.park_run import ControllerRig, WallClock
    w, c = world, world.c
    c.on_target = "youtubetv:tnt"
    rig = ControllerRig(c, WallClock(int(w.clk.now()) - 400, w.clk.now, w.clk.sleep), ["youtubetv:tnt", "youtubetv:espn"], P0)
    assert await w.tv.sound_guard.check("session start") == "ok"
    assert (await c.park_start()).ok
    rig.sync()
    return w, c, rig


async def test_a_look_reads_the_on_screen_streams_place_with_no_key_and_sees_it_slip(world):  # noqa: F811
    w, c, rig = await _placed(world)
    keys = list(w.a.keys)
    one_ = await rig.look(0)
    assert w.a.keys == keys and one_["state"] == "playing" and one_["lag"] == pytest.approx(rig.lag[0], abs=0.3)
    assert isinstance(one_["pos_s"], float) and isinstance(one_["updated"], float)
    await w.clk.sleep(30.0)
    assert (await rig.look(0))["lag"] == pytest.approx(one_["lag"], abs=0.3)   # it plays on: the place holds
    w.a.pos0 -= 5.0                                                            # the stream slips 5 s further behind (a pause that did not hold)
    assert (await rig.look(0))["lag"] - one_["lag"] == pytest.approx(5.0, abs=0.3)
    assert await rig.look(1) is None and w.a.keys == keys                      # only the stream on screen is looked at
    ok, took = await rig.reread(0)                                             # read again: the gesture, on screen
    assert ok and rig.read[0] and w.a.keys[len(keys):] == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]
    assert (await rig.look(0))["lag"] == pytest.approx(rig.lag[0], abs=0.3)


async def test_the_read_gesture_never_commits_with_a_row_button_in_focus(world):  # noqa: F811
    from redzone.tuners.yttv_seek import OverlayRead
    w, c = world, world.c
    reader = w.hands.label_reader

    async def row_focused(stick):
        lab = await reader(stick)
        w.hands._overlay[stick.host] = OverlayRead(overlay=True, row_focused=True, focus_text="CC")   # the frame says: a row button has focus
        return lab
    w.hands.label_reader = row_focused
    out = await c.park_tune("youtubetv:espn")
    assert out.ok and out.numbers["read"] is False and out.numbers["read_why"] == "focus guard: a row button has focus"
    assert w.b.keys == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "BACK", "MEDIA_PLAY_PAUSE"]   # no committing CENTER on that button
    assert w.b.ps == "playing" and w.b.behind() < 45 and w.hands.counts["guarded"] == 1        # nothing was sought: no 30 s step back
    assert w.hands.place_of(w.b).read is False and "not read" in out.why

    async def no_overlay(stick):
        lab = await reader(stick)
        w.hands._overlay[stick.host] = OverlayRead(overlay=False)
        return lab
    w.hands.label_reader, w.b.keys = no_overlay, []
    out = await c.park_tune("youtubetv:fs1")
    assert out.ok and out.numbers["read_why"] == "focus guard: the overlay is not up" and "DPAD_CENTER" not in w.b.keys[3:]


async def test_a_pause_that_back_undid_is_no_park_and_is_never_jumped_onto(world):  # noqa: F811
    """Live run 7: the pause key held, BACK (which clears the pause banner) started the stream again, and the app
    reported it playing. The park is refused, the show is told so, and no jump goes onto that stream."""
    w, c, rig = await _placed(world)
    assert (await rig.tune(1, 1))[0]
    w.b.hold_fails = True                                                      # BACK resumes it
    ok, frozen = await rig.pause(1, None)
    assert not ok and frozen is None and not rig.paused[1] and w.b.ps == "playing"
    assert rig.note[1]["why"].startswith("after BACK it reports playing")
    assert (await rig.jump(1, True, 0))[0] == "kept" and w.tv.sets == [] and w.tv.input == "HDMI1"
    assert rig.game[1] == 1 and rig.read[1]                                    # it still plays its game, its place stands
    w.b.hold_fails = False
    assert (await rig.pause(1, None))[0] and rig.paused[1]                     # a pause that holds is a park again


# ---------------------------------------------------------------------------------------------- B. silence trusted as dead (fix)
@needs_archive
def test_a_slow_second_tune_no_longer_exposes_a_leave_into_silence_after_a_ruled_review(loaded):
    """Regression (night 20261023, 2 games, 45 minutes, the second game's tunes 54.5 s as a Prime game's would be): the
    show left LAC @ LAL at 7:21 of Q1 into 149 s of feed silence after a ruled challenge, trusted as dead up to the review
    cap (420 s); the frozen grader called it a mid-play leave. park.possession_cap_s makes that silence unknown."""
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=45, rig_kw={"tune_s": {1: 54.5}})
    g = pr.graded(show)
    assert (g["grader_entries_mid_play"], g["grader_entries_mid_play_strict"], g["grader_leaves_mid_play"]) == (0, 0, 0)
    assert (show.judge.mid_entry, show.judge.mid_leave, show.judge.hl_leave) == (0, 0, 0)


@needs_archive
@pytest.mark.parametrize("night", ["20261023", "20261028", "20261021"])
def test_three_four_game_nights_hold_the_frozen_graders_zero(night):
    row = pr.result(pr.replay(night, 4))
    assert (row["grader_entries_mid_play"], row["grader_entries_mid_play_strict"], row["grader_leaves_mid_play"]) == (0, 0, 0)
    assert row["warranted"] == row["warrant_n"]
