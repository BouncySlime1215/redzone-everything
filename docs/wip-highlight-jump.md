# WIP: highlight jump (jump to the catch-up reel vs stay). NOT ready to merge.

Branch claude/highlight-jump, based on integration/next dbb33cc (before the contracts landed).

Done
- redzone/reel.py: pure `jump_value(StayView, ReelOffer, JumpWindow, JumpPolicy) -> ReelJump`
  (hard stays: crunch, your team late, must-see, key moment >= 1.5x league rate; reel cut to fit
  the dead ball's cautious length minus both switches and a margin, 5+ clips / 30 s minimum;
  EV vs staying and vs the best live visit), `fit_reel`, `clip_value`, `reel_rate`.
- decider.py: `_reel_jump` builds the inputs (one function for every reel path, replaces
  `_reel_fits`), `_p10_left` (fitted break-length p10), `_plan_reel_return` (home at the restart,
  queued replays of the reel's plays dropped), reel vs single replay by value per second,
  `_home_due` always comes back for the restart from the reel, `reel_trace` hook.
- Backtest: `run(..., reels=True)` + `redzone/lab/reelsim.py` (archive ESPN clips or one per big
  play), reel scorecard lines; `--reels` on scripts/eval.py and eval_sports.py.
- Offline fit: `python -m redzone.lab.reeljump nfl|nba|nhl` (p10 per break kind + margin, 2-fold by night).
- tests/test_reel_jump.py (17 pass). Full suite: see the PR/thread.

Measured (NFL 16 slates, reel.play on vs integration/next baseline)
- 50 reels, 98.2% of reel seconds in dead time, 2009 s of commercials replaced, 0 mid-play entries,
  0 plays missed on entry, 1 home big play missed while on a reel, 37 s home live play missed.
- BUT dead air 8.6 -> 8.7%, big plays seen 95.4 -> 95.1%, big live 29.7 -> 29.1%. Not shippable.
- reel.play stays off by default, so the default scorecards are unchanged by design (to verify).

Known failing: tests/test_backtest.py::test_back_to_live_after_replaying_the_same_game_is_a_switch
(a fake decide() in that test lacks the new reel= keyword; fix the test fake or call decide without it when sim is None).

Next steps
1. Find where the dead air comes back (dead-cause diff: "landed in it (home)" +0.1, "sat through it"
   +0.1): likely the return-for-restart lands in the break tail. Try returning on the reel's
   planned end only, and the restart rule only when the data shows it early.
2. Big plays seen: reel clips credited only when play_at matches the play's wallclock; check the
   rounding/matching, and keep big-play replays ahead of reels when the reel lacks that play.
3. NBA fit (redzone.lab.reeljump nba) didn't finish; NFL folds disagree on margin (60 vs 2).
4. Rebase onto integration/next (contracts landed): expose `reel_jump_value(...) -> dict` and
   Reeler.ready()/played(offer) per redzone/contracts/reel.py; rename local ReelOffer/ReelJump to
   avoid clashing with the contract types; ev units = points per second over the whole window.
5. Then all scorecards + demo, self-review, draft PR into integration/next.
