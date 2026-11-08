"""The PreToolUse guard (.claude/hooks/guard.py) blocks what CLAUDE.md says must never happen, and lets
ordinary work through. docs/coordination.md G1-G7."""
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


def test_normal_edits_pass(root):
    assert run("Edit", root, file_path=str(root / "redzone/fsm.py"), new_string="x", old_string="y")[0] == 0


def test_peacock_provider_is_blocked_but_a_ban_line_is_not(root):
    code, err = run("Write", root, file_path=str(root / "redzone/default_config.yaml"), content="providers: [peacock]")
    assert code == 2 and "G7" in err
    assert run("Write", root, file_path=str(root / "redzone/x.py"), content="# never use Peacock")[0] == 0


def test_secret_files_are_not_read(root):
    assert run("Read", root, file_path=str(root / "data/smartthings_oauth.json"))[0] == 2
    assert run("Read", root, file_path=str(root / "redzone/tokens.py"))[0] == 0


def test_bad_input_fails_open():
    r = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
