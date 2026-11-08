"""[scrub entry, item 4 of Nick's Nov 6 order] Seek the hidden stick into the buffer by the overlay scrub BEFORE
the flip, then flip at a dead ball on the delayed picture. A pause doesn't hold on YouTube TV, and media_session's
position crawls after a seek, so the overlay label's reading is the only offset trusted. Fails closed; ships off."""
import asyncio
import sys
import time
import types

import pytest

from redzone import control as ctl
from redzone.tuners import adb
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)


class Res:
    def __init__(self, ok=True, behind_s=67.0, reason=""):
        self.ok, self.behind_s, self.reason = ok, behind_s, reason


class CrawlStick(FakeStick):
    """After a scrub the app's position crawls (~+0.3 s per 20 s) while the picture plays in real time."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pos, self.upd, self.crawl = 1000, 1000, False

    def remember(self, target, report, scrubbed_s=None):
        if scrubbed_s is None and self.tuned is None:
            self.crawl = False    # a fresh tune plays from the live edge in real time again
        super().remember(target, report, scrubbed_s=scrubbed_s)

    async def playback(self, package=None):
        self.upd += 20_000      # the stick's clock runs 20 s per read...
        self.pos += 15 if self.crawl else 20_000   # ...the position crawls only after a scrub
        return {"state": self.playback_state, "pkg": package, "position": self.pos, "updated": self.upd}


@pytest.fixture
def scrub(two, monkeypatch):   # noqa: F811
    c, tuner = two
    c.cfg.update({"timing": {"behind_live_s": {"enabled": True, "max_s": 90, "apps": ["youtubetv"],
                                               "scrub_entry": True, "scrub_after_tune_s": 0}}})
    c.cfg.defaults.setdefault("yttv_seek", {"enabled": False})   # item 1's flag (its default lands with its PR)
    c.cfg.update({"yttv_seek": {"enabled": True}})
    hid = CrawlStick("b:5555", display=c.sticks[0].display, hdmi="HDMI2")
    c.sticks[1] = hid
    c.behind_of = lambda target: 60.0
    behind = []
    c.on_behind = lambda t, new, old: behind.append((t, new, old))
    calls = []
    result = {"r": Res()}

    async def enter_buffer(stick, seconds, *, cfg=None, pkg=None, still_hidden=None, **kw):
        assert still_hidden is not None and still_hidden()   # the focus/visibility guard is always passed
        calls.append((stick.host, seconds, pkg))
        stick.tuned = None                                   # its key presses clear the tune anchor
        stick.log.extend(["KEYCODE_DPAD_CENTER"] + ["KEYCODE_DPAD_LEFT"] * 4 + ["KEYCODE_DPAD_CENTER"])
        r = result["r"]
        r = r(stick) if callable(r) else r
        stick.crawl = bool(r.ok)          # a committed seek
        return r
    mod = types.ModuleType("redzone.tuners.yttv_seek")
    mod.enter_buffer = enter_buffer
    monkeypatch.setitem(sys.modules, "redzone.tuners.yttv_seek", mod)
    # `from redzone.tuners import yttv_seek` reads the package attribute first: once the real module was imported
    # (tests/test_yttv_seek.py in the same worker), sys.modules alone doesn't swap it
    import redzone.tuners as _tuners
    monkeypatch.setattr(_tuners, "yttv_seek", mod, raising=False)
    return c, tuner, hid, calls, result, behind


def run(coro):
    return asyncio.run(coro)


async def _preload(c, target):
    assert c.preload(target)
    await c._preload_task


def test_preload_scrubs_never_pauses_and_holds_the_label_reading(scrub):
    c, tuner, hid, calls, result, behind = scrub
    run(_preload(c, "youtubetv:fox"))
    assert calls == [("b:5555", 52.0, "yttv.pkg")]           # B 60 less the ~8 s the label read adds
    assert "KEYCODE_MEDIA_PAUSE" not in hid.log
    assert c.hidden_target == "youtubetv:fox" and c.hidden_ready
    assert c.held["youtubetv:fox"] == 67.0 and behind[-1] == ("youtubetv:fox", 67.0, 0.0)
    assert hid.scrubbed("youtubetv:fox") == 67.0
    # media_session's position crawls after the seek; the scrubbed anchor still counts as on it
    assert hid.still_on("youtubetv:fox", run(hid.playback("yttv.pkg")))
    # ...and the STK-06 watchdog doesn't read the crawl as "fell behind" and rebuild it by a pause
    assert c.buffer_error(hid, "youtubetv:fox", run(hid.playback("yttv.pkg"))) is None


def test_flip_onto_a_scrubbed_standby_does_not_scrub_again(scrub):
    c, tuner, hid, calls, result, behind = scrub
    run(_preload(c, "youtubetv:fox"))
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert out.ok and out.path == "preloaded" and len(calls) == 1 and tuner.calls == [("b:5555", "youtubetv:fox")]
    assert c.main is hid and c.on_target == "youtubetv:fox"


def test_tuned_path_scrubs_before_the_safe_wait(scrub):
    """The old game stays on screen through tune + scrub; the safe-moment check runs after the delay book moved."""
    c, tuner, hid, calls, result, behind = scrub
    seen = []

    def may_cut():
        seen.append(c.held.get("youtubetv:espn"))
        return True
    out = run(c.execute("youtubetv:espn", may_cut=may_cut))
    assert out.ok and out.path == "tuned" and len(calls) == 1
    assert seen and all(h == 67.0 for h in seen)                 # judged on the delayed picture
    steps = [m for m, _ in out.marks]
    assert steps.index("tune_done") < steps.index("scrub_start") < steps.index("scrub_done") \
        < steps.index("safe_start") < steps.index("flip_start")
    assert out.provenance["control"]["scrub"] == 67.0


def test_failed_scrub_keeps_the_current_game_and_retunes_at_the_live_edge(scrub, monkeypatch):
    c, tuner, hid, calls, result, behind = scrub
    result["r"] = Res(ok=False, behind_s=None, reason="focus not on the scrub bar")
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and out.screen_unchanged and "buffer entry failed" in out.detail
    assert c.main.host == "a:5555"                               # never flipped
    # re-tuned to a known position THROUGH another channel: a same-channel deep link doesn't reset a stick that is
    # behind (Nick's 90 s run, Nov 7)
    assert tuner.calls == [("b:5555", "youtubetv:espn"), ("b:5555", "youtubetv:fox"), ("b:5555", "youtubetv:espn")]
    assert hid.behind_on is None
    assert "youtubetv:espn" not in c.held and not c.hidden_ready
    # backoff: the next try presses nothing on the stick
    out2 = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out2.ok and len(calls) == 1


def test_failed_scrub_with_live_fallback_flips_at_the_live_edge(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"timing": {"behind_live_s": {"scrub_fallback": "live"}}})
    result["r"] = Res(ok=True, behind_s=None, reason="no label read")   # committed but unread: not trusted
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and c.main is hid and "youtubetv:espn" not in c.held


@pytest.mark.parametrize("reading", [0.0, 140.0])
def test_a_reading_outside_the_cap_fails_closed(scrub, reading):
    c, tuner, hid, calls, result, behind = scrub
    result["r"] = Res(behind_s=reading)
    run(_preload(c, "youtubetv:fox"))
    assert not c.hidden_ready and "youtubetv:fox" not in c.held and hid.scrubbed() is None


def test_a_stick_that_turned_visible_is_never_retuned(scrub):
    c, tuner, hid, calls, result, behind = scrub

    def flipped_meanwhile(stick):
        c.active = 1                         # the hidden stick is now the one on screen
        return Res(ok=False, behind_s=None)
    result["r"] = flipped_meanwhile
    run(_preload(c, "youtubetv:fox"))
    assert tuner.calls == [("b:5555", "youtubetv:fox")]          # only the preload's own tune


def test_missing_seek_helper_fails_closed_without_presses(scrub, monkeypatch):
    c, tuner, hid, calls, result, behind = scrub
    monkeypatch.setitem(sys.modules, "redzone.tuners.yttv_seek", None)   # import raises ImportError
    import redzone.tuners as _tuners
    monkeypatch.delattr(_tuners, "yttv_seek", raising=False)
    run(_preload(c, "youtubetv:fox"))
    assert not c.hidden_ready and not any("DPAD" in x for x in hid.log)
    assert tuner.calls == [("b:5555", "youtubetv:fox")]          # still on its own live tune: no re-tune


def test_off_by_default_and_per_flag(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": False}}})
    assert not c._scrub_wanted("youtubetv:fox")
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": True}}})
    c.cfg.update({"yttv_seek": {"enabled": False}})
    assert not c._scrub_wanted("youtubetv:fox")
    c.cfg.update({"yttv_seek": {"enabled": True}})
    assert c._scrub_wanted("youtubetv:fox") and not c._scrub_wanted("prime:abc")
    c.scrub_of = lambda target: False          # the engine says this league is off
    assert not c._scrub_wanted("youtubetv:fox")


def test_shipped_default_is_off(cfg):
    assert cfg.defaults["timing"]["behind_live_s"]["scrub_entry"] is False
    assert cfg.defaults["timing"]["behind_live_s"]["scrub_fallback"] == "keep"


def test_deadline_prices_the_scrub_until_the_standby_is_scrubbed(scrub):
    c, tuner, hid, calls, result, behind = scrub
    plain = c.deadline_cost("youtubetv:fox")
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": False}}})
    assert plain - c.deadline_cost("youtubetv:fox") == pytest.approx(28.0)
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": True}}})
    run(_preload(c, "youtubetv:fox"))
    assert c.deadline_cost("youtubetv:fox") == pytest.approx(c._flip_upper())


def test_scrubbed_anchor_expires(monkeypatch):
    s = CrawlStick("x:5555")
    s.remember("youtubetv:fox", {"state": "playing", "pkg": "p", "position": 1, "updated": 1}, scrubbed_s=67.0)
    pb = {"state": "playing", "pkg": "p", "position": 2, "updated": 900_000}
    assert s.still_on("youtubetv:fox", pb)
    assert not s.still_on("youtubetv:fox", {**pb, "state": "paused"})
    now = time.monotonic()
    monkeypatch.setattr(adb.time, "monotonic", lambda: now + adb.SCRUB_TRUST_S + 1)
    assert s.scrubbed("youtubetv:fox") is None and not s.still_on("youtubetv:fox", pb)


def test_rebuild_uses_the_scrub_never_a_pause(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": False}}})
    c.behind_of = lambda target: 0.2
    run(_preload(c, "youtubetv:fox"))       # buffered the old way (a pause)
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": True}}})
    c.behind_of = lambda target: 60.0
    hid.log.clear()
    run(c._rebuild_buffer(hid, "youtubetv:fox", 0.0, -60.0))
    assert len(calls) == 1 and "KEYCODE_MEDIA_PAUSE" not in hid.log and c.hidden_ready
    assert c.held["youtubetv:fox"] == 67.0


def test_an_unmeasured_estimate_is_never_trusted(scrub):
    c, tuner, hid, calls, result, behind = scrub
    r = Res(behind_s=60.0)
    r.measured = False                       # item 1: behind_before + presses x 15 s, no label read
    result["r"] = r
    run(_preload(c, "youtubetv:fox"))
    assert not c.hidden_ready and "youtubetv:fox" not in c.held


def test_a_measured_read_goes_to_the_delay_model_and_a_stale_one_redoes_the_hidden_stick(scrub):
    c, tuner, hid, calls, result, behind = scrub
    labels = []
    c.on_label = lambda t, b, sd, ts: labels.append((t, b))
    run(_preload(c, "youtubetv:fox"))
    assert labels == [("youtubetv:fox", 67.0)]

    async def stale():
        assert c.label_stale("youtubetv:fox")
        assert not c.hidden_ready             # no flip trusts the stale offset meanwhile
        await c._preload_task
    run(stale())
    assert len(calls) == 2 and c.hidden_ready and tuner.calls[-1] == ("b:5555", "youtubetv:fox")
    assert not c.label_stale("youtubetv:espn")   # not the hidden stick's target: nothing pressed


def test_a_scrubbed_standby_is_protected_from_preloads_until_the_flip(scrub):
    """Nick's live test, Nov 7: the app's preload overwrote a scrubbed hidden stick."""
    c, tuner, hid, calls, result, behind = scrub
    run(_preload(c, "youtubetv:fox"))
    c._last_preload = 0.0
    assert not c.preload("youtubetv:espn")                       # the planner's pick waits
    assert not c.preload("youtubetv:espn", refresh=True)
    assert c.hidden_target == "youtubetv:fox" and c.hidden_ready
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))   # the flip uses it
    assert out.ok and len(calls) == 1
    # flag off: no protection (today's behavior)
    c2_flag = {"timing": {"behind_live_s": {"scrub_entry": False}}}
    c.cfg.update(c2_flag)
    assert not c._scrub_protected("youtubetv:espn")


def test_a_game_opened_on_the_phone_still_replaces_it(scrub):
    c, tuner, hid, calls, result, behind = scrub
    run(_preload(c, "youtubetv:fox"))

    async def peek():
        assert c.preload("youtubetv:espn", user=True)
        await c._preload_task
    run(peek())
    assert c.hidden_target == "youtubetv:espn"


def test_unavailable_screen_backoff(scrub):
    from redzone.tuners.tuners import TuneResult
    c, *_ = scrub
    res = TuneResult(False, "prime:x", 1.0, ["stopped at an account, payment, or unavailable-content screen"], "")
    assert c._preload_backoff(res) == ctl.PRELOAD_BACKOFF_S          # off by default
    c.cfg.update({"two_stick": {"unavailable_backoff_s": 900}})
    assert c._preload_backoff(res) == 900
    assert c._preload_backoff(TuneResult(False, "prime:x", 1.0, [], "video never started")) == ctl.PRELOAD_BACKOFF_S


def test_label_probe_is_armed_and_shares_the_overlay_reader(scrub, monkeypatch):
    """[label reader] with a LabelProbe wired, enter_buffer gets its read_overlay + read_behind and the stick is
    armed at the live edge first (the probe there becomes the at-live anchor)."""
    c, tuner, hid, calls, result, behind = scrub
    seen = {}

    class Probe:
        armed = []

        def arm(self, stick, behind_before_s=0.0):
            self.armed.append(stick.host)
            return not getattr(stick, "behind_on", None)

        async def overlay_reader(self, stick):
            return None

        async def __call__(self, stick):
            return 67.0
    c.label_probe = Probe()
    mod = sys.modules["redzone.tuners.yttv_seek"]
    inner = mod.enter_buffer

    async def spy(stick, seconds, **kw):
        seen.update(kw)
        return await inner(stick, seconds, **kw)
    monkeypatch.setattr(mod, "enter_buffer", spy)
    run(_preload(c, "youtubetv:fox"))
    assert Probe.armed == ["b:5555"]
    assert seen["read_overlay"] == c.label_probe.overlay_reader and seen["read_behind"] is c.label_probe



def test_an_unarmed_probe_lets_the_scrub_run_unmeasured_and_fail_closed(scrub, monkeypatch):
    """#391: arm() refuses a stick still behind on a target -> no read_behind is passed -> no measured reading."""
    c, tuner, hid, calls, result, behind = scrub
    seen = {}

    class Probe:
        def arm(self, stick, behind_before_s=0.0):
            return False

        async def overlay_reader(self, stick):
            return None

        async def __call__(self, stick):
            raise AssertionError("never read when not armed")
    c.label_probe = Probe()
    mod = sys.modules["redzone.tuners.yttv_seek"]
    inner = mod.enter_buffer

    async def spy(stick, seconds, **kw):
        seen.update(kw)
        return await inner(stick, seconds, **kw)
    monkeypatch.setattr(mod, "enter_buffer", spy)
    run(_preload(c, "youtubetv:fox"))
    assert "read_behind" not in seen and "read_overlay" not in seen
