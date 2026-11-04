"""Project memory: lessons parse and match, the watchdog hook, the CI check and the context pack."""
from __future__ import annotations

import json
from pathlib import Path

from agent import watchdog
from agent.policy import Obs, Verdict
from redzone import memory
from scripts import check_memory, context_pack

ROOT = Path(__file__).resolve().parent.parent


def _tree(tmp_path: Path, lessons: str) -> Path:
    (tmp_path / "docs/memory").mkdir(parents=True)
    (tmp_path / "docs/memory/lessons.md").write_text(lessons)
    return tmp_path


LESSON = """# Lessons

## Tailscale steals the home routes
- **Symptom:** sticks unreachable.
- **Cause:** route overlap.
- **Fix:** quit Tailscale.
- **Signals:** utun, No route to host, ab
"""


def test_every_real_lesson_has_a_cause_fix_and_signals():
    ls = memory.parse_lessons((ROOT / memory.LESSONS).read_text())
    assert len(ls) >= 8
    for x in ls:
        assert x.cause and x.fix and x.signals, x.title


def test_signals_match_case_insensitive_and_short_signals_are_dropped(tmp_path):
    m = memory.Memory(_tree(tmp_path, LESSON))
    assert m.lessons[0].signals == ["utun", "No route to host"]   # "ab" is too short to match on
    assert "quit Tailscale" in m.hints(["adb: NO ROUTE TO HOST 100.N"])
    assert m.hints(["something else entirely"]) == ""
    assert len(m.hints(["utun"], max_chars=40)) == 40


def test_lessons_reload_when_the_file_changes_and_survive_a_missing_file(tmp_path):
    root = _tree(tmp_path, LESSON)
    m = memory.Memory(root)
    assert m.titles(["utun"]) == ["Tailscale steals the home routes"]
    (root / memory.LESSONS).unlink()
    assert m.lessons == [] and m.hints(["utun"]) == ""


def test_record_is_once_per_error_per_day_and_read_back(tmp_path):
    t = [1000.0]
    m = memory.Memory(_tree(tmp_path, LESSON), clock=lambda: t[0])
    assert m.record("adb utun 12 failed", "reconnect_stick", "connected", why="stick offline")
    assert not m.record("adb utun 99 failed", "reconnect_stick", "connected")   # same error, digits aside
    t[0] += memory.RECORD_EVERY_S + 1
    assert m.record("adb utun 99 failed", "reconnect_stick", "connected")
    rows = memory.read_pending(tmp_path)
    assert len(rows) == 2 and rows[0]["known"] == ["Tailscale steals the home routes"]


def test_worked_only_counts_real_successes():
    assert memory.worked("restart_service", "restarted")
    assert memory.worked("prune_disk", "freed 812 MB")
    for action, result in [("restart_service", "restart failed: x"), ("reconnect_stick", "failed to connect"),
                           ("reconnect_stick", "reconnected recently; waiting"), ("prune_disk", "freed 0 MB"),
                           ("restart_service", "dry run: would restart_service"), ("notify", "noted for the phone")]:
        assert not memory.worked(action, result), (action, result)


def test_watchdog_without_memory_is_unchanged_and_with_memory_names_the_lesson(tmp_path):
    o = Obs(errors={"adb: No route to host via utun3": 4})
    w = watchdog.Watchdog(dry_run=True, use_model=False)
    assert w.memory is None   # tests and imports never touch data/
    plain = w.decide(o)
    w.memory = memory.Memory(_tree(tmp_path, LESSON))
    v = w.decide(o)
    assert v.action == plain.action == "notify"
    assert v.why == plain.why + " (lesson: Tailscale steals the home routes)"


def test_model_gets_the_lessons_only_when_one_matches(tmp_path):
    seen = []

    class Fake:
        def chat(self, msgs):
            seen.append(msgs)
            return '{"action": "notify", "why": "x"}'

    w = watchdog.Watchdog(dry_run=True, use_model=True)
    w.memory = memory.Memory(_tree(tmp_path, LESSON))
    w._model = Fake()
    w.gov.allow = lambda kind: True
    w.decide(Obs(errors={"brand new thing broke": 3}))
    w.decide(Obs(errors={"No route to host on utun": 3}))
    assert [m["role"] for m in seen[0]] == ["system", "user"]
    assert [m["role"] for m in seen[1]] == ["system", "system", "user"]
    assert "quit Tailscale" in seen[1][1]["content"]


def test_watchdog_records_a_pending_lesson_after_a_fix_that_worked(tmp_path, monkeypatch):
    w = watchdog.Watchdog(use_model=False, clock=lambda: 5000.0)
    w.memory = memory.Memory(_tree(tmp_path, LESSON), clock=lambda: 5000.0)
    monkeypatch.setattr(w, "observe", lambda: Obs(errors={"adb utun hung": 3}))
    monkeypatch.setattr(w, "decide", lambda o: Verdict("restart_service", "crash loop"))
    monkeypatch.setattr(w, "act", lambda v, o: "restarted")
    monkeypatch.setattr(watchdog, "_journal", lambda row: None)
    w.tick()
    rows = memory.read_pending(tmp_path)
    assert rows and rows[0]["signature"] == "adb utun hung" and rows[0]["action"] == "restart_service"
    monkeypatch.setattr(w, "act", lambda v, o: "restart failed: boom")
    w.memory = memory.Memory(tmp_path, clock=lambda: 9e9)
    w.tick()
    assert len(memory.read_pending(tmp_path)) == 1


def test_repo_memory_links_resolve():
    assert check_memory.broken_links() == []


def test_broken_links_are_reported(tmp_path):
    (tmp_path / "docs/memory").mkdir(parents=True)
    (tmp_path / "docs/real.md").write_text("x")
    (tmp_path / "CLAUDE.md").write_text("[ok](docs/real.md#top) [web](https://x.y) [a](#here) "
                                        "[gone](docs/nope.md)\n```\n[code](not/checked.md)\n```\n")
    (tmp_path / "docs/memory/a.md").write_text("[up](../real.md) [bad](missing.md)")
    assert check_memory.broken_links(tmp_path) == ["CLAUDE.md: broken link docs/nope.md",
                                                   "docs/memory/a.md: broken link missing.md"]


def test_a_default_or_gate_change_needs_a_decision_line():
    assert check_memory.decisions_missing(["redzone/engine.py"]) == []
    assert check_memory.decisions_missing(["redzone/default_config.yaml"])
    assert check_memory.decisions_missing(["scripts/release_gate.py", check_memory.DECISIONS]) == []


def test_context_pack_has_every_section_offline(tmp_path, monkeypatch):
    text = context_pack.build(offline=True)
    for head in ("## Rules (CLAUDE.md)", "## Handoff", "## Status board", "## Decisions", "## Lessons",
                 "## Open PRs", "## Latest scorecards", "## Kaggle state"):
        assert head in text, head
    assert "_skipped (--offline)_" in text
    assert "| nfl |" in text
    monkeypatch.setattr(context_pack, "ROOT", tmp_path)
    (tmp_path / "data/memory").mkdir(parents=True)
    (tmp_path / "data/memory/pending_lessons.jsonl").write_text(
        json.dumps({"action": "prune_disk", "signature": "disk full", "result": "freed 900 MB"}) + "\nnot json\n")
    assert "prune_disk: disk full" in context_pack.lessons()
    assert "_missing_" in context_pack.build(offline=True)   # an empty tree degrades, never crashes
