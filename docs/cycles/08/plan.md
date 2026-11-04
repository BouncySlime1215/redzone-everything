# Cycle 08 plan (Start Planner), Wed Nov 4, ~23:15 to 00:15 UTC (60-min rhythm)

**From:**
- the [cycle 06 review](../06/review.md) (020c778);
- integration/next after the Cycle 07 plan (#176) merged;
- Nick's 6:10 PM decisions.

**Official direction** (Nick, 6:10 PM):
- The [Noggin master plan](../../plans/2026-11-04-noggin-master-plan.md) and the
  [watch science playbook](../../research/watch-science-playbook.md) are now the direction. Both are on
  `codex/resume-redzone` at 51908ba.
- All ~100 ideas are now ONE ranked queue: **[docs/experiments.md](../../experiments.md)** (82 rows, with a
  coverage check).
- Phase 0 starts now.

**Rules as always:**
- bar (B) on locked, flags off until they pass;
- no training signal from Nick;
- read `docs/citymap.md` first;
- every PR shows its North Star delta, a RAM/CPU line and **its EXP id**.

**The line is binding** (playbook §5): the objective is "worth it", not watch time. No betting features, no
artificial suspense, no autoplay or pull-back nudges, no streaks or time counters, and no loaded wording in
commits or docs.

## Status
- **RC6 is ordered now:** #132 (YTTV seek, contains #133) + #173 (gold-data capture). Everything else stays
  flag-off.
  - Deploy with `deploy.py` (full tests, health watch, auto-rollback) **before the 6:50 PM ET tip-off**.
  - If it isn't healthy by 6:50, stay on RC5.1 and capture gold data the next night.
- **#171 rig check is approved:** the Mac service may stop for ≤ 10 min, now, and must restart before RC6
  deploys (lane ③).
- **Statcast is skipped** (Nick). The MLB row stays empty, and MLB is last and out of season.
- **Bar (B):** nothing promoted (cycle 06 review).
  - (a) passes.
  - (b) is blocked on evidence: locked has NHL 2 / NBA 4 nights.
  - (c) is the one behavior blocker: your-team big ≤3 min is 73.5 vs live's 82.4. #180 is the fix (dev NHL
    your-team 19/21/18 → 20/21/20).
  - v3 locked (8 NBA + 8 NHL nights) arrives Friday.
- **City map:** contract_violations 477 (0 new). #179 (one brain step 7: typed contracts + WorldState shadow)
  and #181 (step 5: one BreakBook) are up.
- **Lane ⑤:** #170 → #177 → #178 are stacked (display only, Δ 0).

## Data ledger (unchanged since cycle 07; #169 not merged yet)
| league / season | games | events | tokens | crunch-event share | OT / comeback / blowout games |
|---|---:|---:|---:|---:|---|
| NBA 2025 | 1,324 | 625,000 | 3.76 M | 3.6% | 66 / 367 / 276 |
| NFL 2019 | 267 | 45,209 | 0.27 M | PENDING | PENDING |
| NHL, CFB, CBB | PENDING | | | | |
| MLB | skipped (Nick, 6:10 PM) | | | | |
| synthetic (self-play) | PENDING | | | | |
| live nights (gold) | first one tonight if RC6 is healthy | | | | |

- The estimated real corpus is **~0.7-0.9 B tokens**.
- CFBD is waiting on Nick's key, which he adds as a Kaggle secret himself.
- **The event-token shards go in the next factory pack** (EXP-007).

## Integrator
1. **RC6:** #132 + #173 into integration/release, then deploy before 6:50 PM ET (the overseer's order).
2. Then: #180 (bar c), #160, #154, #174, #171.
3. Then: #166 (WV + crunch_state, which Phase 0's S feeds), #169 (NFM shards), #172, and lane ⑤'s stack
   #170 → #177 → #178.
4. The cycle 07 review at ~23:35 UTC, or at the next big pass. Read tonight's live night once it's in.

## Phase 0 assignments (from docs/experiments.md)
| EXP | what | lanes |
|---|---|---|
| 001 | Suspense `S = E[(ΔWP)^2]`: a scorecard line + a WV input (flag off) | ① S, ④ line, ⑤ why text |
| 002 | WP calibration per league (the gate for 001) | ① |
| 003 | One event language spec as a typed contract, matching #169's vocab | ① + ④ |
| 004 | Nightly shadow mode | ③ Mac half, ④ scoring half |
| 005 | Off-policy grading (propensity logging + a doubly robust estimator; randomization on hold) | ② logging, ④ estimator |
| 006 | V8 Mind (crunch-stay + NBA leak audit), ~2.5 GPU-h | ① |
| 007 | Event-token shards in the next factory pack | ① |

