"""Switch traces, cut grading, the error budget, the nightly report's new sections and the phone push."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from redzone import control as ctl
from redzone import observability as obs
from redzone import supervisor as sup
from redzone import trace
from redzone.channels import ChannelMap
from redzone.config import Config, ConfigError, validate
from redzone.decider.cuts import Landing
from redzone.decider.decider import Decision, State
from redzone.engine import Engine
from redzone.store import Store
from redzone.supervisor import Supervisor, night_report, night_window
from test_hot_standby import FakeStick, FakeTuner

NIGHT = date(2026, 11, 2)
T0, T1 = night_window(NIGHT)
T = T0 + 14 * 3600          # 8 PM ET


def _out(**kw):
    base = dict(ok=True, target="youtubetv:espn", took=3.0, detail="ok", dry_run=False)
    base.update(kw)
    return ctl.Outcome(**base)


def _d(at=T, target="g2", kind="better", action="switch"):
    return Decision(at, action, target, "better game", State.WATCHING, kind=kind)


class Book:
    def __init__(self, mu=30.0, sigma=4.0):
        self.mu, self.sigma = mu, sigma

    def get(self, target):
        return SimpleNamespace(mu=self.mu, sigma=self.sigma)


# --- the trace itself ------------------------------------------------------------------------

def test_two_stick_trace_spans_and_cut_at_the_flip():
    marks = [("started", T + 0.2), ("tune_start", T + 0.3), ("tune_done", T + 4.3), ("safe_start", T + 4.3),
             ("safe_done", T + 6.3), ("flip_start", T + 6.4), ("flip_done", T + 7.5), ("done", T + 7.6)]
    out = _out(path="tuned", confirmed_at=T + 7.6, flip_s=1.1, marks=marks, stick="b:5555")
    t = trace.build(_d(), "g1", "youtubetv:espn", out, T + 0.1, delays=Book(), before_target="youtubetv:fox",
                    labels={"g1": "AAA @ BBB", "g2": "CCC @ DDD"}, now=T + 7.7)
    sp = t["spans"]
    assert sp["decide"] == 0.1 and sp["queued"] == 0.1 and sp["tune"] == 4.0 and sp["safe_wait"] == 2.0
    assert sp["flip"] == 1.1 and t["cut_at"] == round(T + 7.5, 2) and sp["dead_air"] == 0.1
    assert sp["to_picture"] == 7.6 and t["picture_confirmed"] and t["from_label"] == "AAA @ BBB"
    assert t["leave_delay"] == {"mu": 30.0, "sigma": 4.0} and t["steps"][0] == ["started", 0.2]
    assert "CCC @ DDD" in trace.line(t) and "decision->picture 7.6s" in trace.line(t)


def test_single_stick_cut_is_the_visible_tune_and_no_cut_for_failures_dry_runs_or_already():
    marks = [("started", T), ("safe_start", T), ("safe_done", T + 1), ("tune_start", T + 1), ("tune_done", T + 6)]
    one = trace.build(_d(), "g1", "x", _out(path="single", confirmed_at=T + 6, marks=marks), T, now=T + 6)
    assert one["cut_at"] == round(T + 1, 2) and one["spans"]["dead_air"] == 5.0
    failed = trace.build(_d(), "g1", "x", _out(ok=False, path="single", marks=marks), T, now=T + 6)
    assert failed["cut_at"] is None and failed["picture_at"] is None and not failed["picture_confirmed"]
    dry = trace.build(_d(), "g1", "x", _out(dry_run=True, confirmed_at=T), T, now=T)
    already = trace.build(_d(), "g1", "x", _out(path="already", confirmed_at=T), T, now=T)
    assert dry["cut_at"] is None and already["cut_at"] is None


def test_home_flip_that_never_resumed_is_ok_but_picture_unconfirmed():
    marks = [("flip_start", T + 1), ("flip_done", T + 2)]
    t = trace.build(_d(), "g1", "x", _out(path="preloaded", confirmed_at=T + 9, resumed=False, marks=marks), T)
    assert t["ok"] and not t["picture_confirmed"]
    assert "picture unconfirmed" in trace.line(t)


def test_trace_survives_odd_inputs():
    t = trace.build(SimpleNamespace(), None, None, SimpleNamespace(), T)   # nothing set at all
    assert t["spans"]["to_picture"] is None and t["cut_at"] is None and t["steps"] == []
    assert trace.of_row({"detail": "not json"}) is None and trace.of_row({"detail": {}}) is None
    assert trace.pct([], 0.9) is None and trace.pct([1, 2, 3, 4, 10], 0.9) == 10


# --- the controller stamps its steps ---------------------------------------------------------

@pytest.fixture
def two(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner())

    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, "input_status", input_status)
    return c


async def test_two_stick_switch_marks_every_step_in_order(two):
    c = two
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    names = [n for n, _ in out.marks]
    assert out.ok and names[0] == "started" and names[-1] == "done"
    for a, b in (("tune_start", "tune_done"), ("tune_done", "safe_start"), ("safe_done", "flip_start"),
                 ("flip_start", "flip_done")):
        assert names.index(a) < names.index(b)
    ts = [t for _, t in out.marks]
    assert ts == sorted(ts) and c._marks is None            # nothing left open for preloads to write into


async def test_single_stick_switch_marks_and_a_preload_never_marks(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555")]
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner())
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    names = [n for n, _ in out.marks]
    assert out.ok and out.path == "single" and "tune_start" in names and "flip_start" not in names
    c._mark("stray")                                        # outside a switch: dropped, no error
    assert c._marks is None


async def test_engine_saves_one_trace_per_switch(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    store = Store(tmp_path / "t.db")
    engine = Engine(cfg, store)
    engine.decider.current = "old"
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    engine.decider.target_of = lambda gid: "youtubetv:espn"
    d = Decision(T, "switch", "new", "better game", State.WATCHING, kind="better")
    engine._act(d, "old", {})
    callbacks[0](_out(path="preloaded", confirmed_at=T + 3, marks=[("flip_start", T + 2), ("flip_done", T + 3)]))
    rows = store.recent_switches(5)
    t = trace.of_row(rows[0])
    assert len(rows) == 1 and t["to"] == "new" and t["cut_at"] == round(T + 3, 2) and t["from"] == "old"
    assert t["enter_delay"]["mu"] > 0                       # the delay it planned with, for the cut grade
    await engine.close()


# --- cut grading -----------------------------------------------------------------------------

class Dead:
    """landing(x0, x1): 'play' inside any of `plays` (real-time intervals), else 'dead'."""

    def __init__(self, plays):
        self.plays, self.asked = plays, []

    def landing(self, x0, x1):
        self.asked.append((x0, x1))
        for a, b in self.plays:
            if a <= x0 <= b:
                return Landing("play", why="a play is on screen")
        return Landing("dead", "timeout", None, "dead ball on screen")


def _trace(cut, pic, **kw):
    t = {"id": "abc", "from": "g1", "to": "g2", "cut_at": cut, "picture_at": pic, "action": "switch",
         "leave_delay": {"mu": 30.0, "sigma": 3.0}, "enter_delay": {"mu": 40.0, "sigma": 3.0},
         "dry_run": False, "from_replay": False, "kind": "better", "path": "tuned"}
    t.update(kw)
    return t


def test_grade_cut_uses_the_screen_time_of_each_side():
    tls = {"g1": SimpleNamespace(dead=Dead([(T - 35, T - 25)])), "g2": SimpleNamespace(dead=Dead([]))}
    g = sup.grade_cut(_trace(T, T + 2), tls)
    assert g["leave"] == "play" and g["enter"] == "dead" and "timeout" in g["enter_why"]
    assert tls["g1"].dead.asked == [(T - 30, T - 30)] and tls["g2"].dead.asked == [(T + 2 - 40, T + 2 - 40)]
    # Leaving a replay isn't a live cut; entering a replay isn't a live landing; no timeline: no grade.
    assert sup.grade_cut(_trace(T, T + 2, from_replay=True, action="replay"), tls) is None
    assert sup.grade_cut(_trace(T, T + 2, **{"from": "redzone", "to": "reel"}), tls) is None


def _world(tmp_path, store, clock):
    e = SimpleNamespace(store=store, cfg=Config(tmp_path / "c.yaml"),
                        decider=SimpleNamespace(timelines={"g1": SimpleNamespace(dead=Dead([(T - 35, T - 25)])),
                                                           "g2": SimpleNamespace(dead=Dead([]))},
                                                safe_mode=False))
    return Supervisor(e, root=tmp_path, clock=clock)


async def test_cuts_are_graded_once_after_the_feed_caught_up(tmp_path):
    store = Store(tmp_path / "t.db")
    now = [T - 10]
    s = _world(tmp_path, store, lambda: now[0])
    store.switch(ts=T - 1, to_game="g2", ok=True, detail={"trace": _trace(T, T + 2)})
    store.switch(ts=T - 1, to_game="g2", ok=True, detail={"trace": _trace(T, T + 2, id="dry", dry_run=True)})
    now[0] = T + 30
    await s.grade_cuts(now[0])
    assert not (tmp_path / "supervisor_cuts.jsonl").exists()        # too soon: the feed may not have it yet
    now[0] = T + sup.CUT_JUDGE_AFTER_S + 1
    await s.grade_cuts(now[0])
    await s.grade_cuts(now[0] + 10)
    rows = sup.read_jsonl(tmp_path / "supervisor_cuts.jsonl")
    assert len(rows) == 1 and rows[0]["leave"] == "play"
    s2 = _world(tmp_path, store, lambda: now[0])                     # a restart doesn't grade old cuts again
    await s2.grade_cuts(now[0] + 20)
    assert len(sup.read_jsonl(tmp_path / "supervisor_cuts.jsonl")) == 1


async def test_safe_mode_episodes_are_recorded_on_change_only(tmp_path):
    now = [T]
    s = _world(tmp_path, Store(tmp_path / "t.db"), lambda: now[0])
    await s.track_episodes(now[0])                                   # starts off: nothing to record
    s.e.decider.safe_mode = True
    for _ in range(3):
        await s.track_episodes(now[0])
    now[0] += 300
    s.e.decider.safe_mode = False
    await s.track_episodes(now[0])
    ev = sup.read_jsonl(tmp_path / "supervisor.jsonl")
    assert [e["state"] for e in ev] == ["on", "off"]
    assert obs.safe_mode_seconds(ev, T0, T1) == (300.0, 1)
    # one left open counts until the end of the night
    assert obs.safe_mode_seconds(ev[:1], T0, T1)[0] == T1 - T


# --- showed, budget, report, push ------------------------------------------------------------

def test_showed_splits_screen_time_between_confirmed_pictures():
    ts = [_trace(T, T + 2, to="g1", to_label="AAA @ BBB", ok=True),
          _trace(T + 600, T + 605, to="g2", to_label="CCC @ DDD", ok=True),
          _trace(T + 700, None, to="g3", ok=False),                       # failed: shows nothing
          _trace(T + 900, T + 905, action="replay", ok=True)]
    got = dict(obs.showed(ts, T0, T1, end_at=T + 1500))
    assert got == {"AAA @ BBB": 598.0, "CCC @ DDD": 295.0, "catch-up replay": 595.0}


def test_budget_statuses_and_idle_nights_dont_count():
    tonight = {"switches": 40, "held": 0, "failed": 1, "unconfirmed": 0, "cuts": 30, "dead_air_slow": 3,
               "left_mid_play": 0, "safe_mode_s": 0}
    idle = {"switches": 0, "safe_mode_s": 0}
    bad = {"switches": 20, "held": 0, "failed": 4, "unconfirmed": 1, "cuts": 10, "dead_air_slow": 0,
           "left_mid_play": 1, "safe_mode_s": 1500}
    b = obs.budget(tonight, [idle])
    assert b["nights"] == 1 and b["switch_failures"]["status"] == "ok" and b["switch_failures"]["pct"] == 50
    assert b["left_mid_play"]["status"] == "ok" and b["spent"] == []
    b = obs.budget(tonight, [idle, bad])
    assert b["nights"] == 2 and b["switch_failures"]["used"] == 6 and b["switch_failures"]["status"] == "spent"
    assert b["left_mid_play"]["status"] == "spent" and b["left_mid_play"]["pct"] is None
    assert b["safe_mode"]["used"] == 25 and b["safe_mode"]["allowed"] == 20 and "safe_mode" in b["spent"]
    loose = obs.budget(tonight, [bad], {"mid_play_cuts": 2, "failure_pct": 20})
    assert loose["left_mid_play"]["pct"] == 50 and loose["switch_failures"]["status"] == "ok"


def _fill_night(tmp_path, store):
    store.switch(ts=T, to_game="g1", target="youtubetv:espn", kind="better", ok=True,
                 confirmed_ts=T + 3, detail={"took": 3, "detail": "ok", "trace": _trace(
                     T + 2, T + 3, to="g1", to_label="AAA @ BBB", ok=True, picture_confirmed=True,
                     spans={"to_picture": 3.0, "dead_air": 1.0, "tune": 2.0})})
    store.switch(ts=T + 900, to_game="g2", target="prime:x", kind="chase", ok=False,
                 detail={"took": 75, "detail": "switch timed out after 75s", "trace": _trace(
                     None, None, to="g2", ok=False, spans={})})
    store.snapshot([(T + 3600, "g1", "nfl", 50, 0, "", "now")])
    sup.append_jsonl(tmp_path / "supervisor_cuts.jsonl", {"ts": T + 2, "leave": "play", "enter": "dead",
                                                          "from_label": "EEE @ FFF", "leave_why": "a play is on screen"})
    sup.append_jsonl(tmp_path / "supervisor_moments.jsonl", {"ts": T + 100, "matchup": "AAA @ BBB",
                                                             "label": "Touchdown", "seen": False})
    sup.append_jsonl(tmp_path / "agent.jsonl", {"ts": T + 200, "kind": "check", "action": "reconnect_stick",
                                                "why": "stick offline", "result": "reconnected"})
    sup.append_jsonl(tmp_path / "agent.jsonl", {"ts": T + 260, "kind": "check", "action": "none"})


def test_night_report_shows_showed_missed_broke_fixed_and_budget(tmp_path):
    store = Store(tmp_path / "t.db")
    _fill_night(tmp_path, store)
    rep = night_report(NIGHT, store, tmp_path, now=T1 + 400)
    text, phone, m = rep["text"], rep["phone"], rep["metrics"]
    for h in ("## What it showed", "## Switch timings (traces)", "## Cuts, judged from the feed afterwards",
              "## What the watchdog did", "## Error budget", "## Switches", "## Key moments", "## Needs you"):
        assert h in text
    assert "AAA @ BBB: 1h00m" in text and "Left mid-play: 1 of 1" in text and "reconnect_stick: 1" in text
    assert "Error budget spent" in text and "Mid-play cuts" in text
    assert m["left_mid_play"] == 1 and m["failed"] == 1 and m["showed"] == [["AAA @ BBB", 3597]]
    lines = phone.splitlines()
    assert len(lines) == 5 and len(phone) <= 1000
    assert lines[0].startswith("Showed: AAA @ BBB 1h00m") and "1 of 1 big plays" in lines[1]
    assert "1 failed switch (timed out 1)" in lines[2] and "1 mid-play cuts" in lines[2]
    assert "watchdog 1" in lines[3] and "mid-play cuts SPENT" in lines[4]
    assert rep["budget"]["spent"] and json.dumps(m)              # the sidecar is plain JSON


def test_empty_night_report_still_renders(tmp_path):
    rep = night_report(NIGHT, Store(tmp_path / "t.db"), tmp_path, now=T1 + 400)
    assert "Nothing confirmed on screen." in rep["text"] and rep["budget"]["spent"] == []
    assert rep["phone"].startswith("Showed: nothing confirmed")


async def test_maybe_report_writes_the_sidecar_and_pushes_high_when_budget_spent(tmp_path):
    store = Store(tmp_path / "t.db")
    _fill_night(tmp_path, store)
    now = [T1 + sup.REPORT_AFTER_S + 5]
    s = _world(tmp_path, store, lambda: now[0])
    sent = []

    async def fake_push(server, topic, title, body, priority, tags, fake):
        sent.append((topic, title, priority, tags, body))
        return True
    s.opt = lambda k: "rz-abcdefghijklmnop" if k == "ntfy_topic" else sup.DEFAULTS.get(k)
    orig = sup.push_ntfy
    sup.push_ntfy = fake_push
    try:
        await s.maybe_report(now[0])
    finally:
        sup.push_ntfy = orig
    side = json.loads((tmp_path / "reports" / "2026-11-02.json").read_text())
    assert side["left_mid_play"] == 1 and (tmp_path / "reports" / "2026-11-02.md").exists()
    assert sent and sent[0][2] == "high" and "warning" in sent[0][3] and sent[0][4].startswith("Showed:")
    # next night's budget reads this sidecar back
    nxt = obs.budget({"switches": 1}, obs.load_sidecars(tmp_path / "reports", date(2026, 11, 3), 7))
    assert nxt["nights"] == 2 and nxt["left_mid_play"]["status"] == "spent"


async def test_push_ntfy_validates_the_topic_and_sends_safe_headers(monkeypatch):
    assert not await sup.push_ntfy("https://ntfy.sh", "has spaces/slash", "t", "b")
    assert not await sup.push_ntfy("https://ntfy.sh", "", "t", "b")
    seen = {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            seen.update(url=url, body=content, headers=headers)
            return SimpleNamespace(status_code=200, text="")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    ok = await sup.push_ntfy("https://ntfy.sh/", "rz-topic_1", "RedZone — night", "Showed: ✓", "high", "tv")
    assert ok and seen["url"] == "https://ntfy.sh/rz-topic_1" and seen["headers"]["Priority"] == "high"
    seen["headers"]["Title"].encode("latin-1")                      # would raise if not header-safe
    assert seen["body"] == "Showed: ✓".encode()


async def test_push_failure_is_logged_not_raised(monkeypatch):
    import httpx

    class Boom:
        def __init__(self, **kw):
            raise httpx.ConnectError("no network")
    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    assert await sup.push_ntfy("https://ntfy.sh", "rz-topic", "t", "b") is False


def test_budget_settings_are_validated(tmp_path):
    defaults = Config(tmp_path / "c.yaml").defaults
    validate({"supervisor": {"budget": {"failure_pct": 10}}}, defaults)
    with pytest.raises(ConfigError):
        validate({"supervisor": {"budget": {"failure_pct": 150}}}, defaults)
    with pytest.raises(ConfigError):
        validate({"supervisor": {"budget": {"window_nights": 0}}}, defaults)
    with pytest.raises(ConfigError):
        validate({"supervisor": {"budget": {"typo": 1}}}, defaults)


def test_cli_push_test_explains_how_to_set_a_topic(monkeypatch, capsys):
    monkeypatch.setattr(sup, "_cli_cfg", lambda: ("https://ntfy.sh", "", {}))
    assert sup.main(["push-test"]) == 1
    assert "ntfy_topic: redzone-" in capsys.readouterr().out
    assert sup.main(["nonsense"]) == 2


# --- the contract's stages -------------------------------------------------------------------

def _stages(t):
    return [e.stage.value for e in trace.events(t)]


def test_stages_line_up_with_the_contract():
    marks = [("started", T + 0.1), ("tune_start", T + 0.2), ("tune_done", T + 4), ("safe_start", T + 4),
             ("safe_done", T + 5), ("flip_start", T + 5.1), ("flip_done", T + 6)]
    ok = trace.build(_d(), "g1", "x", _out(path="tuned", confirmed_at=T + 6, marks=marks), T, now=T + 6)
    assert _stages(ok) == ["decided", "guarded", "command", "picture"] and ok["stages"][0][0] == "decided"
    ev = trace.events(ok)
    assert {e.switch_id for e in ev} == {ok["id"]} and ev[2].at == round(T + 5.1, 2) and ev[2].component == "control"
    held = trace.build(_d(), "g1", "x", _out(ok=False, detail="no safe moment to cut — kept the current game",
                                            marks=[("started", T)]), T, now=T + 1)
    assert _stages(held) == ["decided", "aborted"]
    dry = trace.build(_d(), "g1", "x", _out(dry_run=True, confirmed_at=T), T, now=T)
    assert _stages(dry)[-1] == "aborted"
    bad = trace.build(_d(), "g1", "x", _out(ok=False, path="tuned", detail="video never started",
                                           marks=marks[:3]), T, now=T + 4)
    assert _stages(bad) == ["decided", "failed"]
    unconfirmed = trace.build(_d(), "g1", "x", _out(path="preloaded", confirmed_at=T + 6, resumed=False,
                                                   marks=marks), T, now=T + 6)
    assert _stages(unconfirmed)[-1] == "failed"


def test_emit_survives_a_broken_sink():
    t = trace.build(_d(), "g1", "x", _out(confirmed_at=T + 1), T, now=T + 1)
    sink = trace.MemoryTrace()
    assert trace.emit(sink, t) == len(sink.events) == 2

    class Broken:
        def emit(self, ev):
            raise RuntimeError("disk full")
    assert trace.emit(Broken(), t) == 0 and trace.emit(None, t) == 0


async def test_engine_emits_trace_events(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    engine = Engine(cfg, Store(tmp_path / "t.db"))
    callbacks = []
    engine.control.submit = lambda *args, **kwargs: callbacks.append(args[2])
    engine.decider.target_of = lambda gid: "youtubetv:espn"
    engine._act(Decision(T, "switch", "new", "x", State.WATCHING, kind="better"), "old", {})
    callbacks[0](_out(path="preloaded", confirmed_at=T + 3, marks=[("flip_start", T + 2), ("flip_done", T + 3)]))
    sink = engine.trace_sink
    if isinstance(sink, trace.JsonlTrace):
        got = [json.loads(x) for x in sink.path.read_text().splitlines()]
    else:
        got = [{"stage": e.stage.value, "switch_id": e.switch_id} for e in sink.events]
    assert [g["stage"] for g in got] == ["decided", "command", "picture"]
    stored = trace.of_row(engine.store.recent_switches(1)[0])
    assert {g["switch_id"] for g in got} == {stored["id"]}         # the same id in the file and the DB row
    await engine.close()


def test_jsonl_sink_writes_one_contract_line_per_event(tmp_path):
    sink = trace.JsonlTrace(tmp_path / "d" / "traces.jsonl")
    assert isinstance(sink, trace.TraceSink)                       # the contract's runtime-checkable Protocol
    t = trace.build(_d(), "g1", "x", _out(confirmed_at=T + 1), T, now=T + 1)
    trace.emit(sink, t)
    rows = [json.loads(x) for x in sink.path.read_text().splitlines()]
    assert [r["stage"] for r in rows] == ["decided", "picture"] and rows[0]["switch_id"] == t["id"]
    mem = trace.MemoryTrace()
    trace.emit(mem, t)
    assert set(mem.timings(t["id"])) == {"decided", "picture"}
