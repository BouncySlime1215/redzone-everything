"""The supervisor: every rule, with fakes (no TV, no network, no adb)."""
import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from redzone import supervisor as sup
from redzone.config import Config
from redzone.decider.decider import Decider, Decision, FocusMode, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League, Phase
from redzone.store import Store
from redzone.supervisor import Limiter, Supervisor, build_report, frames_verdict, night_of, night_window
from conftest import FakeClock, nfl

YT = "com.amazon.firetv.youtube.tv"
T0 = datetime(2026, 11, 2, 20, 0, tzinfo=sup.ET).timestamp()   # 8 PM ET, a game night


# --- fakes -----------------------------------------------------------------------------------

class FakeStick:
    def __init__(self, host="10.0.0.9:5555"):
        self.host = host
        self.state = "connected"
        self.packages = {"youtubetv": YT}
        self.fg = f"{YT}/.PlayerActivity"
        self.pb = {"state": "playing", "read_ok": True}
        self.caps = [b"frame-a", b"frame-b"]
        self.keys: list[str] = []
        self.calls: list[str] = []
        self.connect_ok = True
        self._next_retry = 99.0

    async def foreground(self):
        self.calls.append("foreground")
        return self.fg

    async def playback(self, pkg=None):
        self.calls.append("playback")
        return dict(self.pb)

    async def screencap(self, timeout=8.0):
        self.calls.append("screencap")
        self._cap = getattr(self, "_cap", -1) + 1
        return self.caps[self._cap % len(self.caps)]   # one entry = a still screen; several = moving

    async def key(self, *codes):
        self.keys.extend(codes)

    def drop_session(self):
        self.calls.append("drop_session")

    async def connect(self):
        self.calls.append("connect")
        if self.connect_ok:
            self.state = "connected"
        return self.connect_ok

    async def identify(self):
        self.calls.append("identify")


class FakeControl:
    def __init__(self, stick):
        self.live_control = True
        self.input_unknown = False
        self._flipping = False
        self.main = stick
        self.sticks = [stick]
        self.on_target = "youtubetv:espn"
        self.adb_ok = True
        self._task = None
        self._resume_task = None
        self._visible_fix_at = 0.0
        self._failed = {"youtubetv:espn": 1e12}
        self.busy = False
        self.restored: list[tuple] = []
        self.restore_result = "relink"
        self.cancelled = 0
        self.forgot: list = []

    def _busy(self):
        return self.busy

    def _package(self, stick, target):
        return YT if target.startswith("youtubetv") else None

    def cancel_pending(self):
        self.cancelled += 1

    async def restore_visible(self, target, relink=False, why=""):
        self.restored.append((target, relink))
        return self.restore_result

    def _forget(self, stick):
        self.forgot.append(stick)


class FakeMerger:
    def __init__(self, games):
        self.games = {g.game_id: g for g in games}
        self.stale: set[str] = set()

    def is_stale(self, gid):
        return gid in self.stale


class FakeStore:
    def __init__(self):
        self.rows: list[dict] = []
        self.checkpoints = 0

    def recent_switches(self, n=50):
        return sorted(self.rows, key=lambda r: -r["ts"])[:n]

    def calibrations(self, since=0.0):
        return []

    def checkpoint(self):
        self.checkpoints += 1


def where(target, tunable=True, alternates=()):
    from redzone.discovery.providers import Pick
    return Pick(target, target, "sure", tunable, provider=target.split(":")[0], alternates=list(alternates))


