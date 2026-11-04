"""C17: the watchdog's 13 fault kinds and 12 guarded actions, with fakes only (no app, adb, launchctl,
model or phone).

- every one of the 13 kinds in KIND_HELP has a rule path, and its action is one the kind allows;
- the trained model's prompt (Obs.text) is unchanged by the new fields;
- guard: stick fixes only on the stick on screen while it's dead; never undo a person's pause;
  components only from the list; no feed refresh while the internet is down;
- a dead screen walks resume -> prompt -> re-tune -> a person, one rung per failed fix, never two
  fixes before the first is judged;
- app fixes go through POST /api/agent/fix; flag off / old app / app limit -> a note to a person;
- every action that ran is judged (worked / failed) and counted;
- the app side: off by default, refuses a stick that isn't on screen, runs through the
  supervisor's rate limit, and view() reads the controller without adb."""
import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from agent import policy, watchdog
from agent.policy import KIND_ACTIONS, KINDS, Obs, Verdict, guard, rules

# --------------------------------------------------------------------------- the 13 kinds

ON = "10.0.0.150:5555"
KIND_CASES = {
    "core_loop": Obs(api_ok=False, api_fail_streak=2),
    "component": Obs(live_games=1, components=["fastcast"]),
    "stick_link": Obs(sticks_offline=["10.0.0.181:5555"]),
    "storage": Obs(disk_free_mb=300),
    "person": Obs(person="SmartThings sign-in expired: run scripts/tvsetup.py"),
    "upstream": Obs(live_games=2, feed_stale_s=400),
    "network": Obs(live_games=2, net_down=True),
    "cosmetic": Obs(errors={"WARNING redzone.score bug read failed": 4}),
    "resource": Obs(errors={"ERROR redzone.engine: OSError: [Errno N] Too many open files": 3}),
    "permission": Obs(errors={"ERROR redzone.screen: PermissionError: Screen Recording not allowed": 1}),
    "prompt": Obs(live_games=1, on_screen=ON, screen="prompt", screen_dead_s=120),
    "paused": Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=120),
    "frozen": Obs(live_games=1, on_screen=ON, screen="frozen", screen_dead_s=120),
}


def test_thirteen_kinds_match_the_teacher():
    assert len(KINDS) == 13 and set(KIND_CASES) == set(KINDS) == set(KIND_ACTIONS)
    assert len(policy.ACTIONS) == 11 and "none" in policy.ACTIONS   # 11 + "do nothing" = 12 choices


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_has_a_rule_and_an_allowed_action(kind):
    o = KIND_CASES[kind]
    v = guard(rules(o), o)
    assert v.kind == kind, v
    assert v.action in KIND_ACTIONS[kind], v
    assert v.action == KIND_ACTIONS[kind][0]


def test_every_action_is_reachable():
    seen = {guard(rules(o), o).action for o in KIND_CASES.values()}
    seen |= {guard(rules(o), o).action for o in (
        Obs(safe_mode=True, safe_min=5),
        Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=120, failed=["resume_player"]),
        Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=120,
            failed=["resume_player", "clear_prompt"]),
        Obs(live_games=1, feed_stale_s=400, failed=["refresh_feed"]))}
    assert seen >= set(policy.ACTIONS) - {"reconnect_stick"} | {"reconnect_stick"}


def test_model_prompt_unchanged_by_new_fields():
    base = Obs(live_games=2, errors={"x": 1})
    fancy = Obs(live_games=2, errors={"x": 1}, on_screen=ON, screen="paused", screen_dead_s=99, safe_mode=True,
                feed_stale_s=500, net_down=True, components=["feeds"], person="p", failed=["a"], trying=["b"],
                launchd_exit=78)
    assert fancy.text() == base.text()
    assert "kind" not in Verdict("none", "x", kind="cosmetic").json()


# --------------------------------------------------------------------------- guard

def test_stick_fix_only_on_the_dead_on_screen_stick():
    o = Obs(on_screen=ON, screen="paused", screen_dead_s=120)
    assert guard(Verdict("resume_player", "m", "10.0.0.181:5555", "model"), o).target == ON
    assert guard(Verdict("retune_stick", "m", ON, "model"), Obs(on_screen=ON)).action == "none"   # not dead
    assert guard(Verdict("retune_stick", "m", ON), Obs(on_screen=ON, screen="frozen", away=True)).action == "none"
    off = guard(Verdict("retune_stick", "m", ON), Obs(on_screen=ON, screen="frozen", sticks_offline=[ON]))
    assert (off.action, off.kind) == ("reconnect_stick", "stick_link")


