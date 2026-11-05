# Cycle 24 plan (Start Planner), Thu Nov 5, ~14:15 UTC: the time-shift buffer becomes the main architecture

**Nick, 9:10 AM ET:** "I approve this plan and I want it all done and ready for tonight." Logged in decisions.md.
The order of priority is now Rule #1, then the **buffer (which is the sync program's new home)**, then NBA, then NFL.
The config already exists: `timing.behind_live_s` (off; by_league NFL/CFB 60, NBA/CBB/NHL 90; max 90).

## Tonight (ET)

| When (ET) | What |
|---|---|
| NBA from ~7 PM | Buffer shadow on every game |
| 8:15 PM | TNF JAX@BAL on Prime |
| 10 PM | MIA@GSW on NBA TV |

## Clock (ET)

| By | Gate |
|---|---|
| **3:30 PM** | Code freeze for tonight. A shadow PR is merged on integration/next with the full suite green. |
| 4:30 PM | Lane 4 stages the RC (shadow + #310 YTTV taps + `user_bug_gate` shadow) and gives Nick the deploy card. |
| 5:00-6:30 PM | Deploy with the TV off, on Nick's tap. **No deploy after the first tip.** |
| Night | Shadow logs only. No screen change. A stick is held via pause/seek only on the hidden stick while Nick watches. |
| Fri morning | Lanes 3 and 1 grade shadow vs live. If shadow wins with 0 mid-play entries, the integrator prepares RC8 (buffer ON per league) for Nick to flip. |

## Orders

| Unit | Item | Must be ready by |
|---|---|---|
| **Lane ②** (owner, A + D) | (A) `timing.behind_live_s.mode: shadow`: per stick, compute the buffered plan and every decision it would make, from the exact signals (Prime epoch position, the YTTV behind-live meter and per-channel landing, the web-player ingest clock as the YTTV reference), and log to `data/buffer_shadow.jsonl`. Pause/seek only on the hidden stick. (D) Seek reliability per app/channel: rewind-to-T error and whether a rewind forces ads, measured off the hidden stick tonight. A SEEK_TO helper if keys aren't precise. | A: 3:30 PM. D: logged tonight. |
| **Lane ③ + Lane ①** (B) | Replay sim: every recorded night as if buffered (plays.jsonl wall + feed arrival lags + tune/seek costs). Grade % big plays shown from the start (from the previous dead ball), mid-play entries (= 0), dead air, switches/h, % of oracle. Sweep 45/60/75/90 s per league. Lane 3 pre-registers the grade, and the integrator freezes it before the sweep runs. Report the mean and worst fold. | Pre-registration: 1 PM. Sweep: before the deploy card. |
| **Lane ①** (C) | Windowed-hindsight oracle teacher: the best schedule using only what the buffer reveals (buffer − feed lag − tune/seek). Exact DP/ILP in-window; learned parts only for beyond-window value, play-start backoff and taste (Watch Value on the real attention labels, never Nick's taps). | Offline, the sim reads it once it exists. Not on tonight's path. |
| **Integrator** (E) | Freeze B's grader. Merge the shadow PR by 3:30 PM. Write the shadow-vs-live gate for Friday. Prepare RC8 only if the gate passes. | 3:30 PM. |
| **Lane ④** | Stage tonight's RC, as above. | 4:30 PM. |
| **Test writers** | Tests for the shadow path: no screen change while shadow is on, the hidden stick only, the log schema. Plus a Rule #1 test that the buffered plan never schedules a mid-play entry. | 3:00 PM. |

## Paused or demoted for today

- EXP-803 A1-A5 v3 grading still runs Friday morning (v3 build 8:30 AM ET), but **after** the shadow grade. EXP-803 becomes
  the buffer's delay model.
- Data crews keep their scheduled jobs. Crew F's NBA break tables feed the sim.
- EXP-109/107g: unchanged (Nick launches).

## Waiting on Nick

The tonight deploy card (lane 4), the NHL Rule #1 package card, the RC7 sync card (superseded by tonight's RC if Nick agrees), RC6.3.

## Addendum, 14:20 UTC: Nick's 9:35 AM directives

### 1. Watch Value v2 spec: due this week (crew G leads, with lane 1)

- **File:** `docs/plans/2026-11-xx-watch-value-v2.md`, literature-backed with papers cited.
  - **Neuroscience:** reward prediction error and surprise, anticipation ramps, habituation and novelty decay, arousal regulation.
  - **Psychology:** peak-end, Ely-Frankel-Kamenica suspense and surprise, narrative transport, stakes.
  - **Sociology:** collective attention, contagion velocity, rivalries and storylines.
  - **Physiology:** published fan heart-rate and arousal studies, as labels for game states.
  - **Math:** expected future peak arousal inside the buffer window × stakes × novelty − whiplash.
  - **ML/RL:**
    - pairwise ranking on real attention labels: highlight view velocity, minute-level viewership curves, Twitch, Reddit and Wikipedia spikes, and market volume;
    - inverse RL from whip-around producers;
    - the buffer makes the next ~60 s of surprise *known*.
- **Every component** names a real data source and a pre-registered EXP (crew G 1700s, lane 1 100s), graded by % of oracle on locked nights.
- **Objective (binding):** "worth it", per the playbook §5 line. Attention labels measure worth. Holding attention is not itself the objective. No dark patterns, neutral wording in public docs, and Nick's own viewing and buttons are never labels.
- **Order:** NBA first, then NFL.
- **Off tonight's critical path:** lane 1's tonight work is the replay sim engine (B), and the spec work starts after that.

### 2. Adaptive per-league buffer policy (lane 2 owns, integrator gates)

- **Buffer per league:** `buffer = feed-lag p95 + sync error + tune/seek time + safety margin`.
- **Hard floor:** 10-15 s, so a play is always known before it's shown. The cap stays at 90 s.
- **Ratchet down** only after locked-night gates hold, one step at a time. The buffer never reaches 0.
- **Tonight:** the shadow keeps the fixed `by_league` values, and it **logs each term** (feed lag, sync error, tune/seek cost) per stick, so Friday can fit the formula.
- **Integrator:** adds the ratchet rule to the RC8 gate. Each step down needs its own locked-night PASS with 0 mid-play on both graders.

### 3. Nick's 9:45 AM order: STAY vs LEAVE from full game context

- **Model (lane 1 builds and trains; crew G folds it into the Watch Value v2 spec):** a per-game "game state + consequence" model.
  - **(a) Context:** score and time, the WP trajectory, leverage (the max WP swing still possible), runs, comeback odds,
    timeouts, fouls and possessions left, and stakes (standings, playoff odds, tiebreakers, rivalry, milestones, storylines, Nick's teams).
  - **(b) Consequence of this play:** ΔWP, Δ expected future suspense, Δ stakes, Δ narrative.
  - **(c) STAY/LEAVE:** expected future watch value over the next N min (world-model rollouts beyond the buffer) vs the best
    alternative, plus the switch and whiplash cost, with hysteresis. **It never leaves high leverage** (Rule #1 and the crunch rules stand).
  - **Training:** the windowed-hindsight oracle plus longer-horizon rollouts.
- **Tonight (lane 2, in the shadow log, fields only, no new logic before the freeze):** every stay/leave decision with the
  context fields the brain already computes (score/time, WP, leverage, crunch flag, stakes inputs) and the predicted value of
  staying vs the best alternative.
- **Grade (lanes 3 + 1, pre-registered, integrator freezes):** on locked nights, regret vs the hindsight oracle and crunch-exit
  accuracy. Tonight's shadow log is the first live read, graded on Friday. NBA first, then NFL.
