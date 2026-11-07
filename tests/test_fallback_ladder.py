"""[fallback ladder, Nick's Nov 7 sync order item 2] buffered -> live -> other stick -> hold, per stick, each step
logged and on the phone; flag fallback_ladder.enabled (off). The integration tests replay the real Nov 5-7 failures
(docs/memory/runbooks/sync-fallback.md). Rule #1 is unchanged: every flip still waits for may_cut."""
import asyncio
import json

import pytest

from redzone import fallback_ladder as fl
from redzone.tuners.tuners import TuneResult
from tests.test_control import FakeStick  # noqa: F401
from tests.test_scrub_entry import Res, scrub, two  # noqa: F401  (fixtures)


def run(coro):
    return asyncio.run(coro)


class Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def ok(**kw):
    async def f():
        return fl.Step(True, **kw)
    return f


def bad(why="x"):
    async def f():
        return fl.Step(False, why)
    return f


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def lad(tmp_path=None, **cfg):
    c = Cfg({"fallback_ladder.enabled": True, **{f"fallback_ladder.{k}": v for k, v in cfg.items()}})
    clock = Clock()
    return fl.FallbackLadder(c, tmp_path / "ladder.jsonl" if tmp_path else None, clock=clock,
                             label=lambda h: "Stick on HDMI2"), clock


# --- the ladder itself ------------------------------------------------------------------------------------------

