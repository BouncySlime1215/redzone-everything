# Deploy

Full detail: the docstring of `scripts/deploy.py` and [tonight-checklist.md](../../tonight-checklist.md).

1. Only when no game is live and Nick isn't watching. Mac on wall power, Tailscale quit.
2. Check first: `.venv/bin/python scripts/deploy.py deploy --dry-run` (fetches `integration/release`, runs tests, changes nothing).
3. Ship: `.venv/bin/python scripts/deploy.py deploy`. It tests off to the side, waits for no live game, switches, then watches health for 10 minutes and rolls back on its own.
4. Exit codes: 0 deployed, 1 refused, 2 deferred (game live, retry later), 3 tests failed (nothing changed), 4 rolled back, 5 rollback failed → [rollback.md](rollback.md).
5. First deploy on a Mac without the script: use the bootstrap one-liner in the checklist.
6. Log: `data/deploy/deploy.jsonl`; status: `.venv/bin/python scripts/deploy.py status`.

Never deploy from `main` or an unmerged branch. Untracked local data never blocks a deploy (#34).
