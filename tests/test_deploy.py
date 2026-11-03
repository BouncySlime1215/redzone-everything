"""scripts/deploy.py against a real throwaway git repo and a fake Mac (launchd, the app's API, the clock).

Nothing here touches launchctl, the network or the real checkout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from scripts import deploy as D

T0 = 1_800_000_000.0


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeMac(D.System):
    """The app after a restart behaves as `behaviour[commit]` says:
    ok · dead (never answers) · errors (logs a burst of new errors) · crash (pid keeps changing) ·
    feeds (a sports feed is down) · migrate (rewrites config + model files on start)."""

    def __init__(self, root: Path, behaviour: dict[str, str] | None = None):
        super().__init__(root)
        self.t = T0
        self.behaviour = behaviour or {}
        self.games: list[dict] = []
        self.games_until: float | None = None    # games disappear at this time (for --wait)
        self.restarts: list[tuple[str, str]] = []
        self.pushed: list[str] = []
        self.pid = 100
        self.free = 5000.0
        self.service_up = True
        self.old_warnings_per_min = 0

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD")

    def mode(self) -> str:
        return self.behaviour.get(self.head(), "ok")

    # time
    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        for _ in range(int(s // 10) or 1):
            self.t += min(10.0, s)
            m = self.mode()
            if m == "errors" and self.restarts:
                self.log("ERROR", "decider blew up: KeyError 'lock' in game 401")
            if m == "crash" and self.restarts:
                self.pid += 1

    def log(self, level: str, msg: str, t: float | None = None) -> None:
        p = self.root / "data/service.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(t if t is not None else self.t).strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a") as f:
            f.write(f"{stamp},123 {level} {msg}\n")

    # the app
    def fetch_state(self, api: str, timeout: float = 6.0):
        if not self.service_up or (self.restarts and self.mode() == "dead"):
            return None
        games = self.games if self.games_until is None or self.t < self.games_until else []
        feeds_bad = self.restarts and self.mode() == "feeds"
        return {"games": games,
                "health": {"sources": [{"name": "espn", "ok": not feeds_bad}, {"name": "nhl", "ok": True}],
                           "sticks": [{"host": "10.0.0.5", "state": "online"}]}}

    def service_pid(self, label: str):
        return self.pid

    def restart(self, label: str) -> bool:
        self.restarts.append((label, self.head()))
        if label == D.SERVICE_LABEL:
            self.pid += 1
            self.service_up = True
            if self.mode() == "migrate":
                (self.root / "data/config.yaml").write_text("schema: 2\n")
                (self.root / "agent/model/adapter/adapters.safetensors").write_bytes(b"NEW")
                (self.root / "agent/model/adapter/extra.bin").write_bytes(b"x")
        return True

    def free_mb(self, path: Path) -> float:
        return self.free

    def push(self, text: str) -> None:
        self.pushed.append(text)


@pytest.fixture
def world(tmp_path):
    """origin (bare) + the Mac's checkout on commit A; integration/release on origin at commit B."""
    origin, mac, dev = tmp_path / "origin.git", tmp_path / "mac", tmp_path / "dev"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True, capture_output=True)
    for repo_cfg in (["user.email", "t@t"], ["user.name", "t"]):
        git(dev, "config", *repo_cfg)
    (dev / "tests").mkdir()
    (dev / "tests/test_ok.py").write_text("def test_ok():\n    assert True\n")
    (dev / "app.txt").write_text("v1\n")
    (dev / "requirements.txt").write_text("pyyaml\n")
    (dev / ".gitignore").write_text("data/\nagent/model/\n")
    git(dev, "add", "-A")
    git(dev, "commit", "-qm", "A")
    git(dev, "branch", "-M", "main")
    git(dev, "push", "-q", "origin", "main")
    a = git(dev, "rev-parse", "HEAD")
    (dev / "app.txt").write_text("v2\n")
    git(dev, "commit", "-qam", "B")
    git(dev, "push", "-q", "origin", "HEAD:integration/release")
    b = git(dev, "rev-parse", "HEAD")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(mac)], check=True, capture_output=True)
    (mac / "data").mkdir()
    (mac / "data/config.yaml").write_text("teams: {nfl: [WSH]}\n")
    (mac / "agent/model/adapter").mkdir(parents=True)
    (mac / "agent/model/adapter/adapters.safetensors").write_bytes(b"OLD")
    fake = FakeMac(mac)
    s = D.Settings(test_args=["-q", "-p", "no:cacheprovider"])

    class W:
        pass
    w = W()
    w.dev, w.mac, w.a, w.b, w.fake, w.s = dev, mac, a, b, fake, s
    w.deployer = lambda: D.Deployer(w.s, w.fake, mac, out=lambda *_: None)
    w.push_commit = lambda text, name="app.txt": _push(dev, text, name)
    return w


