"""The Chrome stream backend on FakeBridge: the seek sum, the order of the steps in a park and a jump, muting, health,
and the one error Nick has to fix by hand. No Chrome, no Mac, no network.

Why: redzone/chrome_source was written with no browser to try it on, so every promise it makes that does not need
the real player (which calls go out, in which order, with which numbers, and what it refuses to do) is pinned here;
what does need the real player is listed in the PR for the coordinator to run on the Mac.
"""
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from redzone import chrome_source
from redzone.channels import ChannelMap
from redzone.chrome_source import bridge as br
from redzone.chrome_source import page_script as ps
from redzone.chrome_source.streams import ChromeStreams

ROOT = Path(__file__).resolve().parent.parent
CODES = {"espn": "AAAAAAAAAAA", "tnt": "BBBBBBBBBBB", "abc": "CCCCCCCCCCC", "nbatv": "DDDDDDDDDDD"}      # made up
# What the page script and the AppleScript must never contain. Built from pieces so this file names no such thing whole.
PAGE_NEVER = ("setVolume", "pass" + "word", "coo" + "kie", "localStorage", "fetch(", "XMLHttpRequest", "getStatsForNerds", "location", "click(")
MAC_NEVER = ("volume", "keystroke", "key code", "pass" + "word", "quit", "delete", "preferences", "shell script", "set url", "sleep",
             "restart", "shut down", "system events")