@pytest.fixture
def world(tmp_path):
    clock = FakeClock(T0)
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}}, record=False)
    delays = DelayBook(cfg.get("delay.default"), cfg.get("delay.sigma_default"), clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    stick = FakeStick()
    control = FakeControl(stick)
    game = nfl("g1")
    toasts, released, feeds = [], [], []
    e = SimpleNamespace(
        cfg=cfg, decider=dec, control=control, merger=FakeMerger([game]), delays=delays,
        where={"g1": where("youtubetv:espn")}, _pending_decision=None, _pending_since=0.0, _pending_done=None,
        _fail_cool={}, hold=None, last_decision=None, store=FakeStore(), recorded=None,
        toast=lambda text, level="info": toasts.append(text), _leagues=lambda: list(League),
        _release_hold=lambda msg: released.append(msg), reset_feeds=lambda hard=False: feeds.append(hard) or True)
    dec.current = "g1"

    async def sleep(s):
        clock.t += s
    s = Supervisor(e, root=tmp_path, clock=clock, sleep=sleep, luma=None)
    return SimpleNamespace(e=e, s=s, clock=clock, stick=stick, control=control, dec=dec, game=game,
                           toasts=toasts, released=released, feeds=feeds, root=tmp_path)


def records(w, kind="fix"):
    return [r for r in sup.read_jsonl(w.root / "supervisor.jsonl") if r["kind"] == kind]


# --- helpers ---------------------------------------------------------------------------------

def test_limiter_gap_and_hourly_cap():
    c = FakeClock(0)
    lim = Limiter(c)
    assert lim.allow("k", 60, 2)
    lim.note("k")
    c.t = 30
    assert not lim.allow("k", 60, 2)          # inside the gap
    c.t = 61
    assert lim.allow("k", 60, 2)
    lim.note("k")
    c.t = 200
    assert not lim.allow("k", 60, 2)          # two this hour already
    c.t = 3700
    assert lim.allow("k", 60, 2)


def test_frames_verdict():
    assert frames_verdict(b"", b"x") is None
    # Undecodable pixels: tiny PNGs are DRM-black players (never "still"); big ones are unknown.
    assert frames_verdict(b"same", b"same") == "black"
    assert frames_verdict(b"x" * 50_000, b"x" * 50_000) is None
    assert frames_verdict(b"x" * 50_000, b"y" * 50_000) is None
    page = np.full((4, 4), 120.0)
    luma = {b"p1": page, b"p2": page + 0.1, b"v": page + 40, b"k": np.zeros((4, 4))}.get
    assert frames_verdict(b"p1", b"p2", luma) == "still"
    assert frames_verdict(b"p1", b"v", luma) == "moving"
    assert frames_verdict(b"k", b"k", luma) == "black"


def test_night_window_and_failure_causes():
    late = datetime(2026, 11, 3, 1, 30, tzinfo=sup.ET).timestamp()
    assert night_of(late) == date(2026, 11, 2)
    t0, t1 = night_window(date(2026, 11, 2))
    assert t0 <= late < t1 and t1 - t0 == 86400
    assert sup.failure_cause("switch timed out after 150s") == "timed out"
    assert sup.failure_cause("Peacock: signed out on this stick") == "signed out"
    assert sup.failure_cause("weird") == "other"


# --- screen_not_game ---------------------------------------------------------------------------

async def test_home_screen_restored_with_a_deep_link_after_two_checks(world):
    w = world
    w.stick.fg = "com.amazon.tv.launcher/.ui.HomeActivity"
    await w.s.rule_screen(w.clock())
    assert w.control.restored == []                       # first sighting: only noted
    w.clock.t += 10
    await w.s.rule_screen(w.clock())
    assert w.control.restored == []                       # not long enough yet
    w.clock.t += 15
    await w.s.rule_screen(w.clock())
    assert w.control.restored == [("youtubetv:espn", True)]
    assert w.stick.keys == []                             # the supervisor itself never pressed a key
    assert records(w)[-1]["rule"] == "screen_not_game" and records(w)[-1]["ok"]


async def test_app_page_still_frames_resumed_without_relink(world):
    w = world
    w.stick.fg = f"{YT}/.BrowseActivity"
    w.stick.pb = {"state": "paused", "read_ok": True}
    w.stick.caps = [b"same"]
    await w.s.rule_screen(w.clock())
    w.clock.t += 25
    await w.s.rule_screen(w.clock())
    assert w.control.restored == [("youtubetv:espn", False)]


@pytest.mark.parametrize("state,caps,fg", [
    ("playing", [b"same"], f"{YT}/.BrowseActivity"),          # the player says it's playing
    ("paused", [b"a", b"b"], f"{YT}/.PlayerActivity"),        # the picture is moving
    ("paused", [b"same"], "com.netflix.ninja/.MainActivity"),  # someone else's app: not ours to fix
])
async def test_screen_left_alone_when_not_clearly_broken(world, state, caps, fg):
    w = world
    w.stick.fg, w.stick.pb, w.stick.caps = fg, {"state": state, "read_ok": True}, caps
    for _ in range(4):
        await w.s.rule_screen(w.clock())
        w.clock.t += 25
    assert w.control.restored == [] and w.stick.keys == []


@pytest.mark.parametrize("block", ["pending", "busy", "input", "dry", "watchdog", "game_over"])
async def test_screen_never_touched_while_something_else_owns_it(world, block):
    w = world
    w.stick.fg = "com.amazon.tv.launcher/.ui.HomeActivity"
    if block == "pending":
        w.e._pending_decision = object()
    elif block == "busy":
        w.control.busy = True
    elif block == "input":
        w.control.input_unknown = True
    elif block == "dry":
        w.e.cfg.update({"mode": {"dry_run": True}}, record=False)
    elif block == "watchdog":
        w.control._visible_fix_at = w.clock() + 1000
    elif block == "game_over":
        w.game.phase = Phase.POST
    for _ in range(3):
        await w.s.rule_screen(w.clock())
        w.clock.t += 25
    assert w.control.restored == []
    if block != "game_over":
        assert w.stick.calls == []                        # not even a read


async def test_failed_restores_count_as_failures_and_are_rate_limited(world):
    w = world
    w.stick.fg = "com.amazon.tv.launcher/.ui.HomeActivity"
    w.control.restore_result = None
    for _ in range(12):
        await w.s.rule_screen(w.clock())
        w.clock.t += 25
    n = len(w.control.restored)
    assert 1 <= n <= 4                                    # once per 90 s at most
    assert sum(1 for _, t, c in w.s.failures if c == "screen restore failed") == n


# --- stuck_switch ------------------------------------------------------------------------------

async def test_stuck_switch_is_cancelled_and_reported_failed(world):
    w = world
    outs = []
    d = Decision(w.clock(), "switch", "g1", "x", State.WATCHING)
    w.e._pending_decision, w.e._pending_since = d, w.clock()
    w.e._pending_done = lambda out: (outs.append(out), setattr(w.e, "_pending_decision", None))
    w.clock.t += 100
    await w.s.rule_stuck_switch(w.clock())
    assert not outs and w.control.cancelled == 0
    w.clock.t += 90                                      # 190 s > 150 + 30
    await w.s.rule_stuck_switch(w.clock())
    assert w.control.cancelled == 1 and outs and not outs[0].ok
    assert "timed out" in outs[0].detail and w.e._pending_decision is None


async def test_stuck_controller_task_without_a_decision_is_cancelled(world):
    w = world
    w.control._task = SimpleNamespace(done=lambda: False)
    await w.s.rule_stuck_switch(w.clock())
    w.clock.t += 181
    await w.s.rule_stuck_switch(w.clock())
    assert w.control.cancelled == 1


# --- idle_with_live ----------------------------------------------------------------------------

async def test_idle_with_live_games_clears_back_offs_then_refreshes_feeds(world):
    w = world
    w.dec.current = None
    w.dec.start_retry_at, w.dec.start_fails = w.clock() + 9999, 4
    w.e._fail_cool = {"youtubetv:espn2": w.clock() + 50, "youtubetv:fs1": w.clock() + 50}
    w.s.muted_targets["youtubetv:fs1"] = w.clock() + 9999
    await w.s.rule_idle(w.clock())
    w.clock.t += 181
    await w.s.rule_idle(w.clock())
    assert w.dec.start_retry_at == 0 and w.dec.start_fails == 0
    assert w.e._fail_cool == {"youtubetv:fs1": pytest.approx(w.clock() - 181 + 50)}   # night mute kept
    assert w.control._failed == {} and w.feeds == []
    w.clock.t += 181
    await w.s.rule_idle(w.clock())
    assert w.feeds == [False]


@pytest.mark.parametrize("why", ["paused", "safe", "no_target", "stale", "my_team"])
async def test_idle_rule_respects_pauses_and_untunable_games(world, why):
    w = world
    w.dec.current = None
    if why == "paused":
        w.dec.user.paused = True
    elif why == "safe":
        w.dec.safe_mode = True
    elif why == "no_target":
        w.e.where = {}
    elif why == "stale":
        w.e.merger.stale.add("g1")
    else:
        w.e.last_decision = Decision(w.clock(), "stay", None, "waiting for your team (tips in 4 min)", State.IDLE)
    await w.s.rule_idle(w.clock())
    w.clock.t += 400
    await w.s.rule_idle(w.clock())
    assert records(w) == []


# --- repeat_failures ---------------------------------------------------------------------------

def fail_row(ts, target, detail="playback never started"):
    return {"ts": ts, "target": target, "ok": 0, "dry_run": 0, "detail": json.dumps({"detail": detail})}


BLACKOUT = "blacked out here (local team)"


async def test_two_failures_back_off_three_mute_for_the_night(world):
    w = world
    st = w.e.store
    st.rows += [fail_row(w.clock() + 1, "prime:x", BLACKOUT), fail_row(w.clock() + 2, "prime:x", BLACKOUT)]
    w.clock.t += 5
    await w.s.rule_failures(w.clock())
    assert w.e._fail_cool["prime:x"] == pytest.approx(w.clock() + 600)
    assert not w.s.muted("prime:x")
    st.rows.append(fail_row(w.clock() + 1, "prime:x", BLACKOUT))
    w.clock.t += 5
    await w.s.rule_failures(w.clock())
    assert w.s.muted("prime:x") and w.e._fail_cool["prime:x"] == sup.night_end(w.clock())
    assert any("prime:x" in t for t in w.toasts)


async def test_failures_that_are_not_the_targets_fault_are_ignored(world):
    w = world
    w.e.store.rows += [fail_row(w.clock() + i, "prime:x", "no safe moment to cut") for i in range(1, 6)]
    w.e.store.rows += [{**fail_row(w.clock() + 9, "prime:x"), "dry_run": 1}]
    w.clock.t += 10
    await w.s.rule_failures(w.clock())
    assert not w.s.failures and not w.s.muted("prime:x")


async def test_app_muted_after_two_of_its_targets_are(world):
    w = world
    w.e.where["g2"] = where("prime:y")
    for t in ("prime:a", "prime:b"):
        w.s.muted_targets[t] = w.clock() + 9999
    await w.s.rule_failures(w.clock())
    assert w.s.muted_apps["prime"] > w.clock()
    assert w.s.muted("prime:anything") and w.e._fail_cool["prime:y"] > w.clock()


def test_route_picks_another_source_for_a_muted_target(world):
    from redzone.discovery.providers import Source
    w = world
    w.s.muted_targets["youtubetv:espn"] = w.clock() + 9999
    alt = Source("espn", "espn:abc", "ESPN app", True)
    out = w.s.route("g1", where("youtubetv:espn", alternates=[alt]))
    assert out.target == "espn:abc" and out.tunable
    bare = where("youtubetv:espn")
    assert w.s.route("g1", bare) is bare                 # nothing else: the cool-down keeps it off
    ok = where("youtubetv:fs1")
    assert w.s.route("g1", ok) is ok


# --- safe_mode -------------------------------------------------------------------------------

async def test_safe_mode_cleared_after_three_minutes_and_culprit_muted(world):
    w = world
    w.dec.safe_mode, w.dec.safe_since = True, w.clock()
    w.s.note_failure("prime:x", "timed out", w.clock())
    w.s.note_failure("prime:x", "timed out", w.clock())
    await w.s.rule_safe_mode(w.clock())
    assert w.dec.safe_mode
    w.clock.t += 181
    await w.s.rule_safe_mode(w.clock())
    assert not w.dec.safe_mode and w.s.muted("prime:x")


async def test_safe_mode_that_keeps_coming_back_is_left_on(world):
    w = world
    for _ in range(4):
        w.dec.safe_mode = True
        await w.s.rule_safe_mode(w.clock())
        w.clock.t += 700                                  # past both the 3 min wait and the 10 min gap
        await w.s.rule_safe_mode(w.clock())
    assert w.dec.safe_mode                                # the 4th in ~an hour stays on
    assert any("needs a look" in r["what"] for r in records(w, "observe"))


# --- stale_feeds -----------------------------------------------------------------------------

async def test_stale_feeds_soft_then_hard_reset(world):
    w = world
    w.e.merger.stale.add("g1")
    await w.s.rule_feeds(w.clock())
    w.clock.t += 121
    await w.s.rule_feeds(w.clock())
    assert w.feeds == [False]
    w.clock.t += 300
    await w.s.rule_feeds(w.clock())
    assert w.feeds == [False, True]
    w.e.merger.stale.clear()
    w.clock.t += 1000
    await w.s.rule_feeds(w.clock())
    assert w.feeds == [False, True]


# --- adb -------------------------------------------------------------------------------------

@pytest.fixture
def fake_adb(monkeypatch):
    calls = []

    async def _adb(*args, timeout=8.0):
        calls.append(args)
        return SimpleNamespace(ok=True, out="", took=0.0)

    async def available():
        return True
    from redzone.tuners import adb
    monkeypatch.setattr(adb, "_adb", _adb)
    monkeypatch.setattr(adb, "adb_available", available)
    return calls


async def test_offline_stick_gets_transport_dropped_and_reconnected(world, fake_adb):
    w = world
    w.stick.state, w.stick.packages = "offline", {}
    await w.s.rule_adb(w.clock())
    w.clock.t += 121
    await w.s.rule_adb(w.clock())
    assert ("disconnect", w.stick.host) in fake_adb
    assert w.stick.calls[:3] == ["drop_session", "connect", "identify"] and w.stick._next_retry == 0
    assert w.control.forgot == [w.stick] and w.stick.keys == []


async def test_unauthorized_stick_is_reported_not_hammered(world, fake_adb):
    w = world
    w.stick.state = "unauthorized"
    await w.s.rule_adb(w.clock())
    w.clock.t += 500
    await w.s.rule_adb(w.clock())
    assert fake_adb == [] and any("approved on the TV" in r["what"] for r in records(w, "observe"))


async def test_adb_server_restarted_when_adb_is_down(world, fake_adb):
    w = world
    w.control.adb_ok = False
    await w.s.rule_adb(w.clock())
    w.clock.t += 61
    await w.s.rule_adb(w.clock())
    assert ("start-server",) in fake_adb and w.control.adb_ok


async def test_adb_rule_does_nothing_in_dry_run(world, fake_adb):
    w = world
    w.e.cfg.update({"mode": {"dry_run": True}}, record=False)
    w.stick.state = "offline"
    await w.s.rule_adb(w.clock())
    w.clock.t += 500
    await w.s.rule_adb(w.clock())
    assert fake_adb == []


# --- delay_doubt -----------------------------------------------------------------------------

async def test_delay_doubt_that_never_tightens_falls_back_to_safe_defaults(world):
    w = world
    d = w.e.delays.get("youtubetv:espn")
    d.mu, d.sigma = 61.0, 32.0                           # stalls kept widening it
    await w.s.rule_delay_doubt(w.clock())
    w.clock.t += 600
    await w.s.rule_delay_doubt(w.clock())
    assert w.e.delays.get("youtubetv:espn").sigma == 32.0
    w.clock.t += 301
    await w.s.rule_delay_doubt(w.clock())
    nd = w.e.delays.get("youtubetv:espn")
    assert (nd.mu, nd.sigma) == (45.0, 15.0)


async def test_narrow_delay_is_left_alone(world):
    w = world
    w.e.delays.get("youtubetv:espn")
    for _ in range(3):
        await w.s.rule_delay_doubt(w.clock())
        w.clock.t += 1000
    assert records(w) == []


# --- finished_hold ---------------------------------------------------------------------------

async def test_hold_on_a_finished_game_is_released(world):
    w = world
    w.e.hold = {"game_id": "g1", "auto_resume": False}
    w.game.phase = Phase.POST
    await w.s.rule_finished_hold(w.clock())
    w.clock.t += 181
    await w.s.rule_finished_hold(w.clock())
    assert w.released == [""]


async def test_lock_and_focus_on_a_finished_game_are_cleared(world):
    w = world
    u = w.dec.user
    u.locked_game, u.focus_game, u.focus_mode = "g1", "g1", FocusMode.FOCUS
    w.game.phase = Phase.POST
    await w.s.rule_finished_hold(w.clock())
    w.clock.t += 181
    await w.s.rule_finished_hold(w.clock())
    assert u.locked_game is None and u.focus_game is None and u.focus_mode == FocusMode.AUTOPILOT


async def test_hold_on_a_live_game_is_kept(world):
    w = world
    w.e.hold = {"game_id": "g1", "auto_resume": False}
    w.clock.t += 1000
    await w.s.rule_finished_hold(w.clock())
    w.clock.t += 1000
    await w.s.rule_finished_hold(w.clock())
    assert w.released == []


# --- log_growth ------------------------------------------------------------------------------

@pytest.mark.playbook("MAC-08")
async def test_logs_jsonl_imgcache_and_wal_kept_under_caps(world):
    w = world
    w.s.opt = lambda k: {"log_every_s": 600, "max_log_bytes": 1000, "keep_log_bytes": 100,
                         "max_jsonl_bytes": 1000, "keep_jsonl_lines": 5, "max_imgcache_bytes": 3000,
                         "keep_imgcache_bytes": 1500, "max_wal_bytes": 100}.get(k, sup.DEFAULTS.get(k))
    (w.root / "service-bootstrap.log").write_bytes(b"x" * 5000)
    (w.root / "autonomy.jsonl").write_text("".join(json.dumps({"n": i}) + "\n" for i in range(200)))
    cache = w.root / "imgcache"
    cache.mkdir()
    for i in range(6):
        p = cache / f"{i}.png"
        p.write_bytes(b"i" * 1000)
        import os
        os.utime(p, (1000 + i, 1000 + i))
    (w.root / "redzone.db-wal").write_bytes(b"w" * 500)
    await w.s.rule_logs(w.clock())
    assert (w.root / "service-bootstrap.log").stat().st_size == 0
    assert (w.root / "service-bootstrap.log.1").stat().st_size == 100
    assert len((w.root / "autonomy.jsonl").read_text().splitlines()) == 5
    assert sorted(p.name for p in cache.iterdir()) == ["5.png"]   # oldest removed first, down to the 1500-byte cap
    assert not (cache / "0.png").exists()
    assert w.e.store.checkpoints == 1
    await w.s.rule_logs(w.clock() + 1)                   # not again before log_every_s
    assert w.e.store.checkpoints == 1


# --- moments + report ------------------------------------------------------------------------

async def test_key_moments_judged_seen_and_missed(world):
    w = world
    dec = w.dec
    tl = dec.timeline("g1", League.NFL)
    seen_t, missed_t = w.clock() - 300, w.clock() - 200
    tl.events.append(Event(seen_t, "play", "Touchdown pass", big=True, pid="p1", play_kind="td"))
    tl.events.append(Event(missed_t, "play", "Interception", big=True, pid="p2", play_kind="turnover"))
    tl.events.append(Event(missed_t, "play", "Run for 2", big=False, pid="p3"))
    mu = w.e.delays.get("youtubetv:espn").mu
    dec._screen_log.append((seen_t - 100, "g1"))
    dec._screen_log.append((missed_t + mu - 30, "g2"))   # we were elsewhere when p2 aired
    await w.s.track_moments(w.clock())
    await w.s.track_moments(w.clock())                    # judged once
    ms = sup.read_jsonl(w.root / "supervisor_moments.jsonl")
    assert [(m["label"], m["seen"]) for m in ms] == [("Touchdown pass", True), ("Interception", False)]


def test_report_has_every_section(tmp_path):
    store = Store(tmp_path / "rz.db")
    night = date(2026, 11, 2)
    t0, _ = night_window(night)
    store.switch(ts=t0 + 50000, to_game="g1", target="youtubetv:espn", kind="better", ok=True,
                 detail={"took": 3.2, "detail": "ok"})
    store.switch(ts=t0 + 50100, to_game="g2", target="prime:x", kind="chase", ok=False,
                 detail={"took": 150, "detail": "switch timed out after 150s"})
    store.switch(ts=t0 - 5000, to_game="old", target="prime:x", kind="better", ok=False, detail={})   # other night
    store.calibration("youtubetv:espn", 44.0, 44.0, 4.0, kind="screen")
    sup.append_jsonl(tmp_path / "supervisor.jsonl", {"ts": t0 + 50200, "kind": "fix", "rule": "screen_not_game",
                                                     "action": "restore", "ok": True, "detail": "relink"})
    sup.append_jsonl(tmp_path / "supervisor_moments.jsonl", {"ts": t0 + 50300, "matchup": "DAL @ WSH",
                                                             "label": "TD", "seen": False})
    text, summary = build_report(night, store, tmp_path)
    for h in ("## Switches", "## Failures by cause", "## Key moments", "## Delays measured",
              "## What it fixed on its own", "## Needs you"):
        assert h in text
    assert "2 switches (1 failed)" in summary and "timed out" in text and "screen_not_game: 1" in text
    assert "Seen live: 0 of 1" in text


@pytest.mark.playbook("SLATE-02")
async def test_nightly_report_written_once_and_pushed_only_when_configured(world):
    w = world
    pushed = []
    w.s.push = lambda title, body: pushed.append(title)
    w.clock.t = night_window(date(2026, 11, 3))[0] + 600       # 6:10 AM on the 3rd: the 2nd's night is over
    await w.s.maybe_report(w.clock())
    path = w.root / "reports" / "2026-11-02.md"
    assert path.exists() and pushed == []                    # no topic: no push
    path.unlink()
    w.s._report_checked = None
    w.s.opt = lambda k: "my-secret-topic" if k == "ntfy_topic" else sup.DEFAULTS.get(k)
    await w.s.maybe_report(w.clock())
    assert path.exists() and pushed == ["RedZone night of 2026-11-02"]
    await w.s.maybe_report(w.clock())
    assert pushed == ["RedZone night of 2026-11-02"]


# --- the loop itself ---------------------------------------------------------------------------

async def test_step_survives_a_broken_rule_and_skips_recorded_sessions(world, monkeypatch):
    w = world

    async def boom(now):
        raise RuntimeError("x")
    monkeypatch.setattr(w.s, "rule_screen", boom)
    await w.s.step()                                        # no exception
    assert w.s.last_step == w.clock()
    w.e.recorded = SimpleNamespace(active=True)
    w.clock.t += 50
    await w.s.step()
    assert w.s.last_step == w.clock() - 50


# --- engine + controller hooks ------------------------------------------------------------------

def test_engine_has_a_supervisor_in_its_snapshot_and_routes_through_it(tmp_path):
    from redzone.engine import Engine
    e = Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), Store(tmp_path / "rz.db"))
    assert e.supervisor.root == tmp_path
    assert "supervisor" in e.snapshot()["health"]
    e.fetcher._backoff["site.api.espn.com"] = object()
    assert e.reset_feeds(hard=False)[0] and not e.fetcher._backoff


