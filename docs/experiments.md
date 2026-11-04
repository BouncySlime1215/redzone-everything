# Experiment queue (one ranked list)

**Owner:** the Start Planner keeps this file and re-ranks it each cycle. Lanes update the `status`
column in their own PRs.

**Sources** (official direction, Nick, 6:10 PM Nov 4):
- [Noggin master plan](plans/2026-11-04-noggin-master-plan.md) ideas 1-62, as M1-M62.
- [Watch science playbook](research/watch-science-playbook.md) mechanics 1-22 (P1-P22) and experiments E1-E11.
- The data-scale directive's five data ideas, D1-D5. **Assumption:** I read the "5 data-agent ideas" as the
  5:00 PM data-scale list. If they meant another list, swap it in here.
- This week's directives, W1-W10.

Both source docs are on `codex/resume-redzone` at 51908ba.

**Binding:**
- The playbook's line (§5) is binding. The objective is that the night was **worth it**, not watch time.
- Nothing here adds betting features, artificial variable-ratio tricks, overstated hype, autoplay or pull-back
  nudges, streaks or time counters, or viewer sensors.
- The standing **self-taught rule** holds: Nick's taps, ratings and overrides are controls and
  **evaluation-only** measures, never a training label.
- Rule #1 holds. Every row must keep mid-play entries at 0 and high-leverage leaves at 0, and must not raise
  plays missed on entry or dead air without a measured reason.

**How it's ranked:**
1. Phase 0 first (now to Fri Nov 6).
2. Then work that unblocks promotion bar (B).
3. Then expected North Star gain per unit of cost, where the scarce resource is GPU-h. Kaggle is ~30 GPU-h a
   week, and ~2.5 are left this week, reserved for V8.

Costs are planner estimates. Lanes correct them in their PRs.

**Status:**
- `running`: a PR or job exists.
- `next`: assigned in the current cycle plan.
- `queued`: ranked, not yet assigned.
- `hold`: needs Nick or an outside fact.
- `line`: excluded by playbook §5 or a standing rule, with its allowed remainder noted.
- `standing`: a rule or process, not an experiment.
- `merged into EXP-x`: a duplicate.

