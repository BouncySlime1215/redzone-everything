# Anchor mode: hold the big game, fill its gaps (2026-11-09)

> **Superseded by [docs/PLAN.md](../PLAN.md)**, the only plan (section 4). This file is the anchor-mode spec it uses.

Nick, Nov 9 night: "the OT game has the most weight, so we would hang there for a bit. In any available gaps we would
jump to and catch up on clips." And: "crunch logic is hard cause of our loading restrictions, enter/leave restrictions,
and seeing everything while focused on the BIG moment. u figure this out."

## 1. The one idea that makes it work

The screen stick never leaves the anchor game. It keeps playing the anchor while the TV shows the hidden stick, so
coming back is one input flip (1.8-2.2 s, SmartThings, 9 of 9 on Nov 9), not a re-tune or a seek. The anchor's
position never moves, so we land exactly where we left: inside the same dead ball, as long as we come back before it
ends. Every risk in anchor mode then reduces to one number: **how much dead time the anchor surely has left.**

## 2. Which game is the anchor

- A game becomes eligible when it enters its crunch window (`crunch.py`; NFL Q4 2:00 or less within 8, or overtime)
  or is in overtime in any league.
- One anchor at a time. Its score is the live value v2 of its next 3 minutes (the model's prediction once trained;
  the leverage x stakes table until then), with your teams first as today.
