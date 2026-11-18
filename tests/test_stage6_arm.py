"""Stage 6 reranker arm (task A-03): the fenced model file, the ModelRate port, causality, the arm end to end, L105's metric.

Every model here is a TOY made in the test (one tree per target, the export's own file layout); no real model or data
is read. The night is test_armeval's fixture night (two real NBA games from tests/fixtures).
Why: WORKPLAN v2 A-03 done-when: the stage 6 arm runs end to end through the F-04 harness on a toy model, refuses
unfenced models, and the show decides exactly as before when the arm is not used.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, TESTS_DIR.parent.as_posix())
sys.path.insert(0, TESTS_DIR.as_posix())

from test_armeval import FIXTURE_MINUTES, fixture_night  # noqa: E402

from redzone.lab import fence  # noqa: E402
from redzone.lab.armeval import arms as A  # noqa: E402
from redzone.lab.armeval import episode as E  # noqa: E402
from redzone.lab.armeval import stage6 as S6  # noqa: E402

FEATURES = ["lev_now", "since_stop", "last_kind", "since_to"]


def toy_export(tmp: Path, cut_value: float = 30.0, lo: float = 1.0, hi: float = 6.0) -> tuple[Path, Path]:
    """A state-only export pair (json + npz) with one NBA tree: worth `hi` within cut_value s of a stoppage, else `lo`."""
    T = {"nba_v60_f": np.array([1, -1, -1]), "nba_v60_t": np.array([cut_value, 0.0, 0.0]),
         "nba_v60_l": np.array([1, 1, 2]), "nba_v60_r": np.array([2, 1, 2]), "nba_v60_v": np.array([0.0, hi, lo]),
         "nba_v60_roots": np.array([0])}
    rows = [[1.0, 5.0, 1.0, 50.0], [1.0, 100.0, 0.0, 500.0]]
    J = {"version": "toy", "features": FEATURES, "missing": -999.0, "rank_target": {"nba": "v60"},
         "split": {"nba": {"first_test": "20251001"}},
         "models": {"nba": {"v60": {"iso": [[0.0, 10.0], [0.0, 10.0]], "check_rows": rows, "check_out": [hi, lo]}}}}
    jp, zp = tmp / "stage4_state_only.json", tmp / "stage4_state_only.npz"
    jp.write_text(json.dumps(J))
    np.savez(zp, **T)
    return jp, zp


def packed(tmp: Path, cutoff: str = "2025-09-30", **kw) -> Path:
    jp, zp = toy_export(tmp, **kw)
    out = tmp / "toy_stage6.npz"
    S6.stage6_pack(jp, zp, cutoff, out)
    return out


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    return fixture_night(tmp_path_factory.mktemp("stage6"))


def test_pack_stamps_the_cutoff_and_the_harness_fence_reads_it(tmp_path):
    m = packed(tmp_path)
    arm = A.make_arm("stage6_rerank", hook="redzone.lab.armeval.stage6:factory", model=str(m))
    got = arm.check(["20261101"])
    assert got == {"model_sha256": fence.file_sha256(m), "training_cutoff": "20250930", "model_fence": "clean"}
    model = S6.stage6_load(m, ["20261101"], got["model_sha256"])
    assert model["J"]["stage6_kind"] == "state_only" and S6.stage6_check(model) == 0.0


def test_a_cutoff_that_understates_what_the_model_saw_is_refused(tmp_path):
    jp, zp = toy_export(tmp_path)
    with pytest.raises(S6.Stage6ModelRefused, match="before 20250930"):
        S6.stage6_pack(jp, zp, "2025-04-01", tmp_path / "x.npz")


def test_an_export_without_a_cutoff_is_refused_like_the_shipped_files(tmp_path):
    """The Kaggle stage 6 files carry no training_cutoff: the harness and the loader both refuse them."""
    jp, zp = toy_export(tmp_path)
    m = tmp_path / "nocut.npz"
    with np.load(zp) as z:
        np.savez(m, meta=np.frombuffer(jp.read_text().encode(), dtype=np.uint8), **{k: z[k] for k in z.files})
    with pytest.raises(fence.FenceError, match="no training_cutoff"):
        A.make_arm("stage6_rerank", hook="redzone.lab.armeval.stage6:factory", model=str(m)).check(["20261101"])
    with pytest.raises(S6.Stage6ModelRefused, match="no training_cutoff"):
        S6.stage6_load(m, ["20261101"])
    with pytest.raises(S6.Stage6ModelRefused, match="no meta"):
        S6.stage6_load(zp)                                          # the raw export pair is not a model file


@pytest.mark.parametrize("cutoff,night,why", [("2026-10-15", "20261101", "inside the tuning"),
                                              ("2025-09-30", "20250915", "on or after the first eval night")])
def test_fenced_models_are_refused(tmp_path, cutoff, night, why):
    jp, zp = toy_export(tmp_path)
    m = tmp_path / "m.npz"
    S6.stage6_pack(jp, zp, cutoff, m)
    with pytest.raises(S6.Stage6ModelRefused, match=why):
        S6.stage6_load(m, [night])


def test_changed_bytes_and_wrong_trees_are_refused(tmp_path):
    m = packed(tmp_path)
    with pytest.raises(S6.Stage6ModelRefused, match="not the file the harness fence-checked"):
        S6.stage6_load(m, ["20261101"], "0" * 64)
    meta = json.loads(np.load(m)["meta"].tobytes().decode())
    meta["models"]["nba"]["v60"]["check_out"] = [0.0, 0.0]
    with np.load(m) as z:
        T = {k: z[k] for k in z.files if k != "meta"}
    np.savez(m, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **T)
    with pytest.raises(S6.Stage6ModelRefused, match="check rows"):
        S6.stage6_load(m, ["20261101"])


def test_the_show_takes_only_promoted_models(tmp_path):
    from redzone.models_rt.registry import UnpromotedModelError
    with pytest.raises(UnpromotedModelError):
        S6.stage6_promoted("toy-stage6", base=tmp_path / "ledger", root=tmp_path)


class _Arr:
    def __init__(self, n=4000, lev=1.0):
        self.lev, self.a0, self.a1 = [lev] * n, 0, n - 1


def _const_model(per_league: dict) -> dict:
    """A model whose league output is a constant (a single leaf), exactly as the packed reader walks it."""
    T, models = {}, {}
    for lg, v in per_league.items():
        T.update({f"{lg}_v60_f": np.array([-1]), f"{lg}_v60_t": np.array([0.0]), f"{lg}_v60_l": np.array([0]),
                  f"{lg}_v60_r": np.array([0]), f"{lg}_v60_v": np.array([v]), f"{lg}_v60_roots": np.array([0])})
        models[lg] = {"v60": {"iso": [[0.0, 100.0], [0.0, 100.0]]}}
    return {"J": {"features": FEATURES, "missing": -999.0, "rank_target": {k: "v60" for k in per_league},
                  "models": models}, "T": T}


def test_per_league_scale_and_clamp_match_the_notebook_selftest():
    """stage6_rerank.selftest_rate: NBA output 5x college, equal leverage -> equal mean rates; a 100x spike is held
    at 4x the planner's guess (the per-league scale would otherwise let one league dominate)."""
    rows = S6.Stage6Rows("nba", [{"type": {"text": "Jumpshot"}}] * 2, [0.0, 1.0])
    mr = S6.Stage6Rate(_const_model({"nba": 5.0, "cbb": 1.0}), ["nba", "cbb"], [rows, rows], 0, [0.0, 0.0], 0.02)
    mr.bind([_Arr(), _Arr()], None)
    a = np.mean([mr.rate(0, k, k) for k in range(300)])
    b = np.mean([mr.rate(1, k, k) for k in range(300)])
    assert abs(a - b) / max(a, b) < 1e-6 and abs(a - 0.02) < 1e-9
    spike = S6.Stage6Rate(_const_model({"nba": 1.0}), ["nba"], [rows], 0, [0.0], 0.02)
    spike.bind([_Arr()], None)
    for k in range(299):
        spike.rate(0, k * 10, k * 10)
    spike.m = _const_model({"nba": 100.0})
    spike.cache.clear()
    assert abs(spike.rate(0, 2990, 2990) - 4.0 * 0.02) < 1e-12 and spike.clamped == 1
    no_model = S6.Stage6Rate(_const_model({"nba": 1.0}), ["nfl"], [None], 0, [0.0], 0.02)
    no_model.bind([_Arr(lev=2.0)], None)
    assert no_model.rate(0, 5, 5) == 0.02 * 2.0 and no_model.calls == 0


