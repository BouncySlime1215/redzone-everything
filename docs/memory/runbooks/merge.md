# Merging

How to bring two lines of work together without losing either. Source:
[W1-RULES](../../agents/rules/2026-11-08-debug-validate-merge.md) gap 4. Textual success proves little: the best models
resolve under 60 percent of real conflict hunks correctly.

**Once per clone** (about 5 seconds): `git config rerere.enabled true`. Git then replays a conflict resolution you
already made. Check what it reused before committing.

1. **Merge, don't rebase, on a shared branch.** Anything another session or a PR has fetched keeps its history:
   `git fetch origin && git merge origin/integration/next`. Never force-push (G2). Rebase only a branch nobody else has.
2. **Merge the latest base before the final test run.** Test the combination, not the branch alone.
3. **Match same-commit copies by subject.** Cherry-picks and merged branches hold the same change under different
   shas, so compare subjects before treating a side as new work:
   `git log --format='%h %s' --left-right --cherry-pick HEAD...origin/integration/next`, and
   `git log --all --format='%h %s' --grep='<subject>' -F` for one change.
4. **Keep both sides' intent.** For each conflicted hunk, read both commits' `Why:` lines
   (`git log --merge -p -- <file>`), then write what both wanted. Never "take ours" or "take theirs" on a whole file.
   In `decider.py`, `engine.py` and `control.py`, re-read Rule #1 before saving.
5. **List every conflicted hunk in the PR:** `file:hunk: kept A / kept B / both, because ...`.
6. **Never resolve by editing a test or a gate.** A conflict with one means `BLOCKED: <reason>`.
7. **Run the full suite before pushing:** `python -m pytest -q -rs`, one run at a time, plus
   `python scripts/ci_scorecard.py --nfl-n 2 --nights 4` for the leagues the conflicts touched.
8. **Push a new commit.** `git push origin <your-branch>`, then PR into `integration/next`.

**A red batch is bisected, not retried** ([debugging](debugging.md) step 4). A PR gets 2 queue tries, then goes back
with evidence ([coordination](../../coordination.md) section 6).

**Branch protection** is set by Nick in GitHub: the exact clicks are in [docs/ci.md](../../ci.md).