async def test_engine_hard_feed_reset_swaps_the_http_client(tmp_path):
    from redzone.engine import Engine
    e = Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), Store(tmp_path / "rz.db"))
    old = e.fetcher._client
    assert e.reset_feeds(hard=True)[0] and e.fetcher._client is not old
    await asyncio.sleep(0)
    await e.fetcher.close()


async def test_controller_restore_visible_relinks_or_resumes(tmp_path, cfg, monkeypatch):
    from redzone import control as ctl
    from redzone.channels import ChannelMap
    from redzone.tuners.adb import Stick
    from redzone.tuners.tuners import TuneResult
    cfg.update({"mode": {"dry_run": False}}, record=False)
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    s = Stick("10.0.0.9:5555", os="Fire OS 8")
    s.state = "connected"
    c.sticks = [s]
    tuned, resumed = [], []

    async def tune(stick, target, may_cut=None):
        tuned.append(target)
        return TuneResult(True, target, 1.0, [], "")

    async def resume(stick, target, first_wait_s):
        resumed.append(target)
        return "media_play"
    monkeypatch.setattr(c, "_tune", tune)
    monkeypatch.setattr(c, "_resume_playback", resume)
    assert await c.restore_visible("youtubetv:espn", relink=True) == "relink"
    assert tuned == ["youtubetv:espn"] and c.on_target == "youtubetv:espn"
    assert await c.restore_visible("youtubetv:fs1") == "media_play" and resumed == ["youtubetv:fs1"]
    c.input_unknown = True
    assert await c.restore_visible("youtubetv:espn", relink=True) is None and len(tuned) == 1


