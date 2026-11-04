"""Runtime invariants + Health.report (redzone/platform/health.py): each broken invariant is seen
after its grace period, clears when it holds again, and the report matches the contract."""
import asyncio
from types import SimpleNamespace

from redzone.contracts.health import Health, HealthReport, Severity
from redzone.platform.breaker import Breaker
from redzone.platform.health import PlatformHealth


class Mono:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Lock:
    def __init__(self, held=False):
        self.held = held

    def locked(self):
        return self.held


def world(**ctl):
    c = SimpleNamespace(_task=None, _flipping=False, input_unknown=False, live_control=True,
                        on_target="yt:espn", _lock=Lock(), sticks=[], two_stick=True)
    for k, v in ctl.items():
        setattr(c, k, v)
    cfg = SimpleNamespace(get=lambda key, default=None: {"control.max_switch_s": 150}.get(key, default))
    dec = SimpleNamespace(current="g1", replaying=None, after_replay=False, safe_mode=False)
    return SimpleNamespace(control=c, decider=dec, cfg=cfg, _pending_decision=None)


class Running:
    def done(self):
        return False


def codes(h, now=0.0):
    return {f.code for f in h.faults(now)}


def test_a_healthy_system_has_no_faults():
    h = PlatformHealth(world(), mono=Mono())
    assert codes(h) == set()


def test_orphan_switch_after_its_grace_period():
    m = Mono()
    e = world(_task=Running())
    h = PlatformHealth(e, mono=m)
    assert codes(h) == set()                       # a switch in its first seconds
    m.t = 11
    assert codes(h) == {"orphan_switch"}
    e.control._task = None
    assert codes(h) == set()                       # clears at once


def test_lost_switch_callback():
    m = Mono()
    e = world()
    e._pending_decision = SimpleNamespace(target="g2")
    h = PlatformHealth(e, mono=m)
    codes(h)
    m.t = 11
    assert codes(h) == {"lost_switch_callback"}


def test_a_flip_is_neither_orphan_nor_lost():
    m = Mono()
    e = world(_task=Running(), _flipping=True)
    h = PlatformHealth(e, mono=m)
    codes(h)
    m.t = 30
    assert codes(h) == set()
    m.t = 61
    assert codes(h) == {"stuck_flip"}


def test_unknown_screen_flagged_vs_silent():
    m = Mono()
    e = world(input_unknown=True, on_target=None)
    h = PlatformHealth(e, mono=m)
    assert codes(h) == {"screen_unknown"}          # flagged: recovery owns it
    e.control.input_unknown = False
    codes(h)
    m.t = 31
    assert codes(h) == {"screen_unknown_silent"}   # unknown and nobody is on it
    e.decider.replaying = object()
    assert codes(h) == set()                       # a replay is on: on_target None by design


def test_stuck_lock_uses_the_monotonic_clock():
    m = Mono()
    e = world(_lock=Lock(held=True))
    h = PlatformHealth(e, mono=m)
    assert codes(h, now=1e9) == set()              # a wall-clock jump can't make it stuck
    m.t = 151
    f = h.faults(0.0)
    assert [x.code for x in f] == ["stuck_lock"] and f[0].severity is Severity.CRITICAL


def test_open_breaker_is_a_stick_fault():
    b = Breaker("stick:a", clock=lambda: 0.0)
    b.hang(); b.hang()
    e = world(sticks=[SimpleNamespace(host="a:5555", breaker=b)])
    f = PlatformHealth(e, mono=Mono()).faults(0.0)
    assert [(x.code, x.component) for x in f] == [("stick_hung", "stick:a:5555")]


def test_report_matches_the_contract(tmp_path):
    e = world()
    e.decider.safe_mode = True
    e.network = SimpleNamespace(away=True)
    h = PlatformHealth(e, data_dir=tmp_path, mono=Mono())
    assert isinstance(h, Health)
    r = h.report(123.0)
    assert isinstance(r, HealthReport) and r.at == 123.0 and r.safe_mode and r.two_stick and r.away_from_home
    assert r.ram_mb and r.ram_mb > 0 and r.disk_free_mb and r.disk_free_mb > 0


async def test_supervisor_logs_each_fault_once_and_its_clearing(cfg, tmp_path):
    from redzone.supervisor import Supervisor, read_jsonl
    e = world(input_unknown=True, on_target=None)
    e.cfg = cfg
    sup = Supervisor(e, root=tmp_path)
    for _ in range(3):
        await sup.rule_invariants(sup.clock())
    e.control.input_unknown = False
    e.control.on_target = "yt:espn"
    await sup.rule_invariants(sup.clock())
    kinds = [(r["kind"], r.get("code")) for r in read_jsonl(sup.log_path)]
    assert kinds == [("invariant", "screen_unknown"), ("invariant_cleared", "screen_unknown")]
    assert sup.report().faults == ()
    await asyncio.sleep(0)


async def test_a_real_engine_reports_cleanly(cfg, tmp_path):
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(cfg, Store(tmp_path / "t.db"))
    r = e.supervisor.report()
    assert isinstance(r, HealthReport)
    assert [f.code for f in r.faults] == []
    await e.close()


def test_an_offline_stick_is_a_fault_at_home_but_not_away():
    m = Mono()
    e = world(sticks=[SimpleNamespace(host="a:5555", state="offline", connect_error="no route", breaker=None)])
    h = PlatformHealth(e, mono=m)
    codes(h)
    m.t = 31
    assert codes(h) == {"stick_offline"}
    e.network = SimpleNamespace(away=True)          # the Mac is at school: sticks look offline
    assert codes(h) == set()
