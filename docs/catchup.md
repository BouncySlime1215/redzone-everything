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
(`SeenLike.last_seen(game)`), worth = graded value (value2) + 2 x the grader's must-see weight + leverage x (lead change
1, tie 0.5, the score that makes an 8-0 run 1). The most worth first, at most 5 plays, at most 120 s on screen (Prime
Rapid Recap's cap), shown in story order. Close plays play straight through (no re-seek), up to 30 s.

Each clip starts at the play's start less 2 s and ends 3 s after the result: football at the snap (where the grader's
dead time ends), basketball at the row before the score (the rebound, the steal, the inbound); a basketball play with no
feed row in the 36 s before it has no clean start and gets no clip.

**How old a clip may be** (Nick, Nov 11: "yes the catchup can be old as it wants (assuming its multiple clips) if we
catching up from 1 play then ideally we saw it within the 3 min cap"): a package of 2 or more plays has **no age cap**;
it is limited only by what its source can reach (the hidden stick's rewind, the Key plays reel, Prime's features). A
one-play catch-up keeps the 3-minute cap (`catchup.HARD_CAP_S`, 180 s behind live). Two close plays played through as
one stretch count as two plays. A package that falls to one play gets the cap back.

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
`min_worth` 0.3, `min_value` 0.8, `lead_s` 2, `tail_s` 3, `max_age_s` 180 (one play), `multi_max_age_s` inf (2+ plays),
`rewind_rule` measured, `reach_s` 175,
`reel` false, `moments` false, and the source timings above. The YAML block waits for the PARK-UNIFY and
PARK-TIMING-CONFIG claims on `redzone/default_config.yaml` to close.

## Seams

- **Seen-state** (merged, `redzone/noggin/seen.py`): `park_catchup.SeenLike.last_seen(game) -> grid second | None`.
  With park.seen on, `TrackerSeen` reads the show's `seen_state` (`g[game].last_p`); otherwise `ShowSeen` reads `seen_to`.
  While a replay is up the tracker is ticked with no picture for the catch-up's game, so the others go on getting stale.
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
scores a catch-up as one cut. Every scenario uses Nick's age rule. Scenarios: `off`; `proven` (175 s rewind, no reel,
no Key Moments); `rewind10` (what-if: the hidden stick rewinds 10 minutes); `library` (what-if: the whole game);
`reel` (what-if: proven rewind plus the reel at its measured 12-minute lag and Prime Key Moments at priors).

Results under Nick's age rule, with abort-to-live and the hard lateness cap (Nov 12, after merging #432, #433, #435;
whole dev nights, seed 0, fake sticks; "chances" = a return to a game unseen 2+ min with its stick parked there; length
includes the wait for the live join; oldest clip = how far behind live its first second aired; big plays = the grader's
must-sees):

| case | rewind | fits / chances | packages by plays (1/2/3/4/5) | length median / p90 s | oldest clip median / max s behind live | missed big plays: via catch-up / never | graded value seen: live + via catch-up | frozen grader: mid-play joins / leaves (catch-up joins) | jumps a min | worst behind live s |
|---|---|---|---|---|---|---|---|---|---|---|
| NBA 20261023, 4 games | off | - | - | - | - | 1 missed: - / 1 | 61% | 0 / 0 | 0.502 | 142.0 |
| NBA 20261023, 4 games | 175 s (proven) | 9 / 51 | 5/0/2/0/2 | 39 / 156 | 92 / 201 | 1 missed: 0 / 1 | 51% + 8% | 0 / 0 (0 of 9) | 0.443 | 130.6 |
| NBA 20261023, 4 games | 10 min (what-if) | 11 / 45 | 5/2/1/1/2 | 47 / 86 | 129 / 650 | 0 missed: 0 / 0 | 57% + 10% | 0 / 0 (0 of 11) | 0.391 | 133.9 |
| NBA 20261028, 4 games | off | - | - | - | - | 0 missed: - / 0 | 50% | 0 / 0 | 0.522 | 144.0 |
| NBA 20261028, 4 games | 175 s (proven) | 9 / 43 | 1/6/0/1/1 | 55 / 118 | 90 / 212 | 0 missed: 0 / 0 | 42% + 8% | 0 / 0 (0 of 9) | 0.522 | 143.9 |
| NBA 20261028, 4 games | 10 min (what-if) | 11 / 47 | 0/3/2/3/3 | 103 / 127 | 330 / 484 | 1 missed: 0 / 1 | 40% + 21% | 0 / 0 (0 of 11) | 0.391 | 138.0 |
| NBA 20261021, 4 games | off | - | - | - | - | 0 missed: - / 0 | 44% | 0 / 0 | 0.53 | 140.6 |
| NBA 20261021, 4 games | 175 s (proven) | 9 / 52 | 5/0/2/1/1 | 46 / 206 | 120 / 197 | 0 missed: 0 / 0 | 40% + 13% | 0 / 0 (0 of 9) | 0.435 | 143.7 |
| NBA 20261021, 4 games | 10 min (what-if) | 13 / 52 | 3/3/2/1/4 | 56 / 127 | 236 / 502 | 0 missed: 0 / 0 | 43% + 23% | 0 / 0 (0 of 13) | 0.416 | 134.0 |
| NFL on Prime + 1 NBA, pinned | off | - | - | - | - | 4 missed: - / 4 | 86% | 0 / 0 | 0.204 | 142.0 |
| NFL on Prime + 1 NBA, pinned | 175 s (proven) | 2 / 30 | 2/0/0/0/0 | 46 / 46 | 50 / 50 | 3 missed: 0 / 3 | 88% + 0% | 0 / 0 (0 of 2) | 0.19 | 139.0 |
| NFL on Prime + 1 NBA, pinned | 10 min (what-if) | 2 / 26 | 1/0/0/1/0 | 44 / 44 | 32 / 32 | 7 missed: 0 / 7 | 83% + 1% | 0 / 0 (0 of 2) | 0.163 | 143.0 |

What it says:

- **Rule #1 holds**: 0 mid-play joins and leaves by the frozen grader in every run, 0 catch-up live joins mid-play,
  0 clips starting mid-play, 0 clips over their age cap.
- **Lateness cap: 0 breaches** (worst 144.0 s; the cap is 145 s).
- **Must-sees, catch-up ON vs OFF**: the proven 175 s rows meet the bar on every night (1/1, 0/0, 0/0, 3/4). The
  10-minute what-if does not on two: 20261028 (1 vs 0) and NFL + Prime pinned (7 vs 4). None of those misses aired while
  a package was up or being prepared (a test pins that: abort-to-live works); they came 2-6 minutes later, from the
  planner's own choices after the night had gone differently. A per-night "never more than off" is not guaranteed by
  this design.
- **Abort-to-live**: no catch-up starts while any game has a high-leverage play in its last 5 min or a must-see
  reported and not yet aired; one under way is cut at the end of the clip on screen and its live join goes to that game.

## What is NOT proven

- Nothing has run on the TV. Every source timing past the Nov 9 seek test is a prior.
- The re-seek between clips shows the scrub overlay on screen; how that looks has not been seen.
- A catch-up takes the screen for about a minute: in replay it costs some live jumps (see jumps a minute). Whether a
  viewer prefers the recap is Nick's call; the Game Picker app could measure it.
