"""Platform state machines (redzone/machines/platform.py): recorded session, device work, Fastcast topics,
the merger's benches, the healer's TV, the governor and the phone's crunch label."""
import asyncio
import json
from types import SimpleNamespace as NS

from redzone import fsm
from redzone.machines import platform as P
from redzone.models import GameState, League, Phase, Team


def test_every_platform_spec_is_registered_and_documented():
    specs = [v for v in vars(P).values() if isinstance(v, fsm.Spec)]
    assert len(specs) >= 18
    for s in specs:
        assert fsm.SPECS[s.name] is s and s.doc and all(s.states.values())
        assert s.owner and s.initial in s.states


# --- recorded session ------------------------------------------------------------------------------

async def test_recorded_session_tells_navigating_from_finished_and_failed(cfg):
    from test_recorded_playback import observed, setup
    gate = asyncio.Event()

    async def runner(*a, **kw):
        await gate.wait()
        await observed(*a, **kw)
    engine, body = setup(cfg, runner)
    rec = engine.recorded
    assert rec.session.state == "idle" and not rec.active
    assert (await rec.start(body))[0]
    await asyncio.sleep(0)
    assert rec.session.state == "navigating" and rec.snapshot()["session"]["state"] == "navigating"
    gate.set()
    await rec.task
    assert rec.session.state == "finished" and rec.active            # waits for Stop, still isolated
    await rec.stop()
    assert rec.session.state == "idle" and not rec.active

    engine, body = setup(cfg, runner)
    rec = engine.recorded
    rec.prepare_ocr = lambda: NS(status="timeout", error="no OCR")
    await rec.start(body)
    await rec.task
    assert rec.session.state == "failed" and rec.active
    await rec.stop()
    assert rec.session.state == "idle"


async def test_stop_while_navigating_goes_through_failed_to_idle(cfg):
    from test_recorded_playback import setup

    async def runner(*a, **kw):
        await asyncio.Event().wait()
    engine, body = setup(cfg, runner)
    rec = engine.recorded
    await rec.start(body)
    await asyncio.sleep(0)
    await rec.stop()
    assert [h[2] for h in rec.session.history] == ["preparing", "navigating", "failed", "idle"]


def test_device_work_machine_keeps_the_old_flag_api():
    from redzone.discovery.runtime import DiscoveryRuntime
    rt = DiscoveryRuntime.__new__(DiscoveryRuntime)
    rt.engine = NS(recorded=NS(active=False))
    rt._device_suspended = True
    assert rt.device_work.state == "suspended" and rt.device_suspended
    rt.resume_device_work()
    assert rt.device_work.state == "running" and not rt.device_suspended
    rt.engine.recorded.active = True
    assert rt.device_suspended                                        # a recorded session counts too


# --- fastcast topics ---------------------------------------------------------------------------------

def test_a_live_topic_without_a_document_is_not_reported_live():
    """Bug: a failed patch sets doc=None, but status() built live_topics from the state string alone."""
    from redzone.sources.fastcast import FastcastClient, TopicDoc, _Sub
    c = FastcastClient(lambda *a: None)
    sub = _Sub("gp-basketball-nba-1", TopicDoc("gp-basketball-nba-1"))
    sub.m.to("subscribed", "t")
    sub.m.to("loading", "t")
    sub.doc.load({"x": 1}, 5)
    sub.m.to("live", "t")
    c.topics[sub.name] = sub
    assert c.status()["live_topics"] == ["gp-basketball-nba-1"]
    sub.doc.doc = None                                                 # what a half-applied patch leaves
    assert c.status()["live_topics"] == [] and c.status()["topics"] == {sub.name: "live"}
    assert "gp-basketball-nba-1" in c.status()["waiting"] or not c.wanted


def test_fastcast_connection_stop_is_final():
    from redzone.sources.fastcast import FastcastClient
    c = FastcastClient(lambda *a: None)
    assert c.conn.state == "idle" and not c.connected and not c._stopping
    c.stop()
    assert c._stopping and c.conn.state == "stopped"
    c._conn("connecting", "late")                                      # a late finally can't revive it
    assert c.conn.state == "stopped"


# --- merger benches ----------------------------------------------------------------------------------

def _g(gid, period, clock, hs, as_):
    return GameState(league=League.NFL, game_id=gid, home=Team("WSH", score=hs), away=Team("DAL", score=as_),
                     phase=Phase.LIVE, period=period, clock_s=clock)


def test_a_quiet_benched_source_is_unbenched_when_its_bench_ends():
    """Bug: SourceHealth.benched was refreshed only for the source being updated, so a benched source
    that went quiet stayed flagged after its bench ended."""
    from redzone.sources.merge import Merger
    t = [1000.0]
    m = Merger(clock=lambda: t[0])
    m.update("bad", _g("1", 4, 60, 30, 17))
    for _ in range(3):
        m.update("bad", _g("1", 1, 900, 0, 0))
    assert m.health["bad"].benched and m.feeds.state("bad|1") == "benched"
    t[0] += 301
    m.update("good", _g("2", 2, 500, 7, 7))                            # only ANOTHER source updates
    assert not m.health["bad"].benched and m.feeds.state("bad|1") == "trusted"
    assert m._bench_until == {}


