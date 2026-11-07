"""ABC (WTVD 'ABC 11') by YouTube TV search, Nov 7: the search opens the channel page whose focus ring
OCR can't read; Select at the starting focus, else reopen + DOWN + Select. Only a media-session
SUBTITLE containing 'ABC 11' on a LIVE playhead counts; a miss presses BACK. Flag tuners.yttv_search_abc gates routing. Synthetic
screens (Nick's text read, positions guessed); no adb."""
import copy

from redzone.channels import LOCAL_SEARCH_QUERY, ChannelMap
from redzone.config import Config
from redzone.discovery.yttv import load_table
from redzone.tuners.prime_search import CapturedScreen
from redzone.tuners.yttv_search import hunt_channel, loose_codes_in, near_program_change, yttv_lines
from scripts import find_yttv_code as tool

BOXES = ([["Home", .05, .04, .04, .03], ["Live", .11, .04, .04, .03], ["Library", .17, .04, .06, .03],
          ["ABC 11", .05, .2, .08, .04], ["Top picks", .05, .32, .08, .03]]
         + [["abc", .06 + i * .2, .45, .04, .04] for i in range(4)]
         + [["ABC 11 Eyewitness", .05 + i * .2, .62, .15, .03] for i in range(4)]
         + [["News at 11PM", .05, .66, .12, .03], ["On now & upcoming", .05, .78, .15, .03]])
FX = {"start_scene": "channel_page", "playing": "ferg3lVdMOg",
      "watch": {"ferg3lVdMOg": "SportsCenter, ESPN, null",
                "Abc11Live01": "ABC 11 Eyewitness News at 9AM, ABC 11",
                "Abc11Rec001": "ABC 11 Eyewitness News at 11PM, ABC 11"},
      "positions": {"ferg3lVdMOg": 46800000, "Abc11Live01": 51200000, "Abc11Rec001": 4000},
      "scenes": {"channel_page": {"boxes": BOXES, "keys": {"KEYCODE_DPAD_CENTER": "play:Abc11Live01",
                                                           "KEYCODE_DPAD_DOWN": "on_now"}},
                 "on_now": {"boxes": BOXES, "keys": {"KEYCODE_DPAD_CENTER": "play:Abc11Live01"}}}}


import time as _time

MID = _time.mktime((2026, 11, 7, 11, 15, 0, 0, 0, -1))        # 11:15 local: no program change near


async def hunt(fx, *, channel_page=True, wall=MID):
    now = [1000.0]

    async def sleep(s):
        now[0] += s

    stick = tool.FixtureStick(fx, lambda: now[0])
    seq = [0]

    async def observe():
        seq[0] += 1
        now[0] += .3
        return CapturedScreen(tool._ocr(fx["scenes"].get(stick.scene, {"boxes": []})), seq[0], now[0])

    h = await hunt_channel(stick, observe, key="abc", table=load_table(), query=LOCAL_SEARCH_QUERY["abc"],
                           sleep=sleep, clock=lambda: now[0], wall=lambda: wall,
                           collect_codes=False, channel_page=channel_page)
    return stick, h


def keys(stick):
    return [c[4:] for c in stick.calls if c.startswith("key ")]


def key_times(h):
    return {s["detail"]: s["at_s"] for s in h.steps if s["action"] == "key"}


async def test_down_select_confirmed_by_subtitle_and_live_playhead():
    stick, h = await hunt(copy.deepcopy(FX))
    assert h.status == "confirmed", h.reason
    assert keys(stick) == ["KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"]
    assert "ABC 11" in h.description
    t = [s["at_s"] for s in h.steps if s["action"] == "key"]
    assert t[0] >= 3.0 and t[1] - t[0] >= 1.5          # >= 3 s after the link, >= 1.5 s after DOWN


async def test_recording_after_down_backs_out_then_select_at_start():
    fx = copy.deepcopy(FX)
    fx["scenes"]["on_now"]["keys"]["KEYCODE_DPAD_CENTER"] = "play:Abc11Rec001"
    stick, h = await hunt(fx)
    assert h.status == "confirmed", h.reason
    assert keys(stick) == ["KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER", "KEYCODE_BACK", "KEYCODE_DPAD_CENTER"]
    assert sum(c.startswith("view ") and "/search" in c for c in stick.calls) == 2


async def test_title_only_mention_is_refused_and_backs_out():
    fx = copy.deepcopy(FX)
    fx["watch"]["Abc11Live01"] = "ABC 11 Eyewitness News at 9AM, null, null"   # station only in the title
    stick, h = await hunt(fx)
    assert h.status == "stopped" and "no live session" in h.reason
    assert keys(stick)[-1] == "KEYCODE_BACK" and keys(stick).count("KEYCODE_BACK") == 2


async def test_other_abc_channel_subtitle_is_refused():
    fx = copy.deepcopy(FX)
    fx["watch"]["Abc11Live01"] = "World News Tonight, ABC News Live"
    stick, h = await hunt(fx)
    assert h.status == "stopped" and keys(stick)[-1] == "KEYCODE_BACK"


