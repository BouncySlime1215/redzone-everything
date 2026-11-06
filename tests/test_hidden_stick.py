"""EXP-601 hidden-stick readiness in the replay (docs/prereg/exp-601.md): off = today's grader; arms A and B."""
import json
import math
import shutil
import sys
from pathlib import Path

import pytest

from redzone.config import Config
from redzone.grading import SwitchLog
from redzone.lab import backtest
from redzone.lab.backtest import HiddenStick, Replay
from redzone.models import League

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "your_team_crunch"
NIGHT = "20251212"


class _Clock:
    t = 0.0


def _stick(arm, B=90.0, overrun=1.0, seed=1):
    return HiddenStick(arm, B, overrun, Config(user_path=FIX / "eval_config.yaml"), _Clock(), seed, {})


def _switch(stick, to, frm="old", t=100.0, targets=None):
    sw = SwitchLog(t, frm, to, "live", "test")
    watching = [(0.0, frm), (t, to)]
    stick.on_switch(sw, watching, targets)
    return sw, watching


def test_tune_draw_is_the_registered_lognormal():
    s = _stick("wait", seed=3)
    xs = sorted(s.tune() for _ in range(4000))
    assert all(5.0 <= x <= 120.0 for x in xs)
    assert abs(xs[len(xs) // 2] - 21.61) < 1.0
    assert abs(xs[int(0.95 * len(xs))] - 44.74) < 3.0
    a, b = _stick("wait", seed=7), _stick("wait", seed=7)
    assert [a.tune() for _ in range(5)] == [b.tune() for _ in range(5)]   # seeded: seed * 7_000_003 + 601


def test_ready_target_airs_buffered_as_today():
    s = _stick("live_edge")
    s.load("g1", "youtubetv:g1", 0.0)
    assert s.ready_at == pytest.approx(s.tuned_at + 90.0 + 1.0)
    sw, watching = _switch(s, "g1", t=s.ready_at + 1)
    assert (sw.offset, sw.arrive_dt, sw.leave_dt) == (0.0, 0.0, 0.0)
    assert s.card()["buffered"] == 1 and s.card()["waits"] == 0


def test_arm_a_waits_for_a_fresh_preload_with_the_old_game_on_screen():
    s = _stick("wait", overrun=1.36)
    s.load("other", "youtubetv:other", 0.0)
    sw, watching = _switch(s, "g1", t=100.0)
    wait = sw.arrive_dt
    assert wait == sw.leave_dt and watching[-1][0] == pytest.approx(100.0 + wait)
    assert wait >= 5.0 + 90.0 * 1.36 + 1.0                # tune + B * hold_overrun + resume
    assert sw.offset == 0.0 and s.card()["buffered"] == 1  # it airs buffered, late


def test_arm_a_waits_out_the_rest_of_a_preload_already_running():
    s = _stick("wait")
    s.load("g1", "youtubetv:g1", 50.0)
    held = s.ready_at
    sw, _ = _switch(s, "g1", t=60.0)
    assert sw.arrive_dt == pytest.approx(held - 60.0)       # no new tune: the cut lands when it's held
    assert s.card()["retunes"] == 0


def test_arm_b_tunes_at_the_live_edge_and_airs_unbuffered():
    s = _stick("live_edge")
    s.load("other", "youtubetv:other", 0.0)
    sw, watching = _switch(s, "g1", t=100.0)
    assert 5.0 <= sw.arrive_dt <= 120.0 and sw.leave_dt == sw.arrive_dt   # one fresh tune, old game meanwhile
    assert watching[-1][0] == pytest.approx(100.0 + sw.arrive_dt)
    assert sw.offset == -90.0                                              # graded on the live edge
    assert s.card()["buffered"] == 0 and s.card()["unbuffered_t"] == [100.0]


def test_arm_b_tuned_but_not_held_airs_now_unbuffered():
    s = _stick("live_edge")
    s.load("g1", "youtubetv:g1", 0.0)
    sw, _ = _switch(s, "g1", t=s.tuned_at + 1.0)   # tuned, still inside the B s hold
    assert (sw.arrive_dt, sw.offset) == (0.0, -90.0)


def test_prime_holds_nothing_and_always_airs_at_the_live_edge():
    for arm in ("wait", "live_edge"):
        s = _stick(arm)
        s.load("p", "prime:p", 0.0)
        assert s.ready_at == pytest.approx(s.tuned_at + 1.0)   # B = 0 for Prime
        sw, _ = _switch(s, "p", t=s.ready_at + 1, targets={"p": "prime:p"})
        assert sw.offset == -90.0 and sw.arrive_dt == 0.0
        assert s.card()["prime_switches"] == 1 and s.card()["prime_t"] == [sw.t]


def test_the_stick_we_left_holds_the_game_we_left_ready_at_once():
    for arm in ("wait", "live_edge"):
        s = _stick(arm)
        s.load("other", "youtubetv:other", 0.0)
        sw, _ = _switch(s, "g1", frm="g0", t=100.0)
        assert s.gid == "g0" and s.ready_at == pytest.approx(100.0 + sw.arrive_dt)
        back, _ = _switch(s, "g0", frm="g1", t=s.ready_at + 5)
        assert (back.arrive_dt, back.offset) == (0.0, 0.0)


def test_the_stick_we_left_must_hold_first_if_that_game_aired_at_the_live_edge():
    s = _stick("live_edge")
    s.load("other", "youtubetv:other", 0.0)
    first, _ = _switch(s, "g1", frm="g0", t=100.0)          # g1 airs at the live edge
    assert first.offset == -90.0
    cut = s.ready_at + 50
    _switch(s, "g2", frm="g1", t=cut)                      # leave g1: its stick is tuned, not held
    assert s.gid == "g1" and s.ready_at > s.tuned_at
    assert s.ready_at - s.tuned_at == pytest.approx(90.0 + 1.0)
    early, _ = _switch(s, "g1", frm="g2", t=s.tuned_at + 10)   # back before the hold: live edge again
    assert early.offset == -90.0


def test_replay_switches_need_no_buffer():
    s = _stick("live_edge")
    sw = SwitchLog(100.0, "g0", "g1", "replay", "test")
    s.on_switch(sw, [(100.0, "replay:g1")], None)
    assert s.card()["switches"] == 0 and sw.offset == 0.0


def test_bad_arm_is_refused():
    with pytest.raises(ValueError):
        _stick("sometimes")


# ---- whole night: flag off is today's grader; both arms run the real planner --------------------------------------

def _night():
    evs = json.loads((FIX / f"espn_nba_scoreboard_{NIGHT}.json").read_text())["events"]
    reps = [r for r in (Replay.load(League.NBA, FIX / f"espn_nba_summary_{e['id']}.json") for e in evs) if r]
    targets = {r.gid: ("prime:" if i % 3 == 0 else "youtubetv:") + r.gid for i, r in enumerate(reps)}
    return reps, targets


def _run(**kw):
    reps, targets = _night()
    cfg = Config(user_path=FIX / "eval_config.yaml")
    return backtest.run(reps, cfg, {"WSH"}, targets=targets, seed=1,
                        **backtest.feed_lag_knobs(70.0, 90.0), **kw)


def test_flag_off_never_builds_the_stick_and_matches_today(monkeypatch):
    base = _run()
    monkeypatch.setattr(backtest, "HiddenStick", lambda *a, **k: (_ for _ in ()).throw(AssertionError("built")))
    off = _run(hidden_stick=None, hold_overrun=1.36, behind_live_s=90.0)
    assert not hasattr(off, "hidden_stick")
    assert json.dumps(off.summary, sort_keys=True, default=str) == json.dumps(base.summary, sort_keys=True, default=str)
    assert [vars(s) for s in off.switches] == [vars(s) for s in base.switches]


@pytest.mark.parametrize("arm", ["wait", "live_edge"])
def test_arms_run_a_whole_night(arm):
    base = _run()
    rep = _run(hidden_stick=arm, behind_live_s=90.0)
    hs = rep.hidden_stick
    live = [s for s in rep.switches if s.kind not in ("replay", "resume", "skip")]
    assert len(rep.switches) == len(base.switches)          # the decider isn't told: same decisions
    assert hs["switches"] == len(live) > 0 and hs["preloads"] > 0
    assert hs["buffered"] + len(hs["unbuffered_t"]) == hs["switches"]
    _, targets = _night()
    assert hs["prime_switches"] == sum(targets[s.to].startswith("prime:") for s in live) > 0
    for s, b in zip(rep.switches, base.switches):
        if s in live:
            assert s.offset == pytest.approx(b.offset - (90.0 if s.t in hs["unbuffered_t"] else 0.0))
    if arm == "wait":
        assert set(hs["unbuffered_t"]) == set(hs["prime_t"])   # arm A: only Prime airs at the live edge
    assert math.isfinite(hs["wait_s"])


def test_eval_sports_reports_the_arm(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_sports as es
    arch = tmp_path / "data" / "archive" / "nba"
    arch.mkdir(parents=True)
    shutil.copy(FIX / "eval_config.yaml", tmp_path / "data" / "eval_config.yaml")
    for night in ("20251110", NIGHT):   # two nights: the later one is the locked split, --split fit reads only the first
        shutil.copy(FIX / f"espn_nba_scoreboard_{night}.json", arch / f"scoreboard_{night}.json")
        for e in json.loads((FIX / f"espn_nba_scoreboard_{night}.json").read_text())["events"]:
            shutil.copy(FIX / f"espn_nba_summary_{e['id']}.json", arch / f"summary_{e['id']}.json")
    monkeypatch.setattr(es, "ROOT", tmp_path)
    base = ["eval_sports.py", "nba", "--end", NIGHT, "--nights", "1", "--behind-live", "90", "--feed-lag", "70"]
    # [EXP-601] train+valid only: a hidden-stick run that could read the locked nights is refused
    for split in ([], ["--split", "all"], ["--split", "test"]):
        monkeypatch.setattr(sys, "argv", base + split + ["--hidden-stick", "live_edge"])
        with pytest.raises(SystemExit, match="never reads locked"):
            es.main()
    out = {}
    for arm in (None, "live_edge"):
        monkeypatch.setattr(sys, "argv", base + ["--split", "fit"] + (["--hidden-stick", arm] if arm else []))
        capsys.readouterr()
        es.main()
        out[arm] = json.loads(capsys.readouterr().out)["per_night"][0]
    assert "hidden_stick" not in out[None]
    h = out["live_edge"]["hidden_stick"]
    assert h["arm"] == "live_edge" and h["switches"] == h["non_replay_switches"]
    assert {"buffered_pct", "wait_s", "prime_switches", "prime_mid_play_entries"} <= set(h)
