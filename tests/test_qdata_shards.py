"""Decisive-Q dataset shards (A-02): same shard twice = same bytes; resume (0-1 then 2) = one go (0-2); the manifest
verifies with scripts/data_manifest.py and catches a changed file; the job entry under the F-03 worker's unit protocol.

Runs on the synthetic archive of tests/qdata_fakes.py (the repo's NBA fixture games on made-up March 2025 nights).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from redzone.lab.gym2 import qdata as Q
from redzone.lab.gym2 import qdata_job as J
from scripts import data_manifest
from tests.qdata_fakes import write_archive

KNOBS = {"slates": 6, "per_shard": 2, "episode_min": 8.0, "points": 4}


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return write_archive(tmp_path_factory.mktemp("qdata") / "archive")


def _plan(root, seed=2):
    return Q.SlatePlan(Q.night_pool(root), seed, slates=KNOBS["slates"], per_shard=KNOBS["per_shard"],
                       episode_min=KNOBS["episode_min"], points=KNOBS["points"])


def _files(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_the_same_shard_twice_gives_identical_bytes(root, tmp_path):
    plan = _plan(root)
    outs = []
    for k in (1, 2):                                       # a fresh loader each time: nothing carried in memory
        rows, summary = Q.build_shard(plan, 1, Q.QLoader(root))
        Q.write_shard(tmp_path / f"o{k}", 1, rows, summary)
        outs.append(_files(tmp_path / f"o{k}"))
    assert outs[0] == outs[1]
    assert any(p.startswith("shards/") for p in outs[0]) and "summaries/qdata_s00001.json" in outs[0]
    rows = Q.all_rows(tmp_path / "o1")
    assert rows and all(r["shard"] == 1 and r["slate"] in plan.shard_slates(1) for r in rows)


def test_resume_in_two_runs_equals_one_run(root, tmp_path):
    plan = _plan(root)
    one, two = tmp_path / "one", tmp_path / "two"
    s1 = J.run_range(plan, 0, 3, one, "qdata-test-v1", root, log=None)
    J.run_range(plan, 0, 2, two, "qdata-test-v1", root, log=None)
    built_again = Q.run_shards(plan, 0, 3, two, Q.QLoader(root))
    assert built_again == [2]                              # 0 and 1 were done: resumed, not rebuilt
    s2 = J.finish(two, plan, "qdata-test-v1")
    assert (one / "qdata.manifest.json").read_bytes() == (two / "qdata.manifest.json").read_bytes()
    assert s1["manifest_sha256"] == s2["manifest_sha256"] and s1["rows"] == s2["rows"] > 0
    assert _files(one) == _files(two)


def test_the_manifest_verifies_with_the_data_manifest_cli_and_catches_a_change(root, tmp_path, capsys):
    plan = _plan(root)
    out = tmp_path / "m"
    J.run_range(plan, 0, 2, out, "qdata-test-v1", root, log=None)
    man = out / "qdata.manifest.json"
    doc = json.loads(man.read_text())
    assert doc["format"] == "redzone-data-manifest/1" and doc["fence"]["train_ok"] is True
    assert doc["totals"]["rows"] == sum(len(Q.qd_read_rows(out / f["path"])) for f in doc["files"])
    assert all(f["fence"] == "none" and f["night"] < Q.QD_CUTOFF for f in doc["files"])
    assert data_manifest.main(["verify", str(man), "--root", str(out)]) == 0
    victim = out / doc["files"][0]["path"]
    victim.write_bytes(victim.read_bytes() + b"\n")
    assert data_manifest.main(["verify", str(man), "--root", str(out)]) == 1
    assert "VERIFY FAILED" in capsys.readouterr().err


def _unit(env_base: dict, start: int, end: int, state: str, nxt: Path) -> int:
    nxt.mkdir(parents=True)
    os.environ.update({**env_base, "RZ_JOB_STEP_START": str(start), "RZ_JOB_STEP_END": str(end), "RZ_JOB_STATE": state,
                       "RZ_JOB_NEXT": str(nxt)})
    try:
        return J.main([])
    finally:
        for k in ("RZ_JOB_STEP_START", "RZ_JOB_STEP_END", "RZ_JOB_STATE", "RZ_JOB_NEXT", "RZ_JOB_CONFIG", "RZ_JOB_WORK"):
            os.environ.pop(k, None)


def test_the_job_entry_follows_the_worker_unit_protocol_and_resumes(root, tmp_path):
    cfg = {"master_seed": 2, "data_root": str(root), **KNOBS, "tag": "qdata-test-v1"}
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "config.json").write_text(json.dumps(cfg))
    base_a = {"RZ_JOB_CONFIG": str(tmp_path / "a/config.json"), "RZ_JOB_WORK": str(tmp_path / "a")}
    base_b = {"RZ_JOB_CONFIG": str(tmp_path / "b/config.json"), "RZ_JOB_WORK": str(tmp_path / "b")}
    assert _unit(base_a, 0, 3, "", tmp_path / "a/n3") == 0
    assert _unit(base_b, 0, 2, "", tmp_path / "b/n2") == 0
    assert _unit(base_b, 2, 3, str(tmp_path / "b/n2"), tmp_path / "b/n3") == 0
    assert (tmp_path / "a/n3/qdata.manifest.json").read_bytes() == (tmp_path / "b/n3/qdata.manifest.json").read_bytes()
    assert (tmp_path / "a/n3/state.json").read_bytes() == (tmp_path / "b/n3/state.json").read_bytes()
    assert _unit(base_b, 1, 3, str(tmp_path / "b/n2"), tmp_path / "b/bad") == 3      # state is at step 2, not 1
    assert _unit(base_b, 2, 3, "", tmp_path / "b/bad2") == 3                         # no state for a later unit


def test_a_plan_change_is_not_resumed_into(root, tmp_path):
    out = tmp_path / "p"
    Q.run_shards(_plan(root, 2), 0, 1, out, Q.QLoader(root))
    assert Q.shard_done(out, 0, _plan(root, 2)) and not Q.shard_done(out, 0, _plan(root, 3))
