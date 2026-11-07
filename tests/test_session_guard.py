"""[sync hardening item 4] Session safety: each test is a real failure from Nov 4-7 (runbooks/stick-session.md).

Fail closed everywhere: a verdict that isn't "ok" never leaves the buffer "valid", keys go only to the hidden stick
(except prompts already covering the picture), the auto-play prompt only ever gets 'Ask later', and anything
dialog-shaped we have no rule for is never pressed."""
import asyncio

import pytest

from redzone.tuners import session_guard as sg
from redzone.tuners.adb import RunResult

YT = "com.amazon.firetv.youtube.tv"
PRIME = "com.amazon.firebat"


def S(**kw):
    base = dict(host="b", wall=1000.0, reachable=True, uptime_s=5000.0, pid=4242, package=YT, want_pkg=YT,
                state="playing")
    base.update(kw)
    return sg.Snapshot(**base)


def rows(*lines, lit=None):
    return [sg.Row(t, 0.1, 0.1 + 0.05 * i, lit=(None if lit is None else t == lit)) for i, t in enumerate(lines)]


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


ON = Cfg({"session_guard.enabled": True, "session_guard.act": True})


def test_playing_is_ok_and_valid():
    v = sg.classify(S())
    assert v.ok and v.buffer == "valid" and v.action == sg.NONE


# --- Nov 6: the post-tune LIVE/title banner looks like the overlay; keys do nothing there ---------------------------

def test_nov6_post_tune_banner_waits_instead_of_probing():
    v = sg.classify(S(since_tune_s=6.0, rows=rows("LIVE", "NBA Basketball", "CC")))
    assert v.kind == sg.BANNER and v.action == sg.WAIT and 21 < v.wait_s <= 22.1
    # once the scrub label shows, it is the overlay, not the banner
    assert sg.classify(S(since_tune_s=6.0, rows=rows("LIVE", "1:02:13", "CC"))).kind == sg.OK
    # past the window the banner rule is off
    assert sg.classify(S(since_tune_s=40.0, rows=rows("LIVE", "CC"))).ok


def test_banner_wait_is_bounded_and_off_with_the_flag():
    assert sg.SessionGuard().banner_wait(2.0) == 0.0                     # flag off: no wait at all
    g = sg.SessionGuard(ON)
    assert g.banner_wait(2.0) == 26.0 and g.banner_wait(40.0) == 0.0 and g.banner_wait(None) == 0.0
    g2 = sg.SessionGuard(Cfg({**ON, "session_guard.banner_s": 90, "session_guard.banner_wait_max_s": 30}))
    assert g2.banner_wait(0.0) == 30.0


# --- Nov 7 ~1:00 AM: the overlay label resets at a program boundary ----------------------------------------------

def test_nov7_program_boundary_label_reset():
    last = (1000.0, 3540.0, "12:00 AM - 1:00 AM")
    # 60 s later the label should read ~3600; a new program restarted it at 0:39
    v = sg.classify(S(wall=1060.0, label_s=39.0, window="1:00 AM - 1:30 AM"), last_label=last)
    assert v.kind == sg.LABEL_RESET and v.action == sg.REANCHOR and v.buffer == "unknown"
    # a window change alone is enough, even if the numbers happen to line up
    assert sg.classify(S(wall=1060.0, label_s=3600.0, window="1:00 AM - 1:30 AM"), last_label=last).kind \
        == sg.LABEL_RESET
    # same program, label where it should be: fine
    assert sg.classify(S(wall=1060.0, label_s=3598.0, window="12:00 AM - 1:00 AM"), last_label=last).ok


def test_label_jumping_ahead_is_not_trusted():
    last = (1000.0, 600.0, "")
    v = sg.classify(S(wall=1010.0, label_s=700.0), last_label=last)   # +100 s in 10 s: re-tuned to the edge?
    assert v.kind == sg.LABEL_RESET and v.buffer == "unknown"