async def test_timeouts_only_back_off_never_mute_for_the_night(world):
    # Timeouts / streams that didn't start can be Wi-Fi or the stick: back off, never mute (Nov 2 review).
    w = world
    w.e.store.rows += [fail_row(w.clock() + i, "prime:x", "switch timed out after 75s") for i in range(1, 8)]
    w.clock.t += 10
    await w.s.rule_failures(w.clock())
    assert not w.s.muted("prime:x") and not w.s.muted_apps
    assert w.e._fail_cool["prime:x"] == pytest.approx(w.clock() + 600)


async def test_adb_rule_waits_while_away_from_home(world, fake_adb):
    """Away from home (Mac at school) a stick that can't answer isn't broken: no reconnect churn,
    one plain observation, and the 2-minute clock starts fresh once back home."""
    w = world
    w.e.network = SimpleNamespace(state=SimpleNamespace(away=True, routes_broken=False,
                                                        message="Away from home: this Mac is on 10.23.4.5"))
    w.stick.state = "offline"
    await w.s.rule_adb(w.clock())
    w.clock.t += 500
    await w.s.rule_adb(w.clock())
    assert fake_adb == [] and w.stick.calls == []
    assert [r["what"] for r in records(w, "observe") if r["rule"] == "network"] == \
        ["Away from home: this Mac is on 10.23.4.5"]
    w.e.network.state.away = False           # home again: a fresh 2 minutes before reconnecting
    await w.s.rule_adb(w.clock())
    assert fake_adb == []
    w.clock.t += 121
    await w.s.rule_adb(w.clock())
    assert ("disconnect", w.stick.host) in fake_adb


