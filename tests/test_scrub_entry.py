"""[scrub entry, item 4 of Nick's Nov 6 order] Seek the hidden stick into the buffer by the overlay scrub BEFORE
the flip, then flip at a dead ball on the delayed picture. A pause doesn't hold on YouTube TV, and media_session's
position crawls after a seek, so the overlay label's reading is the only offset trusted. Fails closed; ships off."""
import asyncio
import json
import sys
import time
import types

import pytest

from redzone import control as ctl
from redzone.noggin import gamequeue as gq
from redzone.tuners import adb
from redzone.tuners.join import plan_join
from redzone.tuners.tuners import TuneResult
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


# ---- [queue, WP-13] typed failures and budgets: every failure answers inside its budget ---------------------------

BUDGET = 0.08      # the test's start and scrub budgets; a hung step must end here, not after 75 s
SLACK = 0.5        # scheduling room on a loaded runner: still far below any real wait this guards
WALL = "stopped at an account, payment, or unavailable-content screen"


class FaultTuner:
    """Fails the way the real tuners do: an error string, screen steps, or a tune that never comes back."""
    def __init__(self, ok=True, error="", steps=(), hang=False, warm=None):
        self.ok, self.error, self.steps, self.hang, self.warm, self.calls = ok, error, list(steps), hang, warm, []

    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append(target)
        if self.hang:
            await asyncio.sleep(60)
        if self.ok:
            stick.remember(target, await stick.playback(stick.packages[target.split(":")[0]]))
        return TuneResult(self.ok, target, 4.0, self.steps + ["playing"], self.error, warm=self.warm)


@pytest.fixture
def faults(scrub, tmp_path, monkeypatch):
    c, _, hid, calls, result, _ = scrub
    c.cfg.data["queue"] = {"enabled": True, "scrub_budget_s": BUDGET,
                           "start_budget_s": {"youtubetv": BUDGET, "youtubetv:search": BUDGET, "prime": BUDGET}}
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    moves = []
    c.on_queue_move = lambda target, move, host=None: moves.append(move)

    def use(app, tuner):
        monkeypatch.setitem(ctl.TUNERS, app, tuner)
        return tuner
    return c, hid, use, moves, calls, result


def _rows(c, stage=None):
    if not c.buffer_log_path.exists():
        return []
    rows = [r for r in map(json.loads, c.buffer_log_path.read_text().splitlines()) if r["kind"] == "stage"]
    return [r for r in rows if stage is None or r["stage"] == stage]


def _hang_scrub(stick, seconds, **kw):
    async def never():
        await asyncio.sleep(60)
    return never()


FAULTS = [   # id, failure, step, app, tuner, enter_buffer, attempts, bound, off (s), retry (s)
    ("stick_hung", gq.Failure.STICK_HUNG, gq.Step.WAIT_STICK, "youtubetv",
     dict(ok=False, error=f"{adb.STICK_HUNG}: b:5555, retry in 5s"), None, 1, 0.0, None, None),
    ("link_refused", gq.Failure.LINK_REFUSED, gq.Step.NEXT_GAME, "youtubetv",
     dict(ok=False, error="youtubetv couldn't open the link"), None, 1, 0.0, ctl.ROUTE_OFF_S, None),
    ("walled", gq.Failure.WALLED, gq.Step.STOP, "prime",
     dict(ok=False, error="video never started", steps=[WALL]), None, 1, 0.0, ctl.ROUTE_OFF_S, None),
    ("blackout", gq.Failure.BLACKOUT, gq.Step.NEXT_GAME, "prime",
     dict(ok=False, error="video never started", steps=["blackout: not in your area"]), None, 1, 0.0, ctl.ROUTE_OFF_S, None),
    ("never_started_yttv_retries_once", gq.Failure.NEVER_STARTED, gq.Step.NEXT_GAME, "youtubetv",
     dict(ok=False, hang=True), None, 2, 2 * BUDGET, 120.0, None),
    ("never_started_prime_no_retry", gq.Failure.NEVER_STARTED, gq.Step.NEXT_GAME, "prime",
     dict(ok=False, hang=True), None, 1, BUDGET, 120.0, None),
    ("no_route", gq.Failure.NO_ROUTE, gq.Step.NEXT_GAME, "youtubetv",
     dict(ok=False, error="channel link not set up yet"), None, 1, 0.0, None, None),
    ("unknown", gq.Failure.UNKNOWN, gq.Step.NEXT_GAME, "youtubetv",
     dict(ok=False, error="youtubetv app not found on b"), None, 1, 0.0, 120.0, None),
    ("scrub_banner_miss", gq.Failure.SCRUB_BANNER_MISS, gq.Step.STAY_LIVE, "youtubetv", {},
     Res(ok=False, behind_s=None, reason="focus guard: no scrub label after the LEFT"), None, BUDGET, None, 30.0),
    ("scrub_hangs_at_budget", gq.Failure.SCRUB_FAILED, gq.Step.STAY_LIVE, "youtubetv", {},
     _hang_scrub, None, BUDGET, None, 120.0),
]


