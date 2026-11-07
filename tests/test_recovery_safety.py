"""Regression checks for real-device failures that offline scorecards cannot simulate."""
import asyncio
from types import SimpleNamespace

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.decider.decider import Candidate, Decider, Decision, Missed, State
from redzone.decider.screen import DelayBook
from redzone.engine import Engine
from redzone.models import League
from redzone.scoring.engine import Scorer, Personal
from redzone.store import Store
from redzone.tuners import adb
from conftest import nfl
from test_hot_standby import FakeStick, FakeTuner


async def test_partial_adb_write_is_unknown_and_never_safe_to_resend():
    class Pipe:
        sent = []
        def write(self, data):
            self.sent.append(data)
        async def drain(self):
            raise BrokenPipeError('after delivery')
    shell = adb.AdbShell('fake')
    pipe = Pipe()
    killed = []
    shell.proc = SimpleNamespace(returncode=None, stdin=pipe, kill=lambda: killed.append(True))
    status, reply = await shell._exchange('input keyevent KEYCODE_DPAD_CENTER', 1)
    assert status == adb.LOST and not reply.ok
    assert len(pipe.sent) == 1 and killed and shell.proc is None


async def test_cancel_during_adb_drain_closes_shell():
    started = asyncio.Event()
    class Pipe:
        def write(self, data):
            pass
        async def drain(self):
            started.set()
            await asyncio.Future()
    shell = adb.AdbShell('fake')
    killed = []
    shell.proc = SimpleNamespace(returncode=None, stdin=Pipe(), kill=lambda: killed.append(True))
    task = asyncio.create_task(shell._exchange('input keyevent KEYCODE_DPAD_CENTER', 1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed and shell.proc is None


async def test_single_stick_waits_for_actual_safe_gap(cfg, tmp_path, monkeypatch):
    cfg.update({'mode': {'dry_run': False}})
    channels = ChannelMap(tmp_path / 'channels.json')
    channels.codes['espn'] = 'AAAAAAAAAAA'
    c = ctl.Controller(cfg, channels)
    c.sticks = [FakeStick('a')]
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, 'youtubetv', tuner)
    monkeypatch.setattr(ctl, 'MAX_SAFE_WAIT_S', 0.01)
    monkeypatch.setattr(ctl, 'SAFE_POLL_S', 0.005)
    out = await c.execute('youtubetv:espn', may_cut=lambda: False)
    assert not out.ok and tuner.calls == []


async def test_unknown_hidden_power_never_preloads(cfg, tmp_path, monkeypatch):
    cfg.update({'mode': {'dry_run': False}})
    ch = ChannelMap(tmp_path / 'channels.json')
    ch.codes['espn'] = 'AAAAAAAAAAA'
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick('a'), FakeStick('b', power='Unknown')]
    c.hdmi = {'a': 'HDMI1', 'b': 'HDMI2'}
    c.tv.st_device = 'explicit-test-display'
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, 'youtubetv', tuner)
    assert c.preload('youtubetv:espn')
    await c._preload_task
    assert not tuner.calls



@pytest.fixture(autouse=True)
def _replay_keys(request):
    """These cases cover the KEY rewind mechanics (seek_key presses, wake press, drift re-seek). YouTube TV catch-up
    now seeks by the overlay scrub (replay.yttv_seek: scrub, Nick Nov 7); tests/test_replay_scrub.py covers that."""
    if "cfg" in request.fixturenames:
        request.getfixturevalue("cfg").update({"replay": {"yttv_seek": "keys"}})

def test_unconfirmed_plan_does_not_become_watched_game(cfg, clock):
    dec = Decider(cfg, DelayBook(cfg.get("delay.default"), cfg.get("delay.sigma_default")), clock=clock)
    game = nfl()
    scored = Scorer(cfg).score(game, Personal())
    cands = {'1': Candidate(scored, 'youtubetv:espn')}
    d = dec.decide(cands, commit=False)
    assert d.action == 'switch'
    assert dec.current is None and not dec.history and not dec._screen_log
    dec.reject(d)
    assert dec.current is None
    clock.t += 3   # a rejected start pick is retried after a short backoff (test_restart_start.py)
    d = dec.decide(cands, commit=False)
    assert d.action == 'switch'
    clock.t += 12
    dec.confirm(d, cands)
    assert dec.current == '1' and dec.since == clock.t
    assert dec.on_screen_at(clock.t - 1) is None
    assert dec.on_screen_at(clock.t) == '1'


