# Cycle 05 plan (Start Planner), Wed Nov 4, 19:55 to 21:25 UTC (90-min clock)

**New loop rules (Nick, 2:45 PM ET):** cycles are 90 minutes on a clock. At T+90 the End Planner reviews
whatever has landed, and the Start Planner writes the next plan within 10 minutes. **No idle threads:** each
lane below has 3+ ranked tasks. When a task finishes or blocks (CI, Kaggle, hardware), go straight to the
next one; waiting on CI is not work. **Kaggle goes to the overseer** (factory pack cdf86ef is launching now).
Lab hardware (TV + sticks) is on all day, so use it for anything that needs real timing.
**From:** the cycle 04 work in flight (no review.md yet, so the End Planner reviews cycle 04 at this
cycle's start), Nick's live-lab sync findings (12:10 PM) and the 3:05 PM overseer queue (backlog.md, top).
**Rules as always:** graded on locked unseen nights, flags OFF until they pass, mid_play 0, no signal from
Nick, North Star delta (dev + locked, live + candidate) and a RAM/CPU line in every PR.

**Where we are (dev, live column, post-#121 gates):**
- Big live: NFL 25.2 / NBA 67.6 / NHL 41.4, against ≥95% of oracle.
- Seen within 3 min: 49.7 (bar 99).
- Landings in dead time: 97.5 (bar 98).
- press_to_tv: 8.75 s (bar 3).
- Playbook coverage: 33/78.
- Candidate (lane 2 stack #116 → #125 → #131/#132): mid_play 0, NFL big live ~63 dev / 60 locked, σ p50 3 s.
- Real-hardware risk: the measured YTTV seek makes today's entries mid-play (locked NFL 12, NHL 14) until #132 lands.

**Coverage push this cycle:** 33 → **≥ 50** marked playbook tests. Each lane owns the uncovered entries
listed under it; a marked night-lab test counts even when the behaviour behind it stays flag-off.

## ① Noggin training
1. **Reflex on existing shards while the factory runs:** train the Pick/possession policy (#129) on
   whatever shards exist, forward CV by whole nights. Report rows per league and the decider-level result,
   not only the rollout. Bar: NBA crunch live with the policy ≥ the rule-based possession default on dev
   and locked, mid_play 0, % of oracle up.
2. **Oracle self-play eval:** a harness that grades any policy against the oracle on synthetic nights
   (factory self-play) as well as archived ones, with a pct_of_oracle row per source. This is the eval
   the billions of rows will feed.
3. **Mind salvage (#138) to green**, V7 support only. Then SAFE-04 (ensemble + abstain), with a marked test.
- Coverage: SAFE-04.

## ② Noggin logic / sync
1. **Land the rule #1 seek stack:** #132 must follow #133 (take #132's `_rewind`: one wake press, not two),
   plus the crunch pre-cue recovery (the ~4-pt locked crunch cost of #132). Bar: mid_play 0 with the measured
   seek on dev and locked; crunch locked back to its pre-#132 level.
2. **MediaSession content anchor** (in flight): the burst offset (~0.3 s) goes into the per-stick filter.
   Bar: σ p50 below 3 s with coverage ≥ 95%; NFL/NHL big live and GAME-04 re-graded.
3. **Self-calibration, sync half (#140)** to green: `data/calib/YYYY-MM-DD.json` `sync` section, 3-MAD guard.
4. **NHL whistle detection** (#131 follow-up), then the entry-rewind extra-behind.
- Coverage: SAFE-02, SAFE-03, SAFE-05, SYNC-01..03, GAME-07, GAME-08, GAME-11..14.

## ③ Performance + controls
1. **GO NOW ≤ 3 s press-to-TV on real hardware** (lab is on): measure each segment (phone → Mac → stick → TV)
   with the pre-tune and cold paths split; fix the largest app-side segment. Bar: a real p95 row in northstar.
2. **#136 to green:** (c) timing half (same calib file, `timing` section), (d) pre-game warm-up, (e) buffer
   watchdog STK-06 with the `buffer_lost` fault.
3. **Stick assignment (overseer b, reframed):** with two sticks every new tune goes to the hidden stick, so
   "hottest game on the 4K Plus" can't hold at zero extra flips. The levers are the night's first assignment
   and a standby planner that weighs each stick's tune speed (4K Plus pre-tunes 2.33 s vs 3.74 s p50). Build
   the per-stick standby model in the backtest first so the delta is measurable. Flag `decider.stick_assign.v2`.
4. **LOCK IN hold** end to end on real hardware (BTN-04 release, BTN-06 phone offline).
- Coverage: BTN-04, BTN-06, STK-01, STK-02, TV-01, MAC-02..05.

## ④ App + integration hardening
1. **Night health report (#137)** to green, then its follow-ups: misses linked to the replay viewer, and
   PENDING handling for a night with no games.
2. **#139 LAN redaction** merge; then the night-lab fault kinds for the FEED/NET/APP entries.
3. **Locked switch to eval-data v3 on Fri Nov 6:** prep it now so it's a one-line flip.
- Coverage: FEED-02, FEED-04..07, NET-01..03, APP-01..07, MAC-01, MAC-08, SLATE-01, SLATE-02.

## ⑤ Features (reopened; lowest, never preempts ①-④)
1. **Phone states for the buttons** (UI half of BTN-01..06): LOCK IN badge, GO NOW "switching… / on TV",
   "not delivered, retrying", using lane ③'s command API only.
2. **Teacher decision-record export:** one JSONL per night, objective outcomes only, no taps.
3. **Replay viewer oracle overlay:** the oracle's schedule next to what we did, plus missed-big-play markers.
4. North Star trend page from docs/northstar-history.jsonl (read-only).

## Integrator (at T+0 and T+90)
- **T+0:** review cycle 04 → `docs/cycles/04/review.md`.
- **Merge order:** #133 then #132 (never release one alone) → #116/#125 → #139 → #137/#136/#140 → lane 1.
- **T+90:** review cycle 05 with the coverage count and every lane's delta; the Start Planner writes cycle 06
  within 10 min.
