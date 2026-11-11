"""Public CI shows locked verdicts and never locked values (G-08, W2-LOCKED). Reads the workflow files as text."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github/workflows/ci.yml").read_text()
NORTHSTAR = (ROOT / ".github/workflows/northstar.yml").read_text()


def locked_step() -> str:
    start = CI.index("- name: release gate on the LOCKED test split")
    return CI[start:CI.index("- name: drop per-night rows", start)]


def test_raw_locked_scorecard_stays_out_of_the_artifact_glob():
    step = locked_step()
    assert '--out "$locked"' in step and 'locked="$RUNNER_TEMP/locked.json"' in step
    assert "--out locked.json" not in CI


def test_locked_gate_output_is_verdicts_only():
    step = locked_step()
    assert "results_ledger.py public-gate" in step
    assert "release_gate.py" not in step               # it prints the value column to the log
    assert "cat gate.md" not in step


def test_a_gate_fail_still_fails_the_release_pr():
    step = locked_step()
    assert 'if [ "$rc" != "0" ] && [ "${{ github.base_ref }}" = "codex/resume-redzone" ]' in step


def test_public_scorecard_artifact_is_scrubbed_first():
    scrub = CI.index("results_ledger.py scrub head.json base.json")
    upload = CI.index("name: scorecard\n", scrub)
    assert scrub < upload
    assert "steps.scrub.outcome == 'success'" in CI[scrub:upload]


def test_nightly_north_star_never_scores_the_locked_split():
    assert "scripts/northstar.py --split dev --night-seeds 8" in NORTHSTAR


def test_ledger_is_verified_in_ci():
    assert 'results_ledger.py verify --base "origin/$BASE_REF"' in CI
