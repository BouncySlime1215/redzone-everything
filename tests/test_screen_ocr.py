import json
from dataclasses import replace
from pathlib import Path
import subprocess
import platform
import struct
import zlib

import pytest

from redzone import screen_ocr as ocr


def response(**updates):
    data = {"status": "ok", "width": 1920, "height": 1080,
            "coordinate_system": "normalized_top_left", "observations": [
                {"text": "Philadelphia Eagles at Washington Commanders", "confidence": .92,
                 "box": {"x": .1, "y": .2, "width": .7, "height": .1}}]}
    data.update(updates)
    return json.dumps(data)


def test_valid_result_preserves_ocr_confidence_and_screen_coordinates():
    result = ocr.parse_result(response())
    assert result.read_ok
    assert result.width == 1920 and result.height == 1080
    assert result.observations[0].y == .2
    assert result.observations[0].confidence == .92
    assert "Washington Commanders" in result.text
    assert result.source == "apple_vision_local"


@pytest.mark.parametrize("raw", ["not-json", "[]", "null", response(width=True),
                                  response(coordinate_system="bottom_left"),
                                  response(status="empty"), response(observations=[]),
                                  response(observations=[{"text": "Buy", "confidence": float("nan"),
                                                         "box": {"x": 0, "y": 0, "width": .1, "height": .1}}]),
                                  response(observations=[{"text": "Buy", "confidence": .9,
                                                         "box": {"x": .9, "y": 0, "width": .2, "height": .1}}])])
def test_invalid_helper_output_never_becomes_evidence(raw):
    result = ocr.parse_result(raw)
    assert result.status == "error"
    assert not result.read_ok and not result.text


def test_empty_image_does_not_count_as_readable_menu():
    result = ocr.parse_result(response(status="empty", observations=[]))
    assert result.status == "empty"
    assert not result.read_ok


@pytest.fixture
def fake_png(tmp_path, monkeypatch):
    path = tmp_path / "menu ; $(do-not-execute).png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    monkeypatch.setattr(ocr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr, "_helper", lambda timeout: Path("/tmp/local-ocr-test"))
    return path


def test_local_paths_are_argument_values_without_a_shell(fake_png, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, response(), "")
    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.recognize(fake_png).read_ok
    assert calls[0][0] == ["/tmp/local-ocr-test", str(fake_png)]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["timeout"] == 15.0


def test_timeout_returns_no_stale_text(fake_png, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(ocr.subprocess, "run", timeout)
    result = ocr.recognize(fake_png)
    assert result.status == "timeout" and not result.text


def test_nonzero_exit_cannot_claim_success(fake_png, monkeypatch):
    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, response(), ""))
    assert not ocr.recognize(fake_png).read_ok


def test_unreadable_and_non_png_are_rejected_before_compilation(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr, "_helper", lambda _: pytest.fail("must not compile"))
    assert ocr.recognize(tmp_path / "missing.png").status == "unreadable"
    invalid = tmp_path / "text.png"
    invalid.write_text("not an image")
    assert ocr.recognize(invalid).status == "error"


def test_non_macos_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(ocr.platform, "system", lambda: "Linux")
    assert ocr.recognize("unused.png").status == "unsupported"


def test_compiled_helper_is_reused_without_compiling_each_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []
    def compile_helper(args, **kwargs):
        calls.append(args)
        binary = Path(args[-1])
        binary.write_bytes(b"fake local executable")
        binary.chmod(0o700)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(ocr.subprocess, "run", compile_helper)
    first = ocr._helper(45)
    second = ocr._helper(45)
    assert first == second and first.is_file()
    assert len(calls) == 1


def focus(source="neutral_rectangle_border", **updates):
    result = {"x": .05, "y": .1, "width": .8, "height": .4,
              "confidence": .95, "source": source}
    result.update(updates)
    return result


def test_one_outline_attaches_only_contained_text():
    result = ocr.parse_result(response(focus_candidates=[focus()]))
    assert result.focus_status == "unique"
    assert result.focus_boxes[0].text == result.text
    outside = ocr.parse_result(response(focus_candidates=[focus(x=.85, width=.1)]))
    assert outside.focus_boxes[0].text == ""


def test_multiple_outlines_never_choose_one_arbitrarily():
    result = ocr.parse_result(response(focus_candidates=[focus(), focus(x=.85, width=.1)]))
    assert result.focus_status == "ambiguous"
    assert result.focus_boxes == ()


