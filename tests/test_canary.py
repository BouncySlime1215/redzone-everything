"""[Canary, Nov 7 item 8] redzone/canary: the daily hidden-stick self-test never runs near a game, never touches the
stick on screen, always puts the hidden stick back on live, and fails loud. Offline fakes; real Nov 5-7 failures."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from redzone import canary as cn

HOST = "198.51.100.9:5555"   # a fake address: it must never reach a row or the phone


def state(games=(), role="standby", sstate="connected", host=HOST, name="stickB"):
    return {"games": list(games), "health": {"sticks": [
        {"host": "198.51.100.8:5555", "name": "stickA", "role": "on screen", "state": "connected"},
        {"host": host, "name": name, "role": role, "state": sstate}]}}


class FakeStick:
    def __init__(self, host, up=True, packages=None):
        self.host, self.up = host, up
        self.packages = {"youtubetv": "yt"} if packages is None else packages

    async def connect(self):
        return self.up

    async def identify(self):
        pass


class World:
    def __init__(self, states, stick=None, tune_ok=True, playing=True, join=None, alerts_rows=None):
        self.states = list(states)
        self.stick = stick or FakeStick(HOST)
        self.tunes, self.pushes, self.made = [], [], 0
        self.tune_ok, self.is_playing, self.join_res = tune_ok, playing, join
        self.join_calls = 0

    async def fetch_state(self):
        return self.states[0] if len(self.states) == 1 else self.states.pop(0)

    def make_stick(self, host):
        self.made += 1
        return self.stick

    async def tune(self, stick, channel):
        self.tunes.append(channel)
        ok = self.tune_ok(channel) if callable(self.tune_ok) else self.tune_ok
        return SimpleNamespace(ok=ok, error="" if ok else "Who's watching? prompt not cleared", steps=[])

    async def playing(self, stick):
        return self.is_playing

    async def do_join(self, stick, app, target, **kw):
        self.join_calls += 1
        self.join_kw = kw
        if callable(self.join_res):
            return await self.join_res(kw)
        return self.join_res

    def env(self, tmp_path):
        async def sleep(_s):
            await asyncio.sleep(0)
        return cn.Env(fetch_state=self.fetch_state, make_stick=self.make_stick, tune=self.tune, playing=self.playing,
                      do_join=self.do_join, make_probe=None, push=lambda t, s: self.pushes.append((s, t)),
                      journal_path=tmp_path / "canary.jsonl", joins_path=tmp_path / "joins.jsonl", sleep=sleep)


def join_result(measured=57.0, predicted=56.7, ok=True, reason="ok"):
    plan = SimpleNamespace(predicted_s=predicted)
    pe = None if measured is None else round(measured - predicted, 2)
    row = {"target_s": 60, "presses": 3, "predicted_s": predicted, "measured_s": measured, "pred_error_s": pe,
           "error_s": None if measured is None else measured - 60, "bias_used_s": 11.7, "bias_source": "shipped",
           "took_s": 20.0, "reason": reason}
    return SimpleNamespace(ok=ok, measured_s=measured, pred_error_s=pe, plan=plan, reason=reason, row=lambda: row)


CFG = {"canary.settle_s": 0.0}


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def run(world, tmp_path, dry=False):
    return asyncio.run(cn.Canary(Cfg(CFG), world.env(tmp_path)).run(dry_run=dry))


def no_address(tmp_path, world):
    text = (tmp_path / "canary.jsonl").read_text() + json.dumps(world.pushes)
    assert "198.51.100" not in text


def test_never_near_a_live_game_and_nothing_touched(tmp_path):
    w = World([state(games=[{"matchup": "MIA @ GS", "phase": "live"}])])
    r = run(w, tmp_path)
    assert r.status == "DEFERRED" and w.made == 0 and w.tunes == [] and cn.EXIT[r.status] == 2


def test_never_within_ninety_minutes_of_a_start(tmp_path):
    w = World([state(games=[{"matchup": "WSH @ NYK", "phase": "pre", "start_time": time.time() + 3600}])])
    r = run(w, tmp_path)
    assert r.status == "DEFERRED" and "starts in" in r.reason and w.made == 0


def test_api_down_fails_loud(tmp_path):
    w = World([None])
    r = run(w, tmp_path)
    assert r.status == "FAIL" and w.pushes and w.pushes[0][0] == "FAIL" and "runbooks/canary.md" in w.pushes[0][1]


def test_no_standby_stick_fails_loud(tmp_path):
    """Nov 5-6: one stick offline / two-stick mode off -> nothing is hidden, so nothing to test on."""
    w = World([state(sstate="offline")])
    r = run(w, tmp_path)
    assert r.status == "FAIL" and r.first_fail.name == "standby" and w.made == 0


def test_adb_drop_fails_loud(tmp_path):
    w = World([state()], stick=FakeStick(HOST, up=False))
    r = run(w, tmp_path)
    assert r.status == "FAIL" and r.first_fail.name == "adb" and w.tunes == []


def test_pass_goes_away_then_canary_then_back_to_live(tmp_path):
    w = World([state()], join=join_result())
    r = run(w, tmp_path)
    assert r.status == "PASS" and w.tunes == ["fs1", "espn", "fs1"] and cn.EXIT["PASS"] == 0
    assert w.join_kw["source"] == "canary" and w.join_kw["label"] == "stickB" and w.join_kw["channel"] == "espn"
    assert w.pushes[0][0] == "PASS"
    no_address(tmp_path, w)


def test_unread_label_fails_and_still_relinks(tmp_path):
    """The label reader or overlay changed: the join can't be measured -> FAIL, never a silent 'buffered'."""
    w = World([state()], join=join_result(measured=None))
    r = run(w, tmp_path)
    assert r.status == "FAIL" and r.first_fail.name == "join" and "not measured" in r.first_fail.detail
    assert w.tunes[-1] == "fs1" and r.checks[-1].name == "relink" and r.checks[-1].ok


