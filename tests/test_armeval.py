"""Evaluation harness (task F-04): arms, determinism, the verdict rules, the recount from raw logs, the golden files.

Every test builds its own night from the two NBA games already in tests/fixtures (the second time-shifted to tip
5 minutes after the first, its play wallclocks moved like park_replay.shifted_nfl moves a football game), so nothing
here needs the pinned archive. The goldens in tests/golden/armeval/ pin (a) the verdict logic on stored cards and
(b) every arm's decisions on that fixture slate. If a change to the show is MEANT to change decisions, regenerate (b):
    PYTHONHASHSEED=0 python tests/test_armeval.py --regen
Why: WORKPLAN v2 F-04 done-when: identical JSON twice, arms end to end, registry metadata, recount from raw logs.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_DIR = TESTS_DIR.parent
sys.path.insert(0, REPO_DIR.as_posix())

from redzone.lab import fence  # noqa: E402
from redzone.lab.armeval import arms as A  # noqa: E402
from redzone.lab.armeval import episode as E  # noqa: E402
from redzone.lab.armeval import recompute as R  # noqa: E402
from redzone.lab.armeval import verdict as V  # noqa: E402

GOLDEN = TESTS_DIR / "golden" / "armeval"
FIXTURE_GAMES = ("401909860", "401909906")
FIXTURE_MINUTES = 60
LOCAL_ARMS = ("planner", "director_v1", "leverage_only", "random_legal", "noop_rate")
META = {"git_sha": "0" * 40, "dirty": False, "code_hash": "1" * 64, "config_hash": "2" * 64,
        "data_manifest_hash": "3" * 64, "prereg_hash": "4" * 64, "seed": 0, "arm": "director_v1",
        "baseline": "planner", "fence": {"nights": {"20261101": "none"}, "tuning_nights": [], "locked_refused": True}}


def fixture_night(tmp: Path) -> E.Loaded:
    """Two real NBA games (tests/fixtures) on one made-up night: the second tips 300 s after the first."""
    from redzone.lab.backtest import Replay
    from redzone.models import League
    from redzone.sources import espn
    pr = E.gym.replay_module()
    import park_ceiling as pc
    fs = [TESTS_DIR / "fixtures" / f"espn_nba_summary_{g}.json" for g in FIXTURE_GAMES]
    r0, r1 = (Replay.load(League.NBA, f) for f in fs)
    dt = int(r0.wall[0] + 300 - r1.wall[0])
    data = json.loads(fs[1].read_text())
    for p in data.get("plays") or []:
        t = espn._ts(p.get("wallclock"))
        if t is not None:
            p["wallclock"] = datetime.fromtimestamp(t + dt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    moved = tmp / fs[1].name
    moved.write_text(json.dumps(data))
    rs = [pr.repair(Replay.load(League.NBA, f)) for f in (fs[0], moved)]
    slate = pc.slate([pc.truth_of(r) for r in rs], 2)
    by = {r.gid: r for r in rs}
    return E.Loaded("20261101", "20261101", 2, -1, [by[g["gid"]] for g in slate], slate,
                    [(f"nba/{f.name}", f) for f in fs])


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    return fixture_night(tmp_path_factory.mktemp("armeval"))


@pytest.fixture(scope="module")
def runs(night):
    return {name: E.run_night(A.make_arm(name), night, minutes=FIXTURE_MINUTES) for name in LOCAL_ARMS}


def test_every_local_arm_runs_and_the_no_op_decides_exactly_as_the_planner(runs):
    digests = {k: card["decisions_sha256"] for k, (card, _raw) in runs.items()}
    assert digests["noop_rate"] == digests["planner"]
    assert len({digests[k] for k in ("planner", "director_v1", "random_legal")}) == 3, "the arms must change decisions"
    for card, raw in runs.values():
        assert card["pace"]["jumps"] >= 10 and len(raw["cuts"]) == card["pace"]["flips"]


def test_the_same_arm_twice_gives_byte_identical_verdicts(night, runs):
    again = E.run_night(A.make_arm("random_legal"), night, minutes=FIXTURE_MINUTES)
    assert V.canon_json(again[1]) == V.canon_json(runs["random_legal"][1])
    v1 = V.build_verdict(A.make_arm("random_legal").describe(), [runs["random_legal"][0]], [runs["planner"][0]], META)
    v2 = V.build_verdict(A.make_arm("random_legal").describe(), [again[0]], [runs["planner"][0]], META)
    assert V.canon_json(v1) == V.canon_json(v2)


def test_every_number_is_recounted_from_the_raw_log(night, runs):
    games = {night.spec: night.rs}
    files = {night.spec: {name: p for name, p in night.files}}
    for name, (card, raw) in runs.items():
        v = {"per_night": [card]}
        assert R.recheck(v, {night.spec: raw}, games, files) == [], name


def test_the_recount_catches_a_changed_log(night, runs):
    card, raw = runs["planner"]
    bad = copy.deepcopy(raw)
    bad["jumps"] = bad["jumps"][:-1]
    probs = R.recheck({"per_night": [card]}, {night.spec: bad}, {night.spec: night.rs})
    assert any("pace.jumps" in p for p in probs)
    worse = copy.deepcopy(card)
    worse["rule1"]["joins_mid_play"] += 1
    assert any("rule1.joins_mid_play" in p for p in R.recheck({"per_night": [worse]}, {night.spec: raw}, {night.spec: night.rs}))


def test_a_rule1_rise_fails_and_no_bars_is_exploratory(runs):
    base = runs["planner"][0]
    arm = copy.deepcopy(base)
    v = V.build_verdict({"name": "x"}, [arm], [base], META)
    assert v["verdict"] == "INCONCLUSIVE" and v["fails"] == []
    arm["rule1"]["leaves_mid_play"] += 1
    v = V.build_verdict({"name": "x"}, [arm], [base], META, prereg={"bars": {}})
    assert v["verdict"] == "FAIL" and v["rule1_rise"][0]["baseline_then_arm"]["leaves_mid_play"] == [0, 1]


def test_bars_pass_only_on_a_clean_paired_run_outside_the_tuning_window(runs):
    base = runs["planner"][0]
    arm = copy.deepcopy(base)
    arm["value"]["live_points"] += 3.0
    bars = {"bars": {"value.live_points": {"delta_at_least": 2, "ci90_low_above": 0}}}
    assert V.build_verdict({"name": "x"}, [arm], [base], META, prereg=bars)["verdict"] == "PASS"
    assert V.build_verdict({"name": "x"}, [arm], [base], META, prereg={"bars": {"value.live_points": {"delta_at_least": 5}}})["verdict"] == "FAIL"
    tuned = {**META, "fence": {**META["fence"], "tuning_nights": ["20261021"]}}
    assert V.build_verdict({"name": "x"}, [arm], [base], tuned, prereg=bars)["verdict"] == "INCONCLUSIVE"
    assert V.build_verdict({"name": "x"}, [arm], [base], {**META, "dirty": True}, prereg=bars)["verdict"] == "INCONCLUSIVE"
    assert V.build_verdict({"name": "x"}, [arm], None, META, prereg=bars)["verdict"] == "FAIL"     # bar not measurable


def test_the_registry_record_validates_against_the_f02_registry(runs):
    from redzone.models_rt import registry
    v = V.build_verdict(A.make_arm("director_v1").describe(), [runs["director_v1"][0]], [runs["planner"][0]], META,
                        raw_manifest={"director_v1/20261101.json": "5" * 64})
    rec = v["registry_record"]
    assert registry.validate_record(rec) == rec
    assert rec["artifact_sha256"] == V.sha256_text(V.canon_json(v["raw_logs"]))
    assert rec["metrics"]["rule1.joins_mid_play"] == 0 and rec["verdict"] == v["verdict"]


def _script():
    spec = importlib.util.spec_from_file_location("arm_verdict_cli", REPO_DIR / "scripts" / "arm_verdict.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_locked_nights_are_refused_and_tuning_nights_named():
    cli = _script()
    with pytest.raises(SystemExit, match="REFUSED"):
        cli.fence_block(["20261021", "20251215"], [])
    fb = cli.fence_block(["20261021"], ["20250905"])
    assert fb["tuning_nights"] == ["20261021"] and fb["nights"]["20250905"] == "none"


def test_heavy_arms_need_a_hook_and_a_fenced_model(night, runs, tmp_path):
    arm = A.make_arm("stage6_rerank")
    with pytest.raises(A.HeavyArmUnavailable):
        arm.check(["20261101"])
    m = tmp_path / "model.json"
    m.write_text(json.dumps({"name": "no cutoff"}))
    with pytest.raises(fence.FenceError, match="REFUSED"):
        A.make_arm("stage6_rerank", hook="x:y", model=str(m)).check(["20261101"])
    m.write_text(json.dumps({"training_cutoff": "2025-09-30"}))
    arm = A.make_arm("stage6_rerank", hook="redzone.lab.armeval.arms:planner_guess_hook", model=str(m))
    got = arm.check(["20261101"])
    assert got["model_fence"] == "clean" and got["training_cutoff"] == "20250930"
    card, _raw = E.run_night(arm, night, minutes=FIXTURE_MINUTES)
    assert card["decisions_sha256"] == runs["planner"][0]["decisions_sha256"]


def test_the_show_never_imports_the_harness():
    hits = [p for p in (REPO_DIR / "redzone").rglob("*.py")
            if "armeval" in p.read_text() and "lab/armeval" not in p.as_posix()]
    assert hits == []


def test_golden_verdict_from_stored_cards():
    cards = json.loads((GOLDEN / "cards.json").read_text())
    v = V.build_verdict(cards["arm_desc"], cards["arm"], cards["baseline"], cards["meta"], prereg=cards["prereg"],
                        raw_manifest=cards["manifest"])
    assert V.canon_json(v) == (GOLDEN / "verdict.json").read_text()


def test_golden_fixture_decisions(runs):
    want = json.loads((GOLDEN / "fixture_runs.json").read_text())
    got = {k: {"decisions_sha256": c["decisions_sha256"], "card": c} for k, (c, _r) in runs.items()}
    assert json.loads(V.canon_json(got)) == want, ("the show's decisions on the fixture slate changed; if intended, "
                                                    "regenerate: PYTHONHASHSEED=0 python tests/test_armeval.py --regen")


def regen() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        n = fixture_night(Path(tmp))
        rr = {k: E.run_night(A.make_arm(k), n, minutes=FIXTURE_MINUTES) for k in LOCAL_ARMS}
    GOLDEN.mkdir(parents=True, exist_ok=True)
    (GOLDEN / "fixture_runs.json").write_text(V.canon_json({k: {"decisions_sha256": c["decisions_sha256"], "card": c}
                                                           for k, (c, _r) in rr.items()}))
    arm_desc = A.make_arm("director_v1").describe()
    cards = {"arm_desc": arm_desc, "arm": [rr["director_v1"][0]], "baseline": [rr["planner"][0]], "meta": META,
             "prereg": {"bars": {"value.live_points": {"delta_at_least": 0.0}}},
             "manifest": {"director_v1/20261101.json": "5" * 64, "planner/20261101.json": "6" * 64}}
    (GOLDEN / "cards.json").write_text(V.canon_json(cards))
    v = V.build_verdict(arm_desc, cards["arm"], cards["baseline"], META, prereg=cards["prereg"], raw_manifest=cards["manifest"])
    (GOLDEN / "verdict.json").write_text(V.canon_json(v))
    print("regenerated", sorted(p.name for p in GOLDEN.iterdir()))


if __name__ == "__main__" and "--regen" in sys.argv:
    regen()