def test_plain_youtube_lines_are_dropped_from_code_probes():
    pkg = "com.amazon.firetv.youtube.tv"
    out = ("  dat=https://tv.youtube.com/watch/Abc11Live01 cmp=com.amazon.firetv.youtube.tv/.Main\n"
           "  intent={act=android.intent.action.VIEW cmp=com.amazon.firetv.youtube/.Main} v=jfKfPfyJRdk\n"
           "  dat=https://www.youtube.com/watch?v=jfKfPfyJRdk\n")
    assert loose_codes_in(yttv_lines(out, pkg)) == ["Abc11Live01"]


async def test_without_channel_page_it_still_stops_without_select():
    stick, h = await hunt(copy.deepcopy(FX), channel_page=False)
    assert h.status == "stopped" and "focus ring isn't readable" in h.reason
    assert "KEYCODE_DPAD_CENTER" not in keys(stick) and "KEYCODE_BACK" not in keys(stick)


def test_locals_route_by_search_only_with_the_flag(tmp_path, monkeypatch):
    import redzone.channels as ch
    assert Config().get("tuners.yttv_local_search", None) is False      # shipped off
    cm = ChannelMap(path=tmp_path / "channels.json")
    cm.codes.update(fox="njJRoIW1Lps", cbs="M_1qk2R5Wag", espn="ferg3lVdMOg")
    cm.st_codes = ["njJRoIW1Lps", "M_1qk2R5Wag"]                        # the Sunday Ticket mirrors
    # flag off: exactly as before (ST mirror codes, nbc's old query, no ABC)
    assert cm.yttv_code("youtubetv:fox") == "njJRoIW1Lps" and cm.yttv_search_key("youtubetv:abc") is None
    assert cm.search_ref("nbc") == "search:nbc"
    cm.local_search = True

    class Sat(ch.datetime):
        @classmethod
        def now(cls, tz=None):
            return ch.datetime(2026, 11, 7, 11, 0)

    class Sun(Sat):
        @classmethod
        def now(cls, tz=None):
            return ch.datetime(2026, 11, 8, 13, 0)

    monkeypatch.setattr(ch, "datetime", Sat)
    for key in ("abc", "cbs", "fox", "nbc"):
        assert cm.yttv_code(f"youtubetv:{key}") is None and cm._tunable(f"youtubetv:{key}")
        assert cm.yttv_search_key(f"youtubetv:{key}") == key and cm.search_ref(key) == f"search:local:{key}"
    assert cm.yttv_code("youtubetv:espn") == "ferg3lVdMOg"              # cable untouched
    cm.codes["abc"] = "XXXXXXXXXXX"                                     # a real (non-ST) code still wins
    assert cm.yttv_code("youtubetv:abc") == "XXXXXXXXXXX" and cm.yttv_search_key("youtubetv:abc") is None
    monkeypatch.setattr(ch, "datetime", Sun)                            # NFL Sunday: ST codes stay
    assert cm.yttv_code("youtubetv:fox") == "njJRoIW1Lps"


async def test_tuner_local_ref_uses_station_query_and_channel_page(monkeypatch):
    import redzone.tuners.yttv_search as ys
    from redzone.tuners.tuners import YouTubeTVTuner
    seen = {}

    async def fake_hunt(stick, observe, **kw):
        seen.update(kw)
        return ys.ChannelHunt(kw["key"], status="stopped", reason="fake")

    monkeypatch.setattr(ys, "hunt_channel", fake_hunt)

    class S:
        name, host, packages = "s", "x", {"youtubetv": "com.amazon.firetv.youtube.tv"}

        async def playback(self, pkg):
            return {"state": "playing", "meta": "SportsCenter, ESPN, null"}

        def still_on(self, target, before):
            return False

    for ref, key, query, page in (("search:local:fox", "fox", "FOX 50", True), ("search:nbc", "nbc", "NBC", False)):
        r = await YouTubeTVTuner().tune(S(), ref, f"youtubetv:{key}")
        assert not r.ok and (seen["key"], seen["query"], seen["channel_page"]) == (key, query, page)


def test_near_program_change_window():
    at = lambda h, m, sec: _time.mktime((2026, 11, 7, h, m, sec, 0, 0, -1))  # noqa: E731
    assert near_program_change(at(10, 59, 30)) and near_program_change(at(11, 0, 0))
    assert near_program_change(at(11, 1, 29)) and near_program_change(at(11, 29, 0))
    assert not near_program_change(at(11, 15, 0)) and not near_program_change(at(11, 2, 0))


async def test_settles_longer_at_a_program_change_and_still_checks_the_station():
    t_mid = [s["at_s"] for s in (await hunt(copy.deepcopy(FX)))[1].steps if s["action"] == "key"]
    stick, h = await hunt(copy.deepcopy(FX), wall=_time.mktime((2026, 11, 7, 11, 0, 0, 0, 0, -1)))
    t_edge = [s["at_s"] for s in h.steps if s["action"] == "key"]
    assert h.status == "confirmed" and keys(stick) == ["KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"]
    assert t_edge[0] >= 6.0 and t_edge[0] - t_mid[0] >= 2.9
    assert any("program change" in s["detail"] for s in h.steps)
    fx = copy.deepcopy(FX)                                     # the tile shifted: NFL Network plays
    fx["watch"]["Abc11Live01"] = "Jaguars at Ravens, NFL Network"
    fx["positions"]["Abc11Live01"] = 51200000
    stick, h = await hunt(fx, wall=_time.mktime((2026, 11, 7, 11, 0, 0, 0, 0, -1)))
    assert h.status == "stopped" and keys(stick)[-1] == "KEYCODE_BACK"
