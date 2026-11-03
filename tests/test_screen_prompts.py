"""YouTube TV's own prompts, read from a screenshot: synthetic OCR results + synthetic pixels.

The profile picker is cleared only by walking the lit-up (focused) name to the configured one,
one DPAD step per fresh screenshot, then CENTER. Signed-out, unclear, or moved-on screens stop
without a guessed key."""
import platform
import struct
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from redzone import control as ctl
from redzone.screen_ocr import OCRResult, TextBox
from redzone.tuners import screen_prompts as sp
from redzone.tuners.adb import RunResult
from redzone.tuners.profiles import ProfileSelectionRequired
from redzone.tuners.tuners import CutGuard, CutWindowClosed, YouTubeTVTuner

W, H = 192, 108
PKG = "com.amazon.firetv.youtube.tv"
NAMES = ("Nick", "Serge", "Gabe", "Laureen")


def box(text, x, y, w=0.08, h=0.04, conf=0.95):
    return TextBox(text, conf, x, y, w, h)


def paint(luma, b, level, *, fill=None):
    """Text strokes at `level` (a few columns), optionally on a button fill."""
    x0, x1 = int(b.x * W), int((b.x + b.width) * W)
    y0, y1 = int(b.y * H), int((b.y + b.height) * H)
    if fill is not None:
        luma[y0:y1, x0:x1] = fill
    luma[y0:y1, x0:x1:3] = level


def picker(focus="Nick", names=NAMES, extra=(), marks=(), pkg=PKG, dim=140, bright=250):
    rows = [box("Who's watching?", 0.38, 0.15, w=0.24, h=0.06)]
    luma = np.full((H, W), 15.0)
    for i, name in enumerate(names):
        b = box(name, 0.15 + i * 0.2, 0.62)
        rows.append(b)
        paint(luma, b, bright if name == focus else dim)
        if name in marks:
            rows.append(box(marks[name], 0.15 + i * 0.2, 0.68, h=0.03))
    rows.extend(extra)
    return sp.Frame(OCRResult("ok", tuple(sorted(rows, key=lambda r: (r.y, r.x))), W, H), luma, pkg)


def still(buttons=("Yes",), lit_button="Yes", pkg=PKG, heading="Are you still watching?"):
    rows = [box(heading, 0.3, 0.3, w=0.4, h=0.06)]
    luma = np.full((H, W), 20.0)
    for i, label in enumerate(buttons):
        b = box(label, 0.4, 0.5 + i * 0.1, w=0.2, h=0.06)
        rows.append(b)
        if label == lit_button:
            paint(luma, b, 30, fill=245)          # focused: bright fill, dark text
        else:
            paint(luma, b, 235)                    # unfocused: white text on dark
    return sp.Frame(OCRResult("ok", tuple(rows), W, H), luma, pkg)


class Stick:
    def __init__(self, frames, ok=True):
        self.frames = list(frames)
        self.keys = []
        self.reads = 0
        self.ok = ok
        self.preferred_profiles = {"youtubetv": "Serge"}
        self.packages = {"youtubetv": PKG}
        self.screen_source = "ocr"

    async def key(self, *codes):
        self.keys.extend(codes)
        return SimpleNamespace(ok=self.ok)

    async def read(self, _stick):
        self.reads += 1
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


async def test_walks_the_lit_name_to_the_configured_profile_then_selects_it():
    st = Stick([picker("Nick"), picker("Serge")])
    steps = []
    assert await sp.clear_prompt(st, "Serge", steps, package=PKG, read=st.read) == "profile"
    assert st.keys == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"]
    assert st.reads == 2 and "playback still unconfirmed" in steps[-1]


async def test_walks_left_and_matches_the_name_case_insensitively():
    st = Stick([picker("Laureen"), picker("Gabe"), picker("Serge")])
    assert await sp.select_profile_ocr(st, "serge", [], package=PKG, read=st.read)
    assert st.keys == ["KEYCODE_DPAD_LEFT", "KEYCODE_DPAD_LEFT", "KEYCODE_DPAD_CENTER"]


async def test_already_focused_profile_is_one_center():
    st = Stick([picker("Serge")])
    assert await sp.select_profile_ocr(st, "Serge", [], read=st.read)
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


async def test_another_profile_signed_out_does_not_block_the_configured_one():
    st = Stick([picker("Serge", marks={"Laureen": "Signed out"})])
    assert await sp.select_profile_ocr(st, "Serge", [], package=PKG, read=st.read)
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