@pytest.mark.playbook("LAD-01")
def test_climbs_in_order_and_stops_at_the_first_rung_that_works(tmp_path):
    L, _ = lad(tmp_path)
    seen = []

    def track(name, fn):
        async def f():
            seen.append(name)
            return await fn()
        return f
    c = run(L.climb("b", "youtubetv:espn", {"buffered": track("buffered", bad("no label read")),
                                            "live": track("live", ok()),
                                            "other_stick": track("other_stick", ok())}))
    assert seen == ["buffered", "live"] and c.rung == "live" and c.ok and not c.shows_buffered
    assert c.text() == "Stick on HDMI2: buffered failed (no label read) -> live edge ok"
    row = json.loads((tmp_path / "ladder.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "ladder" and row["rung"] == "live" and row["fell_back"] and not row["buffered"]
    assert "100." not in json.dumps(row) and "b:5555" not in json.dumps(row)   # public repo: no addresses


@pytest.mark.playbook("LAD-02")
def test_buffered_is_never_shown_without_a_measured_position():
    L, _ = lad()
    c = run(L.climb("b", "t", {"buffered": ok(behind_s=60.0, measured=False), "live": ok()}))
    assert c.rung == "live" and not c.shows_buffered and c.attempts[0]["why"] == "position not measured"
    c = run(L.climb("b", "t", {"buffered": ok(behind_s=61.4, measured=True)}))
    assert c.rung == "buffered" and c.shows_buffered and c.behind_s == 61.4


@pytest.mark.playbook("LAD-03")
def test_every_rung_failing_ends_on_hold_and_a_crash_is_a_failure():
    L, _ = lad()

    async def boom():
        raise RuntimeError("adb drop")
    c = run(L.climb("b", "t", {"buffered": boom, "live": bad("video never started"),
                               "other_stick": bad("not for this app")}))
    assert c.rung == "hold" and not c.ok and [a["rung"] for a in c.attempts] == ["buffered", "live", "other_stick",
                                                                                  "hold"]
    assert c.attempts[0]["why"].startswith("crashed: adb drop")
    assert "kept the current game" in c.text()


@pytest.mark.playbook("LAD-04")
def test_a_rung_that_keeps_failing_on_one_stick_is_skipped_there_for_a_while():
    """No key storms: the Nov 7 label reader failed on every tune while the post-tune banner covered the overlay."""
    L, clock = lad(skip_after=2, cooldown_s=300)
    calls = []

    async def scrub():
        calls.append(1)
        return fl.Step(False, "no label read")
    for _ in range(2):
        run(L.climb("b", "t", {"buffered": scrub, "live": ok()}))
    c = run(L.climb("b", "t", {"buffered": scrub, "live": ok()}))
    assert len(calls) == 2 and c.attempts[0]["skipped"] and c.rung == "live"
    assert run(L.climb("a", "t", {"buffered": scrub, "live": ok()})).attempts[0].get("skipped") is None  # per stick
    v = L.phone_view()
    assert v["cooling"] == [{"stick": "Stick on HDMI2", "rung": "buffered", "s": 300}]
    clock.t += 301
    run(L.climb("b", "t", {"buffered": scrub, "live": ok()}))
    assert len(calls) == 4


def test_descend_starts_below_the_rung_that_was_lost_and_record_logs_one_step(tmp_path):
    L, _ = lad(tmp_path)
    c = run(L.descend("b", "t", {"buffered": ok(behind_s=60, measured=True), "live": ok()}, "buffered", "31 s off"))
    assert c.rung == "live" and c.reason == "buffered lost: 31 s off" and len(c.attempts) == 1
    L.record("b", "t", "buffered", True, behind_s=60.2, measured=True)
    rows = [json.loads(x) for x in (tmp_path / "ladder.jsonl").read_text().splitlines()]
    s = fl.summarize(rows)
    assert s["joins"] == 2 and s["by_rung"]["live"] == 1 and s["by_rung"]["buffered"] == 1
    assert s["buffered_measured"] == 1 and s["fell_back"] == 0


def test_phone_view_shows_the_last_fallback_for_15_minutes_only():
    L, clock = lad()
    run(L.climb("b", "t", {"buffered": bad("focus lost"), "live": ok()}))
    assert "focus lost" in L.phone_view()["last"]["text"]
    clock.t += 901
    assert L.phone_view()["last"] is None


def test_one_fallbacks_row_per_step_for_the_night_report(tmp_path):
    L, _ = lad(tmp_path)
    L.steps_path = tmp_path / "fallbacks.jsonl"
    run(L.climb("b", "youtubetv:espn", {"buffered": bad("no label read"), "live": ok()}))
    rows = [json.loads(x) for x in L.steps_path.read_text().splitlines()]
    assert [(r["step"], r["ok"]) for r in rows] == [("buffered", False), ("live", True)]
    assert set(rows[0]) >= {"ts", "stick", "target", "step", "why"} and rows[0]["why"] == "no label read"
    assert rows[0]["stick"] == "Stick on HDMI2"


def test_a_rung_can_be_turned_off():
    L, _ = lad(skip_rungs=["live"])
    c = run(L.climb("b", "t", {"buffered": bad("no label read"), "live": ok(), "other_stick": ok()}))
    assert c.rung == "other_stick" and [a["rung"] for a in c.attempts] == ["buffered", "other_stick"]


def test_off_by_default():
    L = fl.FallbackLadder(Cfg())
    assert not L.enabled


# --- in the controller: the real Nov 5-7 failures --------------------------------------------------------------

@pytest.fixture
def lscrub(scrub):  # noqa: F811
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.defaults.setdefault("fallback_ladder", {"enabled": False})
    c.cfg.update({"fallback_ladder": {"enabled": True, "other_stick_apps": ["youtubetv"]}})
    return scrub


@pytest.mark.playbook("LAD-05")
def test_nov7_no_label_read_falls_to_the_live_edge_and_still_waits_for_a_dead_ball(lscrub):
    """Nov 7 (#385): the post-tune LIVE banner hid the scrubber, no label was read. Before: 'buffer entry failed;
    kept the current game' (scrub_fallback keep). Ladder: the same stick at the live edge, flipped at a dead ball."""
    c, tuner, hid, calls, result, behind = lscrub
    result["r"] = Res(ok=False, behind_s=None, reason="banner: no scrubber")
    cuts = []

    def may_cut():
        cuts.append(c.held.get("youtubetv:espn", 0.0))
        return True
    out = run(c.execute("youtubetv:espn", may_cut=may_cut))
    assert out.ok and c.main is hid and out.provenance["control"]["ladder"] == "live"
    assert cuts and all(h == 0.0 for h in cuts)          # the safe-moment check ran on the live (B=0) picture
    assert "youtubetv:espn" not in c.held or c.held["youtubetv:espn"] == 0.0
    last = c.ladder.recent[-1]
    assert last.rung == "live" and not last.shows_buffered and "banner" in last.text()


@pytest.mark.playbook("LAD-05")
def test_no_dead_ball_after_a_fallback_keeps_the_current_game(lscrub):
    c, tuner, hid, calls, result, behind = lscrub
    result["r"] = Res(ok=False, behind_s=None, reason="no label read")
    import redzone.control as ctl
    old = ctl.MAX_SAFE_WAIT_S
    ctl.MAX_SAFE_WAIT_S = 0.05
    try:
        out = run(c.execute("youtubetv:espn", may_cut=lambda: False))
    finally:
        ctl.MAX_SAFE_WAIT_S = old
    assert not out.ok and out.screen_unchanged and c.main.host == "a:5555"   # Rule #1: never flipped


@pytest.mark.playbook("LAD-06")
def test_nov7_hidden_tune_fails_then_the_visible_stick_tunes_in_place_at_a_dead_ball(lscrub, monkeypatch):
    """'video never started' on the standby: the other stick (YouTube TV only), in place, after may_cut."""
    c, tuner, hid, calls, result, behind = lscrub
    orig = tuner.tune
    seq = []

    async def tune(stick, ref, target, may_cut=None):
        seq.append(stick.host)
        if stick.host == "b:5555":
            return TuneResult(False, target, 0.01, [], "video never started")
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and out.path == "single" and "fallback: other stick" in out.detail
    assert seq == ["b:5555", "a:5555"] and c.on_target == "youtubetv:espn"
    steps = [m for m, _ in out.marks]
    assert steps.index("safe_done") < len(steps) - steps[::-1].index("tune_start") - 1   # tune after the safe wait
    rungs = [(r.rung, r.ok) for r in c.ladder.recent]
    assert rungs == [("other_stick", True), ("other_stick", True)]   # the choice, then its settled result


@pytest.mark.playbook("LAD-07")
def test_nov7_unavailable_prime_game_holds_and_is_not_tuned_on_screen(lscrub, monkeypatch):
    """Nick's live test, Nov 7: the standby kept choosing a Prime game whose page said UNAVAILABLE. A Prime target is
    never handed to the visible stick (a ~32 s tune on screen): hold, screen unchanged."""
    c, tuner, hid, calls, result, behind = lscrub
    from redzone import control as ctl

    class PrimeFail:
        calls = []

        async def tune(self, stick, ref, target, may_cut=None):
            self.calls.append(stick.host)
            return TuneResult(False, target, 0.01, ["detail page"], "UNAVAILABLE on this account")
    p = PrimeFail()
    monkeypatch.setitem(ctl.TUNERS, "primevideo", p)
    for s in c.sticks:
        s.packages["primevideo"] = "prime.pkg"
    out = run(c.execute("primevideo:game1", may_cut=lambda: True))
    assert not out.ok and out.screen_unchanged and c.main.host == "a:5555" and p.calls == ["b:5555"]
    last = c.ladder.recent[-1]
    assert last.rung == "hold" and "UNAVAILABLE" in last.text() and "not for this app" in last.text()


@pytest.mark.playbook("LAD-08")
def test_nov7_preload_never_overwrites_a_scrubbed_standby(lscrub):
    """Nick's Nov 7 run: a planner preload overwrote a scrubbed hidden stick. With the ladder on it still can't."""
    c, tuner, hid, calls, result, behind = lscrub

    async def go():
        assert c.preload("youtubetv:fox")
        await c._preload_task
        assert not c.preload("youtubetv:espn")
    run(go())
    assert c.hidden_target == "youtubetv:fox" and hid.scrubbed("youtubetv:fox") == 67.0
    assert c.ladder.recent[-1].rung == "buffered" and c.ladder.recent[-1].shows_buffered


@pytest.mark.playbook("LAD-09")
def test_nov7_failed_scrub_resets_through_another_channel_before_the_live_rung(lscrub):
    """Nick's 90 s run, Nov 7 (#391): a same-channel deep link does not reset a stick that is behind. The live rung
    is a fresh tune that went through another channel, never a picture still behind live."""
    c, tuner, hid, calls, result, behind = lscrub
    result["r"] = Res(ok=False, behind_s=None, reason="focus not on the scrub bar")

    async def go():
        assert c.preload("youtubetv:espn")
        await c._preload_task
    run(go())
    assert tuner.calls == [("b:5555", "youtubetv:espn"), ("b:5555", "youtubetv:fox"), ("b:5555", "youtubetv:espn")]
    assert c.hidden_ready and c.hidden_target == "youtubetv:espn" and hid.behind_on is None
    assert c.ladder.recent[-1].rung == "live"


@pytest.mark.playbook("LAD-10")
def test_label_reader_failing_twice_on_a_stick_stops_pressing_keys_there(lscrub):
    c, tuner, hid, calls, result, behind = lscrub
    result["r"] = Res(ok=False, behind_s=None, reason="no label read")
    c.cfg.update({"timing": {"behind_live_s": {"scrub_retry_s": 0}}})
    for t in ("youtubetv:espn", "youtubetv:fox", "youtubetv:espn"):
        c._set_hidden(None, False)

        async def go(t=t):
            assert c.preload(t, user=True)
            await c._preload_task
        run(go())
    assert len(calls) == 2                                    # the third preload never scrubbed
    assert c.ladder.recent[-1].attempts[0].get("skipped") and c.hidden_ready
    view = c.ladder.phone_view()
    assert view["cooling"] and view["cooling"][0]["rung"] == "buffered"


@pytest.mark.playbook("LAD-11")
def test_watchdog_descend_on_screen_drops_the_buffered_claim_when_nothing_else_works(lscrub):
    """Item 1's interface: the label read after entry is off target and the re-seek didn't fix it. With no safe
    moment and no standby, the picture stays but is no longer shown (or synced) as buffered."""
    c, tuner, hid, calls, result, behind = lscrub
    run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert c.held.get("youtubetv:espn") == 67.0 and c.on_target == "youtubetv:espn"
    import redzone.control as ctl
    old = ctl.MAX_SAFE_WAIT_S
    ctl.MAX_SAFE_WAIT_S = 0.05
    c.sticks[0].packages.pop("youtubetv")                     # the other stick can't play it
    try:
        climb = run(c.ladder_descend("youtubetv:espn", "buffered", "label 31 s off target", may_cut=lambda: False))
    finally:
        ctl.MAX_SAFE_WAIT_S = old
    assert climb.rung == "hold" and not climb.shows_buffered
    assert c.held.get("youtubetv:espn", 0.0) == 0.0


def test_watchdog_descend_is_a_no_op_with_the_flag_off(scrub):  # noqa: F811
    c = scrub[0]
    assert run(c.ladder_descend("youtubetv:espn", "buffered", "x")) is None


def test_flag_off_keeps_todays_scrub_behavior(scrub):  # noqa: F811
    c, tuner, hid, calls, result, behind = scrub
    result["r"] = Res(ok=False, behind_s=None, reason="focus not on the scrub bar")
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and "buffer entry failed" in out.detail and not c.ladder.recent
