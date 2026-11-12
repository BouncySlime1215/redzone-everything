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
   with a big play or high leverage in the feed's next 30 s, never in crunch time: the TV flips to H. Football comes
   first: a football game is left only in a break long enough to cover the clips, never between the plays of a drive.
3. **Clips.** H plays each clip; between two it is re-cued to the next start (on screen). Meanwhile the other stick (V)
   is tuned to the game live and parked on a padded dead ball (forward, or sent back to one that has just gone by).
4. **Live join.** When the clips end (and the replay is at its own dead ball), the hard guard checks the landing again
   (padded dead ball, lateness cap), then V is resumed and the TV flips to it. Rule #1 and the frozen grader judge this
   join like any jump. The replay is not a live picture, so leaving it is not a Rule #1 leave. If V has no dead ball
   within `join_wait_s` (30 s), H itself is re-cued on screen to the newest confirmed padded dead ball, inside the
   lateness cap, and plays on from there: that is the live join, judged the same way.

When V is the pinned stream, or holds a Prime game (a Prime tune takes about 100 s and Prime has no rewind), V keeps
its game instead ("h_join"): it is held at the leave as a jump holds the stream it leaves (the pinned stream plays on
unseen), H makes the live join itself by the re-cue above, and V is the hidden stick on the way back. A catch-up never
re-tunes or cues the pinned stream.

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

`enabled` (false), `min_gap_s` 120, `cool_s` 300, `leave_wait_s` 20, `join_wait_s` 30, `replay_dead_s` 12,
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

Results (Nov 11, whole dev nights, seed 0, fake sticks; "chances" = a return to a game unseen 2+ min with its stick
parked there; on-screen seconds include the wait for the live join; big plays = the grader's must-sees, of which a
4-game NBA night has 0-1 missed even with catch-ups off):

| case | scenario | fits / chances | mean s on screen | plays each | sources | missed big plays: via catch-up / never | graded value seen: live + via catch-up | frozen grader: mid-play joins / leaves (catch-up joins) | jumps a min | worst behind live s |
|---|---|---|---|---|---|---|---|---|---|---|
| NBA 20261023, 4 games | off | - | - | - | - | 1 missed: - / 1 | 61% | 0 / 0 | 0.502 | 142.0 |
| NBA 20261023, 4 games | proven | 8 / 46 | 51.2 | 1.88 | rewind 15 | 0 missed: 0 / 0 | 59% + 7% | 0 / 0 (0 of 8) | 0.43 | 136.0 |
| NBA 20261023, 4 games | library | 16 / 50 | 64.6 | 3.06 | rewind 49 | 0 missed: 0 / 0 | 56% + 14% | 0 / 0 (0 of 16) | 0.352 | 281.9 |
| NBA 20261023, 4 games | reel | 11 / 47 | 47.8 | 2.09 | rewind 22, reel 1 | 0 missed: 0 / 0 | 55% + 9% | 0 / 0 (0 of 11) | 0.424 | 126.4 |
| NBA 20261028, 4 games | off | - | - | - | - | 0 missed: - / 0 | 50% | 0 / 0 | 0.522 | 144.0 |
| NBA 20261028, 4 games | proven | 9 / 47 | 59.3 | 2.22 | rewind 20 | 0 missed: 0 / 0 | 51% + 7% | 0 / 0 (0 of 9) | 0.467 | 141.0 |
| NBA 20261028, 4 games | library | 10 / 51 | 88.9 | 3.9 | rewind 39 | 1 missed: 0 / 1 | 39% + 21% | 0 / 0 (0 of 10) | 0.405 | 143.0 |
| NBA 20261028, 4 games | reel | 11 / 47 | 81.6 | 2.64 | rewind 27, reel 2 | 0 missed: 0 / 0 | 39% + 19% | 0 / 0 (0 of 11) | 0.467 | 290.9 |
| NBA 20261021, 4 games | off | - | - | - | - | 0 missed: - / 0 | 44% | 0 / 0 | 0.53 | 140.6 |
| NBA 20261021, 4 games | proven | 8 / 53 | 51.1 | 1.88 | rewind 15 | 0 missed: 0 / 0 | 49% + 8% | 0 / 0 (0 of 8) | 0.435 | 137.6 |
| NBA 20261021, 4 games | library | 9 / 53 | 73.6 | 2.56 | rewind 23 | 0 missed: 0 / 0 | 43% + 11% | 0 / 0 (0 of 9) | 0.416 | 288.8 |
| NBA 20261021, 4 games | reel | 13 / 50 | 55.1 | 2.15 | reel 8, rewind 20 | 0 missed: 0 / 0 | 39% + 12% | 0 / 0 (0 of 13) | 0.385 | 136.1 |
| NFL on Prime + 1 NBA, pinned | off | - | - | - | - | 4 missed: - / 4 | 86% | 0 / 0 | 0.204 | 142.0 |
| NFL on Prime + 1 NBA, pinned | proven | 2 / 30 | 32.5 | 1.0 | rewind 2 | 3 missed: 0 / 3 | 89% + 0% | 0 / 0 (0 of 2) | 0.19 | 139.0 |
| NFL on Prime + 1 NBA, pinned | library | 2 / 26 | 40.0 | 2.5 | rewind 5 | 7 missed: 0 / 7 | 83% + 1% | 0 / 0 (0 of 2) | 0.163 | 143.0 |
| NFL on Prime + 1 NBA, pinned | reel | 2 / 30 | 32.5 | 1.0 | rewind 2 | 3 missed: 0 / 3 | 89% + 0% | 0 / 0 (0 of 2) | 0.19 | 139.0 |
| NFL on Prime + 1 NBA, not pinned | off | - | - | - | - | 4 missed: - / 4 | 93% | 0 / 0 | 0.224 | 140.9 |
| NFL on Prime + 1 NBA, not pinned | proven | 2 / 38 | 58.0 | 1.0 | rewind 2 | 4 missed: 0 / 4 | 86% + 1% | 0 / 0 (0 of 2) | 0.197 | 144.0 |
| NFL on Prime + 1 NBA, not pinned | library | 2 / 38 | 58.0 | 1.0 | rewind 2 | 4 missed: 0 / 4 | 86% + 1% | 0 / 0 (0 of 2) | 0.197 | 144.0 |
| NFL on Prime + 1 NBA, not pinned | reel | 2 / 30 | 107.0 | 1.0 | rewind 1, moments 1 | 4 missed: 1 / 3 | 84% + 1% | 0 / 0 (0 of 2) | 0.19 | 217.3 |

