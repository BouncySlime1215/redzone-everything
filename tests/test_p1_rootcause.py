"""[P1-ROOTCAUSE, Nov 7 slate] The switching bottlenecks with a code cause: each bug test failed before its fix."""
import asyncio
import copy
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest

from redzone import control as ctl
from redzone.config import Config
from redzone.control import Outcome
from redzone.decider import buffered as bj
from redzone.decider.decider import Decision, State
from redzone.engine import Engine
from redzone.platform import wake
from redzone.plan import day_of, path_for
from redzone.sources.http import Fetcher
from redzone.store import Store
from redzone.tuners import adb
from redzone.tuners import tuners as tn
from redzone.tuners.adb import RunResult
from tests.test_buffered_jump import EDGE, cands, scene, setup, whistle
from tests.test_scrub_entry import Res, scrub  # noqa: F401  (fixture)
from tests.test_yttv_search_abc import FX, tool
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)

PRIME = "prime:amzn1.test"


def run(coro):
    return asyncio.run(coro)


# --- 01 / 02 / 04: the decider ------------------------------------------------------------------------------------

def test_01_a_live_edge_screen_counts_one_hold_per_tick(cfg, clock):
    """Bug: bj_screen_not_buffered and play_on_screen both counted on the same ticks: 126 min read, 74 min real."""
    dec, delays = setup(cfg, clock)
    scene(dec, clock)
    delays.set_behind("youtubetv:a", 0.0)
    delays.held.pop("youtubetv:a")
    dec.standby_ready = lambda target: False
    dec.decide(cands(a=60, b=50))
    assert "bj_screen_not_buffered" not in dec.jump_holds and "bj_bootstrap_wanted" not in dec.jump_holds
    assert sum(dec.jump_holds.values()) <= 1, dict(dec.jump_holds)


def test_01_bootstrap_wanted_only_with_a_ready_buffered_standby(cfg, clock):
    dec, delays = setup(cfg, clock)
    scene(dec, clock)
    delays.set_behind("youtubetv:a", 0.0)
    delays.held.pop("youtubetv:a")
    x = clock.t - EDGE
    whistle(dec.timelines["a"], "a3", x - 15)
    whistle(dec.timelines["a"], "a4", x + 50)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind == "jump" and d.jump["holds"].get("bj_bootstrap_wanted") == 1


def test_02_a_start_pick_keeps_the_ready_buffered_standby(cfg, clock):
    """The 'start' to another game goes through decide(), not only the helper: the buffered standby isn't re-tuned."""
    dec, _ = setup(cfg, clock)
    scene(dec, clock)
    dec.standby_ready = lambda target: target == "youtubetv:b"
    dec.current = None
    start = Decision(clock.t, "switch", "c", "start", State.WATCHING, "", kind="start")
    owner, why = dec._own(start, cands(b=50, c=70), clock.t)
    assert (owner, why) == ("buffered", "screen_gone")
    kept = dec._jump(start, cands(b=50, c=70), clock.t, why)
    assert kept is not None and kept.action in ("stay", "switch") and kept.target != "c"


def test_04_decide_uses_the_same_flip_cost_as_the_flip_check(cfg, clock):
    """Bug: decided with flip_s 3.2, vetoed at the flip with cost 4.1, then 25 s burned (00:15:41)."""
    dec, _ = setup(cfg, clock)
    scene(dec, clock)
    cs = cands(b=50)
    p = dec._bj_pic("b", cs["b"], clock.t)
    dec.deadline_cost = lambda target: 3.0
    assert dec._bj_flip_cost("youtubetv:b") == 3.2
    dec.deadline_cost = lambda target: 4.1
    assert dec._bj_flip_cost("youtubetv:b") == 4.1
    for cost in (3.0, 4.1, 30.0):
        dec.deadline_cost = lambda target: cost
        assert dec._bj_lands("b", p, cs, clock.t)[0] == dec.jump_lands_dead("b", cs["b"], clock.t, cost), cost


