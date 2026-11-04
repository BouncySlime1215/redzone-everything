# Cycle 06 plan (Start Planner), Wed Nov 4, ~21:00 to 22:00 UTC (60-min rhythm)

**Rhythm (Nick, 4 PM ET):** planners run every 60 min, or at each big pass (a merge train or an RC cut),
whichever comes first. **No idle lanes:** when a task blocks, take the next one.
**Rules:** graded on locked nights; flags OFF until they pass **bar (B)**:
- entries 0 (hard);
- NBA/NHL crunch PASS and ≥ live;
- your-team PASS;
- every other line ≥ live within the CI.

Also: no signal from Nick; North Star delta + RAM/CPU line in every PR; route through the city map (#157).

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
1. **#157 city map + contract checker** to green in report mode, then fail-on-new. Row `contract_violations`.
   Publish the burn-down list per owning lane.
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
