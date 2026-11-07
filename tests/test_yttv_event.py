"""ESPN+ events through YouTube TV (tuners.espnplus_yttv, Nick Nov 7): find by search, fail closed per event."""
import asyncio
import re

from redzone.channels import ChannelMap, describe
from redzone.models import GameState, League, Team
from redzone.screen_ocr import FocusBox, OCRResult, TextBox
from redzone.tuners.adb import RunResult
from redzone.tuners.prime_search import CapturedScreen
from redzone.tuners.yttv_event import (EventSpec, event_spec, event_tiles, hunt_event, is_autoplay_prompt,
                                       names_event, prove_event_code, team_aliases)

SPEC = event_spec("401", "Capitals", "Hurricanes")


# --------------------------------------------------------------------------- names

def test_aliases_and_spec():
    assert team_aliases("Maple Leafs") == ("maple leafs", "leafs")
    assert team_aliases("NC") == ()                       # abbreviations are too short to trust
    assert SPEC.home == ("capitals",) and SPEC.away == ("hurricanes",)
    assert SPEC.queries[0] == "hurricanes capitals"
    assert EventSpec.from_ref(SPEC.ref()) == SPEC
    assert event_spec("1", "Rangers", "Rangers") is None  # can't tell them apart: fail closed
    assert event_spec("1", "UNC", "Duke") is None


def test_both_teams_needed():
    assert names_event("Hurricanes at Capitals | ESPN+", SPEC)
    assert not names_event("Capitals Pregame Live", SPEC)
    assert not names_event("Hurricanes at Devils", SPEC)


# --------------------------------------------------------------------------- a fake stick

def _box(text, x, y, w=.2, h=.04):
    return [text, x, y, w, h]


def _ocr(scene):
    boxes = tuple(TextBox(b[0], 1.0, *map(float, b[1:5])) for b in scene.get("boxes", []))
    f = scene.get("focus")
    fb = (FocusBox(*f[:4], 1.0, f[5] if len(f) > 5 else "", f[4]),) if f else ()
    return OCRResult("ok" if boxes else "empty", boxes, 1920, 1080, focus_boxes=fb,
                     focus_status="unique" if fb else "none")


TILE_A = [.05, .40, .25, .22, "neutral_rectangle_border"]   # left tile (other game)
TILE_B = [.35, .40, .25, .22, "neutral_rectangle_border"]   # right tile (our game)


def _search(our_text="Hurricanes at Capitals", live=True, focus=TILE_A):
    boxes = [_box("hurricanes capitals", .05, .15), _box("Top results", .05, .33),
             _box("Devils at Rangers", .07, .45), _box("LIVE", .07, .52, .05),
             _box(our_text, .37, .45)]
    if live:
        boxes.append(_box("LIVE", .37, .52, .05))
    return {"boxes": boxes, "focus": focus}


class FakeStick:
    def __init__(self, scenes, start="search", plays=None, state=3):
        self.scenes, self.scene = scenes, start
        self.plays = plays or {}          # scene name -> media description once it plays
        self.playing = "Some Show, ESPN"
        self.state = state
        self.packages = {"youtubetv": "com.amazon.firetv.youtube.tv"}
        self.host = "fake:5555"
        self.calls = []

    def _media(self):
        return (f"    package={self.packages['youtubetv']}\n"
                f"      state=PlaybackState {{state={self.state}, position=1000, buffered position=0, speed=1.0, "
                f"updated=1, actions=0}}\n      metadata: size=3, description={self.playing}\n")

    async def shell(self, cmd, timeout=8.0):
        self.calls.append(cmd)
        if cmd.startswith("dumpsys power"):
            return RunResult(True, "  mWakefulness=Awake\n", .01)
        if cmd.startswith("dumpsys media_session"):
            return RunResult(True, self._media(), .01)
        return RunResult(True, "", .01)

    async def key(self, *codes):
        for code in codes:
            self.calls.append(f"key {code}")
            assert code not in ("KEYCODE_HOME", "KEYCODE_WAKEUP")
            nxt = self.scenes.get(self.scene, {}).get("keys", {}).get(code)
            if nxt:
                self.scene = nxt
                if nxt in self.plays:
                    self.playing = self.plays[nxt]
        return RunResult(True, "", .01)

    async def view(self, url, package, wait=False, fresh=False):
        self.calls.append(f"view {url}")
        m = re.search(r"/watch/([A-Za-z0-9_-]{11})", url)
        if m:
            self.playing = self.plays.get(m.group(1), self.playing)
        return RunResult(True, "", .01)