def test_guard_keeps_a_persons_pause_and_components_list():
    assert guard(Verdict("safe_mode_off", "x"), Obs(safe_mode=True, paused=True)).action == "none"
    assert guard(Verdict("safe_mode_off", "x"), Obs()).action == "none"
    assert rules(Obs(safe_mode=True, safe_min=10, paused=True)).action == "none"
    assert guard(Verdict("restart_component", "x", "decider"), Obs()).action == "notify"
    assert guard(Verdict("refresh_feed", "x", "feeds"), Obs(net_down=True)).action == "none"
    assert rules(Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=120, paused=True)).action == "none"


def test_a_short_stall_is_left_to_the_app():
    assert rules(Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=30)).action == "none"


def test_old_rules_unchanged():
    # Same answers as before C17 for observations without the new fields (training labels).
    assert rules(Obs(api_ok=False, api_fail_streak=3)).action == "restart_service"
    assert rules(Obs(errors={"WARNING poll failed": 12})).action == "notify"
    assert rules(Obs(errors={"some brand new error": 1})) is None
    assert rules(Obs()).action == "none"


# --------------------------------------------------------------------------- the watchdog side

@pytest.fixture
def w(monkeypatch):
    t = [1000.0]
    wd = watchdog.Watchdog(use_model=False, clock=lambda: t[0])
    monkeypatch.setattr(wd.gov, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(wd.gov, "allow", lambda kind: kind != "llm")
    wd.t = t
    wd.pushed, wd.posts, wd.rows = [], [], []
    wd.answer = {"ok": True, "message": "media_play"}
    monkeypatch.setattr(wd, "_push", wd.pushed.append)
    monkeypatch.setattr(watchdog, "_journal", wd.rows.append)
    monkeypatch.setattr(watchdog, "_post", lambda path, body, **k: wd.posts.append(body) or wd.answer)
    monkeypatch.setattr(watchdog.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "connected", ""))
    monkeypatch.setattr(watchdog, "deploy_in_progress", lambda now: False)
    return wd


def _check(w, o):
    """One watchdog check on a given observation (observe() itself reads the real app)."""
    w.t[0] += watchdog.CHECK_S
    w.settle(o)
    v = w.decide(o)
    return v, w.act(v, o)


def dead(**kw):
    return Obs(live_games=1, on_screen=ON, screen="paused", screen_dead_s=200, **kw)


def test_dead_screen_walks_the_ladder_then_tells_a_person(w):
    acts = []
    for _ in range(12):
        v, _ = _check(w, dead())
        acts.append(v.action)
    fixes = [a for a in acts if a not in ("none",)]
    assert fixes[:3] == ["resume_player", "clear_prompt", "retune_stick"]
    assert fixes[3] == "notify" and any("didn't help" in p for p in w.pushed)
    assert acts[1] == "none"          # never two fixes in a row: each waits to be judged
    assert [b["fix"] for b in w.posts] == ["resume_player", "clear_prompt", "retune_stick"]
    assert w.fix_stats == {"resume_player": [0, 1], "clear_prompt": [0, 1], "retune_stick": [0, 1]}


def test_a_fix_that_worked_is_counted_and_clears_the_ladder(w):
    v, _ = _check(w, dead())
    assert v.action == "resume_player"
    _check(w, Obs(live_games=1, on_screen=ON))                      # the screen plays again
    assert w.fix_stats["resume_player"] == [1, 1] and not w.failed
    ok = [r for r in w.rows if r.get("kind") == "fix_result"]
    assert ok[-1]["worked"] is True and ok[-1]["fault"] == "paused"
    v, _ = _check(w, dead())                                        # a new stall starts at the bottom rung again
    assert v.action == "resume_player"


def test_app_says_off_becomes_a_note_and_runs_nothing(w):
    w.answer = {"ok": False, "message": "off: hardening.watchdog is false"}
    v, done = _check(w, dead())
    assert v.action == "resume_player" and done.startswith("not run (off:")
    assert w.pushed and "resume player" in w.pushed[0]
    assert not w.pending and not w.actions and w.fix_stats == {}