def test_the_state_features_read_only_rows_already_shown():
    plays = [{"type": {"text": t}, "period": {"number": 1}} for t in ("Jumpshot", "Personal Foul", "Full Timeout",
                                                                      "Layup", "Shooting Foul")]
    wall = [100.0, 110.0, 120.0, 150.0, 200.0]
    f = S6.Stage6Rows("nba", plays, wall).features(160.0, 1.7)
    assert f == {"lev_now": 1.7, "since_stop": 40.0, "last_kind": 0.0, "since_to": 40.0}
    later = plays[:4] + [{"type": {"text": "Official Timeout"}, "period": {"number": 1}}]
    assert S6.Stage6Rows("nba", later, wall).features(160.0, 1.7) == f      # a row after 160 s changes nothing
    assert S6.Stage6Rows("nba", plays, wall).features(90.0, 1.0)["since_stop"] == 9999.0


def test_the_arm_runs_end_to_end_and_is_deterministic(night, tmp_path):
    m = packed(tmp_path)
    runs = []
    for _ in range(2):
        arm = A.make_arm("stage6_rerank", hook="redzone.lab.armeval.stage6:factory", model=str(m))
        arm.check([night.night])
        card, raw = E.run_night(arm, night, minutes=FIXTURE_MINUTES)
        runs.append((card, raw, arm.hook_obj.stats()))
    (c1, r1, s1), (c2, _r2, s2) = runs
    assert c1["decisions_sha256"] == c2["decisions_sha256"] and s1 == s2
    assert s1["rate_calls"] > 100 and s1["games_with_model"] == 2 and 0 < s1["rate_clamped"] < s1["rate_calls"]
    assert c1["pace"]["jumps"] >= 1 and set(c1["rule1"]) >= {"joins_mid_play", "leaves_mid_play", "hl_leaves_mid_play"}
    planner, _ = E.run_night(A.make_arm("planner"), night, minutes=FIXTURE_MINUTES)
    assert c1["decisions_sha256"] != planner["decisions_sha256"], "the toy model must move the planner"
    hl = S6.stage6_hl_card(r1, list(night.slate))
    assert 0 <= hl["hl_seen"] <= hl["hl_total"] and 0 <= hl["v2_seen"] <= hl["v2_total"]


