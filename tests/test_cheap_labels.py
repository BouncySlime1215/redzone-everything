"""Cheap labels (labels.enabled, off): bug-confirmed dead/live at a cut, break-on-picture rows, the joined switch
outcome row, the sync-anchor writer fix and the dry-run filter in the nightly report. Fakes only."""
import json
from datetime import date
from types import SimpleNamespace

from redzone import cheap_labels as cl
from redzone import supervisor as sup
from redzone.screen.scorebug import Bug
from redzone.store import Store
from redzone.supervisor import Supervisor, night_report, night_window

T = night_window(date(2026, 11, 2))[0] + 14 * 3600


class Cfg:
    def __init__(self, on=False):
        self.on, self.asked = on, []

    def get(self, key, default=None):
        self.asked.append(key)
        return self.on if key == "labels.enabled" else default


def bug(clock, period="2"):
    return Bug(home="WSH", away="BOS", period=period, clock=clock)


class Dead:
    def landing(self, x0, x1):
        from redzone.decider.cuts import Landing
        return Landing("dead", "timeout", x0 + 5, "dead ball on screen")


def _trace(**kw):
    t = {"id": "abc", "from": "g1", "to": "g2", "cut_at": T, "picture_at": T + 2, "action": "switch",
         "leave_delay": {"mu": 30.0}, "enter_delay": {"mu": 40.0}, "dry_run": False, "from_replay": False,
         "kind": "better", "path": "tuned", "from_target": "prime:AAA", "target": "prime:BBB", "ok": True,
         "stick": "192.168.1.9", "spans": {"dead_air": 2.0}}
    t.update(kw)
    return t


def _world(tmp_path, store, clock, cfg):
    labels = cl.CheapLabels(cfg, tmp_path / "labels")
    e = SimpleNamespace(store=store, cfg=cfg, cheap_labels=labels,
                        decider=SimpleNamespace(timelines={"g1": SimpleNamespace(dead=Dead()),
                                                           "g2": SimpleNamespace(dead=Dead())}, safe_mode=False))
    return Supervisor(e, root=tmp_path, clock=clock), labels


def _feed(labels, target, t0, clocks, **kw):
    for i, c in enumerate(clocks):
        labels.observe_bug(target, t0 + 2 * i, bug(c) if c else None, game="g1", league="nba", live=True,
                           visible=True, **kw)


async def test_flag_off_no_new_files_and_the_same_calls(tmp_path):
    cfg = Cfg(on=False)
    store = Store(tmp_path / "t.db")
    store.switch(ts=T - 1, to_game="g2", ok=True, detail={"trace": _trace()})
    now = [T - 10]
    s, labels = _world(tmp_path, store, lambda: now[0], cfg)
    now[0] = T + 200
    _feed(labels, "prime:AAA", T - 20, ["10:00", "10:00", "9:58"])
    _feed(labels, "prime:AAA", T - 8, [None] * 20 + ["9:50"])
    await s.grade_cuts(T + 200)
    rows = sup.read_jsonl(tmp_path / "supervisor_cuts.jsonl")
    assert len(rows) == 1 and not any(k.startswith(("leave_bug", "enter_bug")) for k in rows[0])
    assert not (tmp_path / "switch_outcomes.jsonl").exists() and not (tmp_path / "labels").exists()
    assert labels.reads == {}                                     # nothing observed, nothing kept
    assert set(cfg.asked) == {"labels.enabled"}                   # the flag is the only thing it asks of the config


async def test_flag_on_cut_gets_the_bug_clock_and_one_joined_outcome_row(tmp_path):
    cfg = Cfg(on=True)
    store = Store(tmp_path / "t.db")
    store.switch(ts=T - 1, to_game="g2", ok=True, cmd_ts=T - 0.5, confirmed_ts=T + 2.5, detail={"trace": _trace()})
    now = [T - 10]
    s, labels = _world(tmp_path, store, lambda: now[0], cfg)
    now[0] = T + 200
    _feed(labels, "prime:AAA", T - 8, ["10:00", "10:00", "9:58", "9:58"])   # last read at T-2: stopped? 9:58 twice
    _feed(labels, "prime:BBB", T - 3, ["5:00", "4:58", "4:56"])            # entry: clock ran
    await s.grade_cuts(T + 200)
    cut = sup.read_jsonl(tmp_path / "supervisor_cuts.jsonl")[0]
    assert cut["leave_bug_clock_running"] is False and cut["leave_bug_read_age_s"] == 2.0
    assert cut["enter_bug_clock_running"] is True and cut["enter_bug_read_age_s"] == 1.0
    out = sup.read_jsonl(tmp_path / "switch_outcomes.jsonl")
    assert len(out) == 1
    o = out[0]
    assert o["switch_id"] == "abc" and o["cmd_ts"] == T - 0.5 and o["confirmed_ts"] == T + 2.5
    assert o["first_picture_ts"] == T + 2 and o["landed_dead"] is True and "landed_play_s" in o
    assert o["from_app"] == o["to_app"] == "prime"
    assert "192.168" not in json.dumps(o) and "stick" not in o and "AAA" not in json.dumps(o)