def test_unconfirmed_replay_stays_in_queue(cfg, clock, monkeypatch):
    dec = Decider(cfg, DelayBook(cfg.get("delay.default"), cfg.get("delay.sigma_default")), clock=clock)
    m = Missed('1', 'play', 'touchdown', 'td', clock.t - 60, 75)
    dec.queue.append(m)
    monkeypatch.setattr(dec, '_scan_missed', lambda *args: None)
    monkeypatch.setattr(dec, '_decide', lambda *args: Decision(clock.t, 'replay', '1', 'replay', State.REPLAY, replay=m))
    d = dec.decide({}, commit=False)
    dec.reject(d)
    assert m in dec.queue and not m.replayed and dec.replaying is None


async def test_failed_live_tune_keeps_current_and_enters_recovery(cfg, tmp_path):
    cfg.update({'mode': {'dry_run': False}})
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    engine.decider.current = 'old'
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    d = Decision(1, 'switch', 'new', 'better game', State.WATCHING)
    engine._act(d, 'old', {})
    assert engine.decider.current == 'old' and engine._pending_decision is d
    callbacks[0](ctl.Outcome(False, None, 1, 'launch failed', False))
    assert engine.decider.current == 'old'
    # One app hiccup is an error, not safe mode: the 3-in-5-minutes rule decides.
    assert not engine.decider.safe_mode and engine._pending_decision is None
    for _ in range(2):
        engine._act(d, 'old', {})
        callbacks[-1](ctl.Outcome(False, None, 1, 'launch failed', False))
    assert engine.decider.safe_mode
    await engine.close()


async def test_failed_switch_cools_the_target_down(cfg, tmp_path):
    cfg.update({'mode': {'dry_run': False}})
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    engine.decider.current = 'old'
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    engine.decider.target_of = lambda gid: 'peacock:new'
    d = Decision(1, 'switch', 'new', 'better game', State.WATCHING)
    engine._act(d, 'old', {})
    callbacks[0](ctl.Outcome(False, 'peacock:new', 30, 'deep link failed', False, screen_unchanged=True))
    assert 'peacock:new' in engine._fail_cool
    # A closed safe window is not the target's fault: no cooldown.
    engine.decider.target_of = lambda gid: 'espn:x'
    engine._act(d, 'old', {})
    callbacks[1](ctl.Outcome(False, 'espn:x', 1, 'no safe moment to cut — kept the current game', False,
                             screen_unchanged=True))
    assert 'espn:x' not in engine._fail_cool
    await engine.close()


async def test_no_stick_is_not_a_switch_nor_the_targets_fault(cfg, tmp_path):
    """[Nov 7 overseer] Every stick offline in live mode: the start is not confirmed on screen, the target
    isn't cooled down, it never counts toward safe mode, and the switch row says ok=False."""
    cfg.update({'mode': {'dry_run': False}})
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    engine.decider.target_of = lambda gid: 'youtubetv:espn'
    rows = []
    engine.store.switch = lambda **kw: rows.append(kw)
    d = Decision(1, 'switch', 'new', 'first pick', State.WATCHING, kind='start')
    for _ in range(3):
        engine._act(d, None, {})
        callbacks[-1](ctl.Outcome(False, 'youtubetv:espn', 0.0, ctl.NO_STICK, False, screen_unchanged=True))
    assert engine.decider.current is None and engine.decider.start_fails == 3
    assert not engine._fail_cool and not engine.decider.errors and not engine.decider.safe_mode
    assert [r['ok'] for r in rows] == [False, False, False]
    await engine.close()


async def test_cancelled_switch_is_not_an_error(cfg, tmp_path):
    cfg.update({'mode': {'dry_run': False}})
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    engine.decider.current = 'old'
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    seen = []
    engine.discovery.on_outcome = lambda *a, **k: seen.append(a)
    d = Decision(1, 'switch', 'new', 'better game', State.WATCHING)
    for _ in range(3):
        engine._act(d, 'old', {})
        callbacks[-1](ctl.Outcome(False, 'yt:new', 0.0, 'switch cancelled; playback not confirmed', False))
    assert not engine.decider.safe_mode and not engine.decider.errors
    assert not engine._fail_cool and not seen and engine._pending_decision is None
    await engine.close()


async def test_hung_switch_gives_up_after_deadline(cfg, tmp_path):
    cfg.update({'mode': {'dry_run': False}})
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    engine.decider.current = 'old'
    engine.control.submit = lambda *args, **kwargs: None   # never reports back
    cancelled = []
    engine.control.cancel_pending = lambda: cancelled.append(True)
    d = Decision(1, 'switch', 'new', 'better game', State.WATCHING)
    engine._act(d, 'old', {})
    engine._switch_deadline(engine._pending_since + 10)
    assert engine._pending_decision is d and not cancelled
    engine._switch_deadline(engine._pending_since + 151)
    assert engine._pending_decision is None and cancelled
    assert engine.decider.current == 'old' and len(engine.decider.errors) == 1
    await engine.close()


