# One brain: folding every scorer into the Noggin

Status: plan, 2026-11-04. Base audited: `origin/integration/next` e784708 (re-checked, and the
line numbers below corrected, at eb1f65c).
Goal (Nick): RedZone works as ONE connected system. Every module declares inputs and outputs, is
tagged to the North Star line it serves, reuses one definition per fact, has no escape hatches, and
a checker (the proof kernel, `scripts/contract_check.py`) verifies the whole graph on every change.
Rules: [docs/memory/onebrain-rules.md](memory/onebrain-rules.md).

## Where we are

The live path is one chain, but with many brains inside it:

`Engine.decide` (engine.py:1137) -> `DriveScorer.score` (scoring/drive.py:184) -> `Scorer.score`
(scoring/engine.py:213, which calls `Brain.read` + `Reasoner.read` + garbage) -> `Candidate` ->
`Decider.decide` (decider/decider.py:359), an 8-stage pipeline (`_decide`, `_scenario`,
`_clairvoyant`, `_crunch_guard`, `_pick`, `_hold_for_big`, `_time_entry`, `_safe`,
decider.py:382-389).

Inside `_decide`, at least 9 rankers pick "the best game" with different keys (`_crunch_rank` 1505,
`_best`/core.rank 1883-1891, noggin `wire.pick` 1901, the mine_late override 1905, `_hot_live` 1655,
`_rz_drive` 1663, `_drive_takeover` 1678, `_break_destination` 1910, `_long_break_visit` 2098,
`_chase` 1347). The engine ranks again on its own (`_break_standby` 1594, `_standby_options` 1557,
`_hot_games` 821). `DecisionCore.decide` and `NoMidPlayGuard` (decider/core.py:374-398) are never
called.

Facts defined more than once: league order (4x), crunch (4x), break lengths (4-5x, values
disagree), dead ball (5x, one lab copy on the live path), "your team" (4x), three "break coming"
probabilities from two BreakBook instances, four tree walkers and five npz loaders.

## Target shape

- **Feature providers** (no ranking, no deciding): Scorer excitement, drive heat, Brain v2/v3,
  Reasoner lock_in, BreakModel, Noggin Timing and Predict, bets/fantasy. Each emits a typed
  feature set into one `GameView` inside one `WorldState` per tick (contracts/world.py, today unused).
- **One ranker**: the Noggin L1 order (`noggin/priority.rank`: tier, league rank, value), with the
  value term from `core.ev`. Every "which game" question calls it; context only filters candidates.
