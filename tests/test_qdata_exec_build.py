"""Execution accounting on real rollouts (A-02b), on the synthetic archive of tests/qdata_fakes.py (the repo's NBA
fixture games on made-up March 2025 nights; a toy, not NBA's real mix):

  - "a02" (the default) writes no new field and keeps version qdata-v1 and the plan digest;
  - "account" runs the SAME rollouts: stripped of the accounting fields its rows equal the A-02 rows;
  - every forced jump not made carries a reason and a class, every one made the second it was made;
  - "fixed" lists no landing the step refuses, and its shards keep determinism and resume equivalence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from redzone.lab.gym2 import qdata as Q
from redzone.lab.gym2 import qdata_job as J
from tests.qdata_fakes import write_archive

KNOBS = {"slates": 6, "per_shard": 2, "episode_min": 8.0, "points": 4}
OPT_KEYS = ("made", "made_s", "exec", "not_made_reason", "not_made_class")
ROW_KEYS = ("execution", "n_exec", "no_choice", "exec_best", "exec_gap", "exec_decisive", "exec_tie")


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return write_archive(tmp_path_factory.mktemp("qexec") / "archive")


@pytest.fixture(scope="module")
def built(root):
    pool, loader, out = Q.night_pool(root), Q.QLoader(root), {}
    for mode in Q.QD_EXECUTION:
        rows = []
        for i in range(16):
            r, _ = Q.build_slate(Q.draw_slate(pool, 21, i), loader, episode_min=8.0, points=4, master_seed=21,
                                 execution=mode)
            rows.extend(r)
        out[mode] = rows
    return out


def _plan(root, execution="a02"):
    return Q.SlatePlan(Q.night_pool(root), 2, slates=KNOBS["slates"], per_shard=KNOBS["per_shard"],
                       episode_min=KNOBS["episode_min"], points=KNOBS["points"], execution=execution)


def _strip(r: dict) -> dict:
    r = {k: v for k, v in r.items() if k not in ROW_KEYS}
    r["version"] = Q.QD_VERSION
    r["options"] = [{k: v for k, v in o.items() if k not in OPT_KEYS} for o in r["options"]]
    return r


def test_the_default_writes_the_a02_rows(built, root):
    rows = built["a02"]
    assert rows and all(r["version"] == "qdata-v1" and not any(k in r for k in ROW_KEYS) for r in rows)
    assert all(not any(k in o for k in OPT_KEYS) for r in rows for o in r["options"])
    assert _plan(root).knobs()["version"] == "qdata-v1" and "execution" not in _plan(root).knobs()
    assert _plan(root, "fixed").digest() != _plan(root).digest()
    with pytest.raises(ValueError):
        Q.build_slate(Q.draw_slate(Q.night_pool(root), 21, 0), Q.QLoader(root), execution="v9")


def test_accounting_changes_no_rollout(built):
    acct = built["account"]
    assert [Q.qd_canon(_strip(r)) for r in acct] == [Q.qd_canon(r) for r in built["a02"]]
    assert all(r["version"] == "qdata-v1-acct" and r["execution"] == "account" for r in acct)


def test_every_jump_not_made_says_why_and_every_one_made_says_when(built):
    for mode in ("account", "fixed"):
        jumps = [(r, o) for r in built[mode] for o in r["options"] if o["k"] != "stay"]
        assert jumps
        for r, o in jumps:
            assert o["exec"] == o["made"] == o["done"]
            if o["made"]:
                assert o["not_made_reason"] is None and 0 <= o["made_s"] <= o["tau"] + 2
            else:
                assert o["made_s"] is None and o["not_made_reason"] and o["not_made_class"] in ("constraint", "artefact")
        for r in built[mode]:
            f = Q.exec_flags(r["options"])
            assert {k: r[k] for k in f} == f
            assert r["exec_best"] is None or r["options"][r["exec_best"]]["exec"]
            assert all(o["exec"] for o in r["options"] if o["k"] == "stay")


def test_fixed_lists_no_landing_the_step_refuses(built):
    reasons = {o["not_made_reason"] for r in built["fixed"] for o in r["options"] if o["k"] != "stay"}
    assert "core_landing_long_wait" not in reasons and "bank_swap" not in reasons
    assert all(o["route"] != "skip" for r in built["fixed"] for o in r["options"])
    assert all(r["version"] == "qdata-v2" for r in built["fixed"])


def _files(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_fixed_shards_are_deterministic_and_resume_to_the_same_bytes(root, tmp_path):
    plan = _plan(root, "fixed")
    outs = []
    for k in (1, 2):
        rows, summary = Q.build_shard(plan, 1, Q.QLoader(root))
        Q.write_shard(tmp_path / f"o{k}", 1, rows, summary)
        outs.append(_files(tmp_path / f"o{k}"))
    assert outs[0] == outs[1] and "exec_ties" in json.loads(outs[0]["summaries/qdata_s00001.json"])
    one, two = tmp_path / "one", tmp_path / "two"
    J.run_range(plan, 0, 3, one, "qdata-test-v2", root, log=None)
    J.run_range(plan, 0, 2, two, "qdata-test-v2", root, log=None)
    assert Q.run_shards(plan, 0, 3, two, Q.QLoader(root)) == [2]
    J.finish(two, plan, "qdata-test-v2")
    assert _files(one) == _files(two)
    assert "exec_ties" in json.loads((one / "qdata_summary.json").read_text())