class Clock:
    def __init__(self, t=1.7e9 + 5000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def rig(tmp_path, channels=("espn", "tnt", "abc"), **fake):
    clock = Clock()
    cm = ChannelMap(tmp_path / "channels.json")
    cm.codes = dict(CODES)
    fb = br.FakeBridge(clock, **fake)
    cs = ChromeStreams(fb, cm, lead_s=0.3, clock=clock, sleep=clock.sleep)
    cs.open(list(channels))
    return cs, fb, clock


def js(fb, since=0):
    return [(c[1], c[2]) for c in fb.calls[since:] if c[0] == "js"]


def one(cmd):
    return ps.build(cmd, t=12.5) if cmd.startswith("seek") else ps.build(cmd)


# ---- the page script ------------------------------------------------------------------------------------------------------

def test_every_call_is_one_tagged_script_over_movie_player():
    for cmd in ps.COMMANDS:
        s = one(cmd)
        assert ps.parse_tag(s) == (cmd, {"t": 12.5} if cmd.startswith("seek") else {})
        assert "getElementById('movie_player')" in s and "JSON.stringify" in s and s.rstrip().endswith("})()")
    assert {"state", "pause", "play", "seek", "seek_epoch", "live", "mute"} <= set(ps.COMMANDS)
    text = "".join(one(c) for c in ps.COMMANDS)
    for call in ("getPlayerState", "getProgressState", "ingestionTime", "isAtLiveHead", "seekableStart", "seekableEnd",
                 "pauseVideo", "playVideo", "seekTo(to,true)", "seekToLiveHead", "pl.mute()"):
        assert call in text, call
    with pytest.raises(ValueError):
        ps.build("volume")
    with pytest.raises(ValueError):
        ps.build("seek", t="1;alert(1)")


def test_the_page_script_asks_for_nothing_but_the_player():
    text = "".join(one(c) for c in ps.COMMANDS)
    for word in PAGE_NEVER:
        assert word not in text, word


def test_seek_maths():
    # the frame on screen was taken in at epoch 1000.0 and sits at media second 50.0: the frame of epoch 1012.5 is at 62.5
    assert ps.media_second(50.0, 1000.0, 1012.5) == pytest.approx(62.5)
    assert ps.media_second(50.0, 1000.0, 990.0) == pytest.approx(40.0)
    assert ps.epoch_s(1762800000.123) == pytest.approx(1762800000.123)          # seconds stay seconds
    assert ps.epoch_s(1762800000123) == pytest.approx(1762800000.123)           # milliseconds become seconds
    assert ps.epoch_s(None) is None and ps.epoch_s(True) is None
    assert [ps.state_name(c) for c in (1, 2, 3, 99)] == ["playing", "paused", "buffering", "unknown"]


# ---- park ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("over, ms", [(0.0, False), (0.22, False), (0.22, True)])
def test_park_seeks_short_of_the_second_then_pauses(tmp_path, over, ms):
    cs, fb, clock = rig(tmp_path, seek_over_s=over, ms=ms)
    target = clock() - 60.0                       # a second 60 s behind real time (the tab's live head is 25 s behind)
    n = len(fb.calls)
    r = cs.park(1, target)
    assert [c for _, c in js(fb, n)] == ["seek_epoch", "pause"] and {t for t, _ in js(fb, n)} == {cs.streams[1].tab}
    assert fb.calls[n][3] == {"t": pytest.approx(target - 0.3)}                # aimed the lead early
    assert r["ok"] and r["state"] == "paused"
    assert r["epoch"] == pytest.approx(target - 0.3 + over) and r["epoch"] <= target      # never past the second asked for
    assert r["short_s"] == pytest.approx(0.3 - over, abs=1e-3)
    clock.sleep(40.0)                              # a paused tab holds its place
    assert cs.position(1)["epoch"] == pytest.approx(r["epoch"]) and cs.position(1)["state"] == "paused"
    assert cs.position(0)["state"] == "playing"    # and nothing else was touched


def test_park_refuses_a_second_the_tab_cannot_reach(tmp_path):
    cs, fb, clock = rig(tmp_path, dvr_s=600.0)
    n = len(fb.calls)
    ahead, too_old = cs.park(1, clock() + 5.0), cs.park(1, clock() - 5000.0)
    assert not ahead["ok"] and not too_old["ok"] and ahead["why"] == too_old["why"] == "out_of_range"
    assert [c for _, c in js(fb, n)] == ["seek_epoch", "seek_epoch"]           # no pause followed: the tab is as it was
    assert cs.position(1)["state"] == "playing"


# ---- jump ------------------------------------------------------------------------------------------------------------------

def test_jump_is_front_then_play_then_pause_the_one_left(tmp_path):
    cs, fb, clock = rig(tmp_path)
    assert cs.on_screen == 0
    cs.park(2, clock() - 50.0)
    n = len(fb.calls)
    r = cs.jump(2)
    t0, t2 = cs.streams[0].tab, cs.streams[2].tab
    assert fb.calls[n:] == [("activate", t2), ("js", t2, "play", {}), ("js", t0, "pause", {})]
    assert r == {"ok": True, "to": 2, "left": 0, "state": "playing", "left_state": "paused", "left_epoch": pytest.approx(clock() - 25.0)}
    assert cs.on_screen == 2 and fb.active == t2
    left_at = cs.position(0)["epoch"]
    clock.sleep(30.0)
    assert cs.position(0)["epoch"] == pytest.approx(left_at)                   # the stream that was left waits where the viewer left it
    assert cs.position(2)["epoch"] == pytest.approx(clock() - 50.0 - 0.3)      # the new one has played on from its park
    n = len(fb.calls)
    assert cs.jump(2)["left"] is None and [c[0] for c in fb.calls[n:]] == ["js"]      # already on screen: nothing moves
    cs.jump(0)
    assert cs.on_screen == 0 and cs.position(2)["state"] == "paused" and cs.position(0)["state"] == "playing"


# ---- muting ----------------------------------------------------------------------------------------------------------------

def test_every_tab_is_muted_through_the_player_unless_told_otherwise(tmp_path):
    cs, fb, _ = rig(tmp_path)
    assert [c[2] for c in fb.calls if c[0] == "open"] == [f"https://tv.youtube.com/watch/{CODES[k]}" for k in ("espn", "tnt", "abc")]
    assert all(cs.health(i)["muted"] for i in range(3)) and not any(s.audio for s in cs.streams)
    cs.mute(1, on=False)
    assert [cs.health(i)["muted"] for i in range(3)] == [True, False, True] and cs.streams[1].audio
    cs.jump(1)
    assert [cs.health(i)["muted"] for i in range(3)] == [True, False, True]    # a jump never changes the sound


def test_open_waits_for_each_player_and_can_leave_one_with_sound(tmp_path):
    clock = Clock()
    cm = ChannelMap(tmp_path / "channels.json")
    cm.codes = dict(CODES)
    fb = br.FakeBridge(clock, load_polls=3)
    cs = ChromeStreams(fb, cm, lead_s=0.3, clock=clock, sleep=clock.sleep)
    t = clock()
    cs.open(["espn", "tnt"], audio=1)
    assert [fb.tabs[s.tab].muted for s in cs.streams] == [True, False]
    assert sum(1 for c in fb.calls if c[0] == "js") == 8 and clock() - t == pytest.approx(1.5)      # 3 empty answers each, then one that took
    with pytest.raises(ValueError):
        cs.open(["espn", "no-such-channel"])
    assert len(cs.streams) == 2 and len(fb.tabs) == 2                          # a missing code opens nothing at all


# ---- health ----------------------------------------------------------------------------------------------------------------

def test_health_gives_state_lag_and_the_error_overlay(tmp_path):
    cs, fb, clock = rig(tmp_path, lag_s=27.0, load_polls=0)
    h = cs.health(0)
    assert h == {"player": True, "state": "playing", "lag_s": pytest.approx(27.0), "error": False, "muted": True, "at_live": True, "on_screen": True}
    cs.park(1, clock() - 90.0)
    clock.sleep(10.0)
    h1 = cs.health(1)
    assert h1["state"] == "paused" and h1["lag_s"] == pytest.approx(100.3) and not h1["at_live"] and not h1["on_screen"]
    fb.tabs[cs.streams[2].tab].error = True
    assert cs.health(2)["error"] is True
    fb.load_polls = 10 ** 6                        # a tab whose page has no player (signed out, an error page)
    assert cs.health(0) == {"player": False, "state": "none", "lag_s": None, "error": False, "muted": None, "at_live": False, "on_screen": True}
    assert cs.position(0) == {"player": False} and not cs.park(0, clock() - 60.0)["ok"]


# ---- the real bridge, with osascript faked ------------------------------------------------------------------------------------

def runner(stdout="", stderr="", code=0, log=None):
    def run(cmd, **kw):
        if log is not None:
            log.append((cmd, kw.get("input")))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)
    return run


