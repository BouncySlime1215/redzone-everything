# Cycle 06 review (End Planner / Integrator), Wed Nov 4, 21:25 to 22:35 UTC

**Plan:** [plan.md](plan.md). **Scored at:** integration/next 8cb0f6e, `scripts/northstar.py --split both --profile
candidate --oracle`, eval-data-v1, pinned. Locked was read once for this review and not used to tune anything.

## Verdicts

| lane | PR | verdict | against "what we're looking for" |
|---|---|---|---|
| ④ | #165 city map + contract kernel, #168 one-brain steps 1-4 | **keep (merged together)** | `docs/citymap.md/json` (250 modules), the CI kernel in report mode, and timing constants moved into config with values unchanged. The one-brain steps run at exact parity: every scorecard and North Star line is unchanged. 2334 tests pass. The kernel reads **477 violations, 0 new** (baseline ratcheted 498 → 477). lane 4's f83137c / 7e42aa2 come with it. |
| Int | #175 contract line | **keep (merged)** | `contract_violations` reads the kernel: 477 FAIL (K1 141, K2 275, K3 34, K4 21, K5 2, K6 4). It was PENDING before. |
| ③ | #167 unheld-standby watchdog + drift fault, #164 YTTV dialog BACK | **keep (merged)** | Unblocks the lane 2 stack: `test_e2e_buffer` is green. Scorecards are unchanged. |
| ④ | #163 playoff stakes | **keep (merged), flag off** | Data only (`score.postseason.enabled: false`). WV wiring went to lanes 1 and 2. |
| ⑤ | #161 watch strip (+#155) | **keep (merged), phone.v2 off** | Typed Scored fields: `watch_value`, `watch_why`, `crunch_state`, `stakes_line`. Display only. |
| Plan | #162 cycle 06 plan | **keep (merged)** | |
| ② | #132 (contains #125, #131 flag-off, #133) | **next, before tonight** | CI re-running after the merge with next. #125's body numbers get checked against CI's North Star comment at merge (overseer's call). |
| ② | #172 stakes into WV, #174 crunch_block_tail | **next** | #172 is rebased (import-line conflict), and its CI is re-running. #174: NHL locked crunch +2.5 per seed, one seed's your-team 11/13 → 13/13, NHL seen-10-min −5 dev (stated at the top). |

**Self-taught check:** nothing merged this cycle reads Nick's taps, votes or overrides.

## Scoreboard
**Every behavior line is identical to cycle 05.** Everything merged this cycle was flag-off, display-only or exact parity, so the
[cycle 05 table](../05/review.md) stands: % of oracle **65.0 dev / 66.4 locked** (live 49.6 / 43.3), mid-play entries 0
for the candidate, and big live NFL 53.9 / 51.1 and NHL 44.9 / 48.6, still the worst lines. New this cycle: **contract violations 477** (gate 0).

## Promotion step (bar (B)): CI on every thin locked line

| line (locked) | live | candidate | night-bootstrap 95% CI (candidate) | nights | reading |
|---|---:|---:|---|---:|---|
| NHL crunch live | 89.4 (CI 80.6-100) | 76.2 | 58.1-98.1 | 2 | equal within CI, but (b) needs PASS: **blocked on evidence** |
| NBA crunch live | 73.7 (CI 61.8-100) | 94.4 | 92.0-100 | 4 | better than live, still PENDING (needs 8 nights) |
| NBA big live | 62.5 | 100 (16/16) | 100-100 | 4 | PENDING (needs 8 nights / 60 decisions) |
| Your-team big ≤3 min | 82.4 (CI 75.0-87.8) | **73.5** (25/34) | 61.1-88.0 | 4 | overlaps, but (c) needs PASS: **behavior blocker** (NFL 19/21 vs 20/21; NHL ≤10 min 11/13 vs 13/13) |
| Your-team crunch | 100 | 100 (34/34) | 100-100 | 3 | PENDING (needs 8 nights) |

Release gate on locked: live 10 pass / 13 fail / 18 pending; candidate 11 / 8 / 22 ([live](gate-locked-live.md),
[candidate](gate-locked-candidate.md)). The crunch floor test passes on every profile and overlay.

**Promoted this cycle: nothing.** Bar (a) passes. Bars (b) and (c) can't pass yet: NBA and NHL have only 2-4 locked nights,
and those lines need 8 to read PASS at all. Your-team catch-ups are the one real behavior gap, and #174 closes part of it.

**eval-data-v3 (#149, Fri Nov 6, lane 4):** **8 locked nights each for NBA and NHL** (Oct 29 to Nov 5), so those lines
can read PASS for the first time. Crunch-eligible nights are fewer, about 1 per 4 archived, so NHL crunch may still be thin.

## For cycle 07 (Start Planner's #176)
- **Merges:** #132 (one merge lands #125, #131, #133) → #160 → #154 → #174 → #171 → #166 → #169 → #173 → #172.
  Tonight's seek fix reaches the Mac only through an RC cut (lane 4, on Nick's order).
- **Lane ②:** your-team catch-ups → 0 missed (bar (c)). NHL whistle gap: the leave gate reads "play is live" for 7+ min.
- **Lane ④:** ratchet contract violations down from 477. K2 (275) is the biggest block.
- **For Nick to close (blocked for this session):** #157 (superseded by #165), #112 and #113.
