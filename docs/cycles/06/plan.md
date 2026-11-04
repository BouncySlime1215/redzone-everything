# Cycle 06 plan (Start Planner), Wed Nov 4, ~21:00 to 22:00 UTC (60-min rhythm)

**Rhythm (Nick, 4 PM ET):** planners run every 60 min, or at each big pass (a merge train or an RC cut),
whichever comes first. **No idle lanes:** when a task blocks, take the next one.
**Rules:** graded on locked nights; flags OFF until they pass **bar (B)**:
- entries 0 (hard);
- NBA/NHL crunch PASS and ≥ live;
- your-team PASS;
- every other line ≥ live within the CI.

Also: no signal from Nick; North Star delta + RAM/CPU line in every PR.
**Standing rule (overseer, city map #165):** every lane **reads `docs/citymap.md` first** and opens only the
files it needs, and **tags every new module with the North Star line it serves** (and an owner).
No escape hatches: reuse definitions and follow `docs/memory/onebrain-rules.md`.

**Since cycle 05 (merged into integration/next):**
- #116 possession-start entry;
- #136 calibration / warm-up / watchdog (timing from `calibration.*`);
- #138 Mind salvage, #135 auto-tune salvage;
- lane 5's export, oracle ribbon, trend sheet, "I missed that" and clash alert.

**In review:**
- promotion blockers #133/#132 (seek) and #150 (crunch block);
- cut rules #154 and the North Star split #156;
- crunch floor #160;
- city map #157, coverage PRs #143/#147/#159.

## Cycle 05 review folded in (fdcc5c3, 21:25 UTC)
- **% of oracle moved:** dev 64.8 → 65.0, locked 59.1 → **66.4** (mostly #116 on NBA locked, 4 nights).
- **Merged:** #116, #150, #129, #148, #156, #141, #134.
- **Promoted under bar (B): nothing.** Rule #1 and the crunch floor pass. Two blockers remain:
  - NHL crunch live on locked: 76 vs 89 live, with crunch-in-replay at 4 vs 2;
  - 4 your-team catch-ups: NFL 2, NHL 2.
- **Held:** #146, #132, #131 and #125, all on **one red e2e test** (a drifting hold on an unheld standby).
  It's either a lane ③ gap or a test change. No skip.

**Re-ranked top items:**
- **③ #0 (unblocks 4 PRs):** fix the red e2e test. Find out whether the controller drifts a hold on a standby
  it isn't holding (fix the code) or the test asserts the wrong thing (fix the test, with the reason in the
  PR). This comes before everything else in lane ③.
- **② #1 (promotion):** NHL crunch live on locked back to ≥ live (find the remaining crunch-in-replay starts
  and the NHL crunch-exit cases), plus the 4 your-team catch-ups (NFL 2, NHL 2). Bar: release_gate under
  bar (B) passes, with no candidate line worse than live.
- **Integrator next:** #163, #165 (+ f83137c), #168. Then the held four once the e2e test is green. Next
  review at 22:25 UTC or the next big pass.

## The big change: crunch is a learned state, decided by value (Nick, 4:15 PM ET)
This **replaces** the 4:05 "crunch = stay, absolute" line.
1. **Crunch is a state with an entry and an EXIT.** The Noggin learns when a game enters crunch and when it
   leaves it: the lead blows out, the game is decided, the clock is gone, or the key moment passes. It
   uses hysteresis so the state doesn't flicker. The exit is the under-built part. New line:
   **`crunch_exit_acc`**, crunch entry/exit accuracy vs the hindsight oracle (dev + locked).
2. **Crunch protects only against low-value games:** a low-value game can never pull us out. **Crunch vs
   crunch is decided by value**, not a fixed rule.
   - Nick's example: we're on the Bills, 3 min left, and the Bears start a 1-min drive.
   - Best play: watch the Bears drive while the hidden stick holds the Bills, then return and catch up the
     Bills from the buffer; or interleave the two at dead balls.
   - Every night is different, so this is the **scenario-tree planner + Watch Value**, trained by self-play
     against the oracle. **Rule 6 / TREE-6 moves up to this cycle.**
3. **Hard floors only:**
   - never join mid-play;
   - never leave a high-leverage moment for a low-value game;
   - league order and WSH/UNC are **strong priors**, not absolute walls.

## Data ledger (new standing section, Nick 5:00 PM ET: data scale is the Noggin's #1 bottleneck)
Every cycle plan carries this table, filled from the factory manifest. Lane ① reports it in every factory PR.
This cycle it's PENDING: the factory has no event-token shards yet (they arrive next round).

