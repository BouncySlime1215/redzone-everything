# RedZone architecture contract

Version 1 (2026-11-03). Owner: INTEGRATION. Code: `redzone/contracts/` (interfaces + stubs),
`tests/test_contracts.py` (contract tests). Every thread codes against these shapes. A change to
any shape here is an **interface change**: its own small PR into `integration/next`, listing the
callers, with `tests/test_contracts.py` updated in the same PR.

All times are epoch seconds unless the name ends in `_s` (a duration in seconds) or `_ms`.
Probabilities are 0..1. Scores and values are 0..100 on the Scorer's scale.

## 1. One tick, one world

```
          async, off the tick                         the tick (every polling.hot_s = 3 s)
 ┌────────────────────────────┐   ┌──────────────────────────────────────────────────────────────┐
 │ Sources (ESPN poll/FastCast)│──▶│ 1 Clock(now) + Feeds.snapshot()                               │
 │ Reasoner (LLM, ≤ every 30 s)│   │ 2 MomentModel.predict / BreakModel.forecast   per live game   │
 │ Reel builder (ffmpeg)       │   │ 3 SyncModel.estimate                          per target      │
 │ Watchdog (≈60 s) Supervisor │   │ 4 SwitchPaths.paths                           per target      │
 │   (≈10 s)                   │   │ 5 Scorer.score (reads 1-2)                                    │
 └────────────────────────────┘   │ 6 Reasoner.view (cached, never waits)                         │
                                  │ 7 ReelProvider.ready   8 Health.report                         │
                                  │ ───────────── WorldState (frozen) ─────────────                │
                                  │ Decider: WorldState ─views.py─▶ CoreState                      │
                                  │ core.DecisionCore.decide(state) ─▶ Action (ev, terms)          │
                                  │ guards (core.guarded via contracts.traced) ─▶ Action           │
                                  │ Controller.submit(...)      ─▶ Trace: decided/guarded/command  │
                                  └──────────────────────────────────────────────────────────────┘
                                   picture confirmed (async) ─▶ DecisionCore.confirmed, Trace: picture
```

**One state per tick.** `Engine.decide` builds one immutable `WorldState`
(`redzone/contracts/world.py`) and passes it to every module. Nobody re-fetches or recomputes its
own copy during the tick, so every module sees the same `now`, delays and costs. Models are
computed in steps 2-4 and only read afterwards.

**One clock.** `Clock` (`clock.py`) is frozen per tick. It converts between
- **wall**: the Mac's time now (or the replay clock in backtests),
- **feed**: when an event happened per the feed (`Play.wallclock`),
- **screen**: when that event is on a channel's picture, `feed + SyncEstimate.delay_s`.

Breaks, entries, reels and replays all convert through `Clock.to_screen` / `to_feed` /
`screen_window`. Nobody writes `+ delay` by hand.

## 2. Interfaces

Each interface lists who implements it, who calls it, and how often. "Today" is the adapter in
`redzone/contracts/legacy.py` that wraps current code, so the contracts work now. Each owner
replaces their adapter with a native implementation.

### DecisionCore: `redzone/decider/core.py` (canonical; owner DECISION)
The decision core's types are defined **only** in `redzone/decider/core.py` and re-exported by
`redzone.contracts`. No other file defines them a second time. These are `DecisionCore`,
`Action`, `CoreState`, `Option`, `Knobs`, and the per-subsystem views `MomentView`, `BreakView`,
`SwitchPath`, `SyncView`, `EntryView`, `ReelView` and `Priority`.

`DecisionCore(knobs, guards).decide(state: CoreState, verbs) -> Action(verb, target, ev, ev_stay,
reason, path, rewind_s, return_at, kind, terms)`. The verbs are
`stay | switch | visit | reel | replay | return`. One expected-value function (`core.ev`) scores
every candidate as the average points per second on screen over the horizon, minus friction.

- The Decider builds `CoreState` from the tick's `WorldState`. It calls the translators in
  `redzone/contracts/views.py` (`moment_view`, `break_view`, `sync_view`, `reel_option`), which
  are the one place producer outputs become core views.