def _run(stick, spec=SPEC, **kw):
    now = [0.0]
    seq = [0]

    async def sleep(s):
        now[0] += s

    async def observe():
        seq[0] += 1
        now[0] += .3
        return CapturedScreen(_ocr(stick.scenes.get(stick.scene, {"boxes": [_box("(video)", .45, .5)]})),
                              seq[0], now[0])
    return asyncio.run(hunt_event(stick, observe, spec, sleep=sleep, clock=lambda: now[0], **kw))


def _selects(stick):
    return [c for c in stick.calls if c == "key KEYCODE_DPAD_CENTER"]


def test_finds_walks_selects_and_confirms():
    scenes = {"search": {**_search(), "keys": {"KEYCODE_DPAD_RIGHT": "on_b"}},
              "on_b": {**_search(focus=TILE_B), "keys": {"KEYCODE_DPAD_CENTER": "player"}},
              "player": {"boxes": [_box("(video)", .45, .5)]}}
    stick = FakeStick(scenes, plays={"player": "Hurricanes at Capitals, ESPN+"})
    h = _run(stick)
    assert h.status == "confirmed", h.reason
    assert "Hurricanes" in h.description and h.query == "hurricanes capitals"
    assert stick.calls.count("key KEYCODE_DPAD_RIGHT") == 1 and len(_selects(stick)) == 1
    assert h.row()["found"] is True


def test_no_tile_fails_closed_without_select():
    scenes = {"search": {"boxes": [_box("hurricanes capitals", .05, .15), _box("Capitals Today", .07, .45)],
                         "focus": TILE_A}}
    stick = FakeStick(scenes)
    h = _run(stick)
    assert h.status == "stopped" and "fail closed" in h.reason
    assert not _selects(stick)
    assert sum(c.startswith("view https://tv.youtube.com/search") for c in stick.calls) == len(SPEC.queries)


def test_tile_without_live_marker_is_not_selected():
    scenes = {"search": {**_search(live=False), "keys": {"KEYCODE_DPAD_RIGHT": "on_b"}},
              "on_b": _search(live=False, focus=TILE_B)}
    stick = FakeStick(scenes)
    h = _run(stick)
    assert h.status == "stopped" and "LIVE" in h.reason
    assert not _selects(stick)


def test_wrong_game_playing_fails():
    scenes = {"search": {**_search(focus=TILE_B), "keys": {"KEYCODE_DPAD_CENTER": "player"}},
              "player": {"boxes": [_box("(video)", .45, .5)]}}
    stick = FakeStick(scenes, plays={"player": "Devils at Rangers, ESPN+"})
    h = _run(stick)
    assert h.status == "stopped" and "something else" in h.reason


def _prompt(focus_on):
    turn_on = [.30, .60, .15, .06, "neutral_filled_button", "Turn on"]
    later = [.50, .60, .15, .06, "neutral_filled_button", "Ask later"]
    return {"boxes": [_box("Turn on autoplay?", .30, .40, .4), _box("Turn on", .31, .61, .12),
                      _box("Ask later", .51, .61, .12)],
            "focus": turn_on if focus_on == "turn on" else later}


