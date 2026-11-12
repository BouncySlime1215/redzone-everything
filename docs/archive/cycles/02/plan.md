# Cycle 02 plan (Start Planner), Wed Nov 4, ~10:00 to 13:30 UTC

**From:** [cycle 01 review](../01/review.md) (integration/next b41986c), the [idea bank](../idea-bank.md)
(#1 clairvoyance, #2 oracle, #13, #17, #18, #19), [playbooks](../../../playbooks.md),
[backlog](../backlog.md). Usage is ~+4.5 over the line, so the doc is lean, research is skipped this
cycle, lane ⑤ waits until 13:00 UTC, and workers run only the scorecards they touch.

**Where we are (dev, candidate vs live):** mid_play 0.24 vs 2.45, big live NFL 49 / NBA 82 / NHL 45,
live-or-replayed 85, dead air 18.8. On locked, 3 lines are worse, each on thin data, and they get
re-checked on fresh nights. Playbook coverage is 0/79.

**The one idea:** the buffer puts the feed **B − lag ≈ 45-70 s ahead of the screen**. Nothing reads that
yet, which is why Predict v1 showed no gain. Cycle 02 builds the **lookahead view** (clairvoyance) and the
**hindsight oracle** (the ceiling and the teacher), the two foundations every later idea stands on.

## ① Noggin training: the hindsight oracle and `oracle_gap`
- **Goal:** the best possible switch schedule per archived night, given full hindsight, under the
  real switch cost, flip time and the no-mid-play rule (DP over all games). It is built once and used
  three ways: the ceiling, the teacher and the scorer.
- **What we're looking for:** `redzone/lab/oracle.py` runs offline (cloud container or GH Actions,
  never the Mac). It is exact or provably near-exact (state the approximation and its error bound on a
  small night where brute force is possible). It emits per-night schedules plus labels for Pick.
  `northstar.py` reports **"% of oracle"** next to big live, crunch and dead air, plus the **dead-air
  ceiling** (roadmap item 6, previously lane ④). If time allows: **Pick v1 by imitation**, with forward
  CV and graded on the full set, behind a flag OFF.
- **North Star:** a new `oracle_gap` line (target ≥90%), computed for dev and locked. Dead air and
  crunch_* get feasible ceilings, so PENDING can become PASS/FAIL. No live behavior changes.
- **Files:** `redzone/lab/oracle.py`, `scripts/northstar.py` (new columns only), `tests/test_oracle.py`.
- **Depends on:** nothing. Predict v2 waits for lane ②'s lookahead view (cycle 03).

## ② Noggin logic/sync: the lookahead view and clairvoyance in the candidate
- **Goal:** the decider sees what the feed already shows for the next B seconds of screen time, and
  uses it.
- **What we're looking for:**
  1. A `lookahead` view: per game, the feed events between screen-now and screen-now + B, computed
     from the per-stick delay (DelayBook μ/σ). It is identical in live and in the sims, and it's empty
     when σ is too wide (SAFE-02).
  2. **GAME-04 clairvoyance, rule-based, in the candidate:** a big play visible in game X's
     ahead-window → pre-tune X and flip at the last dead ball before it airs. A whistle visible ahead
     means entry exactly at that dead ball, which should also close the last NBA crunch entries.
  3. `sources.nhl_primary` and the api-web lag go into the candidate (the review's honest-lag gap).
  4. The 3 locked regressions are re-checked on fresh nights when they exist (no tuning on locked).
- **North Star (candidate, dev):** big live NFL 49 → **≥65**, NBA 82 → **≥90**, NHL 45 → **≥55**;
  mid_play 0.24 → **0** (NBA crunch entries 3 → 0); dead air no worse. Report dev and locked.
- **Files:** `redzone/noggin/lookahead.py` (new), `redzone/noggin/profile.py`, decider entry/pick
  hooks, `redzone/lab/` sim replay of the same view, tests with `@pytest.mark.playbook("GAME-04")`.
- **Depends on:** nothing to start. Nick's call on `crunch_now=wait` (asked separately); until he
  answers, the lane builds the lookahead fix, which may make the question moot.

## ③ Performance + controls: real numbers and the flip path
- **What we're looking for:**
  1. `northstar.py` runs the controls drill itself, so `press_to_tv_p95` shows a sim value in every run.
  2. Fix the `still_watching_cleared` seed-2 miss that #86 found (STK-04).
  3. SAFE-08, the pre-flip smoothness check: skip the flip if the hidden stick isn't steady.
  4. Idea #17: pre-tune the most-likely-next game on the hidden stick, using lane ②'s lookahead target
     when it's there, else the Priority pick.
  5. Marked night-lab tests for BTN-01..03, STK-01..03, SAFE-08.
- **North Star:** `press_to_tv_p95` ≤3 s (sim), `recovery` ≥95% with seed 2 clean,
  `flip_playing_3s` measured in the e2e night. RAM/CPU line unchanged (tick p99 ~1.6 ms).
- **Files:** `scripts/northstar.py` (drill hook), `redzone/lab/night/*`, `redzone/control.py`,
  `redzone/tuners/screen_prompts.py`, tests.
- **Depends on:** lane ②'s lookahead for item 4 (fall back to Priority until it lands).

## ④ App + integration hardening: coverage and clean data
- **What we're looking for:**
  1. Playbook markers on #86's tests and every existing test that proves an entry. Mark entries `done`
     only when their test and flag exist. **Coverage target: 0 → ≥15 of 79.**
  2. `right_game` reads `Store.taps()` in the harness.
  3. eval-data-v3 ready to build the moment Nov 5 is final (script + leak check). Switch northstar to v2
     dev / v3 locked on Nov 6, not before.
  4. FEED-01 and FEED-03 (source failover order, source disagreement) as marked playbooks.
- **North Star:** none moves directly. Playbook coverage, `right_game` wired, `recovery` ≥95%.
- **Files:** `tests/` markers, `docs/playbooks.md` statuses, `scripts/northstar.py` (taps only),
  `scripts/eval_*`, `redzone/sources/` (failover only).
- **Depends on:** coordinate the `northstar.py` edits with lanes ① and ③ (each touches different
  columns); the Integrator merges ③ → ① → ④.

## ⑤ Features: paused until 13:00 UTC (8 AM ET)
Then only [backlog](../backlog.md) item 1: phone states for Nick's buttons (LOCK IN badge, GO NOW
confirmation, "not delivered"/stale banners) on lane ③'s command API. One small PR, flag OFF.

## Integrator order
lane ③ → ① → ④ for `northstar.py`; lane ② whenever green. Read the merged function, not only the
conflict list (the #92 lesson). The review grades GAME-04 and coverage per playbook id.
