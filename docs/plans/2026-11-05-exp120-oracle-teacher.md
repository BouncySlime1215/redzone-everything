# EXP-120..122: windowed-hindsight oracle teacher (T3): pre-registration

Owner T3 (Nick's 10:00 AM Nov 5 order, item C of the 9:10 AM time-shift-buffer plan). Written Nov 5 ~9:35 AM ET (14:32 UTC),
BEFORE any locked-night number was computed or read. Ids from lane 1's 100s block (EXP-120..124, confirmed free).

## Code under test

`redzone/lab/oracle_teacher.py` on `claude/t3-oracle-teacher` (base: lane 1's `claude/lane1-buffer-oracle` 52c03c6):
- **P** (placeholder): `oracle.solve_window`, beyond-window value = past mean (lane 1's v1).
- **L** (learned): `oracle.solve_window(value_fn=oracle_teacher.value_fn(model))`: ridge per league and reward
  component on game state at the last revealed step. Fitted on TRAIN nights only; its horizon H (12/24/48/96 steps)
  picked on VALID nights by mean % of oracle. Frozen (model JSON sha256 recorded in the results) before the locked run.
- **LC** (learned + play-start backoff): `oracle_teacher.solve_window_cue(backoff_s=15)`.
- Ceilings: **full** = `oracle.solve` (full hindsight), **cue** = `oracle.solve_cue(max_cue_s=15)`.
All arms objective `combined`, tune 5 s, graded by `sportsgrade.grade` (the app's grader, unchanged).

## Grid

Leagues NBA, NFL, NHL. Nights: `splits` test split of eval-data-v1 (LOCKED: NBA 4, NFL 19, NHL 4), read once.
B in {60, 90} s behind the stream's live edge (screen lag = 26 s stream delay + B). Feed lag = the league's
`pick.FEED_LAG_P90` (ESPN today: NBA 70, NFL 62, NHL 75 s). Window = B + 26 - feed lag - tune.

## Metric

Per night: % of oracle = 100 x objective value of the arm / objective value of **full** (`oracle.schedule_value`).

## Bars

- **EXP-120 (learned value, L vs P), primary.** Paired night bootstrap (2000 draws, seed 8; nights resampled across
  the three leagues pooled, both B arms of a night move together) of mean(L - P) in % of oracle, averaged over B.
  PASS: 95% CI lower bound > 0, AND 0 mid-play entries on every L run, AND pooled big-live % of L not below P by
  more than 1.0 pt. FAIL: CI upper bound < 0, or any mid-play entry. Otherwise INCONCLUSIVE (never a pass).
- **EXP-121 (backoff, LC vs L).** Same bootstrap on mean(LC - L) in % of full oracle. PASS: CI lower > 0 AND 0
  mid-play entries on every LC run. FAIL: CI upper < 0 or any mid-play entry. Else INCONCLUSIVE.
- **Rule #1 (all arms, hard):** `entries_mid_play` = 0 on every night, arm and B. Any non-zero = FAIL of that arm.
- **Per league:** reported, descriptive only (NBA and NHL have 4 locked nights: below the 8-night power rule).

## Reported (descriptive, EXP-122)

Same table with feed lag 8 s (the fast-feed case T6 is building), valid-night numbers, big live %, crunch live %,
dead air %, switches/h, LC as % of the cue ceiling, the model's valid-night R^2 per component.

## Known risks, written now

- Big plays from the start (lane 3's line) is not on integration/next yet; big live stands in.
- NBA/NHL training is 7-8 nights each; the pooled ridge backs any league with too few rows.
- The objective is the oracle's own `combined` weights; taste (Watch Value v2) is not in this pass.
- The window ignores the extra look-ahead a backoff offset gives (conservative).

## Amendment 1 (Nov 5 ~10:50 AM ET, before any locked-night number was computed)

- Rebased onto lane 1's f4dceb4 (#322): `solve_window` lives in `oracle_window.py`, reserves the grader's look-ahead
  (`grading.RESUME_LEAD`, NBA 26 s), plans nothing when the revealed window is shorter than a switch, and starts on a
  dead ball. `solve_window_cue` follows the same three rules. Consequence: with ESPN p90 feed lag, NBA at B = 60 has
  no window (P = L = LC by construction); those nights enter the bootstrap with delta 0.
- The beyond-window value drops the your-team component (picked on VALID nights: with it, the learned prior parked
  the screen on WSH/UNC waiting for crunch that never came). H is picked from {24, 48} steps on valid.
- Valid-night read that motivated it (descriptive, pre-f4dceb4 window, ESPN p90 feed): % of full oracle, P 77.9,
  L(H=48, no your-team) 86.4, L + 50/50 blend with P 84.4; by league NBA 78.4 -> 93.0, NFL 93.9 -> 94.6,
  NHL 37.3 -> 59.3.

## Status (Nov 5 10:50 AM ET)

Paused by Nick's budget throttle (10:45 AM): first code pass only. Fit, valid selection and the locked grade are
NOT run yet; this file stays the pre-registration for when they are.