def test_asked_90_read_23_is_a_fail(tmp_path):
    """#391 (Nick, Nov 7): a same-channel deep link left the stick behind live; the 'live edge' probe anchored short."""
    w = World([state()], join=join_result(measured=23.0, predicted=86.7))
    r = run(w, tmp_path)
    assert r.status == "FAIL" and "limit 6" in r.first_fail.detail


def test_program_boundary_join_failure_is_a_fail(tmp_path):
    w = World([state()], join=join_result(ok=False, measured=None, reason="knob moved 39s, not 60s"))
    r = run(w, tmp_path)
    assert r.status == "FAIL" and "39s" in r.first_fail.detail and w.tunes[-1] == "fs1"


def test_uncleared_prompt_fails_and_relinks(tmp_path):
    w = World([state()], tune_ok=lambda ch: ch != "espn", join=join_result())
    r = run(w, tmp_path)
    assert r.status == "FAIL" and r.first_fail.name == "tune" and "Who's watching" in r.first_fail.detail
    assert w.join_calls == 0 and w.tunes[-1] == "fs1"


def test_not_playing_after_tune_fails(tmp_path):
    w = World([state()], playing=False, join=join_result())
    r = run(w, tmp_path)
    assert r.status == "FAIL" and r.first_fail.name == "playing" and w.join_calls == 0


def test_a_game_going_live_mid_test_stops_it_and_relinks(tmp_path):
    w = World([state()], join=None)

    async def slow_join(kw):
        w.states = [state(games=[{"matchup": "MIA @ GS", "phase": "live"}])]
        for _ in range(50):
            await asyncio.sleep(0)
            if not kw["still_hidden"]():
                return join_result(ok=False, measured=None, reason="no longer hidden")
        return join_result()
    w.join_res = slow_join
    r = run(w, tmp_path)
    assert r.status == "DEFERRED" and "live" in r.reason and w.tunes[-1] == "fs1"


def test_stick_taken_on_screen_mid_test_is_never_touched_again(tmp_path):
    w = World([state()], join=None)

    async def slow_join(kw):
        w.states = [state(role="on screen")]
        for _ in range(50):
            await asyncio.sleep(0)
            if not kw["still_hidden"]():
                return join_result(ok=False, measured=None, reason="no longer hidden")
        return join_result()
    w.join_res = slow_join
    r = run(w, tmp_path)
    assert r.status == "DEFERRED" and w.tunes == ["fs1", "espn"]   # no relink on the stick now on screen
    assert r.checks[-1].name == "relink" and "skipped" in r.checks[-1].detail


def test_calibration_shift_is_a_warning(tmp_path, monkeypatch):
    from redzone.tuners import join_calib
    monkeypatch.setattr(join_calib.JoinCalib, "alerts", lambda self: ["youtubetv: last 3 joins landed later"])
    w = World([state()], join=join_result())
    r = run(w, tmp_path)
    assert r.status == "WARN" and w.pushes[0][0] == "WARN" and cn.EXIT["WARN"] == 4


def test_dry_run_touches_nothing_and_pushes_nothing(tmp_path):
    w = World([state()])
    r = run(w, tmp_path, dry=True)
    assert r.status == "PASS" and w.made == 0 and w.pushes == []