@pytest.mark.parametrize("label,accepted", [
    ("Watch Replay\nWarriors Broadcast", True), ("Watch Replay\nClippers Broadcast", True),
    ("Watch Replay\n•\nWarriors Broadcast", True),
    ("Resume", True), ("•\nResume", True), ("• Resume", True),
    ("Resume subscription", False), ("Resume\nBuy", False),
    ("Rent", False), ("Start over", False),
    ("Watch key plays", True), ("Start from beginning", True), ("• Start from beginning", True),
    ("Watch Nov 1 • WAS vs PHI", True), ("Watch Nov 1 • WAS vs PHI Buy", False),
    ("Watch Nov 32 • WAS vs PHI", False), ("Watch Nov 1", False),
    ("Watch Nov 1 • WAS", False), ("Start from beginning $9.99", False),
    ("Watch key plays • Subscribe", False), ("Play", False),
    ("Buy replay", False), ("Subscribe", False), ("a", False),
    ("Watch Replay", False), ("Watch Replay\nWarriors Broadcast\nBuy", False),
])
def test_filled_button_requires_observed_replay_or_resume_action(label, accepted):
    data = json.loads(response(focus_candidates=[focus("neutral_filled_button")]))
    data["observations"][0]["text"] = label
    result = ocr.parse_result(json.dumps(data))
    assert bool(result.focus_boxes) is accepted
    assert result.focus_status == ("unique" if accepted else "none")


