# Cycle 08 review (End Planner / Integrator), Thu Nov 5, 23:55 to 01:15 UTC

**Plan:** [#183](../../../experiments.md) and the compressed 4-day plan (#200).

**Scored at:** the train on integration/next (ac0db65 tree), with `scripts/northstar.py --split both --profile candidate
--oracle`, on eval-data-v1, pinned. Locked was read once and not used to tune anything.

**Grader freeze:** v4 (`docs/grader_freeze.json`).

## Verdicts

| lane | PR | EXP | verdict | note |
|---|---|---|---|---|
| Plan | #183 queue, #200 4-day plan | n/a | **keep (merged)** | `success bar` and `nights needed` columns added; G2 passes |
| ① | #202 data catalog | D1-D5 | **keep (merged, with a fix)** | Fix b5e63d4 removed Nick's overrides as labels or weights (self-taught rule) and made betting data a hidden check only |
| Int | #191 grading rules + `graders` CI job | rules 1-2 | **keep (merged)** | Freeze v1 → v2 (WCBB out: `NICKS_LEAGUES`) |
| ② | #189 crunch vs crunch by value | EXP-018 | **keep, ships off** | Recorded fail; flag off; Δ 0 on every line |
| ② | #180 your-team catch-up alone | bar (c) | **keep, flag off** | Δ 0 on the candidate profile as scored |
| ③ | #171 hold the left game | STK-07 | **keep, flag off** | |
| ③ | #145 press probe | n/a (grader) | **keep (merged), grader v3** | The press-to-TV p95 row is graded on real presses (≥ 20). A miss is FAIL. Until then it stays PENDING. |
| ⑤ | #170 provenance trace, #177 night views | n/a | **keep, phone.v2 off** | Display only |
| Int | per-league promotion (Nick, 8:20 PM) | n/a (grader) | **merged, grader v4** | `release_gate.league_promotion`: 0 mid-play entries in the league on dev + locked, no line worse, at least one better |
| ② | #160, #154 | SAFE-09, cut rules | **next** | Code conflicts with this train; lane 2 is merging next into both |
| ② | #184 decide-step 1 s | grader v5 | **integrator rebuild** | Conflicts with #132's position and real-seek ticks; I'll rebuild it on the current base and pre-register it |

Full suite on the train: **2425 passed**, 15 skipped. Kernel: 477 (0 new).

**Self-taught check:** nothing merged reads Nick's taps, votes or overrides. #202's two violations were removed before the merge.

## Scoreboard
**Every line is identical to [cycle 07](../07/review.md).** Everything merged was flag-off or display-only. % of oracle stays
65.7 dev / 67.5 locked, and candidate mid-play entries stay at 0.

## Rule #1 findings this cycle (measured reasons)

- **NHL candidate, mid-play 0 → 1-2 per seed** (lane 2's bisect: 7d1f36b, `cue=hidden`). This is a **harness artifact**.
  - Without `--real-seek`, the sim doesn't land hidden rewinds the way the app sends them. With it, the count is 0.
  - **Grader v5:** `--real-seek` on every card, for both profiles, with v4 and v5 shown side by side.
- **NFL live, North Star card: 0 → 3 entries in 1155 landings** (acd213b vs 05fcd01). This is a **live-path gap** that #132 exposed.
  - All three are straight entries that `plan_entry` accepted on an "unknown" landing (σ 9 s).
  - **EXP-089 (#212, flag off)** brings it to 3 → 0 on that card, and from 8 to 1 on fresh seeds 4-6.
  - Overall live mid-play entries are unchanged (27/1155 vs 26/1060), so RC6.1 stands.

## Promotion step
**Bar (B), all leagues:** nothing changed since cycle 07, so the result is the same. Nothing promoted.

**Per-league (new):** an NBA-only candidate for RC6.1 is being graded by lane 2 in 4 arms. Only
`{"noggin.timing.leagues": ["nba"]}` is truly NBA-only on the live Mac. The other keys are global, so they need NHL to pass
too.

## For cycle 09
- **Merges:** #212 → #214, #160, #154, the lane 5 stack (#178 #185 #190 #205), #206 (EXP-001 suspense line, grader), #140,
  #146 (grader), then the docs-only fails #196 #198 #199 #201 #207.
- **Grader v5 (pre-register first):**
  - `--real-seek` on every card;
  - decide-step 1 s (#184 rebuilt);
  - "commercial within 30 s of landing" (EXP-020);
  - ping-pong A→B→A within 120 s (EXP-028);
  - worth line (#205).
- **RC6.1** waits for Nick's paste, or for after the last game.
