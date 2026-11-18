# Cycle 11 plan (Start Planner), Thu Nov 5, ~02:00 to 03:00 UTC (60-min rhythm)

**From:**
- the [cycle 08 review](../08/review.md) (ccc6cfd);
- Nick's 9:15 PM directive: every lane takes its next ranked experiment **now**, without waiting on merges;
- the [compressed 4-day plan](../../plans/2026-11-05-compressed-4day.md).

**Rules:**
- Pre-register in [experiments.md](../../../experiments.md) using your lane's id block.
- Before you start an id, check for a `claude/exp-<id>` go-wide branch.
- Grader v5 is coming (`--real-seek`, decide-step 1 s, commercial-within-30 s, ping-pong, worth line). Report v4
  and v5 side by side until it is frozen.

## Status
- **Merged since cycle 10:** #189 #180 #171 #170 #177 #145 #183 #200 #202 #191 #140 #146 #158 #188, plus Nick's
  per-league promotion rule (`release_gate.league_promotion`, grader freeze v4). Behavior lines are unchanged
  (everything is flag-off).
- **Rule #1:**
  - the NHL candidate's 1-2 mid-play entries were a harness artifact (fixed in grader v5 with `--real-seek`);
  - the NFL live entries onto unknown landings are fixed by EXP-089 (#212, passed and replicated).
- **Per-league promotion:** EXP-203 (an NFL-passing global settings set) **passed dev + locked** (#216, docs only).
  Lane ② is grading an NBA-only candidate in 4 arms. It goes to the integrator's promotion step, not straight to
  the Mac.
- **Compute (overseer, 9:15 PM):**
  - the V8.1 watchdog (EXP-101) is on an L4, ~1.2 h, ~6 CU;
  - the A100 V8 game reasoner is at step ~1750/7862, ~3.3 h left;
  - the duplicate T4 run is shut down;
  - the EXP-100 world-model pilot is running.
- **RC6.1:** the tests are green. The switch is waiting on Nick to paste lane ④'s command (Mac guard).

## Data ledger (cycle 11)
| Source | Wave | Verdict | Status | Seasons held | Values held | Nick action needed |
|---|---|---|---|---|---:|---|
| NBA pbp (ESPN 2025) | 1 | green | done | 2025 | 3.76 M tokens | none |
| nflverse pbp | 1 | green | **pulling** (lane ①, 1999+) | 2019 | 0.27 M tokens | none |
| NHL api-web + shifts | 1 | green | **pulling** (lane ①) | none | | none |
| Retrosheet | 1 | green | **pulling** (lane ①) | none | | none |
| MoneyPuck, BDB, Wikimedia pageviews, media-timeout tables | 1 | green | planned | none | | none |
| CollegeFootballData | 2 | yellow | blocked | none | | **key as a Kaggle secret** |
| Synthetic | n/a | cache | planned (factory re-run Thu) | n/a | | none |
| Live nights (gold) | n/a | own | blocked until RC6.1 switches | 0 | | paste lane ④'s command |

## Next ranked experiment per lane (start now)
| lane | now | then | then |
|---|---|---|---|
| ① | **Wave 1 ingest** (nflverse 1999+, NHL shifts, Retrosheet) into the next factory pack, with ledger rows filled | **EXP-086 Teacher 32B** notebook (self-contained, pinned SHA), ready for Thursday's A100 slot | **EXP-022 point-process timing** notebook for Friday's L4; EXP-019 break labels (dev) in between |
| ② | **EXP-023 belief tracker** in the decider (with ③): due Fri | the NBA-only per-league candidate (EXP-200s) through the promotion step | **EXP-056** planner v1 decider interface (Sunday) |
| ③ | **EXP-084** sync calibration: #140 is merged, so run the fit on tonight's logs (or RC5.1's) and report the p95 | **EXP-021** MediaSession bursts as dead-ball labels (#146 merged) | **EXP-004** Mac shadow (#187) ready for Sunday NFL |
| ④ | **EXP-003** one-event-language contract (due Thu) | **EXP-005** DR estimator over #186's propensities + the morning shadow score | **EXP-017** v3 locked on Fri; **EXP-032** red-team night hunters if time allows |
| ⑤ | **EXP-038** orientation beat (corner, never covering play) | **EXP-039** "also close" strip | the catch-up order display for EXP-026's re-run (once EXP-084 passes) |
| Int | grader v5, pre-registered and frozen | merges: #212 → #214, #160, #154, the lane ⑤ stack, #206, the docs fails, #213, #203 | the per-league promotion read on EXP-203 and the NBA-only arm |
