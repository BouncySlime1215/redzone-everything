# Cycle 01 review (End Planner / Integrator), Wed Nov 4, 08:00 to 09:50 UTC

**Plan:** [plan.md](plan.md). **Merged train:** integration/next 9ad88a1 (#92 and #82 last), 2034 tests pass. **Scored at:** b3afe37
(the merged train plus the #92 harness fix), `scripts/northstar.py --split both --profile candidate`, eval-data-v1.
Locked was read once for this review and not used to tune anything.

## Verdicts

| lane | PR | verdict | against "what we're looking for" |
|---|---|---|---|
| ① | #82 Predict v1 NBA + NHL, Timing v1 NFL + NHL, NFL factory | **keep, flags off (merged)** | It is a real forecaster: forward CV, conformal Timing bound, numpy live path, and the PR reports params, rows, RAM and size. It misses the target **big_live_nba +5 on the candidate**: dev stays 81.7 → 81.7, and crunch live NBA gets worse (90.9 → 89.6). The lane kept it out of the candidate, which is the right call. It merged after the lane resolved a config conflict with #78/#83. |
| ② | #81 finished-game exits + crunch rewind | **keep (merged)** | It moves the right line: candidate mid_play dev 0.35 → **0.24**, locked 0.33 → 0.17. The target was ≤ 0.10 and NBA ≤ 1/61, but it reached 3/60, so it **misses**. The 3 left are crunch entries with no rewind in reach, and closing them needs a better pick (lane ① Pick), not a longer wait. `crunch_now=wait` reaches 0/60 but costs big live NBA 81.7 → 77.5: that trade is Nick's call. |
| ② | #78 sync stack | **keep (merged)** | Stalls shift the filter instead of resetting it, network features widen σ, break anchors are logged with the filter-fed flag off, and there's a drift alarm, all as asked. delay_err_p95 stays PENDING until Wednesday's probe logs, which is honest. |
| ② | item 2: the 3 locked regressions from #76 | **open, pending fresh nights** | The lane couldn't reproduce them on dev (candidate dev: NHL crunch 80.6 vs 79.7 live, your-team crunch 100 vs 71.7). On locked they rest on 2 nights (NHL crunch) and 1 decision each (your-team). Pinning a flag off would tune on locked, so I agree with re-checking on Wed/Thu fresh nights. |
| ③ | #83 phone controls | **keep (merged)** | Tap ids + ack + status + retry, so taps land exactly once. Skip now counts from leaving. Drill: 0 locks broken in 8, and `press_to_tv_p95` is a line (sim can FAIL, never PASS). LOCK IN was already honored after safe mode and persisted, so no decider change was needed. The Samsung local-key fast path already existed, and the new probe only times it. **Gap:** the northstar run here didn't pass `--controls-json`, so press_to_tv shows PENDING; the nightly job runs the drill. |
| ④ | #80 eval-data-v2 + live-test collector | **keep (merged)** | v2 is published, but 4 NBA and 3 NHL v2 locked nights were v1 dev nights. The new leak check caught it, so **locked must not be graded on v2**. The clean pin is v3, built Fri Nov 6 morning once Nov 5 is final (NBA needs 1 more night). `collect_live_test.py` covers probe, tv_input, cuts and traces, plus press_to_tv from decided(user) to picture. B10 was already fixed. |
| ④ | #86 night-lab playbook faults | **keep (merged), rework one thing** | It adds FEED-02 (feed_frozen), FEED-06 (rate_limit), MAC-05 (slow_poll), SAFE-03 (wifi_degrade) and NET-02 (buffering) as opt-in faults with a per-fault no-mid-play check: seeds 1-2 show 0 mid-play cuts in every window. Rework: its tests carry no `@pytest.mark.playbook(...)` markers yet, so coverage reads 0/79 (see below). |
| ④ | #20/#23/#24 platform stack | **drop (close)** | Superseded on integration/next. The only leftover diff is the old `feed_book` name. Closing is blocked by the permission check for every thread, so Nick has to close them. |
| ⑤ | #79 phone v2 | **keep (merged), flag off** | Reason chips, 👍/👎 per switch, "I missed that", and records in `feedback` read by `Store.taps()`. The Noggin L2 sliders save only. No decision path reads them, and every North Star delta is 0. `right_game` is wired from the store side; the harness still has to read `Store.taps()`. |
| Int | #87 CI playbook coverage + RAM/CPU line, #92 northstar candidate fix | keep (both merged) | The RAM/CPU line is now in every PR comment: tick p50 1.4 ms, p99 1.6 ms, RSS 70 MB on the CI runner. #92: the #76 + #83 merge silently dropped the candidate column from `northstar.py --profile`. |

### Playbook entries, per id
| id | PR | state after cycle 01 |
|---|---|---|
| SAFE-03 wifi_degrade, FEED-02 feed_frozen, FEED-06 rate_limit, MAC-05 slow_poll, NET-02 buffering | #86 | fault + graded window exist, 0 mid-play on seeds 1-2. **Not `done`**: needs a marked test and its behavior flag. |
| SAFE-06 finished game on screen | #81 | behavior in the candidate (wait for the target's dead ball). Still `partial`: no marked night-lab test. |
| SAFE-07 crunch entry | #81 | rewind when in reach. `partial`: 3 NBA dev entries remain. |
| Coverage | #87 | **0/79** entries have a marked test (`@pytest.mark.playbook("ID")`). CI fails only when an entry marked `done` lacks a passing test. |

## Merged scoreboard: live default vs candidate (eval-data-v1)

| line | target | dev live | dev candidate | locked live | locked candidate |
|---|---|---:|---:|---:|---:|
| Mid-play cuts + entries / 100 landings | 0 | 2.45 | **0.24** (NBA 3, NHL 1) | 1.75 | **0.17** (NBA 2) |
| Landings in dead time | ≥95% | 97.5 | 99.8 PASS | 98.3 | 99.8 PASS |
| Big plays live NFL | ≥95% | 25.2 | **49.4** | 23.3 | 45.6 |
| Big plays live NBA | ≥95% | 67.6 | **81.7** | 62.5 | 100 (4 nights, PENDING) |
| Big plays live NHL | ≥95% | 41.4 | 44.7 | 38.9 | 48.1 |
| Your-team big (live or ≤10 min) | 100% | 77.4 | 90.3 | 94.1 | **88.2 ↓** |
| Crunch live NFL / NBA / NHL | ≥95% | 62.2 / 76.7 / 79.7 | 65.4 / **90.9** / 80.6 | 59.6 / 73.7 / 89.4 | 67.0 / 93.3 / **84.7 ↓** |
| Your-team crunch live | 100% | 71.7 | **100** (3 nights) | 100 | **97.1 ↓** (1 of 34) |
| Live or replayed ≤10 min | ≥99% | 62.3 | **85.1** | 61.3 | 88.8 |
| Dead air | 0 (ceiling n/a) | 22.5 | **18.8** | 26.7 | 20.5 |
| Switch p95 / flips ≤3 s / delay err / press→TV | | 2.2 s PENDING; the rest PENDING | same | | |

On dev, the candidate is better than or equal to live on **every** graded line. On locked, it is better on 9 lines
and worse on 3. Those 3 are the known item-2 regressions, each resting on 2 nights or a single decision, and they
stay open until the fresh nights. **No line PASSes that didn't before.** Thin data (NBA/NHL locked at 4 nights,
your-team at 3-5) keeps most lines PENDING or wide, and the clean eval-data-v3 (Nov 6) fixes that.

**Honest-lag gap:** `feed_lag_nhl` still reports ESPN's 50.2 s. #75 made the sims draw the api-web profile *when
`sources.nhl_primary` is on*, but the candidate profile doesn't turn it on yet, so NHL candidate numbers still use
ESPN lag. That's for lane ② in cycle 02 (target: NHL mid-play 1 → 0, big live NHL up).

## What's left from cycle 01
1. **Lane ①:** Predict and Timing are in the tree with flags off and have no candidate gain yet (see below).
2. **Lane ②:** NBA crunch entries 3 → ≤1 (needs Pick, or Nick's call on `crunch_now=wait`); the locked regressions
   are re-checked on fresh nights; NHL wsc goes into the candidate (`sources.nhl_primary` + NHL api-web lag).
3. **Lane ③:** `--controls-json` in the review run (or the harness runs the drill itself), so press_to_tv shows a sim value.
4. **Lane ④:** playbook markers on #86's tests; v3 built Fri; #20/#23/#24 closed (Nick).
5. **Harness:** `right_game` reads `Store.taps()`; the dead-air ceiling is still not computed (plan item 6).

## Is lanes ①-② ready for cycle 02 (clairvoyance and the hindsight oracle)?

**Partly.**
- **Ready:**
  - The buffer (#61) is in the candidate at B=90/75.
  - Timing v1 exists for NBA, NFL and NHL, and Predict v1 for NBA and NHL. All have a numpy live path at under 5 ms.
  - The factory makes whole-night windows for three leagues.
  - Lane ① already plans Reflex Pick by imitating the oracle.
- **Missing for clairvoyance (Predict on the buffered-ahead feed):**
  - Predict and Timing read the feed at feed time, and nothing yet hands the decider "what the feed already shows for
    the next B seconds of screen time" as features. Cycle 02 needs an explicit `lookahead` view: feed events between
    screen-now and screen-now+B per game. Predict and Pick should consume it, and the sims should replay it the same way.
  - Without that, Predict v1 has no information edge over the score, which is why it showed no candidate gain.
- **Missing for the hindsight oracle:**
  - We have no oracle scorer yet: the best possible schedule per night under switch cost, flip time and the
    no-mid-play rule.
  - Item 6 (dead-air ceiling), `crunch_*` "feasible ceiling" and Pick's training labels all need the same oracle, so
    build it once (lane ① or ②, in `redzone/lab/`) and have northstar.py report "% of oracle" next to each line.

## Suggestions for cycle 02
- **Lane ②:**
  - lookahead view + NHL wsc in the candidate;
  - NBA crunch entries ≤1;
  - the fresh-night re-check of the 3 locked lines.
- **Lane ①:**
  - hindsight oracle → Pick v1 by imitation;
  - Predict v2 on the lookahead features;
- **Lane ③:** press_to_tv measured in the harness run; the still_watching_cleared seed-2 miss that #86 found.
- **Lane ④:**
  - playbook markers;
  - switch northstar to v2 (dev) / v3 (locked) on Nov 6;
  - the dead-air ceiling via the oracle, if lane ① lands it.
- **Integrator:**
  - The harness reads `Store.taps()`.
  - When two PRs touch the same function, read the merged function, not only the conflict list (lesson logged from #92).