What it says:

- **Rule #1 holds**: 0 mid-play joins and 0 mid-play leaves by the frozen grader in every run; 0 of 107 catch-up joins
  mid-play; every clip starts at a dead ball or at the feed event before its play (0 mid-play starts); no clip 3+ min late.
- **Proven setting** (175 s rewind, 3-minute rule): about 1 return in 6 gets a package, about 2 plays and 50-60 s each
  (prep 17-19 s hidden, join wait about 22 s). Graded value seen goes up 5-8 points net (live drops 0-2, recaps add 7-8);
  jumps a minute drop by about 0.06-0.10, since a catch-up takes a minute of screen. The usual reasons for no package:
  nothing missed worth a clip (81), nothing in reach in time (57), crunch time (16).
- **The rewind is the bottleneck.** The library what-if (whole-game rewind, recaps up to 15 min old) gives 3-4 plays a
  package and +11-21 points of recap value, at a cost in live value. Its worst-behind can pass the 145 s cap: when the
  game has had no padded dead ball for minutes, the live join takes the newest dead ball at all rather than join mid-play.
- **The Key plays reel adds little**: at its one measured 12-minute lag, 1-8 plays a night, and never under the 3-minute rule.
- **NFL + Prime**: the pinned football stick is never cued or re-tuned; the NBA game gets 2 catch-ups a night; football's
  missed must-sees stay 3-4 (Prime has no rewind, so a missed football play cannot be shown; Key Moments, a what-if
  here, showed 1).

## What is NOT proven

- Nothing has run on the TV. Every source timing past the Nov 9 seek test is a prior.
- The re-seek between clips shows the scrub overlay on screen; how that looks has not been seen.
- A catch-up takes the screen for about a minute: in replay it costs some live jumps (see jumps a minute). Whether a
  viewer prefers the recap is Nick's call; the Game Picker app could measure it.
