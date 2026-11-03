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
gh release create eval-data-v2 /tmp/evaldata/eval-archive.tgz /tmp/evaldata/eval-archive.tgz.sha256 \
  --repo BouncySlime1215/redzone-everything --title "Eval data v2" --notes "window ..."
```

Commit the rewritten `eval-data.lock.json` in its own PR with the new baseline numbers. The build is
deterministic (same files give the same sha256). Never replace an existing tag's asset: older branches pin it.
`providers.profiles` is stripped from `eval_config.yaml` before publishing (grading never reads it).
