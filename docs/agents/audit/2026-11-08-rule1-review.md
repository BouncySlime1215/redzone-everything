---
id: P1-REVIEW
title: Independent Rule 1 review of the P1 merge (entry, switch path, TV)
date: 2026-11-08
stream: audit
author: fresh Rule #1 reviewer
status: draft
supersedes: []
sources: 0
verified: 0
affects: [redzone/engine.py, redzone/control.py, redzone/decider/decider.py, redzone/decider/buffered.py, redzone/default_config.yaml, tests/test_landing_bars.py, tests/test_decider.py]
gate: [P1-REVIEW-01, P1-REVIEW-02, P1-REVIEW-03, P1-REVIEW-04, P1-REVIEW-05, P1-REVIEW-06]
---

## Summary

Five S1 and one S2 on `git diff 399dd52d..HEAD` (scope: decider, engine, control, default config, tv).
Three S1 are ways to join mid-play that the diff's own claims say are closed (01, 02, 03). One is a restart guard that nothing calls (04).
One leaves the TV on a finished game with no end (05).
No merge damage that breaks Rule 1: the fallback ladder, session guard, edge probe and EXP-610 are still reachable and consistent, apart from S3 item 08.
No new `except: pass`. Evidence is code plus throwaway unit runs against this worktree; nothing was run on a TV.

### What the diff changes (from the diff alone)

- Entries: every go-in-live exit sits behind `decider.entry.allow_live_fallback` (off); no play-by-play now waits; MLB and 'start' are timed.
- Landing bars: basketball may land in the walk after a made shot or defensive rebound; football prefers landing after the replay.
- Switch path: snap-parked standby is flippable and rolled before the flip; taps wait unless tapped twice; replays are refused on one stick; jump veto reads current scores.
- Rotation: holds and priors for your team and league order; neutral crunch games release at breaks; a restart hold (P-04) is added.
- TV: SmartThings off by policy, local key first, definite non-delivery falls through to the next method, shared-socket lock.

## Findings

### P1-REVIEW-01 (S1): whistle-timed leagues are never re-planned at the flip

- File: `redzone/engine.py:2031`. Introduced here: no, but D-02 adds MLB to it and D-01 claims "every league".
- Code: `if d.action == "switch" and d.kind != "user" and self.decider._cut_timed(d.target, cands1):`
- Failure: `_cut_timed` is NFL/CFB only (`decider.entry.leagues: [nfl]`). NBA, CBB, NHL and MLB entries are timed by `_entry_only` at decide time. The flip can come up to 25 s later (`MAX_SAFE_WAIT_S`) plus any hidden tune. Nothing re-checks the landing, except EXP-610 on the basketball scrub path. The picture comes up mid-play.
- Verified: NBA target, `entry_plan().ok is False`, `_cut_timed False`, `_entry_only True`, `Engine._entry_valid` returned True.
- Fix: `if ... and (self.decider._cut_timed(d.target, cands1) or self.decider._entry_only(cands1.get(d.target), d)):`.
- Test: NBA 'better' switch, plan not ok at flip time: `_entry_valid` is False. Same for NHL and MLB.
- WORTH FIXING: yes.

### P1-REVIEW-02 (S1): a single tap onto Prime joins without a landing check

- File: `redzone/engine.py:2644` with `redzone/default_config.yaml:301`; also `engine.py:2662`. Introduced here: scope is older, but N4/S-4 now promise otherwise.
- Code: `if scope and not any(str(target or "").startswith(f"{p}:") for p in scope):` then `return True`. Default `user_timed_targets: [youtubetv]`.
- Failure: comment at `engine.py:1908` says "every tap waits for a dead ball". That holds for leaving only. One tap to a Prime game passes `_tap_lands_dead` without any plan and joins mid-play. `plan is None` (no play-by-play) also returns True. The phone still says "at the next dead ball".
- Verified: `Engine._tap_lands_dead(.., "prime:b")` returned True with `entry_plan` rigged to raise.
- Fix: default `user_timed_targets: []` (needs a line in decisions.md, it overrides the Nov 5 scope). `plan is None` returns False for a live game with a target; the second tap still goes.
- Test: Prime single tap with a mid-play plan is False, second tap True; no-data single tap is False.
- WORTH FIXING: yes.

### P1-REVIEW-03 (S1): the default entry rewind runs on screen, after the flip

- File: `redzone/control.py:2684`, `redzone/default_config.yaml:283`. Introduced here: no.
- Code: `elif entry_rewind_s and out.ok and not out.dry_run and not replay_back_s and self.main is not None:` with `cue: seek`.
- Failure: a plan only needs a rewind because the live-edge frame is not a dead ball. With the default cue the TV flips to that live-edge frame, then rewinds in front of Nick: a mid-play glimpse, whole-step presses and a 2-4 s stall. The landing check judges only the post-rewind frame. S-11 closed this for replays only. A tap arms the same path after 15 s (`user_rewind_after_s`), and the transition divert to the prior walk uses it too.
- Verified: default config, CFB target: `_time_entry` returned a switch with `rewind_s=63.0`, `cue=''`; `Controller.execute`'s docstring states the visible stick then rewinds.
- Fix: default `cue: hidden`. In `Controller.execute`, refuse a post-flip rewind on the visible stick (same shape as S-11); no two-stick path means wait.
- Test: entry rewind with `_hidden_entry_done` False sends no key to `self.main` and returns not ok with the screen unchanged.
- WORTH FIXING: yes.

### P1-REVIEW-04 (S1): the restart hold (P-04) is never wired in

