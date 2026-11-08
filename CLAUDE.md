# CLAUDE.md: rules for every AI working on RedZone

RedZone for every sport on Nick's Samsung TV: two Fire TV sticks, switching only at dead balls.
League order NFL > CFB > NBA > NCAA basketball > NHL > MLB; his teams (WSH, UNC) first; crunch time first.
Spec: [redzone-north-star](docs/context/claude-memory/redzone-north-star.md). Gap to perfect:
[docs/northstar.md](docs/northstar.md). Next work: [docs/roadmap.md](docs/roadmap.md).

Start: run `python scripts/context_pack.py`, then the "Verify first" commands in [docs/handoff.md](docs/handoff.md).
Find code with `python scripts/citymap.py --focus <file>` / `--impact <file>`, not by reading many files.
The only plan: [docs/PLAN.md](docs/PLAN.md). Organization standard: [docs/STANDARD.md](docs/STANDARD.md).
Index of all docs: [docs/README.md](docs/README.md). How agents work together: [docs/coordination.md](docs/coordination.md).

## Rule #1

Join a game only at a dead ball. Leave mid-play only at low leverage (after a score, in transition, early,
not close); never leave a high-leverage moment (close and late, or overtime: `redzone/lab/leverage.py`).
Checked by: CI counters on replayed nights (entries_mid_play must stay 0; high-leverage mid-play leaves,
plays_missed_entry and dead_air may not rise without a measured reason at the top of the PR).
If your change and a Rule #1 test disagree: stop and write `BLOCKED: <reason>`. Do not edit the test.

## Hard rules (each is enforced; the hook message tells you what to do instead)

- Do not read, print, commit or ask for secrets. Nick adds them. The repo is public. Enforced: guard.py G1.
- Do not push or merge to `main`, `codex/resume-redzone` or `integration/release`; no force-push.
  Every PR targets `integration/next`. Enforced: guard.py G2 + deny rules.
- Do not run adb, stick or TV scripts; the TV may be in use. Use `tests/fake_adb.py` or the simulator.
  Nick unlocks live tests with `data/.agent_hw_ok`. Enforced: guard.py G3.
- Do not train or run heavy jobs on the Mac (8 GB, runs the live app; local AI ≤ ~2 GB RSS). Use Kaggle or CI.
  Enforced: guard.py G4.
- Deploy only with `scripts/deploy.py --dry-run`. Nick unlocks real deploys. Enforced: guard.py G5.
- Do not edit protected paths: `.claude/`, `.github/`, `tests/conftest.py`, release gate, leak check, eval
  scripts and locks, gate baseline, grader freeze. Those are grader PRs for Nick. Enforced: guard.py G6 + CI.
- Never use Peacock. Enforced: guard.py G7.
- Every state change passes a reason: `machine.to("state", why="...")`. Enforced: tests (`redzone/fsm.py`).
- $0 budget: ask Nick before anything that costs money.
- Stop all Claude work at 88% of weekly usage ([runbook](docs/memory/runbooks/usage-pause.md)).
- At most 3 builders + 1 verifier at once (5 only for independent cards); claim files first:
  `python scripts/claims.py new <card> --files <globs>`. Enforced: CI claims check.
- Every commit has a `Why:` line (one sentence) and ends with the `Co-Authored-By:` line your session gives
  you. Enforced: `scripts/commit_why.py` in CI.
- To learn why something happened or why a setting is what it is: `python scripts/why.py <HH:MM | file | key>` or `--night <date>`
  ([runbook](docs/memory/runbooks/why.md)).

## Release gate

"95% good minimum", judged only on the locked test set plus fresh nights
([architecture.md §7](docs/architecture.md)). Anything that misses its gate ships OFF behind its flag.
No overfitting: time splits by whole night, a locked set of ≥25% of nights scored once per release,
forward-chaining CV (mean and worst fold), leakage checks in CI.

## Commands

```sh
python -m pytest -q tests/<file>.py            # prefer targeted tests; full suite only when the app is idle
ruff check .                                   # lint, real errors only
python scripts/fetch_eval_data.py    # pinned eval data eval-data-v1, once
python scripts/ci_scorecard.py --nfl-n 2 --nights 4   # scorecards; only the leagues you touch
python scripts/release_gate.py heldout.json    # the 95% gates
python scripts/check_memory.py                 # links resolve, decisions logged (CI)
python scripts/state_machines.py --check       # state-machine doc current (CI)
python scripts/citymap.py --check              # code map current (CI)
```

Before each PR: self-review, list known risks, paste the CI before/after table (entries_mid_play,
plays_missed_entry, dead_air_pct), name the North Star line it moves.

## Branches and memory

- Base `codex/resume-redzone` is what the Mac runs. PRs → `integration/next`; INTEGRATION ("Run the
  integration train") merges; release `integration/next` → `integration/release` → `codex/resume-redzone`.
- [decisions.md](docs/memory/decisions.md): every user decision with date and reason. A PR that changes a
  default or a gate adds a line (CI enforces). [lessons.md](docs/memory/lessons.md): one per fix, with
  `Signals:`. [glossary](docs/memory/glossary.md), [runbooks](docs/memory/runbooks/README.md),
  [moving accounts](docs/new-environment.md), [live status](docs/handoff.md), [board](docs/status.md).
