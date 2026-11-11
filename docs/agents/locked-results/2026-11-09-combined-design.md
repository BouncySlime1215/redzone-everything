---
id: W2-LOCKED
title: Locked test data and results, one combined design
date: 2026-11-09
stream: locked-results
author: wave-2 locked-results agent (G-08)
status: draft
supersedes: [W1-ACCESS, W1-SEALED, W1-TAMPER]
sources: 3
verified: 3
affects: [.github/workflows/ci.yml, .github/workflows/northstar.yml, .claude/hooks/guard.py, .claude/settings.json, scripts/results_ledger.py, results/]
gate: [W2-LOCKED-01, W2-LOCKED-02, W2-LOCKED-03, W2-LOCKED-04, W2-LOCKED-05, W2-LOCKED-06, W2-LOCKED-07, W2-LOCKED-08, W2-LOCKED-09, W2-LOCKED-10]
---
# Locked test data and results: one design

## Summary

Today nothing is sealed: the locked nights and the grader are public, and CI prints locked scores on every PR.
Tier 0 ships now at $0 with no clicks: CI shows locked verdicts only, a hash-chained results ledger exists, and guard rule G8 covers the shell.
Tier 1 is the real seal and needs about 45 minutes of Nick's clicks: a bot account, a private grader repo, branch protection and a GitHub environment.
Tier 2 (a separate macOS user) is optional, and waits for the first release candidate.
Until Tier 1, treat every existing locked set as a public "dev-locked" set: useful for regression, never a release verdict by itself.

## Findings

Every `yes` below is a process-gate item for docs/PLAN.md section 2. "Fixed by patch" means `docs/agents/patches/g08.patch` or the scripts beside it.

| Id | Finding | Evidence | Files | WORTH FIXING | State |
|---|---|---|---|---|---|
| W2-LOCKED-01 | The locked-split gate prints its value column to the public log and job summary, and uploads the raw scorecard | `ci.yml` wrote `locked.json` into the `*.json` artifact glob and `cat gate.md` into `$GITHUB_STEP_SUMMARY`; `release_gate.py` also prints the table to stdout | ci.yml | yes | Fixed by patch |
| W2-LOCKED-02 | Per-night rows for locked nights reach the public artifact through the smoke scorecard | `ci_scorecard.py` defaults to `--split all`; `eval_sports.py` takes `sbs[-n:]`, the newest nights, which the `release_gate.py` HELDOUT text puts in the locked window (NBA, NHL 20261029-20261101); cards carry `per_night` | ci.yml, head.json | yes | Fixed by patch (`scrub`) |
| W2-LOCKED-03 | The nightly North Star scores the locked split and commits it into files every agent reads | `northstar.yml` ran with the default `--split both`, then committed `docs/northstar.md` (it has a "LOCKED unseen nights" section) and `docs/northstar-history.jsonl`; the raw JSON was an artifact | northstar.yml | yes | Fixed by patch for new runs. Old rows stay in git history |
| W2-LOCKED-04 | G8 covered only Read, Grep and Glob paths, and failed open | `guard.py` before this patch; `python scripts/eval.py --split test` and `cat results/locked/x` both passed | guard.py, settings.json | yes | Fixed by patch |
| W2-LOCKED-05 | G6 never caught `>>` appends to protected paths | New in this report. The regex tried `>` first, so `>> file` matched with target `>` and passed. Reproduced by a failing test | guard.py | yes | Fixed by patch |
| W2-LOCKED-06 | Nothing makes a recorded result tamper-evident | No ledger existed; W1-TAMPER section 1 | scripts/, results/ | yes | Shipped: `scripts/results_ledger.py`, CI verify |
| W2-LOCKED-07 | PR scorecard aggregates still come from locked nights | Same as 02: the smoke slice's NBA and NHL nights sit in the locked window, so the PR comment's counters are close to locked aggregates. Fix is `--split fit` on both smoke runs | ci.yml | yes | **Open.** Not in the patch: it changes what the PR counters measure, and only a CI run can show it still works |
| W2-LOCKED-08 | Locked nights and grader are public, so nothing is sealed | W1-SEALED section 1 (checked against `eval-data*.lock.json`) | eval-data releases, grading code | yes | Open: Tier 1 |
| W2-LOCKED-09 | Agents use Nick's identity, so code-owner review and required reviewers cannot bind | W1-ACCESS section 5.1: an author cannot approve their own PR, and an owner token can approve environments | GitHub settings | yes | Open: Tier 1 |
| W2-LOCKED-10 | A PASS/FAIL on every PR is still an adaptive query | W1-SEALED section 2 (Ladder, Thresholdout). Kept on purpose in Tier 0 so the existing gate keeps working | ci.yml | yes | Open: moves to release-only in Tier 1 |
| W2-LOCKED-11 | W1-TAMPER proposes extending `scripts/claims.py` to check quoted numbers | `claims.py` checks file-edit claims between parallel agents, not numbers | - | no | Correction: a number check would be new code |

