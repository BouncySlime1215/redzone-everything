"""YouTube TV channel hunt (scripts/find_yttv_code.py): search by OCR, confirm by media session,
prove a leaked code by a clean re-tune. Runs on recorded (synthetic) fixtures; no adb."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from redzone.discovery.yttv import VerifyState, load_table
from redzone.screen_ocr import FocusBox, TextBox
from redzone.tuners.prime_search import CapturedScreen
from redzone.tuners.yttv_search import loose_codes_in, names_channel, step_toward
from scripts import find_yttv_code as tool

FIX = Path(__file__).parent / "fixtures"
NBCSN = json.loads((FIX / "yttv_find_code_nbcsn.json").read_text())


async def run(fx, *, save=True):
    now = [1000.0]

    async def sleep(s):
        now[0] += s

    stick = tool.FixtureStick(fx, lambda: now[0])
    seq = [0]

    async def observe():
        seq[0] += 1
        now[0] += .3
        scene = fx["scenes"].get(stick.scene, {"boxes": [["(video)", .45, .5, .1, .03]]})
        return CapturedScreen(tool._ocr(scene), seq[0], now[0])

    saved = []
    cm = NS(codes=dict(fx.get("known_codes", {})), path=Path("x"), save=lambda: saved.append(True))
    state = VerifyState(Path("/nonexistent-dir-never-written/v.json"))
    state.save = lambda: None
    lines = []
    report = await tool.find_one(stick, observe, fx["channel"], load_table(), cm=cm, state=state,
                                 profile=fx.get("profile", ""), out=lines.append, sleep=sleep,
                                 clock=lambda: now[0], save=save)
    return stick, cm, report, saved, lines


def keys(stick):
    return [c[4:] for c in stick.calls if c.startswith("key ")]


@pytest.mark.parametrize("path", sorted(FIX.glob("yttv_find_code_*.json")), ids=lambda p: p.stem)
async def test_recorded_fixtures_pass_self_test(path):
    assert await tool.run_fixture(path, out=lambda *_: None)


async def test_found_code_is_proven_from_a_decoy_then_saved():
    stick, cm, report, saved, lines = await run(NBCSN)
    assert report["status"] == "found" and cm.codes["nbcsn"] == "Nb7cSnLive1" and saved
    views = [c for c in stick.calls if c.startswith("view ")]
    assert views[-2].endswith("/watch/ferg3lVdMOg") and views[-1].endswith("/watch/Nb7cSnLive1")
    assert keys(stick) == ["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"]
    assert any("FOUND Nb7cSnLive1" in line for line in lines)


async def test_paused_session_on_a_hidden_input_still_identifies_the_channel():
    # A stick that isn't the TV's input pauses its app; the metadata still changes.
    stick, cm, report, _saved, _ = await run(dict(NBCSN, media_state=2))
    assert report["status"] == "found" and cm.codes["nbcsn"] == "Nb7cSnLive1"
    assert "(paused)" in report["reason"]


async def test_no_save_reports_without_writing():
    _stick, cm, report, saved, _ = await run(NBCSN, save=False)
    assert report["code"] == "Nb7cSnLive1" and "nbcsn" not in cm.codes and not saved


async def test_sleeping_stick_is_never_woken_or_touched():
    fx = dict(NBCSN, wakefulness="Asleep")
    stick, cm, report, _saved, _ = await run(fx)
    assert report["status"] == "stopped" and "not Awake" in report["reason"]
    assert not keys(stick) and not any(c.startswith("view ") for c in stick.calls)


async def test_profile_picker_without_configured_profile_stops_before_select():
    fx = dict(NBCSN, profile="")
    stick, _cm, report, _saved, _ = await run(fx)
    assert "profile picker" in report["reason"] and not keys(stick)


async def test_purchase_text_on_results_stops_before_any_select():
    fx = copy.deepcopy(NBCSN)
    fx["scenes"]["search_on_nbcsn"]["boxes"].append(["Start free trial", .60, .80, .15, .03])
    stick, cm, report, _saved, _ = await run(fx)
    assert report["status"] == "stopped" and "payment" in report["reason"]
    assert keys(stick) == ["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_RIGHT"]   # profile, then one move only
    assert "nbcsn" not in cm.codes


async def test_tile_that_plays_another_channel_is_not_accepted():
    fx = copy.deepcopy(NBCSN)
    fx["watch"]["Nb7cSnLive1"] = "Big Noon Kickoff, null, FS1"
    _stick, cm, report, _saved, _ = await run(fx)
    assert report["status"] == "stopped" and "another channel" in report["reason"]
    assert "nbcsn" not in cm.codes


async def test_leaked_code_that_does_not_reproduce_the_channel_is_not_saved():
    fx = copy.deepcopy(NBCSN)
    fx["probes"]["logcat"] += "11-02 20:06:00.000 I cobalt: load videoId=Zz9OtherCh1\n"
    fx["probes"]["logcat"] = fx["probes"]["logcat"].replace("Nb7cSnLive1", "Ab3NotThis1")
    fx["watch"]["Zz9OtherCh1"] = "Big Noon Kickoff, null, FS1"
    _stick, cm, report, _saved, _ = await run(fx)
    assert report["status"] == "stopped" and "nbcsn" not in cm.codes
    assert [c["code"] for c in report["candidates"]] == ["Zz9OtherCh1", "Ab3NotThis1"]


async def test_confirmed_channel_without_any_leak_says_so():
    fx = copy.deepcopy(NBCSN)
    fx["probes"] = {}
    _stick, cm, report, _saved, _ = await run(fx)
    assert report["status"] == "stopped" and "no code leaked" in report["reason"]
    assert report["description"].endswith("NBCSN")


def test_channel_names_are_specific():
    table = load_table()
    assert names_channel("LIVE NBCSN", "nbcsn", table) and not names_channel("NBCSN", "nbc", table)
    assert names_channel("NBC Sports Network", "nbcsn", table)
    assert not names_channel("NBC Sports", "nbc", table)
    assert names_channel("WRAL News at 6", "nbc", table)


def test_loose_codes_and_step_direction():
    assert loose_codes_in("Navigate https://tv.youtube.com/watch/AAAAAAAAAAA x docid=BBBBBBBBBBB "
                          '"video_id": "CCCCCCCCCCC" videoId=AAAAAAAAAAA') == \
        ["AAAAAAAAAAA", "CCCCCCCCCCC", "BBBBBBBBBBB"]
    focus = FocusBox(.05, .40, .20, .20, 1)
    assert step_toward(focus, TextBox("NBCSN", 1, .27, .61, .08, .03)) == "KEYCODE_DPAD_RIGHT"
    assert step_toward(focus, TextBox("NBCSN", 1, .05, .90, .08, .03)) == "KEYCODE_DPAD_DOWN"
    assert step_toward(FocusBox(.27, .40, .2, .2, 1), TextBox("x", 1, .05, .61, .08, .03)) == "KEYCODE_DPAD_LEFT"


def test_hardware_run_requires_explicit_spare_flag(capsys):
    assert tool.main(["--stick", "10.0.0.9"]) == 2
    assert "--spare" in capsys.readouterr().out