def test_guard_drops_its_label_anchor_after_a_reset_and_learns_a_new_one():
    g = sg.SessionGuard(ON)
    g.note_label("b", 1000.0, 3540.0, "12:00 AM - 1:00 AM")
    assert g.observe(S(wall=1060.0, label_s=39.0, window="1:00 AM - 1:30 AM"), hidden=True).kind == sg.LABEL_RESET
    assert "b" not in g.labels
    assert g.observe(S(wall=1070.0, label_s=49.0, window="1:00 AM - 1:30 AM"), hidden=True).ok
    assert g.labels["b"] == (1070.0, 49.0, "1:00 AM - 1:30 AM")


# --- Nov 7: Prime's detail page (the first CENTER only wakes the nav; #390 owns the start) ------------------------

def test_nov7_prime_detail_page():
    snap = S(package=PRIME, want_pkg=PRIME, state="paused",
             rows=rows("Thursday Night Football", "Watch Live", "Start Over", "More ways to watch"))
    v = sg.classify(snap, hidden=True)
    assert v.kind == sg.PRIME_DETAIL_PAGE and v.action == sg.PRIME_START and v.buffer == "lost"
    assert sg.classify(snap, hidden=False).action == sg.HOLD     # never on the stick on screen


def test_nov7_prime_unavailable_page_is_never_pressed():
    v = sg.classify(S(package=PRIME, want_pkg=PRIME, state="none", rows=rows("NBA on Prime", "UNAVAILABLE")))
    assert v.kind == sg.ACCOUNT and v.action == sg.HOLD and v.alert


def test_payment_screen_is_never_pressed():
    v = sg.classify(S(state="none", rows=rows("Start your free trial", "Continue")))
    assert v.kind == sg.ACCOUNT and v.action == sg.HOLD


# --- prompts: YouTube TV's profile picker, Still watching, auto-play ---------------------------------------------

def test_yttv_profile_picker_restarted_the_player():
    v = sg.classify(S(state="none", rows=rows("Who's watching?", "Nick", "Serge")))
    assert v.kind == sg.PROFILE and v.action == sg.CLEAR_PROFILE and v.buffer == "lost"


def test_fire_os_profile_picker_after_wake_is_not_ours():
    """Fire OS has its own 'Who's watching?' after a wake (screen_prompts' docstring): never answered here."""
    v = sg.classify(S(package="com.amazon.tv.profile", state="none", rows=rows("Who's watching?", "Nick")))
    assert v.kind == sg.FIRE_OS_PROFILE and v.action == sg.HOLD and v.alert


def test_still_watching_makes_the_buffer_unknown():
    """e2e night tests (#28/#86): the prompt during a replay; the picture pauses behind it, so behind-live grew."""
    v = sg.classify(S(state="paused", rows=rows("Are you still watching?", "Yes", "No")))
    assert v.kind == sg.STILL_WATCHING and v.action == sg.CLEAR_STILL and v.buffer == "unknown"


def test_autoplay_prompt_only_ever_ask_later():
    v = sg.classify(S(rows=rows("Turn on auto-play?", "Turn on", "Ask later")))
    assert v.kind == sg.AUTOPLAY_PROMPT and v.action == sg.ASK_LATER_KEY
    # no Ask later read: hold, never 'Turn on'
    v2 = sg.classify(S(rows=rows("Turn on auto-play?", "Turn on")))
    assert v2.kind == sg.AUTOPLAY_PROMPT and v2.action == sg.HOLD


def test_ask_later_needs_proof_it_is_the_highlighted_button():
    assert sg.ask_later_lit(S(rows=rows("Turn on auto-play?", "Turn on", "Ask later", lit="Ask later")))
    assert not sg.ask_later_lit(S(rows=rows("Turn on auto-play?", "Turn on", "Ask later", lit="Turn on")))
    assert not sg.ask_later_lit(S(rows=rows("Turn on auto-play?", "Turn on", "Ask later")))   # pixels unread


# --- crash, reboot, adb drop, Fire TV home, wrong app ---------------------------------------------------------------

def test_adb_drop_is_unknown_not_fine():
    v = sg.classify(S(reachable=False, uptime_s=None, pid=None, package="", state=None))
    assert v.kind == sg.ADB_DROP and v.buffer == "unknown" and v.action == sg.RECONNECT
    assert sg.classify(S(breaker_open=True)).kind == sg.ADB_DROP


