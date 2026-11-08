#!/usr/bin/env python3
"""PreToolUse guard for every Claude session in this repo (wired in .claude/settings.json).

CLAUDE.md states the hard rules; this file enforces the ones that must hold every time
(docs/research/2026-11-08/agents/rule-files.md: prose is advice, hooks are enforcement).
Exit 2 blocks the call and the stderr text is shown to the agent, so every message names the
rule and the allowed alternative. Stdlib only.

Failure policy (W1-HARNESS section 2.4 hole 4, decisions.md 2026-11-08):
  - The hook cannot start or times out: BLOCK (`onFailure: "block"` in .claude/settings.json).
  - The hook runs but crashes or gets unreadable input: ALLOW, and append one line to
    data/guard_errors.log (tool name and error only, never the input). CI checks the same rules
    again, and a guard bug must not freeze every session.

Rules (ids match docs/coordination.md):
  G1 secrets     no reading or printing token / password / key files (Read, Grep, Glob, shell tools)
  G2 branches    no push or merge to main / codex/resume-redzone, no --force; PRs target integration/next
  G3 hardware    no adb, stick or TV scripts, and no computer-use tools, unless Nick created
                 data/.agent_hw_ok (the TV may be in use)
  G4 heavy jobs  no training or calibration scripts on the 8 GB Mac (run them on Kaggle or in CI)
  G5 deploy      deploy.py only with --dry-run or status, unless data/.agent_deploy_ok exists
  G6 protected   no edits to the sealed grader, gates, eval locks, CI, hooks (docs/coordination.md "Protected")
  G7 peacock     never add Peacock as a provider
  G8 locked      no Read, Grep or Glob of locked test data or results
Shell rules cover Bash, Monitor and the terminal panel (mcp__terminal__run_in_terminal).
Escape hatches are files only Nick creates; an agent must not create them (G6 protects them).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])

SECRET_PAT = re.compile(
    r"(\.env\b|token|secret|password|passwd|credential|oauth|\.pem\b|\.key\b|id_rsa|api[_-]?key|"
    r"smartthings_oauth|tv_token)", re.I)
SECRET_OK = re.compile(r"(tokens?_(budget|count|used)|tokenize|max_tokens|--tokens|test_.*token|"
                       r"tokens\.py|token_budget|keyword|\.keys\(\))", re.I)

PROTECTED = [
    ".claude/**", ".github/**", "tests/conftest.py", "conftest.py",
    "scripts/release_gate.py", "scripts/leak_check.py", "scripts/northstar.py", "scripts/autofix_guard.py",
    "scripts/ci_*.py", "scripts/eval*.py", "scripts/fetch_eval_data.py", "scripts/build_eval_data.py",
    "docs/gate_baseline.json", "docs/grader_freeze.json", "eval-data*.lock.json", "**/eval-data*.lock.json",
    "data/.agent_*",
]

# The locked-results design's holding places (docs/agents/locked-results/2026-11-08-access.md).
LOCKED = re.compile(r"(^|/)(results/locked|data/locked|\.rz_locked|redzone-vault)(/|$)")

SHELL_TOOLS = {"Bash", "Monitor", "mcp__terminal__run_in_terminal"}
COMPUTER_USE = "mcp__computer-use__"

# adb and friends as a program name: bare, quoted (bash -c 'adb ...'), by path (/opt/x/adb) or assigned
# (ADB=adb). The lookahead keeps file names out: redzone/adb.py, tools/adb/, adb-notes.
HARDWARE = re.compile(
    r"(^|[\s;&|(`'\"/=])(adb|scrcpy|cec-ctl)(?![\w./-])|"
    r"\bpython[\d.]*\b[^\n]*\s-c\s[^\n]*\badb\b|"
    r"scripts/(stick_|tv_|controls_drill|fault_drill)|"
    r"redzone\.tvsetup|launchctl\s+(kickstart|bootstrap|bootout|load|unload)\b.*redzone")
# Text that only names a word: a commit message or a search pattern. Nothing with $ or a backtick.
_QUOTED = r"(\"[^\"`$]*\"|'[^']*')"
INERT_TEXT = re.compile(r"(\bgit\s+commit\b(?:\s+-\S+)*?\s+-\w*m\s*|\b(?:grep|rg)\b(?:\s+-\S+)*\s+)" + _QUOTED)
HEAVY = re.compile(r"scripts/(train_|build_training|calibrate_)\w*\.py")
DEPLOY = re.compile(r"scripts/deploy\.py\b")
DEPLOY_SAFE = re.compile(r"scripts/deploy\.py\s+(status|--help|-h)\b|--dry-run")
_GIT = r"git(?:\s+-[cC]\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+"     # git -C dir push, git -c a=b push
PUSH_PROTECTED = re.compile(_GIT + r"push\b[^\n;&|]*\b(main|master|codex/resume-redzone|integration/release)\b")
FORCE = re.compile(_GIT + r"push\b[^\n;&|]*(--force\b|-f\b|--force-with-lease)")
MERGE_MAIN = re.compile(r"git\s+(checkout|switch)\s+main\b[^\n]*&&[^\n]*git\s+merge|git\s+merge\b[^\n;&|]*\b(into\s+)?main\b")
PR_CREATE = re.compile(r"gh\s+pr\s+create\b")
PR_BASE_OK = re.compile(r"--base\s+integration/next\b|-B\s+integration/next\b")
PEACOCK = re.compile(r"peacock", re.I)


def block(rule: str, why: str, instead: str) -> None:
    sys.stderr.write(f"Blocked by {rule}: {why}\nInstead: {instead}\n(docs/coordination.md, .claude/hooks/guard.py)\n")
    sys.exit(2)


def flag(name: str) -> bool:
    return (ROOT / "data" / name).exists()


def rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except (ValueError, OSError):
        return path


def is_protected(path: str) -> bool:
    r = rel(path)
    if any(fnmatch.fnmatch(r, p) for p in PROTECTED):
        return True
    # A path outside this checkout (another worktree of the same repo) is judged by its tail: scratch/wt/.claude/x.
    parts = Path(r).parts
    return Path(r).is_absolute() and any(
        fnmatch.fnmatch("/".join(parts[i:]), p) for i in range(1, len(parts)) for p in PROTECTED)


def check_bash(cmd: str) -> None:
    if SECRET_PAT.search(cmd) and re.search(r"\b(cat|less|more|head|tail|grep|rg|sed|awk|printenv|echo|strings|"
                                            r"xxd|base64|security\s+find)\b", cmd) and not SECRET_OK.search(cmd):
        block("G1 secrets", "this command may read or print a secret.", "ask Nick; never read token, key or oauth files.")
    if re.search(r"\bprintenv\b|\benv\s*$|\benv\s*\|", cmd):
        block("G1 secrets", "dumping the environment can print secrets.", "read one named, non-secret variable.")
    if PUSH_PROTECTED.search(cmd):
        block("G2 branches", "pushes to main, codex/resume-redzone or integration/release are Nick's.",
              "push your own branch and open a PR into integration/next.")
    if FORCE.search(cmd):
        block("G2 branches", "force-push rewrites shared history.", "push a new commit instead.")
    if MERGE_MAIN.search(cmd):
        block("G2 branches", "never merge to main.", "open a PR into integration/next; the integration train merges.")
    if PR_CREATE.search(cmd) and not PR_BASE_OK.search(cmd):
        block("G2 branches", "every PR targets integration/next.", "add `--base integration/next`.")
    if HARDWARE.search(INERT_TEXT.sub(lambda m: m.group(1) + '""', cmd)) and not flag(".agent_hw_ok"):
        block("G3 hardware", "the sticks and TV may be in use (no disruptive tests while Nick watches).",
              "use tests/fake_adb.py or the simulator; ask Nick to create data/.agent_hw_ok for a live test.")
    if HEAVY.search(cmd) and not os.environ.get("REDZONE_CLOUD"):
        block("G4 heavy jobs", "training and calibration don't run on the 8 GB Mac that runs the live app.",
              "run it on Kaggle or in CI (docs/memory/runbooks/kaggle-run.md).")
    if DEPLOY.search(cmd) and not DEPLOY_SAFE.search(cmd) and not flag(".agent_deploy_ok"):
        block("G5 deploy", "a deploy restarts the live app.",
              "run `scripts/deploy.py --dry-run`, or ask Nick to create data/.agent_deploy_ok.")
    m = re.search(r"(>|>>|tee\s+(-a\s+)?)\s*([^\s;&|]+)", cmd)
    if m and is_protected(m.group(3)):
        block("G6 protected", f"{m.group(3)} is a protected path (sealed grader, gates, CI, hooks).",
              "open a separate grader PR for Nick; touch nothing else in it.")
    if re.search(r"\b(sed\s+-i|perl\s+-pi|mv|cp|rm)\b", cmd):
        for tok in re.findall(r"[\w./*-]+", cmd):
            if "/" in tok and is_protected(tok):
                block("G6 protected", f"{tok} is a protected path.", "open a separate grader PR for Nick.")
    if re.search(r"touch\s+[^\n]*data/\.agent_", cmd):
        block("G6 protected", "only Nick creates the data/.agent_* permission files.", "ask Nick.")


def check_edit(path: str, text: str) -> None:
    if is_protected(path):
        block("G6 protected", f"{rel(path)} is a protected path (sealed grader, gates, eval locks, CI, hooks).",
              "write BLOCKED with the reason, or open a separate grader PR for Nick that touches nothing else.")
    if SECRET_PAT.search(Path(path).name) and not SECRET_OK.search(Path(path).name) and not path.endswith(".py"):
        block("G1 secrets", "this looks like a secrets file.", "Nick adds secrets himself.")
    r = rel(path)
    if PEACOCK.search(text or "") and (r.endswith((".yaml", ".yml", ".json")) or r.startswith("redzone/")) \
            and not re.search(r"(never|ban|block|forbid|not\s+peacock|exclude)", text, re.I):
        block("G7 peacock", "Peacock is never used.", "pick another provider.")


def check_locked(path: str) -> None:
    if LOCKED.search(rel(path)):
        block("G8 locked", f"{rel(path)} is locked test data or results (docs/agents/locked-results/).",
              "use the PASS/FAIL summary CI reports; nobody in a Claude session reads locked data.")


def check_read(path: str) -> None:
    check_locked(path)
    name = Path(path).name
    if SECRET_PAT.search(name) and not SECRET_OK.search(name) and not name.endswith((".py", ".md")):
        block("G1 secrets", f"{name} may hold a secret.", "don't read it; ask Nick if a value is needed.")


def check_search(*targets: str) -> None:
    """Grep and Glob: a path or file pattern aimed at a secret. Grep's content pattern is not a path."""
    for t in filter(None, targets):
        check_locked(t)
        r = rel(t)
        if SECRET_PAT.search(r) and not SECRET_OK.search(r) and not r.endswith((".py", ".md")):
            block("G1 secrets", f"{r} may match a secret file.", "search code and docs only; ask Nick if a value is needed.")


