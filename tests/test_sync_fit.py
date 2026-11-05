"""[sync fit] EXP-084: a night's delay error comes from anchors graded against the estimate before them.
Serves: EXP-084. Owner: lane 3."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_fit


def _rows(rng, n, err_sd, stick="10.0.0.5:5555"):
    rows, t, mu = [{"t": 0.0, "target": "youtubetv:espn", "kind": "tune", "mu": 45.0, "behind": 0.0}], 0.0, 45.0
    for _ in range(n):
        t += 60.0
        z = 41.0 + rng.gauss(0, err_sd)
        rows.append({"t": t, "target": "youtubetv:espn", "kind": "break", "z": round(z, 2), "used": True,
                     "stick": stick, "mu": 41.0, "behind": 0.0})
    return rows


def test_anchors_are_graded_against_the_estimate_before_them_and_need_enough_of_them():
    rng = random.Random(3)
    ok = sync_fit.delay_errors(_rows(rng, 40, 0.6))
    assert ok["grade"] == "PASS" and ok["n"] == 40 and ok["p95"] <= 2.0 and list(ok["per"]) == ["stick 1"]
    bad = sync_fit.delay_errors(_rows(rng, 40, 3.0))
    assert bad["grade"] == "FAIL"
    few = sync_fit.delay_errors(_rows(rng, 5, 0.6))
    assert few["grade"] == "PENDING"
    first = sync_fit.delay_errors(_rows(rng, 1, 0.0))   # the first anchor after a tune is graded vs the prior
    assert first["n"] == 1 and first["per"]["stick 1"]["p50"] == 4.0


def test_a_night_dir_with_only_sync_rows_still_grades_and_prints_no_addresses(tmp_path):
    rows = _rows(random.Random(1), 25, 0.5)
    (tmp_path / "sync.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = sync_fit.fit(tmp_path, None, None)
    assert out["seek"] is None and out["bursts"] == {} and out["delays"]["delay_s:youtubetv:espn"]["n"] == 25
    assert "10.0.0.5" not in json.dumps(out) and out["delay_error"]["grade"] == "PASS"
