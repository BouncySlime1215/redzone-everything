# Research index

Back to [docs/README.md](../README.md). All research is read-only analysis: no entry here is a measured RedZone result unless it says `[local]`.
Newest first. "Affects" = repo files a follow-up would touch (from each note's own mapping).

## Nov 8, 2026 (the ten topic notes)

### [firetv-switching](2026-11-08/firetv-switching.md) - Fire TV app/channel switching over adb
1. New Fire TV sticks (4K Select, 2026 HD, 3rd-gen 4K) run Vega OS, not Android; adb control unproven. Replace only with Fire OS models.
2. YouTube TV activity names drifted in 2026; keep the package-only VIEW intent.
3. Display-on and warm app is about 3 s vs about 11 s to tune (ADBTuner log); Fire OS kills apps for memory, so budget cold starts.
- Affects: `redzone/tuners/adb.py` (`Stick.identify`), `redzone/control.py` (`_stick_watchdog`), `tuners/providers`, `yttv_seek.py`.
- Open: does Vega expose `adb shell`? Forum numbers are anecdotal; nothing re-tested on Nick's sticks.

### [samsung-tv-switching](2026-11-08/samsung-tv-switching.md) - Samsung TV input switching
1. SmartThings free API ended Nov 3; paid plan needed by Dec 15 2026 (about $5/mo, price unconfirmed). `SmartThingsAuth` is an "API Access App".
2. Fastest path is Tizen websocket `KEY_HDMI1/2` on an open socket (about 50 ms key); picture time is the HDMI handshake (InstaPort claims under 1 s).
3. No independent latency measurement exists; run a timing test on the TV (rec R1). Multi View likely unsupported on a 6 Series.
- Affects: `redzone/tv.py`, `docs/instant-switching.md`, `docs/timing.md`.
- Open: exact TV model not recorded; country eligibility for the paid plan; whether a 24 h PAT stays free; Input Signal Plus effect.

### [live-data-feeds](2026-11-08/live-data-feeds.md) - lowest-latency free feeds per league
1. ESPN polling is 35-50 s late; NBA liveData (S3 mirror) and NHL api-web are faster; ESPN fastcast is the only push channel (NFL/CFB main route).
2. No free official NFL live feed; no feed has a "commercial break" flag, so infer breaks from timeout/period events + clock + OCR.
3. Only NBA `timeActual` and MLB `endTime` give a true event clock to measure lag against. Every league forbids scraping beyond personal use.
- Affects: `redzone/sources/fastcast.py`, `espn.py` (`stoppage_for`), `docs/timing.md`, `docs/data-sources.md`.
- Open: fastcast for NFL/NHL/MLB unverified; NHL CDN can serve a 19 s old copy; MLB latency unpublished; Super Bowl article labelled LX vs LIX.

### [stream-delay-sync](2026-11-08/stream-delay-sync.md) - measure/control stream delay to +-1 s
1. Player-side relative clocks (YTTV edge E, Prime epoch position) have sd about 0.1-0.5 s: biggest gain, need one absolute anchor per tune.
2. The error floor is the reference timestamps (feed lag, `adb screencap` 2-7 s), not the Kalman filter; bracket each capture and use the midpoint.
3. Model delay as constant plus jump events (tune/seek/pause/stall); fit the running game clock over 10-20 frames instead of single reads.
- Affects: `redzone/decider/sync.py`, `redzone/sync_edge.py`, `redzone/screen/scorebug.py`, `docs/sync/yttv_tests/`.
- Open: HDMI capture and HDCP; audio fingerprint reference; captions only a sanity check; nothing run on the TV.

### [leverage-excitement](2026-11-08/leverage-excitement.md) - what is most worth watching now
1. Probable grader blind spot: `lab/leverage.py:high_leverage()` has no CFB/CBB keys and assumes 4 periods; may undercount high-leverage mid-play leaves.
2. WP is noisy; rank on smoothed forward suspense `E[(WP_next-WP_now)^2]`, not raw deltas. Suspense beats surprise for switching.
3. No published cross-sport normalisation exists; the percentile design is a hypothesis.
- Affects: `redzone/lab/leverage.py`, `lab/sportsgrade.py:185`, `lab/backtest.py:1133`, `scoring/gameflow.py`, `scoring/engine.py`, `crunch.py`.
- Open: blind spot not yet tested (read only); "Solow 2024 NBA" cited in the playbook is UNVERIFIED.

### [policy-learning](2026-11-08/policy-learning.md) - learn a switching policy safely
1. Do not start with RL: the oracle replay answers counterfactuals exactly. Imitate the oracle with DAgger.
2. Wrap in the hard-rule shield plus a conformal risk-control layer; judge in shadow mode vs the rule decider.
3. OPE (IPS/DR) only for human-signal data; live logs have p=1.0, so add epsilon-explore among safe near-ties only if Nick wants a live loop.
- Affects: `noggin/pick.py`, `scripts/train_noggin_pick_dagger.py`, `redzone/lab/oracle.py`, `lab/offpolicy.py`, `docs/distill.md`.
- Open: staged plan is inference; judge-distillation needs gold labels.

### [on-device-perception](2026-11-08/on-device-perception.md) - live/dead/replay/commercial cheaply
1. Score bug presence + clock running/stopped is free; add a "bug gone" state.
2. Mic next: YAMNet class scores (3.7M weights) as extra features; frozen MobileCLIP2-S0 (about 23 MB fp16, estimate) plus a small head trained on Nick's nights.
3. Small VLMs and PaddleOCR are not worth RAM; use them on Kaggle for labels only.
- Affects: `redzone/screen/tap_bug.py`, `screen/audio.py`, `screen/scorebug.py`.
- Open: no RSS or latency measured on this Mac; public datasets are soccer-heavy; nothing proven for NFL/CFB/NBA.

### [catch-up-story](2026-11-08/catch-up-story.md) - drive catch-up when joining late
1. Industry converged on a short key-plays playlist then auto-drop to live (Prime Rapid Recap: 2 min, 13 clips max). No retention data published.
2. Build text-first from play-by-play; ESPN clips post 4.5-14 min late. Rejoin on the next dead ball.
3. Keep faster-playback out of v1 (evidence is lecture-only).
- Affects: `redzone/reel.py`, `decider/buffered.py`, `buffer_rc8.py` (new drive selector + story planner).
- Open: DVR seek cost unmeasured; gates to pre-register; two sources snippet-only.

### [viewer-experience](2026-11-08/viewer-experience.md) - UX of automated multi-game viewing
1. Interrupt only at boundaries (matches Rule #1); each cut has a resumption cost, so cue at arrival.
2. Predictable rules and small user control raise trust; be quiet (notifications disrupt, ADHD-like symptoms); tap feedback 0.1 s / progress 1 s / explain 10 s.
3. Nick's 7.5 jumps/min bar is far faster than any studied setting; the research cannot call it wrong, so measure landing cost.
- Affects: switch pacing, phone UI (`lanes/experience.md`), explanations/"why I switched".
- Open: no published RedZone switch rate; no study of score bugs after a cut; no ADHD-specific multi-game study. Links `research/attention-science.md`, which exists only in the main checkout.

### [home-reliability](2026-11-08/home-reliability.md) - 8 GB Mac reliability
1. Governor reads RSS, which falls under swap; measure footprint instead. `swap_eco_mb=1500` is absolute and may be stuck on.
2. No leak/hang telemetry (`tracemalloc`, `faulthandler`, loop lag); `ru_maxrss` is a lifetime peak.
3. Logging can block the loop; `restart_service` has no own intensity limit; add planned rejuvenation, chaos drills, burn-rate alert.
- Affects: `redzone/governor.py`, `agent/watchdog.py`, `agent/policy.py`, `redzone/platform/breaker.py`, `scripts/service_runtime.py:78`, `scripts/deploy.py`.
- Open: all thresholds are guesses; calibrate with one week of rec-3 logging.

### [redzone-numbers](2026-11-08/redzone-numbers.md) - how busy a RedZone Sunday really is (measured)
1. 19 recorded NFL Sundays: 10.1 touchdowns and 14.5 red-zone trips per hour (RedZone's own lifetime figure: 8.9 TD/h).
2. Showing every event needs a median 5 cuts per 10 min (p90 9, max 14); 75 per 10 min is about 15x that.
3. RedZone now shows ~9 s/hour of split-screen ads; our 0 commercial seconds is stricter.
- Affects: the pass bar (fix plan N5), scorecards, `scripts/northstar.py` metrics.
- Open: NFL only; measure other sports the same way.

## Nov 8, 2026 (workflow notes)

### [agent-coordination-and-rationale](agent-coordination-and-rationale.md) - legible and safe for many agents
1. Isolated worktrees, one planner, merge through a test gate (CAID +25.6/+14.7 pts; chatty agents about 30% worse; 41.7% PR conflict rate).
2. Graph-shaped repo map (calls/data/machine edges, `--focus`, `--impact`) beats prose overviews; keep CLAUDE.md rules-only.
3. Record why: required `why` on transitions, ADR-style decisions with status, `Why:` in commits.
- Affects: `scripts/citymap.py`, `redzone/fsm.py`, `docs/citymap.md`, `docs/memory/decisions.md`, CLAUDE.md.
- Open: no RedZone measurement; 40 sources checked by abstract only; 180 unowned modules.

### [opus-5-5-harness-and-time](opus-5-5-harness-and-time.md) - spend less, wait less, test without a game
1. Biggest lever: replay recorded nights through the real decider with fake sticks as a deploy gate ("time machine", 8-10 h).
2. Route reads to Haiku (`Explore`), fix cache-breaking habits, Monitor not polling for deploy watch, auto mode with hard edges on adb/deploy/push.
3. Candidate-in-shadow on live nights generalises `buffer_shadow.py`.
- Affects: `.claude/agents`, hooks/settings, `scripts/deploy.py`, `buffer_shadow.py`, `redzone/clock` seam.
- Open: every hour and saving figure is an estimate; global settings are Nick's call.

## Nov 8, 2026 (agent rules and coordination; read in full before the coordination step)

### [agents/rule-files](2026-11-08/agents/rule-files.md) - what in CLAUDE.md-style files changes behaviour
1. Rule files are advice; zero-exception rules belong in hooks, deny rules, branch protection or CI (Anthropic docs; Krupa n=1).
2. Rules erode: compliance odds -5.6% per generated function; `/compact` kept 53% of rules after one pass, 10% after five.
3. Plain imperatives win on Opus 5 (78%); conditional phrasing worst; no measured effect of file size or position (25-500 lines).
- Affects: CLAUDE.md (outline to ~40 lines in 5 blocks), new `.claude/settings.json` hooks, `.claude/rules/*.md`, 3 subagents, side-effect skills.
- Open: studies use trivial marker rules and older models; X posts not opened (HTTP 402).

### [agents/coordination-sota](2026-11-08/agents/coordination-sota.md) - coordinating many agents to one goal
1. Keep the acceptance signal outside agents' reach; generic rejections halve gate evasion (40.5% vs 20.3%).
2. A fresh-context verifier beats builder self-tests; check constraints separately (34% of test-passing repairs break review rules).
3. Cap parallelism by review capacity (default 3 builders + 1 verifier); one writer per file (72% vs 100% fact survival); BLOCKED counts as success (hacking 23.6% -> 5.3%).
- Affects: task cards `docs/cards/`, claims `docs/claims/`, protected paths, CI Rule #1 checks, the integration train.
- Open: few Oct-Nov 2026 practitioner reports; arXiv 2609.04217 date mismatch.

### [agents/safe-agents-realtime](2026-11-08/agents/safe-agents-realtime.md) - agents on invariant-heavy, hardware code
1. Block, don't ask: deny hooks + sandbox for adb, deploy, pushes; prompts barely change cheating (METR).
2. Our fsm edge tables are too loose (`control.preload_slot` allows every move); Rule #1 needs cross-machine monitors + one commit gate at `_flip_to`.
3. Hypothesis stateful tests with FakeStick, mutation tests in CI; TLA+/P not justified yet (~46% conformance).
- Affects: `redzone/control.py` `_flip_to`, `redzone/machines/control.py`, new `redzone/invariants.py`, `tests/`, `.claude/hooks/`.
- Open: no RedZone measurement; no X posts opened.

### [agents/memory-and-handoffs](2026-11-08/agents/memory-and-handoffs.md) - continuity across sessions
1. `context_pack.py` pastes all 154 KB of decisions (~50k tokens); target a 12k-token budgeted pack.
2. Context-bearing handoffs cut agent events 20-59%; use a 60-80 line capsule with YAML front matter and "Verify first".
3. Split decisions.md into topic files + an index of standing invariants with `status/scope/anchors`; `why.py` lookup; anchor-drift warnings in CI.
- Affects: `docs/handoff.md`, `docs/memory/decisions.md`, `scripts/context_pack.py`, `scripts/check_memory.py`, new `scripts/why.py`.
- Open: evidence is mostly abstracts and synthetic repos.

## Nov 3-4, 2026 (earlier)

| Doc | Topic | Top finding | Affects | Open |
|---|---|---|---|---|
| [00-PLAN](../archive/research/2026-11-03/00-PLAN.md) | merged queue | live app has about 0 s data lead; backtests assumed `feed_lag=8` | `scripts/eval.py`, `eval_sports.py` | rerun with measured lags |
| [01-inputs](../archive/research/2026-11-03/01-inputs-and-sources.md) | inputs | ESPN plays carry wallclock in all 6 leagues | `redzone/sources/*` | marked UNVERIFIED rows |
| [02-capabilities](../archive/research/2026-11-03/02-capabilities-and-tasks.md) | model tasks | timing = GBM+rules; LLM = words only | `redzone/reasoner/*` | distill branches |
| [03-switch-timing](../archive/research/2026-11-03/03-switch-timing.md) | timing | data lead decides everything | `decider/*`, `lab/*` | per-league numbers from 1 week |
| [04-architecture](2026-11-03/04-architecture-distillation.md) | distill | 1.5-2 GB total on the M2 | `agent/distill/*` | estimates to confirm |
| [05-eval](2026-11-03/05-eval-and-learning-loop.md) | eval | pooled point-estimate gates flicker; add CIs | `scripts/release_gate.py` | NHL 97.8% lower bound 93.7% |
| [watch-science-playbook](watch-science-playbook.md) | attention | care x suspense; replay on surprise | scoring, `lab/worth.py` | Solow 2024 UNVERIFIED |
| [arenaswap-compare](arenaswap-compare.md) | competitor | ideas only, no code taken | scoring | licence status unclear |

## Cross-cutting conclusions

- Rule #1 is backed from three sides: interruption theory (viewer-experience), dead-ball timing (03-switch-timing, live-data-feeds), and a shield around any learned policy (policy-learning).
- Data lag is the main constraint: ESPN polled is 35-50 s late (00-PLAN, live-data-feeds, stream-delay-sync). Prefer league-origin or push feeds and honest `feed_lag` in evals.
- Delay accuracy comes from better reference timestamps and relative clocks, not a fancier filter (stream-delay-sync, on-device-perception: score bug plus clock).
- Test without a live game: whole-night replay and shadow mode appear in opus-5-5, policy-learning, perception, catch-up and the plan.
- Ship OFF, shadow first, for anything learned or perceptual (policy-learning, on-device-perception, catch-up-story).
- Explicit state machines plus recorded "why" make behaviour auditable (agent-coordination, opus-5-5, home-reliability).
- Cheap signals first on the 8 GB Mac: bug/clock/mic before models; footprint not RSS (perception, home-reliability).
- Single adb client per stick and Fire OS only (firetv-switching, home-reliability watchdog).
- Hardware and vendor risks have dates: SmartThings Dec 15 2026, Vega sticks (samsung-tv, firetv).
- Suspense (not surprise) ranks games; surprise drives replays (leverage-excitement, watch-science-playbook, viewer-experience).

- Agents (the four agent notes agree): enforce with hooks/CI not prose; keep the grader sealed; one writer per file; verify with a fresh context; keep always-loaded memory small and re-read from disk.

## Conflicts between research docs

- **Feed lag:** 00-PLAN says use FastCast for every league and league feeds; live-data-feeds says fastcast is verified only on WCBB and NHL api-web can be 19 s cached. Treat the older plan as a hypothesis.
- **SmartThings:** 03-switch-timing and `instant-switching.md` rely on SmartThings readback; samsung-tv-switching says the cloud path is paid after Dec 15. Needs a decision, not code.
- **Solow 2024 NBA:** cited in watch-science-playbook; leverage-excitement found no such paper.
- **Speed vs comprehension:** Nick's 7.5 jumps/min (viewer-experience) pulls against the orientation cues and key-plays playlist in catch-up-story and the "be quiet" principle; resolve by measuring landing cost.
- **CLAUDE.md length:** agent-coordination says hard rules in the first 20 lines; rule-files found no measured position or size effect. Resolution: position is unproven, so keep hard rules in the root file (survives compaction) and enforce them with hooks.
- **Parallelism:** CLAUDE.md allows 5 threads; coordination-sota recommends 3 builders + 1 verifier by default.
- **Self-improvement loop:** 05-eval-and-learning-loop favours a live loop; policy-learning says do offline oracle imitation first, live exploration only if Nick wants it.