- File: `redzone/decider/decider.py:720` (no caller), `:3008`. New in this diff.
- Code: `def note_restart(self, gid: str | None, at: float, high_leverage: bool = False) -> None:`
- Failure: `grep -rn note_restart redzone` finds only the definition; only `tests/test_landing_bars.py` calls it. `_restored_screen` stays None, so `_restart_hold` is always False. After a restart in a close, late game, `current` is None, `safe_to_leave(None)` and `jump_may_leave(None)` are True, and a 'start' or free cut leaves the moment before `_relearn_screen` runs. The saved state does not hold the screen game either (`userstate.capture` has no such field). Even when wired, the hold is only reached on `why == "screen_gone"`, which needs buffered mode.
- Fix: save (screen game, wall time, high leverage) with the user state each tick; call `decider.note_restart` from `_restore_user_state`; apply the hold to 'start' with jump mode off too.
- Test: Engine-level: save state with a high-leverage game, build a new Engine, assert `decide` stays until `adopt` or `restart_hold_s`.
- WORTH FIXING: yes.

### P1-REVIEW-05 (S1): an entry with no proven landing waits forever, on a finished game

- File: `redzone/decider/decider.py:1392-1398` and the wait at the end of `_time_entry`. New in this diff (D-01 removed the only cap).
- Code: `return Decision(now, "stay", self.current, f"no play-by-play to time the landing: {d.reason}", state,`
- Failure: the top-ranked game has no marks (small ESPN game, id mismatch) or its feed went dark. The switch stays, with no timer and no second choice. `want` stays on that game, so other games are never tried.
- Verified: kind 'ended' stayed for 4 hours of clock in both the no-marks and dark-feed cases. Full `decide()` with the screen game gone, top game without play-by-play and a lower game with a logged dead window: stayed on the finished game each time, `want='b'`.
- Fix: not a go-live cap (Rule 1 stands). After `decider.entry.no_data_wait_s` (suggest 45 s) drop that target for this pick, so `_pick` takes the next game with a provable landing. If none has one, send one phone toast naming the wait. Nick decides whether a last resort exists.
- Test: the `decide()` case above picks 'c' after the wait; a single-candidate case stays and toasts once.
- WORTH FIXING: yes.

### P1-REVIEW-06 (S2): S-2 reads fresh scores but not the hold

- File: `redzone/engine.py:1928`. New in this diff.
- Code: `else "leave" if not self.decider.jump_may_leave(before, live, now, self._leave_margin()) else "")`
- Failure: the fresh scores reach only the gate. `_jump_hold` (crunch, your team late, red zone drive) runs at decide time. If the game turns high leverage during the 25 s wait, the dead-ball branch of `_jump_gate` lets the jump go. The S-2 comment names exactly this case.
- Verified: crunch candidate at a dead ball: `_jump_hold` returned "crunch", `jump_may_leave` returned True.
- Fix: in `may_cut`, veto "hold" when `self.decider._jump_hold(live[before])` is set.
- Test: jump pending, screen game flips to crunch between polls: veto is "hold".
- WORTH FIXING: yes.

### P1-REVIEW-07 (S3): a park inside a walk only lands if PAUSE is exactly on time

- File: `redzone/decider/buffered.py:161`. `pause_at = w.end - flip - m`
- Failure: x1 equals `w.end` with zero slack, so any key latency fails `lands`. The park then sits until it expires, and parked mode has no other cut. Safe direction: throughput only.
- Verified: late 0.0 s lands, 0.05 s and 0.3 s do not.
- Fix: subtract `park_guard_s`. Test: 0.3 s late still lands.
- WORTH FIXING: yes, low priority.

### P1-REVIEW-08 (S3): EXP-610 refuses the walk landing it was planned for

- File: `redzone/engine.py:2006`. `return whistle.clock_stopped(sorted(tl.clock_events), a,`
- Failure: non-jump kinds in jump mode on the scrub path can plan a walk landing, but this gate needs a stopped clock and the clock usually runs after a made shot. The switch burns 25 s and ends "no safe moment". Safe direction. By reading only.
- Fix: pass when the delayed picture is inside `entry_windows(walk)`. Test: scrub path, made shot, picture in the walk: True.
- WORTH FIXING: no, unless walks on the scrub path are wanted.

### P1-REVIEW-09 (S3): old decider tests now run off the shipped default

- File: `tests/test_decider.py` (two tests), `tests/test_buffer_shadow.py`. `dec.cfg.data["decider"]["entry"]["allow_live_fallback"] = True`
- Failure: these pass with the fallback on, which ships off. `test_d01_no_play_by_play_yet_waits` checks one tick and never asks how the wait ends, which is how 05 got through.
- Fix: give those games marks and keep the flag off. Test: the 05 test.
- WORTH FIXING: yes, with 05.

## What to change in this repo

1. 01: one condition in `_entry_valid` (about 15 minutes with test).
2. 06: one veto in `may_cut` (about 15 minutes).
3. 02 and 03: two config defaults, decisions.md lines, one refusal in `execute` (about 45 minutes).
4. 04: persist and wire the restart state (about 45 minutes).
5. 05 and 09: needs Nick's call on the last resort (about 1 hour).

## Risks / unverified

- Nothing ran on a TV or a stick. 03's on-screen effect is from the code path and its docstring.
- Checked and not reported: S-1's `rolled` shortcut (fails safe: any `input` key clears `tuned`, so a stick still paused reads empty and SAFE-08 refuses), `_open_walk` (its `_covered` subtracts feed lag), D-05 leave, tv.py definite-delivery fallthrough, edge probe on a parked stick (guarded by `held`).
- 03 and 05 interact with Nick's rules: whether an entry rewind counts as "replay" and whether any last resort exists are his to rule on.

## Bibliography

None: code review of this repository only.
