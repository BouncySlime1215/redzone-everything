"""F-03 job worker: atomic writes, checkpoint fallback, kill-and-resume == uninterrupted, self-stop, SIGTERM, refusals.

Every run here is the toy entry (tools/jobs/toy.py) on this checkout (`--repo`), no clone, no network; each takes
about a second.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import job as jobcli
from tools.jobs import worker

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "tools" / "jobs" / "worker.py"


@pytest.fixture(scope="module")
def head() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def write_spec(tmp: Path, spec, name="spec.json") -> Path:
    p = tmp / name
    p.write_text(spec.to_json())
    return p


def run(tmp, spec, work="work", env=None, kill_at=None):
    return jobcli.run_worker(WORKER, write_spec(tmp, spec), tmp / work, env, kill_at_step=kill_at)


# --- atomic writes and checkpoints -------------------------------------------------------------------------------
def test_atomic_write_survives_a_crash_between_temp_write_and_rename(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    worker.atomic_write(str(target), b"old")

    def crash(src, dst):
        raise OSError("power cut between the temp write and the rename")

    monkeypatch.setattr(worker.os, "replace", crash)
    with pytest.raises(OSError):
        worker.atomic_write(str(target), b"new and longer")
    assert target.read_bytes() == b"old"                       # never half-written
    monkeypatch.undo()
    worker.atomic_write(str(target), b"new")
    assert target.read_bytes() == b"new"


def _unit(work: Path, step: int, payload: bytes) -> Path:
    nxt = work / f"next_{step}"
    nxt.mkdir(parents=True)
    (nxt / "state.bin").write_bytes(payload)
    return nxt


def test_a_crash_during_commit_keeps_the_previous_checkpoint(tmp_path, monkeypatch):
    sh = "s" * 64
    worker.commit_unit(str(_unit(tmp_path, 10, b"ten")), str(tmp_path), sh, 10)
    real = os.replace

    def crash_on_dir(src, dst):
        if os.path.basename(dst).startswith("step_"):
            raise OSError("killed before the checkpoint rename")
        return real(src, dst)

    monkeypatch.setattr(worker.os, "replace", crash_on_dir)
    with pytest.raises(OSError):
        worker.commit_unit(str(_unit(tmp_path, 20, b"twenty")), str(tmp_path), sh, 20)
    monkeypatch.undo()
    step, d = worker.latest(str(tmp_path), sh)
    assert step == 10 and Path(d, "state.bin").read_bytes() == b"ten"


def test_a_corrupt_checkpoint_falls_back_to_the_one_before(tmp_path):
    sh = "s" * 64
    worker.commit_unit(str(_unit(tmp_path, 10, b"ten")), str(tmp_path), sh, 10)
    newest = worker.commit_unit(str(_unit(tmp_path, 20, b"twenty")), str(tmp_path), sh, 20)
    assert worker.latest(str(tmp_path), sh)[0] == 20
    Path(newest, "state.bin").write_bytes(b"bit rot")
    assert worker.latest(str(tmp_path), sh)[0] == 10
    assert worker.latest(str(tmp_path), "o" * 64) == (None, None)       # another spec's checkpoints never count


# --- whole runs ---------------------------------------------------------------------------------------------------
def test_kill_and_resume_equals_the_uninterrupted_run(tmp_path):
    rep = jobcli.local_smoke(tmp_path)
    assert rep["uninterrupted_status"] == rep["resumed_status"] == "ok"
    assert rep["killed_result_line"] is False                  # SIGKILL: no result line, nothing to flush
    assert rep["resumed_from"] >= 50
    assert rep["uninterrupted"] == rep["resumed"] and rep["equal"]
    # the killed run's in-flight unit was never committed and its scratch folder is gone after the resume
    assert not [n for n in os.listdir(tmp_path / "resumed") if n.startswith("next_")]
    for name in jobcli.TOY_OUTPUTS:
        assert (tmp_path / "whole" / "out" / name).read_bytes() == (tmp_path / "resumed" / "out" / name).read_bytes()


def test_a_wrong_resume_would_change_the_hash(tmp_path, head):
    a = jobcli.toy_spec(head, steps=(0, 40))
    b = jobcli.toy_spec(head, steps=(0, 40), every=20)         # other unit seeds: other result
    _t, ra = run(tmp_path, a, "a")
    _t, rb = run(tmp_path, b, "b")
    assert ra["status"] == rb["status"] == "ok" and ra["outputs_sha256"] != rb["outputs_sha256"]


def test_self_stop_writes_partial_with_a_valid_checkpoint_then_finishes(tmp_path, head):
    short = jobcli.toy_spec(head, steps=(0, 40), budget=4, margin=2)
    env = {"RZ_TOY_SLEEP_S": "0.05"}
    text, res = run(tmp_path, short, env=env)
    assert res["status"] == "partial", text
    assert 0 < res["step"] < 40
    ck = Path(worker.ckpt_root(str(tmp_path / "work"), short.spec_hash())) / f"step_{res['step']:010d}"
    assert worker.valid_ckpt(str(ck), short.spec_hash()) == res["step"]
    assert res["ckpt_sha256"] == worker.sha_file(str(ck / "CKPT.json"))
    assert not (tmp_path / "work" / "out" / "DONE").exists()
    for _ in range(10):                                        # "run the same cell again" until done
        text, res = run(tmp_path, short, env=env)
        if res["status"] == "ok":
            break
        assert res["status"] == "partial", text
    assert res["status"] == "ok"
    _t, whole = run(tmp_path, jobcli.toy_spec(head, steps=(0, 40)), "whole", env=env)
    assert res["outputs_sha256"] == whole["outputs_sha256"]


def test_another_spec_in_the_same_folder_never_touches_the_checkpoints(tmp_path, head):
    # regression: pruning by name across specs deleted the newer spec's only checkpoint (old spec at higher steps)
    _t, old = run(tmp_path, jobcli.toy_spec(head, steps=(0, 100), every=50))
    assert old["status"] == "ok"
    new = jobcli.toy_spec(head, steps=(0, 30))
    text, res = run(tmp_path, new)
    assert res["status"] == "ok", text
    _t, whole = run(tmp_path, new, "whole")
    assert res["outputs_sha256"] == whole["outputs_sha256"]


def test_sigterm_stops_the_unit_and_reports_partial(tmp_path, head):
    spec = jobcli.toy_spec(head, steps=(0, 100))
    p = jobcli.start_worker(WORKER, write_spec(tmp_path, spec), tmp_path / "work", {"RZ_TOY_SLEEP_S": "0.05"})
    lines = []
    for line in p.stdout:
        lines.append(line)
        if line.startswith("RZ-PROGRESS ") and json.loads(line.split(" ", 1)[1])["step"] >= 10:
            p.send_signal(signal.SIGTERM)
            break
    lines.extend(p.stdout)
    assert p.wait(30) == 4
    results = [ln for ln in lines if ln.startswith("RZ-RESULT ")]
    assert len(results) == 1 and lines[-1].startswith("RZ-RESULT ")
    res = json.loads(results[0].split(" ", 1)[1])
    assert res["status"] == "partial" and 10 <= res["step"] < 100


def test_done_is_reused_and_the_result_chain_links(tmp_path, head):
    spec = jobcli.toy_spec(head, steps=(0, 20))
    first_text, first = run(tmp_path, spec)
    second_text, second = run(tmp_path, spec)
    assert first["status"] == second["status"] == "ok" and second.get("reused") is True
    assert first["prev"] is None
    line1 = [ln for ln in first_text.splitlines() if ln.startswith("RZ-RESULT ")][0]
    assert second["prev"] == worker.sha(line1.encode())
    assert second["outputs_sha256"] == first["outputs_sha256"]


def test_failures_are_reported_not_hidden(tmp_path, head):
    bad_entry = jobcli.toy_spec(head, steps=(0, 20))
    bad_entry.entry = {"module": "tools.jobs.no_such_module", "args": []}
    _t, res = run(tmp_path, bad_entry, "a")
    assert res["status"] == "failed" and "entry exited" in res["error"]
    missing = jobcli.toy_spec(head, steps=(0, 20))
    missing.outputs = [*missing.outputs, "absent.txt"]
    _t, res = run(tmp_path, missing, "b")
    assert res["status"] == "failed" and "absent.txt" in res["error"]
    wrong = jobcli.toy_spec("f" * 40, steps=(0, 20))
    _t, res = run(tmp_path, wrong, "c")
    assert res["status"] == "failed" and "pinned commit" in res["error"]


def test_a_refused_spec_prints_one_failed_result_and_exits_2(tmp_path, head):
    spec = jobcli.toy_spec(head).to_dict()
    spec["commit"] = head[:7]
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    r = subprocess.run([sys.executable, str(WORKER), "--spec", str(p), "--work", str(tmp_path / "w"),
                        "--repo", str(REPO)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    lines = r.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("RZ-RESULT ") and '"status":"failed"' in lines[0]


def test_dry_run_prints_the_plan_and_touches_nothing(tmp_path, head):
    p = write_spec(tmp_path, jobcli.toy_spec(head))
    r = subprocess.run([sys.executable, str(WORKER), "--spec", str(p), "--work", str(tmp_path / "w"), "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("RZ-DRY ") and "RZ-RESULT" not in r.stdout
    plan = json.loads(r.stdout.split(" ", 1)[1])
    assert plan["units"] == 10 and plan["commit"] == head and plan["clone"].startswith("https://")
    assert not (tmp_path / "w").exists()


def test_clone_refuses_a_credentials_url_before_running_git(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "git", lambda *a, **k: pytest.fail("git must not run"))
    for url in ("https://user:pw@github.com/a/b.git", "http://github.com/a/b.git", "git@github.com:a/b.git",
                "https://github.com/a/b.git?x=1"):
        with pytest.raises(worker.JobError, match="refused clone URL"):
            worker.clone(url, "0" * 40, str(tmp_path / "c"))


def test_clone_fetches_exactly_the_pinned_commit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    g = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run([*g[:3], "config", "uploadpack.allowReachableSHA1InWant", "true"], check=True)
    (src / "a.txt").write_text("one")
    subprocess.run([*g, "add", "a.txt"], check=True)
    subprocess.run([*g, "commit", "-qm", "one"], check=True)
    first = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    (src / "a.txt").write_text("two")
    subprocess.run([*g, "commit", "-qam", "two"], check=True)
    dest = worker.clone(f"file://{src}", first, str(tmp_path / "dest"))
    assert worker.git("rev-parse", "HEAD", cwd=dest) == first
    assert Path(dest, "a.txt").read_text() == "one"


def test_nothing_the_show_runs_imports_the_job_runner():
    hits = [p for top in ("redzone", "agent") for p in (REPO / top).rglob("*.py")
            if "tools.jobs" in p.read_text(errors="replace") or "tools/jobs" in p.read_text(errors="replace")]
    assert hits == []
