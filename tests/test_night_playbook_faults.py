"""Night-lab playbook faults (docs/playbooks.md, lane 4 backlog 2): each kind builds from a spec,
turns its switch on and back off, and is mapped to a playbook entry. The full night with them runs as
`python -m redzone.lab.night --faults redzone.lab.night.faults:playbook` (about 3 min a seed)."""
import random

import pytest
from types import SimpleNamespace

from redzone.lab.night import faults as F
from redzone.lab.night import house as H


def _world():
    house = H.House()
    d = house.add("10.0.0.11:5555", "HDMI1", "A")
    house.add("10.0.0.12:5555", "HDMI2", "B")
    house.tv.source = d
    a = d.apps[H.YTTV]
    d.fg, a.screen = H.YTTV, "player"
    d._stamp(a, H.PLAYING, 1000)
    feed = SimpleNamespace(frozen=False, rate_limited=False, slow_s=0.0, outage=False)
    return SimpleNamespace(house=house, feed=feed, live_windows=[(0.0, 4 * 3600.0)]), d, a


def test_every_playbook_kind_is_registered_and_mapped():
    assert set(F.PLAYBOOK) <= set(F.KINDS)
    assert all(v.split("-")[0] in ("FEED", "SAFE", "MAC", "NET", "STK", "APP", "TV") for v in F.PLAYBOOK.values())


def test_switches_turn_on_and_off():
    w, d, a = _world()
    for name, attr, on in (("feed_frozen", "frozen", True), ("rate_limit", "rate_limited", True),
                           ("slow_poll", "slow_s", 6.0)):
        f = F.from_spec({"name": name, "at": 0, "dur": 60})
        f.start(w)
        assert getattr(w.feed, attr) == on
        f.stop(w)
        assert not getattr(w.feed, attr)
    f = F.from_spec({"name": "wifi_degrade", "at": 0, "dur": 60, "scale": 8})
    f.start(w)
    assert w.house.adb_scale == 8.0
    f.stop(w)
    assert w.house.adb_scale == 1.0


def test_buffering_spins_the_visible_player_then_it_plays_again():
    w, d, a = _world()
    f = F.from_spec({"name": "buffering", "at": 0, "dur": 20, "host": "visible"})
    f.start(w)
    assert a.state == H.BUFFERING and d.picture()[1] == "loading" and f.info["hit"]
    f.stop(w)
    assert a.state == H.PLAYING


def test_playbook_plug_puts_one_of_each_inside_live_time():
    w, _, _ = _world()
    got = F.playbook(w, random.Random(3))
    names = [x.name for x in got]
    assert len(set(names)) == len(names) == 5 and set(names) <= set(F.PLAYBOOK)
    assert all(300 <= x.at <= 4 * 3600 - 600 for x in got)


def _tv_world():
    w, d, a = _world()
    w.live_windows = [(0.0, 4 * 3600.0)]
    w.engine = SimpleNamespace(cfg=SimpleNamespace(update=lambda x: None))
    return w, d, a


def test_tv_plug_seeds_st_down_other_input_and_cec_steal():
    w, _, _ = _tv_world()
    got = F.tv(w, random.Random(5))
    assert [x.name for x in got] == ["st_down", "tv_other_input", "cec_steal"]
    assert all(F.PLAYBOOK[x.name].startswith("TV-0") for x in got)


@pytest.mark.playbook("TV-02")
def test_st_down_fails_every_smartthings_call_then_clears():
    w, d, _ = _tv_world()
    f = F.from_spec({"name": "st_down", "at": 0, "dur": 60})
    f.start(w)
    assert "st_down" in w.house.active_faults
    f.stop(w)
    assert "st_down" not in w.house.active_faults


@pytest.mark.playbook("TV-03")
def test_other_input_takes_the_tv_off_our_sticks_then_gives_it_back():
    w, d, _ = _tv_world()
    f = F.from_spec({"name": "tv_other_input", "at": 0, "dur": 60})
    f.start(w)
    assert w.house.tv.source is None and w.house.tv.other == "HDMI3"
    f.stop(w)
    assert w.house.tv.source is d and w.house.tv.other is None


@pytest.mark.playbook("TV-04")
def test_cec_steal_reboots_the_hidden_stick_onto_the_tv():
    w, d, _ = _tv_world()
    f = F.from_spec({"name": "cec_steal", "at": 0, "dur": 0, "host": "10.0.0.12:5555"})
    f.start(w)
    b = w.house.sticks["10.0.0.12:5555"]
    assert w.house.tv.source is b and b.fg == H.LAUNCHER