def _push(dev: Path, text: str, name: str) -> str:
    (dev / name).write_text(text)
    git(dev, "add", "-A")
    git(dev, "commit", "-qm", f"edit {name}")
    git(dev, "push", "-q", "origin", "HEAD:integration/release")
    return git(dev, "rev-parse", "HEAD")


def state(w) -> dict:
    return json.loads((w.mac / "data/deploy/state.json").read_text())


# ---- the happy path -------------------------------------------------------------------------------

def test_healthy_deploy_switches_watches_ten_minutes_and_records_good(world):
    w = world
    assert w.deployer().deploy() == D.EXIT_OK
    assert w.fake.head() == w.b
    assert [label for label, _ in w.fake.restarts] == [D.SERVICE_LABEL, D.AGENT_LABEL]
    assert w.fake.t - T0 >= w.s.watch_s                       # really watched the whole window
    st = state(w)
    assert st["good"]["commit"] == w.b and st["previous"]["commit"] == w.a
    assert "pending" not in st
    assert not (w.mac / "data/deploy/in_progress.json").exists()
    assert "healthy" in w.fake.pushed[-1]


def test_runs_the_test_suite_on_the_new_commit_off_to_the_side(world):
    w = world
    assert w.deployer().deploy(dry_run=True) == D.EXIT_OK      # real pytest in the stage worktree
    assert w.fake.head() == w.a and w.fake.restarts == []      # dry run touched nothing
    assert not (w.mac / "data/deploy/stage").exists()
    assert "stage" not in git(w.mac, "worktree", "list")


def test_failing_tests_stop_before_anything_changes(world):
    w = world
    w.push_commit("def test_broken():\n    assert False\n", "tests/test_broken.py")
    assert w.deployer().deploy() == D.EXIT_TESTS
    assert w.fake.head() == w.a and w.fake.restarts == []
    assert "tests failed" in w.fake.pushed[-1]
    assert not (w.mac / "data/deploy/state.json").exists() or "pending" not in state(w)


def test_already_current_is_a_no_op(world):
    w = world
    git(w.mac, "fetch", "-q", "origin", "integration/release")
    git(w.mac, "checkout", "-q", "--detach", w.b)
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    assert w.fake.restarts == []


# ---- never during a game --------------------------------------------------------------------------

def test_live_game_defers_without_restarting(world):
    w = world
    w.fake.games = [{"id": "1", "matchup": "DAL @ WSH", "phase": "live"}]
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_DEFERRED
    assert w.fake.head() == w.a and w.fake.restarts == []


def test_game_starting_inside_the_watch_window_counts_as_live(world):
    w = world
    w.fake.games = [{"id": "1", "matchup": "BOS @ NYR", "phase": "pre", "start_time": T0 + 300}]
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_DEFERRED
    w.fake.games = [{"id": "1", "matchup": "BOS @ NYR", "phase": "pre", "start_time": T0 + 4 * 3600}]
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK


def test_wait_polls_until_the_games_end(world):
    w = world
    w.s.live_wait_s = 3 * 3600
    w.fake.games = [{"id": "1", "matchup": "DAL @ WSH", "phase": "live"}]
    w.fake.games_until = T0 + 3600
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    switch_at = [r for r in map(json.loads, (w.mac / "data/deploy/deploy.jsonl").read_text().splitlines())
                 if r["step"] == "switch"][0]["ts"]
    assert switch_at >= T0 + 3600


def test_down_app_does_not_block_a_deploy(world):
    w = world
    w.fake.service_up = False
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    assert w.fake.head() == w.b


# ---- auto-rollback -------------------------------------------------------------------------------

def test_app_that_never_comes_up_rolls_back_code_config_and_models(world):
    w = world
    w.fake.behaviour[w.b] = "dead"   # ...after rewriting config + the adapter on its way down
    orig_restart = w.fake.restart

    def restart(label):
        if w.fake.head() == w.b and label == D.SERVICE_LABEL:
            (w.mac / "data/config.yaml").write_text("schema: 2\n")
            (w.mac / "agent/model/adapter/extra.bin").write_bytes(b"x")
        return orig_restart(label)
    w.fake.restart = restart
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert w.fake.head() == w.a
    assert (w.mac / "data/config.yaml").read_text() == "teams: {nfl: [WSH]}\n"
    assert not (w.mac / "agent/model/adapter/extra.bin").exists()
    assert (w.mac / "agent/model/adapter/adapters.safetensors").read_bytes() == b"OLD"
    assert w.fake.t - T0 < 300                      # gave up after the startup grace, not 10 min
    st = state(w)
    assert st["good"]["commit"] == w.a and "pending" not in st
    assert st["history"][-1]["result"] == "rolled_back"
    assert "rolled back" in w.fake.pushed[-1]


