import json
from datetime import datetime

from scripts import why


def put(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_timeline_merges_sources_in_order_with_reasons(tmp_path, capsys):
    t = datetime(2026, 11, 7, 20, 42).timestamp()
    put(tmp_path / "transitions.jsonl", [
        {"ts": t - 10, "machine": "control.standby", "old": "loading", "new": "ready", "why": "tuned", "commit": "abc"},
        {"ts": t - 9999, "machine": "x", "old": "a", "new": "b", "why": "too early"}])
    put(tmp_path / "jumps.jsonl", [{"ts": t + 5, "from": "g1", "to": "g2", "ok": False, "fail": "other",
                                    "fail_detail": "standby not ready", "veto": "landing", "sha": "abc"}])
    put(tmp_path / "jump_status.jsonl", [{"ts": t, "on_screen": "g1", "owner": "buffered", "owner_why": "jump on",
                                          "standby": "g2", "standby_ready": True, "pending": None,
                                          "holds": {"bj_keep_standby": 3}}])
    assert why.main(["20:42", "--date", "2026-11-07", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3
    assert "control.standby: loading -> ready  because tuned @abc" in out[0]
    assert "owner buffered (jump on)" in out[1] and "bj_keep_standby=3" in out[1]
    assert "FAILED" in out[2] and "veto landing" in out[2]


def test_machine_history(tmp_path, capsys):
    put(tmp_path / "transitions.jsonl", [{"ts": 1.0, "machine": "decider.owner", "old": "idle", "new": "jump", "why": "jump mode"}])
    why.main(["--machine", "decider.owner", "--data", str(tmp_path)])
    assert "idle -> jump  because jump mode" in capsys.readouterr().out


def test_mentions_find_decisions_and_lessons_newest_first(tmp_path, capsys):
    mem = tmp_path / "docs" / "memory"
    mem.mkdir(parents=True)
    (mem / "decisions.md").write_text("# D\n\n## AI\n\n- 2026-11-05: older cuts.py rule\n  more text\n"
                                      "- 2026-11-07: buffered_jump.keep_standby_s 120 because x\n"
                                      "- 2026-11-06: unrelated\n")
    (mem / "lessons.md").write_text("- 2026-11-06: cuts landed late. Signals: cut late\n")
    why.main(["redzone/decider/cuts.py", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert out.index("2026-11-06") < out.index("2026-11-05")
    assert "unrelated" not in out
    why.main(["buffered_jump.keep_standby_s", "--root", str(tmp_path)])
    assert "120 because x" in capsys.readouterr().out
