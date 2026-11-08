# docs/ index

Find any doc in under 30 seconds. Every `.md` under `docs/` is listed once below (non-.md assets are summarised per folder).
Marks: **[STALE]** = older than the live docs, check before trusting. **[GEN]** = generated, do not hand-edit.
Not in this worktree (untracked in the main checkout, not listed): `docs/club/`, `docs/compute/`, `docs/livelab-2026-11-04.md`, `docs/plans/2026-11-04-noggin-master-plan-v2.md`, `docs/research/attention-science.md`.

## Start here

- [PLAN.md](PLAN.md) - the only plan: phases, process gate, test queue link, questions for Nick
- [STANDARD.md](STANDARD.md) - how every report, finding and doc is organized (checked in CI)
- [agents/INDEX.md](agents/INDEX.md) - [GEN] every agent report by stream, status and gate items
- [test-queue.md](test-queue.md) - what gets tested on the TV, in order

- [../CLAUDE.md](../CLAUDE.md) - hard rules, North Star, branches, commands (read first)
- [handoff.md](handoff.md) - live handoff; newest status is section 5, updated by the overseer (Nov 7)
- [northstar.md](northstar.md) - [GEN] where we are vs perfect (measured, 2026-11-06)
- [roadmap.md](roadmap.md) - [GEN] what next, ranked from roadmap.json
- [research/README.md](research/README.md) - research index: findings, affected files, open questions
- [plans/2026-11-08-fix-plan.md](plans/2026-11-08-fix-plan.md) - what to fix first (from the Nov 8 audits) and next steps, immediate and long term
- [coordination.md](coordination.md) - how agents work together: roles, claims, hard rules and what enforces them, merge path
- [system-map.md](system-map.md) - how the parts connect; `citymap.py --focus/--impact`, `/api/machines`, `scripts/why.py`
- [audits/](audits/) - the six Nov 8 reviews (decider, switch path, devices, feeds/scoring, platform, rules/config)

## Live status

- [status.md](status.md) - board and queue **[STALE]** last update Nov 4 05:45 UTC; prefer handoff.md section 5
- [integration.md](integration.md) - integration train: how PRs merge one at a time
- [ci.md](ci.md) - what GitHub Actions runs
- [tonight-checklist.md](tonight-checklist.md) - at-home setup checklist (Nov 3) **[STALE]** still linked by runbooks
- [service-operation.md](service-operation.md) - run the app without Codex (launchd, logs)
- [live-nights/README.md](live-nights/README.md) - real nights on Nick's TV: summary.json per night
- [nights/2026-11-04.md](nights/2026-11-04.md) - night health report
- [nights/2026-11-05.md](nights/2026-11-05.md) - night health report
- [nights/2026-11-06.md](nights/2026-11-06.md) - night health report
- Data (not .md): `northstar-history.jsonl`, `roadmap.json`, `releases.json`, `catchup_ratchet.json`, `gate_baseline.json`, `grader_freeze.json`, `contract_baseline.json`, `families.json`, `scorecards-2026-11-02.json`, `tonight-risk-findings.json`, `label_checks/*.json`

## Architecture & contracts

