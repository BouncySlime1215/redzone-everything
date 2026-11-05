# Cycle 09 plan (Start Planner), Thu Nov 5, ~00:15 to 01:15 UTC (60-min rhythm, a live night)

**From:** the [cycle 07 review](../07/review.md) (f91e318), the Cycle 08 plan (#183), and lane 2's four
pre-registered runs this cycle.

**Queue:** [docs/experiments.md](../../experiments.md). Every PR names its EXP id and pre-registers its
`success bar` and `nights needed` before the first run (grading rule 2, #191). Rules as always: bar (B) on
locked, flags off until they pass, no training signal from Nick, `docs/citymap.md` first, and the playbook's
line (§5) is binding.

## The finding of this cycle: sync σ is the bottleneck

Lane 2 ran four honest experiments, each pre-registered, and all four failed their bars. All ship off:
- EXP-018 (TREE-6, scored on the old score);
- EXP-020 (break-owed countdown);
- EXP-029 (fill breaks);
- EXP-083 (`break_destination_min` 0, which did not replicate on fresh seeds).

EXP-029 and EXP-026 fail for the same measured reason. With the honest sync σ ≈ 9 s, the leave and
return-trip gates are closed almost all the time. While a missed play is 2-3 min old, a catch-up is even
considered on only 67 of 1017 NHL ticks and 525 of 3594 NFL ticks. Most fillable NBA dead air is in halftimes
that the return-trip check blocks.

**So the top new item is EXP-084: nightly sync self-calibration (#140, lane ③).** Its bar is a per-stick delay
error p95 ≤ 2 s on live-night logs. Once it passes, lane ② re-runs EXP-026 and EXP-029 against their
existing frozen bars.

## Status
- **Scoreboard (cycle 07 review, candidate):**
  - % of oracle: 65.7 dev / 67.5 locked (live 48.5 / 44.5).
  - Mid-play entries: 0 / 0.
  - Dead air: 19.1 / 21.4.
  - Your-team big ≤3 min: 87.1 / 82.4.
- **Bar (B):** nothing promoted.
  - (a) passes.
  - (b) NHL locked crunch: 79.2 vs live 85.5 over 2 nights, within the CI. NBA and NHL need 8 locked nights
    (v3, Friday).
  - (c) NFL your-team big seen: 20/21 vs 21/21. The ≤10-min catch-ups fail on both profiles.
- **RC6.1** is cut from acd213b (#132 + #173 + #188 + #193). Tonight is the first chance for gold data
  (EXP-008). Lane ③ reports in the morning.
- **Grading:**
  - #191 (graders job, G1-G3) is ready for review.
  - The integrator owns #184 (decide-step 1 s, a grader change). It shows 10 s and 1 s side by side.
  - Grader v2 adds "commercial within 30 s of landing".
- **EXP-005:** propensity logging and exploration are built in #186, with exploration off until v3 (Nick's
  limits).
- **Merge order (integrator):** #183 → #189, then #180, #160, #154, #171, #166, #169, then lane ⑤'s stack
  #170 → #177 → #178 → #185 → #190.

## Data ledger (unchanged; #169 not merged yet)
| league / season | games | events | tokens | crunch-event share | OT / comeback / blowout games |
|---|---:|---:|---:|---:|---|
| NBA 2025 | 1,324 | 625,000 | 3.76 M | 3.6% | 66 / 367 / 276 |
| NFL 2019 | 267 | 45,209 | 0.27 M | PENDING | PENDING |
| NHL, CFB, CBB | PENDING | | | | |
| MLB | skipped (Nick) | | | | |
| synthetic (self-play) | PENDING | | | | |
| live nights (gold) | tonight, if RC6.1 is healthy | | | | |

CFBD is waiting on Nick's key, which he adds as a Kaggle secret himself. The real corpus estimate is
~0.7-0.9 B tokens.

## ① Noggin training (unchanged; Phase 0 is not landed yet)
1. **EXP-002 → EXP-001:** WP calibration (ECE ≤ 0.05), then S as a WV input, flag off.
2. **EXP-006 V8:** ~2.5 GPU-h via the overseer. Judge it by lift over the context-only baseline.
3. **EXP-003 + EXP-007:** the one-event-language spec with lane ④; shards in the next factory pack.
4. **EXP-019:** free break labels from wall-clock gaps. They also feed EXP-084's break anchors.

## ② Noggin logic / sync
1. **Bar (c) on dev only:** find the NFL your-team miss pattern **on dev nights**, never by reading the locked
   miss. Pre-register a new EXP row with a bar of 21/21 dev seeds.
2. **EXP-024:** conformal risk control on entries, with an abstain option. Report the stated bound and its
   coverage on dev.
3. **EXP-028:** a dwell / hysteresis sweep, offline only. Find the ping-pong knee without losing crunch.
4. **When EXP-084 passes:** re-run EXP-026 and EXP-029 against their frozen bars. The flags are already built.

## ③ Performance + controls
1. **EXP-084 (#140): sync self-calibration.** This is the cycle's unblocker. Fit the YTTV seek and the
   per-channel delay from tonight's RC6.1 logs (MediaSession bursts, #146, as anchors). Bar: per-stick delay
   error p95 ≤ 2 s.
2. **EXP-008:** the RC6.1 night. Confirm the switch rows, provenance and STK counts, and report the first gold
   night in the morning.
3. **EXP-004:** the Mac half of shadow mode (≤ 150 MB extra RSS, 0 TV commands).
4. **EXP-036:** the commit command path (#194). The #171 rig check runs after tonight, once Nick OKs it in
   the Mac session.

## ④ App + integration hardening
1. **EXP-001, scorecard half:** a "suspense at entry" info line in `northstar.py`.
2. **EXP-004 + EXP-005, scoring half:** a morning shadow score next to the #137 night report, and the doubly
   robust estimator reading #186's propensities.
3. **EXP-003:** the one-event-language contract with ①. Land #179 and #181 with 0 new violations.
4. **EXP-017:** the v3 locked switch on Friday, reporting the NBA and NHL night counts.

## ⑤ Features (display only, flags off)
1. Land the stack #170 → #177 → #178 → #185 → #190 with tests.
2. **EXP-038:** a 2-3 s orientation beat on each switch, as a corner overlay that never covers play.
3. **EXP-039:** a quiet "also close" strip.
4. **EXP-040:** a one-tap "worth it?" rating on the Slate-complete screen. It is **evaluation only**, never a
   training label.
