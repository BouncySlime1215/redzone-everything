# Cloud build prompts (one cloud session each, repo BouncySlime1215/redzone-everything)

Paste the **shared rules** plus ONE task into each session.

## Shared rules (put at the top of every prompt)

All current work is on branch `codex/resume-redzone` (`main` is stale): check it out and branch from it.
- Merge rule: the full test suite passes (`python -m pytest -q`). A few tests need a Mac or the sticks; note which ones fail, and compare against the unchanged branch.
- The scorecards must show a clear win: 0 mid-play cuts and nothing important worse.
- Scorecards are `scripts/eval.py` (NFL) and `scripts/eval_sports.py nba|nhl`. They read `data/archive/<league>/`, which is NOT in git. Rebuild it with `scripts/download_recent.py` / `scripts/download_season.py` (free ESPN API).
- Run the BASELINE on the unchanged branch first, then your change, on the same window.
- Use `data/eval_config.yaml` if present.
- $0 budget: ask before anything that costs money.
- The Mac has ~5 GB free disk and 8 GB RAM. Never run heavy jobs on it, and never write big files to it:
  no copies of `data/archive`, no bundles, no tarballs over ~50 MB, nothing outside `.xfer/`. Run
  `scripts/disk_guard.py` first; if it says the disk is low, stop and say so. Delete what you put in
  `.xfer/` when your thread is done. Prefer the pinned eval dataset over copying the archive.
- Commits end with "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>".
- Open a PR into `codex/resume-redzone`, never main. Don't merge it.

## 1. NHL: power plays, goals, overtime

The user wants NHL to "show me power plays, goals, and overtimes." The code is in `redzone/scoring/engine.py` (`_nhl`), `redzone/default_config.yaml` (`score.nhl`), and `redzone/decider/decider.py` (`CRUNCH_LOCKS`).

Today:
- A power play adds +15 (+22 in a close 3rd, +25 for a 5-on-3), and it only locks the game when closeness k >= 0.5.
- Overtime adds +70 and is treated as crunch.
- Goals are seen 99.5% of the time counting catch-up replays, but only ~41% live.

Steps:
1. Add a scorecard line: power-play minutes on screen, and power-play goals seen live.
2. A power play is always a candidate when the game is within 2 goals and not garbage time. It never beats crunch time elsewhere or the user's team late.
3. Overtime is never left except for crunch time elsewhere.

Done means: NHL goals seen live up, NHL crunch-live not worse, NBA/NFL unchanged, 0 mid-play cuts. Put a before/after table in the PR.

## 2. Hype v2: retrain the NBA part (needs branch `hype-review` pushed)

Branch `hype-review` has "Hype v2", a learned key-moment score.

What it did:
- NFL big plays seen LIVE went from 29.9% to 39.5%.
- But NFL your-team big plays went 100 → 93.1%, NBA big plays seen 95.4 → 90.8%, and NHL crunch 87.5 → 83.8%.
- An ablation showed the NBA loss comes from the model's own NBA rankings.
- A 50/50 blend (`score.v2.blend` 0.5) got NFL live 34.0%.

Steps:
1. Rebuild the training data (`scripts/build_training.py`).
2. Retrain the NBA (and if needed NHL) part. Keep NFL as is.
3. Keep your-team floors.
4. Re-run all three scorecards against the baseline.

Done means: NFL big-live up a lot, your-team big at 100%, NBA/NHL not worse, 0 mid-play cuts. Otherwise report the best trade-off honestly.

## 3. RedZone mode: NFL drive priority (needs branch `redzone-mode` pushed)

Port only commit 4c36104 from `redzone-mode` ("NFL drive heat, NFL priority over other sports' non-crunch moments, live drive before catch-up"). Drop its reel (9f53037), since `redzone/reel.py` replaces it. Respect `score.league_weight`.

Last time it cost NFL big plays seen 95.0 → 93.4% and your-team 100 → 93.1%. Rework it so a live NFL drive inside the 20 beats other sports' non-crunch play without losing big plays.

Score it on the NFL scorecard plus `python -m redzone.demo 20261101 --summary --json` (download that day with `redzone/demo/download.py`).

Done means: NFL drives seen live up, big plays seen not down, your-team big at 100%, 0 mid-play cuts.

## 4. Break-visit trip budget (needs branch `fv-review` pushed)

`fv-review` skips a break visit when the round trip doesn't fit in the home game's break.

What it did:
- NFL crunch live went 70.0 → 71.4%.
- But big plays seen went 95.4 → 93.9% and your-team big 100 → 93.1%.
- Nearly all of the loss is CLE @ CHI, where today's code shows 14 minutes of catch-up replays and the branch stays on a live drive instead.

Steps:
1. Port it onto the branch.
2. Find exactly why CLE @ CHI loses its big plays, and fix it so only visits that truly can't fit are skipped.

Done means: crunch gain kept, big plays seen at 95.4% or higher, your-team big at 100%, 0 mid-play cuts.