def test_crash_is_a_loud_fail(tmp_path):
    w = World([state()])

    async def boom(kw):
        raise RuntimeError("adb: device offline")
    w.join_res = boom
    r = run(w, tmp_path)
    assert r.status == "FAIL" and w.pushes[0][0] == "FAIL"


def test_once_per_day(tmp_path):
    p = tmp_path / "canary.jsonl"
    assert not cn.done_today(p)
    p.write_text(json.dumps({"ts": time.time(), "status": "FAIL"}) + "\n")
    assert not cn.done_today(p)
    p.write_text(json.dumps({"ts": time.time(), "status": "PASS"}) + "\n")
    assert cn.done_today(p)


def test_the_launchd_job_is_daily_and_never_at_load():
    import plistlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import canary as script
    d = plistlib.loads(script.plist(Path("/x/redzone")))
    assert d["Label"] == "com.redzone.canary" and d["RunAtLoad"] is False and len(d["StartCalendarInterval"]) == 2
    assert "--once-per-day" in d["ProgramArguments"][-1]


@pytest.mark.parametrize("status", ["FAIL", "WARN"])
def test_phone_text_names_the_check_and_runbook(status):
    r = cn.CanaryResult(status=status, stick="stickB", checks=[cn.Check("adb", False, "adb connect failed")])
    assert "adb" in r.phone_text() and "canary.md" in r.phone_text()


def _with_codes(world, tmp_path, miss):
    env = world.env(tmp_path)
    env.missing_codes = lambda chans: miss
    return env


@pytest.mark.parametrize("dry", [False, True])
def test_missing_channels_json_fails_before_any_key(tmp_path, dry):
    """Nick's rig note, Nov 7: a scratch worktree has no data/channels.json (gitignored) -> 'no watch code for fs1'.
    The preflight says so up front, in the dry run too, and never touches the stick."""
    w = World([state()], join=join_result())
    miss = "data/channels.json is missing (gitignored): run from the Mac repo, or symlink its data/ into this checkout"
    r = asyncio.run(cn.Canary(Cfg(CFG), _with_codes(w, tmp_path, miss)).run(dry_run=dry))
    assert r.status == "FAIL" and r.first_fail.name == "channels" and "channels.json" in r.first_fail.detail
    assert w.made == 0 and w.tunes == []


def test_codes_present_passes_the_preflight(tmp_path):
    w = World([state()], join=join_result())
    r = asyncio.run(cn.Canary(Cfg(CFG), _with_codes(w, tmp_path, "")).run())
    assert r.status == "PASS" and any(c.name == "channels" and c.ok for c in r.checks)


def test_real_preflight_names_the_missing_file_and_codes(tmp_path, monkeypatch):
    import redzone.channels as ch
    cfgp = tmp_path / "data" / "channels.json"
    monkeypatch.setattr(ch.ChannelMap.__init__, "__defaults__", (cfgp,))
    env = cn.real_env(Cfg({}), tmp_path, "http://127.0.0.1:1")
    assert "channels.json is missing" in env.missing_codes(["fs1", "espn"])
    cfgp.parent.mkdir()
    cfgp.write_text(json.dumps({"codes": {"espn": "abc"}}))
    env = cn.real_env(Cfg({}), tmp_path, "http://127.0.0.1:1")
    assert env.missing_codes(["fs1", "espn"]) == "no YouTube TV watch code for fs1 in data/channels.json"


def test_relink_gap_waits_between_tune_away_and_tune(tmp_path):
    """tuners.relink_gap_s (Nick Nov 7: back-to-back tunes failed 1/10, a 3 s gap fixed the delay probe)."""
    w = World([state()], join=join_result())
    order = []
    env = w.env(tmp_path)
    tune = env.tune

    async def tune_logged(stick, channel):
        order.append(("tune", channel))
        return await tune(stick, channel)

    async def sleep(s):
        order.append(("sleep", s))
    env.tune, env.sleep = tune_logged, sleep
    r = asyncio.run(cn.Canary(Cfg({**CFG, "tuners.relink_gap_s": 3.0}), env).run())
    assert r.status == "PASS"
    assert order[:3] == [("tune", "fs1"), ("sleep", 3.0), ("tune", "espn")]


def test_relink_gap_off_by_default(tmp_path):
    w = World([state()], join=join_result())
    env = w.env(tmp_path)
    slept = []

    async def sleep(s):
        slept.append(s)
    env.sleep = sleep
    asyncio.run(cn.Canary(Cfg(CFG), env).run())
    assert 3.0 not in slept and all(s == 0.0 for s in slept)