- **One decision entry**: `DecisionCore.decide` over a proposal list, then the guard chain
  (`NoMidPlayGuard` first, Rule #1). Every switch carries guard provenance in its trace.
- **One definition per fact**, in `redzone/contracts` or `redzone/models`, read from config.

## Ranked steps

Each step: one PR to `integration/next`, behind a flag that ships OFF unless parity holds, the
kernel shows zero new violations and ratchets its baseline down, and the PR carries the
entries_mid_play / plays_missed_entry / dead_air_pct before/after table.

**Parity test** means: replay the golden nights and the dev-test nights with the flag off and on,
and diff the decision stream (tick, action, target, reason). "Exact" = zero diffs. "Measured" =
diffs are allowed, but none of the Rule #1 metrics may rise and the North Star delta is shown.
Golden nights: a small fixed set of replay nights checked into `tests/golden/` (that folder does
not exist yet on integration/next; step 0 creates it). Dev-test nights: the pinned eval data's
`--split fit` nights (train + valid, eval-data-v1). Per the overfitting rule (architecture.md
section 8), the **locked set is scored once per release at the 95% gate, never per PR**.

| # | Step | Owner | Parity | Risk | Estimate |
|---|---|---|---|---|---|
| 0 | Kernel + city map in report mode (claims A, B), first timing constants (claim C), golden nights | INTEGRATION | exact (no runtime change) | low | 1 day (in flight) |
| 1 | One league order | INTEGRATION + DECISION | exact on decisions | low | 3 hours |
| 2 | One crunch table | DECISION | exact | medium | 4 hours |
| 3 | Lab predicates out of the live path | DECISION + INTEGRATION | exact | low | 4 hours |
| 4 | One forest walker and model loader | MOMENT/BREAK | exact (bitwise predictions) | low | 4 hours |
| 5 | One break table + one BreakModel instance | MOMENT/BREAK | measured | medium | 1 day |
| 6 | One "your team" predicate | DECISION | measured | medium | 4 hours |
| 7 | Typed Scored/Candidate/Decision + WorldState builder (shadow) | INTEGRATION + DECISION | exact (shadow only) | medium | 1.5 days |
| 8 | Scorers become feature providers | DECISION, MOMENT/BREAK, LOCAL AI | exact via legacy_score, then measured | high | 2 days |
| 9 | One ranker, ranker by ranker | DECISION | exact per ranker where possible | high | 3 days (9 small PRs) |
| 10 | One decision entry with guards | DECISION | measured, Rule #1 must not rise | highest | 2 days |
| 11 | Delete the legacy paths | all lanes | exact | low | 1 day |

Flags per step (all ship OFF): 1 `onebrain.league_order`, 2 `onebrain.crunch_table`,
3 none needed (pure move, exact parity), 4 `onebrain.forest`, 5 `breaks.one_model`,
6 `onebrain.is_yours`, 7 `onebrain.world_shadow`, 8 `core.reasoner_view` (plus one flag per
provider), 9 `onebrain.ranker.<name>`, 10 `decider.core_entry`, 11 removes the flags it retires.

### 1. One league order
Seams: decider/core.py:235,272; noggin/priority.py:22,49; plan.py:37 (no college leagues);
default_config `score.league_weight` (default_config.yaml:79, wcbb below nhl, opposite to
league_priority). Add `LEAGUE_ORDER` (North Star order) to `models.League` read from
`core.league_priority`; derive `core.league_rank`, `priority.league_rank` (keep each polarity
explicit) and plan sorting from it. Kernel check: `league_weight` is monotone in `LEAGUE_ORDER`.
Parity: exact on decisions; plan.py ordering of college games changes (plan only, list it in the
PR). Fixing the wcbb/nhl weight is a separate, logged decision.

### 2. One crunch table
Seams: scoring/engine.py:57 crunch_time, :694 crunch_window, :638 NBA lock; standby.py:63
crunch_soon; scoring/drive.py:151 + scoring/engine.py:545 copied two-minute drill; lab
sportsgrade.is_crunch (lab/sportsgrade.py:123); demo/engine.py:50-79 fallback (always runs:
backtest has no crunch_counts); web/phone_v2.py:285 (`CRUNCH_MIN`). One `CRUNCH` table per league
(run-up window, graded window, margin) in config, read by all. Keep today's values exactly
(including the NBA 3:00 run-up); the docstring mismatch (scoring/engine.py:62, "graded: final 2:00
within 5") is written down, not silently "fixed". Parity: exact. Risk: grading code shares the
table, so the scorecard cannot drift from the app; a values change later is its own decision.

### 3. Lab predicates out of the live path
Seams: noggin/pick.py:28-29,280,299 (cutgrade, sportsgrade, backtest, oracle); engine.py:356
(lab.timing.is_dead_ball); autonomy.py:86. Move `_dead_at` (lab/cutgrade.py:180), `_clean_leave`
(lab/oracle.py:52), `is_big`, `is_crunch` (lab/sportsgrade.py:102,123), `is_dead_ball`
(lab/timing.py:47), `SwitchLog`, `lag_of` to production `redzone/grading.py` (dead-ball
classification goes through `espn.stoppage_for`). Kernel rule K6 to zero. Parity: exact.

### 4. One forest walker and model loader
Seams: scoring/brain.py:66 (`Trees`), :118 (`Forest`), :186 (`walk`); scoring/brain3.py:91
(`Forest3`), :150 (`walk`); noggin/timing.py:52 (`Forest`); five npz+meta loaders
(scoring/brain.py:229, scoring/brain3.py:187, noggin/timing.py:177, noggin/predict.py:56,
noggin/pick.py:225); MODEL_DIR x5. New `redzone/models_rt/forest.py` (`Forest`,
`load_npz(path) -> (arrays, Meta)` with feature-list check) and one model registry. Parity:
bitwise-equal predictions on fixture rows for every model.

### 5. One break table + one BreakModel instance
Seams: engine.py:145 BreakBook(brain) vs scoring/drive.py:182 BreakBook() vs raw `break_soon_p`;
break lengths in decider/decider.py:69 (`BREAK_LEN_S`), scoring/breaks.py:38 (`LEN`), :71
(`BRAIN_LEN`), decider/cuts.py:168 (`MEDIAN_S`), decider/screen.py:53 (`MIN_BREAK_S`), unused yaml
`break_lengths_s` (default_config.yaml:453) (values disagree: NFL score 150/160/187). One measured
break table with provenance (a lab script writes it) loaded via contracts/breaks; one BreakModel
owned by the engine and injected into DriveScorer; drop `factors['break_p']`
(scoring/drive.py:192). Parity: measured (gates on `_rz_drive` and `_drive_takeover` move). Risk:
medium, flag `breaks.one_model`.

### 6. One "your team" predicate
Seams: plan.py:114,136; scoring/engine.py:349 (`_personal`); decider/decider.py:366-367
(personal >= 5 counts bets, fantasy and manual boosts), again at :1896-1897; priority.HardRules +
wire.py YOURS token. One `is_yours(game, cfg, personal) -> {team, bet, boost, none}`; Scored
carries the reason. Also fixes standby.py:158,166 (`getattr(x, 'mine_late', False)` always False,
so the your-team preload never fires). Parity: measured (the preload fix changes standby). Ask
Nick whether bets/boosts are tier 0.

### 7. Typed contracts + WorldState builder (shadow)
Add `contracts/scored.py` (Scored, Candidate, Decision frozen types). `Engine.decide` builds one
`WorldState` per tick (feeds.snapshot -> models -> sync -> paths -> views) and logs it beside the
legacy path; nothing reads it yet. Wire the orphan contracts (views.moment_view, BreaksV3,
MomentV3, ReasonerView) or delete them. Parity: exact (shadow). Budget: the tick stays inside
`contracts/budget.py` limits on the perf probe (Mac stays light).

### 8. Scorers become feature providers
Each of Scorer, DriveScorer heat, Brain, Reasoner, BreakModel, Noggin Timing/Predict, bets and
fantasy writes features into `GameView` instead of adding points to a score. The old sum survives
as a feature `legacy_score`; parity test: `legacy_score == Scored.score` on every tick of the
golden and dev nights (exact). Then the reasoner moves to its one path (lock_in/must_see via
MomentView, drop `lock_points` inside Scorer.score, scoring/engine.py:243-262): measured, flag
`core.reasoner_view`. Risk: high (score scale changes); one provider per PR.

### 9. One ranker
Replace each ranker with `noggin.priority.rank` over a filtered candidate list, one PR each, in
this order (least to most coupled): `_chase`, `_rz_drive`, `_hot_live`, `_drive_takeover`,
`_long_break_visit`, `_break_destination`, `_crunch_rank`, `_best`, mine_late override. Then the
engine's own rankers via public `Decider.preview_break()` / `Decider.redzone_candidate()` (removes
the 14 private reaches into the decider in engine.py, 6 of them in `_break_standby`,
engine.py:1594-1626). GameInput gets real team abbreviations, red zone / power play factors and
bet sweat, so the phone prefs (web/phone_v2.py:471-488) finally act. Parity: exact where the old
key is a projection of the new order; otherwise measured with the League-order and Crunch rows
shown. Kernel K4 baseline goes to zero.

### 10. One decision entry with guards
Turn each `_decide` branch and the 8 pipeline stages into proposals (core.Action); the final choice
goes through `DecisionCore.decide` with guards [NoMidPlayGuard, CrunchGuard, MineLateGuard,
SafeModeGuard]. The ~6 hand-rolled leave-gate checks collapse into NoMidPlayGuard. Kernel: every
`switch` Decision carries guard provenance. Parity: measured; **entries_mid_play, plays_missed_entry
and dead_air_pct may not rise** (Rule #1). Flag `decider.core_entry` ships OFF until a fresh-night
pass and the release gate.

### 11. Delete the legacy paths
Remove the additive score path, wire.py YOURS token, duplicate constants, unused flags or wire
`flags.*` (default_config.yaml:459-474 is read nowhere). Kernel baseline at zero for K1-K6;
untagged (K7) becomes a failure.

## Not in this plan (separate claims)
- SWITCHING timing: ~55 constants in control.py:73-127, seek model (measured YTTV 15 s step, first
  press swallowed), tv.py verify window, sync_probe cadences.
- Stick/TV I/O behind `contracts/devices.py` (StickIO, TVInput) with all `adb shell` inside
  `redzone/tuners/`.
- Config schema (280 inline `cfg.get` defaults, 21 keys missing from the yaml, 7 disagreeing).
- JSON/JSONL readers (92 `json.load(s)` sites, ~6 shapes) behind one reader module.

## Total
About 12-13 working days of builder time, at most 5 threads at once, every step behind a flag.