def test_safe_mode_auto_resumes_after_ten_quiet_minutes(cfg, clock):
    dec = Decider(cfg, DelayBook(cfg.get("delay.default"), cfg.get("delay.sigma_default")), clock=clock)
    dec.user.paused = True
    for _ in range(3):
        dec.note_error()
    assert dec.safe_mode
    clock.t += 300
    dec.note_error()   # more errors while safe don't restart the clock
    assert not dec.auto_resume(600)
    clock.t += 301
    assert dec.auto_resume(600) and not dec.safe_mode and not dec.errors
    assert dec.user.paused   # your own pause is never lifted by the timer


async def test_cancel_waiting_on_controller_lock_reports_completion(cfg, tmp_path):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / 'channels.json'))
    await c._lock.acquire()
    result = []
    c.submit('youtubetv:espn', on_done=result.append)
    await asyncio.sleep(0)
    c.cancel_pending()
    await asyncio.gather(c._task, return_exceptions=True)
    await asyncio.sleep(0)
    c._lock.release()
    assert len(result) == 1 and not result[0].ok


def test_mlb_fielding_change_is_bookkeeping_not_a_pitch():
    from redzone.sources import espn
    from redzone.decider.screen import _is_marker
    from redzone.lab import sportsgrade
    # Actual ESPN shape in ATL @ LAD, 2026-10-03, event 401908007.
    p = {'type': {'text': 'Play Result'},
         'alternativeType': {'type': 'lineup-change', 'text': 'Lineup Change'},
         'text': 'E. Hernández in left field.', 'scoringPlay': False}
    assert espn._kind(League.MLB, p) == 'lineup_change'
    assert _is_marker(espn._kind(League.MLB, p), League.MLB)
    assert sportsgrade._clean_cut(League.MLB, [100.0], [p], 98)[0]
    pitch = {'type': {'text': 'Play Result'}, 'text': 'Batter singles.', 'scoringPlay': False}
    assert not sportsgrade._clean_cut(League.MLB, [100.0], [pitch], 98)[0]


async def test_unknown_screen_timing_is_not_a_safe_cut(cfg, tmp_path):
    e = Engine(cfg, Store(tmp_path / 'test.db'))
    assert e.safe_to_leave(None)
    assert not e.safe_to_leave('game-with-no-timeline')
    await e.close()


async def test_dry_run_keepalive_does_not_contact_saved_sticks(cfg, tmp_path):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / 'channels.json'))
    c.adb_ok = True
    class NoConnect(FakeStick):
        async def connect(self):
            raise AssertionError('dry run contacted a stick')
    c.sticks = [NoConnect('saved-stick')]
    c.sticks[0].state = 'offline'
    await c.keepalive()


async def test_producer_failure_closes_every_service():
    from redzone.__main__ import run_services
    class Server:
        should_exit = False
        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0.001)
    class Producer:
        closed = False
        async def run(self):
            raise ValueError('unexpected producer failure')
        async def close(self):
            self.closed = True
    server, engine = Server(), Producer()
    with pytest.raises(ValueError, match='producer failure'):
        await run_services(server, engine, asyncio.Event())
    assert server.should_exit and engine.closed


def test_doctor_never_selects_a_discovered_tv_without_explicit_choice():
    from scripts.doctor import select_tv
    xbox = {'ip': '192.168.1.10', 'wifi_mac': 'aa'}
    sports = {'ip': '192.168.1.11', 'wifi_mac': 'bb'}
    assert select_tv([xbox], None, None) is None
    assert select_tv([xbox, sports], None, None) is None
    assert select_tv([xbox, sports], None, sports['ip']) == sports
    assert select_tv([xbox, sports], {'ip': '192.168.1.99', 'wifi_mac': 'bb'}, None) == sports
    assert select_tv([xbox], sports, None) is None
    assert select_tv([xbox, sports], xbox, '192.168.1.99') is None


def test_baseball_inning_break_never_borrows_football_minimum():
    from redzone.decider.screen import Timeline, Event, Delay, leave_gate
    tl = Timeline(League.MLB)
    tl.fresh_at = 138
    tl._lags.append(21)
    tl.events.append(Event(86, 'stop_start', 'end_period', spread=158))
    assert not leave_gate(tl, Delay(35, 5), 208, 10).safe


def test_missing_break_timestamp_uses_earliest_start_for_minimum():
    from redzone.decider.screen import Timeline, Event, Delay, leave_gate
    tl = Timeline(League.NFL)
    tl.fresh_at = 138
    tl._lags.append(21)
    tl.events.append(Event(86, 'stop_start', 'end_period', spread=158))
    assert not leave_gate(tl, Delay(35, 5), 208, 10).safe
    tl.events.clear()
    tl.events.append(Event(86, 'stop_start', 'end_period'))
    assert leave_gate(tl, Delay(35, 5), 208, 10).safe