**Core contracts and maps**
- [architecture.md](architecture.md) - interfaces, owners, release gate (section 7); read before any interface change
- [citymap.md](citymap.md) - [GEN] import graph, owners, orphans (`scripts/citymap.py`; also citymap.json, citymap_tags.json)
- [playbooks.md](playbooks.md) - given situation X, do Y: named, tested playbook entries
- [lanes/experience.md](lanes/experience.md) - lane 5: phone v2, replay viewer, multiview (flag `phone.v2`, off)
- [memory/onebrain-rules.md](memory/onebrain-rules.md) - one-brain rules: how modules stay one connected system
**Noggin (the one decision brain)**
- [noggin.md](noggin.md) - the Noggin: one brain, three layers
- [noggin-foundation-model.md](noggin-foundation-model.md) - foundation model (NFM) design
- [onebrain-migration.md](onebrain-migration.md) - folding every scorer into the Noggin
- [brain3/REPORT.md](brain3/REPORT.md) - Brain v3 per-league models and gaps (Nov 3; STATUS.md and calibration PNGs beside it)
- [brain3/STATUS.md](brain3/STATUS.md) - Brain v3 status note
- [reasoner.md](reasoner.md) - live game reasoner status (stopped early, Nov 3)
- [distill.md](distill.md) - big teacher, small students on Kaggle
- [decision-core-next.md](decision-core-next.md) - decision core findings (Nov 3): stoppage lengths by type
- [decision-records.md](decision-records.md) - Teacher nightly decision-record JSONL format
**Switching and timing**
- [timing.md](timing.md) - measured switch timing: feed lag, landing offsets (largest timing doc)
- [instant-switching.md](instant-switching.md) - TV changes input while sticks keep playing (HDMI)
- [provider-routing-readiness.md](provider-routing-readiness.md) - provider routing and navigation evidence
- [prime-multiview-calibration.md](prime-multiview-calibration.md) - Prime NBA multiview live calibration plan (flag off)
- [device-trial-2026-11-02.md](device-trial-2026-11-02.md) - first supervised device trial (ADB keys) **[STALE]**
**Eval, grading, testing**
- [eval-data.md](eval-data.md) - pinned eval dataset (eval-data-v1) and how to fetch
- [grading.md](grading.md) - training and grading rules ("grading even sharper")
- [replay-testing.md](replay-testing.md) - testing when no live games: replay vs archived feed
- [verification-2026-11-02.md](verification-2026-11-02.md) - software verification, archived-event sims **[STALE]**
**Data sources** (folder `datasources/`; also .json ledgers `scout.json`, `pbp-ledger.json`, `terms.json`)
- [data-sources.md](data-sources.md) - license, terms and rate limits (summary)
- [datasources/sources-catalog.md](datasources/sources-catalog.md) - Data sources catalog (Noggin world model)
- [datasources/ingest-plan.md](datasources/ingest-plan.md) - Ingest plan: the Kaggle data factory for the Noggin world model
- [datasources/data-ledger.md](datasources/data-ledger.md) - Data ledger
- [datasources/scout-ledger.md](datasources/scout-ledger.md) - Data scout ledger
- [datasources/pbp-ledger.md](datasources/pbp-ledger.md) - Data Division A: play-by-play landed
- [datasources/tokenizer-v2-sources.md](datasources/tokenizer-v2-sources.md) - Tokenizer v2: where each context field comes from (scout, Nov 5 2026)
- [datasources/media-metadata.md](datasources/media-metadata.md) - Data Division C: media metadata (which plays got clipped, and how much people wa
- [datasources/web-text-h.md](datasources/web-text-h.md) - Data H: web-scale sports text (plan, ledger, crew)
- [datasources/attention-g.md](datasources/attention-g.md) - Data Division G: attention and hype at scale
- [datasources/context-tokens.md](datasources/context-tokens.md) - Context fact shards (Data Division B -> lane 1 vocab v2)
- [datasources/context-b1-landing.md](datasources/context-b1-landing.md) - Crew B context: first landing from GitHub-hosted data (worker B1)
- [datasources/context-buzz.md](datasources/context-buzz.md) - Context: BUZZ (Wikipedia pageviews, Wikipedia edits, GDELT news)
- [datasources/context-nba.md](datasources/context-nba.md) - NBA context facts (Data Crew B, worker B4)
- [datasources/context-stakes-weather.md](datasources/context-stakes-weather.md) - Context: stakes and conditions (Crew B, worker B3)
- [datasources/markets/contract.md](datasources/markets/contract.md) - Markets division (Data type D): tick contract
- [datasources/markets/feed-race-tnf.md](datasources/markets/feed-race-tnf.md) - TNF feed race, JAX@BAL, Thu Nov 5 2026 (tracker item 4)
- [datasources/markets/kalshi.md](datasources/markets/kalshi.md) - Kalshi (markets division, worker D2)
- [datasources/markets/ledger.md](datasources/markets/ledger.md) - Markets ledger (Data Division D)
- [datasources/markets/lines.md](datasources/markets/lines.md) - Markets D3: free odds history and line moves (open -> close, forward snapshots)
- [datasources/markets/polymarket.md](datasources/markets/polymarket.md) - Polymarket (markets worker D1)
**Ops, deploy, recovery**
- [auto-fix.md](auto-fix.md) - Claude repairs what the watchdog can't
- [new-environment.md](new-environment.md) - moving to a new Claude account / machine
**Sync test suite (YTTV)**
- [sync/yttv_tests/README.md](sync/yttv_tests/README.md) - how to run the stream-delay tests (scripts in ads/ fusion/ mechanics/ truth/ ui/; work/ = dry-run logs)

## State machines

- [state-machines.md](state-machines.md) - [GEN] all 52 machines: states, edges, owners (`scripts/state_machines.py`; live `GET /api/machines`)
- [plans/2026-11-08-after-state-machines.md](plans/2026-11-08-after-state-machines.md) - next steps after fsm conversion, checked against research (graph map, why-codes, time machine)

## Memory (decisions, lessons, runbooks)

- [memory/decisions.md](memory/decisions.md) - every Nick decision, newest first (154 KB; CI-guarded)
- [memory/lessons.md](memory/lessons.md) - bugs we hit and why; add one after every fix
- [memory/glossary.md](memory/glossary.md) - the words we use
- [memory/runbooks/README.md](memory/runbooks/README.md) - runbook index
- [memory/runbooks/deploy.md](memory/runbooks/deploy.md) - Deploy
- [memory/runbooks/rollback.md](memory/runbooks/rollback.md) - Rollback
- [memory/runbooks/rc-cut.md](memory/runbooks/rc-cut.md) - Cut a release candidate
- [memory/runbooks/kaggle-run.md](memory/runbooks/kaggle-run.md) - Kaggle run
- [memory/runbooks/stick-offline.md](memory/runbooks/stick-offline.md) - A stick looks offline
- [memory/runbooks/tv-setup.md](memory/runbooks/tv-setup.md) - TV setup (TV-side input switching)
- [memory/runbooks/usage-pause.md](memory/runbooks/usage-pause.md) - Usage pause
**Claude-memory mirror** (copies of Nick's standing preferences)
- [context/claude-memory/MEMORY.md](context/claude-memory/MEMORY.md) - memory note
- [context/claude-memory/mac-load-limits.md](context/claude-memory/mac-load-limits.md) - memory note
- [context/claude-memory/no-build-until-explicit-go.md](context/claude-memory/no-build-until-explicit-go.md) - memory note
- [context/claude-memory/redzone-cut-philosophy.md](context/claude-memory/redzone-cut-philosophy.md) - memory note
- [context/claude-memory/redzone-home-network.md](context/claude-memory/redzone-home-network.md) - memory note
- [context/claude-memory/redzone-no-peacock.md](context/claude-memory/redzone-no-peacock.md) - memory note
- [context/claude-memory/redzone-north-star.md](context/claude-memory/redzone-north-star.md) - memory note

## Research

Full index with findings, files affected, open questions: [research/README.md](research/README.md). One line each below (date; topic; best finding).

**Agents and workflow**
- [research/agent-coordination-and-rationale.md](research/agent-coordination-and-rationale.md) - Nov 8; many-agent safety. Isolated worktrees + one planner + test-gated merge beat chat coordination (CAID +25.6 pts).
- [research/opus-5-5-harness-and-time.md](research/opus-5-5-harness-and-time.md) - Nov 8; spend less/wait less. Biggest lever: replay whole nights through the real decider before deploy ("time machine").
- [research/2026-11-08/redzone-numbers.md](research/2026-11-08/redzone-numbers.md) - Nov 8; measured pace. RedZone-style needs ~5 cuts per 10 min (p90 9); about 10 TDs and 14.5 red-zone trips an hour.
- [research/2026-11-08/agents/rule-files.md](research/2026-11-08/agents/rule-files.md) - Nov 8; rule files. Rules in CLAUDE.md are advice; hard rules need hooks/CI; plain imperatives work best on Opus.
- [research/2026-11-08/agents/coordination-sota.md](research/2026-11-08/agents/coordination-sota.md) - Nov 8; many agents. Sealed grader, fresh-context verifier, 3 builders + 1 verifier, one writer per file.
- [research/2026-11-08/agents/safe-agents-realtime.md](research/2026-11-08/agents/safe-agents-realtime.md) - Nov 8; invariant safety. Deny hooks first; cross-machine monitors and one commit gate at `_flip_to`.
- [research/2026-11-08/agents/memory-and-handoffs.md](research/2026-11-08/agents/memory-and-handoffs.md) - Nov 8; memory. 12k-token context pack, 60-80 line handoff capsule, decisions split with status/scope.
**Switching hardware and feeds (Nov 8)**
- [research/2026-11-08/firetv-switching.md](research/2026-11-08/firetv-switching.md) - Nov 8; Fire TV adb. New Vega OS sticks may not be adb-controllable; keep sticks Fire OS.
- [research/2026-11-08/samsung-tv-switching.md](research/2026-11-08/samsung-tv-switching.md) - Nov 8; Samsung TV. SmartThings free API ends Dec 15 2026 unless paid (about $5/mo); real switch time is the HDMI handshake.
- [research/2026-11-08/live-data-feeds.md](research/2026-11-08/live-data-feeds.md) - Nov 8; fastest free feeds. ESPN polled is 35-50 s late; NBA liveData and NHL api-web beat it; no feed flags commercials.
- [research/2026-11-08/stream-delay-sync.md](research/2026-11-08/stream-delay-sync.md) - Nov 8; delay to +-1 s. Reference timestamps (feed lag, frame capture), not the Kalman filter, are the error floor.
**Brain, scoring, learning (Nov 8)**
- [research/2026-11-08/leverage-excitement.md](research/2026-11-08/leverage-excitement.md) - Nov 8; leverage models. Likely grader blind spot: lab high_leverage() misses CFB/CBB keys; rank on smoothed forward suspense.
- [research/2026-11-08/policy-learning.md](research/2026-11-08/policy-learning.md) - Nov 8; learn a policy safely. Skip RL: oracle imitation + DAgger + hard shield, judged in shadow mode.
- [research/2026-11-08/on-device-perception.md](research/2026-11-08/on-device-perception.md) - Nov 8; live/dead/replay on 8 GB. Score bug + clock is free; add YAMNet audio; small VLMs not worth RAM.
**Viewer experience and reliability (Nov 8)**
- [research/2026-11-08/catch-up-story.md](research/2026-11-08/catch-up-story.md) - Nov 8; drive catch-up. Key-plays playlist then auto-jump to live; text-first because clips lag 4.5-14 min.
- [research/2026-11-08/viewer-experience.md](research/2026-11-08/viewer-experience.md) - Nov 8; UX of auto-switching. Interrupt only at boundaries, cheap landings, small user control; no published RedZone switch-rate data.
- [research/2026-11-08/home-reliability.md](research/2026-11-08/home-reliability.md) - Nov 8; 8 GB Mac. Governor reads RSS (misleading under swap); add footprint, leak telemetry, restart intensity limit.
**Earlier research (Nov 3-4)**
- [research/2026-11-03/00-PLAN.md](research/2026-11-03/00-PLAN.md) - Nov 3; merged queue. Live app has about 0 s data lead; backtests assumed 35 s.
- [research/2026-11-03/01-inputs-and-sources.md](research/2026-11-03/01-inputs-and-sources.md) - Nov 3; model inputs. ESPN plays carry wallclock in all six leagues.
- [research/2026-11-03/02-capabilities-and-tasks.md](research/2026-11-03/02-capabilities-and-tasks.md) - Nov 3; what models should do. Timing is GBM + rules; the 1.7B LLM writes words only.
- [research/2026-11-03/03-switch-timing.md](research/2026-11-03/03-switch-timing.md) - Nov 3; cut only in dead time. Data lead decides everything.
- [research/2026-11-03/04-architecture-distillation.md](research/2026-11-03/04-architecture-distillation.md) - Nov 3; train on Kaggle, run in about 1.5-2 GB.
- [research/2026-11-03/05-eval-and-learning-loop.md](research/2026-11-03/05-eval-and-learning-loop.md) - Nov 3; gates are pooled point estimates; use confidence intervals.
- [research/watch-science-playbook.md](research/watch-science-playbook.md) - Nov 4; rank on suspense, replay on surprise, care multiplies suspense.
- [research/arenaswap-compare.md](research/arenaswap-compare.md) - Nov 4; ArenaSwap comparison, ideas only, no code copied.
- Scripts (not .md): `research/2026-11-03/analysis/dead_windows.py`, `nhl_race_check.py`

## Plans

- [plans/2026-11-04-noggin-master-plan.md](plans/2026-11-04-noggin-master-plan.md) - The Noggin master plan: becoming THE sports AI
- [plans/2026-11-05-brain-engines.md](plans/2026-11-05-brain-engines.md) - Two 24/7 brain engines: the context and hype data engine, and the test factory
- [plans/2026-11-05-compressed-4day.md](plans/2026-11-05-compressed-4day.md) - Compressed plan: master plan v2, Phases 0-2 by Sun Nov 8
- [plans/2026-11-05-data-division.md](plans/2026-11-05-data-division.md) - Data Division: org chart, id blocks, first sources and pacing
- [plans/2026-11-05-rc8-buffer-gate.md](plans/2026-11-05-rc8-buffer-gate.md) - RC8 gate: the time-shift buffer ON per league (pre-registered before tonight's s
- [plans/2026-11-05-test-factory.md](plans/2026-11-05-test-factory.md) - Test Factory (engine 2): plan
- [plans/2026-11-07-world-model-100m.md](plans/2026-11-07-world-model-100m.md) - Saturday world model (~100M params), plan B data
- [experiments.md](experiments.md) - ranked experiment queue (150 KB)
- [cycles/backlog.md](cycles/backlog.md) - ranked backlogs
- [cycles/idea-bank.md](cycles/idea-bank.md) - idea bank: "100x better"

## Reports & gate history

- [gate-report-rc5.md](gate-report-rc5.md) - RC5 candidate gate triage (latest gate report)
**Pre-registrations** (`prereg/`; scripts/json beside them)
- [prereg/exp-023.md](prereg/exp-023.md) - EXP-023 pre-registration: belief tracker
- [prereg/exp-1704.md](prereg/exp-1704.md) - EXP-1704 pre-registration: Watch Value trained on real human-attention labels
- [prereg/exp-1715.md](prereg/exp-1715.md) - EXP-1715 pre-registration: Game Importance on human attention (market volume), N
- [prereg/exp-1720.md](prereg/exp-1720.md) - Pre-registration: GI v3 NBA, closeness-anchored (WV loop cycle 1)
- [prereg/exp-1721.md](prereg/exp-1721.md) - EXP-1721: oracle reward v2 (big-play definitions + objective weights), pre-regis
- [prereg/exp-1722.md](prereg/exp-1722.md) - EXP-1722: NBA-only oracle big play v2 (pre-registered before eval-data-v3 lands,
- [prereg/exp-1723.md](prereg/exp-1723.md) - EXP-1723: ledger-weighted Watch Value as the windowed oracle's beyond-window val
- [prereg/exp-1724.md](prereg/exp-1724.md) - EXP-1724: does the window length bind? EXP-1723 arms at the PRIMARY feed lag (WV
- [prereg/exp-1800.md](prereg/exp-1800.md) - EXP-1800 pre-registration: recap-aligned importance labels for Watch Value
- [prereg/exp-601.md](prereg/exp-601.md) - Pre-registration: hidden-stick readiness in the replay (RC8 buffer gate)
- [prereg/exp-602.md](prereg/exp-602.md) - EXP-602: VOID (Nick, 21:45 UTC Nov 6)
- [prereg/exp-603.md](prereg/exp-603.md) - EXP-603: hidden-stick readiness with the overlay-scrub entry (Nov 7, 2026)
- [prereg/exp-604.md](prereg/exp-604.md) - EXP-604: EXP-603 plus the post-scrub safe-moment re-check (Nov 7, 2026)
- [prereg/exp-605.md](prereg/exp-605.md) - EXP-605: NBA with YouTube TV buffered and Prime planned at its live edge (Nov 7,
- [prereg/exp-606.md](prereg/exp-606.md) - EXP-606: NFL B75 dwell sweep under the stick model (RC8)
- [prereg/exp-607.md](prereg/exp-607.md) - EXP-607: EXP-605 with the re-sync prior pinning Prime at its live edge (Nov 7, 2
- [prereg/exp-608.md](prereg/exp-608.md) - EXP-608: Prime buffered by pause on the hidden stick (Nov 7, 2026)
- [prereg/v3-reads/README.md](prereg/v3-reads/README.md) - first-pass reads on eval-data-v3 NBA nights (json beside)
- [prereg/v3-reads/your_team_crunch_misses.md](prereg/v3-reads/your_team_crunch_misses.md) - your-team crunch misses, personal use
- [replay-results-20261101.md](replay-results-20261101.md) - Nov 1 replay results (+ .json) **[STALE]**

## Archive / dated notes

**Old gate reports (superseded by gate-report-rc5.md and northstar.md)**
- [gate-report-rc1.md](gate-report-rc1.md) - RC1 gate **[STALE]** superseded by RC2/RC5
- [gate-report-rc2.md](gate-report-rc2.md) - RC2 gate **[STALE]** superseded by RC5
- [gate-report-rc3.md](gate-report-rc3.md) - RC3 gate **[STALE]** deploy-fix only, no app change
**Dated build notes**
- [build-plan-2026-11-03.md](build-plan-2026-11-03.md) - Nov 3 build plan **[STALE]** day is over
- [morning-report-2026-11-03.md](morning-report-2026-11-03.md) - Nov 3 morning report **[STALE]**
- [live-test-wed.md](live-test-wed.md) - Wed Nov 4 live-test script **[STALE]** one-off
- [cloud-build-prompts.md](cloud-build-prompts.md) - cloud session prompts **[STALE]** Nov 3-4
- [cloud-training-prompt.md](cloud-training-prompt.md) - cloud training prompt **[STALE]** Nov 3
**Planner cycles (Nov 4-5; plan/review per cycle, gate-locked-*.md = copied gate tables)**
- [cycles/01/plan.md](cycles/01/plan.md) - Cycle 01 plan (Start Planner), Wed Nov 4, 3:00 AM ET (08:00 UTC)
- [cycles/01/review.md](cycles/01/review.md) - Cycle 01 review (End Planner / Integrator), Wed Nov 4, 08:00 to 09:50 UTC
- [cycles/02/plan.md](cycles/02/plan.md) - Cycle 02 plan (Start Planner), Wed Nov 4, ~10:00 to 13:30 UTC
- [cycles/02/review.md](cycles/02/review.md) - Cycle 02 review (Integrator), Wed Nov 4, 10:00 to 12:20 UTC (lean: usage brake)
- [cycles/03/plan.md](cycles/03/plan.md) - Cycle 03 plan (Start Planner), Wed Nov 4, ~12:15 to 15:30 UTC
- [cycles/03/review.md](cycles/03/review.md) - Cycle 03 review (End Planner / Integrator), Wed Nov 4, 12:15 to 15:30 UTC
- [cycles/04/gate-locked-candidate.md](cycles/04/gate-locked-candidate.md) - gate-locked-candidate.md
- [cycles/04/gate-locked-live.md](cycles/04/gate-locked-live.md) - gate-locked-live.md
- [cycles/04/plan.md](cycles/04/plan.md) - Cycle 04 plan (Start Planner), Wed Nov 4, ~15:30 to 19:00 UTC (before tonight's 
- [cycles/04/review.md](cycles/04/review.md) - Cycle 04 review (End Planner / Integrator), Wed Nov 4, 15:30 to 19:45 UTC
- [cycles/05/gate-locked-candidate.md](cycles/05/gate-locked-candidate.md) - gate-locked-candidate.md
- [cycles/05/gate-locked-live.md](cycles/05/gate-locked-live.md) - gate-locked-live.md
- [cycles/05/plan.md](cycles/05/plan.md) - Cycle 05 plan (Start Planner), Wed Nov 4, 19:55 to 21:25 UTC (90-min clock)
- [cycles/05/review.md](cycles/05/review.md) - Cycle 05 review (End Planner / Integrator), Wed Nov 4, 19:55 to 21:25 UTC
- [cycles/06/gate-locked-candidate.md](cycles/06/gate-locked-candidate.md) - gate-locked-candidate.md
- [cycles/06/gate-locked-live.md](cycles/06/gate-locked-live.md) - gate-locked-live.md
- [cycles/06/plan.md](cycles/06/plan.md) - Cycle 06 plan (Start Planner), Wed Nov 4, ~21:00 to 22:00 UTC (60-min rhythm)
- [cycles/06/review.md](cycles/06/review.md) - Cycle 06 review (End Planner / Integrator), Wed Nov 4, 21:25 to 22:35 UTC
- [cycles/07/gate-locked-candidate.md](cycles/07/gate-locked-candidate.md) - gate-locked-candidate.md
- [cycles/07/gate-locked-live.md](cycles/07/gate-locked-live.md) - gate-locked-live.md
- [cycles/07/plan.md](cycles/07/plan.md) - Cycle 07 plan (Start Planner), Wed Nov 4, ~22:35 to 23:35 UTC (60-min rhythm)
- [cycles/07/review.md](cycles/07/review.md) - Cycle 07 review (End Planner / Integrator), Wed Nov 4, 22:35 to 23:55 UTC
- [cycles/08/plan.md](cycles/08/plan.md) - Cycle 08 plan (Start Planner), Wed Nov 4, ~23:15 to 00:15 UTC (60-min rhythm)
- [cycles/08/review.md](cycles/08/review.md) - Cycle 08 review (End Planner / Integrator), Thu Nov 5, 23:55 to 01:15 UTC
- [cycles/09/plan.md](cycles/09/plan.md) - Cycle 09 plan (Start Planner), Thu Nov 5, ~00:15 to 01:15 UTC (60-min rhythm, a 
- [cycles/10/plan.md](cycles/10/plan.md) - Cycle 10 plan (Start Planner), Thu Nov 5, ~01:05 to 02:05 UTC (60-min rhythm)
- [cycles/11/plan.md](cycles/11/plan.md) - Cycle 11 plan (Start Planner), Thu Nov 5, ~02:00 to 03:00 UTC (60-min rhythm)
- [cycles/12/plan.md](cycles/12/plan.md) - Cycle 12 plan (Start Planner; corrected 03:05 UTC), Thu Nov 5, ~03:00 to 04:00 U
- [cycles/13/plan.md](cycles/13/plan.md) - Cycle 13 plan (Start Planner), Thu Nov 5, ~04:00 to 05:00 UTC (60-min rhythm, ov
- [cycles/14/plan.md](cycles/14/plan.md) - Cycle 14 plan (Start Planner), Thu Nov 5, ~05:00 to 06:00 UTC (60-min rhythm, ov
- [cycles/14/review.md](cycles/14/review.md) - Cycles 09-15 review (integrator), Wed Nov 4 9:30 PM to Thu Nov 5 1:30 AM ET
- [cycles/16/plan.md](cycles/16/plan.md) - Cycle 16 plan (Start Planner), Thu Nov 5, ~07:00 to 08:00 UTC: NBA first, then N
- [cycles/17/plan.md](cycles/17/plan.md) - Cycle 17 plan (Start Planner), Thu Nov 5, ~08:00 to 09:00 UTC: sync #1, then NBA
- [cycles/18/plan.md](cycles/18/plan.md) - Cycle 18 plan (Start Planner), Thu Nov 5, ~09:00 to 10:00 UTC: sync, then NBA, t
- [cycles/19/plan.md](cycles/19/plan.md) - Cycle 19 plan (Start Planner), Thu Nov 5, ~10:00 to 11:00 UTC: sync, then NBA, t
- [cycles/24/plan.md](cycles/24/plan.md) - Cycle 24 plan (Start Planner), Thu Nov 5, ~14:15 UTC: the time-shift buffer beco

## Cleanup candidates (suggested, nothing done)

1. `status.md` vs `handoff.md` section 5 vs `integration.md`: three status boards, status.md is 4 days old. Action: fold status.md into handoff.md or regenerate it; add a banner to status.md.
2. `gate-report-rc1/rc2/rc3.md` plus `cycles/0[4-7]/gate-locked-*.md`: `gate-locked-live.md` is byte-identical in 3 of 4 cycles (04-07); `gate-locked-candidate.md` is identical in 2 of 3 (05-07). Action: keep one copy plus a pointer, or move all to `docs/archive/` (needs link fix in CI first).
3. `nights/2026-11-04..06.md`: tiny generated stubs (about 286 bytes each). Action: keep generated, or collapse into one table.
4. `noggin.md` (37 KB) vs `noggin-foundation-model.md` vs `onebrain-migration.md` vs `plans/2026-11-04-noggin-master-plan.md` (v2 untracked in main): overlapping Noggin design. Action: one canonical doc, others marked superseded.
5. `plans/2026-11-05-compressed-4day.md` vs `plans/2026-11-04-noggin-master-plan.md` vs `plans/2026-11-07-world-model-100m.md`: stacked plans. Action: add status line to each.
6. `data-sources.md` vs `datasources/sources-catalog.md` vs `datasources/scout-ledger.md` vs `datasources/data-ledger.md`: same sources, four views. Action: data-sources.md becomes a 10-line pointer.
7. `instant-switching.md` vs `memory/runbooks/tv-setup.md` vs `tonight-checklist.md`: TV setup in three places. Action: runbook links only to instant-switching.
8. `cloud-build-prompts.md` vs `cloud-training-prompt.md`: both one-off cloud prompts. Action: merge or archive.
9. `memory/decisions.md` (154 KB) and `experiments.md` (150 KB): too big to skim. Action: split by month, keep an index on top (check_memory.py and CI link to decisions.md, so keep the path).
10. `context/claude-memory/*` duplicates Nick's global Claude memory. Action: confirm still the source of truth, else delete.
11. `research/2026-11-03/03-switch-timing.md` vs `research/2026-11-08/live-data-feeds.md` and `stream-delay-sync.md`: same feed-lag topic, newer docs refine older numbers. Action: add "see also" lines.
12. Non-doc clutter in docs: `sync/yttv_tests/work/dry1/*.log` (50 logs of 37 bytes), `replay-results-20261101.json`. Action: git-ignore or move out of docs/.