@pytest.mark.parametrize("frame,reason", [
    (picker("Serge", marks={"Serge": "Signed out"}), "signed out"),
    (picker("Serge", marks={"Serge": "Sign in"}), "signed out"),
    (picker("Serge", names=("Nick", "Gabe")), "missing"),
    (picker("Serge", names=("Nick", "Serge", "Serge")), "more than once"),
    (picker("Nobody"), "highlighted"),                                   # nothing lit
    (picker("Nick", dim=240), "highlighted"),                            # everything lit
    (picker("Serge", pkg="com.amazon.tv.launcher"), "app in front"),     # Fire OS's own picker
    (picker("Serge", extra=(box("Rent $9.99", 0.4, 0.85, w=0.2),)), "payment"),
    (sp.Frame(picker("Serge").ocr, None, PKG), "highlighted"),           # no pixels: focus unknown
])
async def test_unclear_or_unsafe_pickers_never_get_a_key(frame, reason):
    st = Stick([frame])
    with pytest.raises(ProfileSelectionRequired, match=reason):
        await sp.select_profile_ocr(st, "Serge", [], package=PKG, read=st.read)
    assert st.keys == []


async def test_focus_that_does_not_move_stops_after_one_step():
    st = Stick([picker("Nick")])
    with pytest.raises(ProfileSelectionRequired, match="did not move"):
        await sp.select_profile_ocr(st, "Serge", [], read=st.read)
    assert st.keys == ["KEYCODE_DPAD_RIGHT"]


async def test_picker_that_turns_into_something_else_stops():
    st = Stick([picker("Nick"), still()])
    with pytest.raises(ProfileSelectionRequired, match="no longer identifiable"):
        await sp.select_profile_ocr(st, "Serge", [], read=st.read)
    assert st.keys == ["KEYCODE_DPAD_RIGHT"]


async def test_no_configured_profile_or_unconfirmed_key_stops():
    with pytest.raises(ProfileSelectionRequired, match="no preferred profile"):
        await sp.select_profile_ocr(Stick([picker()]), "", [], read=Stick([picker()]).read)
    st = Stick([picker("Serge")], ok=False)
    with pytest.raises(ProfileSelectionRequired, match="unconfirmed"):
        await sp.select_profile_ocr(st, "Serge", [], read=st.read)
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


async def test_live_play_guard_runs_before_every_key():
    st = Stick([picker("Nick"), picker("Serge")])
    guard = CutGuard(lambda: False)
    from redzone.tuners.tuners import require_cut
    with pytest.raises(CutWindowClosed):
        await sp.select_profile_ocr(st, "Serge", [], lambda: require_cut(guard, []), read=st.read)
    assert st.keys == []


@pytest.mark.parametrize("frame,pressed", [
    (still(), True),                                                       # one button: CENTER
    (still(heading="Still there?", buttons=("Continue watching",), lit_button=""), True),
    (still(buttons=("Yes", "No"), lit_button="Yes"), True),
    (still(buttons=("Yes", "Exit"), lit_button="Exit"), False),            # the wrong button is lit
    (still(buttons=("Yes", "No"), lit_button=""), False),                  # can't see focus
    (still(pkg="com.amazon.tv.launcher"), False),
    (still(heading="Are you still watching? Sign in to continue"), False),
])
async def test_still_watching_is_cleared_only_when_yes_is_safe(frame, pressed):
    st = Stick([frame])
    steps = []
    got = await sp.clear_prompt(st, "Serge", steps, package=PKG, read=st.read)
    assert (got == "still_watching") is pressed
    assert st.keys == (["KEYCODE_DPAD_CENTER"] if pressed else [])


async def test_unreadable_or_ordinary_screens_are_left_alone():
    for frame in (sp.Frame(OCRResult("error", error="x")),
                  sp.Frame(OCRResult("ok", (box("SportsCenter", 0.1, 0.1),), W, H), None, PKG)):
        st = Stick([frame])
        assert await sp.clear_prompt(st, "Serge", [], package=PKG, read=st.read) is None
        assert st.keys == []


# --- pixels ------------------------------------------------------------------------------

def _bmp(rgb, bottom_up=True):
    h, w, _ = rgb.shape
    stride = (w * 3 + 3) & ~3
    rows = rgb[::-1] if bottom_up else rgb
    body = b"".join(r[:, ::-1].tobytes().ljust(stride, b"\0") for r in rows)   # BGR
    header = struct.pack("<2sIHHI", b"BM", 54 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h if bottom_up else -h, 1, 24, 0, len(body), 0, 0, 0, 0)
    return header + info + body