| league | source (license / terms checked) | real tokens | synthetic (self-play) tokens | crunch share | OT / comeback / blowout share |
|---|---|---:|---:|---:|---|
| NFL | nflverse open pbp 1999+ (check the license) + ESPN | PENDING | PENDING | PENDING | PENDING |
| CFB | collegefootballdata.com API (free key; Nick adds it) + ESPN | PENDING | PENDING | PENDING | PENDING |
| NBA | full historical pbp (source + terms to record) | PENDING | PENDING | PENDING | PENDING |
| NHL | api-web full pbp + shift data | PENDING | PENDING | PENDING | PENDING |
| MLB | Statcast pitch-level via Baseball Savant (check terms, throttle) | PENDING | PENDING | PENDING | PENDING |
| CBB | ESPN (everything it has) | PENDING | PENDING | PENDING | PENDING |
| live nights (gold) | our own logs: TV timing, ads, delay | PENDING | n/a | PENDING | n/a |

**Lane ① source expansion (new item 2, after the NFM design doc):**
- Add each source to the factory as its own resumable shard family. **Record each source's license or terms
  in `docs/data-sources.md` before the first pull.** Respect rate limits. No scraping of sites that forbid it,
  and no paid feeds without Nick's OK.
- The CFB key is a secret, so Nick adds it himself. Ask the overseer, and don't ask for the value.
- **Self-play slates ×5-10** over real nights.
- **Every live night becomes gold data** (lane ③'s auto-log, lane ④'s night report).
- **Balance, not just volume:** stratify sampling so crunch, OT, comeback and blowout have target shares,
  and report the shares in the ledger.

## Addendum: strategy shift, the Noggin Foundation Model (Nick, 4:45 PM ET). The Noggin's main track
Stop hand-listing features. Build one **pretrained event model** and put the heads on top (roadmap item
**NFM**, priority 0).
1. **Data = event tokens:** every play, stoppage, score, clock, score-state and lineup event from the
   factory, across all leagues and seasons, as sequences per game, plus the cross-game slate. ~1B tokens,
   real + self-play.
2. **Pretrain self-supervised:** predict the next event, its time, and the next stoppage. No human labels.
3. **Fine-tuned heads:** Watch Value, crunch enter/exit, break start/length, big-play-next, game
   importance. The hand features become optional inputs.
4. **Size to the data**, then distill to the Reflex (< 5 ms) and run a 3B-class Mind/MLX tier on the Mac
   (≤ 2 GB) at a few Hz.
5. **Gates:** locked nights, oracle rank agreement, calibration.

**Planner's compute note for the design doc (lane ① to confirm):**
- Training compute is ≈ 6·N·D FLOPs:
  - 100M params × 1B tokens ≈ 6e17 FLOPs, about **8 T4-hours** at a realistic ~20 TFLOPS fp16;
  - 300M params is ≈ 25 T4-hours, which uses almost all of the 30 GPU-h/week.
- Compute-optimal scaling (~20 tokens per param) puts 1B tokens nearer **~50M params**. So start at
  50-100M, add tokens (self-play) before adding size, and keep 300M as the stretch once tokens reach ~5B.
- On the Mac, a 3B 4-bit model is ~1.7-2 GB. "A few Hz" fits only short-context scoring passes, not
  generation, so budget the Mind tier as scoring, with the Reflex carrying every tick.

**Lane ① re-ranked for this cycle:**
1. `docs/noggin-foundation-model.md`: tokenizer, objective, size/compute plan, milestones, gates.
2. The factory emits **event-token shards** next round (sequence schema, per-game + slate context, resumable,
   rows/tokens per league in every PR).
3. **V8** (the crunch + leak fixes) stays the near-term model.
4. The WV / crunch-state / importance work this cycle continues as **baselines and head definitions**. The
   NFM heads must beat them on locked nights before they replace them. The `Scored.*` contract is unchanged.

## Addendum: GAME IMPORTANCE, a per-game weight in Watch Value (Nick, 4:35 PM ET; extends #163)
"How much does this game matter?" A weight is set before kickoff and updated live. It is **a weight, not a
lock**: on a busy Sunday the big matchup gets more screen time, but the other games still get their
red-zone trips and big plays.

Inputs:
1. **Playoff-race leverage:** how far this result moves each team's playoff or seeding odds, from standings
   plus a quick season Monte Carlo, or ESPN's FPI/BPI playoff odds if a free endpoint exists. It grows
   through the season (Week 15 > Week 2, April > November).
2. **Matchup quality ("big boy matchups", e.g. Bills at Rams):** both teams' strength (records, a power
   index or Elo), star power, rivalry, and the national-TV slot as a proxy.
