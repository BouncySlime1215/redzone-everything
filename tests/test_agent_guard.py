"""The PreToolUse guard (.claude/hooks/guard.py) blocks what CLAUDE.md says must never happen, and lets
ordinary work through. docs/coordination.md G1-G7."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"


def run(tool, root, **inp):
    ev = {"tool_name": tool, "tool_input": inp}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(ev), capture_output=True, text=True,
                       env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"}, timeout=10)
    return r.returncode, r.stderr


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.mark.parametrize("cmd,rule", [
    ("adb -s 1.2.3.4:5555 shell input keyevent 3", "G3"),
    ("python scripts/tv_practice.py --go", "G3"),
    ("python scripts/train_brain3.py", "G4"),
    ("python scripts/deploy.py deploy", "G5"),
    ("git push origin main", "G2"),
    ("git push origin HEAD:codex/resume-redzone", "G2"),
    ("git push --force origin my-branch", "G2"),
    ("gh pr create --title x --body y", "G2"),
    ("cat data/tv_token.txt", "G1"),
    ("printenv", "G1"),
    ("echo x > docs/gate_baseline.json", "G6"),
    ("sed -i '' 's/a/b/' scripts/release_gate.py", "G6"),
    ("touch data/.agent_hw_ok", "G6"),
    ("bash -c 'adb shell input keyevent 3'", "G3"),
    ('sh -c "adb devices"', "G3"),
    ("/usr/local/bin/adb devices", "G3"),
    ('"$HOME/platform-tools/adb" devices', "G3"),
    ("~/Library/Android/sdk/platform-tools/adb devices", "G3"),
    ("ADB=adb; $ADB devices", "G3"),
    ("python3 -c \"import subprocess; subprocess.run(['adb','devices'])\"", "G3"),
    ('git commit -m "$(adb devices)"', "G3"),
    ("git -C . push origin main", "G2"),
    ("git -c a=b push --force origin my-branch", "G2"),
])
def test_blocked(root, cmd, rule):
    code, err = run("Bash", root, command=cmd)
    assert code == 2 and rule in err and "Instead:" in err


@pytest.mark.parametrize("cmd", [
    "git status",
    "python -m pytest -q tests/test_fsm.py",
    "python scripts/deploy.py --dry-run",
    "python scripts/deploy.py status",
    "git push origin claude/my-branch",
    "gh pr create --base integration/next --title x --body y",
    "python scripts/citymap.py --focus redzone/fsm.py --tokens 1500",
    "grep -rn max_tokens redzone",
    "cat redzone/adb.py",
    "ls tools/adb/",
    "python -m pytest -q tests/fake_adb.py",
    "git commit -m 'Reconnect adb after a reboot'",
    'git commit -am "adb: retry once"',
    'grep -rn "adb shell" redzone',
])
def test_allowed(root, cmd):
    assert run("Bash", root, command=cmd)[0] == 0


def test_hardware_allowed_once_nick_creates_the_flag(root):
    (root / "data" / ".agent_hw_ok").write_text("")
    assert run("Bash", root, command="adb devices")[0] == 0


def test_protected_edits_are_blocked(root):
    for p in ["tests/conftest.py", ".github/workflows/ci.yml", "scripts/release_gate.py", ".claude/hooks/guard.py"]:
        code, err = run("Edit", root, file_path=str(root / p), new_string="x", old_string="y")
        assert code == 2 and "G6" in err, p


def test_protected_paths_in_another_worktree_are_blocked(root):
    code, err = run("Edit", root, file_path="/private/tmp/scratch/wt/.claude/hooks/guard.py", new_string="x", old_string="y")
    assert code == 2 and "G6" in err
    code, err = run("Edit", root, file_path="/private/tmp/scratch/wt/scripts/release_gate.py", new_string="x", old_string="y")
    assert code == 2 and "G6" in err
    assert run("Edit", root, file_path="/private/tmp/scratch/wt/redzone/fsm.py", new_string="x", old_string="y")[0] == 0


def test_normal_edits_pass(root):
    assert run("Edit", root, file_path=str(root / "redzone/fsm.py"), new_string="x", old_string="y")[0] == 0


def test_peacock_provider_is_blocked_but_a_ban_line_is_not(root):
    code, err = run("Write", root, file_path=str(root / "redzone/default_config.yaml"), content="providers: [peacock]")
    assert code == 2 and "G7" in err
    assert run("Write", root, file_path=str(root / "redzone/x.py"), content="# never use Peacock")[0] == 0


def test_secret_files_are_not_read(root):
    assert run("Read", root, file_path=str(root / "data/smartthings_oauth.json"))[0] == 2
    assert run("Read", root, file_path=str(root / "redzone/tokens.py"))[0] == 0


@pytest.mark.parametrize("tool", ["mcp__terminal__run_in_terminal", "Monitor"])
@pytest.mark.parametrize("cmd,rule", [
    ("adb devices", "G3"),
    ("python scripts/deploy.py deploy", "G5"),
    ("cat data/tv_token.txt", "G1"),
    ("git push origin main", "G2"),
])
def test_other_shell_tools_get_the_shell_rules(root, tool, cmd, rule):
    code, err = run(tool, root, command=cmd)
    assert code == 2 and rule in err


def test_terminal_tool_allows_ordinary_commands(root):
    assert run("mcp__terminal__run_in_terminal", root, command="git status")[0] == 0


@pytest.mark.parametrize("tool", ["mcp__computer-use__app_click", "mcp__computer-use__computer_batch"])
def test_computer_use_needs_nicks_flag(root, tool):
    code, err = run(tool, root, app="Terminal")
    assert code == 2 and "G3" in err
    (root / "data" / ".agent_hw_ok").write_text("")
    assert run(tool, root, app="Terminal")[0] == 0


@pytest.mark.parametrize("inp", [
    {"pattern": "x", "path": "data/smartthings_oauth.json"},
    {"pattern": "x", "path": "/repo/.env"},
    {"pattern": "x", "glob": "**/*token*"},
    {"pattern": "x", "glob": ".env*"},
])
def test_grep_on_secret_paths_is_blocked(root, inp):
    code, err = run("Grep", root, **inp)
    assert code == 2 and "G1" in err


@pytest.mark.parametrize("inp", [{"pattern": "**/.env*"}, {"pattern": "data/*token*"}, {"pattern": "*", "path": "data/secrets"}])
def test_glob_on_secret_paths_is_blocked(root, inp):
    code, err = run("Glob", root, **inp)
    assert code == 2 and "G1" in err


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "results/locked/night.json"}),
    ("Grep", {"pattern": "x", "path": "data/locked"}),
    ("Glob", {"pattern": "results/locked/**"}),
    ("Glob", {"pattern": "*", "path": "/Users/x/redzone-vault"}),
])
def test_locked_results_are_not_searched_or_read(root, tool, inp):
    code, err = run(tool, root, **inp)
    assert code == 2 and "G8" in err


@pytest.mark.parametrize("tool,inp", [
    ("Grep", {"pattern": "token", "path": "redzone", "glob": "*.py"}),
    ("Grep", {"pattern": "password"}),
    ("Glob", {"pattern": "tests/test_*.py"}),
    ("Glob", {"pattern": "**/*tokens.py"}),
])
def test_ordinary_searches_pass(root, tool, inp):
    assert run(tool, root, **inp)[0] == 0


def test_a_crash_fails_open_and_is_logged_without_the_input(root):
    ev = json.dumps({"tool_name": "Bash", "tool_input": "hunter2-not-a-dict"})
    r = subprocess.run([sys.executable, str(GUARD)], input=ev, capture_output=True, text=True,
                       env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"}, timeout=10)
    assert r.returncode == 0 and "allowed" in r.stderr
    log = (root / "data" / "guard_errors.log").read_text()
    assert "Bash" in log and "hunter2" not in log


def _settings():
    return json.loads((ROOT / ".claude" / "settings.json").read_text())


def test_settings_route_every_guarded_tool_through_the_hook():
    import re
    entry = _settings()["hooks"]["PreToolUse"][0]
    for tool in ("Bash", "Read", "Grep", "Glob", "Monitor", "mcp__terminal__run_in_terminal", "mcp__computer-use__app_click"):
        assert re.fullmatch(entry["matcher"], tool), tool
    assert entry["hooks"][0]["onFailure"] == "block"


def test_git_push_always_asks():
    assert "Bash(git push *)" in _settings()["permissions"]["ask"]


def test_stop_hooks_are_wired_within_their_timeout():
    s = _settings()["hooks"]
    h = s["Stop"][0]["hooks"][0]
    assert "stop_gate.py" in h["command"] and h["timeout"] >= 60
    assert "SubagentStop" not in s         # parallel workers' half-done files must not block each other


def test_hardware_stays_unlockable_by_the_flag():
    """adb, the terminal tool and computer use are guarded by guard.py G3 (data/.agent_hw_ok), never by a deny rule,
    which no flag could lift: the coordinator has to be able to run Nick's TV tests."""
    deny = " ".join(_settings()["permissions"]["deny"])
    assert "adb" not in deny and "mcp__terminal" not in deny and "mcp__computer-use" not in deny


