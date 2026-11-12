# Deploy

Full detail: the docstring of `scripts/deploy.py` and [tonight-checklist.md](../../archive/tonight-checklist.md).

0. **What applies today (Nick, Nov 7 12:08 PM ET: "You can deploy whatever"; 22:35: do the work yourself).** Nick authorized agents to deploy, including while games are live. So: (a) the busy() live-game gate may be bypassed (`--if-paused`, or the overseer's wrapper in handoff §9) but **never skip the 10-minute health watch and auto-rollback**; (b) **never run a disruptive TV or stick test, or restart the app, while Nick is watching** (CLAUDE.md); a restart during a watch counts as a crash; (c) the branch the Mac runs is whatever the last deploy put there (check `scripts/deploy.py status`; at Nov 7 22:32 it was the unmerged `claude/buffered-jump`), not necessarily `codex/resume-redzone`. This authorization is Nick's words in decisions.md, not a general rule: if he says stop, stop.
1. Default (no authorization needed): deploy when no game is live and Nick isn't watching. Mac on wall power, Tailscale quit.
2. Check first: `.venv/bin/python scripts/deploy.py deploy --dry-run` (fetches `integration/release`, runs tests, changes nothing).
3. Ship: `.venv/bin/python scripts/deploy.py deploy`. It tests off to the side, waits for no live game, switches, then watches health for 10 minutes and rolls back on its own.
   - Games live but auto-switching paused on the phone (Nick's OK needed for a game-time deploy): add `--if-paused`. Live games then don't defer while the app reports paused; the watch and auto-rollback still run. Keep it paused until the watch ends (a rollback restarts the app again). `--at-break` is the halftime-only alternative.
4. Exit codes: 0 deployed, 1 refused, 2 deferred (game live, retry later), 3 tests failed (nothing changed), 4 rolled back, 5 rollback failed → [rollback.md](rollback.md).
5. First deploy on a Mac without the script: use the bootstrap one-liner in the checklist.
6. Log: `data/deploy/deploy.jsonl`; status: `.venv/bin/python scripts/deploy.py status`.

**Sync test scripts (`docs/sync/yttv_tests`).** They read the sticks from `RZ_STICKS=<hdmi1 host:port>,<hdmi2 host:port>`; without it they fall back to placeholders that reach nothing. On the Mac, set it in the shell from the Mac's own stick config before running them (`export RZ_STICKS=...`). Never commit the addresses or put them in a tracked file: the repo is public. Run them only when Nick isn't watching.

Never deploy from `main`. An unmerged branch is allowed only under the Nov 7 authorization in step 0 and must be named in the deploy journal; the normal path is `integration/release`. Untracked local data never blocks a deploy (#34).