### How the three reports were resolved

| Question | Reports said | Decision | Why |
|---|---|---|---|
| Where sealed nights live | ACCESS: encrypted public asset, key in environment secret. SEALED: private repo | **Private repo, never on the Mac** | A key in a secret inherits write-access risk, and public ciphertext is decryptable forever after one leak (SEALED section 3). SEALED's fallback stays the fallback |
| What the environment is for | ACCESS: the main lock | **Query limiter only**: the public-repo locked job waits for Nick | It costs one click and enforces the once-per-release budget. It does not hide the public nights |
| Agent identity | SEALED: bot optional. ACCESS: bot required | **Bot required** | Otherwise code-owner review has no one to bind (W2-LOCKED-09) |
| Rulesets or branch protection | SEALED: rulesets on Free unconfirmed. ACCESS: snippet says yes | **Classic branch protection** | Both reports agree it exists; neither confirmed rulesets by reading the plan page |
| Who owns the ledger | TAMPER: macOS user `rzledger`, `results` branch by CI. ACCESS: `rzgrader` with a vault | **One optional user, `rzgrader`; Tier 0 chain lives in the repo, anchored by the base branch** | Same-user agents can call any local sealer, so the separate user only matters with Tier 2 hardware holdouts |
| Coarseness of public output | SEALED: PASS/FAIL, counters on hard fail. ACCESS: three buckets | **Gate, scope, PASS/FAIL/PENDING and totals. No values, no buckets** | A `pass_thin` label needs a margin, which is a value. The gate name already reveals zero or not-zero |
| Local vault | ACCESS: `~/redzone-vault` now | **Not created at Tier 0** | With sealed nights off the Mac there is nothing to put in it. Rules still deny the path for Tier 2 |

W1-TAMPER verified no source beyond link checks. Only its macOS `chflags` test was run. Its design is used for the record format, where the logic stands on its own; none of its external claims are relied on here.

## What to change in this repo

### Tier 0: ships now ($0, no clicks)

Status: the scripts, tests and folder are in the tree; the items marked (patch) wait for `git apply docs/agents/patches/g08.patch`.

1. (patch) `ci.yml`: raw locked scorecard stays in `$RUNNER_TEMP`, the summary gets `public-gate` verdicts, and a release-PR FAIL still fails the job.
2. (patch) `ci.yml`: `scrub` drops per-night rows from head and base JSON before upload. No scrub, no upload. The PR comment reads aggregates only (tested).
3. (patch) `northstar.yml`: `--split dev`. `docs/northstar.md` loses its locked section on the next run.
4. (patch) `ci.yml`: `results_ledger.py verify --base origin/<base>` in the memory job.
5. (patch) `guard.py`: G8 covers shell text, local runs of `--split test`, bare `northstar.py` and full artifact downloads, and is the one rule that fails closed. Ledger files join G6. `>>` fixed. `settings.json` gains deny rules.
6. Shipped: `scripts/results_ledger.py`, `tests/test_results_ledger.py`, `results/` with its genesis row.

Unchanged and still visible on every PR: scorecard PASS/FAIL, the mid-play hard rule, all counters in the PR comment, gate tallies.

### Results layout

| Path | Holds | In git | Writer | Agents |
|---|---|---|---|---|
| `results/ledger.jsonl` | One hash-chained row per run | yes | `results_ledger.py` only | read |
| `results/HEAD` | `<seq> <hash>` of the last row | yes | `results_ledger.py` only | read |
| `results/summary/latest.json` | Latest row per kind/suite, run counts, retractions | yes | `results_ledger.py` only | read: **the file to cite** |
| `results/dev/<UTC-date>-<sha7>-<suite>.json` | Full detail on dev nights | no (ignored) | any agent | read, write |
| `results/locked/` | Reserved; stays empty until Tier 2 | no (ignored) | nobody in a Claude session | denied (G8) |
| `redzone-sealed` repo (Tier 1) | Sealed nights, pinned grader, private ledger | private | Nick, sealed job | none |

Suite names are short lowercase slugs: `nfl-smoke`, `hdmi1-nfl`. Row kinds: `genesis`, `prereg`, `result`, `live`, `locked_eval`, `retract`.
A `locked_eval` row holds a verdict and release tag only, once per release. A wrong row stays and gets a `retract` row with a reason.
Only the coordinator appends, so two branches cannot fork the chain.

### How the coordinator works