**Pre-registration** (grading rule 2 in docs/grading.md, #191):
- A row needs a `success bar` and `nights needed` before it moves to `running`.
- Once it is `running`, its hypothesis, line, bar and nights are frozen. Change them by registering a new EXP id.
- `set before running` means the owning lane writes the bar in its PR before the first run.

**Lanes:** ① Noggin training, ② logic/sync, ③ performance + controls, ④ app + CI, ⑤ features, Int = Integrator.

## Phase 0: foundations (now to Fri Nov 6)

**Done when:** the suspense line shows on the scoreboard, the first event-token shards are on Kaggle,
shadow mode has run one night, and off-policy grading reads one logged night.

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | EXP-001 | M19, P1, P3, E1 | Suspense `S = E[(ΔWP)^2]`, computed at each dead ball (concave late multiplier, P3), ranks games better than clock-only heat. Gate 1: suspense-at-entry is logged beside entries_mid_play. Gate 2: as a WV input (flag off) it beats #166's WV on locked rank agreement (NBA 74.2, NFL 58.6). | Crunch live (NFL/NBA/NHL); Big plays seen live; % of oracle; new info line "suspense at entry" | 0 / ~8 (① S + WV input, ④ scorecard line, ⑤ why text) | ①④⑤ | Gate 1: the suspense-at-entry info line is computed on dev + locked with entries_mid_play unchanged (0 for the candidate). Gate 2: the WV with S beats #166's WV on locked rank agreement (NBA > 74.2, NFL > 58.6) with the night-bootstrap lower bound above #166's point, and WV calibration error ≤ 0.06 | dev all fit nights; locked read once; ≥ 8 nights per league for PASS | next |
| 2 | EXP-002 | E2, M17 (part) | Per-league WP models are calibrated enough to feed S, including OT and garbage time. Target ECE ≤ 0.05 per league on locked; a league that misses keeps S off. | Gate for EXP-001 | 0 / ~3 per league | ① | ECE ≤ 0.05 per league on locked (OT and garbage-time slices reported); a league over 0.05 keeps S off | locked ≥ 8 nights per league (PENDING below) | next |
| 3 | EXP-003 | M9, NFM vocab | One event language for every sport (possession, stoppage, score, clock and break primitives) as a typed contract in `redzone/contracts/`, matching #169's vocab v1 ids. Hypothesis: one vocab tokenizes NFL/NBA/NHL/CBB with < 1% unmapped events. | NFM main track; contract_violations | 0 / ~5 | ①④ | < 1% unmapped events per league (NFL, NBA, NHL, CBB) on the pinned archive; 0 new contract violations | all archive games per league | next |
| 4 | EXP-004 | M48, E1 (shadow half) | Nightly shadow mode: the candidate decider runs silently beside live on the Mac (≤ 150 MB extra RSS, no TV commands) and gets scored the next morning. Hypothesis: shadow-vs-live deltas on fresh nights agree in sign with the locked deltas. | Release gate (fresh nights); every line, as evidence | 0 / ~8 (③ Mac half, ④ scoring half) | ③④ | Shadow runs a full live night with ≤ 150 MB extra RSS, 0 TV commands, and a morning score file; the sign of shadow-vs-live deltas matches locked on ≥ 2 of 3 headline lines | 3 fresh live nights | next |
| 5 | EXP-005 | M7, M49, E9, P21 (propensity part) | Off-policy grading. The decider logs a propensity for every option, and a doubly robust estimator grades a new brain on logged nights. Hypothesis: the DR estimate agrees with the sim within its CI on the night-lab nights. Randomizing near-ties is **approved with limits** (Nick, 7:21 PM ET), but held until Friday's v3 locked set lands. The limits: behind `decider.explore.enabled` (off by default); it only picks between options that are BOTH already safe (dead ball, no mid-play) and within a small value margin; at most 5% of decisions; never in crunch, down the stretch, your-team moments or the catch-up queue; a propensity is logged on every decision; it goes through the normal promotion bar (worth it ≥ live). | % of oracle (off-policy); Right game agreement (evaluation) | 0 / ~8 | ②④ | Logging: propensities on 100% of decisions, summing to 1, with the sim JSON identical when the flag is on. Estimator: the DR estimate lies within the sim's night-bootstrap 95% CI on ≥ 80% of night-lab nights. Exploration (after v3): ≤ 5% of decisions, 0 in excluded states, mid-play entries 0 | logging: 1 replayed night; estimator: all night-lab nights; exploration: ≥ 8 live nights | running (#186 logging); next (estimator); approved, starts after v3 (randomization) |
| 6 | EXP-006 | W (V8) | V8 Mind (crunch-stay fix + NBA leak audit) lifts NBA crunch live on dev, with no leak flagged by the audit. | Crunch live NBA; head lift over a context-only baseline | ~2.5 / ~3 | ① | NBA crunch live on dev ≥ V7 + 1.0 (seed mean) with no seed worse; leak audit finds no single-feature AUC > .98; head lift over the context-only baseline > 0 | dev all NBA fit nights, 3 seeds | next (when ready) |
| 7 | EXP-007 | D1, NFM | Event-token shards (EXP-003 vocab) ship in the next factory pack, so pretraining run #1 can start next week. | NFM; data ledger tokens | 0 (Kaggle CPU) / ~3 | ① | Shards for NBA + NFL (≥ 4 M tokens) published in the factory pack with a manifest and hash; the ledger matches the manifest | n/a (data) | next |
| 8 | EXP-008 | M58, D3 | Every live night is gold data. RC6 (#132 + #173) captures switch rows with provenance and STK-05/06/07 counts from tonight. | Live nights section of northstar.md; every line | 0 / ~2 | ③ Int | RC6 night logs switch rows with provenance on 100% of switches and STK-05/06/07 counts; the morning night report reads them | 1 live night (then every night) | running (RC6) |
| 9 | EXP-009 | D5 | A data ledger in every plan (games, events, tokens, crunch/OT/comeback/blowout shares per league-season). | NFM; data scale | 0 / ~0.5 per cycle | ① + planner | Every cycle plan has a ledger table with games, events, tokens and slice shares per league-season | n/a (process) | standing |

## Promotion blockers and this week's directives

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 10 | EXP-010 | W (bar B, c) | A your-team catch-up leaves at the first dead window even when nothing else is worth a switch (#180). Lifts locked your-team big ≤3 min from 73.5 to at least live's 82.4, with crunch-in-replay not worse. | Your-team big plays seen | 0 / ~3 | ② | Locked your-team big ≤3 min ≥ live's 82.4 (point) with the CI lower bound ≥ live's lower bound 75.0; crunch-in-replay not worse; mid-play entries 0 | dev 3 seeds (21/21 target), then locked once | running (#180) |
| 11 | EXP-011 | W (crunch 4:15 PM), M28 (part) | Crunch is a learned state with entry, exit and hysteresis (#166 crunch_state). Crunch vs crunch is decided by value, with floors only (#160, #174). | crunch_exit_acc ≥ 95% vs oracle; Crunch live | 0 / ~4 | ①② | crunch_exit_acc ≥ 95% vs the oracle on locked; crunch live not below live on any league; high-leverage leaves 0 | locked ≥ 8 nights per league | running (#166, #160, #174) |
| 12 | EXP-012 | W (YTTV seek) | The measured YTTV seek (15 s a press, first press swallowed) gives 0 mid-play entries on catch-ups. | Mid-play entries | 0 / ~1 | ② Int | 0 mid-play entries on catch-ups with the measured seek on dev and locked; plays_missed_entry not worse | dev all fit nights, 3 seeds; locked once | running (#132, in RC6) |
| 13 | EXP-013 | W (cut rules 2-3) | Leave mid-play only at low leverage, and stay sticky down the stretch via the learned leverage score (#154). | High-leverage leaves = 0; Crunch live | 0 / ~2 | ② | High-leverage leaves 0; crunch live NFL and NBA ≥ live (seed mean); low-leverage leaves reported | dev all fit nights, 3 seeds | running (#154) |
| 14 | EXP-014 | W (game importance), P2 (part) | Game importance as a WEIGHT (playoff race, matchup, scarcity, postseason), never a lock (#172 + an importance head). | importance_rank_agreement; % of oracle | ~1 / ~4 | ①② | importance_rank_agreement on locked slates > the no-importance baseline, with % of oracle not worse | locked ≥ 8 slates | running (#172) |
| 15 | EXP-015 | W (HEAT 2.0) | The learned WV drives the decider behind `scoring.watch_value`, with the old scorers migrated at parity. | % of oracle; Big plays seen live | 0 / ~4 | ② | set before running | set before running | queued (after #166) |
| 16 | EXP-016 | W (city map) | One brain: burn contract_violations down from 477 to 0, lane by lane, with no behavior change. | contract_violations | 0 / ~10 | ④ + all | contract_violations falls each cycle and reaches 0 with every scorecard line identical (parity) | n/a (parity on dev) | running (#179, #181) |
| 17 | EXP-017 | W (bar B evidence) | The v3 locked set (#149) brings NBA and NHL to 8 locked nights each, so bars (b) and (c) can read PASS. | Release gate (locked) | 0 / ~2 | ④ | set before running | set before running | next (Fri) |
| 18 | EXP-018 | W (TREE-6), M31 | Scenario-tree planning 5-10 min ahead: jump / stay / interleave / catch-up-on-return looked up by WV. Marked test: Bills / Bears. | % of oracle; Crunch live | ~2 / ~8 | ①② | (a) The marked Bills/Bears test passes (jump to the 1-minute drive, catch the left game up from the buffer on return). (b) All dev (`fit`) nights, 3 seeds, honest knobs (`--real-lag --tune-jitter 6 --break-anchors`), candidate vs candidate + TREE-6, frozen grader: mid-play entries 0 and high-leverage leaves 0 on every seed; crunch live NFL, NBA and NHL each ≥ baseline in the seed mean, and no seed worse by more than 1.0; your-team big ≤3 min and crunch-in-replay not worse. (c) Only then, locked read once. Failing (a) or (b) ships it off behind its flag. Pre-registered by lane 2, Nov 4 23:20 UTC, before any run. | dev all fit nights × 3 seeds; then locked once | failed (value = score); retry with WV registered as a new row when #166 lands |

## Phase 1: timing brain (to ~Wed Nov 11)

**Done when:** mid-play entries are 0 with a stated guarantee, and live-or-replayed within 3 min beats live
on locked.

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 19 | EXP-019 | M53 | Wall-clock gaps between plays become free break labels (a long gap means a TV break), and they raise break-model recall at no data cost. | Landings in dead time; Dead air | 0 / ~3 | ① | set before running | set before running | next |
| 20 | EXP-020 | M54 | A rulebook "break owed" countdown (NBA, CBB, NHL TV timeouts; verify the exceptions in each rulebook) predicts the next break window. | Landings in dead time; Dead air | 0 / ~3 | ② | set before running | set before running | queued |
| 21 | EXP-021 | M56 | MediaSession bursts on the sticks become automatic dead-ball labels from our own nights (#146). | Per-stick delay error p95; Landings in dead time | 0 / ~3 | ③ | set before running | set before running | queued |
| 22 | EXP-022 | M2, M12 (part) | A neural point-process timing model (transformer TPP) for breaks, scores and crunch entry/exit beats the trees on break-start log-loss. | Entries at a possession start; Mid-play entries | ~3 / ~6 | ① | set before running | set before running | queued |
| 23 | EXP-023 | M29, M44 | A belief tracker (a particle filter per game for feed and TV lag, with online Bayesian updates of each broadcast's break rhythm) cuts landing error. | Per-stick delay error p95; Mid-play entries | 0 / ~6 | ② | set before running | set before running | queued |
| 24 | EXP-024 | M6 | Conformal risk control on every entry gives a stated bound on mid-play joins, with an abstain (stay) option. | Mid-play entries = 0 with a guarantee | 0 / ~5 | ② | set before running | set before running | queued |
| 25 | EXP-025 | P7 | Arrive at the dead ball BEFORE the big play: predict the next high-leverage snap and switch at the stoppage before it. Measure: seconds of build-up seen per big play. | Big plays seen live; Landings in dead time | 0 / ~4 | ② | set before running | set before running | queued |
| 26 | EXP-026 | M1, M20, M32, P9, P17, E5 | Catch-up ordering as a scheduling problem with spoiler control and peak-end ordering. Open threads are resolved at their next dead ball, and at most 1-2 are visible. | Big plays live or replayed within 3 min | 0 / ~6 | ②⑤ | set before running | set before running | queued |
| 27 | EXP-027 | P11, P5, M21 | Surprise drives replays and never cut-ins. KL surprise over the full outcome distribution handles OT and shootouts; a rarity bonus comes later from the world model. | Big plays live or replayed within 3 min; Mid-play entries unchanged | 0 / ~3 | ①② | set before running | set before running | queued |
| 28 | EXP-028 | P8, M25, E8 | Minimum dwell time plus a hysteresis margin, swept offline. Pick the knee where ping-pong falls without losing crunch; whiplash is a learned cost. | Crunch live; guardrails: switches per hour, ping-pong | 0 / ~3 | ② | set before running | set before running | queued |
| 29 | EXP-029 | P10 | Fill known breaks (commercials, halftime, intermissions) with the best other live game. | Dead air | 0 / ~3 | ② | Hypothesis (from a dev-only diagnosis, NBA/NHL fit seed 0, grader untouched): 43% of NBA dead air and 25% of NHL dead air are seconds when another game is live and not on a break, and ~39% / ~15% of it sits in runs of 2+ minutes; the decider stays on the ad because every live alternative scores under decider.break_destination_min (25), mostly 6-15. Arms, both fixed a priori: (A) `decider.break_destination_min` 0 (any live game beats an ad); (B) flag `decider.break_fill.enabled` (off): bar 0 only while home's typical break length is >= 120 s (halftime, intermission, end of period, NBA TV timeouts), 25 otherwise. Bar, all dev (`fit`) nights, NBA and NHL, seeds 0/1/2, honest knobs (`--real-lag --tune-jitter 6 --break-anchors`, candidate profile), frozen grader, per arm vs candidate: (a) mid-play entries 0 and high-leverage leaves 0 on every seed; (b) dead_air_pct down >= 3.0 points in the seed mean in NBA, and not up in NHL; (c) crunch live not down by more than 1.0 on any seed in either league; (d) plays missed on entry, crunch-in-replay and your-team big <= 3 min not worse in the seed mean. If both arms pass, keep the one with the larger NBA dead-air drop (dev only). Only then, locked read once for that arm. Fail: ships off, result logged. Pre-registered by lane 2, Nov 5 ~00:05 UTC, before any arm was run. | dev all fit nights NBA + NHL x 3 seeds x 2 arms; then locked once | pre-registered |
| 30 | EXP-030 | P2, P6 | Care × suspense. WSH/UNC and league order are **strong priors with floors** (Nick, 4:15 PM, supersedes the playbook's "hard rule" wording). Followed players are a config multiplier. | Your-team crunch live; Your-team big plays | 0 / ~2 | ① | set before running | set before running | queued |
| 31 | EXP-031 | P4 | An "upset in progress" term: surprise weighted by how unlikely the current state was before the game. The pre-game line is a hidden prior only and is never shown. | % of oracle | 0 / ~2 | ① | set before running | set before running | queued |
| 32 | EXP-032 | M50 | Red-team agents hunt for nights that break the brain, extending the night lab. | Faults recovered; Mid-play entries | 0 / ~4 | ④ | set before running | set before running | queued |
| 33 | EXP-033 | M51 | Drift detection: notice when a league's broadcast patterns change and recalibrate automatically (#140 self-calibration). | Per-stick delay error p95; Landings in dead time | 0 / ~3 | ③ | set before running | set before running | queued |
| 34 | EXP-034 | M34 | Anytime planning: a decision is always ready within the Reflex budget and gets better as time allows. | Switch time p95; % of oracle | 0 / ~3 | ② | set before running | set before running | queued |

## Phase 1: what the viewer sees and controls (lane ⑤, display only, flags off)

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 35 | EXP-035 | P14 | A "why we're here" tag tied to a logged threshold (watch_why + suspense) keeps labels honest. Measure: the "alerted but nothing happened" rate. | Right game agreement (evaluation) | 0 / ~2 | ⑤ | set before running | set before running | next |
| 36 | EXP-036 | P20, P19, P17 (finale) | Commitment controls ("stay here", "only my teams", "stop after this game", "end at 11:30", each waiting for a dead ball), a wind-down after the last high-leverage window, and a "Slate complete" state. | Phone press to TV p95; guardrails: late-night hours, sessions after slate complete | 0 / ~5 | ⑤③ | set before running | set before running | next |
| 37 | EXP-037 | P13 | A one-question context line ("Down 3, 1:12 left, no timeouts") frames only questions the game will answer soon. | Right game agreement (evaluation) | 0 / ~2 | ⑤ | set before running | set before running | next |
| 38 | EXP-038 | P12, E4 | A 2-3 s orientation beat on every switch (teams, score, clock, situation) as a corner overlay, never covering play. Measure: overrides within 30 s. | Right game agreement (evaluation) | 0 / ~2 | ⑤ | set before running | set before running | queued |
| 39 | EXP-039 | P15 | A quiet "also close" strip resolves curiosity without forcing a switch. | Big plays live or replayed within 3 min | 0 / ~2 | ⑤ | set before running | set before running | queued |
| 40 | EXP-040 | §6 metrics | A one-tap end-of-night "worth it?" rating (1-5) on the slate-complete screen, plus once-a-week agency / regret probes. **Evaluation only**; never a training label. | New evaluation line; warning rule: watch time up while rating down blocks a change | 0 / ~2 | ⑤④ | set before running | set before running | queued |
| 41 | EXP-041 | P21, E3 | 👍/👎 per switch. Allowed: evaluation only (the existing Right game agreement line). Excluded: fitting weights on it (E3), by the self-taught rule. | Right game agreement (evaluation) | 0 / ~1 | ⑤ | set before running | set before running | line (training use); queued (evaluation) |
| 42 | EXP-042 | P16 | Variety only as a tiebreaker; no fixed switch sting. | Right game agreement; Mid-play entries unchanged | 0 / ~1 | ② | set before running | set before running | queued |
| 43 | EXP-043 | P22 | Followed players as a config multiplier; group mode is evaluated separately from the solo gates. | Right game agreement (low priority) | 0 / ~2 | ①⑤ | set before running | set before running | queued |
| 44 | EXP-044 | P18, M61, E7 | Deliberate valleys and tension/release pacing vs peak-only cutting, alternated by whole night. A test, not a default. | End-of-night rating (evaluation); guardrails | 0 / ~3 | ② | set before running | set before running | hold (needs EXP-040 data) |
| 45 | EXP-045 | E6 | Does peak suspense plus end suspense predict the end-of-night rating as well as mean suspense does? | Evaluation | 0 / ~1 | ④ | set before running | set before running | hold (after ~20 rated nights) |
| 46 | EXP-046 | E10 | Does the wind-down mode cost anything on your-team lines? | Your-team crunch live; late-night hours (guardrail) | 0 / ~1 | ④ | set before running | set before running | hold (after EXP-036) |

## Phase 2: world model v1 (to ~Wed Nov 25)

**Done when:** the planner beats the hand-built decider on % of oracle on locked in at least 2 leagues.

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 47 | EXP-047 | W (NFM), M10, M12 | NFM pretraining run #1: 50-100M params on event tokens. Compute-optimal for ~1B tokens is ~50M, so add tokens before size. Beats the trees on next-event loss and calibration on locked. | NFM; % of oracle (via heads) | ~8 / ~6 | ① | set before running | set before running | queued (next week's quota) |
| 48 | EXP-048 | D1 | Free sources wave 1: nflverse, NBA pbp archive, NHL api-web + shifts, CBB ESPN, and Retrosheet (check its notice terms first). CFBD is waiting on Nick's key; Statcast is skipped (Nick, 6:10 PM). | Data ledger tokens (target ≥ 0.7B real) | 0 / ~8 | ① | Ledger rows for NFL (nflverse), NBA (back seasons), NHL (api-web + shifts) and CBB filled, with ≥ 0.7 B real tokens total and the per-slice shares reported; every source's terms are noted in docs/data-sources.md | n/a (data) | running |
| 49 | EXP-049 | D2 | Self-play ×5-10 synthetic nights from the factory (#122, #152), clearly labelled synthetic in the ledger. | Data ledger; % of oracle on real locked | 0 (Kaggle CPU) / ~3 | ① | Synthetic nights labelled synthetic in the ledger; % of oracle on REAL locked is not worse when they're added to training | locked once per release | running |
| 50 | EXP-050 | D4 | Stratified coverage lifts crunch (3.6% today), OT, comeback and blowout shares to balanced quotas. | Crunch live; data ledger shares | 0 / ~2 | ① | set before running | set before running | queued |
| 51 | EXP-051 | M11 | Mixture of experts: a shared trunk plus league experts and a crunch expert (NFM v2). | Crunch live | ~6 / ~4 | ① | set before running | set before running | queued |
| 52 | EXP-052 | M14 | Retrieval of the 50 most similar historical moments, used as features (CPU embeddings). | % of oracle; calibration | ~1 / ~4 | ① | set before running | set before running | queued |
| 53 | EXP-053 | M15 | Cross-sport transfer, measured: does football pretraining help hockey crunch? An ablation, not an assumption. | Crunch live NHL | ~2 / ~2 | ① | set before running | set before running | queued |
| 54 | EXP-054 | M16 | "Game DNA" embeddings (pace, volatility, comeback-proneness) improve WV calibration early in games. | WV calibration error | ~1 / ~3 | ① | set before running | set before running | queued |
| 55 | EXP-055 | M13 | Graph networks over lineups (NBA first): who's on the floor changes clutch odds. | Crunch live NBA | ~2 / ~5 | ① | set before running | set before running | queued |
| 56 | EXP-056 | M27 (v1), M30 | Planner v1: search over the world model, with value of information (peek with the hidden stick before committing; the flip cost is counted). | % of oracle; Crunch live | ~2 / ~8 | ②① | set before running | set before running | queued |
| 57 | EXP-057 | M37 | Offline RL on real logged nights (conservative), rewarded by the oracle, not by Nick. | % of oracle | ~3 / ~6 | ① | set before running | set before running | queued (needs ≥ 5 gold nights) |
| 58 | EXP-058 | M26 | Train directly on regret vs the hindsight oracle. | % of oracle | ~2 / ~3 | ① | set before running | set before running | queued |
| 59 | EXP-059 | M5, M36, M42, M45 | Adversary-designed hard nights, an auto-curriculum (one game, then two, then a full slate, then hard nights) and active learning on the nights the model is least sure about. | Worst-fold % of oracle | ~3 / ~6 | ①④ | set before running | set before running | queued |
| 60 | EXP-060 | M41 | A realism filter keeps only synthetic nights a judge model can't tell from real ones. | Data ledger (kept share); % of oracle on real | ~1 / ~3 | ① | set before running | set before running | queued |
| 61 | EXP-061 | M57 | A broadcast simulator (ad pods, delays, glitches) calibrated to our live logs. | Dead air; Per-stick delay error p95 | 0 / ~6 | ③④ | set before running | set before running | queued (needs gold nights) |
| 62 | EXP-062 | M24 | A multi-objective dial (value vs dead air vs whiplash) reported as a Pareto front, with the chosen point logged in decisions.md. | % of oracle; Dead air; guardrails | 0 / ~3 | ② | set before running | set before running | queued |
| 63 | EXP-063 | M59, M60 (reframed) | A suspense forecast for the next 10 min (from WP paths) and a dead-stretch forecast, used only to pre-tune cuts and fill breaks. It never predicts or targets viewer drift (§5 "vulnerable states"). | Dead air; Big plays seen live | ~1 / ~4 | ① | set before running | set before running | queued |
| 64 | EXP-064 | M22, E11 | Hindsight attention labels from the Wikimedia pageviews API (free and documented; hourly only). Evaluation of suspense peaks only, never a training target. | Evaluation (rank correlation) | 0 / ~2 | ① | set before running | set before running | queued (check terms + granularity) |

## Phase 3-4: better planner, self-improving lab (mid-December onward)

| rank | id | covers | hypothesis | North Star line | cost GPU-h / agent-h | lane | success bar | nights needed | status |
|---:|---|---|---|---|---|---|---|---|---|
| 65 | EXP-065 | M38, M46 | Expert iteration (the planner teaches the Reflex) plus league adapters merged with quantization-aware training, so it runs < 5 ms and ≤ 2 GB on the Mac. | Switch time p95; % of oracle | ~4 / ~6 | ①③ | set before running | set before running | queued |
| 66 | EXP-066 | M27 (v2), M35 | A stochastic MuZero-style planner plus a population league of rival planners. | % of oracle ≥ 95 per league | ~10 / ~10 | ①② | set before running | set before running | queued |
| 67 | EXP-067 | M28 | Hierarchical options: "follow this game until X" (macro) and "cut on this dead ball" (micro). | Crunch live; % of oracle | ~2 / ~5 | ② | set before running | set before running | queued |
| 68 | EXP-068 | M4 | Test-time compute: answer instantly in quiet moments and search deeper when crunches collide. | Crunch live | 0 / ~3 | ② | set before running | set before running | queued |
| 69 | EXP-069 | M33 | A risk-sensitive (CVaR) policy optimizes the worst nights, so must-sees are never missed. | Worst-fold % of oracle; Your-team lines | ~2 / ~4 | ①② | set before running | set before running | queued |
| 70 | EXP-070 | M47 | Formal verification of the floor rules: prove the policy can't join mid-play under its own model. | Mid-play entries (guarantee) | 0 / ~8 | ④ | set before running | set before running | queued |
| 71 | EXP-071 | M52 | Interpretability probes: what did the world model learn (clock, score, momentum)? | Diagnostic | ~1 / ~3 | ① | set before running | set before running | queued |
| 72 | EXP-072 | M17 (full) | Calibrated uncertainty from deep ensembles or evidential heads, so the model knows what it doesn't know (abstain input for EXP-024). | Calibration error; Mid-play entries | ~3 / ~3 | ① | set before running | set before running | queued |
| 73 | EXP-073 | M39, M43 | Continual learning with replay, plus in-game adaptation to tonight's rhythm (this ref crew, this broadcast's ad pattern). | Landings in dead time; Dead air | ~2 / ~6 | ① | set before running | set before running | queued |
| 74 | EXP-074 | M8 | An AI-scientist lab: agents propose, train, grade and write up, inside the release gate and the $0 budget. | Throughput of this queue | 0 / ~10 | ④ | set before running | set before running | queued |
| 75 | EXP-075 | M3 | A diffusion "dream" world model samples many plausible next-5-minute futures per game for the planner. | % of oracle | ~15 / ~10 | ① | set before running | set before running | queued (GPU-bound) |
| 76 | EXP-076 | M18 | A causal world model answers what-ifs ("timeout here?"). | Diagnostic | ~4 / ~6 | ① | set before running | set before running | queued |

## Hold and line (not scheduled)

| id | covers | why | allowed remainder | success bar | nights needed | status |
|---|---|---|---|---|---|---|
| EXP-077 | M55 | Betting-line movement as a live news sensor is wager-driven switching, which §5.1 excludes. | Pre-game lines as a hidden data prior only (EXP-031). | n/a | n/a | line |
| EXP-078 | M19 (market part) | Same: live market odds are not a ranking input. | WP from our own models; pre-game line as prior. | n/a | n/a | line |
| EXP-079 | M23 | Inverse RL from producers' choices needs licensed video or producer logs, and there is no $0, terms-compliant source. | Revisit if a licensed source appears. | n/a | n/a | hold |
| EXP-080 | M40 + "Company (later)" | Federated personalization, Drama API, shared cloud brain, licensed audio/video: company stage, which needs money and Nick's OK. | None now. | n/a | n/a | hold |
| EXP-081 | M62 | The guardrail is now the binding line (§5) and the metrics (§6). | Applied to every row above. | n/a | n/a | standing |
| EXP-082 | Mind 3B | Explains choices and handles odd cases; it is not the core timing. | Stays shadow-first and flag-off (≤ 2 GB). | n/a | n/a | standing |

## Results log

- **EXP-018** (Nov 4, 23:40 UTC, value = score, because WV isn't wired yet). The bar was pre-registered at
  4b7aecd. (a) PASS. (b) FAIL:
  - NFL crunch live 72.1/70.8/71.8 → 71.1/68.7/70.8 (seed 1 −2.1);
  - NHL crunch-in-replay 0/8/0 → 0/11/5;
  - NHL your-team ≤3 min 16/16/15 → 15/16/15;
  - NBA identical; mid-play entries and high-leverage leaves 0.

  Locked was not read. It ships OFF (#189). The retry uses lane 1's watch value as the value.

## Coverage check

Every source idea maps to a row:
- **M1-M62:** all covered, in EXP-001 to EXP-082.
- **P1-P22:** all covered. P6 (league order) is EXP-030, under the 4:15 PM priors-with-floors rule, and the
  promotion bar (B) cut rule 4 keeps it graded.
- **E1-E11:** E1 → 001/004, E2 → 002, E3 → 041 (line), E4 → 038, E5 → 026, E6 → 045, E7 → 044, E8 → 028,
  E9 → 005, E10 → 046, E11 → 064.
- **D1-D5:** D1 → 048/007, D2 → 049, D3 → 008, D4 → 050, D5 → 009.
- **W1-W10:**
  - promotion bar (B) → 010/017;
  - cut rules → 013;
  - crunch as a learned state → 011;
  - HEAT 2.0 → 015;
  - game importance → 014;
  - NFM → 003/007/047;
  - data scale → 048-050;
  - city map → 016;
  - TREE-6 → 018;
  - RC6 / YTTV → 008/012.