3. **Season scarcity:** an NFL game (17) weighs more than an NBA/NHL game (82).
4. **Postseason** (#163): series state, Game 7, elimination.

The weights are learned against the oracle (self-taught). Nick's teams stay preference multipliers.

**Contract:** `Scored.importance` (a multiplier on WV, 1.0 = an ordinary game) and `Scored.stakes_line`
(e.g. "AFC 1-seed race, top-5 matchup").

**Line:** `importance_rank_agreement`, the rank agreement of pre-game importance with the oracle's realized
value per slate (dev + locked).

**Split:**
- **①** builds the importance model and its features:
  - Elo from the factory's results 2002+;
  - standings and a season Monte Carlo for playoff odds (cloud);
  - first, one quick check for a free, ToS-clean ESPN FPI/BPI odds endpoint, used only if it is clean;
  - forward CV by whole seasons, locked untouched;
  - the `stakes_line` from its top inputs.
- **②** applies `importance` inside WV in the decider behind `scoring.watch_value` (a weight, never a lock).
  Test: on a busy slate the big matchup's screen share rises while the other games keep their red-zone
  trips and big plays (big live per league ≥ live).
- **⑤** shows the per-game stakes line on the phone next to WV and its why, against a stub until ① sets it.

## ① Noggin training
1. **Watch Value (HEAT 2.0)**, in flight: `Scored.watch_value` 0-100 + `watch_why`. Old scorers are feature
   providers. Lines: WV calibration error, rank agreement with the oracle.
2. **Crunch state model:** learned entry/exit with hysteresis, labelled by the oracle (when holding a game
   stops paying), forward CV by whole nights. Line: `crunch_exit_acc` vs the oracle. Bar: ≥ 95% on locked,
   no added flicker (state changes per game-hour ≤ today).
3. **Scenario-tree tables (TREE-6, cloud half):** self-play vs the oracle on factory nights. Value-of-holding
   vs value-of-jumping for crunch-vs-crunch, with a catch-up from the buffer as an action. Use #152's
   self-play eval as the harness. Line: % of oracle on crunch-overlap nights.
4. #129 possession policy with DAgger; #122 factory to green.

## ② Noggin logic / sync
1. **Land the promotion blockers:** #133 → #132 (one wake press), #150 crunch block. **Rework #154, #156 and
   #160 to match the 4:15 correction:**
   - the floor is "never leave a high-leverage moment for a LOW-value game", not "crunch = stay";
   - #160's SAFE-09 test asserts exactly that floor and nothing stronger;
   - #156 adds the `crunch_exit_acc` row.
2. **Crunch vs crunch by value (TREE-6, Mac half):** the decider looks up the scenario tree with WV. The
   actions are jump, stay, interleave at dead balls, and catch up from the buffer on return (the hidden stick
   holds the left game; reuse #130 replay-and-resume). **Marked test: the Bills/Bears case.** Bar: crunch
   live + big live ≥ live on locked, entries 0.
3. **WV into the decider** behind `scoring.watch_value`; migrate the old scorers out with locked parity
   tests (city map).
4. #146 burst anchor (honest σ), then NHL whistle detection.

## ③ Performance + controls
1. **Hold the left game for the return:** when a crunch game is left for another, the hidden stick holds it
   (pause buffer, calibrated hold) so the catch-up starts at the exact dead ball. Use the buffer watchdog
   (STK-06). Real-hardware check in the lab.
2. **Provenance traces** for every TV action and decision (which module contributed what).
3. **GO NOW press-to-TV** (#145) on the rig: publish the segment split and a real p95 row.
4. Close out #144: stick assignment is a ~0 lever, so record it in the idea bank as done/dropped with the
   reason.

## ④ App + integration hardening (lead: city map)
1. **Execute the onebrain migration plan** (`docs/onebrain-migration.md`, overseer #165) step by step:
   - tag and own the 210 untagged / 99 unowned modules;
   - resolve the 10 orphans;
   - burn down contract violations from the 490 baseline;
   - the Integrator ratchets the baseline after each merge.
   **Reconcile #157 with #165** (same city map and checker): keep one, fold the other's unique parts in,
   and close the duplicate.
2. Coverage PRs #147 / #159 / #151 to green (target ≥ 50 marked).
3. eval-data-v3 locked switch on Fri (#149).

## ⑤ Features
1. One phone surface for WV: fold #155 and #161 into one sheet (top-3 + why).
2. Show each game's crunch state (entering / in / leaving) from lane ①'s model, read-only, flag off.
3. Replay viewer: mark the catch-ups and interleaves the planner chose next to the oracle ribbon.

## Integrator
- Log the 4:15 correction in decisions.md, **replacing** the 4:05 "absolute" line.
- Merge order:
  1. #133 → #132;
  2. #150;
  3. #156 / #154 / #160, after their rework;
  4. #157;
  5. the rest.
- The next review comes at the next big pass or at 22:00 UTC.
