"""Watchdog v2 fixes: the new rules, the guard around each fix, and carrying them out with fakes
(adb, the app's API) under their rate limits."""
import subprocess

import pytest

from agent import watchdog
from agent.policy import ACTIONS, Obs, Verdict, guard, parse_answer, rules

A, B = "100.110.165.181:5555", "100.110.165.150:5555"


def two(**kw) -> Obs:
    return Obs(on_screen=A, standby=B, **kw)


# ---- rules ---------------------------------------------------------------------------------
def test_safe_mode_is_left_once_its_errors_are_gone_but_never_over_a_pause():
    assert rules(Obs(safe_mode=True, safe_min=5)).action == "safe_mode_off"
    assert rules(Obs(safe_mode=True, safe_min=1)).action == "none"           # give it time
    assert rules(Obs(safe_mode=True, safe_min=9, paused=True)).action == "none"
    assert rules(Obs(safe_mode=True, safe_min=9, errors={"ERROR x: new thing": 1})) is None   # model's call


def test_stale_live_feed_is_refreshed():
    v = rules(Obs(live_games=2, feed_stale_s=400))
    assert v.action == "refresh_feed" and v.target == "feeds"
    assert rules(Obs(live_games=0, feed_stale_s=4000)).action == "none"      # nothing live: nothing to refresh
    assert rules(Obs(live_games=2, feed_stale_s=60)).action == "none"


def test_old_rules_decide_exactly_as_before():
    assert rules(Obs(api_ok=False, api_fail_streak=2, safe_mode=True, safe_min=9)).action == "restart_service"
    assert rules(Obs(disk_free_mb=500, live_games=1, feed_stale_s=999)).action == "prune_disk"
    assert rules(Obs(sticks_offline=[A], offline_min=1, safe_mode=True, safe_min=9)).action == "reconnect_stick"


# ---- guard ---------------------------------------------------------------------------------
@pytest.mark.parametrize("action", ["resume_player", "clear_prompt", "retune_stick"])
def test_stick_fixes_only_touch_a_known_online_stick(action):
    assert guard(Verdict(action, "x", B), two()).target == B
    assert guard(Verdict(action, "x", "1.2.3.4:5555"), two()).target == A      # unknown -> the one on screen
    v = guard(Verdict(action, "x", B), two(sticks_offline=[B]))
    assert v.action == "reconnect_stick" and v.target == B
    assert guard(Verdict(action, "x", A), Obs()).action == "notify"            # no stick known at all


def test_retune_never_cuts_the_stick_on_screen_in_crunch_time():
    assert guard(Verdict("retune_stick", "x", A), two(crunch_on_screen=True)).action == "notify"
    assert guard(Verdict("retune_stick", "x", B), two(crunch_on_screen=True)).action == "retune_stick"


def test_component_restarts_only_name_real_parts():
    assert guard(Verdict("restart_component", "x", "screen"), Obs()).action == "restart_component"
    assert guard(Verdict("restart_component", "x", "kernel"), Obs()).action == "notify"


def test_safe_mode_off_needs_safe_mode_and_no_pause():
    assert guard(Verdict("safe_mode_off", "x"), Obs()).action == "none"
    assert guard(Verdict("safe_mode_off", "x"), Obs(safe_mode=True, paused=True)).action == "none"
    assert guard(Verdict("safe_mode_off", "x"), Obs(safe_mode=True)).action == "safe_mode_off"


def test_every_new_action_parses():
    for a in ACTIONS:
        assert parse_answer(f'{{"action": "{a}", "target": "", "why": "x"}}').action == a


# ---- carrying them out, with fakes -------------------------------------------------------
class FakeAdb:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **k):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def w(monkeypatch):
    t = [1000.0]
    wd = watchdog.Watchdog(use_model=False, clock=lambda: t[0])
    wd.t = t
    wd.pushed = []
    monkeypatch.setattr(wd, "_push", wd.pushed.append)
    monkeypatch.setattr(watchdog.time, "sleep", lambda s: None)
    wd.adb = FakeAdb()
    monkeypatch.setattr(watchdog.subprocess, "run", wd.adb)
    wd.posts = []
    wd.reply = {"ok": True, "message": "done"}

    def post(path, body, timeout=10.0):
        wd.posts.append((path, body))
        return wd.reply
    monkeypatch.setattr(watchdog, "_post", post)
    monkeypatch.setattr(watchdog, "fetch_state", lambda timeout=6.0: wd.state)
    wd.state = {"mode": {"safe": True, "paused": False}}
    return wd