def test_bug_state_is_unknown_when_stale_or_alone():
    labels = cl.CheapLabels(Cfg(True), None)
    _feed(labels, "prime:A", T, ["10:00"])
    assert labels.bug_state("prime:A", T + 1)["bug_clock_running"] is None       # one read: cannot tell
    assert labels.bug_state("prime:A", T + 60) == {"bug_clock_running": None, "bug_read_age_s": 60.0}
    assert labels.bug_state("prime:Z", T) == {"bug_clock_running": None, "bug_read_age_s": None}


def test_break_on_picture_row_after_20s_without_a_bug_on_a_live_game(tmp_path):
    labels = cl.CheapLabels(Cfg(True), tmp_path / "labels")
    _feed(labels, "youtubetv:ESPN", T, ["10:00", None, None, None])             # 6 s: too short
    _feed(labels, "youtubetv:ESPN", T + 8, ["9:50"])
    assert not (tmp_path / "labels" / "break_on_picture.jsonl").exists()
    _feed(labels, "youtubetv:ESPN", T + 20, [None] * 14 + ["9:40"])             # 26 s of nothing, then the bug
    rows = [json.loads(x) for x in (tmp_path / "labels" / "break_on_picture.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["type"] == "break_on_picture" and r["game"] == "g1" and r["league"] == "nba" and r["app"] == "youtubetv"
    assert r["start"] == T + 20 and r["end"] == T + 48 and r["dur_s"] == 28.0 and r["bug"] is False
    assert "target" not in r and "stick" not in r


def test_no_break_row_for_a_game_the_feed_does_not_say_is_live(tmp_path):
    labels = cl.CheapLabels(Cfg(True), tmp_path / "labels")
    for i in range(20):
        labels.observe_bug("prime:A", T + 2 * i, None, game="g1", league="nba", live=False, visible=True)
    labels.observe_bug("prime:A", T + 60, bug("9:00"), game="g1", league="nba", live=False, visible=True)
    assert not (tmp_path / "labels").exists()


def test_sync_anchor_rows_name_the_feed_event_and_why_they_were_not_used(cfg, tmp_path, monkeypatch):
    """Repro: every anchor had used=false and break_start rows had feed_type None. `used` false was the design
    (sync.break_anchor.enabled is false, logged only); feed_type was only filled for bursts (the bug)."""
    cfg.defaults["league_overrides"] = cfg.data["league_overrides"] = {}
    from redzone.engine import Engine
    from redzone.models import GameState, League, Phase, Play, Team
    from redzone.recorder import Recorder
    e = Engine(cfg)
    e.recorder = Recorder(tmp_path)
    g = GameState(game_id="g1", league=League.NBA, phase=Phase.LIVE, home=Team("WSH"), away=Team("BOS"))
    g.recent_plays = [Play(id="2", type_text="Full Timeout", wallclock=950.0)]
    e.merger.games["g1"] = g
    monkeypatch.setattr(e, "_game_on", lambda target, visible: "g1")
    e._break_event("yt:a", "b", False, "break_start", 1010.0, 5.0)
    cfg.update({"sync": {"break_anchor": {"enabled": True}}})
    e._break_event("yt:a", "b", False, "break_start", 1010.0, 5.0)
    e._break_event("yt:a", "b", False, "break_end", 1040.0, 5.0)
    rows = [json.loads(x) for x in next((tmp_path / "recordings").rglob("sync_anchors.jsonl")).read_text().splitlines()]
    assert rows[0]["feed_type"] == "Full Timeout" and rows[0]["used"] is False and rows[0]["used_why"] == "flag_off"
    assert rows[1]["feed_type"] == "Full Timeout" and rows[1]["used_why"] in ("used", "filter_rejected")
    assert rows[2]["used_why"] == "not_an_anchor_kind"


def test_nightly_by_kind_counts_only_live_rows(tmp_path):
    store = Store(tmp_path / "t.db")
    for i in range(5):
        store.switch(ts=T + i, kind="user", ok=True, dry_run=True, detail={})
    store.switch(ts=T + 9, kind="user", ok=True, dry_run=False, detail={})
    rep = night_report(date(2026, 11, 2), store, tmp_path, now=T + 86400)
    assert "By kind: user 1" in rep["text"] and "user 6" not in rep["text"]