def test_the_turned_off_error_tells_nick_which_menu_item(tmp_path):
    err = "execution error: Google Chrome got an error: Executing JavaScript through AppleScript is turned off. To turn it on, ... (12)"
    log = []
    b = br.AppleScriptBridge(runner(stderr=err, code=1, log=log))
    with pytest.raises(br.JavaScriptFromAppleEventsOff) as e:
        b.run_js(7, ps.build("state"))
    msg = str(e.value)
    assert "View > Developer > Allow JavaScript from Apple Events" in msg and "never changes that setting" in msg
    assert len(log) == 1 and log[0][1] == br.AS_RUN_JS                         # it asked once and did nothing else about it
    cs, fb, _ = rig(tmp_path)
    fb.js_off = True
    with pytest.raises(br.JavaScriptFromAppleEventsOff):
        cs.health(0)


def test_the_applescript_bridge_passes_arguments_and_reads_small_json():
    log = []
    b = br.AppleScriptBridge(runner(stdout='{"player":1,"state":2}\n', log=log))
    script = ps.build("pause")
    assert b.run_js(41, script) == {"player": 1, "state": 2}
    cmd, text = log[0]
    assert cmd == ["osascript", "-", "41", script] and text == br.AS_RUN_JS    # the page script is an argument, never pasted into AppleScript
    assert 'tell application "Google Chrome"' in text and "execute t javascript js" in text
    assert br.AppleScriptBridge(runner(stdout="1234\n")).open_tab("https://tv.youtube.com/watch/AAAAAAAAAAA") == 1234
    br.AppleScriptBridge(runner(stdout="2\n", log=log)).activate(41)
    assert log[-1][1] == br.AS_ACTIVATE and "set active tab index of w to i" in br.AS_ACTIVATE and "set index of w to 1" in br.AS_ACTIVATE
    with pytest.raises(br.BridgeError):
        br.AppleScriptBridge(runner(stdout="missing value\n")).run_js(1, script)
    with pytest.raises(br.BridgeError, match="gone"):
        br.AppleScriptBridge(runner(stderr="execution error: RZ_TAB_NOT_FOUND (-2700)", code=1)).run_js(1, script)
    with pytest.raises(br.BridgeError, match="watch pages only"):
        br.AppleScriptBridge(runner()).open_tab("https://example.com/")

    def no_osascript(cmd, **kw):
        raise FileNotFoundError("osascript")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    with pytest.raises(br.BridgeError, match="Mac only"):
        br.AppleScriptBridge(no_osascript).run_js(1, script)
    with pytest.raises(br.BridgeError, match="did not answer"):
        br.AppleScriptBridge(slow).run_js(1, script)


def test_the_bridge_can_only_open_select_close_a_tab_and_run_page_script():
    """Everything it can send to the Mac is these four texts. None touches sound, power, keys, settings or accounts."""
    assert set(br.SCRIPTS) == {"run_js", "open", "activate", "close"}
    for text in br.SCRIPTS.values():
        assert text.count('tell application "Google Chrome"') == 1
        for word in MAC_NEVER:
            assert word not in text.lower(), word


# ---- the flag ----------------------------------------------------------------------------------------------------------------

def test_the_flag_ships_off_and_nothing_in_the_app_imports_the_package():
    cfg = yaml.safe_load((ROOT / "redzone/default_config.yaml").read_text())
    assert cfg["chrome_source"] == {"enabled": False, "park_lead_s": 0.3}
    assert chrome_source.enabled(cfg) is False and chrome_source.enabled(None) is False and chrome_source.enabled({}) is False
    assert chrome_source.enabled({"chrome_source": {"enabled": True}}) is True
    assert chrome_source.enabled(SimpleNamespace(get=lambda key, default=None: default)) is False
    # off: no streams, and the bridge is never asked for anything
    fb = br.FakeBridge(Clock())
    assert chrome_source.from_config(cfg, object(), fb) is None and chrome_source.from_config(None, object(), fb) is None and fb.calls == []
    # on: the streams, with the park lead the config holds
    on = chrome_source.from_config({"chrome_source": {"enabled": True, "park_lead_s": 0.5}}, object(), fb)
    assert isinstance(on, ChromeStreams) and on.lead_s == 0.5 and on.bridge is fb and fb.calls == []
    users = [p.relative_to(ROOT).as_posix() for p in (ROOT / "redzone").rglob("*.py")
             if "chrome_source" not in p.parts and re.search(r"chrome_source", p.read_text())]
    assert users == []                             # flag off is decision-identical: no runtime module even names it