def test_regressions_that_age_out_return_the_feed_to_trusted():
    from redzone.sources.merge import Merger
    t = [1000.0]
    m = Merger(clock=lambda: t[0])
    m.update("s", _g("1", 4, 300, 10, 10))
    m.update("s", _g("1", 4, 301, 10, 10))                             # one backward jump
    assert m.feeds.state("s|1") == "regressing"
    t[0] += 400
    m.update("s", _g("1", 4, 290, 10, 10))
    assert m.feeds.state("s|1") == "trusted"


# --- the healer's TV ---------------------------------------------------------------------------------

def test_tv_found_by_scan_clears_on_lan_with_power_and_lost_since(tmp_path):
    """Bug: _move_tv set power and lost-since but left tv_on_lan from the failed look (three fields that
    reset together in one place and not in another)."""
    from test_network import HOME_STATE, FakeNet, _healer
    h, t, c = _healer(tmp_path, HOME_STATE, FakeNet())
    h._tv_silent("status page didn't answer")
    h._tv_on_lan = True
    assert h.tv_on_lan and h.tv_power is None and h.tv_lost_since == t[0]
    tv = {"ip": "100.110.165.151", "wifi_mac": "8c:79:f5:8e:e2:5f"}
    d = {"tv": tv}
    notes = h._move_tv(d, tv, [{"ip": "100.110.165.140", "wifi_mac": "8c:79:f5:8e:e2:5f", "power": "standby"}],
                       t[0])
    assert notes and h.tv_power == "standby" and h.tv_lost_since is None and not h.tv_on_lan
    assert h.tv.state == "standby"


async def test_away_resets_tv_and_sticks_together(tmp_path):
    from test_network import AWAY_STATE, HOME_STATE, FakeNet, _healer
    h, t, c = _healer(tmp_path, HOME_STATE, FakeNet())
    h._lost(c.sticks[0], t[0])
    h._tv_silent("x")
    assert h.offline_since and h.tv.state == "not_answering"
    h.network.state = AWAY_STATE
    h.network.refresh.return_value = AWAY_STATE
    await h.check()
    assert h.offline_since == {} and h.tv.state == "unknown" and h.pass_.state == "idle"


# --- governor ----------------------------------------------------------------------------------------

def test_governor_level_is_a_machine_and_keeps_its_changes_api():
    from redzone.governor import Governor, Sample
    t = [0.0]
    g = Governor({"calm_s": 100}, clock=lambda: t[0])
    g.update(Sample(mem_pressure=4))
    assert g.level == "critical" and g.m.state == "critical" and g.since == 0.0
    t[0] = 150
    g.update(Sample())
    t[0] = 260
    g.update(Sample())
    assert g.level == "normal" and [c[1] for c in g.changes] == ["critical", "normal"]
    assert g.view()["machine"]["state"] == "normal"


# --- crunch label ------------------------------------------------------------------------------------

def test_crunch_leaving_is_produced_and_times_out():
    """Bug: the stub was memoryless, so "leaving" (shown as "Crunch ending") could never appear."""
    from redzone.scoring.engine import Scored
    from redzone.web import phone_v2
    t = [0.0]
    tr = phone_v2.CrunchTracker(clock=lambda: t[0], leave_s=60)
    s = Scored("a", League.NBA, 140.0, 140.0, 0.0, 0.0, 0.0, "r", [], False, None, None, 0.0, crunch=True)
    games = {"a": GameState(league=League.NBA, game_id="a", home=Team("B"), away=Team("A"), phase=Phase.LIVE)}

    def cs():
        return phone_v2.watch_values({"a": s}, games, tr)["games"][0]["crunch_state"]
    assert cs() == "entering"
    s.crunch_now = True
    assert cs() == "in"
    s.crunch_now, s.crunch = False, False
    t[0] = 10
    assert cs() == "leaving"
    t[0] = 69
    assert cs() == "leaving"
    t[0] = 71
    assert cs() is None
    assert phone_v2.watch_values({"a": s}, games)["games"][0]["crunch_state"] is None   # no tracker: as before
    s.crunch_now = True
    assert cs() == "in"
    phone_v2.watch_values({}, {}, tr)                                  # the game left the list
    assert tr.games.state("a") == "out" and "a" not in tr.games


def test_state_machines_doc_renders_platform_specs():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "state_machines_script", Path(__file__).resolve().parents[1] / "scripts" / "state_machines.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.render()
    for name in ("supervisor.target", "deploy.lifecycle", "fastcast.topic", "autonomy.job", "phone.crunch"):
        assert f"## {name}" in text
    json.dumps(text)
