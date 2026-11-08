#!/usr/bin/env python3
"""PreToolUse guard for every Claude session in this repo (wired in .claude/settings.json).

CLAUDE.md states the hard rules; this file enforces the ones that must hold every time
(docs/research/2026-11-08/agents/rule-files.md: prose is advice, hooks are enforcement).
Exit 2 blocks the call and the stderr text is shown to the agent, so every message names the
rule and the allowed alternative. Stdlib only. A crash here allows the call (fail open), and
CI checks the same rules again, so a broken hook never stops work silently for long.

Rules (ids match docs/coordination.md):
  G1 secrets     no reading or printing token / password / key files
  G2 branches    no push or merge to main / codex/resume-redzone, no --force; PRs target integration/next
  G3 hardware    no adb, stick or TV scripts unless Nick created data/.agent_hw_ok (the TV may be in use)
  G4 heavy jobs  no training or calibration scripts on the 8 GB Mac (run them on Kaggle or in CI)
  G5 deploy      deploy.py only with --dry-run or status, unless data/.agent_deploy_ok exists
  G6 protected   no edits to the sealed grader, gates, eval locks, CI, hooks (docs/coordination.md "Protected")
  G7 peacock     never add Peacock as a provider
Escape hatches are files only Nick creates; an agent must not create them (G6 protects them).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
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

HARDWARE = re.compile(
    r"(^|[\s;&|(`])(adb|scrcpy|cec-ctl)\b|scripts/(stick_|tv_|controls_drill|fault_drill)|"
    r"redzone\.tvsetup|launchctl\s+(kickstart|bootstrap|bootout|load|unload)\b.*redzone")
HEAVY = re.compile(r"scripts/(train_|build_training|calibrate_)\w*\.py")
DEPLOY = re.compile(r"scripts/deploy\.py\b")
DEPLOY_SAFE = re.compile(r"scripts/deploy\.py\s+(status|--help|-h)\b|--dry-run")
PUSH_PROTECTED = re.compile(r"git\s+push\b[^\n;&|]*\b(main|master|codex/resume-redzone|integration/release)\b")
FORCE = re.compile(r"git\s+push\b[^\n;&|]*(--force\b|-f\b|--force-with-lease)")
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
    return any(fnmatch.fnmatch(r, p) for p in PROTECTED)


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
    if HARDWARE.search(cmd) and not flag(".agent_hw_ok"):
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


def check_read(path: str) -> None:
    name = Path(path).name
    if SECRET_PAT.search(name) and not SECRET_OK.search(name) and not name.endswith((".py", ".md")):
        block("G1 secrets", f"{name} may hold a secret.", "don't read it; ask Nick if a value is needed.")


def main() -> None:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    tool = ev.get("tool_name", "")
    inp = ev.get("tool_input") or {}
    try:
        if tool == "Bash":
            check_bash(str(inp.get("command", "")))
        elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            text = str(inp.get("content") or inp.get("new_string") or "")
            for e in inp.get("edits") or []:
                text += "\n" + str(e.get("new_string", ""))
            check_edit(str(inp.get("file_path") or inp.get("notebook_path") or ""), text)
        elif tool == "Read":
            check_read(str(inp.get("file_path", "")))
    except SystemExit:
        raise
    except Exception as exc:  # fail open, say so
        sys.stderr.write(f"guard.py error (allowed): {exc}\n")


if __name__ == "__main__":
    main()