def test_old_app_without_the_endpoint_becomes_a_note(w, monkeypatch):
    monkeypatch.setattr(watchdog, "_post", lambda *a, **k: None)
    v, done = _check(w, Obs(live_games=1, components=["fastcast"]))
    assert v.action == "restart_component" and "no fix endpoint" in done


def test_a_fix_the_app_ran_and_failed_moves_up_a_rung_at_once(w):
    w.answer = {"ok": False, "message": "still not playing"}
    _check(w, dead())
    assert "resume_player" in w.failed and w.fix_stats["resume_player"] == [0, 1]
    v, _ = _check(w, dead())
    assert v.action == "clear_prompt"


def test_fix_limits_per_target(w):
    o = Obs(live_games=1, components=["fastcast"])
    w.answer = {"ok": True, "message": "push feed restarting"}
    v = Verdict("restart_component", "x", "fastcast", kind="component")
    assert w.act(v, o).startswith("restart_component")
    w.t[0] += 60
    assert "waiting" in w.act(v, o)
    assert len(w.posts) == 1


def test_feed_ladder_and_safe_mode(w):
    stale = Obs(live_games=2, feed_stale_s=400)
    acts = [_check(w, stale)[0].action for _ in range(8)]
    assert [a for a in acts if a != "none"][:3] == ["refresh_feed", "restart_component", "notify"]
    assert [b["fix"] for b in w.posts] == ["refresh_feed", "restart_component"]
    v, _ = _check(w, Obs(safe_mode=True, safe_min=4))
    assert v.action == "safe_mode_off" and w.posts[-1] == {"fix": "safe_mode_off", "target": ""}


def test_restart_is_judged_too(w):
    _check(w, Obs(api_ok=False, api_fail_streak=2))
    _check(w, Obs())
    assert w.fix_stats["restart_service"] == [1, 1]
    assert w.fix_rates() == {"restart_service": "1/1"}


def test_observe_reads_the_agent_block(w, monkeypatch):
    st = {"games": [{"phase": "live"}], "now": {}, "health": {"agent": {
        "on_screen": ON, "screen": "frozen", "screen_dead_s": 95.5, "safe_mode": True, "safe_min": 4,
        "paused": False, "feed_stale_s": 12, "net_down": False, "components": ["fastcast"], "person": ""}}}
    monkeypatch.setattr(watchdog, "fetch_state", lambda *a, **k: st)
    monkeypatch.setattr(watchdog, "recent_errors", lambda **k: {})
    o = w.observe()
    assert (o.on_screen, o.screen, o.screen_dead_s, o.safe_mode, o.components) == (ON, "frozen", 95.5, True,
                                                                                    ["fastcast"])
    st["health"]["agent"] = {"screen_dead_s": "garbage"}             # an odd app: no crash
    assert w.observe().screen_dead_s == 0.0


# --------------------------------------------------------------------------- the app side

class FakeSup:
    def __init__(self):
        from redzone.supervisor import Limiter
        self.t = [0.0]
        self.limiter = Limiter(lambda: self.t[0])
        self.fixes, self.ran = [], []

    async def fix(self, rule, key, action, fn, limit=None):
        from redzone.supervisor import LIMITS, Fix
        gap, per = LIMITS[limit]
        if not self.limiter.allow(f"{limit}:{key}", gap, per):
            return None
        self.limiter.note(f"{limit}:{key}")
        res = fn()
        if asyncio.iscoroutine(res):
            res = await res
        ok, detail = res if isinstance(res, tuple) else (res is not False, "")
        self.fixes.append(Fix(rule, key, action, bool(ok), str(detail)))
        self.ran.append((rule, key))
        return bool(ok)


class FakeCtl:
    def __init__(self):
        self.main = SimpleNamespace(host=ON)
        self.hidden = SimpleNamespace(host="10.0.0.181:5555")
        self.two_stick = True
        self.on_target = "youtubetv:espn"
        self._visible_bad_since = 100.0
        self._visible_state = "paused"
        self._flipping = False
        self.input_unknown = False
        self.calls = []

    def _busy(self):
        return False

    def _package(self, stick, target):
        return "yttv.pkg"

    async def restore_visible(self, target, relink=False, why=""):
        self.calls.append(("restore", target, relink))
        return "relink" if relink else "media_play"

    async def _clear_screen_prompt(self, stick, target, pkg):
        self.calls.append(("prompt", target))
        return True