- It is called once per tick, with a 100 ms budget that includes the guards.
- Every knob lives under `core.*` (`core.Knobs.from_cfg`) with a range in `config.py LIMITS`, so
  the auto-tuner (`redzone/lab/tune.py`) can search it.
- Trip budget (#1), NHL rules (#3), drive priority (#2) and highlight jump are all parts of this
  **one** core, not separate deciders.

### Guard: the hard rules (`core.Guard`; order in `contracts/guard.py HARD_RULES`)
Code: DECISION. Rule list: the user, via INTEGRATION.
- A guard is `core.Guard.check(state, proposed) -> Action | None`. Guards run in order through
  `core.guarded`, and the first that objects wins.
- A guard may only veto an action (turn it into stay or a wait) or force one (crunch time, your
  locked game). It never adds value of its own.
- `contracts.guard.traced()` wraps `core.guarded` and emits one Trace `guarded` event: what was
  proposed, what came out, which rule, ev and ev_stay.

| # | Guard name | Meaning |
|---|------|---------|
| H0 | user control | paused, user lock, manual hands-off, safe mode, focus mode |
| H1 | no overlap | never start a switch while one is in flight (`SwitchPaths.busy()`) |
| H2 | no mid-play cut | never cut away while a play is on screen (`screen.leave_gate`; `core.NoMidPlayGuard`) |
| H3 | no mid-play entry | the whole entry window lands on a dead ball (`cuts.plan_entry` → `EntryView.ok`) |
| H4 | locks | never leave a locked moment, crunch time, or your team late except by the lock rules |
| H5 | trip fits | visit/reel: trip there + back + exit fits the break's cautious remaining length (`BreakView.remaining_p10_s`, `core.reel_fits`) |
| H6 | no dead-air entry | don't switch INTO a break while the current game is live (the #2 commercials fix), except under crunch/your-team rules |
| H7 | fresh data | never move on stale feeds or an unknown TV input |

Only H2 exists as a core guard today. H3/H4 are still enforced inside `Decider`. The DECISION
owner moves each one into a core guard, with golden nights unchanged at each step.

**Merge rule for every PR:** no rise in mid-play entries, plays missed on entry, or dead air
without an explicit, measured reason at the top of the PR. These baselines are protected:

| Metric | Baseline |
|---|---|
| NFL dead air | 8.6 % |
| NFL big plays live | 29.9 % |
| NFL crunch live | 70.0 % |
| NHL entries mid-play | 11 |
| NHL plays missed on entry | 1 |
| NBA entries mid-play | 38 |

### MomentModel / Brain v3 (`moment.py`). Owner: MOMENT/BREAK
`predict(g: GameState, now) -> MomentPrediction(game_id, at, hotness 0-100, key_soon_p,
break_soon_p, horizon_s=60, home_wp, reasons, model_version)`; `forget(game_id)`.
Called by the Scorer and the core in tick step 2, once per live game. Budget: under 5 ms per game,
no network, no LLM. Hype v2 sits behind this same call. Today: `LegacyMoment(Brain)`.

### BreakModel (`breaks.py`). Owner: MOMENT/BREAK
`observe(g)`, `forecast(g, now) -> BreakForecast(game_id, at, p_start, kind: BreakKind,
in_data_now, length_q10_s, length_q50_s, length_q90_s, next_restart_at, why)`.
- Lengths are total dead time with the restart included.
- `next_restart_at` is in **feed** time. Convert it with the Clock.
- Planning uses `q10` for "surely fits" and `q90` for "surely back".

Today: `LegacyBreaks(BreakBook)` uses the measured medians, with q10/q90 set to 0.6×/1.6× the median.

### Reasoner (`reasoner.py`). Owner: LOCAL AI
`view(game_id, now) -> ReasonerView | None`. The view holds `lock_in`, `lock_reason`,
`value_bonus` (clamped to 0..20 by the core), `storyline`, `expectations[(what, within_s, p)]`,
and `at` and `ttl_s=60`.
- It is **advisory only**. It can raise a game's value or ask for a lock-in, and both still pass
  the Guard.
- It runs async, at most every 30 s per game, and only for hot or your-team games. The tick reads
  the cached view and never waits for it.
- RAM: watchdog + reasoner share one base model in `agent/llm_server.py` with one LoRA per job,
  ≤ 1.5 GB total.

### ReelProvider (`reel.py`). Owner: DECISION
`ready(now) -> ReelOffer(target, seconds, value, built_at, clip_ids) | None`, `played(offer)`.
The highlight jump is `ReelJumper.jump_value(world, offer, window_s) -> ReelJump(ev_reel,
ev_stay, cost_s, ok, reason)`, in the core's units (average points per second over the window).

- `redzone/reel.py reel_jump_value` returns those fields as a dict, and `ReelJump.from_dict`
  converts it.
- `views.reel_option(offer, jump)` turns them into the core's `Option(kind="reel",
  reel=ReelView, value_override=ev_reel)`. A jump with `ok=False` offers nothing.
- The core and the H5 guard still check that the reel fits (`core.reel_fits` / `fit_reel`).

### SwitchPaths (`switching.py`). Owner: SWITCHING
`paths(target, now) -> tuple[core.SwitchPath, ...]` (ready paths only) and `busy()`.
- The path type is the core's `SwitchPath(kind, cost_s, cost_p90_s, preloaded)`, with kinds
  `tv_flip | home_flip | in_app_tune | replay_seek | none`.
- Costs run from the command being sent to the picture being confirmed, and update after every
  completed switch (LatencyBook).
- The core ranks paths by `cost_s`. The entry and trip guards budget `switching.deadline()`,
  the cheapest path's p90.

Today: `Controller.switch_costs()` / `deadline_cost()`.

Pending interface request to DECISION: add optional `stick`, `fastest_s` and `samples` fields to
`core.SwitchPath` (defaults keep it backward compatible). The entry guard needs `fastest_s` for
the start of the landing window.

### SyncModel (`sync.py`). Owner: SYNC
`estimate(target, now) -> SyncEstimate(target, delay_s, sigma_s, source, calibrated_at,
behind_s, uncertain)`, `observe(target, sample_s, kind: tap|screen|boundary|mic|predicted|clock, at)`.
- `source` may be `sync` (the per-channel Kalman filter has the channel from automatic signals);
  `uncertain` is True while the channel's signals disagree (treat it like uncalibrated for tight timing).
- `screen = feed + delay_s`, and `sigma_s` grows between calibrations.
- Estimates are read once per target per tick. Samples are written whenever they arrive.

Today: `LegacySync(DelayBook)`.

### Feeds (`feeds.py`). Owner: PLATFORM
`snapshot(now) -> FeedSnapshot(at, games: {game_id: GameFeed(state, fetched_at, source,
stale)})`. Polling runs every 3 s for hot games and every 10 s for others. FastCast pushes land
between polls. Fetching is async, and the snapshot only reads fetched data (50 ms budget).

### Health / Watchdog (`health.py`). Owners: PLATFORM (supervisor, governor, network), LOCAL AI (watchdog model)
`report(now) -> HealthReport(at, faults, fixes, safe_mode, two_stick, away_from_home, ram_mb,
disk_free_mb)`.
- `Fault(code, severity: info|warn|error|critical, component, detail, since, count)`
- `Fix(fault_code, action, target, ok, at, source, note)`

The supervisor runs about every 10 s and the watchdog about every 60 s. Both report in these
shapes. `redzone/platform/faults.py` imports them and does not redefine them.

### Trace (`trace.py`). Owner: PLATFORM
One trace per switch: `TraceEvent(switch_id, stage: decided|guarded|rechecked|command|picture|aborted|failed,
at, component, detail)`, sent to a `TraceSink.emit`. It writes to `data/traces.jsonl`, and
`MemoryTrace` collects events in tests. `guarded` is the decision's guard pass and comes once per decision. `rechecked` is the controller's final safe-moment check just before the command. Each stage appears at most once per switch, so `timings()` is unambiguous. The nightly report and the error budget read traces.
Nothing on the decision path reads them.

## 3. Shared building blocks

1. **WorldState per tick**: see §1 (`world.py`: `WorldState`, `GameView`, `Screen`).
2. **One Clock**: see §1 (`clock.py`).
3. **One config schema**: every module's settings, defaults and limits live in
   `redzone/default_config.yaml` + `config.py LIMITS`. Each owner owns their own section. A new
   numeric key ships with a LIMITS range in the same PR. Saving validates, and versions and
   rollback come from `Config.update` / `Config.rollback` (`data/config_history/`). A setting
   that changes meaning gets a migration in `config.py` (see `RETIRED_SETTINGS`).
4. **Contract tests**: `tests/test_contracts.py` covers every interface against today's
   adapters, and each native implementation adds itself to the same parametrised tests. These run
   in CI on every PR.
5. **Golden nights**: a few replayed nights are frozen with their full decision logs
   (`tests/golden/`, built from the pinned eval archive). A PR that changes any decision on them
   must show the diff and explain it. They run in CI.
6. **Explainability**: every `core.Action` carries `ev`, `ev_stay` and `terms` (the ev's parts:
   rate, friction, stay, guard), its `reason`, and the guard name that changed it. `traced()`
   writes them to the trace. This feeds the phone's "why this game" and golden-night diffs.
7. **Feature flags / kill switches**: one per subsystem under `flags.*` in config (`flags.py`
   `Flag`). They can be switched from the phone and A/B tested in replays by overriding the key.
   A flag that is off means the subsystem runs in its degraded mode, never an error. Defaults are
   all `true` ("allowed to run"), and the subsystem's own setting still decides whether it is on.
8. **Latency budget per tick** (`budget.py`, ms): feeds 50, models 150, sync 10, paths 20,
   reasoner 5, decision 100, command 250, about 585 ms total within a 3 s tick. `TickTimer`
   records any overrun, and an exception returns that part's degraded result. After three
   overruns in a row, the supervisor turns that part's flag off and says so on the phone.
9. **Degraded modes** (`flags.DEGRADED`): defined for every flag, plus `one_stick`,
   `feed_down`, `away_from_home` and `tv_unreachable`. The end-to-end night test runs each one.

## 4. Owners

| Owner | Thread | Scope |
|---|---|---|
| DECISION | Build one decision core | decision core + guard, trip budget (#1), NHL rules (#3), drive priority (#2), highlight jump, reel |
| MOMENT/BREAK | Rebuild the brain lean and sharper | Brain v3, Hype v2, break lengths/quantiles |
| LOCAL AI | Train watchdog model for unseen errors | reasoner, watchdog model, teacher/student, LLM server |
| SWITCHING | Switch games instantly via TV input | switch paths + measured costs, TV flip, preloading, tuners, channel discovery |
| SYNC | Predict live delay per channel | delay/sync predictor, screen evidence (OCR, boundary, mic) |
| PLATFORM | Harden errors and speed up switches | sources, error handling, supervisor, governor, network awareness, observability, deploy, service |
| INTEGRATION | Run the integration train (this) | contracts, engine glue, config schema, CI, pinned eval data, scorecards, golden nights, end-to-end night test, integration train |

### File ownership (one owner per file)

Anyone else changes a file only through its owner (a PR the owner reviews) or through a reviewed
interface change. Tests belong to the owner of the module they test.

| Owner | Files |
|---|---|
| INTEGRATION | `redzone/contracts/*` (core types excepted: they are DECISION's), `redzone/engine.py`, `redzone/config.py`, `redzone/default_config.yaml` (owners edit their own sections), `redzone/models.py`, `redzone/lab/{backtest,sportsdata,sportsgrade,cutgrade,datasets,optimize,replaycheck}.py`, `redzone/lab/tune.py`, `redzone/demo/*`, `scripts/{eval,eval_sports,backtest,replay_check}.py`, `tests/test_contracts.py`, `tests/golden/*`, `tests/e2e/*`, `.github/workflows/*`, `data/eval_config.yaml`, `docs/{architecture,status,integration}.md`, `docs/cloud-build-prompts.md` |
| DECISION | `redzone/decider/core.py` (canonical decision types), `redzone/decider/decider.py`, `redzone/decider/cuts.py`, `redzone/decider/screen.py` (DelayBook moves to SYNC, see below), `redzone/scoring/{engine,drive,gameflow,garbage}.py`, `redzone/reel.py`, `redzone/plan.py`, `redzone/bets.py`, `redzone/fantasy.py`, `redzone/userstate.py`, `scripts/plan_tonight.py` |
| MOMENT/BREAK | `redzone/scoring/brain.py`, `redzone/scoring/breaks.py`, `redzone/lab/brainfit.py`, `scripts/{train_brain,build_training,eval_brain}.py`, model files under `models/` |
| LOCAL AI | `agent/*` (incl. `agent/llm_server.py`, `agent/train/*`, `agent/com.redzone.agent.plist`), reasoner modules |
| SWITCHING | `redzone/control.py` (all of it, flips and CEC included), `redzone/standby.py`, `redzone/tv.py`, `redzone/tvsetup.py`, `redzone/tuners/*`, `redzone/discovery/*`, `redzone/channels.py`, `scripts/{tv_setup,stick_cec,tv_practice,prime_flip_probe,prime_search_trial,find_yttv_code,providers_check,screen_lab}.py`, `tools/hdmi-cec-probe`, `docs/instant-switching.md` |
| SYNC | `redzone/screen/*` (audio, boundary, scorebug), `redzone/screen_ocr.py`, `scripts/vision_ocr.swift`, new `redzone/sync/*` |
| PLATFORM | `redzone/sources/*`, `redzone/supervisor.py`, `redzone/autonomy.py`, `redzone/store.py`, `redzone/recorder.py`, `redzone/recorded.py`, `redzone/views.py`, `redzone/web/*`, `redzone/__main__.py`, `redzone/platform/*`, `scripts/{service,service_runtime,doctor,rz,download_recent,download_season,deploy}.py`, `scripts/install_service.sh`, `deploy/*`, `docs/service-operation.md` |

Notes:
- `redzone/decider/screen.py` holds both the Timeline/leave gate (DECISION) and `DelayBook`
  (SYNC). SYNC moves `DelayBook` to `redzone/sync/delay.py` behind `SyncModel`, and DECISION
  reviews that one move. Until then SYNC's changes to `DelayBook` go through DECISION.
- `scripts/service.py` belongs to PLATFORM. PR #5's small edit to `check()` is a contribution
  that PLATFORM reviews.

## 5. Sequence (by dependency)

| Stage | What | Who | Gate to the next stage |
|---|---|---|---|
| S0 | Contracts + stubs + contract tests (this), CI, pinned eval data, golden nights | INTEGRATION | stubs on `integration/next`, CI green |
| S1 | Real numbers behind the interfaces: MomentModel/BreakModel quantiles, SyncModel, SwitchPaths measured costs, Health/Trace emitters | MOMENT/BREAK, SYNC, SWITCHING, PLATFORM | each passes its contract tests + scorecards not worse |
| S2 | The Decider builds `CoreState` from `WorldState` via `views.py`, and the hard rules move into core guards; #1/#3/#2 live inside it | DECISION | golden-night diffs explained, 0 mid-play cuts, no rise in entries mid-play / missed on entry / dead air |
| S3 | Plug-ins: highlight jump (`ReelJumper`), reasoner lock-in (`ReasonerView`) | DECISION, LOCAL AI | flags A/B on replays |
| S4 | End-to-end replayed night with everything on, every degraded mode; release PR | INTEGRATION | beats today on every status line, or each trade-off is explained |

A thread blocked on an earlier stage stops coding features and writes that stage's tests and
measurements instead.

## 6. One integration branch

- Every PR targets `integration/next`, not `codex/resume-redzone`.
- Rebase on `integration/next` at least every few hours. Merge small and often, and don't sit on a
  big private diff.
- A PR merges only when it is green **on `integration/next`**: CI, contract tests, golden nights,
  the end-to-end night test, and all scorecards (NFL/NBA/NHL + the Nov 1 mixed demo) on the
  pinned archive.
- INTEGRATION merges one PR at a time, re-runs everything, records the result in
  `docs/integration.md`, and drops a PR that makes the combination worse even if it won alone.
- The end state is one release PR from `integration/next` into `codex/resume-redzone`.
- Before opening any PR, run a self-review for edge cases, races, exceptions, config validation,
  and tests that would fail without the fix, and list known risks in the PR.
