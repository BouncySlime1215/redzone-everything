# Resume notes (usage pause, 2026-11-02 ~1:30pm ET)

State of `main`: all logic agents merged, 158 tests passing, 0 mid-play cuts in all 4 leagues.

Running when paused (agent workflows; finished agents are cached, in-flight ones restart):
- Wave A — speed (ADB fast path, standby strategy, ESPN Fastcast): run `wf_cde53586-7a8`
- Wave C — autonomy (YouTube TV channel discovery, all providers, self-healing ops): run `wf_ba485a48-6c9`
- Wave B — per-league logic (MLB, NBA/NHL dead air, NFL crunch, cross-league red team): run `wf_556372b1-832`
Their work lives on `worktree-agent-*` branches under `.claude/worktrees/` (not pushed).

On resume:
1. Check each workflow's result; resume unfinished ones with `resumeFromRunId`.
2. Merge order: Wave A → Wave C (wire INTEGRATION hooks) → Wave B. After each: pytest + `scripts/eval.py` (+ stress flags) + `scripts/eval_sports.py nba|nhl` + `mlb --nights 40`. Cuts must stay 0.
3. Phase 0 with the stick (`scripts/doctor.py`), then tonight's live dry run (README "First night checklist").
4. Push `main` to GitHub.