def log_error(tool: str, exc: Exception) -> None:
    """Fail open, but leave a trace. Never the tool input: it can hold the very thing G1 protects."""
    sys.stderr.write(f"guard.py error (allowed): {type(exc).__name__}\n")
    try:
        log = ROOT / "data" / "guard_errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {tool or '?'} {type(exc).__name__}: {exc}\n")
    except OSError:
        pass


def route(tool: str, inp: dict) -> None:
    if tool in SHELL_TOOLS:
        check_bash(str(inp.get("command", "")))
    elif tool.startswith(COMPUTER_USE):
        if not flag(".agent_hw_ok"):
            block("G3 hardware", "computer-use drives the Mac that runs the live TV app, and no command text can be checked.",
                  "use Bash; ask Nick to create data/.agent_hw_ok if the desktop is really needed.")
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        text = str(inp.get("content") or inp.get("new_string") or "")
        for e in inp.get("edits") or []:
            text += "\n" + str(e.get("new_string", ""))
        check_edit(str(inp.get("file_path") or inp.get("notebook_path") or ""), text)
    elif tool == "Read":
        check_read(str(inp.get("file_path", "")))
    elif tool == "Grep":
        check_search(str(inp.get("path") or ""), str(inp.get("glob") or ""))
    elif tool == "Glob":
        check_search(str(inp.get("path") or ""), str(inp.get("pattern") or ""))


def main() -> None:
    tool = ""
    try:
        ev = json.load(sys.stdin)
        tool = str(ev.get("tool_name", ""))
        route(tool, ev.get("tool_input") or {})
    except SystemExit:
        raise
    except Exception as exc:  # the failure policy in the module docstring
        log_error(tool, exc)


if __name__ == "__main__":
    main()
