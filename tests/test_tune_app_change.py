"""[tuners.app_change_confirm] A tune right after another app was in front (measured on the TV, Nov 10).

Why: with Prime in front, a YouTube TV channel link started the asked channel, yet the tuner reported "video never started"
in 8 of 11 tries: Android answers "brought to the front", the tuner re-sends the link as a new task (the app restarts and the
first window runs out), and the retry then snapshots the asked channel already playing, so a watch that waits for a change
can never pass. The flag lets the plain link confirm first and counts the asked channel at the retry as success. Off: as before.
"""
import time

from fake_adb import media
from redzone.tuners.adb import RunResult
from redzone.tuners.tuners import TUNERS, YouTubeTVTuner, apply_flags
from test_tuners import YT, ScriptedStick, quick

FRONT = "Warning: Activity not started, its current task has been brought to the front"


def on(channel: str, pos: int = 100, upd: int = 5000) -> str:
    return media(YT, 3, pos, upd).replace("Game, Channel", f"Show, {channel}")


class LateStick(ScriptedStick):
    """Android answers the FIRST link "brought to the front" (the app was behind another one). The asked channel reports
    playing `delay` seconds after that link, or, with delay None, only once `ready` is set."""

    def __init__(self, before: str, then: str, delay: float | None):
        super().__init__(now=before)
        self.before, self.then, self.delay, self.t_link, self.ready = before, then, delay, None, False

    async def _shell(self, cmd, timeout=8.0):
        self.cmds.append(cmd)
        if cmd.startswith("am start"):
            first, self.linked = self.t_link is None, True
            self.t_link = self.t_link or time.monotonic()
            return RunResult(True, FRONT if first else self.am_reply, 0.0)
        if "media_session" in cmd:
            late = self.ready or (self.delay is not None and self.t_link is not None and time.monotonic() - self.t_link >= self.delay)
            return RunResult(True, self.then if late else self.before, 0.0)
        return await super()._shell(cmd, timeout)


def window_runs_out_once(monkeypatch):
    """The first confirm window passes with nothing playing (the app is restarting as a new task); the channel is on
    just after it. Later windows are the real ones."""
    real, calls = YouTubeTVTuner._confirm, []

    async def confirm(self, stick, pkg, steps, watch=None, may_cut=None):
        calls.append(1)
        if len(calls) == 1:
            stick.ready = True
            return False
        return await real(self, stick, pkg, steps, watch, may_cut)
    monkeypatch.setattr(YouTubeTVTuner, "_confirm", confirm)


async def test_flag_off_the_nov_10_sequence_still_ends_video_never_started(monkeypatch):
    window_runs_out_once(monkeypatch)
    st = LateStick(before="", then=on("ESPN"), delay=None)
    res = await quick().tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and res.error == "video never started"
    assert len(st.links()) == 3 and st.tuned is None               # link, resend as a new task, second link


async def test_flag_on_the_asked_channel_already_on_at_the_retry_is_success(monkeypatch):
    window_runs_out_once(monkeypatch)
    st = LateStick(before="", then=on("ESPN"), delay=None)
    res = await quick(app_change_confirm=True, front_grace_s=0.05).tune(st, "CODE", "youtubetv:espn")
    assert res.ok and any("the first link had worked" in s for s in res.steps)
    assert len(st.links()) == 2                                    # no second link is sent to a channel already on


async def test_flag_on_the_plain_link_confirms_without_a_resend():
    st = LateStick(before="", then=on("ESPN"), delay=0.01)         # as measured: the plain link plays in about 2.5 s
    res = await quick(app_change_confirm=True, front_grace_s=0.2).tune(st, "CODE", "youtubetv:espn")
    assert res.ok and any("plain link worked" in s for s in res.steps)
    assert len(st.links()) == 1 and not any("resent the link" in s for s in res.steps)


async def test_flag_on_another_channel_still_playing_never_confirms():
    """Rule: an unchanged old stream never confirms a new target, however long we wait."""
    st = LateStick(before=on("FS1"), then=on("FS1"), delay=0.0)
    res = await quick(app_change_confirm=True, front_grace_s=0.05).tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and st.tuned is None


async def test_flag_on_a_target_already_named_before_the_link_is_not_called_a_new_success():
    """The retry shortcut needs a CHANGE to the asked channel: the same channel before and after is left to the old rules."""
    st = LateStick(before=on("ESPN"), then=on("ESPN"), delay=0.0)
    st.still_on = lambda target, report: False                     # not remembered as on it: the tuner must see a transition
    res = await quick(app_change_confirm=True, front_grace_s=0.05).tune(st, "CODE", "youtubetv:espn")
    assert not res.ok


def test_the_flag_reaches_every_tuner_and_is_off_by_default():
    class Cfg(dict):
        def get(self, k, d=None):
            return super().get(k, d)
    table = {"youtubetv": YouTubeTVTuner()}
    assert YouTubeTVTuner.app_change_confirm is False
    apply_flags(Cfg({"tuners.app_change_confirm": True}), table)
    assert table["youtubetv"].app_change_confirm is True
    apply_flags(Cfg(), table)
    assert table["youtubetv"].app_change_confirm is False and TUNERS is not table
