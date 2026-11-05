# The pinned eval dataset

Every scorecard (`scripts/eval.py`, `scripts/eval_sports.py nba|nhl`, `python -m redzone.demo 20261101 --summary --json`)
grades on **one** snapshot of real nights, so a number from one thread, CI or the Mac means the same thing everywhere.

| | |
|---|---|
| Lock (in git) | `eval-data.lock.json` at the repo root: tag, asset sha256, window per league, per-file tree digest |
| Release | tag `eval-data-v1`, assets `eval-archive.tgz` (~24 MB) and `eval-archive.tgz.sha256` |
| URL | `https://github.com/BouncySlime1215/redzone-everything/releases/download/eval-data-v1/eval-archive.tgz` |
| Window | NFL 2025-09-05 .. 2026-11-01, NBA 2026-10-20 .. 2026-11-01, NHL 2026-10-19 .. 2026-11-01 |
| Also inside | `data/eval_config.yaml` (pinned switch costs), `data/demo/20261101/` (the Nov 1 mixed demo), `data/eval_pin.json` (every file's sha256) |

## Use it

```bash
python scripts/fetch_eval_data.py          # download (cached in ~/.cache/redzone-eval), verify, extract into data/
python scripts/fetch_eval_data.py --check  # is my data/ exactly the pin? (no network)
```

Stdlib only, no token (the repo is public). It only writes the pinned files; other nights in `data/archive`
stay. If a local file differs from the pin (the live recorder refreshed it), it stops; `--force` replaces it.

Each scorecard prints `"eval_data": "eval-data-v1"` when it graded the pinned data, or `"UNPINNED: ..."` with
the first difference. Numbers from an UNPINNED run are not comparable. The scorecards also take their last night
from the lock (`window.<league>.end`), so new nights the service archives at 5 AM never slide into a comparison.

In GitHub Actions, cache `~/.cache/redzone-eval` keyed on `hashFiles('eval-data.lock.json')`.

## Moving the window (on purpose)

On a machine with the archive (the Mac, low priority):

```bash
python scripts/build_eval_data.py --tag eval-data-v2 --out /tmp/evaldata \
  --window nfl:20250905:20261108 --window nba:20261020:20261108 --window nhl:20261019:20261108 --demo 20261101
```

Cloud sessions can't create releases directly, so the release is made by a workflow that lives only on the
`eval-data` branch (`.github/workflows/publish-eval-data.yml`, free, `GITHUB_TOKEN`): replace
`eval-archive.tgz`, `eval-archive.tgz.sha256` and README.md on that branch, set `TAG: eval-data-v2` in the
workflow, push. It checks the sha256 and creates the release only if the tag doesn't exist yet. (With
release rights, `gh release create eval-data-v2 eval-archive.tgz eval-archive.tgz.sha256` does the same.)

Commit the rewritten `eval-data.lock.json` in its own PR with the new baseline numbers. The build is
deterministic (same files give the same sha256). Never replace an existing tag's asset: older branches pin it.
`providers.profiles` is stripped from `eval_config.yaml` before publishing (grading never reads it).

## Friday Nov 6: switch the default pin to eval-data-v3

eval-data-v2 is published but its locked set reaches back over v1 dev nights (leak_check FAILs it), so it is
never the default. v3 waits for the Nov 5 night: then every league has >= 8 locked nights, all at or after v1's
locked start (Oct 29). Its old your-team nights (NBA/NHL) come from Oct 2025 - Jan 2026 only, before the
Noggin's own locked start (agent/distill/split.py BIG_CUTS, Feb 1), so no pin-dev night is a Noggin-locked one.

1. Build (GitHub runner, ~11 UTC Fri): `git checkout -B claude/eval-data-v2 origin/integration/next` and push it.
   `.github/workflows/eval-data-v2.yml` backfills to 20261105, builds, leak-checks and publishes `eval-data-v3`
   with `eval-data-v3.lock.json` (only if the tag is new). NOT READY = a skipped build: re-run later.
2. Switch (cloud session): `python scripts/switch_eval_pin.py --tag eval-data-v3`: rewrites eval-data.lock.json
   (the v1 pin is kept as eval-data-v1.lock.json), points ci.yml / northstar.yml / CLAUDE.md at v3, fetches,
   runs leak_check, writes agent/kaggle/assets/locked_v3.json, and lists what's left by hand.
3. One PR: the decisions.md line, the docs row, the Kaggle `--locked` default, and the new baseline
   (`scripts/northstar.py --split both`; locked read once).
