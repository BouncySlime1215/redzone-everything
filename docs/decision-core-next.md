# Decision core: where it stands (paused 2026-11-03 17:10Z for the usage reset)

Branch `claude/decision-core`, on top of integration/next d0c775a.

Done
- `redzone/decider/core.py`: the views (MomentView, BreakView, SwitchPath, SyncView, EntryView, ReelView,
  Priority), Option/CoreState/Action, the one `ev()` function, guards, `Knobs` (decider.* + core.* config).
- decider.py routes `_best`, the better-game margin, go-back, break-visit worth + ranking (trip budget's
  score x watch seconds is now `core.window_value`), the halftime pick and the reel fit through the core.
- Scorecards are bit-identical to integration/next (NFL, NBA, NHL, Nov 1 demo). Gotcha found: a "visit" to
  the game on screen must still be scored against the commercial (chained visits list it).

Next (in order)
1. TOP: NFL commercial time on screen -> 0 (Nick, 17:08Z). Baseline 8.6% on eval-data-v1. Use the
   break forecasts (BreakView: remaining p10, break_soon_p) so visits never land in a break, leave right
   after a scoring play airs, and fill all-games-in-commercial stretches with replays. No mid-play rule
   still holds. Knobs to try: core.dead_air_penalty, core.break_risk, core.break_value_frac.
2. Move hard rules into guards (crunch, your team late, garbage time, user pause/lock), delete duplicates.
3. Unit tests for core.py (tests/test_core.py), then the auto-tune SPACE on the core.* knobs.
4. Draft PR into integration/next with the before/after table.
