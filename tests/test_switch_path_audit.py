"""The switch-path audit's findings, each pinned (docs/archive/audits/2026-11-08-switch-path.md, -devices.md)."""
import asyncio
import dataclasses
import json
import time

import pytest
from test_app import engine  # noqa: F401  (fixture)
from test_control import FakeStick, FakeTuner, ctl, two  # noqa: F401  (fixture)

from redzone.channels import ChannelMap
from redzone.decider.decider import REEL, Candidate, Decision, State
from redzone.engine import _stage_times
from redzone.models import Phase
from redzone.tuners.adb import RunResult
from redzone.tuners.tuners import TuneResult

FOX = "youtubetv:fox"


def run(coro):
    return asyncio.run(coro)


async def _parked(c, hid, delay_reads=0):
    """A snap-parked standby, as engine._buffered_park leaves it. delay_reads: reads that still say 'paused' after PLAY."""
    orig = hid.key
    left = [delay_reads]

    async def key(*codes):
        if "KEYCODE_MEDIA_PAUSE" in codes:
            hid.playback_state = "paused"
        if "KEYCODE_MEDIA_PLAY" in codes:
            hid.playback_state = "playing"
        return await orig(*codes)
    hid.key = key
    plain = hid.playback

    async def playback(package=None):
        pb = await plain(package)
        if pb["state"] == "playing" and left[0] > 0:
            left[0] -= 1
            return {**pb, "state": "paused"}
        return pb
    hid.remember(FOX, await plain("yttv.pkg"), scrubbed_s=79.0)
    c.held[FOX] = 79.0
    c._set_hidden(FOX, True)
    assert c.snap_park(FOX, 0.0)
    await c._snap_task
    assert c.snap_parked(FOX) is not None
    await asyncio.sleep(0.15)   # the pause it will have to count
    hid.playback = playback


def _buffered(cfg):
    cfg.update({"jump_mode": {"enabled": True}, "buffered_jump": {"enabled": True}})


def test_s1_a_jump_flips_to_a_snap_parked_standby(two, cfg):  # noqa: F811
    """Repro: parked standby, jump -> 'standby not ready (stopped playing)'. It must PLAY, count the pause, flip."""
    c, _ = two
    _buffered(cfg)
    hid = c.hidden

    async def go():
        await _parked(c, hid)
        c._preloaded_only = True
        return await c.execute(FOX, may_cut=lambda: True)
    out = run(go())
    assert out.ok and out.path == "preloaded", out.detail
    assert "KEYCODE_MEDIA_PLAY" in hid.log and c.main is hid and c.held[FOX] > 79.0


def test_s1_no_steady_wait_between_play_and_flip(two, cfg, monkeypatch):  # noqa: F811
    """The snap rolls from PLAY on: the 1 s steady wait there ate the lead the park planned. A read that still says
    'paused' right after PLAY is asked again, not refused (SAFE-08)."""
    c, _ = two
    _buffered(cfg)
    hid = c.hidden
    steady = []
    real = c._steady_playing

    async def counted(*a):
        steady.append(a)
        return await real(*a)

    async def go():
        await _parked(c, hid, delay_reads=2)
        monkeypatch.setattr(c, "_steady_playing", counted)
        c._preloaded_only = True
        return await c.execute(FOX, may_cut=lambda: True)
    out = run(go())
    assert out.ok, out.detail
    assert not steady
    names = [n for n, _ in out.marks]
    assert names.index("snap_play") < names.index("flip_start")