async def test_failed_rewind_never_confirms_replay_or_flips(cfg, tmp_path, monkeypatch):
    cfg.update({'mode': {'dry_run': False}})
    channels = ChannelMap(tmp_path / 'channels.json')
    channels.codes['espn'] = 'AAAAAAAAAAA'
    c = ctl.Controller(cfg, channels)
    class FailedSeek(FakeStick):
        async def shell(self, cmd, timeout=8):
            if 'MEDIA_REWIND' in cmd:
                return adb.RunResult(False, 'connection lost', 0.01)
            return await super().shell(cmd, timeout)
    c.sticks = [FakeStick('a'), FailedSeek('b')]
    c.hdmi = {'a': 'HDMI1', 'b': 'HDMI2'}
    c.tv.st_device = 'explicit-test-display'
    monkeypatch.setitem(ctl.TUNERS, 'youtubetv', FakeTuner())
    out = await c.execute('youtubetv:espn', replay_back_s=60, replay_id='play')
    assert not out.ok and c.main.host == 'a' and not c.hidden_ready
    assert not any('onetouchplay' in cmd for cmd in c.sticks[1].log)


async def test_saved_current_target_is_not_playback_evidence(cfg, tmp_path, monkeypatch):
    cfg.update({'mode': {'dry_run': False}})
    channels = ChannelMap(tmp_path / 'channels.json')
    channels.codes['espn'] = 'AAAAAAAAAAA'
    c = ctl.Controller(cfg, channels)
    c.sticks = [FakeStick('a', state='paused')]
    c.on_target = 'youtubetv:espn'
    tuner = FakeTuner(ok=False)
    monkeypatch.setitem(ctl.TUNERS, 'youtubetv', tuner)
    out = await c.execute('youtubetv:espn')
    assert not out.ok and tuner.calls and c.on_target is None


async def test_uncertain_event_cannot_calibrate_precise_screen_delay(cfg, tmp_path, monkeypatch):
    from redzone.decider.screen import Event
    import time
    cfg.update({'mode': {'dry_run': False}})
    e = Engine(cfg, Store(tmp_path / 'test.db'))
    e.decider.current = '1'
    e.where['1'] = SimpleNamespace(target='youtubetv:espn')
    tl = e.decider.timeline('1', League.NFL)
    tl.events.append(Event(time.time()-35, 'play', 'TD', True, pid='td', spread=28))
    ok, _ = e._calibrate('td')
    assert not ok and e.delays.get('youtubetv:espn').calibrated_at is None
    await e.close()


async def test_enabling_tv_control_discards_simulated_screen(cfg, tmp_path):
    e = Engine(cfg, Store(tmp_path / 'test.db'))
    e.decider.current = 'simulated'
    e.decider._screen_log.append((1.0, 'simulated'))
    e.action('dry_run', value=False)
    assert e.decider.current is None and not e.decider._screen_log
    assert e.screen_evidence()['status'] == 'unknown'
    await e.close()


async def test_unchanged_screen_rejection_does_not_stop_autopilot(cfg, tmp_path, monkeypatch):
    import time
    cfg.update({'mode': {'dry_run': False}})
    e = Engine(cfg, Store(tmp_path / 'test.db'))
    e.decider.current = 'old'
    e.decider._screen_log.append((time.time()-20, 'old'))
    callbacks = []
    monkeypatch.setattr(e.control, 'submit', lambda target, back, done, **kw: callbacks.append(done))
    d = Decision(time.time(), 'switch', 'new', 'better', State.WATCHING)
    e._act(d, 'old', {})
    callbacks[0](ctl.Outcome(False, 'youtubetv:new', 1, 'window expired', False, screen_unchanged=True))
    assert not e.decider.safe_mode and e.decider.current == 'old'
    assert e.decider.on_screen_at(time.time()) == 'old'
    await e.close()


async def test_idle_snapshot_does_not_invent_a_channel_delay(cfg, tmp_path):
    e = Engine(cfg, Store(tmp_path / 'test.db'))
    before = dict(e.delays.book)
    for _ in range(2):
        s = e.snapshot()
        assert s['screen_evidence']['delay_s'] is None
        assert s['screen_evidence']['delay_margin_s'] is None
        assert s['delays'] == []
    assert e.delays.book == before
    await e.close()


async def test_broadcast_bug_does_not_count_toward_safe_mode(cfg, tmp_path):
    engine = Engine(cfg, Store(tmp_path / 'test.db'))
    ticks = []
    engine.decide = lambda: True
    def boom():
        raise RuntimeError('snapshot bug')
    engine.broadcast = boom
    async def tick(seconds):
        ticks.append(seconds)
        if len(ticks) >= 4:
            raise asyncio.CancelledError
    engine._next_tick = tick
    with pytest.raises(asyncio.CancelledError):
        await engine._decide_loop()
    assert not engine.decider.errors and not engine.decider.safe_mode
    await engine.close()