def test_reboot_seen_across_an_adb_drop():
    """Fault drill reboot_visible (lessons: 'A rebooted stick on screen was never given its game back')."""
    g = sg.SessionGuard(ON)
    assert g.observe(S(uptime_s=86000.0), hidden=False).ok
    assert g.observe(S(reachable=False, uptime_s=None, pid=None, package="", state=None), hidden=False).kind \
        == sg.ADB_DROP
    v = g.observe(S(uptime_s=40.0, pid=None, package="com.amazon.tv.launcher", state="none"), hidden=False)
    assert v.kind == sg.REBOOT and v.buffer == "lost" and v.action == sg.RESTORE and v.alert


def test_app_crash_new_process():
    g = sg.SessionGuard(ON)
    g.observe(S(pid=4242), hidden=True)
    v = g.observe(S(pid=5151), hidden=True)
    assert v.kind == sg.APP_CRASH and v.buffer == "lost" and v.action == sg.RELINK


def test_our_own_tune_is_not_a_crash():
    g = sg.SessionGuard(ON)
    g.observe(S(pid=4242), hidden=True)
    g.note_tune("b")                      # a fresh deep link may restart the app's process
    assert g.observe(S(pid=5151), hidden=True).ok


def test_crash_dialog_is_never_pressed():
    v = sg.classify(S(rows=rows("YouTube TV isn't responding", "Close app", "Wait")))
    assert v.kind == sg.CRASH_DIALOG and v.action == sg.HOLD and v.alert


def test_fire_tv_home():
    v = sg.classify(S(package="com.amazon.tv.launcher", pid=None, state="none"), hidden=True)
    assert v.kind == sg.HOME and v.action == sg.RELINK and v.buffer == "lost"


def test_keys_on_the_wrong_app():
    """Nov 6 (#356): keys meant for YouTube TV landed in Prime. A different app in front = lost, not ours to press."""
    v = sg.classify(S(package=PRIME, pid=None), hidden=True)
    assert v.kind == sg.WRONG_APP and v.buffer == "lost"


def test_player_error():
    v = sg.classify(S(state="stopped", rows=rows("Something went wrong", "Try again")))
    assert v.kind == sg.PLAYER_ERR and v.buffer == "lost"


def test_not_playing_and_unread_state_are_not_valid():
    assert sg.classify(S(state="buffering")).action == sg.WAIT
    assert sg.classify(S(state=None)).buffer == "unknown"
    assert sg.classify(S(state="paused"), hidden=True).action == sg.RELINK


# --- UI changes: unexpected OCR text -----------------------------------------------------------------------------

def test_unexpected_dialog_text_is_never_pressed():
    v = sg.classify(S(rows=rows("Introducing Multiview", "Try it", "Not now")))
    assert v.kind == sg.UI_UNKNOWN and v.action == sg.HOLD and v.buffer == "unknown" and v.alert
    assert v.unknown_hash and "try" not in v.reason.lower()    # the words never reach a public log


def test_expected_overlay_missing_is_a_ui_change():
    """#356: LEFT x9 with focus unknown seeked the stick 60 s. Expecting the overlay and reading none of its parts
    must stop the scrub, not guess."""
    v = sg.classify(S(expect="overlay", rows=rows("Some new panel", "Recommended for you")))
    assert v.kind == sg.UI_UNKNOWN and v.action == sg.HOLD
    assert sg.classify(S(expect="overlay", rows=rows("9:00 PM - 11:00 PM", "1:02:13", "CC"))).ok


def test_program_titles_are_not_ui_changes():
    assert sg.classify(S(rows=rows("Lakers at Celtics", "NBA Basketball", "LIVE", "9:00 PM - 11:00 PM"))).ok


# --- the action gate ----------------------------------------------------------------------------------------------

def test_gate_flag_off_blocks_every_key():
    g = sg.SessionGuard(Cfg({"session_guard.enabled": True}))
    ok, why = g.allowed(sg.classify(S(package="com.amazon.tv.launcher"), hidden=True), hidden=True, host="b")
    assert not ok and "act is off" in why