@pytest.mark.parametrize("name,failure,step,app,tune,enter,attempts,bound,off,retry", FAULTS, ids=[f[0] for f in FAULTS])
def test_fault_table_each_failure_advances_inside_its_budget(faults, name, failure, step, app, tune, enter, attempts,
                                                             bound, off, retry):
    c, hid, use, moves, calls, result = faults
    tuner = use(app, FaultTuner(**tune))
    target = "prime:gti" if app == "prime" else "youtubetv:fox"
    if callable(enter):
        import redzone.tuners as _t
        _t.yttv_seek.enter_buffer = enter
    elif enter is not None:
        result["r"] = enter
    c._last_preload = time.time()

    async def go():
        assert c.preload(target, urgent=True)
        t0 = time.monotonic()
        await c._preload_task
        return time.monotonic() - t0
    took = run(go())

    assert took <= bound + SLACK, f"{name}: {took:.2f}s against a {bound}s budget"
    assert [m.failure for m in moves] == [failure] and moves[0].step == step
    if attempts is not None:
        assert len(tuner.calls) == attempts
    left = c._failed.get(target, 0.0) - time.time()
    assert (left <= 0) if off is None else (off - 5 < left <= off)
    if retry is not None:
        assert retry - 5 < c._scrub_backoff[target] - time.time() <= retry
        assert not c.hidden_ready and "youtubetv:fox" not in c.held   # R3: at the live edge, never called buffered
    if moves[0].advance:
        assert c._last_preload == 0.0                                 # the queue may pick again at once
    assert [r["failure"] for r in _rows(c, "failure")] == [failure.value]
    assert not c.standby_m.illegal and all(h[3] for h in c.standby_m.history)   # every move says why


@pytest.mark.parametrize("hang", [False, True], ids=["retune_ok", "retune_hangs_at_budget"])
def test_fault_table_hold_failed_retunes_once_inside_a_tune_budget(faults, hang):
    c, hid, use, moves, *_ = faults
    tuner = use("prime", FaultTuner())

    async def go():
        assert c.preload("prime:gti")
        await c._preload_task
        tuner.calls.clear()
        tuner.hang = hang
        t0 = time.monotonic()
        await c._hold_standby(hid, "prime:gti")      # the fake stick never reports paused: the hold is refused
        return time.monotonic() - t0
    took = run(go())

    assert took <= BUDGET + SLACK and tuner.calls == ["prime:gti"]
    assert [(m.failure, m.step) for m in moves] == [(gq.Failure.HOLD_FAILED, gq.Step.RETUNE)]
    assert c.hidden_ready is (not hang) and c.hidden_target == (None if hang else "prime:gti")
    assert not c.standby_m.illegal and all(h[3] for h in c.standby_m.history)


def test_fault_table_second_hold_failure_stays_live_without_another_tune(faults):
    c, hid, use, moves, *_ = faults
    tuner = use("prime", FaultTuner())

    async def go():
        assert c.preload("prime:gti")
        await c._preload_task
        await c._hold_standby(hid, "prime:gti")
        tuner.calls.clear()
        await c._hold_standby(hid, "prime:gti")
    run(go())
    assert tuner.calls == [] and moves[-1].step == gq.Step.STAY_LIVE and moves[-1].advance


def test_fault_table_hold_retune_of_a_stick_behind_live_goes_through_another_channel(faults):
    """A same-channel link does not reset a stick that is behind live, so `held` must not read 0 after one."""
    c, hid, use, moves, *_ = faults
    tuner = use("youtubetv", FaultTuner())

    async def go():
        assert c.preload("youtubetv:fox")
        await c._preload_task
        assert c.held["youtubetv:fox"] == 67.0
        tuner.calls.clear()
        await c._hold_standby(hid, "youtubetv:fox")
    run(go())
    assert tuner.calls == ["youtubetv:espn", "youtubetv:fox"] and "youtubetv:fox" not in c.held


def test_a_channel_named_regional_is_not_a_blackout(faults):
    c, hid, use, moves, *_ = faults
    use("youtubetv", FaultTuner(ok=False, error="video never started", steps=["opened Regional Sports Net"]))
    run(_preload(c, "youtubetv:fox"))
    assert moves[0].failure == gq.Failure.NEVER_STARTED


