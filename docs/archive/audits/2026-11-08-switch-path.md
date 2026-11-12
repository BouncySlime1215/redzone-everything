---
id: A-SWITCH
title: "Switch path review, 2026-11-08"
date: 2026-11-08
stream: audit
author: "unrecorded (2026-11-08 audit agent)"
status: reviewed
supersedes: []
sources: 0
verified: 0
affects: ["redzone/engine.py", "redzone/control.py"]
gate: []
---

# Switch path review, 2026-11-08

Scope: `redzone/engine.py` (decide loop, `_act`, `may_cut`, `_entry_valid`, `safe_to_leave`, `_preload_standby`,
`_buffered_park`, `_relearn_screen`, pending switch, user actions) and how it drives `redzone/control.py`
(`submit` / `_run_locked` / `execute` / `_execute_two` / `_flip_to`, standby, holds, snap park, scrub).
Method: read the code, quote it, and reproduce S-1 with a throwaway script (nothing written to the repo).
Not run: the full suite, the app. Line numbers are in the worktree `wthot`.

## Findings

### S-1 (ERROR, S2) A snap-parked standby is reported ready, then every jump to it is refused

- engine.py `_buffered_park` calls `ctl.snap_park(...)`; control.py:3749 `_do_snap_park` PAUSEs the hidden stick
  and stores `self._snap_park = Parked("snap", ...)`. The stick stays `hidden_ready` with `parked is None`, so
  control.py:1626 `_live_on_hidden()` and 1633 `standby_ready()` stay True. The decider reads that as "ready" and
  plans the cut (`decider.py:2617 snap_parked`, `bj.frozen`).
- control.py `_execute_two` path selection:
  ```
  elif not replay and self._live_on_hidden(target) and await self._steady_playing(hid, target) is not None:
      path = "preloaded"
  elif getattr(self, "_preloaded_only", False):
      return Outcome(False, ..., STANDBY_NOT_READY + " (stopped playing)", ...)
  ```
  A paused stick is never "steady playing" (`_note_steady` -> `still_on` needs `state == "playing"`;
  `_steady_playing` returns None on `paused`). The only place that PLAYs a snap park is `_snap_roll`, at control.py:2857,
  after both branches above. For a jump (`preloaded_only=True`) the roll is unreachable.
- Reproduced (FakeStick harness from tests/test_buffered_jump.py): snap_park -> `snap_parked` True, `standby_ready`
  True -> `execute(target, may_cut=lambda: True)` with `_preloaded_only=True` returns
  `ok=False path=refused "standby not ready (stopped playing)"`. A non-jump switch to a parked stick falls to the
  "tuned" branch and re-tunes the hidden stick, throwing the buffer away (~30 s).
- Why it matters: `buffered_jump.park` defaults True. Park mode is "cut at every dead ball to the paused standby" and
  every such cut is refused, so the editor loses its throughput and the screen sits on a game the decider wanted to
  leave. No Rule #1 break (screen untouched, `screen_unchanged=True`), but dead time. The only test of the roll
  (`test_control_parks_rolls_and_counts_the_pause`) calls `_snap_roll` directly, never through `execute`.
- Fix: in `_execute_two`, before the steady check, treat `self._snap_park` for this target on a `_live_on_hidden`
  stick as `path = "preloaded"` and skip `_steady_playing`. The roll, the re-read and the `still_on` check at
  2862-2885 already verify it.
- Related timing (check after the fix): PLAY is sent in `_snap_roll`, then the flip code re-reads playback and, if the
  state just changed, waits `FLIP_STEADY_S` (1 s) in `_steady_playing` before flipping. PLAY-to-flip is then about 2-3 s,
  while `bj.park_lead_s` / `bj.lands` treat the picture as frozen until the flip (`bj.frozen`). The snap would start on
  the hidden stick before the TV shows it. Skip the steady wait right after a roll, or add the measured roll time to
  `park_lead_s`. A read of "paused" straight after PLAY would also trip SAFE-08 (`still_on` needs `playing`).
- Test: in tests/test_buffered_jump.py: park, `execute` as a jump, expect `ok`, `KEYCODE_MEDIA_PLAY` sent, `held`
  increased by the pause, and a flip.