def test_burst_of_new_errors_rolls_back(world):
    w = world
    w.fake.behaviour[w.b] = "errors"
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert w.fake.head() == w.a
    reasons = state(w)["history"][-1]["reasons"]
    assert any("new kind" in r for r in reasons)


def test_old_versions_usual_warnings_are_not_held_against_the_new_one(world):
    w = world
    for i in range(20):     # the old version logged this warning every 30 s
        w.fake.log("WARNING", "fastcast reconnect for game 401", t=T0 - 600 + i * 30)

    def sleep(s, _orig=w.fake.sleep):
        _orig(s)
        if int(w.fake.t) % 30 == 0:
            w.fake.log("WARNING", "fastcast reconnect for game 402")
    w.fake.sleep = sleep
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK


def test_crash_loop_rolls_back_early(world):
    w = world
    w.fake.behaviour[w.b] = "crash"
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert w.fake.head() == w.a
    assert w.fake.t - T0 < w.s.watch_s              # didn't sit through the full 10 minutes
    assert any("crashed" in r for r in state(w)["history"][-1]["reasons"])


def test_feed_failing_only_on_the_new_version_rolls_back(world):
    w = world
    w.fake.behaviour[w.b] = "feeds"
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert any("feed" in r for r in state(w)["history"][-1]["reasons"])


def test_failed_checkout_is_rolled_back_not_left_half_done(world):
    w = world
    c = w.push_commit("new\n", "data_seed.txt")
    (w.mac / "data_seed.txt").write_text("untracked local file in the way\n")
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLED_BACK
    assert w.fake.head() == w.a and c != w.a
    assert any("deploy step failed" in r for r in state(w)["history"][-1]["reasons"])


def test_failed_rollback_is_loud_and_blocks_the_next_deploy(world):
    w = world
    w.fake.behaviour[w.b] = "dead"
    w.fake.behaviour[w.a] = "dead"      # the old version won't start either (e.g. the Mac is broken)
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLBACK_FAILED
    assert "ROLLBACK FAILED" in w.fake.pushed[-1]
    assert state(w)["pending"]["rollback_failed"]
    with pytest.raises(D.DeployError, match="never finished"):
        w.deployer().deploy(skip_tests=True)


def test_interrupted_watch_blocks_until_rollback_or_confirm(world):
    w = world
    w.deployer()._save_state({"good": {"commit": w.a, "snapshot": None}, "pending": {"commit": w.b}})
    with pytest.raises(D.DeployError, match="never finished"):
        w.deployer().deploy(skip_tests=True)
    assert w.deployer().confirm() == D.EXIT_OK
    assert "pending" not in state(w)


# ---- manual rollback + snapshots ------------------------------------------------------------------

def test_manual_rollback_after_a_good_deploy_goes_to_the_previous_version(world):
    w = world
    w.fake.behaviour[w.b] = "migrate"   # healthy, but rewrites config/models on first start
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    assert (w.mac / "data/config.yaml").read_text() == "schema: 2\n"
    assert w.deployer().rollback() == D.EXIT_OK
    assert w.fake.head() == w.a
    assert (w.mac / "data/config.yaml").read_text() == "teams: {nfl: [WSH]}\n"
    assert (w.mac / "agent/model/adapter/adapters.safetensors").read_bytes() == b"OLD"
    assert not (w.mac / "agent/model/adapter/extra.bin").exists()
    assert state(w)["good"]["commit"] == w.a


