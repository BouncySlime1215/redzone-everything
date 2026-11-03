# CLAUDE.md: rules for every AI working on RedZone

Read this first. Then [docs/handoff.md](docs/handoff.md) (live status),
[docs/status.md](docs/status.md) (board and queue) and
[docs/architecture.md](docs/architecture.md) (contracts and owners). To brief a brand-new session,
paste the output of `python scripts/context_pack.py`.

## North star

RedZone for every sport on Nick's Samsung TV: two Fire TV sticks, switching only at dead balls.
League order NFL > CFB > NBA > NCAA basketball > NHL > MLB; his teams (WSH and UNC) come first;
crunch time first. Full spec: [redzone-north-star](docs/context/claude-memory/redzone-north-star.md).

**Rule #1: never land mid-play.** 0 mid-play cuts is hard. No PR may raise mid-play entries,
plays missed on entry or dead air unless a measured reason sits at the top of the PR.

## Release gate

"95% good minimum", judged only on the locked test set plus fresh nights
([architecture.md §7](docs/architecture.md)). Anything that misses its gate ships OFF behind its
flag. No overfitting: time splits by whole night, a locked set of ≥25% of nights scored once per
release, forward-chaining CV (report the mean and the worst fold), and leakage checks in CI.

## Hard constraints

- **Never touch secrets.** Never read, print, commit or ask for tokens, passwords or keys. Nick
  adds secrets himself. The repo is public, so redact issues and logs.
- **$0 budget.** Ask before anything that costs money.
- **The Mac is light** (8 GB M2, it runs the live app): no training and no heavy jobs on it.
  Train on Kaggle or in the cloud. Local AI stays ≤ ~2 GB RSS.
- **Usage pause:** all Claude work stops at **88%** of Nick's weekly usage
  ([runbook](docs/memory/runbooks/usage-pause.md)).
- **At most 5 working threads** (fewer when the pacing rule in status.md says so). Everything else
  waits in the queue in docs/status.md.
- No disruptive TV tests while Nick is watching. Never use Peacock.

## Branches

- `codex/resume-redzone` is the base and what the Mac runs. **Never merge to `main`.**
- Every PR targets `integration/next`. INTEGRATION ("Run the integration train") merges it.
- The release goes `integration/next` → `integration/release` → `codex/resume-redzone`.
- Commits end with the `Co-Authored-By:` line your session gives you.

## Run things

```sh
python -m pytest -q -rs              # full suite (mac_only tests skip off macOS)
ruff check .                         # lint, real errors only
python scripts/fetch_eval_data.py    # pinned eval data eval-data-v1, once
python scripts/ci_scorecard.py --nfl-n 2 --nights 4   # scorecards; run only the leagues you touch
python scripts/release_gate.py heldout.json           # the 95% gates
python scripts/check_memory.py       # links resolve, decisions logged (also runs in CI)
```

Before each PR: self-review, list the known risks, and add a before/after table with
entries_mid_play, plays_missed_entry and dead_air_pct ([PR rules](docs/memory/decisions.md)).

## Memory

- [docs/memory/decisions.md](docs/memory/decisions.md): every user decision, with date and
  reason. **A PR that changes a default or a gate must add a line here** (CI enforces it).
- [docs/memory/lessons.md](docs/memory/lessons.md): bugs we hit and why. Add one after each fix.
- [docs/memory/glossary.md](docs/memory/glossary.md): the words we use.
- [docs/memory/runbooks/](docs/memory/runbooks/README.md): deploy, rollback, Kaggle run,
  TV setup, stick offline, usage pause.
- Moving accounts: [docs/new-environment.md](docs/new-environment.md).