def test_autoplay_prompt_gets_ask_later_only():
    scenes = {"search": {**_search(focus=TILE_B), "keys": {"KEYCODE_DPAD_CENTER": "prompt"}},
              "prompt": {**_prompt("turn on"), "keys": {"KEYCODE_DPAD_RIGHT": "prompt_later"}},
              "prompt_later": {**_prompt("ask later"), "keys": {"KEYCODE_DPAD_CENTER": "player"}},
              "player": {"boxes": [_box("(video)", .45, .5)]}}
    stick = FakeStick(scenes, plays={"player": "Hurricanes at Capitals, ESPN+"}, state=0)
    stick.plays["player"] = "Hurricanes at Capitals, ESPN+"
    orig = stick.key

    async def key(*codes):
        # CENTER on the prompt is only ever sent while 'Ask later' has focus
        assert not (stick.scene == "prompt" and "KEYCODE_DPAD_CENTER" in codes)
        r = await orig(*codes)
        if stick.scene == "player":
            stick.state = 3
        return r
    stick.key = key
    h = _run(stick)
    assert h.status == "confirmed", h.reason
    assert h.autoplay_prompt == "answered_ask_later"
    assert is_autoplay_prompt("Turn on autoplay? Turn on Ask later")


def test_event_tiles_skip_heading_and_purchase():
    scene = _search()
    scene["boxes"].append(_box("Hurricanes at Capitals Buy for $4.99", .70, .45))
    tiles = event_tiles(_ocr(scene), SPEC, "hurricanes capitals")
    assert tiles and tiles[0][1].text == "Hurricanes at Capitals" and tiles[0][2]
    assert all(t[1].x < .6 and t[1].y >= .30 for t in tiles)   # no purchase tile, no heading


def test_prove_event_code():
    stick = FakeStick({}, plays={"EEEEEEEEEEE": "SportsCenter, ESPN", "CCCCCCCCCCC": "Hurricanes at Capitals, ESPN+"})
    now = [0.0]

    async def sleep(s):
        now[0] += s
    ok, why = asyncio.run(prove_event_code(stick, SPEC, "CCCCCCCCCCC", "EEEEEEEEEEE", sleep=sleep,
                                           clock=lambda: now[0]))
    assert ok, why
    stick2 = FakeStick({}, plays={"EEEEEEEEEEE": "SportsCenter, ESPN", "DDDDDDDDDDD": "Devils at Rangers"})
    ok, _ = asyncio.run(prove_event_code(stick2, SPEC, "DDDDDDDDDDD", "EEEEEEEEEEE", sleep=sleep,
                                         clock=lambda: now[0]))
    assert not ok


# --------------------------------------------------------------------------- routing

def _g(gid="g1", nets=("ESPN+",), lg=League.NHL):
    return GameState(lg, gid, Team("WSH", name="Capitals"), Team("CAR", name="Hurricanes"), broadcasts=list(nets))


def test_flag_off_keeps_old_route(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.espn_ids["g1"] = "abc"
    assert cm.where(_g()).target == "espn:abc"


def test_flag_on_routes_by_search_then_fails_closed(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.espn_ids["g1"] = "abc"
    cm.espnplus_yttv = True
    w = cm.where(_g())
    assert w.target == "youtubetv:evq:g1" and w.tunable
    ref = cm.yttv_event_ref(w.target)
    assert ref and EventSpec.from_ref(ref).home == ("capitals",)
    assert cm.yttv_code(w.target) is None and cm.yttv_search_key(w.target) is None
    assert describe(w.target) == "ESPN+ · YouTube TV (by search)"
    cm.event_missed(w.target)                              # the search failed
    assert cm.where(_g()).target == "espn:abc"             # old route for event_miss_s
    cm.yttv_event_miss["g1"] -= cm.event_miss_s + 1
    assert cm.where(_g()).target == "youtubetv:evq:g1"


def test_proven_code_wins_and_is_saved(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.espnplus_yttv = True
    cm.yttv_events["g1"] = "CCCCCCCCCCC"
    cm.save()
    cm2 = ChannelMap(tmp_path / "ch.json")
    cm2.espnplus_yttv = True
    w = cm2.where(_g())
    assert w.target == "youtubetv:ev:CCCCCCCCCCC" and w.tunable and w.confidence == "sure"


def test_unreadable_names_keep_old_route(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.espnplus_yttv = True
    g = GameState(League.NHL, "g2", Team("WSH"), Team("CAR"), broadcasts=["ESPN+"])
    assert cm.where(g).target == "youtubetv:espnplus" and not cm.where(g).tunable