async def test_adb_rule_does_not_reconnect_when_tailscale_steals_routes(world, fake_adb):
    w = world
    w.e.network = SimpleNamespace(state=SimpleNamespace(away=False, routes_broken=True, message="Tailscale is ..."))
    w.stick.state = "offline"
    await w.s.rule_adb(w.clock())
    w.clock.t += 500
    await w.s.rule_adb(w.clock())
    assert fake_adb == []


# --- platform safety (P-03, P-09, P-11, P-02, P-16) -----------------------------------------------

async def test_p03_heartbeat_age_is_in_the_status_even_when_the_supervisor_is_off(world):
    w = world
    assert w.s.status()["heartbeat_age_s"] is None            # never stepped: unknown, not a hang
    w.e.cfg.update({"supervisor": {"enabled": False}}, record=False)
    await w.s.step()
    w.clock.t += 7
    st = w.s.status()
    assert st["heartbeat_age_s"] == 7.0 and st["switch_in_flight"] is False
    w.e._pending_decision = object()
    assert w.s.status()["switch_in_flight"] is True


async def test_p09_the_app_does_not_drop_a_transport_under_a_switch_in_flight(world, fake_adb):
    w = world
    w.stick.state, w.stick.packages = "offline", {}
    w.e._pending_decision = object()
    await w.s.rule_adb(w.clock())
    w.clock.t += 121
    await w.s.rule_adb(w.clock())
    assert ("disconnect", w.stick.host) not in fake_adb and "drop_session" not in w.stick.calls
    w.e._pending_decision = None
    await w.s.rule_adb(w.clock())
    assert ("disconnect", w.stick.host) in fake_adb


