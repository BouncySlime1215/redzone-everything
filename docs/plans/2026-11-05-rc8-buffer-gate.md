# RC8 gate: the time-shift buffer ON per league (pre-registered before tonight's shadow data)

Owner: the integrator. Nick, Nov 5 9:10 AM ET: "I approve this plan and I want it all done and ready for tonight."
The time-shift buffer is the main architecture: the show runs 60-90 s behind live (cap 90 s) and the brain edits.
This file fixes the gate (part E) BEFORE any shadow night or sweep result is read, so no bar moves after the fact.

## What decides it, per league (NBA first, then NFL; others where shared)

A league's buffer turns ON in RC8 (`timing.behind_live_s` scoped to that league, Nick flips it) only if all of:

1. **Rule #1, hard.** Mid-play entries 0 on every run of the sim sweep (B) at the chosen buffer, on BOTH harnesses
   (honest and `--resync`), and 0 on tonight's shadow night. NBA/CBB need 0 on both graders (cutgrade and the
   clock-truth grader, v26). Mid-play leaves at high leverage 0. Forced (double Go now) entries are graded on their
   own line and never count here.
2. **It beats live (sim sweep, dev nights only, >= 8 nights per league, seeds 0-2).** Against the same nights with the
   buffer off, paired night bootstrap:
   - primary: big plays shown from the start (from the previous dead ball; `big_from_start`, grader v28, #316),
     CI lower bound of buffer - live > 0. The live arm is the same harness with `--behind-live 0` (the stream's own
     delay), and the sweep also reports one run at the old default lag for continuity;
   - guardrails, each on both harnesses: crunch live (shown from the start) not down by more than 1.0 pt; your-team
     crunch 100%; dead air not up by more than 0.5 pt; switches per hour <= 1.5x live; plays missed on entry not up
     by more than 5% relative.
   The buffer value is the sweep point (45/60/75/90 s) with the best primary among those that hold every guardrail,
   picked on train+valid nights; it is never picked on locked.
3. **Tonight's shadow confirms.** On the shadow night (and any later shadow night) the shadow schedule's lines, graded
   by the same graders on the night's real play-by-play, are no worse than live beyond the guardrails above, with
   0 mid-play entries. One night cannot PASS a rate on its own (MIN_NIGHTS); it can only veto.
4. **Locked once.** The chosen setting is read on locked once, at the RC8 cut, both harnesses; the release gate's
   own floors (>= 8 nights, >= 60 decisions per gate) apply, and a thin line reads PENDING, never PASS.
5. **Sync honesty.** The stick's real behind-live, from the shadow logs (Prime epoch position, YTTV meter), stays
   within 2 s p95 of the buffer the brain assumed (EXP-800 bar), or the league waits.

A league that misses any of 1-5 stays live in RC8, behind its flag. Rule #1 never changes. While Nick watches,
only the hidden stick is touched.

## Buffer size policy (Nick, Nov 5 9:35 AM ET)

The buffer shrinks as feeds and sync improve, but it never reaches 0. Per league:

    behind_live_s = feed-lag p95 + sync error p95 + tune/seek time p95 + safety margin,   capped at 90 s

- **Inputs are measured, not tuned.** Feed-lag p95 comes from lane 2's per-league feed-lag table, sync error p95 from
  the EXP-800 sync grader on the shadow logs, tune/seek p95 from lane 2's seek-reliability measurement per app/channel.
  Each input is read on train+valid nights only; locked never sets any of them.
- **Hard floor.** The buffer is never below 15 s in RC8 (Nick: "start ~10-15 s"; we start at the top of that range).
  The floor exists so a play is always known from the feed before its first frame is shown. Lowering the floor itself
  to 10 s is a separate decision for Nick, after a release whose locked gates hold at 15 s.
- **Ratchet down only, one step per release, only after locked gates hold.** A league's buffer can go down only when
  (a) the current value passed sections 1-5 above on a locked read at a release, and (b) the new, smaller value
  (recomputed from the formula, never below the floor) passes the same sim sweep and shadow veto on fresh nights.
  One step per release, at most 15 s per step. It goes back up at once (to the last value that held) if any shadow
  or live night shows a mid-play entry, or if a re-measured input pushes the formula above the current value.
- RC8 sets each passing league to max(sweep point from section 2, formula, floor), so the measured inputs can only
  raise the buffer, never cut it below what was graded.

## Steps

- Tonight: lane 2's shadow (no screen change) logs every decision; lane 3 + 1 run the sweep on recorded nights.
- Friday morning: the integrator grades 1-3, posts a per-league card to Nick, and builds RC8 on integration/next with
  only the passing leagues ON. Lane 4 cuts; Nick flips.
