"""Card D deploy fixes: rollbacks and tests wait for a quiet Mac (P-05, P-06), a rollback never silently drops
settings (P-13), a healthy deploy is not rolled back on a feed flap (P-15), a dead watchdog is said out loud (P-02)."""
from __future__ import annotations

from collections import Counter

from scripts import deploy as D
from test_deploy import FakeMac, T0, state, world  # noqa: F401  (fixtures)

LIVE = [{"id": "1", "matchup": "DAL @ WSH", "phase": "live", "on_now": True, "status_text": "3rd 4:12"}]
HALF = [{"id": "1", "matchup": "DAL @ WSH", "phase": "live", "on_now": True, "status_text": "Halftime"}]


def _games_after_restart(fake: FakeMac, until: float, games=LIVE) -> None:
    """The app comes back from the deploy's restart with a live game on screen until `until` seconds later."""
    real = fake.fetch_state
    fake.live_since = None

    def fetch_state(api, timeout=6.0):
        st = real(api, timeout)
        if st is not None and fake.restarts:
            fake.live_since = fake.live_since or fake.t
            if fake.t < fake.live_since + until:
                st = {**st, "games": games}
        return st
    fake.fetch_state = fetch_state


# ---- P-05 ---------------------------------------------------------------------------------------

def test_p05_a_rollback_waits_for_a_break_while_a_game_is_on_screen(world):
    """Repro: the rollback restart had no busy() check at all: about 10 minutes after the at-break restart (mid 3rd
    quarter) rollback_to() restarted the app again. Cause: busy() guarded deploy() only, not _switch_and_watch's rollback."""
    w = world
    w.fake.behaviour[w.b] = "errors"
    _games_after_restart(w.fake, until=3000)
    restarts_at_rollback = []
    real = D.Deployer.rollback_to

    def spy(self, *a, **k):
        restarts_at_rollback.append(w.fake.t)
        return real(self, *a, **k)
    D.Deployer.rollback_to = spy
    try:
        assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    finally:
        D.Deployer.rollback_to = real
    switch_ts = [r for r in map(__import__("json").loads, (w.mac / "data/deploy/deploy.jsonl").read_text().splitlines())
                 if r["step"] == "rollback_waiting"]
    assert switch_ts, "the rollback never said it was waiting"
    assert restarts_at_rollback[0] >= w.fake.live_since + 3000        # only once the game was off screen
    assert any("next break" in m for m in w.fake.pushed)
    assert w.fake.head() == w.a


def test_p05_a_rollback_goes_ahead_at_halftime(world):
    w = world
    w.fake.behaviour[w.b] = "errors"
    _games_after_restart(w.fake, until=10 ** 6, games=HALF)
    assert w.deployer().deploy(skip_tests=True, deps_ok=True) == D.EXIT_ROLLED_BACK
    assert not any(r["step"] == "rollback_waiting" for r in map(
        __import__("json").loads, (w.mac / "data/deploy/deploy.jsonl").read_text().splitlines()))


def test_p05_a_dead_app_is_rolled_back_at_once_even_with_games(world):
    w = world
    w.fake.behaviour[w.b] = "dead"
    _games_after_restart(w.fake, until=10 ** 6)       # (a dead app answers nothing, so no game is visible)
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert w.fake.t - T0 < 1500


def test_p05_the_wait_is_bounded(world):
    w = world
    w.s.rollback_wait_s = 600.0
    w.fake.behaviour[w.b] = "errors"
    _games_after_restart(w.fake, until=10 ** 6)
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert any(r["step"] == "rollback_gate_expired" for r in map(
        __import__("json").loads, (w.mac / "data/deploy/deploy.jsonl").read_text().splitlines()))


# ---- P-06 ---------------------------------------------------------------------------------------

def test_p06_tests_do_not_start_while_a_game_is_live(world, monkeypatch):
    """Repro: deploy() ran run_tests() (4 xdist workers, 30-60 min) BEFORE wait_idle(), with no busy check, over
    Saturday's games. Now it defers before spawning pytest."""
    w = world
    w.fake.games = LIVE
    spawned = []
    monkeypatch.setattr(D.Deployer, "run_tests", lambda self, c: spawned.append(c) or (True, ""))
    assert w.deployer().deploy() == D.EXIT_DEFERRED
    assert spawned == [] and w.fake.restarts == []
    assert state(w)["machine"]["state"] == "deferred"
    w.fake.games = []
    assert w.deployer().deploy() == D.EXIT_OK and spawned