def _png(rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + r.tobytes() for r in rgb)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _image():
    rgb = np.zeros((6, 5, 3), np.uint8)
    rgb[0, 0] = (255, 255, 255)
    rgb[5, 4] = (255, 0, 0)
    return rgb


@pytest.mark.parametrize("bottom_up", [True, False])
def test_bmp_luma_reads_rows_top_down(bottom_up):
    luma = sp.bmp_luma(_bmp(_image(), bottom_up))
    assert luma.shape == (6, 5)
    assert luma[0, 0] == pytest.approx(255, abs=1) and luma[5, 4] == pytest.approx(0.2126 * 255, abs=1)
    assert luma[1, 1] == 0
    assert sp.bmp_luma(b"not a bmp") is None


@pytest.mark.skipif(platform.system() != "Darwin", reason="sips is macOS-only")
def test_png_luma_decodes_a_screencap_png(tmp_path):
    path = tmp_path / "s.png"
    path.write_bytes(_png(_image()))
    luma = sp.png_luma(str(path))
    assert luma is not None and luma.shape == (6, 5)
    assert luma[0, 0] > 250 and luma[2, 2] < 5


def test_lit_needs_one_clear_winner():
    frame = picker("Gabe")
    names = [r for r in frame.ocr.observations if r.text in NAMES]
    assert names[sp.lit(frame.luma, names)].text == "Gabe"
    assert sp.lit(frame.luma, names[:1]) is None


# --- wiring: tuner confirm path and the visible-stick watchdog ------------------------------

async def test_yttv_tuner_uses_screenshots_when_the_ui_dump_was_blind(monkeypatch):
    st = Stick([picker("Nick"), picker("Serge")])
    monkeypatch.setattr(sp, "read_frame", st.read)
    steps = []
    assert await YouTubeTVTuner()._press_prompt(st, "Who's watching? | Nick | Serge", steps, "cleared prompt")
    assert st.keys == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"]


async def test_tuner_watchdog_hook_swallows_unclear_pickers(monkeypatch):
    st = Stick([picker("Serge", marks={"Serge": "Signed out"})])
    monkeypatch.setattr(sp, "read_frame", st.read)
    steps = []
    assert await YouTubeTVTuner().clear_screen_prompt(st, PKG, steps) is None
    assert st.keys == [] and "signed out" in steps[-1]


async def test_watchdog_clears_a_prompt_before_reloading_the_game(cfg, tmp_path, monkeypatch):
    from redzone.channels import ChannelMap
    from test_hot_standby import FakeStick
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.2)
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    s = FakeStick("a:5555", state="none")
    s.packages = {"youtubetv": PKG}

    class Tuner:
        tuned = 0

        async def clear_screen_prompt(self, stick, pkg, steps):
            assert pkg == PKG
            stick.playing = "playing"           # "Who's watching?" cleared: the game comes back
            steps.append("profile selection sent: Serge")
            return "profile"

        async def tune(self, *a, **k):
            Tuner.tuned += 1
            raise AssertionError("no reload needed")
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", Tuner())
    assert await c._resume_playback(s, "youtubetv:espn", 0.0) == "prompt"
    assert Tuner.tuned == 0


async def test_watchdog_survives_a_tuner_without_the_hook(cfg, tmp_path, monkeypatch):
    from redzone.channels import ChannelMap
    from test_hot_standby import FakeStick
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", SimpleNamespace())
    assert await c._clear_screen_prompt(FakeStick("a:5555"), "youtubetv:espn", PKG) is False


async def test_stick_flags_ocr_reads(monkeypatch):
    from redzone.tuners.adb import Stick as Real
    st = Real("x:5555")

    async def run(*a, **k):
        return RunResult(True, "<hierarchy></hierarchy>", 0.0)

    async def ocr_text():
        return "Still there? | Continue watching"
    monkeypatch.setattr(st, "run", run)
    monkeypatch.setattr(st, "ocr_text", ocr_text)
    assert (await st.screen_text()).startswith("Still there?")
    assert st.screen_source == "ocr"


def test_describe_is_a_dry_run_of_the_picker():
    d = sp.describe(picker("Nick"), "Serge")
    assert d["prompt"] == "profile" and d["focused"] == "Nick" and d["would_press"] == "RIGHT"
    assert sp.describe(picker("Serge", marks={"Serge": "Signed out"}), "Serge")["would_press"] is None
    assert sp.describe(still(buttons=("Yes", "No")), "Serge")["buttons"] == {"yes": ["Yes"], "no": ["No"]}