def fake_engine(on=True):
    cfg = {"hardening.watchdog": on}
    dec = SimpleNamespace(safe_mode=True, safe_since=0.0, user=SimpleNamespace(paused=False),
                          auto_resume=lambda after: True)
    e = SimpleNamespace(cfg=SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)), control=FakeCtl(), decider=dec,
                        supervisor=FakeSup(), merger=SimpleNamespace(games={}), source_status={},
                        _fastcast_task=None)
    e.reset_feeds = lambda hard=False: (True, "hard" if hard else "soft")
    e._stop_fastcast = lambda: None
    return e


def run(coro):
    return asyncio.run(coro)


def test_app_fixes_off_by_default():
    from redzone.config import Config
    from redzone.platform.agent_fix import run_fix
    assert Config().get("hardening.watchdog") is False
    e = fake_engine(on=False)
    ok, msg = run(run_fix(e, "resume_player", ON))
    assert not ok and msg.startswith("off:") and not e.control.calls and not e.supervisor.ran


def test_app_stick_fixes_only_on_the_stick_on_screen():
    from redzone.platform.agent_fix import run_fix
    e = fake_engine()
    assert run(run_fix(e, "resume_player", "10.0.0.181:5555"))[0] is False
    assert run(run_fix(e, "resume_player", ON)) == (True, "media_play")
    assert run(run_fix(e, "retune_stick", ON)) == (True, "relink")
    assert run(run_fix(e, "clear_prompt", ON)) == (True, "prompt cleared")
    assert e.control.calls == [("restore", "youtubetv:espn", False), ("restore", "youtubetv:espn", True),
                               ("prompt", "youtubetv:espn")]
    e.control._visible_bad_since = None                              # playing again: nothing to do
    e.supervisor.t[0] += 10_000
    assert run(run_fix(e, "retune_stick", ON)) == (True, "already playing")


def test_app_fix_rate_limit_and_parts():
    from redzone.platform.agent_fix import run_fix
    e = fake_engine()
    assert run(run_fix(e, "restart_component", "fastcast")) == (True, "push feed restarting")
    assert run(run_fix(e, "restart_component", "fastcast")) == (False, "rate-limited by the app")
    assert run(run_fix(e, "restart_component", "decider"))[0] is False
    assert run(run_fix(e, "refresh_feed", "feeds")) == (True, "soft")
    assert run(run_fix(e, "safe_mode_off")) == (True, "safe mode off")
    e.decider.user.paused = True
    e.supervisor.t[0] += 10_000
    assert run(run_fix(e, "safe_mode_off")) == (False, "a person paused auto-switching")
    assert run(run_fix(e, "rm -rf", "x"))[0] is False


def test_view_reads_without_adb():
    from redzone.platform.agent_fix import view
    e = fake_engine()
    v = view(e, now=250.0)
    assert (v["on_screen"], v["standby"], v["screen"], v["screen_dead_s"]) == (ON, "10.0.0.181:5555", "paused",
                                                                              150.0)
    assert v["safe_mode"] and v["fixes_on"] and not v["net_down"]
    e.control._visible_bad_since = None
    assert view(e, now=250.0)["screen"] == ""
    e.source_status = {"espn": {"ok": False}, "nhl": {"ok": False}}
    assert view(e)["net_down"] is True


def test_endpoint_is_local_only_and_never_crashes(monkeypatch):
    from fastapi.testclient import TestClient

    from redzone.web import server
    called = []

    async def fake_run_fix(engine, fix, target):
        called.append((fix, target))
        raise RuntimeError("boom")
    monkeypatch.setattr("redzone.platform.agent_fix.run_fix", fake_run_fix)
    c = TestClient(server.build_app(fake_engine()))
    r = c.post("/api/agent/fix", json={"fix": "refresh_feed", "target": "feeds"})
    assert r.json()["ok"] is False and "crashed" in r.json()["message"] and called
