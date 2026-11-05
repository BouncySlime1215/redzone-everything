# Test Factory (engine 2): plan

Owner: the integrator. Nick asked for it on Nov 4 2026 at 11:10 PM ET: "every brain change is tested the moment it exists."
Lane 1 owns engine 1 (context and hype data). The factory grades what engine 1's features do to the brain. It never
grades the features themselves.

## Rules it runs under

- **Graders stay frozen.** Factory agents may *propose* a grader as a PR labelled `grader-proposal`. The integrator
  reviews it, adds it to `docs/grader_freeze.json` and bumps the freeze. Nothing the factory writes grades anything until
  then (rule 1).
- **Pre-registration first.** Every generated experiment lands as a `planned` row in `docs/experiments.md` (hypothesis,
  North Star line, success bar, nights needed) before anything runs. `grading_check.py` G2 and G3 already enforce this
  (rule 2). Factory ids come from the integrator block (600s).
- **Locked nights are never touched.** The factory mines and perturbs train and dev nights only. Locked stays read once
  per release, by the integrator.
- **No training signal from Nick.** His taps, votes, overrides and double Go now are evaluation inputs only, and they're
  graded on their own lines.
- **Never on the Mac.** Light jobs run in GitHub Actions on every PR (public repo, $0). The nightly sweep runs as a
  Kaggle CPU notebook ($0). There are no paid runners.

## What it does

| # | Part | What it produces | Where it runs |
|---|---|---|---|
| F1 | **Nightmare miner** (`scripts/nightmare_mine.py`) | Scans train/dev history for the nights that break brains: overlapping crunches across leagues, OT pileups, a your-team game late and close against an NFL two-minute drill, simultaneous breaks, NHL power plays in the last 2 min, feed drops, blowout → comeback. Each night becomes `tests/golden/<id>.json` with its source night id, the pattern, and its pinned value per line. | Kaggle nightly |
| F2 | **Golden ratchet** (rule 7) | `tests/golden/` is scored on every PR. A pinned value can only go up. A PR that lowers one fails, unless the PR opens with a measured reason, like rule #1. The integrator raises pins after each release. | CI, every PR |
| F3 | **Edge-case generator** | Seeded perturbations of real nights through `redzone/lab/night` faults: feed-lag spikes, missing or duplicated plays, clock errors, a stick offline, a cold Prime tune, benched sources (tonight's RC6.1 bug), sync drift. Every case records its seed and base night. | CI (small set), Kaggle (full) |
| F4 | **Two-harness grading** (tonight's lesson) | Every league set is graded on the honest harness *and* the live-sync (`--resync`) harness, and a set promotes only if it passes both. RC6.2's NBA set passed one harness and lost your-team crunch on the other. | CI + integrator review |
| F5 | **Off-policy grading of every logged night** | Each live night (`data/live_nights`, decision records) is replayed through the candidate brain on that night's real play-by-play and graded against live and the oracle on the same lines. Once exploration is on (Friday, flag, ≤5%, propensity logged), importance-weighted and doubly robust estimates are added with night-bootstrap CIs. | Kaggle nightly, after each night |
| F6 | **Experiment writer** | Each cycle, it turns gaps on the scorecard (the worst FAIL lines, ranked by the roadmap's gap score) into pre-registered experiment rows and edge-case sets for the lanes to pick up. | Integrator cycle (hourly) |
| F7 | **Brain-change hook** | A `factory` CI job on any PR touching `redzone/noggin`, `redzone/decider` or `league_overrides`. It runs F2 + F3 (small) + F4 and posts one delta table on the PR. | CI, every PR |

"24/7" means the scripts and scheduled jobs run around the clock. The agent work (F6, grader reviews) runs in the
integrator's hourly cycle, inside the usage brake (pause at 88%).

## First steps

1. **Thu morning.** F4: `--resync` becomes the second column of every per-league grade, so the RC6.3 call is made on
   both harnesses.
2. **Thu.** F1 + F2: the first 10 nightmare nights from dev history go into `tests/golden/`, with a ratchet test and a
   `grading_check.py` G4 (pins may only go up), as grader freeze v12.
3. **Thu–Fri.** F3: a small CI edge set (benched source, stick offline, lag spike, Prime cold), then the full set on
   Kaggle.
4. **Fri.** F5 on Wednesday's and Thursday's live nights (gold capture). F7 CI job.
5. **Sat.** F6 runs every cycle.

## First sources (for F1 and F5; all already in the repo)

- eval-data-v1, then v3 on Friday (#149): train/dev nights only.
- `data/live_nights/*/summary.json` and decision records: Nick's TV nights.
- Gold-data capture from Nov 4 (#173, on since RC6.1).
- `redzone/lab/night` fault library: e2e fault nights.