def test_fault_table_covers_every_failure_type():
    assert {f[1] for f in FAULTS} | {gq.Failure.HOLD_FAILED} == set(gq.Failure)


def test_second_banner_miss_in_a_row_is_a_real_scrub_failure(faults):
    c, hid, use, moves, calls, result = faults
    use("youtubetv", FaultTuner())
    result["r"] = Res(ok=False, behind_s=None, reason="focus guard: no scrub label")
    for _ in range(2):
        c._scrub_backoff.clear()
        c._set_hidden(None, False, why="test: next preload")
        c._last_preload = 0.0
        run(_preload(c, "youtubetv:fox"))
    assert [m.failure for m in moves] == [gq.Failure.SCRUB_BANNER_MISS, gq.Failure.SCRUB_FAILED]


def test_a_purchase_screen_is_walled_even_when_it_mentions_a_blackout(faults):
    c, hid, use, moves, *_ = faults
    tuner = use("prime", FaultTuner(ok=False, error="video never started", steps=["blackout word", WALL]))
    run(_preload(c, "prime:gti"))
    assert moves[0].failure == gq.Failure.WALLED and not moves[0].press_ok and len(tuner.calls) == 1
    assert "sign-in or purchase" in c.note


def test_queue_on_scrub_sends_the_join_plans_presses(faults):
    c, hid, use, moves, calls, result = faults
    use("youtubetv", FaultTuner())
    seen = {}

    async def enter(stick, seconds, **kw):
        seen.update(seconds=seconds, presses=kw.get("presses"))
        return Res()
    import redzone.tuners as _t
    _t.yttv_seek.enter_buffer = enter
    run(_preload(c, "youtubetv:fox"))
    plan = plan_join("youtubetv:fox", 60.0, 0.0, cfg=c.cfg)
    assert plan.presses > 0 and seen == {"seconds": plan.target_s, "presses": plan.presses}
    assert c.hidden_ready and c.held["youtubetv:fox"] == 67.0        # the label reading is still the only truth


def test_stage_rows_carry_the_warm_flag_and_the_scrub_split(faults):
    c, hid, use, moves, calls, result = faults
    use("youtubetv", FaultTuner(warm=True))
    res = Res()
    res.reads, res.presses, res.retries = [{"what": "open", "s": 1.5}, {"what": "label", "s": 2.5}], 3, 0
    result["r"] = res
    run(_preload(c, "youtubetv:fox"))
    tune = _rows(c, "tune")[0]
    assert tune["ok"] and tune["warm"] is True and tune["budget_s"] == BUDGET and tune["stick"] == "HDMI2"
    scrub = _rows(c, "scrub")[0]
    assert scrub["read_s"] == 4.0 and scrub["reads"] == res.reads and scrub["presses"] == 3 and scrub["keys_s"] >= 0
    assert c.latency.median("youtubetv:warm") == 4.0 and c.latency.median("scrub_read") == 4.0
    assert moves == []


def test_budget_miss_gets_its_own_row(faults):
    c, hid, use, *_ = faults
    use("prime", FaultTuner(ok=False, hang=True))
    run(_preload(c, "prime:gti"))
    miss = _rows(c, "budget_miss")
    assert [(r["what"], r["budget_s"]) for r in miss] == [("tune", BUDGET)]


def test_flag_off_matches_todays_calls_with_no_budget_and_no_rows(faults):
    c, hid, use, moves, calls, result = faults
    c.cfg.data["queue"]["enabled"] = False
    tuner = use("youtubetv", FaultTuner())
    seen = {}

    async def enter(stick, seconds, **kw):
        seen.update(seconds=seconds, kw=set(kw))
        await asyncio.sleep(BUDGET * 3)                 # past the budget: nothing may cut it short
        return Res()
    import redzone.tuners as _t
    _t.yttv_seek.enter_buffer = enter
    run(_preload(c, "youtubetv:fox"))
    assert seen["seconds"] == 52.0 and "presses" not in seen["kw"]
    assert c.hidden_ready and c.held["youtubetv:fox"] == 67.0 and tuner.calls == ["youtubetv:fox"]
    assert moves == [] and _rows(c) == []


def test_flag_off_failed_preload_keeps_the_old_backoff_and_one_attempt(faults):
    c, hid, use, moves, *_ = faults
    c.cfg.data["queue"]["enabled"] = False
    tuner = use("prime", FaultTuner(ok=False, error="video never started", steps=[WALL]))
    run(_preload(c, "prime:gti"))
    left = c._failed["prime:gti"] - time.time()
    assert 115 < left <= 120 and len(tuner.calls) == 1 and moves == [] and _rows(c) == []