def test_s6_restart_keeps_behind_on_so_a_tune_resets_first(two, cfg, tmp_path, monkeypatch):  # noqa: F811
    """Repro: restart restored held but not behind_on, so a same-channel tune booked a stick still 75 s behind as live."""
    c, _ = two
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    c.sticks[1].behind_on = FOX
    c._set_held(FOX, 75.0, how="scrubbed")
    n = ctl.Controller(cfg, c.channels)
    n.buffer_log_path = c.buffer_log_path
    n.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    assert n.restore_held() == {FOX: 75.0} and n.sticks[1].behind_on == FOX
    seen = []

    async def reset(stick, target, tuner):
        seen.append(target)
        return TuneResult(True, target, 0.0, [], "")
    monkeypatch.setattr(n, "_reset_through_other", reset)
    run(n._tune(n.sticks[1], FOX))
    assert seen == [FOX]


def test_s7_held_json_is_atomic(two, tmp_path, monkeypatch):  # noqa: F811
    """A crash mid-save must leave the last good file (write_text on the real path left half a file)."""
    c, _ = two
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    c._set_held(FOX, 75.0)
    good = (tmp_path / "held.json").read_text()

    from pathlib import Path
    orig = Path.write_text

    def crash(self, data):
        orig(self, data[:5])   # the crash lands mid-write
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", crash)
    c._set_held(FOX, 80.0)
    assert (tmp_path / "held.json").read_text() == good and json.loads(good)["held"] == {FOX: 75.0}


def test_s10_a_flip_that_happened_is_never_a_failed_switch(two, monkeypatch):  # noqa: F811
    """Repro: an exception in the post-flip books -> 'crashed', ok=False, decider stuck on the old game."""
    c, _ = two

    def boom(*a):
        raise RuntimeError("books")
    monkeypatch.setattr(c, "_book_flip", boom)
    out = run(c.execute(FOX, may_cut=lambda: True))
    assert out.ok and "bookkeeping failed" in out.detail
    assert c.on_target == FOX and c.main.host == "b:5555"


def test_s11_a_replay_is_never_rewound_on_the_visible_stick(cfg, tmp_path, monkeypatch):
    """Repro: 'can't play on the hidden stick' / one stick -> the viewer saw the live edge, then the seek."""
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [FakeStick("a:5555")]
    monkeypatch.setitem(ctl.TUNERS, "prime", FakeTuner())
    out = run(c.execute("prime:abc", replay_back_s=20.0, may_cut=lambda: True))
    assert not out.ok and out.screen_unchanged and "hidden stick" in out.detail
    assert not any("REWIND" in x for x in c.sticks[0].log)


def test_dev06_a_search_tuned_channel_has_no_relaunch_link(two, monkeypatch):  # noqa: F811
    """Repro: HOME flip relaunch sent https://tv.youtube.com/watch/search:nbcsn."""
    c, _ = two
    c.channels.yttv_search_key = lambda t: "nbcsn"
    c.channels.search_ref = lambda k: f"search:{k}"
    c.channels.yttv_code = lambda t: ""
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", type("T", (), {"url": lambda self, ref: f"https://tv.youtube.com/watch/{ref}"})())
    st = c.sticks[0]
    run(c._relaunch(st, "yttv.pkg", time.monotonic() - 60, "youtubetv:nbcsn"))
    assert not any("watch/search" in x for x in st.log) and any("resolve-activity" in x for x in st.log)


def _hdmi_said(st, addr):
    orig = st.shell

    async def shell(cmd, timeout=8.0):
        if "hdmi_control | grep" in cmd and "physical" in cmd:
            return RunResult(True, f"physical_address: {addr}", 0.01)
        return await orig(cmd, timeout)
    st.shell = shell


def test_dev09_a_stick_on_the_wrong_input_degrades_and_alerts(two, tmp_path, monkeypatch):  # noqa: F811
    c, _ = two
    monkeypatch.setattr(ctl, "DEVICES", tmp_path / "devices.json")
    alerts = []
    c.on_alert = alerts.append
    assert c.two_stick_why() == ""
    _hdmi_said(c.sticks[0], "0x2000")   # the map says HDMI1, the stick says it's on 2
    run(c._check_identities())
    assert c.two_stick_why() != "" and alerts and "HDMI2" in alerts[0] and not c.two_stick_ok
    c._two_stick_retry_at = 0.0
    c.input_unknown = False
    run(c._check_identities())          # checked once per connection: no alert storm, still off
    assert len(alerts) == 1 and not c.two_stick_ok