async def test_p11_paused_with_a_live_game_alerts_after_ten_minutes_once(world):
    w = world
    pushed = []

    async def send_push(title, body, priority="default", tags="tv"):
        pushed.append(title)
        return True
    w.s.send_push = send_push
    w.dec.user.paused = True
    await w.s.rule_paused_alert(w.clock())
    w.clock.t += 599
    await w.s.rule_paused_alert(w.clock())
    assert pushed == [] and not w.toasts
    w.clock.t += 2
    await w.s.rule_paused_alert(w.clock())
    assert pushed == ["RedZone: paused"] and "paused 10 min" in w.toasts[-1]
    w.clock.t += 60
    await w.s.rule_paused_alert(w.clock())
    assert pushed == ["RedZone: paused"]                      # once, not every 10 s
    w.dec.user.paused = False
    await w.s.rule_paused_alert(w.clock())
    assert w.s._paused_alerted == 0.0                         # resumed: re-armed


async def test_p11_paused_with_no_live_game_is_quiet(world):
    w = world
    w.game.phase = Phase.POST
    w.dec.user.paused = True
    await w.s.rule_paused_alert(w.clock())
    w.clock.t += 3000
    await w.s.rule_paused_alert(w.clock())
    assert not w.toasts


async def test_p02_a_stale_or_missing_watchdog_heartbeat_with_a_live_game_pushes_once(world):
    import os
    w = world
    pushed = []

    async def send_push(title, body, priority="default", tags="tv"):
        pushed.append(body)
        return True
    w.s.send_push = send_push
    await w.s.rule_watchdog_beat(w.clock())                   # no heartbeat file at all
    w.clock.t += 61
    await w.s.rule_watchdog_beat(w.clock())
    assert len(pushed) == 1 and "never run" in pushed[0]
    w.clock.t += 120
    await w.s.rule_watchdog_beat(w.clock())
    assert len(pushed) == 1                                   # not again within the repeat window
    beat = w.root / "agent.heartbeat"
    beat.write_text("x")
    os.utime(beat, (w.clock(), w.clock()))                    # fresh: the alert re-arms
    w.clock.t += 30
    await w.s.rule_watchdog_beat(w.clock())
    assert w.s._wd_alerted == 0.0 and w.s.watchdog_beat_age() == 30.0
    w.clock.t += 400                                          # went quiet again
    await w.s.rule_watchdog_beat(w.clock())
    w.clock.t += 61
    await w.s.rule_watchdog_beat(w.clock())
    assert len(pushed) == 2 and "not checked in" in pushed[1]


async def test_p02_no_live_game_no_watchdog_alert(world):
    w = world
    w.game.phase = Phase.POST
    w.clock.t += 5000
    await w.s.rule_watchdog_beat(w.clock())
    w.clock.t += 100
    await w.s.rule_watchdog_beat(w.clock())
    assert not w.toasts


def test_p16_trim_jsonl_keeps_rows_another_process_appended_meanwhile(tmp_path, monkeypatch):
    """Repro: trim_jsonl read, then replace()d the file; a row the watchdog or deploy.py appended in between was lost."""
    p = tmp_path / "agent.jsonl"
    p.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(10)))
    real = type(p).write_bytes

    def write_then_append(self, data):
        n = real(self, data)
        if self.name.endswith(".tmp"):
            with p.open("ab") as f:
                f.write(b'{"n": 99}\n')                        # lands after the read, before the replace
        return n
    monkeypatch.setattr(type(p), "write_bytes", write_then_append)
    sup.trim_jsonl(p, 3)
    rows = [json.loads(x)["n"] for x in p.read_text().splitlines()]
    assert rows == [7, 8, 9, 99]
