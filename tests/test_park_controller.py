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

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone import config  # noqa: E402
from redzone.noggin.park_core import Arr  # noqa: E402
from redzone.noggin.park_facts import refusal  # noqa: E402
from redzone.noggin.park_show import NoModel, NoWatcher, ShowParams  # noqa: E402
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)

NIGHT = "20261023"
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="no recorded dev nights")
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
    assert wide and all(r["pad_s"] == 6 and r["was_s"] == 3 for r in wide)     # exactly the padding the guard enforces for it
    assert show.jumps_done >= 3
    for rec in show.records:                                                   # each jump's record says what it was cut at
        assert rec["pad_s"]["to"] == (3 if rec["read"]["to"] else 6)


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
    assert any(r.get("ev") == "padding" and not r["read"] and r["pad_s"] == 6 for r in rows)   # and its tune counts as not read
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
def test_a_first_stream_that_was_never_placed_is_almost_never_left(loaded):
    """Why Controller.park_start exists: the feed is hardly ever far enough ahead of a stream at its live edge, whose
    place was never read, for a padded leave. Placed by a read, the same half hour has its jumps."""
    lost, _, _ = run(loaded, minutes=30, first="live")
    placed, _, _ = run(loaded, minutes=30)
    assert lost.jumps_done <= 1 and placed.jumps_done >= 3 and rule1(lost) == rule1(placed) == (0, 0, 0)


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
