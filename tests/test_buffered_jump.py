"""[buffered jump] Both sticks ~79 s behind live: the play-by-play holds every snap and whistle about to air, so the
editor cuts play by play (Nick Nov 7 20:45 ET; overseer Nov 8 01:50Z). Rule #1 both ways."""
from redzone.decider import buffered as bj
from redzone.decider.cuts import DeadTracker
from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored

EDGE = 35.0
BEHIND = 79.0
MU = EDGE + BEHIND


def sc(gid, score, league=League.CFB, **kw):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, None, None, 1.0, **kw)


def cands(**games):
    return {gid: Candidate(sc(gid, s), f"youtubetv:{gid}") for gid, s in games.items()}


def whistle(dt_or_tl, pid, t):
    dt = getattr(dt_or_tl, "dead", dt_or_tl)
    dt.add(pid, t, t, "rush", "run for 3", "Rush", None, 1)


def setup(cfg, clock, on=True, behind=BEHIND, park=False):
    cfg.update({"jump_mode": {"enabled": True}, "buffered_jump": {"enabled": on, "park": park}})
    delays = DelayBook({"youtubetv": EDGE}, sigma_default=15.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.deadline_cost = lambda target: 3.0
    for gid in ("a", "b"):
        for v in (EDGE - 0.5, EDGE, EDGE + 0.5):
            delays.calibrate(f"youtubetv:{gid}", v)
        delays.set_behind(f"youtubetv:{gid}", behind)
        delays.held[f"youtubetv:{gid}"] = behind   # the controller's scrubbed streams (control.held)
        dec.timeline(gid, League.CFB)._lags.append(15.0)
    dec.current = "a"
    return dec, delays


def scene(dec, clock, h_snap_after_landing=12.0, v_next_snap_in=40.0):
    """V (a): a play just finished on screen, next snap v_next_snap_in from now. H (b): its snap shows
    h_snap_after_landing after the slowest flip lands. Everything already in the play-by-play (it runs ahead)."""
    now = clock.t
    mu = dec.delays.get("youtubetv:a").mu
    x = now - mu                                          # real time a's picture shows now
    whistle(dec.timelines["a"], "a1", x - 15)             # a's play ended on screen 15 s ago (> 2 sigma + 3 s)
    dec.timelines["a"].events.append(Event(x - 15, "play", "play"))
    whistle(dec.timelines["a"], "a2", x + v_next_snap_in + 10)    # its next play: snap at x + v_next_snap_in
    whistle(dec.timelines["b"], "b1", x - 30)
    whistle(dec.timelines["b"], "b2", x + 3.2 + h_snap_after_landing + 10)
    for tl in dec.timelines.values():
        tl.fresh_at = now
        tl.dead.seen(now, 15.0)
    dec.since = now - 60


def test_editor_cuts_just_before_the_other_snap(cfg, clock):
    dec, _ = setup(cfg, clock)
    scene(dec, clock)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind == "jump" and d.target == "b", d.reason
    assert d.jump["why"] == "bj_snap_next" and d.jump["buffered"] and d.jump["behind_s"] == BEHIND
    assert 0 < d.jump["h_snap_s"] <= 8 + 2 * dec.delays.get("youtubetv:b").current_sigma(clock.t)


def test_stays_when_the_screen_game_snaps_first(cfg, clock):
    dec, _ = setup(cfg, clock)
    scene(dec, clock, h_snap_after_landing=30.0, v_next_snap_in=20.0)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind != "jump"
    assert dec.jump_holds["bj_own_snap_first"] + dec.jump_holds["bj_play_on_screen"] >= 1


def test_never_leaves_mid_play(cfg, clock):
    dec, _ = setup(cfg, clock)
    now = clock.t
    x = now - MU
    whistle(dec.timelines["a"], "a1", x + 4)    # a play on screen right now (snap x-6, whistle x+4)
    whistle(dec.timelines["b"], "b1", x - 30)
    whistle(dec.timelines["b"], "b2", x + 20)
    for tl in dec.timelines.values():
        tl.fresh_at = now
        tl.dead.seen(now, 15.0)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind != "jump"


def test_never_joins_mid_play(cfg, clock):
    dec, _ = setup(cfg, clock)
    scene(dec, clock)
    x = clock.t - MU
    dec.timelines["b"].dead.marks.clear()
    whistle(dec.timelines["b"], "b1", x + 5)   # b's play is on screen when its picture comes up
    whistle(dec.timelines["b"], "b2", x + 45)
    dec.timelines["b"].dead._sorted = dec.timelines["b"].dead._windows = None
    d = dec.decide(cands(a=60, b=50))
    assert d.kind != "jump"
    assert not dec.jump_lands_dead("b", cands(b=50)["b"], clock.t, 3.0)


def test_a_live_edge_standby_is_not_a_buffered_picture(cfg, clock):
    dec, delays = setup(cfg, clock)
    delays.set_behind("youtubetv:b", 0.0)   # its scrub failed: at the live edge
    scene(dec, clock)
    dec.decide(cands(a=60, b=50))
    assert dec.jump_holds.get("bj_standby_not_buffered", 0) >= 1 or dec.jump_holds.get("bj_snap_next", 0) == 0
    assert dec._bj_pic("b", cands(b=50)["b"], clock.t) is None


def test_flag_off_changes_nothing(cfg, clock):
    dec, _ = setup(cfg, clock, on=False)
    scene(dec, clock)
    dec.decide(cands(a=60, b=50))
    assert not any(k.startswith("bj_") for k in dec.jump_holds)
    assert dec._bj_pic("a", cands(a=60)["a"], clock.t) is None


def test_jump_next_keeps_the_buffered_partner(cfg, clock):
    dec, _ = setup(cfg, clock)
    scene(dec, clock)
    dec.timeline("c", League.CFB)
    c = cands(a=60, b=10, c=90)
    assert dec.jump_next(c, standby="youtubetv:b") == "b"    # ping-pong: no 60 s re-tune for a better score
    dec._bj_pair = (frozenset(("a", "b")), clock.t - 10_000)  # the pair has been on for ages: rotate
    assert dec.jump_next(c, standby="youtubetv:b") != "b" or dec.timelines["c"].dead.marks == {}


def test_editor_unit_long_dead_never_cuts_into_another_break():
    v, h = DeadTracker(League.NFL), DeadTracker(League.NFL)
    now, mu = 10_000.0, 100.0
    x = now - mu
    whistle(v, "v1", x - 10)
    whistle(v, "v2", x + 120)          # V: two minutes to its next snap (a TV timeout)
    whistle(h, "h1", x - 10)
    whistle(h, "h2", x + 200)          # H: even further (also in a break)
    for dt in (v, h):
        dt.seen(now, 15.0)
    V, H = bj.Pic(v, mu, 1.0), bj.Pic(h, mu, 1.0)
    assert not bj.cut_now(V, H, now).go
    h.marks.pop("h2")
    whistle(h, "h2", x + 30)            # H snaps in ~20 s: leave V's break for it
    h._sorted = h._windows = None
    c = bj.cut_now(V, H, now)
    assert c.go and c.why in ("long_dead", "snap_next")


def test_sim_runs_on_a_tiny_slate(tmp_path):
    import json
    import scripts.buffered_jump_sim as sim

    def game(gid, t0):
        plays = [{"id": f"{gid}{i}", "wallclock": _iso(t0 + 40 * i), "type": {"text": "Rush"}, "text": "run",
                  "period": {"number": 1}} for i in range(120)]
        return {"drives": {"previous": [{"plays": plays}]}}

    for gid, off in (("9001", 0), ("9002", 13), ("9003", 27)):
        (tmp_path / f"summary_{gid}.json").write_text(json.dumps(game(gid, 1_700_000_000 + off)))
    out = tmp_path / "o.json"
    assert sim.main(["--archive", str(tmp_path), "--json", str(out)]) == 0
    s = json.loads(out.read_text())["summary"]
    assert s["joins_mid_play"] == 0 and s["leaves_mid_play"] == 0
    assert s["plays_shown_per_min"] >= s["single_game_plays_per_min"] * 0.9


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_parked_standby_takes_the_next_dead_ball(cfg, clock):
    """[park] b's stick paused 5 s ago on the line-up park_lead_s before its snap: a's dead ball cuts to it."""
    dec, delays = setup(cfg, clock, park=True)
    scene(dec, clock)
    now, at = clock.t, clock.t - 5.0
    pic = dec._bj_pic("b", cands(b=50)["b"], now)
    lead = bj.park_lead_s(pic, cfg)
    x_f = at - MU                                      # b's frozen picture
    tl = dec.timelines["b"]
    tl.dead.marks.pop("b2")
    whistle(tl, "b2", x_f + lead + 10)                 # its snap: lead after the frozen picture
    tl.dead._sorted = tl.dead._windows = None
    dec.snap_parked = lambda t: None                   # not parked yet: the park mode waits for the park
    assert dec.decide(cands(a=60, b=50)).kind != "jump" and dec.jump_holds["bj_not_parked"] >= 1
    dec.snap_parked = lambda t: at if t == "youtubetv:b" else None
    assert dec.jump_lands_dead("b", cands(b=50)["b"], now, 3.0)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind == "jump" and d.target == "b" and d.jump["why"] == "bj_parked_snap", (d.reason, dict(dec.jump_holds))


def test_park_in_times_the_pause_from_the_logged_snap():
    dt = DeadTracker(League.NFL)
    now, mu = 10_000.0, 114.0
    x = now - mu
    whistle(dt, "p1", x - 10)
    whistle(dt, "p2", x + 40)          # next snap at real x + 30
    dt.seen(now, 15.0)
    h = bj.Pic(dt, mu, 1.0)
    lead = bj.park_lead_s(h)
    assert abs(bj.park_in(h, now) - (30 - lead)) < 1e-6
    later = bj.frozen(h, now + 30 - lead, now + 30 - lead)
    ok, snap = bj.lands(later, now + 30 - lead, 2.0, 3.2, 2.0, 2.0)
    assert ok and 0 < snap <= lead
    dt2 = DeadTracker(League.NFL)
    whistle(dt2, "q1", x - 10)         # the next snap isn't in the data yet: never park blind
    dt2.seen(now, 15.0)
    assert bj.park_in(bj.Pic(dt2, mu, 1.0), now) is None


def test_control_parks_rolls_and_counts_the_pause(cfg, tmp_path, monkeypatch):
    """The hidden stick: PAUSE at the planned moment, confirmed paused; the flip's roll sends PLAY and adds the pause
    to its behind-live (held + on_label), and verify_standby keeps a deliberate park."""
    import asyncio
    import time as _t

    from tests.test_control import FakeStick, FakeTuner, ctl, ChannelMap  # noqa: F401

    cfg.update({"mode": {"dry_run": False}, "jump_mode": {"enabled": True}, "buffered_jump": {"enabled": True}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    hid = c.hidden
    orig = hid.key

    async def key(*codes):
        if "KEYCODE_MEDIA_PAUSE" in codes:
            hid.playback_state = "paused"
        if "KEYCODE_MEDIA_PLAY" in codes:
            hid.playback_state = "playing"
        return await orig(*codes)
    hid.key = key
    labels = []
    c.on_label = lambda t, b, sd, ts: labels.append((t, b))
    target = "youtubetv:fox"

    async def go():
        hid.remember(target, await hid.playback("yttv.pkg"), scrubbed_s=79.0)
        c.held[target] = 79.0
        c._set_hidden(target, True)
        assert c.snap_park(target, 0.05)
        await c._snap_task
        at = c.snap_parked(target)
        assert at is not None and "KEYCODE_MEDIA_PAUSE" in hid.log and c.standby_ready(target)
        c._verified_at = 0.0
        await c.verify_standby()
        assert c.snap_parked(target) == at            # a deliberate park is kept (not STK-05's re-sync)
        await asyncio.sleep(0.2)
        async with c._lock:
            assert await c._snap_roll(hid, target)
        assert "KEYCODE_MEDIA_PLAY" in hid.log and c.snap_parked(target) is None
        assert 79.1 <= c.held[target] <= 79.0 + (_t.time() - at) + 0.2 and labels[-1][0] == target
        # a stick that is no longer paused can't be rolled as a park
        c._set_hidden(target, True)
        assert c.snap_park(target, 0.0)
        await c._snap_task
        hid.playback_state = "playing"
        async with c._lock:
            assert not await c._snap_roll(hid, target)
    asyncio.run(go())


def test_bootstrap_flips_from_a_live_edge_screen_onto_the_buffered_standby(cfg, clock):
    """[overseer Nov 8 21:47] the screen's stick was tuned before the flag: it is never scrubbed, so the first cut goes
    from the live-edge picture onto the buffered hidden stick (jump mode's leave gate), and both are buffered after."""
    dec, delays = setup(cfg, clock)
    scene(dec, clock)                       # b: buffered, its snap ~12 s after the picture would come up
    delays.set_behind("youtubetv:a", 0.0)   # a: at the live edge
    delays.held.pop("youtubetv:a")
    x = clock.t - EDGE                      # what a's live-edge picture shows: a dead ball there too
    whistle(dec.timelines["a"], "a3", x - 15)
    whistle(dec.timelines["a"], "a4", x + 50)
    d = dec.decide(cands(a=60, b=50))
    assert d.kind == "jump" and d.target == "b" and d.jump["why"] == "bj_bootstrap", (d.reason, dict(dec.jump_holds))


def test_control_rescrubs_an_unscrubbed_standby_and_lets_a_stuck_one_go(cfg, tmp_path, monkeypatch):
    import asyncio

    from tests.test_control import ChannelMap, FakeStick, ctl

    cfg.update({"mode": {"dry_run": False}, "jump_mode": {"enabled": True}, "buffered_jump": {"enabled": True}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.scrub_of = lambda t: True
    started = []

    async def rescrub(hid, target):
        started.append(target)
    monkeypatch.setattr(c, "_rescrub", rescrub)

    async def go():
        c._set_hidden("youtubetv:fox", True)          # tuned at the live edge before the flag: never scrubbed
        assert c.buffer_standby("youtubetv:fox") and not c.hidden_ready
        await c._preload_task
        assert started == ["youtubetv:fox"]
        c._set_hidden("youtubetv:nbc", False)          # stuck not ready, nothing running
        assert not c.drop_stuck_standby("youtubetv:nbc", 0.05)
        await asyncio.sleep(0.1)
        assert c.drop_stuck_standby("youtubetv:nbc", 0.05)
        assert c.hidden_target is None and not c.can_tune("youtubetv:nbc")
    asyncio.run(go())


def test_scrub_waits_for_the_tune_banner_and_a_banner_miss_retries_soon(cfg, tmp_path, monkeypatch):
    """[overseer Nov 8 21:55] 'focus guard: no scrub label after the LEFT' (62 s) = keys sent while YouTube TV's post-tune
    banner was up: the first key waits scrub_after_tune_s after the tune played, and that miss backs off 30 s, not 120."""
    import asyncio
    import time as _t

    from tests.test_control import ChannelMap, FakeStick, ctl

    cfg.update({"mode": {"dry_run": False}, "timing": {"behind_live_s": {"scrub_after_tune_s": 0.4}}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    hid = c.hidden
    seen = {}

    class R:
        ok, behind_s, presses, committed, reason = False, None, 0, False, "focus guard: no scrub label after the LEFT"

    async def enter_buffer(stick, seconds, **kw):
        seen["after"] = _t.time() - c._played_at[stick.host]
        return R()
    import redzone.tuners.yttv_seek as ys
    monkeypatch.setattr(ys, "enter_buffer", enter_buffer)

    async def tune(stick, target, may_cut=None):
        from redzone.tuners.tuners import TuneResult
        return TuneResult(True, target, 0.01, ["playing"], "")
    monkeypatch.setattr(c, "_tune", tune)

    async def go():
        c._set_hidden("youtubetv:secn", False)
        c._played_at[hid.host] = _t.time()
        assert not await c._scrub_into(hid, "youtubetv:secn", 75.0)
    asyncio.run(go())
    assert seen["after"] >= 0.4
    assert 25 <= c._scrub_backoff["youtubetv:secn"] - _t.time() <= 31