def test_the_hook_changes_nothing_when_the_arm_is_not_used(night):
    """Flag-off: with the arm not used, the planner on the fixture slate decides byte for byte as the F-04 golden."""
    card, _ = E.run_night(A.make_arm("planner"), night, minutes=FIXTURE_MINUTES)
    assert A.make_arm("planner").describe()["seam"] == "none"
    want = json.loads((TESTS_DIR / "golden" / "armeval" / "fixture_runs.json").read_text())
    assert card["decisions_sha256"] == want["planner"]["decisions_sha256"]


def _raw(shown, base=1000, span=(0, 100), v2_total=10.0):
    return {"header": {"base": base, "span": list(span), "v2_total": v2_total,
                       "games": [{"league": "nba"}, {"league": "cbb"}]}, "shown": shown}


def test_l105_metric_counts_weighted_high_leverage_value_seen():
    facts = [{"v2": [(1010, 2.0, 0), (1050, 3.0, 0), (1200, 9.0, 0)], "hl": [(1040, 1060)]},
             {"v2": [(1045, 1.0, 0), (1046, -1.0, 0), (1070, 4.0, 0)], "hl": [(1044, 1047)]}]
    card = S6.stage6_hl_card(_raw([[0, 50, 0.0, 3.0], [1, 70, 0.0, 4.0]]), facts)
    # hl total: game 0 second 50 (3 x 1.05) + game 1 second 45 (1 x 1.0; second 46 is negative); seen: game 0 second 50
    assert card == {"hl_seen": 3.15, "hl_total": 4.15, "v2_seen": 7.0, "v2_total": 10.0}
    x = {"hl_seen": 6.0, "hl_total": 10.0, "v2_seen": 5.0, "v2_total": 10.0}
    b = {"hl_seen": 5.0, "hl_total": 10.0, "v2_seen": 5.0, "v2_total": 10.0}
    out = S6.stage6_l105([("nba_only", x, b)] * 4)
    assert out["hl_pooled"] == {"points": 10.0, "interval90": [10.0, 10.0], "ok": True}
    assert out["all_value"]["points"] == 0.0 and out["hl_per_mix"]["nba_only"]["nights"] == 4