def test_04_a_bad_cost_never_lands():
    p = bj.Pic(None, 0.0, 0.0)
    assert bj.lands(p, 0.0, 2.0, float("inf"), 2.0, 2.0) == (False, -1.0)


# --- 04: the wait -------------------------------------------------------------------------------------------------

def _named(fn, veto="", tap=False):
    fn.veto, fn.tap = veto, tap
    return fn


def test_04_the_wait_is_capped_by_config(two):  # noqa: F811
    c, _ = two
    c.cfg.update({"two_stick": {"max_safe_wait_s": 0.05}})
    t0 = time.monotonic()
    assert run(c._wait_safe(_named(lambda: False))) is None
    assert time.monotonic() - t0 < 1.0 and 0.05 <= c._safe_gave_up_s < 1.0


def test_04_shipped_caps(two):  # noqa: F811
    c, _ = two
    assert c._safe_wait_cap(_named(lambda: False)) == 12.0   # measured useful waits peak at 10.7 s
    assert c._safe_wait_cap(_named(lambda: False, tap=True)) == 25.0   # [N4] a tap keeps its wait


@pytest.mark.parametrize("veto", ctl.HOPELESS_VETOES)
def test_04_a_hopeless_veto_gives_up_at_once(two, veto):  # noqa: F811
    c, _ = two
    t0 = time.monotonic()
    assert run(c._wait_safe(_named(lambda: False, veto))) is None
    assert time.monotonic() - t0 < 0.5


def test_04_a_landing_veto_keeps_waiting(two):  # noqa: F811
    c, _ = two
    calls = []

    def may():
        calls.append(1)
        return len(calls) > 2
    assert run(c._wait_safe(_named(may, "landing"))) is not None


def _engine(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}})
    return Engine(cfg, Store(tmp_path / "t.db"))


def test_04_every_kind_names_its_veto(tmp_path, monkeypatch):
    """Bug: 11 of 23 'no safe moment' fails were 'start' switches, and only jumps logged why."""
    e = _engine(tmp_path)
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: got.update(done=done, **kw))
    monkeypatch.setattr(e, "_entry_valid", lambda d, target: False)
    d = Decision(time.time(), "switch", "g2", "start", State.WATCHING, kind="start")
    e._act(d, None, {})
    may_cut = got["may_cut"]
    assert may_cut() is False and may_cut.veto == "entry_invalid" and may_cut.tap is False
    got["done"](Outcome(False, None, 1.0, "no safe moment to cut — kept the current game", False, waited=12.0))
    assert "entry_invalid" in json.dumps(e.store.recent_switches(1)[0])


# --- 03: the score bug --------------------------------------------------------------------------------------------

def _prime_on_screen(c):
    c.main.tuned = {"target": PRIME}
    c.on_target = PRIME


def test_03_a_preload_on_the_hidden_stick_does_not_stop_the_visible_read(two):  # noqa: F811
    """Bug: any preload blanked the score bug, so Prime's delay doubt stayed +-13.3 s all night."""
    c, _ = two
    _prime_on_screen(c)

    async def cap(timeout=8.0):
        return b"png"
    c.main.screencap = cap

    async def go():
        c._preload_task = asyncio.create_task(asyncio.sleep(5))
        await c._lock.acquire()               # a preload holds the control lock
        try:
            sticks = c.scorebug_sticks(True)
            assert [(s, t, v) for s, t, v in sticks] == [(c.main, PRIME, True)]
            frames = await c.scorebug_frames(True)
            assert len(frames) == 1 and frames[0]["visible"] is True
        finally:
            c._lock.release()
            c._preload_task.cancel()
    run(go())