### S-2 (ERROR, S2, can reach S1) A jump's `may_cut` judges the leave on a stale `cands` snapshot

- engine.py `_act` -> `may_cut` (jump branch) calls `self.decider.jump_may_leave(before, cands, ...)` and
  `jump_lands_dead(d.target, cands.get(d.target), ...)`. `cands` is the dict built in `decide()` at the tick that made
  the decision, captured by the closure. `control._wait_safe_loop` re-polls `may_cut` every 0.2 s for up to
  `MAX_SAFE_WAIT_S` = 25 s.
- `jump_may_leave` -> `_jump_gate(x, ...)` reads the stale `x.scored`: `cur.scored.is_break` (decider.py:2556), and
  `_gate_at` -> `_leavable(cur)` reads `s.high_leverage`, `s.crunch`, `s.lock`, `s.imminent`, `s.pre_moment`,
  `s.closeness`, `s.personal` (decider.py:3156-3181). Those are the "never leave a high-leverage moment" inputs, frozen
  at decision time. `_entry_valid` (non-jump) does it right: it rebuilds `Candidate(self.scored[d.target], target)` from
  live state.
- Failure: a game that was low leverage at decision time (so the low-leverage mid-play leave exception applied)
  becomes a red-zone / power-play / close-and-late moment during the wait. The gate still says "low-leverage moment:
  leaving mid-play is OK" and the flip goes. `_high_lev_at` is live, which covers only part of it. Buffered mode blocks the
  low-leverage exception (`strict`), so exposure is non-buffered jump mode with `decider.leave.low_leverage` non-empty
  (default `[]`, so it depends on Nick's runtime config), plus the stale `is_break` case in both modes.
- Fix: in `may_cut`, rebuild the two `Candidate`s on every call from `self.scored` / `self.where` (as `_entry_valid`
  does) and pass those.
- Test: submit a jump with `scored[before]` low leverage and a mid-play timeline so `may_cut` is True via the exception;
  then set `scored[before].lock = True` and assert `may_cut()` is False.

### S-3 (CONFLICT, S2) Jump leave margin is a fixed 3.2 s; the measured flip is longer on some paths

- engine.py jump veto: `jump_may_leave(before, cands, now, float(self.cfg.get("jump_mode.flip_s", 3.2)))`.
  default_config.yaml:404 `flip_s: 3.2 # a leave's dead window must outlast it`; the buffered block says
  `flip_s: 3.2 # slowest input flip (live tonight 2.5-3.7 s)`.
- The figure is below its own evidence: a 3.7 s live flip is noted in that comment; control.py:1707 documents a HOME
  flip as "2-6 s" plus `input_settle_s` (1.0); `control._flip_upper` (control.py:1747) holds the measured bound but
  the leave path never uses it. The landing side is right (`max(flip_s, cost)`, decider.py:2623, `cost` =
  `deadline_cost`); only the leave side is under-margined.
- Why it matters: the old game keeps playing until the TV input changes. A 3.7 s flip against a 3.2 s window can cut
  the end of a play, so Rule #1 on the leave side holds only on the fast TV path.
- Fix: leave margin = `max(cfg flip_s, control._flip_upper())` (make it public).
- Test: controller with `flip_path()` = home_flip and 5 s samples; a 4 s leave window must be refused.

### S-4 (ERROR, S2) A manual `back`, `focus` or `replay` tap cuts out of the screen game at once, mid-play

- engine.py user branch of `may_cut`:
  ```
  return (not self._hold_waits(d) or self.safe_to_leave(before, required_s=8.0)) \
      and self._tap_lands_dead(d, target)
  ```
  `_hold_waits(d)` is True only for the held game and not forced. `_hold` is the only action that sets a hold.
  `action("back")`, `action("focus")` (when not already on it) and `action("replay")` call `_act_manual` with no hold, so
  `_hold_waits` is False, `not False` short-circuits, and `safe_to_leave(before)` is never asked. Only the landing
  (`_tap_lands_dead`) is checked, and for `action == "replay"` that returns True at once (`d.action != "switch"`).
- Why it matters: "Watch this" is documented as never mid-play (`_hold` docstring); the North Star says never leave a
  high-leverage moment and no manual forces. These three taps bypass that with no confirm tap. If intended ("a tap is
  a force"), record it in docs/memory/decisions.md; today it contradicts the `_hold` contract.
- Fix: for every user switch, require `safe_to_leave(before, 8.0)` unless `_forced_go(d)`.
- Test: tap `back` with `safe_to_leave` patched False (as tests/test_phone_control.py does for hold); expect it to wait.

### S-5 (ERROR, S3 now, S2 if `reel.play` is turned on) `safe_to_leave` cannot leave the catch-up reel

- engine.py `safe_to_leave`: `if not gid or gid == REDZONE: return True`, then
  `if gid not in self.decider.timelines: return False`. `REEL` is a pseudo game (`PSEUDO = (REDZONE, REEL)`, "no
  timeline"), and the decider's own gate treats it as free to leave (`_gate_at`: "on the catch-up reel: nothing live to
  cut"). So the decider decides to leave the reel, `may_cut` waits the full 25 s, the switch fails "no safe moment",
  is rejected with backoff, repeat.
- Latent today: `reel.play` defaults False (`_reel_target`).
- Fix: `if not gid or gid in (REDZONE, REEL): return True`.
- Test: `safe_to_leave(REEL)` is True; a reel -> live switch completes without waiting.

### S-6 (ERROR, S2) A restart restores held offsets but not `behind_on`, so the next same-channel tune claims the live edge

- control.py:2058 `restore_held` re-creates `held[target]` (commit fd6c5d6a), but `Stick.behind_on` (adb.py:124; set at
  control.py:2250 and 3932 when keys moved a stick behind live) is in memory only. `_tune` (control.py:1995) goes
  through "another channel first" only when `behind_on == target`. After a restart it is None, so a deep link to the
  same channel does not reset the stick, and control.py:2012 then runs
  `self._set_held(target, 0.0)  # a fresh tune plays from the live edge`.
- Result: a stick still ~75 s behind live is booked as live edge, the delay book is off by that much, and every entry
  and leave judged on that picture can land mid-play (the same hazard as the "Nick's 90 s run, Nov 7" comment).
- Fix: when `restore_held` restores a target, also set `behind_on` on the stick that holds it, or persist
  `{host: target}` next to `held` in held.json.
- Test: scrub a stick, save, build a new Controller, `restore_held`, tune the same target, assert the reset-through-other runs.

### S-7 (GAP, S3) State that does not survive a restart

- `Engine.boosted` (engine.py:284, set by `action("boost")`) is not in `userstate.capture`: a boost vanishes on restart.
  `manual_until`, `_hand_left`, `decider.stack` and `decider.queue` are also not saved; those are rebuilt, so only
  `boosted` is a user decision lost.
- held.json is written with `Path.write_text` (control.py:2054), not tmp + replace as `userstate.save` does. A crash
  mid-write leaves invalid JSON; `restore_held` returns `{}` (safe), so the cost is only the offsets (and S-6 gets worse).
- Test: capture/apply round trip for `boosted`.

### S-8 (ERROR, S3) A superseded switch's flip can still land and is never booked

- control.py `submit`: a command that arrives while `_flipping` is True does not cancel the running task. The engine has
  already resolved the old decision with `SUPERSEDED` (engine.py:1742-1748), so the old outcome is dropped
  (`self._pending_decision is not d` in `done`). `screen_m` is "flipping" from `_screen_to("flipping")` (before the
  readbacks, so 0.5-3 s) until the flip returns, wider than the "~1 s" in the comment.
- Effect: the old switch still flips (its `may_cut` had passed), `decider.confirm` is never called for it, then the
  new switch runs. The end state is right, but there are two flips in a row and the middle one is never booked in the
  decider (screen log, `current`).
- Fix: note it in lessons, or confirm the old outcome when `out.ok` arrives for an already-superseded decision.

### S-9 (ERROR, S3) `_flip_to` can send a HOME flip after the safe window closed when the TV command was ambiguous

- control.py `_flip_to`: after `tv.set_input` fails, the exit check is skipped when `ev.accepted is None`
  (`... and not (ev and ev.accepted is None)`) and `_home_flip_cec` runs. If the TV command was really lost, nothing had
  switched and the HOME flip now cuts after the window closed. The comment assumes the first command landed.
- `cmd hdmi_control onetouchplay` (after a failed HOME flip) has no `_exit_check`, unlike the later `tv.set_input`
  fallback. Fire OS rejects that command (`_cec_bad`), so it is mostly dead.
- Fix: read `tv.input_status()` once before the HOME flip in the ambiguous case; add `_exit_check` before onetouchplay.

### S-10 (ERROR, S3) No exception boundary after the flip

- control.py `_execute_two`: after `flipped = await self._flip_to(hid)` the bookkeeping (`_holds_left`, `_book_flip`,
  `_set_held`, resume) runs outside any try except the two resume blocks. An exception there reaches `_run_locked`'s
  `except Exception`: the switch is reported `crashed: ...`, `ok=False`, though the TV input changed. The engine then
  runs `decider.reject`, `lock_failed`, `note_error` and a "Couldn't switch" toast, and `decider.current` stays on the old
  game while `control.active` / `on_target` already say the new one. `_relearn_screen` only runs when `current` is None.
- Fix: wrap the post-flip block and still return `Outcome(True, ..., "bookkeeping failed: ...")`.

### S-11 (ERROR, S3) A replay can be rewound on the visible stick on two paths

- control.py `_execute_two` returns None (falls to one-stick) when "the hidden stick can't play this app but the visible one
  can", replays included. `_execute_one` refuses a YouTube TV replay (`_replay_scrubs`, good), but any other app
  (`replay.apps` defaults to `["youtubetv"]`, yet `action("replay")` never checks it) is tuned and MEDIA_REWOUND on the
  visible stick: the viewer sees the live edge, then the seek. `two_stick` off does the same.
- Fix: refuse a replay in `_execute_one` for every app ("replay needs the hidden stick"), or check `replay.apps` in `action("replay")`.

### S-12 (cleanup, S3)

- engine.py `if action == "watch":` (after `back`) is unreachable: `action in ("hold", "watch")` returns earlier.
- `_act` reads `self.merger.games[d.target].league` for a replay (engine.py:1726). A KeyError there fires before
  `_pending_decision` is set; `_decide_loop` catches it and calls `note_error()` each tick (2 Hz, the decision is re-made
  every tick), so three of them put the TV in safe mode. Use `.get` with the 8 s default. (I found no code that drops games
  from `merger.games`, so this is defensive.)
- The 25 s `MAX_SAFE_WAIT_S` is paid even when the answer is already known to be no (S-5).

## What's solid

- Rule #1 at the flip is checked three times: `_wait_safe` (0.2 s poll), a re-check after the readbacks ("safe exit window
  closed during preparation"), and `_flip_to` before each mutating command.
- `submit` never cancels a flip (`_flipping`), and `done()` is idempotent (`resolved`, `_pending_decision is not d`).
  Supersede resolves the old decision with a store row and no error (engine.py:1742).
- A hung switch cannot hold the TV forever: `_switch_deadline` gives up at `control.max_switch_s` (75 s for a first pick),
  and `pending_age` removes wall-clock jumps.
- Exceptions in `_decide_loop`, `_run_locked`, provenance, trace and the `_jump_standby` refill are caught and logged; a
  bad phone view or trace never costs the switch its row.
- `_entry_valid` refreshes phase, feed freshness, route, catchability and the planned rewind at the moment of the cut
  from live state (the pattern S-2 should copy).
- `_wait_safe` runs a stalled-player repair every 4 s while it holds the lock, so a wait does not freeze the screen behind a prompt.
- Failure outcomes carry `screen_unchanged`, so a refused or timed-out switch does not count toward safe mode; neither do
  "TV input is unknown" and a superseded switch (engine.py `done`).
- A YouTube TV replay is never rewound on screen (`_execute_one` refuses), and a replay that cannot be parked is refused
  before any key.
- Restart: pause, hold, lock, focus, mutes, skips (with expiry) and suspended focus are saved atomically and restored;
  stale game choices drop after 6 h while a pause is kept; `_relearn_screen` re-checks pending and `current` after its await.
- Snap park, rescrub and preload take `_lock` and re-check `ours()` / `still_hidden()` before every key.

## Suggested order

1. S-1 (park mode never completes a jump) and S-2 (stale gate): small and testable. 2. S-3, S-4, S-6. 3. The rest.