def test_snapshots_dedupe_restore_exactly_and_prune(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/config.yaml").write_text("a: 1\n")
    (tmp_path / "agent/model").mkdir(parents=True)
    (tmp_path / "agent/model/w.bin").write_bytes(b"W" * 1000)
    sn = D.Snapshots(tmp_path, ["data/config.yaml", "agent/model"], keep=2)
    first = sn.save("a" * 40, T0)
    second = sn.save("a" * 40, T0)                         # same second: unique id anyway
    assert first != second
    assert len(list(sn.store.iterdir())) == 2              # identical content stored once
    (tmp_path / "data/config.yaml").write_text("a: 2\n")
    (tmp_path / "agent/model/stray.bin").write_bytes(b"s")
    third = sn.save("b" * 40, T0 + 1)
    changed = sn.restore(first)
    assert (tmp_path / "data/config.yaml").read_text() == "a: 1\n"
    assert not (tmp_path / "agent/model/stray.bin").exists()
    assert set(changed) == {"data/config.yaml", "-agent/model/stray.bin"}
    sn.prune(protect={first})
    ids = {r["id"] for r in sn.list()}
    assert ids == {first, second, third}                   # newest 2, plus the protected one
    sn.prune(protect=set())
    assert {r["id"] for r in sn.list()} == {second, third}
    assert sn.restore(third)                               # its blobs survived the prune
    assert set(sn.restore(second)) == {"data/config.yaml", "-agent/model/stray.bin"}


def test_missing_config_in_a_snapshot_is_removed_on_restore(tmp_path):
    sn = D.Snapshots(tmp_path, ["data/config.yaml"])
    rid = sn.save("c" * 40, T0)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/config.yaml").write_text("new: 1\n")
    sn.restore(rid)
    assert not (tmp_path / "data/config.yaml").exists()


def test_restore_refuses_a_snapshot_with_missing_blobs(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/config.yaml").write_text("a: 1\n")
    sn = D.Snapshots(tmp_path, ["data/config.yaml"])
    rid = sn.save("c" * 40, T0)
    for b in sn.store.iterdir():
        b.unlink()
    (tmp_path / "data/config.yaml").write_text("keep me\n")
    with pytest.raises(D.DeployError, match="missing"):
        sn.restore(rid)
    assert (tmp_path / "data/config.yaml").read_text() == "keep me\n"
    with pytest.raises(D.DeployError, match="bad snapshot id"):
        sn.restore("../../etc/passwd")


# ---- refusals ------------------------------------------------------------------------------------

def test_refuses_with_edited_tracked_files(world):
    w = world
    (w.mac / "app.txt").write_text("local edit\n")
    with pytest.raises(D.DeployError, match="edited"):
        w.deployer().deploy(skip_tests=True)
    assert w.fake.restarts == []


def test_refuses_when_requirements_change_unless_told(world):
    w = world
    w.push_commit("pyyaml\nnumpy\n", "requirements.txt")
    with pytest.raises(D.DeployError, match="requirements"):
        w.deployer().deploy(skip_tests=True)
    assert w.deployer().deploy(skip_tests=True, deps_ok=True) == D.EXIT_OK


def test_disk_guard(world):
    w = world
    w.fake.free = 900
    with pytest.raises(D.DeployError, match="MB free"):
        w.deployer().deploy(skip_tests=True)
    assert w.fake.head() == w.a


def test_one_deploy_at_a_time(world):
    w = world
    d = w.deployer()
    with d.lock():
        with pytest.raises(D.DeployError, match="another deploy"):
            w.deployer().deploy(skip_tests=True)


def test_cli_returns_error_code_not_traceback(world, capsys):
    w = world
    (w.mac / "app.txt").write_text("local edit\n")
    assert D.main(["deploy", "--skip-tests"], system=w.fake, root=w.mac) == D.EXIT_ERROR
    assert "edited" in capsys.readouterr().err


# ---- settings + pieces ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,match", [
    ("bogus: 1\n", "unknown setting"),
    ("watch_s: ten\n", "should be float"),
    ("managed: [/etc/passwd]\n", "inside the checkout"),
    ("managed: [../x]\n", "inside the checkout"),
    ("watch_s: 10\nsample_s: 30\n", "watch_s"),
    ("- a\n", "mapping"),
])
def test_settings_validation(tmp_path, text, match):
    p = tmp_path / "deploy.yaml"
    p.write_text(text)
    with pytest.raises(ValueError, match=match):
        D.Settings.load(p)


def test_settings_accepts_ints_for_floats(tmp_path):
    p = tmp_path / "deploy.yaml"
    p.write_text("watch_s: 300\nbranch: integration/next\n")
    s = D.Settings.load(p)
    assert s.watch_s == 300.0 and isinstance(s.watch_s, float) and s.branch == "integration/next"


def test_shipped_settings_file_is_valid():
    D.Settings.load(Path(D.ROOT) / "deploy/deploy.yaml")