def test_03_the_preloading_stick_itself_is_not_read(two):  # noqa: F811
    c, _ = two
    h = c.hidden
    h.tuned = {"target": PRIME}
    c._set_hidden(PRIME, True, why="test")

    async def go():
        c._preload_task = asyncio.create_task(asyncio.sleep(5))
        assert c.scorebug_sticks(True) == [] and c.bug_why == "preload_running"
        c._preload_task.cancel()
    run(go())


def test_03_a_switch_still_blanks_the_score_bug(two):  # noqa: F811
    c, _ = two
    _prime_on_screen(c)

    async def go():
        c._task = asyncio.create_task(asyncio.sleep(5))
        assert c.scorebug_sticks() == [] and c.bug_why == "switch_running"
        c._task.cancel()
    run(go())


# --- 07: Prime's steps ---------------------------------------------------------------------------------------------

class PrimeLoading(FakeStick):
    def __init__(self):
        super().__init__("p:5555")
        self.reads = 0

    async def screen_text(self):
        self.reads += 1
        return ""

    async def playback(self, package=None):
        return {"state": "buffering", "pkg": package}


def test_07_no_screen_read_once_the_player_is_loading():
    """Bug: each round paid uiautomator (12 s) + OCR (16 s) with the stream already loading."""
    t = tn.PrimeTuner()
    t.page_wait_s = 0.0
    stick = PrimeLoading()

    async def watch(*a):
        return False

    async def base(self, *a, **k):
        return True
    t._watch_for = watch
    orig = tn.Tuner._confirm
    tn.Tuner._confirm = base
    try:
        steps = tn.TimedSteps()
        assert run(t._confirm(stick, "prime.pkg", steps)) is True
    finally:
        tn.Tuner._confirm = orig
    assert stick.reads == 0 and any("screen read skipped" in m["step"] for m in steps.marks)


def test_07_steps_carry_times_but_stay_plain_steps():
    s = tn.TimedSteps()
    s.append("deep link #1: ok")
    s.mark("timing only")
    assert list(s) == ["deep link #1: ok"] and [m["step"] for m in s.marks] == ["deep link #1: ok", "timing only"]
    assert all(m["at_s"] >= 0 for m in s.marks)


def test_07_prime_tunes_are_logged_with_their_steps(two, tmp_path, monkeypatch):  # noqa: F811
    c, _ = two
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    steps = tn.TimedSteps()
    steps.append("deep link #1: ok")

    class T:
        async def tune(self, stick, ref, target):
            return tn.TuneResult(True, target, 25.3, steps)
    monkeypatch.setitem(ctl.TUNERS, "prime", T())
    monkeypatch.setattr(c, "_ref", lambda target: ("prime", "ref"))
    run(c._tune(c.hidden, PRIME))
    row = json.loads(c.buffer_log_path.read_text())
    assert row["kind"] == "prime_tune" and row["steps"][0]["step"] == "deep link #1: ok" and "at_s" in row["steps"][0]


# --- 09: adb --------------------------------------------------------------------------------------------------------

def test_09_handshake_tolerates_a_mac_stall():
    assert adb.HANDSHAKE_S == 8.0


def test_09_a_hang_names_its_command_and_nothing_after_it(caplog):
    s = adb.Stick("a:5555")

    async def hung(cmd, timeout):
        return RunResult(False, "timeout/error", 8.0)
    s._shell = hung
    with caplog.at_level(logging.WARNING):
        run(s.shell("am start -a android.intent.action.VIEW -d secret://x"))
    assert "am start" in caplog.text and "secret" not in caplog.text and "8.0 s" in caplog.text


# --- cross-file ------------------------------------------------------------------------------------------------------

def test_keepalive_does_not_wait_on_the_tv(two, monkeypatch):  # noqa: F811
    c, _ = two
    called = []
    monkeypatch.setattr(c.tv, "maintain_soon", lambda: called.append(1))

    async def boom():
        raise AssertionError("keepalive must not await maintain()")
    monkeypatch.setattr(c.tv, "maintain", boom)
    c.adb_ok, c._tv_maintained_at = True, 0.0
    now = time.time()
    c._cec_read_at, c._cec_synced_at = now, now
    for s in c.sticks:
        s.last_ok = now
    run(c.keepalive())
    assert called == [1]


