# Catch-up packages: "here's what you missed, then live"

Flag `park.catchup.enabled`, **off**. Replay-proven with fake sticks only; nothing here has run on the TV.

Nick, Nov 11: "the last time we checked in on this Browns game ... so much has happened in between that, so we're going
to give you like 4 or 5 plays back to back, and then we'll go back and join live." Phase 2 plan, 2C item 3.

## What the move does

When the PARK planner has parked the hidden stick (H) on a game it is about to return to, and the viewer has not seen
that game for `min_gap_s` (120 s), the show may run a catch-up instead of a plain jump:

1. **Prep (hidden).** H is sent back to the first clip's start and paused there ("queue it to the snap"). The screen
   keeps its game. Started only when the feed already shows a safe leave once H is ready.
2. **Leave.** At a padded dead ball of the screen's game (the jump guard's own leave test, `park_facts.refusal`), never
   with a big play or high leverage in the feed's next 30 s, never in crunch time: the TV flips to H.
3. **Clips.** H plays each clip; between two it is re-cued to the next start (on screen). Meanwhile the other stick (V)
   is tuned to the game live and parked on a padded dead ball (forward, or sent back to one that has just gone by).
4. **Live join.** When the clips end (and the replay is at its own dead ball), the hard guard checks the landing again
   (padded dead ball, lateness cap), then V is resumed and the TV flips to it. Rule #1 and the frozen grader judge this
   join like any jump. The replay is not a live picture, so leaving it is not a Rule #1 leave.

For the judge and the frozen grader a catch-up is **one cut**: the leave of the live game and the live join.

## Which plays

`redzone/noggin/catchup_pack.py` (pure, unit-tested): plays whose result came after the last second the viewer saw
(`seen.missed_since(game)`), worth = graded value (value2) + 2 x the grader's must-see weight + leverage x (lead change
1, tie 0.5, the score that makes an 8-0 run 1). The most worth first, at most 5 plays, at most 120 s on screen (Prime
Rapid Recap's cap), shown in story order. Close plays play straight through (no re-seek), up to 30 s.

Each clip starts at the play's start less 2 s and ends 3 s after the result: football at the snap (where the grader's
dead time ends), basketball at the row before the score (the rebound, the steal, the inbound), at most a shot clock back.

**Never 3 minutes late**: every clip airs less than `catchup.HARD_CAP_S` (180 s) after it first aired (Nick, Nov 9).
Showing older plays is a what-if only (`max_age_s`), and Nick's call.

## Sources, in Nick's order

| Source | When it can show a play | Status |
|---|---|---|
| (a) `rewind`: the hidden stick sent back behind live | YouTube TV; the clip's start within reach | Seek timings measured (Nov 9); this PR models it |
| (b) `reel`: YouTube TV's Key plays reel | YouTube TV; the play is in the reel (reel lag) | Driver in PR 382 (not merged); off |
| (c) `moments`: Prime Key Moments | Prime; the play is in the list (lag) | No driver, no measurement; off |

**How the hidden stick's start limits (a).** The seek is measured to 175 s behind live, 25 s after a fresh tune (Nov 9,
5 tries, one channel). Past that, the outside research says YouTube TV rewinds only to where the stick started watching
the channel, unless the game is recorded to the library. So a stick tuned to the game a minute before reaches only the
measured 175 s; a stick that has sat on the game for 10 minutes could (unproven) reach 10 minutes; a recorded game
(Nick's open call #3) reaches the whole game. `rewind_rule`: `measured` (default), `watch` (also back to the stick's
own tune), `library` (whole game). The planner tunes the hidden stick shortly before each return, so in practice
`watch` adds little: the library is what would open older plays.

## Unknowns (to measure on the real TV)

| Unknown | Value used | Where it comes from |
|---|---|---|
| Rewind reach on YouTube TV | 175 s behind live | measured, Nov 9 (5 seeks, one channel); deeper unproven |
| Rewind reach past the stick's own tune, without the library | not used (rule `measured`) | outside research only |
| Seek prep | 12 s + 0.079 s per second back | measured fit, 30-180 s back |
| Re-seek between two clips, on screen | 8 s | prior, never timed |
| Key plays reel lag | 720 s | one read, Nov 7 (PR 382) |
| Key plays reel open time | 20 s | prior |
| Key plays per play | 10 s | measured (overseer, Nov 6-7) |
| Which YouTube TV channels show reel rows | none assumed | Nov 7: ESPN unavailable, Big Ten spoiler mode, ACC rows |
| Prime Key Moments: lag, open, per play | 60 s, 20 s, 12 s | priors; no driver |
| Prime rewind | none | docs/park.md: Prime has no rewind |

## Keys (`park.catchup.*`, read with code defaults; not yet in `default_config.yaml`)

`enabled` (false), `min_gap_s` 120, `cool_s` 300, `leave_wait_s` 20, `join_wait_s` 90, `replay_dead_s` 12,
`look_ahead_s` 30, `plan_wait_s` 10, and the package's own (`catchup_pack.PackParams`): `max_plays` 5, `cap_s` 120,
`min_worth` 0.3, `min_value` 0.8, `lead_s` 2, `tail_s` 3, `max_age_s` 180, `rewind_rule` measured, `reach_s` 175,
`reel` false, `moments` false, and the source timings above. The YAML block waits for the PARK-UNIFY and
PARK-TIMING-CONFIG claims on `redzone/default_config.yaml` to close.

## Seams

- **Seen-state** (branch `seen-director`): `park_catchup.SeenLike.missed_since(game) -> grid second | None`.
  `CatchupShow(seen=...)` takes the real tracker; the stub `ShowSeen` answers from the show's own `seen_to`.
- **Director v1**: `CatchupShow.catchup_offer(t, game) -> (Package | None, why)` asks what a catch-up would play now,
  without starting it.
- **Device**: one rig step, `cu_cue(k, game, frame, source, visible) -> (ok, seconds)`. For the real sticks: rewind =
  the scrub seek with its label read and the pause before the snap (`replay.yttv_seek: scrub`, `yttv_seek.enter_buffer`);
  reel = PR 382's `yttv_keyplays.catch_up(stick, Want(period, clock_s))`; moments = nothing yet. The app's
  `ControllerRig` has no `cu_cue`, so the move never starts in the app (fail closed), and `park_run.py` does not build
  `CatchupShow` yet.

## Replay

```sh
nice -n 15 .venv/bin/python scripts/catchup_replay.py --night 20261028 --games 4                    # proven numbers
nice -n 15 .venv/bin/python scripts/catchup_replay.py --night 20261028 --games 4 --scenario library --lines 30
nice -n 15 .venv/bin/python scripts/catchup_replay.py --table                                       # every case below
```

It wraps `scripts/park_replay.py` (not edited) and swaps in `CatchupShow`, a fake rig with `cu_cue` and a judge that
scores a catch-up as one cut. Scenarios: `off`; `proven` (175 s rewind, the 3-minute rule, no reel, no Key Moments);
`library` (what-if: whole-game rewind, recaps up to 15 min old); `reel` (what-if: proven rewind plus the reel at its
measured 12-minute lag and Prime Key Moments at priors, recaps up to 15 min old).

RESULTS

## What is NOT proven

- Nothing has run on the TV. Every source timing past the Nov 9 seek test is a prior.
- The re-seek between clips shows the scrub overlay on screen; how that looks has not been seen.
- A catch-up takes the screen for about a minute: in replay it costs some live jumps (see jumps a minute). Whether a
  viewer prefers the recap is Nick's call; the Game Picker app could measure it.
