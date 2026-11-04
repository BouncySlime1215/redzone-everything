"""hardening.mac (redzone/platform/wake.py): after the Mac sleeps or changes networks every
connection re-proves itself at once (fakes only):
- WakeWatch sees a sleep (wall clock ahead of monotonic) and a late tick, not a normal tick;
- recover() reconnects sticks, probes breakers, drops the TV socket, resyncs the input, restarts
  FastCast and clears HTTP backoffs, and never presses a key;
- the engine runs it only with hardening.mac on, on a wake or a home/away change (not from "unknown")."""
import pytest
from types import SimpleNamespace

from redzone.engine import Engine
from redzone.platform.breaker import Breaker
from redzone.platform.wake import WakeWatch, recover


class Clocks:
    def __init__(self):
        self.w, self.m = 1_000_000.0, 500.0


def watch(c):
    return WakeWatch(wall=lambda: c.w, mono=lambda: c.m)


def test_wakewatch_sees_sleep_and_stalls_not_normal_ticks():
    c = Clocks()
    ww = watch(c)
    assert ww.check() is None
    c.w += 10
    c.m += 10
    assert ww.check() is None                      # a normal tick
    c.w += 3600 + 10                               # slept an hour: monotonic stopped
    c.m += 10
    assert ww.check() == 3600
    c.w += 60
    c.m += 60                                      # the loop stalled for a minute
    assert ww.check() == 50
    c.w -= 120                                     # NTP pulled the clock back: not a sleep
    c.m += 10
    assert ww.check() is None


class FakeStick:
    def __init__(self, host):
        self.host, self.last_ok, self.keys = host, 999.0, []
        self.breaker = Breaker(host, clock=lambda: 100.0)
        self.breaker.hang()
        self.breaker.hang()


class FakeTV:
    def __init__(self):
        self.dropped = 0
        self.st_breaker = Breaker("st", clock=lambda: 100.0)
        self.st_breaker.hang()
        self.st_breaker.hang()

    def _drop_local(self):
        self.dropped += 1


def fake_engine():
    ctl = SimpleNamespace(sticks=[FakeStick("a"), FakeStick("b")], tv=FakeTV(), _cec_synced_at=5.0,
                          _verified_at=5.0, _tv_maintained_at=5.0)
    stopped = []
    e = SimpleNamespace(control=ctl, fastcast=object(), _stop_fastcast=lambda: stopped.append(1),
                        fetcher=SimpleNamespace(_backoff={"site.api.espn.com": object()}))
    return e, stopped


@pytest.mark.playbook("MAC-01")
def test_recover_resets_every_connection_and_presses_nothing():
    e, stopped = fake_engine()
    did = recover(e, "woke after 3600s")
    assert did == ["sticks", "tv", "resync", "fastcast", "http_backoff"]
    for s in e.control.sticks:
        assert s.last_ok == 0.0 and s.breaker.state == "half-open" and s.keys == []
    assert e.control.tv.dropped == 1 and e.control.tv.st_breaker.state == "half-open"
    assert e.control._cec_synced_at == e.control._verified_at == e.control._tv_maintained_at == 0.0
    assert stopped == [1] and e.fetcher._backoff == {}


def test_recover_survives_a_broken_step():
    e, stopped = fake_engine()
    e.control.tv = SimpleNamespace()               # no _drop_local
    did = recover(e, "network changed")
    assert "tv" not in did and "sticks" in did and "fastcast" in did


class FakeNet:
    def __init__(self, where="home"):
        self.state = SimpleNamespace(where=where)
        self.forced = 0

    async def refresh(self, force=False):
        self.forced += force


def engine_like(on, where="home"):
    e, _ = fake_engine()
    e.cfg = SimpleNamespace(get=lambda k, d=None: on if k == "hardening.mac" else d)
    e.network = FakeNet(where)
    c = Clocks()
    e.wake = watch(c)
    e._net_where = None
    return e, c


@pytest.mark.playbook("MAC-01")
async def test_engine_recovers_after_sleep_only_when_on():
    for on in (False, True):
        e, c = engine_like(on)
        assert await Engine._after_wake(e) is None
        c.w += 7200
        c.m += 10
        out = await Engine._after_wake(e)
        assert (out is not None) == on
        assert e.network.forced == (1 if on else 0)


@pytest.mark.playbook("NET-01")
async def test_engine_recovers_on_a_network_change_but_not_from_unknown():
    e, c = engine_like(True, where="unknown")
    assert await Engine._after_wake(e) is None
    e.network.state.where = "home"                 # first real answer at startup: not a change
    c.w += 10
    c.m += 10
    assert await Engine._after_wake(e) is None
    e.network.state.where = "away"                 # left the house
    c.w += 10
    c.m += 10
    assert await Engine._after_wake(e) is not None
    c.w += 10
    c.m += 10
    assert await Engine._after_wake(e) is None     # still away: nothing new


@pytest.mark.playbook("MAC-01")
def test_caffeinate_holds_the_mac_awake_only_while_games_are_live(tmp_path, monkeypatch):
    """MAC-01's first response: caffeinate -i -s while any game is live (the Mac never sleeps mid-game),
    once, and let it go when nothing is live or the app closes."""
    from redzone import engine as eng
    from redzone.config import Config
    from redzone.store import Store
    made = []

    class P:
        def __init__(self, cmd):
            self.cmd, self.done = cmd, False
            made.append(self)

        def poll(self):
            return 0 if self.done else None

        def terminate(self):
            self.done = True
    monkeypatch.setattr(eng.subprocess, "Popen", P)
    e = eng.Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), Store(tmp_path / "rz.db"))
    e._keep_awake(True)
    e._keep_awake(True)                                   # already held: no second caffeinate
    assert len(made) == 1 and made[0].cmd == ["caffeinate", "-i", "-s"] and not made[0].done
    e._keep_awake(False)
    assert made[0].done and e._caffeinate is None
    monkeypatch.setattr(eng.subprocess, "Popen", lambda cmd: (_ for _ in ()).throw(OSError("no caffeinate")))
    e._keep_awake(True)                                   # not a Mac: no crash, nothing held
    assert e._caffeinate is None