def test_bad_input_fails_open():
    r = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True, timeout=10)
    assert r.returncode == 0


@pytest.mark.parametrize("cmd", [
    "cat results/locked/night.json",
    "grep -r x data/locked/",
    "python3 -c \"open('/Users/me/redzone-vault/x').read()\"",
    "ls ~/redzone-vault",
    "python scripts/ci_scorecard.py --split test --out x.json",
    "python scripts/eval.py --n 99 --split=test",
    ".venv/bin/python scripts/eval_sports.py nba --split test",
    "python scripts/northstar.py",
    "python scripts/northstar.py --split locked",
    "python scripts/northstar.py --split both --no-write",
    "gh run download 123",
    "gh run download 123 -n scorecard",
    "gh api repos/o/r/actions/artifacts/5/zip",
])
def test_g8_blocks_shell_routes_to_locked_results(root, cmd):
    rc, err = run("Bash", root, command=cmd)
    assert rc == 2 and "G8" in err


@pytest.mark.parametrize("cmd", [
    "git add docs/agents/locked-results/2026-11-09-combined-design.md",
    "python scripts/ci_scorecard.py --split fit --out x.json",
    "python scripts/northstar.py --split dev --no-write --json a.json",
    "python scripts/northstar.py --delta a.json b.json --md n.md",
    "sed -n 1,5p scripts/northstar.py",
    "gh run download 123 -n lint",
    "gh run download 123 --name pytest-3.12",
    "gh run view 123 --log-failed",
    'grep -rn "results/locked" docs',
    "python scripts/results_ledger.py verify",
    "python scripts/results_ledger.py append --kind live --suite hdmi1 --verdict PASS",
])
def test_g8_leaves_ordinary_work_alone(root, cmd):
    rc, err = run("Bash", root, command=cmd)
    assert rc == 0, err


@pytest.mark.parametrize("path", ["results/ledger.jsonl", "results/HEAD", "results/summary/latest.json",
                                  "results/locked/x.json", "scripts/results_ledger.py"])
def test_the_ledger_is_written_only_by_its_script(root, path):
    rc, err = run("Edit", root, file_path=path, new_string="x")
    assert rc == 2 and "G6" in err


def test_appending_to_the_ledger_by_hand_is_blocked(root):
    rc, err = run("Bash", root, command="echo row >> results/ledger.jsonl")
    assert rc == 2 and "G6" in err


def test_g8_fails_closed_when_its_check_crashes(monkeypatch):
    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    def crash(*_):
        raise RuntimeError("boom")
    monkeypatch.setattr(guard, "check_g8", crash)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})))
    with pytest.raises(SystemExit) as e:
        guard.main()
    assert e.value.code == 2
