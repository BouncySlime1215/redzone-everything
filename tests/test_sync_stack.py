"""[SYNC 1/4/6 + auto-anchors] known shifts keep the sync, network widens the error bar, drift alarms,
on-screen break edges matched to the feed."""
import asyncio

from redzone.decider.screen import DelayBook
from redzone.decider.sync import SyncBook
from redzone import sync_probe as sp


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def synced_book():
    clk = Clock()
    b = SyncBook(lambda target: (40.0, 15.0), clock=clk)
    for _ in range(6):
        clk.t += 10
        b.measure("yt:a", 40.0, "clock")
    return b, clk


def test_a_measured_stall_shifts_the_sync_instead_of_throwing_it_away():
    b, clk = synced_book()
    f = b.get("yt:a")
    assert f.synced(clk.t) and f.plan_mu is not None
    b.shift("yt:a", 2.0, 0.5)
    assert abs(f.mu(clk.t) - 42.0) < 0.3 and f.sd(clk.t) < 2.0      # still synced, moved by the stall
    assert abs(f.drift(clk.t)) < 0.3                                  # a booked stall isn't drift


def test_delaybook_known_move_shifts_unknown_move_resyncs():
    clk = Clock()
    db = DelayBook({"yt": 40.0}, 15.0, clock=clk)
    s = db.attach_sync()
    for _ in range(6):
        clk.t += 10
        s.measure("yt:a", 40.0, "clock")
    db.set_behind("yt:a", 2.0, known_sd=0.5)
    assert s.filters["yt:a"].sd(clk.t) < 2.0
    db.set_behind("yt:a", 0.0)                 # a fresh tune at the live edge: re-sync
    assert s.filters["yt:a"].sd(clk.t) > 5.0


def test_bad_network_widens_the_error_bar_faster():
    b, clk = synced_book()
    f = b.get("yt:a")
    clk.t += 600
    calm = f.sd(clk.t)
    b.condition("yt:a", 20.0)
    assert f.sd(clk.t + 600) > f.sd(clk.t) and f.sd(clk.t + 600) - calm > (calm - 0) * 0.5
    assert b.alarms(clk.t) == [{"target": "yt:a", "why": "predicted", "q_scale": 20.0}]


def test_drift_alarm_when_the_delay_walks_away_from_the_plan():
    b, clk = synced_book()
    assert b.alarms(clk.t) == []
    for z in (43, 45, 47, 49):
        clk.t += 10
        b.measure("yt:a", float(z), "clock")
    a = b.alarms(clk.t)
    assert a and a[0]["why"] == "measured" and a[0]["drift_s"] > 5


def test_probe_condition_features():
    p = sp.SyncProbe({}, object())
    got = []
    p.on_condition = lambda target, q, feats: got.append((target, q, feats))
    p._condition("b", "yt:a", 1000.0, {"stall_s": 0.0, "wifi": {"rssi": -55}})
    assert got[-1][1] == 1.0
    p._condition("b", "yt:a", 1060.0, {"stall_s": 2.0, "wifi": {"rssi": -75}})
    q, feats = got[-1][1], got[-1][2]
    assert feats["rssi_drop"] == 20 and feats["stalls_per_min"] == 1.0
    assert q == 1.0 + 4.0 + 5.0 + 8.0


def test_probe_break_edges():
    p = sp.SyncProbe({}, object())
    ev = []
    p.on_screen_event = lambda *a: ev.append(a)
    p._screen_edge("b", "yt:a", False, 1000.0, False)
    p._screen_edge("b", "yt:a", False, 1020.0, True)
    p._screen_edge("b", "yt:a", False, 1040.0, True)
    p._screen_edge("b", "yt:a", False, 1060.0, False)
    p._screen_edge("b", "yt:b", False, 1080.0, True)        # another stream: no edge
    assert [e[3] for e in ev] == ["break_start", "break_end"]
    assert ev[0][4] == 1010.0 and abs(ev[0][5] - 20 / 12 ** 0.5) < 1e-9


def test_engine_matches_a_break_start_to_the_feeds_dead_ball(cfg, tmp_path, monkeypatch):
    from redzone.engine import Engine
    from redzone.models import GameState, League, Phase, Play, Team
    from redzone.recorder import Recorder
    e = Engine(cfg)
    e.recorder = Recorder(tmp_path)
    g = GameState(game_id="g1", league=League.NBA, phase=Phase.LIVE, home=Team("WSH"), away=Team("BOS"))
    g.recent_plays = [Play(id="1", type_text="Jump Shot", wallclock=900.0),
                      Play(id="2", type_text="Full Timeout", wallclock=950.0)]
    e.merger.games["g1"] = g
    monkeypatch.setattr(e, "_game_on", lambda target, visible: "g1")
    seen = []
    monkeypatch.setattr(e.sync, "measure", lambda *a, **k: seen.append(a) or True)
    e._break_event("yt:a", "b", False, "break_start", 1010.0, 5.0)
    assert seen == []                                          # off by default: logged only
    cfg.update({"sync": {"break_anchor": {"enabled": True}}})
    e._break_event("yt:a", "b", False, "break_start", 1010.0, 5.0)
    assert seen and seen[0][:3] == ("yt:a", 1010.0 - 950.0 - 15.0, "break")
    rows = (tmp_path / "recordings").rglob("sync_anchors.jsonl")
    lines = next(rows).read_text().splitlines()
    assert len(lines) == 2 and '"feed_wall":950.0' in lines[0] and '"sample_kind":"break_start"' in lines[0]