def test_gate_keys_only_on_hidden_except_covering_prompts():
    g = sg.SessionGuard(ON)
    relink = sg.Verdict(sg.HOME, sg.RELINK, "lost")
    assert not g.allowed(relink, hidden=False, host="a")[0]
    assert g.allowed(relink, hidden=True, host="b")[0]
    still = sg.Verdict(sg.STILL_WATCHING, sg.CLEAR_STILL, "unknown")
    assert g.allowed(still, hidden=False, host="a")[0]


def test_gate_rate_limits_recoveries():
    t = [0.0]
    g = sg.SessionGuard(ON, clock=lambda: t[0])
    v = sg.Verdict(sg.APP_CRASH, sg.RELINK, "lost")
    ran = []

    async def go():
        for _ in range(5):
            await g.recover(v, host="b", hidden=True, handlers={sg.RELINK: lambda: ran.append(1) or True})
    asyncio.run(go())
    assert len(ran) == 3                                       # max_actions, then hold
    assert g.summary()["blocked"] == 2 and g.summary()["recoveries_ok"] == 3
    t[0] = 700.0                                               # the window passed
    asyncio.run(g.recover(v, host="b", hidden=True, handlers={sg.RELINK: lambda: ran.append(1) or True}))
    assert len(ran) == 4


def test_handler_failure_never_raises():
    g = sg.SessionGuard(ON)

    def boom():
        raise RuntimeError("adb gone")
    out = asyncio.run(g.recover(sg.Verdict(sg.HOME, sg.RELINK, "lost"), host="b", hidden=True,
                                handlers={sg.RELINK: boom}))
    assert out is False and g.events[-1].done == "ran:failed"


def test_events_carry_numbers_and_kinds_only():
    seen = []
    g = sg.SessionGuard(ON, on_event=seen.append)
    g.observe(S(rows=rows("Introducing Multiview", "Try it")), hidden=True)
    assert seen and set(seen[0]) == {"t", "host", "kind", "action", "buffer", "hidden", "done", "reason",
                                     "unknown_hash"}
    assert "multiview" not in str(seen[0]).lower()


# --- snapshot from a fake stick ----------------------------------------------------------------------------------

class Fake:
    host = "b"

    def __init__(self, up="5000.12 1000.0", pid="4242", fg=YT, state="playing", ok=True):
        self.up, self.pid, self.fg, self.st, self.ok, self.keys = up, pid, fg, state, ok, []

    async def shell(self, cmd, timeout=8.0):
        if not self.ok:
            return RunResult(False, "", 4.0)
        if "uptime" in cmd:
            return RunResult(True, self.up, 0.01)
        if cmd.startswith("pidof"):
            return RunResult(True, self.pid, 0.01)
        return RunResult(True, "", 0.01)

    async def foreground(self):
        return f"{self.fg}/.Main"

    async def playback(self, package=None):
        return {"state": self.st}

    async def key(self, *codes):
        self.keys.extend(codes)
        return RunResult(True, "", 0.01)


def test_snapshot_reads_adb_facts_and_presses_nothing():
    st = Fake()
    snap = asyncio.run(sg.snapshot(st, YT))
    assert (snap.reachable, snap.uptime_s, snap.pid, snap.package, snap.state) == (True, 5000.12, 4242, YT, "playing")
    assert snap.rows is None and st.keys == []
    dead = asyncio.run(sg.snapshot(Fake(ok=False), YT))
    assert dead.reachable is False and sg.classify(dead).kind == sg.ADB_DROP


@pytest.mark.parametrize("kind", [sg.ADB_DROP, sg.REBOOT, sg.APP_CRASH, sg.CRASH_DIALOG, sg.HOME, sg.FIRE_OS_PROFILE,
                                  sg.ACCOUNT, sg.PROFILE, sg.STILL_WATCHING, sg.AUTOPLAY_PROMPT, sg.PLAYER_ERR,
                                  sg.PRIME_DETAIL_PAGE, sg.LABEL_RESET, sg.UI_UNKNOWN, sg.WRONG_APP, sg.NOT_PLAYING])
def test_every_failure_kind_has_a_runbook_entry(kind):
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "docs/memory/runbooks/stick-session.md").read_text()
    assert f"`{kind}`" in text