def test_dev09_serial_mismatch_when_cec_is_off(two, tmp_path, monkeypatch):  # noqa: F811
    c, _ = two
    dev = tmp_path / "devices.json"
    dev.write_text(json.dumps({"sticks": [{"host": "a:5555", "serial": "AAA"}, {"host": "b:5555", "serial": "BBB"}]}))
    monkeypatch.setattr(ctl, "DEVICES", dev)
    st = c.sticks[1]
    orig = st.shell

    async def shell(cmd, timeout=8.0):
        if "ro.serialno" in cmd:
            return RunResult(True, "ZZZ\n", 0.01)
        return await orig(cmd, timeout)
    st.shell = shell
    run(c._check_identities())
    assert c.two_stick_why() == "degraded" and "b:5555" in c._stick_mismatch and "a:5555" not in c._stick_mismatch


def test_dev09_no_evidence_is_not_a_mismatch(two, tmp_path, monkeypatch):  # noqa: F811
    c, _ = two
    monkeypatch.setattr(ctl, "DEVICES", tmp_path / "devices.json")   # no file, CEC reads nothing
    run(c._check_identities())
    assert c.two_stick_why() == "" and not c._stick_mismatch


def test_dev18_onetouchplay_verifies_by_cec_when_the_tv_has_no_readback(two, monkeypatch):  # noqa: F811
    """Repro: with the cloud gone input_status is None, so a worked onetouchplay reported 'could not be verified'."""
    c, _ = two
    st = c.sticks[1]

    async def none():
        return None

    async def no(*a, **k):
        return False

    async def yes(hdmi):
        return True
    monkeypatch.setattr(c.tv, "input_status", none)
    monkeypatch.setattr(c.tv, "ready", lambda: False)
    monkeypatch.setattr(c, "_home_flip_cec", no)
    monkeypatch.setattr(c, "_cec_corroborate", yes)
    assert run(c._flip_to(st)) is True


def test_stage_times_are_numbers_and_skip_what_did_not_run():
    out = type("O", (), {"marks": [("started", 1.0), ("snap_play", 2.0), ("flip_start", 2.4)]})()
    tr = {"spans": {"decide": 0.1, "queued": 0.2, "tune": None, "safe_wait": 3.0, "flip": 2.5, "resume": 0.6,
                    "to_picture": 6.0, "total": 7.0}}
    s = _stage_times(tr, out)
    assert s == {"stage_decide_s": 0.3, "stage_wait_s": 3.0, "stage_play_to_flip_s": 0.4, "stage_flip_s": 2.5,
                 "stage_picture_s": 0.6, "stage_to_picture_s": 6.0, "stage_total_s": 7.0}
    assert _stage_times(None, type("O", (), {"marks": []})()) == {}


# --- the engine's half ----------------------------------------------------------------------------------------------

def _two_live(e, monkeypatch):
    """Two live, scored games; the dry-run switch decide() starts is dropped (these tests submit their own)."""
    monkeypatch.setattr(e.control, "submit", lambda *a, **k: None)
    live = [gid for gid, g in e.merger.games.items() if g.phase == Phase.LIVE]
    for gid, g in list(e.merger.games.items()):
        if len(live) >= 2:
            break
        if gid not in live:
            g.phase, g.period, g.clock_s = Phase.LIVE, 3, 600.0
            live.append(gid)
    e.decide()
    e._pending_decision, e._pending_done = None, None
    return live[:2]


def _live_engine(e, monkeypatch):
    e.cfg.update({"mode": {"dry_run": False}})
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda *a, **k: got.update(a=a, k=k, may_cut=k["may_cut"]))
    return got