def test_the_cloud_is_no_tv_path_when_smartthings_is_off(two, monkeypatch):  # noqa: F811
    c, _ = two
    c.cfg.update({"two_stick": {"smartthings_cloud": False, "cec_flip": False}})
    monkeypatch.setattr(c.tv, "ready", lambda: False)
    monkeypatch.setattr(type(c.tv), "cloud_disabled", property(lambda self: True))
    assert c.two_stick_why() == "no_flip_path"


def test_reconnect_after_a_drop_identifies_again(two):  # noqa: F811
    c, _ = two
    s = c.sticks[0]
    s.state, s.last_ok = "offline", 0.0
    seen = []

    async def connect():
        s.state = "connected"
        return True

    async def identify():
        seen.append(1)
    s.connect, s.identify = connect, identify
    run(c._reconnect(s))
    assert seen == [1]


def test_wake_clears_the_tv_backoff():
    called = []
    tv = SimpleNamespace(_drop_local=lambda: None, reset_backoff=lambda: called.append(1))
    wake.recover(SimpleNamespace(control=SimpleNamespace(sticks=[], tv=tv)), "test")
    assert called == [1]


def _fetcher(headers, status=200):
    async def handler(request):
        return httpx.Response(status, json={"a": 1}, headers=headers)
    t = [1000.0]
    return Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=lambda: t[0]), t


@pytest.mark.parametrize("headers,want", [({"Age": "19"}, 981.0), ({}, 1000.0), ({"Age": "soon"}, 1000.0)])
def test_f15_freshness_counts_the_cdn_age(headers, want):
    """Bug: a 19 s old CDN copy was stamped fresh as of the request."""
    f, _ = _fetcher(headers)
    run(f.get_json("https://x.test/a"))
    assert f.fetched_at("https://x.test/a") == want


def test_f15_age_does_not_change_the_poll_interval():
    f, t = _fetcher({"Age": "19"})
    run(f.get_json("https://x.test/a"))
    t[0] += 5
    assert f._cache["https://x.test/a"].fetched_at == 1000.0


def test_restart_reads_back_the_screen_and_its_leverage(tmp_path):
    """Bug (P-04 was dormant): nothing saved the screen game or called note_restart."""
    e = _engine(tmp_path)
    e.decider.current = "g1"
    e.scored["g1"] = SimpleNamespace(high_leverage=True, crunch=False, late=False, garbage=False, personal=0, closeness=0)
    e._save_screen_state()
    e2 = _engine(tmp_path)
    e2._restore_screen_state()
    gid, at, lev = e2.decider._restored_screen
    assert gid == "g1" and lev is True and abs(at - time.time()) < 5


def test_keep_awake_counts_a_game_planned_within_the_hour(tmp_path):
    """Bug (P-10): caffeinate only started once a game was live, and a sleeping Mac never saw that."""
    e = _engine(tmp_path)
    assert e._planned_soon() is False
    p = path_for(day_of(time.time()), e._user_state_path.parent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"games": [{"start": time.time() + 1800}]}))
    assert e._planned_soon() is True
    p.write_text(json.dumps({"games": [{"start": time.time() + 7200}]}))
    assert e._planned_soon() is False


def test_defaults(cfg):
    assert cfg.get("mode.leagues.wcbb") is False and "wcbb" not in cfg.get("core.league_priority")
    assert cfg.get("two_stick.identity_check") is True
    assert cfg.get("buffered_jump.roll_wait_s") == 1.5 and cfg.get("buffered_jump.roll_poll_s") == 0.15