# --- wired into the scrub (control._scrub_into), flag on -----------------------------------------------------------

from tests.test_scrub_entry import scrub  # noqa: E402,F401  (fixture)
from tests.test_control import two  # noqa: E402,F401  (fixture)


def _guard_on(c, **extra):
    c.cfg.defaults.setdefault("session_guard", dict(sg.DEFAULTS))
    c.cfg.update({"session_guard": {"enabled": True, **extra}})


def test_scrub_waits_out_the_banner_then_scrubs(scrub, monkeypatch):   # noqa: F811
    c, tuner, hid, calls, result, behind = scrub
    _guard_on(c)
    hid.packages = {"youtubetv": YT, "prime": PRIME}          # the real package: the banner rule is YouTube TV's
    slept = []
    real = asyncio.sleep

    async def fake_sleep(s, *a, **k):
        slept.append(s)
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(_preload(c, "youtubetv:fox"))
    assert any(20 <= s <= 28 for s in slept)              # waited for the post-tune banner, once
    assert len(calls) == 1 and c.hidden_ready and c.held["youtubetv:fox"] == 67.0


def test_scrub_refused_when_the_session_is_gone_and_retuned(scrub, monkeypatch):   # noqa: F811
    c, tuner, hid, calls, result, behind = scrub
    _guard_on(c, banner_s=0)

    async def fg():
        return "com.amazon.tv.launcher/.Home"             # the app crashed back to the Fire TV home
    monkeypatch.setattr(hid, "foreground", fg)
    asyncio.run(_preload(c, "youtubetv:fox"))
    assert calls == []                                     # no scrub key was pressed
    assert "KEYCODE_DPAD_LEFT" not in hid.log and "KEYCODE_DPAD_CENTER" not in hid.log
    assert c.held.get("youtubetv:fox", 0.0) == 0.0 and hid.scrubbed("youtubetv:fox") is None
    assert hid.behind_on is None                           # nothing pressed: no reset-through-other needed
    assert tuner.calls.count(("b:5555", "youtubetv:fox")) == 2   # lost session -> fresh live tune
    assert c.session_guard.counts.get(sg.HOME) == 1


def test_scrub_unchanged_with_the_guard_off(scrub):   # noqa: F811
    c, tuner, hid, calls, result, behind = scrub
    assert not c.session_guard.enabled
    asyncio.run(_preload(c, "youtubetv:fox"))
    assert len(calls) == 1 and c.session_guard.counts == {}


async def _preload(c, target):
    assert c.preload(target)
    await c._preload_task


def test_settle_banner_polls_until_the_screen_is_clear_and_never_presses():
    t = [5.0]
    st = Fake()
    real = asyncio.sleep

    async def look(_s, pkg, since_tune_s=None, read_screen=False, **kw):
        t[0] += 2.0
        up = t[0] < 25.0                                     # Nov 6: banner up ~25 s after the tune
        return S(wall=t[0], since_tune_s=since_tune_s, rows=rows("LIVE", "CC") if up else [])

    async def go():
        async def vs(s, *a, **k):
            t[0] += s
            await real(0)
        asyncio.sleep = vs
        try:
            return await sg.settle_banner(sg.SessionGuard(ON), st, YT, lambda: t[0], look=look)
        finally:
            asyncio.sleep = real
    v = asyncio.run(go())
    assert v.ok and 25.0 <= t[0] < 30.0 and st.keys == []


def test_settle_banner_stops_at_the_cap():
    t = [0.0]
    real = asyncio.sleep

    async def look(_s, pkg, since_tune_s=None, read_screen=False, **kw):
        return S(since_tune_s=0.0, rows=rows("LIVE", "CC"))   # never clears

    async def go():
        async def vs(s, *a, **k):
            t[0] += s
            await real(0)
        asyncio.sleep = vs
        try:
            return await sg.settle_banner(sg.SessionGuard(ON), Fake(), YT, lambda: 0.0, look=look)
        finally:
            asyncio.sleep = real
    v = asyncio.run(go())
    assert v.kind == sg.BANNER and t[0] == 30.0