def test_resume_player_presses_play_only(w):
    assert w.act(Verdict("resume_player", "paused", B), two()).startswith("pressed 126")
    assert w.adb.calls == [[watchdog.ADB, "-s", B, "shell", "input", "keyevent", "126"]]


def test_clear_prompt_presses_ok_then_play(w):
    w.act(Verdict("clear_prompt", "still watching?", A), two())
    assert [c[-1] for c in w.adb.calls] == ["23", "126"]


def test_stick_fixes_are_rate_limited_then_become_a_note(w):
    v = Verdict("resume_player", "paused", B)
    w.act(v, two())
    w.t[0] += 30
    assert w.act(v, two()) == "noted for the phone"
    assert "already tried recently" in w.pushed[-1]
    assert len(w.adb.calls) == 1
    w.t[0] += 200                                      # past the 2-min gap: allowed again
    w.act(v, two())
    assert len(w.adb.calls) == 2


def test_hourly_cap(w):
    v = Verdict("retune_stick", "frozen", B)
    for _ in range(5):
        w.act(v, two())
        w.t[0] += 700
    assert len([p for p in w.posts if p[1]["fix"] == "retune"]) == 3


def test_endpoint_fixes_post_the_right_body(w):
    w.act(Verdict("retune_stick", "frozen", B), two())
    w.act(Verdict("refresh_feed", "stale", "feeds"), Obs())
    w.act(Verdict("restart_component", "crashing", "screen"), Obs())
    assert w.posts == [("/api/agent/fix", {"fix": "retune", "target": B}),
                       ("/api/agent/fix", {"fix": "refresh_feeds", "target": "feeds"}),
                       ("/api/agent/fix", {"fix": "restart", "target": "screen"})]


def test_no_fix_endpoint_means_a_note_not_silence(w):
    w.reply = {"ok": False, "message": "HTTP 404"}
    w.act(Verdict("refresh_feed", "feed stale 400 s", "feeds"), Obs())
    assert w.pushed and "HTTP 404" in w.pushed[0]


def test_safe_mode_off_rechecks_the_app_first(w):
    assert w.act(Verdict("safe_mode_off", "cause gone"), Obs(safe_mode=True)) == "left safe mode"
    assert w.posts == [("/api/action", {"action": "resume"})]
    w.t[0] += 700
    w.state = {"mode": {"safe": True, "paused": True}}    # someone paused in the meantime
    assert w.act(Verdict("safe_mode_off", "cause gone"), Obs(safe_mode=True)) == "paused by a person: left alone"
    assert len(w.posts) == 1


def test_adb_hang_never_crashes(w, monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(watchdog.subprocess, "run", hang)
    assert "no answer" in w.act(Verdict("resume_player", "x", A), two())


def test_dry_run_does_no_fix(monkeypatch):
    wd = watchdog.Watchdog(dry_run=True, use_model=False)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: pytest.fail("ran adb"))
    assert wd.act(Verdict("clear_prompt", "x", A), two()).startswith("dry run")


def test_observe_reads_sticks_safe_mode_and_feed_age(monkeypatch, tmp_path):
    t = [1000.0]
    wd = watchdog.Watchdog(use_model=False, clock=lambda: t[0])
    st = {"games": [{"phase": "live"}], "mode": {"safe": True, "paused": False},
          "health": {"sticks": [{"host": A, "state": "online", "role": "on screen"},
                                {"host": B, "state": "online", "role": "standby"}],
                     "sources": [{"name": "espn", "age_s": 12, "benched": False},
                                 {"name": "nhl", "age_s": 400, "benched": False},
                                 {"name": "old", "age_s": 9999, "benched": True}]}}
    monkeypatch.setattr(watchdog, "fetch_state", lambda timeout=6.0: st)
    monkeypatch.setattr(watchdog, "LOG", tmp_path / "none.log")
    monkeypatch.setattr(watchdog, "recent_errors", lambda **k: {})
    o = wd.observe()
    assert (o.on_screen, o.standby, o.safe_mode, o.feed_stale_s) == (A, B, True, 400.0)
    t[0] += 240
    assert wd.observe().safe_min == 4.0
