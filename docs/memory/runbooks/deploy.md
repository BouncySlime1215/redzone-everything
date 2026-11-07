# Deploy

Full detail: the docstring of `scripts/deploy.py` and [tonight-checklist.md](../../tonight-checklist.md).

1. Only when no game is live and Nick isn't watching. Mac on wall power, Tailscale quit.
2. Check first: `.venv/bin/python scripts/deploy.py deploy --dry-run` (fetches `integration/release`, runs tests, changes nothing).
3. Ship: `.venv/bin/python scripts/deploy.py deploy`. It tests off to the side, waits for no live game, switches, then watches health for 10 minutes and rolls back on its own.
   - Games live but auto-switching paused on the phone (Nick's OK needed for a game-time deploy): add `--if-paused`. Live games then don't defer while the app reports paused; the watch and auto-rollback still run. Keep it paused until the watch ends (a rollback restarts the app again). `--at-break` is the halftime-only alternative.
4. Exit codes: 0 deployed, 1 refused, 2 deferred (game live, retry later), 3 tests failed (nothing changed), 4 rolled back, 5 rollback failed → [rollback.md](rollback.md).
5. First deploy on a Mac without the script: use the bootstrap one-liner in the checklist.
6. Log: `data/deploy/deploy.jsonl`; status: `.venv/bin/python scripts/deploy.py status`.

**Sync test scripts (`docs/sync/yttv_tests`).** They read the sticks from `RZ_STICKS=<hdmi1 host:port>,<hdmi2 host:port>`; without it they fall back to placeholders that reach nothing. On the Mac, set it in the shell from the Mac's own stick config before running them (`export RZ_STICKS=...`). Never commit the addresses or put them in a tracked file: the repo is public. Run them only when Nick isn't watching.

Never deploy from `main` or an unmerged branch. Untracked local data never blocks a deploy (#34).