def test_p06_tests_do_not_start_while_the_governor_is_not_normal(world, monkeypatch):
    w = world
    real = w.fake.fetch_state
    w.fake.fetch_state = lambda api, timeout=6.0: {**real(api, timeout), "health": {
        "governor": {"level": "eco", "why": "memory is tight"}, "sources": [], "sticks": []}}
    spawned = []
    monkeypatch.setattr(D.Deployer, "run_tests", lambda self, c: spawned.append(c) or (True, ""))
    assert w.deployer().deploy() == D.EXIT_DEFERRED and spawned == []
    w.s.test_when_busy = True
    assert w.deployer().deploy() == D.EXIT_OK and spawned


# ---- P-13 ---------------------------------------------------------------------------------------

def test_p13_a_setting_saved_during_the_watch_is_named_and_kept_when_a_rollback_replaces_it(world):
    """Repro: the rollback restored the snapshot taken BEFORE the switch, so a setting saved during the 10-minute watch
    was overwritten with only 'restored N config file(s)' in the journal."""
    w = world
    w.fake.behaviour[w.b] = "errors"
    real_sleep = w.fake.sleep
    done = []

    def sleep(s):
        real_sleep(s)
        if w.fake.restarts and not done:
            done.append(1)
            (w.mac / "data/config.yaml").write_text("teams: {nfl: [WSH]}\nmode: {dry_run: false}\n")   # a phone edit
    w.fake.sleep = sleep
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert (w.mac / "data/config.yaml").read_text() == "teams: {nfl: [WSH]}\n"        # restored, as before
    note = [m for m in w.fake.pushed if "Rollback replaced" in m]
    assert note and "mode.dry_run" in note[0]
    snap_id = note[0].split("snapshot ")[1].split(" ")[0]
    snaps = D.Snapshots(w.mac, w.s.managed)
    assert snaps.get(snap_id)["files"]["data/config.yaml"]                              # restorable
    assert snap_id.endswith("-discarded")


# ---- P-15 ---------------------------------------------------------------------------------------

def _h(bad=(), off=()):
    return D.Health(api_ok=True, sources_bad=len(bad), sticks_offline=len(off), bad_names=frozenset(bad),
                    offline_hosts=frozenset(off))


def test_p15_a_feed_flap_at_the_end_does_not_roll_back_but_a_standing_outage_does():
    """Repro: Nov 8 00:09 'worse: 1 sports feed(s) failing vs 0 before', Nov 4 21:25/21:39 '2 vs 1', '2 vs 0': each a
    healthy deploy rolled back on the last 3 samples vs ONE baseline sample, then redeployed and passing."""
    s = D.Settings()                                           # sample_s 30, feed_bad_s 180 -> the last 6 samples
    ok = _h()
    flap = [ok] * 17 + [_h(["nhl"])] * 3                       # down in the last 90 s only
    assert not D.judge(s, ok, Counter(), flap, Counter(), 0, True).worse
    wobble = [ok] * 14 + [_h(["nhl"]), _h(["espn"]), _h(["nhl"]), _h(["mlb"]), _h(["espn"]), _h(["nhl"])]
    assert not D.judge(s, ok, Counter(), wobble, Counter(), 0, True).worse     # a different feed each time
    standing = [ok] * 10 + [_h(["nhl"])] * 10
    v = D.judge(s, ok, Counter(), standing, Counter(), 0, True)
    assert v.worse and "nhl" in v.reasons[0]
    assert not D.judge(s, _h(["nhl"]), Counter(), standing, Counter(), 0, True).worse   # it was down before too
    crash = [ok] * 17 + [D.Health(api_ok=False)] * 3
    assert D.judge(s, ok, Counter(), crash, Counter(), 0, True).worse                  # a crash loop still rolls back


def test_p15_the_rollback_waits_300_s_for_the_app_not_120():
    assert D.Settings().rollback_grace_s == 300.0


def test_p15_a_stick_that_was_already_offline_is_not_the_new_versions_fault():
    s = D.Settings()
    samples = [_h(off=["10.0.0.5"])] * 20
    assert not D.judge(s, _h(off=["10.0.0.5"]), Counter(), samples, Counter(), 0, True).worse
    assert D.judge(s, _h(), Counter(), samples, Counter(), 0, True).worse


# ---- P-02 ---------------------------------------------------------------------------------------

def test_p02_a_watchdog_that_will_not_restart_is_journaled_and_pushed_once(world):
    w = world
    real = w.fake.restart
    w.fake.restart = lambda label: False if label == D.AGENT_LABEL else real(label)
    w.fake.behaviour[w.b] = "errors"
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK       # switch + rollback: two agent restarts
    told = [m for m in w.fake.pushed if "watchdog is not running" in m]
    assert len(told) == 1 and "agent.install" in told[0]
    rows = [r for r in map(__import__("json").loads, (w.mac / "data/deploy/deploy.jsonl").read_text().splitlines())
            if r["step"] == "agent_restart_failed"]
    assert len(rows) == 2