def _synthetic_png(path, rectangles, border_thickness=3):
    """Create neutral rectangles on a dark canvas without any private screenshots."""
    width, height = 800, 450
    pixels = bytearray(bytes([15, 25, 40]) * width * height)
    for x, y, w, h, filled in rectangles:
        for py in range(y, y + h):
            for px in range(x, x + w):
                if (filled or px < x + border_thickness or px >= x + w - border_thickness
                        or py < y + border_thickness or py >= y + h - border_thickness):
                    i = (py * width + px) * 3
                    pixels[i:i + 3] = bytes([235, 235, 235])
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.mark.skipif(platform.system() != "Darwin", reason="native local pixel reader requires macOS")
def test_native_geometry_moves_with_border_and_rejects_keyboard(tmp_path):
    helper = ocr._helper(45)
    for name, rectangles, expected in [
        ("card", [(40, 70, 250, 150, False)], ["neutral_rectangle_border"]),
        ("moved", [(480, 210, 250, 150, False)], ["neutral_rectangle_border"]),
        ("thick", [(40, 70, 250, 150, False)], ["neutral_rectangle_border"]),
        ("busy_thin", [(40, 70, 250, 150, False), (50, 84, 220, 4, True),
                       (50, 201, 220, 4, True)], ["neutral_rectangle_border"]),
        ("multiple", [(40, 70, 250, 150, False), (480, 210, 250, 150, False)],
         ["neutral_rectangle_border", "neutral_rectangle_border"]),
        ("keyboard", [(40, 70, 50, 50, True)], []),
        ("button", [(350, 100, 400, 60, True)], ["neutral_filled_button"]),
    ]:
        path = tmp_path / f"{name}.png"
        _synthetic_png(path, rectangles, border_thickness=5 if name == "thick" else 3)
        proc = subprocess.run([str(helper), str(path), "--focus-only"], capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, proc.stderr
        boxes = json.loads(proc.stdout)["focus_candidates"]
        assert sorted(box["source"] for box in boxes) == sorted(expected)
        if name in {"card", "moved"}:
            assert abs(boxes[0]["x"] - rectangles[0][0] / 800) < .005
            assert abs(boxes[0]["y"] - rectangles[0][1] / 450) < .005


def caption_result(*extra):
    box = ocr.FocusBox(.1, .4, .22, .2, 1., "Playing\n3:30:45")
    rows = (ocr.TextBox("Eagles", 1., .105, .62, .07, .03),
            ocr.TextBox("Commanders", 1., .105, .66, .12, .03),
            ocr.TextBox("NBC 4 • Recorded 18 hours ago", 1., .105, .70, .18, .022), *extra)
    return ocr.OCRResult("ok", rows, 1920, 1080, focus_boxes=(box,), focus_status="unique")


def test_focus_caption_associates_only_same_column_and_preserves_relative_age():
    result = caption_result(ocr.TextBox("Cowboys", 1., .34, .62, .08, .03),
                            ocr.TextBox("Recorded 6 days ago", 1., .34, .70, .18, .022),
                            ocr.TextBox("Next section", 1., .105, .77, .13, .03))
    caption = ocr.focus_caption(result)
    assert caption.status == "matched"
    assert caption.text == "Eagles\nCommanders\nNBC 4 • Recorded 18 hours ago"
    assert "Cowboys" not in caption.text and "Next section" not in caption.text


@pytest.mark.parametrize("change", ["merged", "low_confidence", "overlap"])
def test_focus_caption_rejects_ambiguous_geometry(change):
    result = caption_result()
    first, second, third = result.observations
    if change == "merged":
        first = replace(first, width=.4, text="Eagles Cowboys")
    elif change == "low_confidence":
        first = replace(first, confidence=.4)
    else:
        second = replace(second, y=.63)
    result = replace(result, observations=(first, second, third))
    assert ocr.focus_caption(result).status == "ambiguous"
    assert not ocr.focus_caption(result).text


def test_focus_caption_does_not_complete_clipped_title_or_reassign_focus():
    result = caption_result()
    result = replace(result, observations=(replace(result.observations[0], text="Washington"),
                                          replace(result.observations[2], y=.66)))
    assert ocr.focus_caption(result).text == "Washington\nNBC 4 • Recorded 18 hours ago"
    assert ocr.focus_caption(result, replace(result.focus_boxes[0], x=.4)).status == "none"
    assert ocr.focus_caption(replace(result, focus_status="ambiguous", focus_boxes=())).status == "ambiguous"


def test_focus_caption_rejects_detached_text_and_button_focus():
    result = caption_result()
    result = replace(result, observations=(replace(result.observations[0], y=.68),))
    assert ocr.focus_caption(result).status == "none"
    result = replace(result, focus_boxes=(replace(result.focus_boxes[0], source="neutral_filled_button"),))
    assert ocr.focus_caption(result).status == "none"


def test_focus_caption_rejects_isolated_next_carousel_heading():
    result = caption_result()
    result = replace(result, observations=(replace(result.observations[0], text="Leagues and teams"),))
    assert ocr.focus_caption(result).status == "none"


def test_single_team_caption_requires_explicit_exact_title():
    result = caption_result()
    result = replace(result, observations=(replace(result.observations[0], text="Philadelphia Eagles"),))
    assert ocr.focus_caption(result).status == "none"
    assert ocr.focus_caption(result, expected_title="Philadelphia Eagles").text == "Philadelphia Eagles"
    assert ocr.focus_caption(result, expected_title="Eagles").status == "none"
    assert ocr.focus_caption(result, expected_title="Philadelphia Eagles at Washington").status == "none"
    assert ocr.focus_caption(result, expected_title="").status == "none"
    heading = replace(result, observations=(replace(result.observations[0], text="Leagues and teams"),))
    assert ocr.focus_caption(heading, expected_title="Philadelphia Eagles").status == "none"
    uncertain = replace(result, observations=(replace(result.observations[0], confidence=.8),))
    assert ocr.focus_caption(uncertain, expected_title="Philadelphia Eagles").status == "none"


def test_prepare_compiles_without_reading_an_image_or_running_vision(monkeypatch):
    monkeypatch.setattr(ocr.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(ocr, "_helper", lambda timeout: calls.append(timeout) or Path("/tmp/prepared-ocr"))
    result = ocr.prepare(compile_timeout=20)
    assert result.status == "ready" and result.helper_path == "/tmp/prepared-ocr"
    assert calls == [20]


def test_prepare_timeout_is_not_ready(monkeypatch):
    monkeypatch.setattr(ocr.platform, "system", lambda: "Darwin")
    def expired(_):
        raise subprocess.TimeoutExpired("swiftc", 1)
    monkeypatch.setattr(ocr, "_helper", expired)
    assert ocr.prepare().status == "timeout"


def test_a_word_a_hair_past_the_frame_edge_is_clamped_not_fatal():
    """Vision placed text cut at the left border at x = -0.0015 and the whole screen was rejected."""
    import json as _json
    from redzone import screen_ocr as so
    payload = {"status": "ok", "coordinate_system": "normalized_top_left", "width": 1920, "height": 1080,
               "focus_candidates": [],
               "observations": [{"text": "INDIANA", "confidence": 1,
                                 "box": {"x": -0.0015, "y": 0.27, "width": 0.0456, "height": 0.038}},
                                {"text": "NBCSN", "confidence": 1,
                                 "box": {"x": 0.08, "y": 0.2, "width": 0.05, "height": 0.03}}]}
    r = so.parse_result(_json.dumps(payload))
    assert r.read_ok
    texts = {o.text: o for o in r.observations}
    assert set(texts) == {"INDIANA", "NBCSN"} and texts["INDIANA"].x == 0.0
