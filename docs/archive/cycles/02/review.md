# Cycle 02 review (Integrator), Wed Nov 4, 10:00 to 12:20 UTC (lean: usage brake)

**Merged train:** integration/next 9c423c6, 2101 tests pass. **Scored:** `northstar.py --split both --profile candidate
--oracle`, eval-data-v1. Locked was read once, not tuned on.

## Verdicts
| lane | PR | verdict | vs intent |
|---|---|---|---|
| ① | #89 hindsight oracle, #104 Pick v1 (offline) | **keep** | The oracle exists and the % of oracle line is live. Pick v1 is offline only: on dev, rollout mid-play goes 3 → 0 and dead air 19.4 → 13.7. Its 4-5 switches an hour (vs about 1.2) and the "switch lands as planned" assumption are the risks. **Next: shadow hook.** |
| ② | #100 lookahead + GAME-04, #93 SAFE, #97 scenarios, #103 GAME playbooks | **keep; rework GAME-04 gating** | mid_play target **met**: candidate 0.24 → **0 PASS** on dev (0/1703) and locked (0/1231). Big-live targets **missed**: NFL 49 → 53 (target 65), NHL 45 → 45 (target 55), and **NBA 82 → 77.5 got worse** (locked 100 → 69). Both come from `crunch_now=wait`, which went into the candidate *before* Nick's call. 790 of 866 GAME-04 attempts were gated by σ > 8 s, so sync accuracy is the lever, not more rules. |
| ③ | #90 BTN-03..06, #99 STK/TV, #102 drill in harness + STK-04 fix | **keep; target missed** | The drill now runs on every northstar run. **press_to_tv_p95 = 8.75 s FAIL** (sim: 8 presses, median 8.5 s, 0% within 3 s) vs a ≤3 s target. Pre-tune-on-open is off in defaults, and idea #17 (pre-tune the likely-next game) isn't in yet. |
| ④ | #101 coverage, taps, FEED-01/03 | **keep** | **Coverage 0 → 32/79** (target ≥15), with 11 entries `done`, all with passing tests (CI-enforced). `right_game` reads `Store.taps()` and stays PENDING until Nick taps. It also fixed a #92-style bug (a duplicate `nights` filter dropped `settle_book`). |
| ⑤ | #94, #95, #96 (merged); #88, #91 | keep / **wait** | #88 and #91 conflict in the UI files. They're paused under the brake. |

**Semantic conflict caught:** #99's new night faults broke #101's exact-set test, so I changed it to a subset check in
the merge (d52e408).

## Playbook ids
- **GAME-04:** built and in the candidate (flag `noggin.lookahead.enabled`) with a marked test. It's mostly gated
  (σ > 8 s on 91% of attempts), so its live effect is small: NFL big live +4. **Not done.**
- **Done (11):** BTN-01/02/03, STK-01/02/03, SAFE-08, GAME-06, GAME-10, FEED-01, FEED-03.
- **Marked, not done (21):** BTN-05, FEED-02/06, GAME-01/02/03/04/05/09/21/22, MAC-05/06/07, NET-02, SAFE-01/03,
  STK-04, TV-02/03/04.
- **No test yet:** 47 entries.

## Scoreboard (candidate vs live; dev / locked)
| line | live | candidate | % of oracle (cand) |
|---|---|---|---|
| mid_play /100 | 2.45 / 1.75 | **0 / 0 PASS** | |
| big live NFL | 25 / 23 | 53 / 51 | ceiling 91 / 93 |
| big live NBA | 68 / 63 | 77.5 / 69 (**↓ from 82 / 100 last cycle**) | ceiling 93 / 100 |
| big live NHL | 41 / 39 | 45 / 49 | ceiling 85 / 90 |
| crunch live NFL / NBA / NHL | 62 / 77 / 80 | 67 / 90 / 81 (locked NHL 76 ↓) | |
| live or replayed ≤10 min | 62 / 61 | 83 / 85 | |
| dead air | 22.5 / 26.7 | 19.1 / 21.1 | **ceiling 10.6 NFL, 11.2 NBA, 18.9 NHL** (dev) |
| press_to_tv p95 (sim) | 8.75 s FAIL | 8.75 s FAIL | |

The % of oracle (worst line per league) for the candidate is NFL 59 / 55, NBA 83 / 69 and NHL 53 / 55. **Big live is
the worst line in every league.**

## For cycle 03
1. **Nick's call: `crunch_now=wait`.** It buys mid-play 0 and costs NBA big live (−4 dev, −31 locked) and locked NHL
   crunch. Until he decides, suggest the candidate keeps `rewind` and grade both.
2. **Lane ②: tighten sync σ below 8 s.** That unlocks GAME-04, which is the gate on 91% of its attempts. Wed probe data
   arrives Thursday.
3. **Lane ①:** Pick v1 in shadow behind a flag, with a switch-rate cap.
4. **Lane ③:** press_to_tv ≤3 s in the drill (pre-tune on open, plus idea #17).
5. **Lane ④** (after the brake): #88/#91 rebase. v3 lands Fri: switch locked to v3.