- A second eligible game takes the anchor only if its score beats the anchor's by 30% (hysteresis, so the show does
  not ping-pong), and only at a dead ball in both games (Rule #1, unchanged).
- The anchor ends when its game ends, when it leaves crunch and the margin is two scores or more, or when another
  game takes it. Then the normal show resumes and the late queue (section 5) airs.

## 3. Which gaps can be filled, and how much

A gap is usable only when its **guaranteed** remaining dead time is known. Guaranteed = the 10th percentile of that
stoppage type's length (feed stoppage to real restart, `break_lengths.json`, train nights) minus the time already
elapsed, minus 2 sigma of the stream-delay estimate.

| Anchor stoppage (pooled q10 / q50, s) | NFL | NBA | NHL |
|---|---|---|---|
| Timeout | 35 / 136 (two kinds: no-commercial ~35-50, TV ~136+) | 99 / 183 | 135 / 149 |
| Two-minute warning / end of period | 145 / 160; 177 / 195 | 152 / 168 | 139 / 153 |
| After a score | 110 / 192 | (whistle) | 41 / 49 |
| Review | 108 / 215 | 20 / 161 | 151 / 190 |
| Injury | 29 / 77 | n/a | n/a |
| Plain between plays / whistle | **not used in v1** | 5 / 35: not used | 25 / 37: not used |

Between-play gaps are excluded in v1: a two-minute drill snaps in about 15 s, and the next snap is the moment the
anchor exists for. They unlock only after we measure snap-to-snap times in crunch from the feed and the TV.

**Budget for one gap** = guaranteed dead time - flip out (1.9) - flip back (1.9) - margin (3.0).
An NFL no-commercial timeout (35 s) gives about 28 s: one short clip. An NFL commercial (110-136 s guaranteed) gives
about 100-130 s.

## 4. What fills a gap (two kinds, both clean under Rule #1)

1. **A short catch-up clip** (`catchup_route_short`): 3 s lead-in, the play, 1 s; an NFL play is about 10 s on screen.
   The hidden stick must be **loaded before the gap opens** (tune + 3 s + seek measured 18-25 s, n = 6, busy Mac). It
   prepares the next clip while the anchor is live, so the first clip of every gap costs no wait.
   A clip ends on its own: it can never strand us mid-play.
2. **A one-play live visit**: another game is about to run a high-value snap (red zone, 4th down, last-minute shot).
   The hidden stick is tuned to it (2.3-4.3 s measured), we flip in at its pre-snap dead ball, watch the play, and
   flip back at the dead ball after it. Allowed only when that game's expected snap + play (about 8 s NFL) ends
   before the anchor's guaranteed restart minus the margin. If the snap is late, we come back anyway at a dead ball:
   the anchor always wins.

Inside a commercial the hidden stick is the only free stick, so the gap runs as a chain:
clip 1 (preloaded) -> flip back to the anchor's commercial while the hidden stick loads clip 2 (18-25 s) -> clip 2 ->
... About 34 s per clip, so 3 clips in a 110 s NFL commercial. A one-play visit costs only a tune (about 4 s), so
when one is available it goes first.

**Choosing the fill** is a small knapsack every time a gap opens: items are the queued clips (replay value v2, each
with its 180 s deadline) and the visits on offer (live value v2 of that snap), the capacity is the budget, and the
order is fixed by what is already loaded on the hidden stick. At most a few items, so it is solved exactly.

## 5. What happens to plays the gaps cannot hold

Every must-see elsewhere during the anchor's live play goes to the clip queue with its replay value.
- Airs in the next anchor gap if its 180 s deadline allows (HARD_CAP_S, unchanged).
- Past 180 s: dropped, **unless** it is big enough (replay value above a threshold to be fitted; a prior of the
  top 5% of replay values) and then it airs **last**, after the anchor ends (Nick, Nov 9: "a later one only if big
  enough, and very late ones air last").

## 6. What must stay true (checked by the sim and the commit gate)

- 0 joins mid-play, 0 leaves of the anchor during a live play, 0 high-leverage leaves anywhere.
- 0 returns to the anchor after its restart (a late return is the one new way anchor mode could break Rule #1).
- No anchor live play missed while away.
- Flag off (`anchor.enabled`, default false): decisions hash-identical to today on every fit night.

## 7. What the current code already has, and what changes

From the code map of Nov 9 (PR 423 head, file:line in the map):
- `_crunch_time` + `_crunch_replay` already hold a crunch game and play a clip that ends before the next snap. Anchor
  mode generalises this into one state that every crunch rule shares; today four separate blocks
  (`replay.crunch_block`, `_crunch_guard`, the arbiter's class check, `catchup_gates`) each refuse clips in crunch.
- **Bugs to fix first (flag-off paths, no live effect today):** the live catch-up route never passes `ready_at`, so it
  assumes the hidden stick is free; `prep_s` is a constant 15, not config; the live route values clips with
  `clip_weight` while the audit uses 5/3/1.
- **One timing source:** flip is 3.2 / 3.0 / 2.5 / 1.89 / 5.0 across files; dead-ball length lives in five tables
  that disagree (NFL two-minute warning 50 / 70 / 150 / 145). Anchor mode reads one predictor
  (`break_lengths.json` quantiles) and the measured Nov 9 timings, behind a timing flag so flag-off stays identical.
- The zero-loss audit sizes clips with hindsight dead-ball length; it must use the same guaranteed floor the live
  code sees, or it overstates what anchor mode can fill.

## 8. Build order (one builder at a time)

1. TIMING-TRUTH: measured timings and one dead-ball predictor behind `timing.measured` (off); `ready_at` and
   `prep_s` wired; audit on the live floor. Rerun the zero-loss audit with the measured numbers.
2. VALUE-V2B (running now): live and replay value per moment.
3. ANCHOR: the anchor state, the gap budget, the knapsack fill, the late queue, behind `anchor.enabled` (off).
   Sim on all fit nights: the six counters of section 6 plus clips aired per anchor gap and must-sees seen.
4. TV session: one real crunch or overtime game, mute read back first, 10 minutes, every failure and its reason.

## 9. Open, and how each gets settled

- Seek landing: the time label and the knob disagree by about 15 s (TV test 3b). Until settled, a clip's lead-in is
  3 s + 15 s of doubt, so v1 clips start 15 s earlier than needed (longer clips, never a missed start).
- Prep time on a quiet Mac (Nov 9 numbers were on a busy one): TV test 3e, 10 tries.
- Whether a one-play visit's tune is ready in time on the 4K Plus (first tune after waking took 11.6 s): keep that
  stick awake during an anchor.
