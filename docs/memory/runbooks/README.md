# Runbooks

Short, do-this-now steps. Each one links to the full doc. Run everything from the Mac repo folder
`/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone` unless it says otherwise.

| Situation | Runbook |
|---|---|
| Cut a release candidate (lane 4) | [rc-cut.md](rc-cut.md) |
| Ship new code to the Mac | [deploy.md](deploy.md) |
| A deploy made things worse | [rollback.md](rollback.md) |
| Start or check a Kaggle training run | [kaggle-run.md](kaggle-run.md) |
| Set up TV-side input switching | [tv-setup.md](tv-setup.md) |
| A stick looks offline | [stick-offline.md](stick-offline.md) |
| Weekly usage is near the cap | [usage-pause.md](usage-pause.md) |
| The phone says the Mac is the problem (battery, sleep, memory, paused, no switches) | [mac-health.md](mac-health.md) |

## Memory hook

The AI that repairs RedZone reads memory before acting and writes it back after:

- **Watchdog** (`agent/watchdog.py`, via `redzone/memory.py`): before asking its local model about
  an unknown error, it adds the lessons whose `Signals:` match the error (at most 3, ~1.2 KB) to
  the prompt. When a matching lesson exists and the model can't judge, the phone note names it.
  After an action works (a restart, a reconnect, a disk prune), it appends a pending lesson to
  `data/memory/pending_lessons.jsonl` on the Mac, at most one per error per day.
  `scripts/context_pack.py` shows pending lessons so the next session can turn them into a PR
  against [lessons.md](../lessons.md).
- **Auto-fix brain** (`.github/auto-fix/prompt.md`, PR #30): reads `CLAUDE.md`,
  [lessons.md](../lessons.md) and these runbooks first, and adds a lesson to `lessons.md` in the
  same draft PR as its fix. Lessons go in only through PRs.