def test_r16_changed_keys_are_values_not_comments():
    from scripts import check_memory as cm
    diff = """--- a/x
+++ b/x
@@ -1 +1 @@
-  max_x: 1   # old note
+  max_x: 2   # old note
-  same: 1   # a
+  same: 1   # b
+  brand_new: true
-  leagues: {a: true}
+  leagues: {a: false}
"""
    assert cm.changed_keys(diff) == {"max_x", "brand_new", "leagues"}


def test_r16_a_changed_key_must_be_named(monkeypatch):
    """Bug (R16): any line added to decisions.md satisfied the rule."""
    from scripts import check_memory as cm
    cfgd = "-  max_x: 1\n+  max_x: 2\n"
    monkeypatch.setattr(cm, "_diff", lambda base, path, root: ("+- a line about something else\n" if path == cm.DECISIONS else cfgd))
    assert "max_x" in cm.unlogged_keys("b")[0]
    monkeypatch.setattr(cm, "_diff", lambda base, path, root: ("+- two_stick.max_x went to 2\n" if path == cm.DECISIONS else cfgd))
    assert cm.unlogged_keys("b") == []


def test_05_a_failed_scrub_is_relinked_for_the_retry_and_logged(scrub, tmp_path, monkeypatch):  # noqa: F811
    """Bug: _scrub_into passed no relink, so enter_buffer's 'knob moved' retry was dead code."""
    c, tuner, hid, *_ = scrub
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    seen = {}

    async def enter_buffer(stick, seconds, *, cfg=None, pkg=None, still_hidden=None, relink=None, **kw):
        seen["relinked"] = await relink()
        r = Res(ok=False, behind_s=None, reason="knob moved 0s, not 60s")
        r.presses, r.committed, r.relinked, r.retries, r.steps = 5, False, seen["relinked"], 1, ["bar focused at 3600s"]
        return r
    import redzone.tuners as _tuners
    monkeypatch.setattr(_tuners.yttv_seek, "enter_buffer", enter_buffer)
    run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert seen["relinked"] is True
    # the first tune, then the relink through another channel; no second relink after a known live edge
    assert tuner.calls == [("b:5555", "youtubetv:espn"), ("b:5555", "youtubetv:fox"), ("b:5555", "youtubetv:espn")]
    assert hid.behind_on is None
    row = json.loads(c.buffer_log_path.read_text().splitlines()[-1])
    assert row["kind"] == "scrub_failed" and row["steps"] == ["bar focused at 3600s"] and row["retries"] == 1


async def _hunt(fail_reads):
    from redzone.channels import LOCAL_SEARCH_QUERY
    from redzone.discovery.yttv import load_table
    from redzone.screen_ocr import OCRResult
    from redzone.tuners.prime_search import CapturedScreen
    from redzone.tuners.yttv_search import hunt_channel
    from tests.test_yttv_search_abc import MID as wall
    now, seq = [1000.0], [0]

    async def sleep(s):
        now[0] += s
    fx = copy.deepcopy(FX)
    stick = tool.FixtureStick(fx, lambda: now[0])

    async def observe():
        seq[0] += 1
        now[0] += .3
        if seq[0] <= fail_reads:
            return CapturedScreen(OCRResult("error", error="screencap returned nothing"), seq[0], now[0])
        return CapturedScreen(tool._ocr(fx["scenes"].get(stick.scene, {"boxes": []})), seq[0], now[0])
    return await hunt_channel(stick, observe, key="abc", table=load_table(), query=LOCAL_SEARCH_QUERY["abc"],
                              sleep=sleep, clock=lambda: now[0], wall=lambda: wall, collect_codes=False, channel_page=True)


def test_08_one_failed_read_is_read_again():
    """Bug: one failed screencap ended the whole search tune."""
    h = run(_hunt(1))
    assert h.status == "confirmed", h.reason
    assert any(s["action"] == "read_failed" and "screencap" in s["detail"] for s in h.steps)


def test_08_the_error_names_the_step():
    h = run(_hunt(9))
    assert h.status != "confirmed" and "screencap returned nothing" in h.reason
