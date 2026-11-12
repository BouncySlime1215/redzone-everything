# PARK: the two-stick show

PARK is how RedZone cuts between live games with two Fire TV sticks and one TV. It is behind the flag
`park.enabled`, which is **off by default**. Nothing in this file has run end to end on the real TV yet
(see "What is not proven").

## What the show does

- Two sticks play two different games. The TV shows one of them. The other stick is the **hidden stick**.
- The hidden stick is tuned to the next game and **paused** on a dead-ball frame (a moment when the ball is dead).
- A **jump** does three things: resume the hidden stick, flip the TV input to it, then pause the stick that was
  just left. The viewer sees a new game starting right after a dead ball.
- Each stick is held 35 s behind its stream's live edge (`park.show.show_delay`), so the feed is complete for
  everything on screen.
- A jump takes about 3 s on the real sticks; a tune with a read takes 13 to 19 s.
- The plan looks a few jumps ahead each second. Pace is only a soft pull (`park.show.bonus`).

## Rule #1

Rule #1 is: never join mid-play; never leave a high-leverage moment (close and late, or overtime). The root
[CLAUDE.md](../CLAUDE.md) holds the full wording. It is judged by the **frozen grader**, which PARK never edits:
`redzone/grading.py` (`dead_at`, the grader's own answer for "is the ball dead at this second") and
`redzone/lab/cutgrade.py` (scores both ends of every switch). High leverage is `redzone/lab/leverage.py`.

PARK never trades Rule #1 for pace. If a step fails or a read-back is missing, there is no jump; the picture
that is up stays up.

## How it is wired

1. `Engine._park_loop` (`redzone/engine.py`) sleeps while the flag is off. With it on, it makes a `ParkRun`.
2. `ParkRun.loop` (`redzone/park_run.py`) runs once a second. It starts a show only when the steps may run, no user,
   safe-mode or replay owner holds the TV, and two or more live games of `park.leagues` are on YouTube TV (or Prime)
   with the game on screen among them. On any stand-down it finishes first (`park_finish`), so a stick is never left paused.
3. `redzone/noggin/park_show.py:Show.tick` is the brain: facts, padding, plan, guard, then a stick move.
4. Helpers in `redzone/noggin/`: `park_facts.py` (what is known so far, never hindsight), `park_plan.py` (the planner),
   `park_core.py` (the step function shared with the simulator), `park_timing.py` (K and padding), `park_prime.py`.
5. `redzone/moments/value2.py:grade` scores a moment as base x leverage x stakes (football, basketball).
6. `ControllerRig` (`park_run.py`) turns moves into `Controller.park_*` calls in `redzone/control.py`; `ParkHands`
   there does the stick work (tune, read the place, pause, resume, flip).

## The hard guard before each jump

`Controller.park_why` (`control.py`) refuses every step unless `park.enabled` is on, the app is live (not dry run),
two sticks are usable, `sound_guard.enabled` and `sound_guard.must_be_silent` are on, and no sound incident is open.
A sound stop ends the show until a person clears it.

Then `park_facts.refusal` is asked once more before any key goes out. It refuses a jump if:

- the leave is inside a padded high-leverage span;
- the leave is outside a padded legal window (the grader's dead time only);
- the landing is outside a padded legal window;
- the landing would be more than `park.late_cap_s` (145 s) behind live.

`ParkHands.jump` keeps the old picture if the flip does not land.

## The game on the picture (`park.verify_game`, off)

A game is mapped to a channel from the schedule (ESPN's broadcast field, or a guess such as Peacock to NBCSN), never
from the picture. On Nov 11 the show called ESPN's picture CLE @ NY for 15 minutes after that game ended and ESPN
moved on to GS @ HOU. With `park.verify_game` on, every tune (and the placing of the stream on screen) reads the words
on the picture with the expected game's team names and every other live game's (`redzone/noggin/park_verify.py`):

- **Verified**: the expected game's two teams are named and no other game's are. Its K (if any) is used.
- **Wrong**: another live game's two teams are named and the expected game's are not. The tune fails, the stream's
  place is dropped, the show stands down and re-picks without that game-and-channel pair for 30 minutes.
- **Unknown**: nothing readable, a blank frame, or both games named. Its K is kept back (the wide padding) until a
  later tune confirms it. A guessed channel counts as unknown the same way.

YouTube TV's video is DRM-blank in a screenshot, but its overlay is not: the words come from the read gesture's own
frame (no extra screenshot, no key, no added seconds). Prime Video: one screenshot and one local text read.

## The warrant on each jump

Every jump gets a **warrant** before it is planned: why leave here and go there (`park_plan.py`, `warrant`
inside the search). A jump is warranted when:

- the game being left is over; or
- live play will be on screen at least 5 s sooner (`WARRANT_GAIN`); or
- the next 30 s are worth more there than here (`WARRANT_LOOK`).

A landing that shows no play is never warranted. With `park.show.warrant: need`, an unwarranted jump is not planned,
and `Show` checks again before it goes. The warrant is kept in each jump's decision record and the Show tab (`show.ui`).

## Padding

Padding is extra seconds kept clear at both ends of every legal window and around every high-leverage span.
`park_timing.py` sets it per game: `pad_k` (4) times the root of the summed squares of four error parts: the position
read, the spread of K, the feed stamp, and the keys. **K** is the content time of a frame minus the feed time of the
same moment. It belongs to one game on one night, and comes from the K file named by `timing.game_offset_file`.

- A game with a read tune and a known K gets about 3 s.
- A game with no K tonight is **unknown**: it gets a wide prior, about 48 s, never zero.
- `park_facts.GRID` trims a few more seconds because the planner works on a 1 s grid.

## Prime and football

Football uses the grader's NFL dead time (`dead_at`): each action row covers the 7 s before its stamp, and
everything else is dead. One extra guard: a dead stretch more than `park.play_clock_s` (40) plus `park.snap_slack_s`
(5) after an action row, with no stoppage row to explain it, is not trusted. Football padding is about 3 s
(`park.timing.stamp_sd_football`).

Prime Video games (Thursday Night Football, Friday NBA) need `park.prime.enabled`. A Prime tune uses the existing
Prime tuner and a pause-hold; Prime has no rewind. K comes from the hidden stick's score-bug reads (numbers only).

**The anchor.** With `park.prime.pin_football` on, a Prime football game that is on screen when the show starts is
pinned on its stick. It is never held paused when left. It plays on unseen, and is re-tuned (about 100 s on Prime) only
when it reaches the lateness cap. All the roaming happens on the other stick. Separately, in crunch time
(`park.show.crunch`) the best high-leverage game is the anchor and the others only fill its stoppages.

**The NFL-night slider.** `park.football_slider` runs from 0 ("more everything else") to 100 ("more football").
Football's value is multiplied by 16 to the power of (slider minus 50) over 50, so the range is 1/16 to 16. The ends
are weights, not locks: at 100 other games still backfill football's long breaks. 50 leaves value in charge, and is
the same as no slider.

## Fast steps (`park.fast_steps`, off)

The Nov 11 real-TV hands test measured each step (`data/hands_2026-11-11.log`, n=20): tune 16.0 s, park 4.2 s,
jump 6.9 s. A jump, the re-tune of the stick it left and a park are the cycle before the next jump. With
`park.fast_steps` on, the same keys go out in the same order and every check still happens; fewer seconds sit in
front of the next step. `scripts/park_step_bench.py` measures each step on fake sticks and a fake TV whose calls take
real time (set so the flag-off steps match the TV test within 0.2 s). It is a model, not the TV.

| Step | Flag off: where the seconds go (bench) | Flag on |
|---|---|---|
| jump 6.9 s | resume lead 0.3, flip 2.0-2.4 (TV command and its input read-back), silence read twice 1.4 (the TV's own after-switch read, then a second read queued behind it), new stick playing 0.2, then the stick left: its pause read 0.2, BACK 0.25, 1.6 s wait, read-back 0.9 | 2.7 s: one silence read 0.35 (the after-switch read is used; mute, volume, power and input in one round); the playing report and the left stick's pause are read during the flip; the left stick's BACK and read-back run after the jump |
| park 3.9 s from its key | key 0.25, paused seen 0.6, BACK 0.25, 1.6 s wait, session read 0.2 then foreground app read 0.65 | 3.4 s: the two read-back reads at once; reads poll every 0.1 s, the first one when the frame can have frozen |
| tune 16.0 s | tuner, 2 s of position 0, the read gesture (4 keys, label read 3.3 s, its fixed waits 6.2 s) | 15.8 s: reads poll every 0.1 s |

The checks and where they now happen:

- **Silence around every switch.** The sound guard still refuses a switch while an incident is open, and its
  after-switch check (started by the switch once the TV reported the new input) is the jump's read-back. If that
  check is missing, unreadable or anything but silent, the jump reads again exactly as with the flag off. A sound
  stop sends nothing more: no hold is started, `_key` refuses every key.
- **Input confirmation.** Unchanged: the TV's own read-back inside `set_input`, then the input from the silence read.
- **The new picture playing.** Read during the flip; waited for before the jump returns (and once more if late).
- **The stick left (focus guard: YouTube TV in front).** Its pause is seen before the jump returns. Its BACK and
  read-back (still paused, YouTube TV in front) run after it, as `ParkHands.pending`. Until they end: the show holds
  that stream as not ready (`ControllerRig.confirming`), a jump onto it is refused with nothing sent, a park or resume
  waits for them, and a new tune or `finish` ends them before their next key (the stick's place is then dropped:
  never believed parked without its read-back). A read-back that fails clears the standby record.
- **Rule #1** is untouched: the planner, the guard and the leave timing are the same; nothing waits in front of a
  jump's resume key.

The TV's input path: PARK's flip uses `park.steps.flip_methods` ([smartthings]) only, so the local path costs nothing
on a jump. (The ordinary two-stick switch is local-first through `SamsungTV.paths()`; on Nov 11 two local tries
took 4.2 and 9.1 s and did not confirm. That path is not PARK's and is not changed here.)

Replay with the step costs priced from the TV test (`--steps off`) and with the flag's savings (`--steps on`):
`scripts/park_replay.py --night <id> --games 4 --steps on`. `--steps model` (default) is the replay as before.
On the 3 NBA dev nights (20261021/23/28, 4 games, 5 seeds each, Nov 11): 0.464 -> 0.481 jumps a minute (on faster in
11 of 15 runs), best 5 minutes 6.2 -> 6.6 jumps, graded value seen 54% -> 52% (paired run-to-run swings of -17 to +13
points: noise at this sample), frozen grader 0 joins and 0 leaves mid-play of 1080 and 1114 cuts. The replay's pace
is held mostly by safe places to join, not by the sticks; the bench's jump-to-jump time (27.2 -> 22.6 s back to back)
is the ceiling the flag lifts.

## Config keys (`park.*`, defaults in `redzone/default_config.yaml`)

Top level:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | false | master switch |
| `fast_steps` | false | the step mechanics with fewer seconds on the critical path, every check kept (see "Fast steps") |
| `leagues` | [nba, cbb] | leagues the show takes; add nfl, cfb for football |
| `max_park_s` | 180 | a stick parked longer is stale and never jumped onto |
| `late_cap_s` | 145 | nothing shown further behind live than this |
| `session_min` | 240 | length of one planned show |
| `possession_cap_s`, `late_row_s` | 30, 60 | feed-quality caps: silence after a review ruling; a late-edited row |
| `idle_poll_s`, `start_retry_s` | 5, 30 | idle flag check; retry wait |
| `football_slider` | 50 | the NFL-night slider |

Groups (numbers measured on the sticks or picked in replay; the YAML comments say how):

- `steps.*`: stick step times from the Nov 10 TV runs (tune, read gesture, pause, resume, flip). `flip_methods` is [smartthings].
- `feed_lag.*`: `enabled` false. Parks a late-feed football game deeper. `leagues`, `prior_q`, `min_samples`, `move_s`.
- `prime.*`: `enabled` false, `pin_football` true, `bug_gate` false, `pair_offset_s` 60, `stop_match_s` 1.
- `show.*`: `show_delay` 35, `bonus` 3.0, `min_look` 3.0, `warrant` need, `crunch` true, `stops` true, `land_wait` 20,
  `long_break` 90, `tune_stick` 1.0, `late_cap` 145, `jump_real` 3.0, `read` 10.5, and the tables `stop_max` and `stop_min`.

Read in code but absent from the YAML (defaults in `park_facts.py`, `park_timing.py`): `pad_k`, `play_clock_s`,
`snap_slack_s`, `timing.*` (error parts, `live_lag`).

## Run the replay (no TV, no sticks)

`scripts/park_replay.py` runs the app's own `Show` on a recorded dev night with fake sticks and a fake TV. It needs
the pinned eval data once (`python scripts/fetch_eval_data.py`). Dev nights only; a locked night is never opened.

```sh
nice -n 15 .venv/bin/python scripts/park_replay.py --night 20261023 --games 4
.venv/bin/python scripts/park_replay.py --night 20261023 --games 4 --fail flip=2 --fail read=3 --lines 30
```

Other flags: `--league`, `--hands app|runner`, `--slider 0-100`, `--pin`, `--prime N`, `--nfl i`, `--deep`, `--seed`.
It reports Rule #1 counts, jumps a minute and the warranted share, judged where the pictures truly were. No device I/O.

## What is NOT yet proven

- **Nothing has run on the real TV through the app.** A local show runner ran on the TV on Nov 10;
  the in-app version is proven only in replay with fake sticks. The flip path is SmartThings; its free access ends 2026-12-15.
- A pause held longer than 180 s, or on other channels, has not been read back. The 40-minute hold test and a muted 10-minute Prime test are still to do.
- Prime football timing, football K and the play-clock gate rest on one replay and priors.
- Pace is a replay result with fake sticks: the app's own controller on 3 recorded NBA nights with 4 games made about 0.52 jumps a minute at 3 s padding and 0.39 at 8 s, with the frozen grader at 0 / 0 / 0. The real TV will use 8 s until enough measured whistles exist. Live results are unmeasured.
- The Mac runs `42e2ee8f`, which lacks this code.

## The picture grader (flag `park.picture_grade.enabled`, off)

Every jump judged on what YouTube TV showed, not on the feed. With the flag on and `park.picture_grade.tool` naming the
Eyes program (`redzone-agents/tools/eyes/picture_grade.py`, outside this repo), `Engine._picture_loop` starts it while
a show runs, at `nice -n 15`, and stops it at the stand-down (`redzone/picture_sidecar.py`). It uses Eyes' own windowless, muted Chrome
and signed-in profile, follows the channel on screen, and on each jump reads the score bug on paused frames at the
leave (from its buffer) and at the landing (it switches channel at once). It writes `picture_report.jsonl` and
`picture.json` into the show's state folder; `state.json` then carries `picture` ("picture: clean n / mid-play n /
unknown n"). Off, nothing starts and the state is unchanged. The live check (`park.picture_check.live`) is off and not
wired to any decision yet.