def test_log_errors_reads_rotated_files_and_skips_tracebacks(tmp_path):
    log = tmp_path / "service.log"
    stamp = datetime.fromtimestamp(T0).strftime("%Y-%m-%d %H:%M:%S")
    old = datetime.fromtimestamp(T0 - 3600).strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "service.log.1").write_text(f"{stamp},1 WARNING HOME flip on 10.0.0.5: never active\n")
    log.write_text(f"{stamp},2 ERROR boom 12\n{stamp},3 ERROR stderr: Traceback (most recent call last):\n"
                   f"{old},4 ERROR too old\n{stamp},5 INFO fine\n")
    import os
    os.utime(tmp_path / "service.log.1", (T0 - 30, T0 - 30))
    c = D.log_errors(log, T0 - 60, T0)
    assert sum(c.values()) == 2
    os.utime(tmp_path / "service.log.1", (T0 - 600, T0 - 600))   # rotated before the window
    c = D.log_errors(log, T0 - 60, T0)
    assert sum(c.values()) == 1
    os.utime(tmp_path / "service.log.1", (T0 - 30, T0 - 30))
    c = D.log_errors(log, T0 - 60, T0)
    assert sum(c.values()) == 2
    assert D.switch_failures(c) == 1


def test_judge_needs_more_than_noise():
    s = D.Settings()
    ok = D.Health(api_ok=True)
    samples = [ok] * 20
    from collections import Counter
    base = Counter({"WARNING x": 4})
    assert not D.judge(s, ok, base, samples, Counter({"WARNING x": 8}), 0, True).worse
    assert D.judge(s, ok, base, samples, Counter({"WARNING x": 20}), 0, True).worse
    flaky = [ok] * 17 + [D.Health(api_ok=False)] * 3
    assert D.judge(s, ok, base, flaky, base, 0, True).worse
    one_miss = [ok] * 19 + [D.Health(api_ok=False)]
    assert not D.judge(s, ok, base, one_miss, base, 0, True).worse


def test_busy():
    assert D.busy(None, T0, 900) == ""
    assert "live" in D.busy({"games": [{"matchup": "A @ B", "phase": "live"}]}, T0, 900)
    assert D.busy({"games": [{"phase": "pre", "start_time": T0 + 2000}]}, T0, 900) == ""
    assert D.busy({"games": [{"phase": "pre", "start_time": None}, {"phase": "post"}]}, T0, 900) == ""


def test_watchdog_leaves_restarts_to_a_running_deploy(tmp_path, monkeypatch):
    from agent import watchdog
    from agent.policy import Verdict
    monkeypatch.setattr(watchdog, "DATA", tmp_path)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/in_progress.json").write_text(json.dumps({"until": T0 + 60}))
    calls = []
    monkeypatch.setattr(watchdog.subprocess, "run",
                        lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    w = watchdog.Watchdog(clock=lambda: T0)
    v = Verdict(action="restart_service", why="api down")
    assert "deploy in progress" in w.act(v, None)
    assert calls == []
    w2 = watchdog.Watchdog(clock=lambda: T0 + 120)       # marker expired: back to normal
    monkeypatch.setattr(w2, "_push", lambda t: None)
    w2.act(v, None)
    assert calls


@pytest.mark.skipif(sys.platform != "darwin", reason="taskpolicy is macOS-only")
def test_tests_run_under_taskpolicy_on_the_mac():
    assert D.System().low_priority(["x"])[:2] == ["taskpolicy", "-b"]


def test_warm_up_warnings_right_after_the_restart_are_not_a_regression(world):
    w = world
    orig = w.fake.restart

    def restart(label):
        ok = orig(label)
        if label == D.SERVICE_LABEL:
            for i in range(6):    # the new process reconnecting sticks while it starts
                w.fake.log("WARNING", f"stick startup check failed on 10.0.0.{i}; keepalive will retry")
        return ok
    w.fake.restart = restart
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK


def test_ignore_patterns_drop_known_harmless_lines(world):
    w = world
    w.fake.behaviour[w.b] = "errors"
    w.s.ignore = [r"decider blew up"]
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK


def test_manual_rollback_refuses_to_overwrite_local_edits(world):
    w = world
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    (w.mac / "app.txt").write_text("hand fix during the game\n")
    with pytest.raises(D.DeployError, match="overwrite"):
        w.deployer().rollback()
    assert (w.mac / "app.txt").read_text() == "hand fix during the game\n"


def test_snapshots_referenced_by_state_survive_pruning(world):
    w = world
    w.s.keep_releases = 1
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK
    for i in range(3):
        w.deployer().snaps.save(w.b, w.fake.t + i + 1, "manual")
    w.deployer().snaps.prune(w.deployer()._protected(state(w)))
    assert w.deployer().rollback() == D.EXIT_OK            # previous's snapshot is still there
    assert (w.mac / "data/config.yaml").read_text() == "teams: {nfl: [WSH]}\n"
