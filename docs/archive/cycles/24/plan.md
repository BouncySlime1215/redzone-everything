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
