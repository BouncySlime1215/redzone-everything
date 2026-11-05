# Cycle 12 plan (Start Planner; corrected 03:05 UTC), Thu Nov 5, ~03:00 to 04:00 UTC (60-min rhythm)

**From:**
- integration/next 777323f;
- the handoff on codex/resume-redzone (99131b3, 21:26);
- go-wide PRs #216-#231;
- the [compressed 4-day plan](../../plans/2026-11-05-compressed-4day.md).

**Rules:**
- Pre-register in [experiments.md](../../experiments.md) using your lane's id block.
- Check for a `claude/exp-<id>` branch first.
- Graders are frozen at v9.

## Status
- **RC6.1 is live (resolved).**
  - The 9:25 PM auto-rollback was caused by a benched faster-feed source reporting `ok:false`.
  - The overseer redeployed RC6.1 at 9:28 and again at 9:40 PM ET with a one-off wrapper. Lane ④'s real fix is
    #232.
  - **Tonight can still be a gold night from 9:40 PM** (EXP-008).
- **RC7: Nick's three hard requirements.** The earliest RC7 is Thursday night.
  1. Switches ≤ 3 s.
  2. Sync p95 ≤ 2 s.
  3. Never join at a break (no landing into a commercial).
- **Merged this cycle:**
  - rule #1: #212 (EXP-089, unknown landings rewind or wait) and #214 (EXP-024 conformal floor: NFL bound
    0.0012 vs α 0.005; NBA and NHL PENDING);
  - cut rules: #160 and #154;
  - lane ⑤: #178, #185, #190, #205 (the worth line, frozen as grader v9);
  - lane ①'s rows: #220;
  - recorded fails: #201 and #207.

  All are flag-off.
- **RC7** is planned for Thursday before TNF, with per-league promotion as league-scoped defaults (graders v7/v8).
  EXP-203 (NFL-passing set) passed dev and locked, and EXP-204 (#231) proposes a WP cap for your team's
  "late and close".
- **Go-wide (the overseer's parallel runs):**
  - #218 EXP-021, #223 **EXP-023 belief tracker**, #225 EXP-027, #226 EXP-032, #228 EXP-025, #231 EXP-204;
  - lane ⑤ has #221, #222 and #224.

  Lanes don't start these ids.
- **Compute (handoff 21:26):** V8 game reasoner on the A100 at 2550/7862; V8.1 watchdog on the L4 at 600/1688;
  the factory is running; EXP-100 pilot PASS. Usage is 42% of the weekly limit.
- **Waiting on Nick:**
  - the A/B data call (gates EXP-086's CBB/NHL/MLB Teacher pass; EXP-102 is the green fallback);
  - whether to accept the NHL api-web bulk risk;
  - the CFBD key;
  - a TV overlay path for EXP-038 (not blocking: phone-only for now).

## Data ledger (cycle 12)
| Source | Wave | Verdict | Status | Seasons held | Values held | Nick action needed |
|---|---|---|---|---|---:|---|
| NBA pbp: ESPN 2025 + NBA.com cdn | 1 | green | done (2025) / **ingest in #169** (2020-25 cdn) | 2020-25 | 3.76 M tokens (2025) + PENDING | none |
| nflverse pbp | 1 | green | **ingest in #169** (1999+) | 1999-2025 | PENDING the pack | none |
| Retrosheet | 1 | green | **ingest in #169** (1974-2024) | 1974-2024 | PENDING the pack | none (notice text) |
| NHL api-web + shifts | n/a | cautious ("not using" in the catalog) | **held** | none | | **accept the risk or keep it off** |
| MoneyPuck, BDB, Wikimedia pageviews, media-timeout tables | 1 | green | planned | none | | none |
| CollegeFootballData | 2 | yellow | blocked | none | | the key as a Kaggle secret |
| ESPN site API (CBB/NHL moments) | 2 | red for training | held | none | | the A/B call |
| Synthetic | n/a | cache | factory running | n/a | | none |
| Live nights (gold) | n/a | own | **tonight from 9:40 PM ET** (RC6.1 live again) | 0 so far | | none |

## Next ranked experiment per lane (start now)
| lane | now | then | then |
|---|---|---|---|
| ① | **Land #169** (the wave 1 ingest) and push the factory pack with event-token shards (EXP-007) | the **EXP-022** point-process notebook, ready for Friday's L4 (pinned SHA, grades.json) | **EXP-102** green moment builder (nflverse + cdn) so the A100 slot runs under either data plan; EXP-087 after V8 finishes |
| ② | **RC7 requirement 3, never join at a break:** make sure every entry path (EXP-089's rewind/wait included) refuses a landing in a commercial, measured by grader v5's commercial-within-30 s line. Then **review and wire #223** (go-wide EXP-023 belief tracker) into the decider behind its flag, and fold in your own design. Don't start a second tracker | the **RC7 per-league defaults**: EXP-200/201/203 with the integrator (#216), and EXP-204's cap (#231) | **EXP-056**, planner v1's decider interface (Sunday) |
| ③ | **RC7 requirements 1 + 2:** confirm tonight's gold capture is landing (EXP-008); then switch p95 ≤ 3 s on the real rig (#145 press probe) and sync p95 ≤ 2 s (**EXP-084** on tonight's RC6.1 logs) | **EXP-004** Mac shadow (#187) for Sunday NFL | EXP-021 review (go-wide #218) |
| ④ | **land #232** (the benched-feed `ok:false` fix) so RC7 needs no wrapper | **EXP-003**, the one-event-language contract (due today) | **EXP-017** v3 locked (Fri); EXP-005 DR estimator |
| ⑤ | land #221, #222, #224 with tests | **EXP-043**, followed players as a config multiplier (display + config; lane ① owns the weight) | a morning "last night" card: worth-it, mid-play, dead air, and the RC in use |
| Int | **the RC7 cut plan:** per-league defaults plus Nick's 3 hard requirements as gates (switch ≤ 3 s, sync p95 ≤ 2 s, 0 landings into a commercial: grader v5's commercial-within-30 s line) | merge #169, #206, #187, #186, #166, then the go-wide PRs as their verdicts land | promotion read on EXP-203 / NBA-only |
