# CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every pull request (any base branch, including
`integration/next`) and on pushes to `codex/resume-redzone` and `integration/next`.
Free tier only: the repo is public, so GitHub-hosted Ubuntu runners cost nothing; the built-in
`GITHUB_TOKEN` posts the comment, and no secrets are used.

| Job | What it does | Blocks the PR when |
|---|---|---|
| lint | `ruff check .` with the real-error rules in `pyproject.toml` (syntax errors, undefined names, broken format strings, bad `is`/comparisons) | any error |
| data | downloads the pinned eval dataset (release `eval-data-v1`, asset `eval-archive.tgz`), checks its sha256, keeps it in the Actions cache | the checksum does not match |
| test | Python 3.12 and 3.13: `tests/test_e2e*.py` first as its own step, then the whole suite with the pinned data unpacked (so archive-backed tests run) | any test fails |
| scorecard | NFL/NBA/NHL scorecards and the Nov 1 mixed demo, on the PR **and** on its base, same data | the PR adds mid-play cuts, or a scorecard crashes on the PR |
| report | one PR comment (updated on every push) with tests, lint and the PR-vs-base table | never |

**Scorecard size.** PRs into `integration/next`, pushes to it, and manual runs grade the full merge
scorecard (NFL 16 slates, NBA/NHL 14 nights). Every other PR grades a fast slice (NFL 4 slates,
NBA/NHL 3 nights, the demo). Slice numbers only compare with the same slice, which is what the
comment does. Other metric changes get ▲/▼ with ⚠️ when worse by 0.5 points or more; they are
for review, not gated.

**Mac-only tests.** Mark them `@pytest.mark.mac_only`. `tests/conftest.py` skips them by name
on anything but macOS, and the comment counts them. Don't use ad-hoc `platform.system()` skips.

**Run it locally.**

    ruff check .
    python -m pytest -q -rs
    python scripts/ci_scorecard.py --out head.json            # needs the pinned data in data/
    python scripts/ci_scorecard.py --root ../base --out base.json
    python scripts/ci_report.py --head head.json --base base.json

**New dataset pin.** Publish it under a new tag and change `EVAL_DATA_TAG` in the workflow. The
cache is keyed on the tag, so re-uploading assets under the same tag would keep serving the old
cached copy.
