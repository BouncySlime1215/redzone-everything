# Cycle 07 review (End Planner / Integrator), Wed Nov 4, 22:35 to 23:55 UTC

**Plan:** [plan.md](plan.md) (#176).

**Scored at:** the train #172 + #174 + #137 on acd213b (df54d61, same behavior as integration/next 6a66f24), with
`scripts/northstar.py --split both --profile candidate --oracle`, on eval-data-v1, pinned. Locked was read once for this
review and not used to tune anything.

**Experiment ids** are from [experiments.md](../../experiments.md) (#183).

## Verdicts

| lane | PR | EXP | verdict | against "what we're looking for" |
|---|---|---|---|---|
| ② | #132 YTTV seek fix (lands #125, #131 flag-off, #133) | EXP-008 (RC6) | **keep (merged, 815c39a)** | Lane 2's 59ba3ea moved the 10 new K2 timing constants into config with values unchanged, so the kernel reads 477 = baseline. Full suite with #173: 2378 passed, 15 skipped. I checked #125's body against CI's North Star comment: the live profile is unchanged on every line (PR = base). The candidate-only numbers carry lane 2's 20:15 UTC correction at the top, and that correction stands. |
| ③ | #173 gold-data capture (provenance) | EXP-008 | **keep (merged, 5428388)** | One conflict, in `Controller.switch`, resolved by keeping both sides (`_hidden_entry` + `_why`). |
| ④ | #188 `deploy --at-break` | none (ops) | **keep (merged)** | Off without the flag. It fails safe: it defers unless the game on screen shows Halftime, Intermission or an NHL "End of 1st/2nd", and it also defers when no game is on screen. 48 deploy tests pass. |
| ④ | #193 citymap tag rename | none (ops) | **keep (merged, acd213b)** | The nightly table renamed the mid-play line, which broke `test_citymap` on the RC6 cut. Docs only. RC6.1 is cut from here. |
| ② | #172 stakes into WV, #174 crunch_block_tail | EXP-001 (WV input), bar (c) | **keep (merged, 6a66f24)** | #174 lifts locked NHL candidate crunch from 76.2 to 79.2. It also lifts locked your-team big ≤3 min from 73.5 to 82.4 (live moved the other way, 82.4 → 73.5, under #132's seek model). |
| ④ | #137 night report | none (ops) | **keep (merged, 6a66f24)** | It turns `report.nightly` on. It was held until RC6 was cut, so it doesn't ride RC6. |
| Int | #191 grading rules + `graders` CI job | rules 1-2 | **open** | Nick's 14 rules are in [grading.md](../../grading.md). G1 adds the grader freeze, G2 pre-registration, G3 no moved goalposts. It re-freezes after the train lands. |
| ② | #184 decide-step 1 s | none yet | **integrator takes it over** | It's grader code, so under rule 1 it belongs to the integrator. Moving the review protocol from 10 s to 1 s for both profiles is a grader change. It gets pre-registered, then both tables are shown side by side in cycle 08. It isn't switched mid-review. |

**Self-taught check:** nothing merged this cycle reads Nick's taps, votes or overrides.

**Lesson:** after a base merge I ran only the targeted tests, and that let the citymap break reach the Mac's deploy
test step. The deploy stopped there, so nothing switched. The rule now: run the full suite again after any base merge before
handing over an RC SHA (lessons.md).

## Scoreboard (candidate; live in brackets)

| line | dev | locked |
|---|---:|---:|
| Mid-play entries / 100 landings | **0.00** (2.34) | **0.00** (1.48) |
| High-leverage mid-play leaves | 0 (0) | 0 (0) |
| % of oracle (mean NFL/NBA/NHL) | **65.7** (48.5), was 65.0 | **67.5** (44.5), was 66.4 |
| Big live NFL / NBA / NHL | 53.3 / 78.9 / 45.9 | 52.9 / 100 (16/16) / 50.0 |
| Crunch live NFL / NBA / NHL | 68.9 / 90.1 / 82.7 | 70.9 / 94.4 / 79.2 |
| Your-team big ≤3 min | 87.1 (71.0) | 82.4 (73.5) |
| Dead air | 19.1 (21.8) | 21.4 (24.5) |
| Contract violations | 477 (0 new) | 477 |

Most of the live moves come from #132's seek model, which grades live and candidate alike. Live crunch on locked NFL went
59.6 → 53.9 and on NHL 89.4 → 85.5.

## Promotion step (bar (B)): CI on every thin locked line

| line (locked) | live | candidate | nights | reading |
|---|---:|---:|---:|---|
| (a) mid-play entries | 1.48 | **0** | 18 | PASS |
| (b) NHL crunch live | 85.5 (CI 73.6-100) | 79.2 | 2 | below live, within CI: **blocked** |
| (b) NBA crunch live | 73.7 (CI 61.8-100) | 94.4 | 4 | better than live, PENDING (needs 8) |
| (c) your-team big seen, NFL | 100 | **95.2** | 10 | **FAIL**: 20/21 vs 21/21 |
| (c) your-team ≤10 min, NFL / NHL | 19/21 / 11/13 | 19/21 / 12/13 | | both fail the 100% gate |
| (d) other lines | | | | ≥ live on every line, within CI |

Release gate on locked: live 12 pass / 11 fail / 18 pending; candidate 12 / 7 / 22 ([live](gate-locked-live.md), [candidate](gate-locked-candidate.md)). The crunch floor test passes on
every profile and overlay (full suite).

**Promoted this cycle: nothing.** (a) passes. (b) is blocked by NHL crunch (2 locked nights, and the candidate is below
live) and by the 8-night minimum. (c) is blocked by one NFL your-team big play the candidate misses live. eval-data-v3 (#149, Fri) brings 8
locked nights each for NBA and NHL.

## For cycle 08 (Start Planner's #183)
- **Merges:** #183 → #189, then #180 (bar c), #160, #154, #171, then #166, #169, lane 5's #170 → #177 → #178 → #185 → #190.
  #191 re-freezes the graders after each grader change.
- **Lane ②:** close the one NFL your-team big play the candidate misses live (bar (c)), and the NHL ≤10-min catch-up.
- **Integrator:** pre-register the decide-step 1 s protocol (#184), then show the 10 s and 1 s tables side by side. Grader v2 adds "commercial within 30 s of landing", per
  league (lane 2's ask for EXP-020: `landed_dead_pct` reads 100 on every NBA and NHL run, so it can't see this).
- **RC6.1:** lane 4 switches at a halftime tonight, or after the games.