1. Read results: `python scripts/results_ledger.py summary`, or the PR comment and CI job summary.
2. Check a number is unedited: `python scripts/results_ledger.py verify --base origin/integration/next`.
3. Debug a failure on dev nights: `--split fit`, full detail in `results/dev/`. Never the locked split.
4. Run a live TV test: nothing changes under G3. Nick creates `data/.agent_hw_ok` when he is not watching, and the test comes from `docs/test-queue.md`.
5. Record it: `python scripts/collect_live_test.py ...`, then `python scripts/results_ledger.py append --kind live --suite hdmi1-nfl --verdict PASS --metric entries_mid_play=0 --raw <summary.json>`. The row holds the raw file's sha256.

Live logs are not locked. They stay in `data/`, and the coordinator reads them to debug. Designating a live holdout is a Tier 2 choice.

### Tier 1: needs Nick (about 45 minutes)

Agents never create accounts or handle tokens. Do these in order.

1. **Bot account, 10 min.** Sign up for a free GitHub account for agents. On this repo: Settings > Collaborators > add it with Write. As the bot: Settings > Developer settings > Fine-grained tokens > Generate; only `redzone-everything`; Contents, Pull requests and Issues read/write; Metadata read; everything else No access. On the Mac run `gh auth login --with-token` and paste it yourself.
2. **Private repo, 2 min.** github.com/new, owner BouncySlime1215, name `redzone-sealed`, Private. Do not clone it on the Mac.
3. **Status token, 8 min.** From your account, a fine-grained token: only `redzone-everything`, Commit statuses read/write, 90-day expiry. Add it in `redzone-sealed` > Settings > Secrets and variables > Actions as `PUBLIC_STATUS_TOKEN`. Set a rotation reminder.
4. **Branch protection, 10 min.** Settings > Branches > Add classic rule, for `integration/release` and `codex/resume-redzone`: require a PR, require Code Owner review, require status checks, do not allow bypassing. For `integration/next`, require status checks and Code Owner review only: the nightly North Star commits there directly, and a required PR would send it to its artifact-only fallback.
5. **Environment, 5 min.** Settings > Environments > New `grader`: required reviewer you, prevent self-review on, deployment branches `integration/release` and `codex/resume-redzone`.
6. **Pin the grader, 10 min.** Review and run the script agents prepare, which copies the frozen grader files and their hashes into `redzone-sealed/grader/` from a clean clone.

Then agents build, about 4 hours (W1-SEALED section 6): `.github/CODEOWNERS`, `sealed-gate.yml` with the grader overlay and `--network none`, slice rotation, the Ladder rule, and a release-only `locked-gate` job under `grader` (closes W2-LOCKED-10). Each sealed verdict also goes in the ledger as `locked_eval`. Not built yet: the honeytoken and the monthly impossible-task canary (W1-SEALED Layer 3).

### Tier 2: optional, after the first release candidate (about 15 minutes)

A macOS user `rzgrader` owns `~/redzone-vault` (mode 700), reached through one sudoers line and a root-owned wrapper (W1-ACCESS section 7.4). It is what makes a live holdout or a raw-log vault truly unreadable to same-user agents. Check adb works under that user before relying on it. Trial the Claude Code sandbox separately: its network allowlist has not been tested against adb and the LAN.

## Risks / unverified

- **Not run on GitHub.** The patch was applied in a throwaway clone and its tests pass, and both workflows parse. No real CI run has exercised them.
- **Old leaks stay.** Past artifacts and logs last up to 90 days; `gh run view --log` stays allowed because CI debugging needs it. Git history keeps old locked rows. This is why current locked sets count as burned.
- **G8 reads command text only.** Encoded paths or a script that opens a file defeat it. Tier 1 is the control, since the data is then not on the Mac.
- **The ledger is tamper-evident, not honest.** It cannot catch a false number written at append time, and someone able to rewrite the base branch can rewrite the chain. Tier 1 protection is what anchors the base.
- **North Star loses its locked column** until the sealed gate exists. Scoring the locked set nightly already broke the once-per-release rule.
- **GitHub plan facts** are from the Nov 8 reports. Rulesets on Free remain unconfirmed; `sysadminctl` and sudoers lines for Tier 2 are untested.

## Bibliography

| Id | Source | Status |
|---|---|---|
| 1 | W1-ACCESS, [2026-11-08-access.md](2026-11-08-access.md), wave-1 agent, 2026-11-08 (18 of 22 sources read) | opened |
| 2 | W1-SEALED, [2026-11-08-sealed-holdout.md](2026-11-08-sealed-holdout.md), wave-1 agent, 2026-11-08 (22 of 24 sources read) | opened |
| 3 | W1-TAMPER, [2026-11-08-tamper-evident.md](2026-11-08-tamper-evident.md), wave-1 agent, 2026-11-08 (0 of 30 sources read, link checks only) | opened |