def test_no_other_channel_means_no_live_claim(scrub):
    """[Nick's 90 s run, Nov 7] With nothing to reset through, the stick is NOT re-tuned 'at live' on the same channel."""
    c, tuner, hid, calls, result, behind = scrub
    c.channels.codes = {"espn": c.channels.codes.get("espn", "x")}
    result["r"] = Res(ok=False, behind_s=None, reason="focus not on the scrub bar")
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and c.main.host == "a:5555"
    assert tuner.calls == [("b:5555", "youtubetv:espn")]         # only the first tune: no same-channel "reset"
    assert hid.behind_on == "youtubetv:espn" and not c.hidden_ready and "youtubetv:espn" not in c.held


def test_a_scrubbed_stick_is_marked_behind_and_a_different_channel_clears_it(scrub):
    c, tuner, hid, calls, result, behind = scrub
    run(_preload(c, "youtubetv:fox"))
    assert hid.behind_on == "youtubetv:fox"
    run(c._tune(hid, "youtubetv:espn"))
    assert hid.behind_on is None


@pytest.mark.parametrize("gap,slept", [(0, []), (3.0, [3.0])])
def test_relink_gap_between_the_reset_tune_and_the_tune_back(scrub, monkeypatch, gap, slept):
    """tuners.relink_gap_s (Nick Nov 7: back-to-back tunes failed 1/10 'video never started'; 3 s fixed it). Off: 0."""
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"tuners": {"relink_gap_s": gap}})
    seen = []
    real_sleep = asyncio.sleep

    async def sleep(s, *a, **k):
        seen.append(s)
        await real_sleep(0)
    monkeypatch.setattr(ctl.asyncio, "sleep", sleep)
    hid.behind_on = "youtubetv:espn"
    run(c._tune(hid, "youtubetv:espn"))
    assert tuner.calls[-2:] == [("b:5555", "youtubetv:fox"), ("b:5555", "youtubetv:espn")]
    assert [s for s in seen if s == gap and s > 0] == slept
