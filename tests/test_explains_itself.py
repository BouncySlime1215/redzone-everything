"""RedZone keeps explaining itself after today: these fail if a future change quietly removes a piece.

What "explains itself" means here (docs/memory/runbooks/why.md):
1. every state move in the code names a reason;
2. every state machine is declared with a meaning for each state, an owner that exists, and is in the doc;
3. the running app saves every move (data/transitions.jsonl) and stamps decision rows with its commit;
4. `scripts/why.py` answers "what happened at HH:MM", "the night in numbers" and "why is this setting so".
"""
import ast
import json
import re
from datetime import datetime
from pathlib import Path

import redzone.machines as machines
from scripts import state_machines, why

ROOT = Path(__file__).resolve().parents[1]
STATES = {s for spec in machines.ALL.values() for s in spec.states}


def test_every_state_move_in_the_code_names_a_reason():
    missing = []
    for p in sorted((ROOT / "redzone").rglob("*.py")):
        src = p.read_text()
        if "fsm" not in src and "machines" not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("to", "reset")
                    and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in STATES):
                if not (len(n.args) >= 2 or any(k.arg == "why" for k in n.keywords)):
                    missing.append(f"{p.relative_to(ROOT)}:{n.lineno}")
    assert not missing, "state moves without a why (pass why=...): " + ", ".join(missing)


def test_every_machine_is_declared_with_meanings_and_a_real_owner():
    for name, spec in machines.ALL.items():
        if name.startswith("test_"):
            continue
        assert spec.doc.strip(), name
        empty = [s for s, meaning in spec.states.items() if not meaning.strip()]
        assert not empty, f"{name}: states without a meaning: {empty}"
        assert _owner_file(spec.owner), f"{name}: owner {spec.owner} not found"


def _owner_file(owner: str) -> Path | None:
    """`control.Controller.standby_m` -> redzone/control.py; `scripts.deploy.Deployer.m` -> scripts/deploy.py."""
    parts = owner.split(".")
    for k in range(len(parts), 0, -1):
        rel = "/".join(parts[:k])
        for base in (ROOT, ROOT / "redzone"):
            if (base / f"{rel}.py").exists() or (base / rel / "__init__.py").exists():
                return base / rel
    return None


def test_the_state_machine_doc_is_current():
    assert (ROOT / "docs" / "state-machines.md").read_text() == state_machines.render(), \
        "run: python scripts/state_machines.py"


def test_the_app_saves_every_move_and_stamps_rows_with_its_commit():
    main = (ROOT / "redzone" / "__main__.py").read_text()
    assert "log_transitions(" in main and "_log_transitions()" in main
    engine = (ROOT / "redzone" / "engine.py").read_text()
    assert engine.count('rec["sha"] = fsm.run_commit()') >= 2      # jumps.jsonl and jump_status.jsonl


def test_why_answers_a_time_a_night_and_a_setting(tmp_path, capsys):
    t = datetime(2026, 11, 7, 21, 0).timestamp()
    (tmp_path / "transitions.jsonl").write_text(json.dumps(
        {"ts": t, "machine": "control.standby", "old": "empty", "new": "loading", "why": "planner", "commit": "abc"}) + "\n")
    (tmp_path / "jumps.jsonl").write_text(json.dumps({"ts": t + 1, "from": "a", "to": "b", "ok": True, "sha": "abc"}) + "\n")
    (tmp_path / "jump_status.jsonl").write_text(
        json.dumps({"ts": t, "owner": "buffered", "holds": {"crunch": 2}}) + "\n"
        + json.dumps({"ts": t + 30, "owner": "buffered", "holds": {"crunch": 5}}) + "\n")
    assert why.main(["21:00", "--date", "2026-11-07", "--data", str(tmp_path)]) == 0
    assert "because planner" in capsys.readouterr().out
    assert why.main(["--night", "2026-11-07", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "jumps: 1 ok" in out and "crunch 5" in out and "buffered 2" in out and "abc" in out
    assert why.main(["--night", "2026-11-01", "--data", str(tmp_path / "nothing")]) == 0   # empty night: no crash
    capsys.readouterr()
    assert why.main(["decider.entry", "--root", str(ROOT)]) == 0


def test_the_runbook_and_rules_point_at_the_tools():
    runbook = (ROOT / "docs" / "memory" / "runbooks" / "why.md").read_text()
    for needle in ("scripts/why.py", "--night", "/api/machines", "transitions.jsonl", "citymap.py"):
        assert needle in runbook, needle
    assert re.search(r"scripts/why\.py", (ROOT / "CLAUDE.md").read_text())