## ① Noggin training
1. **EXP-002 → EXP-001.** Calibrate the per-league WP models on locked: reliability curves and ECE, including
   OT and garbage time; a league that misses keeps S off. Then compute `S = E[(ΔWP)^2]` at each dead ball
   with the concave late multiplier, and add it as a WV input behind a flag. Gate: WV rank agreement on
   locked beats #166's (NBA 74.2, NFL 58.6). Line: Crunch live, Big plays seen live, % of oracle.
2. **EXP-006 V8 Mind** when ready: the crunch-stay fix plus the NBA leak audit. ~2.5 GPU-h is left this week,
   and the kernel goes to the overseer. Judge it by lift over a context-only baseline.
3. **EXP-003 + EXP-007.** Write the one-event-language spec with lane ④: possession, stoppage, score, clock
   and break primitives, mapped to #169's vocab ids, with < 1% unmapped events per league. Ship the shards
   in the next factory pack.
4. **EXP-019:** wall-clock gaps between plays as free break labels.

## ② Noggin logic / sync
1. **EXP-010 / #180:** your-team catch-ups. Get it to locked your-team big ≥ live's 82.4 with crunch-in-replay
   not worse, so bar (c) can pass.
2. **EXP-005, logging half:** the decider logs the option set and a propensity for every decision, in the
   #173 trace. It is deterministic today, so the propensity is 1.0. **Do not** randomize near-ties: that
   stays on hold for Nick.
3. **EXP-018 TREE-6 Mac half** with WV (#166): jump / stay / interleave / catch-up-on-return, reusing #171's
   hold. Marked test: Bills / Bears.
4. **EXP-020:** the rulebook "break owed" countdown (NBA, CBB, NHL TV timeouts).

## ③ Performance + controls
1. **#171 rig check now** (≤ 10 min of service stop, approved), then restart the service before RC6 deploys.
2. **RC6 night:** watch the health checks, confirm #173's switch rows, provenance and STK-05/06/07 counts land
   in tonight's logs, and report the first gold night in the morning (EXP-008).
3. **EXP-004, Mac half:** shadow mode runs the candidate decider silently beside live (≤ 150 MB extra RSS, no
   TV commands, flag off). Measure RSS and CPU on a replayed night first.
4. #145 GO NOW real p95; EXP-021 MediaSession bursts as dead-ball labels (#146).

## ④ App + integration hardening
1. **EXP-001, scorecard half:** add an info line "suspense at entry" to `scripts/northstar.py` (dev + locked),
   next to entries_mid_play. Use the WP model as-is until ① ships calibrated S.
2. **EXP-004 + EXP-005, scoring half:**
   - score the shadow candidate each morning next to the night report (#137);
   - write a doubly robust off-policy estimator over the logged nights (it reads ②'s propensities);
   - validate it against the sim on the night-lab nights.
3. **EXP-003:** the one-event-language contract in `redzone/contracts/` with ①. Keep contract_violations at
   0 new; land #179 and #181.
4. **EXP-017:** the v3 locked switch on Friday. Report the NBA/NHL locked night counts.

## ⑤ Features (display only, flags off)
1. Land the stack #170 → #177 → #178 with endpoint tests.
2. **EXP-035:** a "why we're here" tag tied to a logged threshold (watch_why + S once ① ships it). Measure the
   "alerted but nothing happened" rate. No banners or stings on routine moments.
3. **EXP-036:** commitment controls ("stay here", "only my teams", "stop after this game", "end at 11:30",
   each waiting for a dead ball), plus a "Slate complete, 0 live games" state with no filler after it.
4. **EXP-037:** a one-question context line ("Down 3, 1:12 left, no timeouts").

## Answered by Nick
- EXP-005 randomization: **yes, with limits** (7:21 PM ET). It is held until Friday's v3 locked set lands. The
  limits:
  - behind `decider.explore.enabled`, off by default;
  - only between options that are both already safe and within a small value margin;
  - at most 5% of decisions;
  - never in crunch, down the stretch, your-team moments or the catch-up queue;
  - a propensity is logged on every decision;
  - it goes through the normal promotion bar.

  Lane ② owns it.
