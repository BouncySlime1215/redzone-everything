# Cycle 05 review (End Planner / Integrator), Wed Nov 4, 19:55 to 21:25 UTC

**Plan:** [plan.md](plan.md). **Scored at:** integration/next 853012f (+ docs #141, #134), `scripts/northstar.py --split
both --profile candidate --oracle`, eval-data-v1, pinned. Locked was read once for this review and not used to tune anything.
From now on, planners run every 60 min or at each big pass, whichever comes first (Nick, 4 PM ET).

## Verdicts

| lane | PR | verdict | against "what we're looking for" |
|---|---|---|---|
| ② | #116 possession-start entry | **keep (merged), candidate** | Entries at a possession start: candidate **100%** dev (298/298) and locked (128/128), against live's 90 / 90. Mid-play entries are 0 on both splits. It is the main reason the locked % of oracle moved 59.1 → 66.4: NBA locked big live 68.8 → 100 (16/16, 4 nights, PENDING). |
| ② | #150 replay.crunch_block | **keep (merged), candidate** | NHL locked crunch plays aired during a replay drop 10 → 4. Live is at 2, so it's still worse than live. |
| ① | #129 possession oracle + policy | **keep (merged), flag off** | The possession-start oracle ceilings come with it (NBA 94.4 / NFL 96.2 / NHL 93.4 dev). The learned policy stays off. |
| ⑤ | #148 your-team crunch clash banner | **keep (merged), phone.v2 off** | Display only, and every North Star line is unchanged. #155 (watch value sheet) is next once it is rebased. |
| Int | #156 North Star split + crunch floor | **keep (merged)** | Nick 3:15 PM: "Mid-play entries" (0), "high-leverage mid-play leaves" (0; close + late or OT, `redzone/lab/leverage.py`) and "low-leverage leaves" (info). **Today nothing leaves mid-play at all:** 0 leaves on every split and column, so the old line was entries only. It adds the Heat 2.0 (`wv_*`), `contract_violations` and `crunch_exit_acc` rows, all PENDING until their builders land. `tests/test_crunch_floor.py` holds the crunch floor in the promotion step: no overlay may leave live crunch where the live default stays, and a low-value game never pulls us out of crunch. decisions.md has bar (B), the cut rules and Nick's 4:15 PM "crunch is a learned state". |
| Plan | #141 cycle 05 plan, #134 overseer backlog | **keep (merged)** | Docs only. |
| ② | #146 burst anchor, #132 (+#133) YTTV seek, #131, #125 | **hold** | Red on `test_a_drifting_hold_is_rebuilt`. The shifted night drifts a standby that was tuned by a failed switch and never held, so the watchdog has nothing to judge. That's a real gap on next (a failed "tuned" switch leaves the hidden stick at the live edge). Lane 3 fixes it, or the test drifts a held stick. No skip. |
| ④ | #163 playoff stakes, #165 city map | **next in line** | Both reviewed: flag off / report mode. CI is running. #165 needs lane 4's f83137c (leverage.py reads `models.PERIOD_S`) for its contract step. |

**Self-taught check:** no PR merged this cycle reads Nick's taps, votes or overrides. The new night report code lists them under `never`.

**Highlight jump `jump_when_it_fits` (overseer's promotion check): not promoted** (decisions.md). The sims never play a
reel, so there is nothing on locked to gate it with. Its 99.8% is on the distill pack's test split, which excludes locked. And 99.8% is not
100%: about 1 return in 500 lands mid-play, which is a JOIN.

## Scoreboard: live default vs candidate (eval-data-v1, pinned)

| line | gate | dev live | dev cand | locked live | locked cand | cand gap to 100% of oracle (dev / locked) |
|---|---|---:|---:|---:|---:|---|
| Mid-play entries / 100 landings | 0 | 2.45 | **0 PASS** | 1.75 | **0 PASS** | |
| High-leverage mid-play leaves | 0 | 0 PASS | 0 PASS | 0 PASS | 0 PASS | (low-leverage leaves: 0 everywhere) |
| Landings in dead time | ≥98% | 97.5 FAIL | 100 PASS | 98.3 PENDING | 100 PASS | |
| Entries at a possession start | ≥95% | 90.4 | **100 PASS** | 89.6 | **100 PASS** | |
| Big live NFL | ≥95% of oracle | 25.2 | 53.9 (59.1%) | 23.3 | 51.1 (55.1%) | 41 / 45 pts |
| Big live NBA | ≥95% of oracle | 67.6 | 77.5 (83.3%) | 62.5 | **100** (16/16, PENDING) | 17 / 0 pts |
| Big live NHL | ≥95% of oracle | 41.4 | 44.9 (52.6%) | 38.9 | 48.6 (54.1%) | 47 / 46 pts |
| Crunch live NFL / NBA / NHL | ≥95% | 62.2 / 76.7 / 79.7 | 66.2 / 90.0 / 82.8 | 59.6 / 73.7 / 89.4 | 69.1 / 94.4 / **76.2 ↓** | |
| Big seen ≤3 min (≤2 min) | ≥99% | 49.7 (46.0) | 66.1 (61.8) | 47.4 (43.2) | 68.2 (64.0) | |
| Your-team big ≤3 min | 100% | 64.5 | 80.6 | 82.4 | **73.5 ↓** | |
| Dead air | 0 | 22.5 | 19.3 | 26.7 | 21.1 | |
| **% of oracle** (mean) | ≥95% | 49.6 | **65.0** | 43.3 | **66.4** | 35 / 34 pts |
| press_to_tv p95 (sim) | ≤3 s | 8.75 | 8.75 | | | |
| Crunch exit accuracy / WV calibration / rank / contract violations | ≥95% / TBD / TBD / 0 | PENDING | PENDING | PENDING | PENDING | |

**Honest reading:**
- % of oracle moved for the first time since cycle 03: dev 64.8 → 65.0, locked 59.1 → **66.4**. Nearly all of it is #116 on NBA locked (4 nights, so it's thin).
- Big live is still the worst line in NFL and NHL, 41-47 points short of oracle. The levers that would move it (#125 honest sync, #146 burst anchor, #132 seek) are all held on one red e2e test.
- Locked your-team big fell 79.4 → 73.5 (27 → 25 of 34). NHL your-team catch-up within 10 min is 11/13 vs live 13/13. That's new this cycle and owned by lane 2.

## Promotion step (bar (B), Nick 3:15 PM)

`scripts/release_gate.py` on the LOCKED nights. Full tables: [gate-locked-live.md](gate-locked-live.md),
[gate-locked-candidate.md](gate-locked-candidate.md).

| | pass | fail | pending |
|---|---:|---:|---:|
| live default | 10 | 13 | 18 |
| candidate | 11 | 8 | 22 |

| bar (B) | candidate on locked | result |
|---|---|---|
| (a) mid-play entries = 0 | NFL 0, NBA 0/22, NHL 0 (live: 1, 8/17, 4) | **PASS** |
| (b) NBA + NHL crunch live PASS and ≥ live | NBA 94.4 vs 73.7 (PENDING, 4 nights); **NHL 76.2 vs 89.4** (2 nights) | **FAIL** |
| (c) your-team gates PASS | **NFL your-team big seen 90.5 (19/21) vs 95.2; NFL ≤10 min 18/21 vs 19/21; NHL ≤10 min 11/13 vs 13/13** | **FAIL** |
| (d) every other line ≥ live (CI-aware) | NHL crunch aired in a replay 4 vs 2; NHL ≤10 min 76.4 vs 77.9 (inside CI) | FAIL on crunch-in-replay |
| crunch floor test | passes on every profile and overlay | PASS |

**Promoted this cycle: nothing.** The candidate passes rule #1 and the crunch floor, and is better than live on 9 locked lines. What blocks it:
- NHL crunch live, 76 vs 89 on 2 nights, which comes from the catch-up replays. #150 took crunch-in-replay from 10 to 4, but it still needs to reach ≤ 2.
- Four your-team plays: 2 NFL and 2 NHL catch-ups.

Both are lane 2's, and they are its #1 for cycle 06.

## For cycle 06 (Start Planner's #162)
- **Merges:** #163 → #165 (with f83137c) → #168. Then the #146 / #132 / #131 / #125 stack once the drifting-hold test is green. Then #155, #122, #152 and #157 (#165 supersedes #157, so close it).
- **Lane ②:** your-team catch-ups 4 plays → 0 and NHL crunch-in-replay 4 → ≤ 2. Those are the only things between the candidate and promotion.
- **Lane ③:** the failed-"tuned"-switch hold gap (or agree the test change with lane 2).
- **Lanes ①+②:** emit `crunch_exits` / `crunch_exits_ok` and `metrics.watch_value` so the new lines stop reading PENDING.
- **Lane ④:** after #165, `contract_violations` should read the kernel's `--json` total. I'll wire northstar to it in the #165 follow-up.