def _jump(e, monkeypatch, before, to):
    """A jump to `to`, may_cut captured; the gates record what they were asked."""
    got = _live_engine(e, monkeypatch)
    w = dataclasses.make_dataclass("W", ["target"])
    e.where[to], e.where[before] = w("youtubetv:b"), w("youtubetv:a")
    monkeypatch.setattr(e.feeds, "stale", lambda g: False)
    monkeypatch.setattr(e.control, "deadline_cost", lambda t: 3.0)
    monkeypatch.setattr(e.decider, "jump_lands_dead", lambda *a: True)
    cands = {g: Candidate(e.scored[g], f"youtubetv:{n}") for g, n in ((before, "a"), (to, "b"))}
    d = Decision(time.time(), "switch", to, "jump", State.PAUSED, kind="jump")
    e.decider.current = before
    e._act(d, before, cands)
    return got, d


def test_s2_a_jump_judges_the_leave_on_live_scores_not_the_ticks(engine, monkeypatch):  # noqa: F811
    """Repro: may_cut re-polls for 25 s on the decision tick's cands: a game turned red zone still read low leverage."""
    a, b = _two_live(engine, monkeypatch)
    locked = []

    def may_leave(gid, cands, now, req):
        locked.append(bool(cands[gid].scored.lock))
        return not cands[gid].scored.lock
    monkeypatch.setattr(engine.decider, "jump_may_leave", may_leave)
    got, _ = _jump(engine, monkeypatch, a, b)
    assert got["may_cut"]() is True
    engine.scored[a] = dataclasses.replace(engine.scored[a], lock=True)
    assert got["may_cut"]() is False and locked == [False, True]


def test_s3_leave_margin_is_never_under_the_measured_flip(engine, monkeypatch):  # noqa: F811
    assert engine._leave_margin() >= 3.2
    monkeypatch.setattr(engine.control, "flip_path", lambda: "home_flip")
    engine.control.latency.samples["home_flip"] = [5.0] * 6
    assert engine._leave_margin() >= 5.0
    a, b = _two_live(engine, monkeypatch)
    asked = []
    monkeypatch.setattr(engine.decider, "jump_may_leave", lambda g, c, n, req: asked.append(req) or True)
    got, _ = _jump(engine, monkeypatch, a, b)
    got["may_cut"]()
    assert asked and asked[0] >= 5.0


@pytest.mark.parametrize("how", ["back", "focus", "replay"])
def test_s4_a_tap_waits_for_a_dead_ball_and_a_second_tap_goes(engine, monkeypatch, how):  # noqa: F811
    """Repro: Back / Focus / Replay never asked safe_to_leave and cut mid-play. N4: they wait unless tapped twice."""
    a, b = _two_live(engine, monkeypatch)
    got = _live_engine(engine, monkeypatch)
    engine.decider.current = a
    engine.decider.stack.append(b)
    monkeypatch.setattr(engine, "safe_to_leave", lambda g, required_s=0.0: False)
    monkeypatch.setattr(engine, "_tap_lands_dead", lambda d, t: True)
    miss = None
    if how == "replay":
        miss = dataclasses.make_dataclass("M", ["pid", "game_id", "label", "replayed", "t"])("p1", b, "a play", False, 1.0)
        miss.back_s = lambda now, mu, plen: 10.0
        engine.decider.queue.append(miss)
    args = {"back": ("back", None, None), "focus": ("focus", b, "focus"), "replay": ("replay", b, "p1")}[how]
    ok, msg = engine.action(*args)
    assert ok and "dead ball" in msg
    assert got["may_cut"]() is False                  # first tap: mid-play it waits
    ok, msg = engine.action(*args)
    assert ok and "now" in msg
    assert got["may_cut"]() is True                    # second tap: it goes


def test_s5_the_reel_is_free_to_leave(engine):  # noqa: F811
    """Repro: the reel has no timeline, so safe_to_leave said no for the whole 25 s wait."""
    assert engine.safe_to_leave(REEL) is True
