# 02 · Capabilities and tasks: what RedZone's models should DO and KNOW

Research note, 2026-11-03. Read-only review of the repo (`codex/resume-redzone` plus the
`origin/claude/distill-teacher-student` and `origin/agent-training` branches, where `agent/distill`,
`agent/kaggle`, `redzone/reasoner`, `redzone/scoring/brain3.py` and `agent/llm_server.py` live).
Nothing in the repo was changed, run, or touched on a device.

**Verdict in one line:** today's two tasks (game-reasoner JSON, watchdog action) are the right
*shape* but cover maybe a fifth of what the north star needs. The biggest measured gaps (NBA entries
mid-play 38/97, NBA/NHL dead air 19–24 %, NFL big plays live 30 %, crunch live NFL 70 %) are
**timing** problems, and timing is a calibrated-GBM-plus-rules job, not an LLM job. The 1.7B
student should own *words, novel-error classification, journal summaries and one-shot
classification through `/score`*, and nothing with a deadline inside the 3 s tick.

Label-source legend used below: **R** rules (code), **O** outcomes from play-by-play with
hindsight (archive wallclocks), **T** teacher (Qwen3-32B-AWQ grounded pass on Kaggle), **N** Nick's
own feedback (phone taps), **C** Claude (gold rules, hard-case review, auto-repair).
Owner legend: **RULES**, **TINY** (logistic / isotonic / conformal, numpy), **GBM** (LightGBM →
numpy arrays, `redzone/scoring/brain.py` style), **LLM** (Qwen3-1.7B 4-bit + LoRA, `score` or
`generate` mode), **TEACHER** (Qwen3-32B/14B offline), **CLAUDE**.

---

## 1. Top 10 capability upgrades, ranked

Ranked by: closes a measured gap on the status board → uses data we already have ($0) → stays off
the Mac's critical path. Effort is wall-clock for one thread; "gate" is the locked-night number
that lets it ship ON.

| # | Upgrade | Closes | Owner | Live? | Effort | Gate (locked nights, per slice) |
|---|---|---|---|---|---|---|
| 1 | **Restart countdown**: one calibrated dead-window model (start odds, type, `len10/50/90`, `entry10/50/90`, cautious `safe_entry`) per league × stoppage kind, read by *every* cut, entry, visit, reel and crunch decision | NBA entries mid-play 38/97; NBA/NHL dead air 19–24 %; `jump.py` p10 stand-in; `LegacyBreaks` q10/q90 = 0.6×/1.6× median | GBM + RULES | yes, < 5 ms | 3–4 days (Brain v3 heads exist; wire + gate) | entries at dead ball ≥ 95 % NBA/NHL; pinball loss and `P(real < safe_entry) ≤ 5 %` per kind |
| 2 | **Safe-to-cut-now / safe-to-enter-now probability with abstain**: `P(no play on screen in [t0,t1])` from the dead windows + the sync sigma; below the conformal cut → "I don't know" → rules (unsure = unsafe) | mid-play cuts 0 (hard), NBA entries, "0 mid-play cuts or entries" | TINY + RULES | yes, < 2 ms | 2–3 days | 0 mid-play cuts; coverage ≥ 80 % at ≤ 1 % error on valid; mid-play entries ≤ 5 % and never above today |
| 3 | **Lock-in v2 with calibrated uncertainty and a runtime "I don't know"**: head + student emit `p_big`, `p_crunch_next`, `conf`; the decider escalates to rules below the valid-night cut (today the cut exists only in `grade.py`) | NFL key AUC 0.68, NHL 0.58 (brain metrics); reasoner gate pending | GBM (head) + LLM (`/score`) | head yes (< 1 ms); LLM async | 3 days (+ Kaggle round) | lock_acc ≥ 95 % vs gold, unsafe leaves ≤ 0.5 %, usable JSON 100 %, coverage reported |
| 4 | **Next big play: what + when**: per-league `P(kind within 60/180 s)` for TD/FG/turnover/goal (PP goal)/HR/clutch shot with ETA quantiles; feeds chase, `_hold_for_big`, standby MOMENT, reel timing | NFL big plays live 30 %, NFL drives live 13–20 %, NHL goals live 42 % | GBM | yes, < 5 ms | 3 days | lift ≥ 2× over base rate per league; `eta_mae_s` ≤ 45 s where named |
| 5 | **Stakes layer**: standings, playoff/elimination math, rivalry, rankings, national TV, odds → one `stakes` multiplier with provenance; **bet and fantasy awareness** upgraded to "bet-decisive moment next" | `_stakes` reads only win %, spread, national TV; bets are a static sweat | RULES + TINY (offline nightly) | lookup, < 1 ms | 2 days | agreement with Nick's "this mattered" taps ≥ 90 %; no change in mid-play lines |
| 6 | **Per-game journal (narrative memory)**: facts by rules (runs, lead changes, comebacks, injuries, milestones, your-team/bet thread), 1–2 lines by the LLM every 60–120 s per hot game; feeds the brief, the phone and "why I switched" | brief has no history beyond 8 plays; `storyline` is one-shot | RULES + LLM (`generate`, async) | async, ≤ 3 s per call, ≤ 4 calls/min | 2–3 days | teacher-judged factuality ≥ 98 % (no invented facts); never in the tick |
| 7 | **Catch-up reel: clip value + ordering as a ranker**: value from PBP (WP delta, score, crunch, your team, bet) not just headline keywords; chronological within a game, best last; Nick's skip/keep taps as labels | reel gate pending; `headline_points` is a keyword table | GBM/TINY + RULES | build offline; rank < 20 ms | 2 days | reels ≥ 95 % in dead time, 0 big missed on reel, Nick keep-rate ≥ 80 % |
| 8 | **Jump-or-stay as expected value** on top of #1 and #7, abstain → stay; the LLM only words it | highlight jump #18 paused on break quantiles | RULES (EV) + LLM words | yes, < 5 ms | 1–2 days after #1/#7 | `reels_in_dead_time_pct` 100, `big_missed_on_reel` 0, `jump_recall_pct` ≥ 80 |
| 9 | **Multi-game scheduling for the hidden stick**: `P(next switch target within 180 s)` from current-game break odds, crunch-soon elsewhere, replays queued, reel ready, path costs (Prime 8–27 s vs flip 1–4 s) | preloaded switches < 3 s on ≥ 95 % (unmeasured); `standby.py` is a class ladder | GBM/RULES | yes, < 20 ms | 2 days | preload hit rate ≥ 70 %; switch < 3 s on ≥ 95 % once the TV path is measured |
| 10 | **Explanations + self-diagnosis**: "why I switched" from `Action.ev/ev_stay/terms/guard` worded by the LLM; nightly "what went wrong" report; watchdog fault taxonomy (13 kinds in `KIND_HELP`) with a wider guarded action set (resync, re-tune hidden stick, dismiss prompt, reload app) | watchdog gate pending; `--no-model` in launchd; only 5 actions | RULES + LLM | watchdog 60 s loop; LLM ≤ 2 s | 3 days | watchdog ≥ 95 % correct, every kind ≥ 90 %, 0 unsafe; explanation factuality ≥ 98 % |

Two more worth a line, below the cut: **(11) a versioned per-league rulebook/TV-timeout handbook**
as code tables + a doc the teacher reads (today it is spread across `breaks.py`, `cuts.py`,
`garbage.py`, `engine.py` with 2025–26 numbers hard-coded); **(12) RedZone-as-teacher** (status
queue #8: imitation labels from Nick's own YTTV, legal and screenshot-gated).

---

## 2. What exists today (baseline the map builds on)

| Area | Today | Where |
|---|---|---|
| Student | Qwen3-1.7B-4bit, LoRA r8/α16 last 8 layers, two adapters (game-reasoner, watchdog); greedy; one base loaded once, adapters swapped; `/generate` and `/score` (one prefill, logprobs over choices) | `agent/llm_server.py` (agent-training branch), `agent/kaggle/run_students.py` |
| Reasoner output | 7 keys in order: `lock_in`, `safe_to_leave`, `confidence`, `storyline`, `why`, `next`, `stay_s`; plus jump task `decision/reel_s/why` | `redzone/reasoner/reasoner.py`, `agent/distill/teacher.py` |
| Brief | ~150–250 tokens, one fact per line: score/period/clock/stoppage, situation, timeouts, WP + swing, flags, brain line (dropped in round 2: leak), head odds, "you:" line, last 8 plays | `redzone/reasoner/brief.py` |
| Head | LightGBM lock odds, cuts watch 0.35 / must_see 0.60, horizons NFL 180 / NBA 120 / NHL 180 / MLB 300 s | `redzone/reasoner/head.py`, `redzone/lab/reasoner_data.py` |
| Labels | `y = (big_next or crunch_next) and not garbage`, `t_big`, `t_break`; gold rules: your team late → must_see, NFL red zone / 2-min → must_see, NHL PP → watch, garbage → none, `safe_to_leave=no` if must_see or hindsight risk | `agent/distill/gold.py` |
| Grading | must_see precision/recall/lift, lock_acc, unsafe_leave %, over-cautious %, eta MAE, usable JSON; gate 100 / 95 / 0.5; abstain cut picked on valid | `agent/distill/evaluate.py`, `agent/kaggle/grade.py` |
| Brain v2 (live, flag off) | break_soon_p (60 s), key_soon_p (2–5 min), hotness; AUC break NFL .86 NBA .83 NHL .69 MLB .88; key NBA .97 **NFL .68 NHL .58** MLB .80; live blend ECE 0.12–0.17 (blend hurts calibration) | `redzone/scoring/brain.py`, `brain_models/metrics.json` |
| Brain v3 (branch) | heads `stop15/30/60`, `type` (9 kinds), `len10/50/90`, `entry10/50/90`, `big60/180`, `hot`, isotonic; `entry_cal_v3.json` cautious scale (α = 5 %) for tv_timeout/score_break | `redzone/scoring/brain3.py`, `redzone/lab/brain3{data,fit,cal}.py` |
| Break rules | TV-timeout structure per league + measured medians (`LEN`), `BreakBook` owed marks | `redzone/scoring/breaks.py` |
| Cut timing | `DeadTracker` windows per kind, `FLOOR_S` (p2) / `MEDIAN_S`, `plan_entry` with DVR rewind, margins 2σ + 2 s, relaxed 8 s, urgent 2 s | `redzone/decider/cuts.py` |
| Decider | states, crunch arbitration (personal → closeness → score), break visits, replays, chase, reel fit, `_why` strings | `redzone/decider/decider.py` |
| Reel | ESPN clips (lag NBA 4.5 / NHL 5.5 / NFL ~14 min), keyword points, 5+ clips 30–60 s, per-game 2, live-angle trim by score-bug overlay | `redzone/reel.py` |
| Jump | hindsight labels (never mid-play, crunch first, NFL drives first, your team, value ≥ 1.0, ≥ 3 clips), hard set | `agent/distill/jump.py` |
| Standby | hidden-stick classes NEXT/HOME/REPLAY/BREAK/MOMENT/BEST, hold 20 s, min gain 8 | `redzone/standby.py` |
| Sync | taps, quarter-hour title flips (boundary), Prime score-bug OCR (YTTV is DRM-black), FastCast push ~0.1 s | `redzone/decider/screen.py`, `redzone/screen/*`, `redzone/sources/fastcast.py` |
| Watchdog | `Obs` (api, log age, live games, crunch on screen, sticks offline, away, route, disk, restarts, error sigs); 5 actions; `guard`; 13 error kinds in `KIND_HELP`; launchd runs `--no-model` | `agent/policy.py`, `agent/watchdog.py` |
| Teacher | 32B hardest-first (~65 req/min round 1) then 14B tail; tasks `reasoner_blind/grounded`, `jump_blind`, `wd_paraphrase/classify`; round 2 = millions of moments, 40 k-row weighted pick, slices + gap + don't-know | `agent/kaggle/*`, `agent/distill/teacher.py` |
| Contracts | `ReasonerView(lock_in, lock_reason, value_bonus ≤ 20, storyline, expectations[(what, within_s, p)], ttl 60)`, `BreakForecast(q10/q50/q90, next_restart_at)`, `Action(ev, ev_stay, terms, reason)`, tick budget models 150 ms / reasoner 5 ms cached / decision 100 ms | `docs/architecture.md` (staged copy) |

---

## 3. Capability map

Each entry: **In** → **Out** (schema in §6) · **Labels** · **Metric** · **Owner** · **Live?** (budget).
"Live" means read inside the 3 s tick; "async" means computed off the tick and read from a cache
with a TTL; "offline" means nightly / Kaggle.

### 3A. The live game reasoner (per game, every poll)

**A1 · Stoppage start odds and type**
In: `GameState`, Tracker row (v2 + v3 sequence features: last 20 play classes, wall gap, pace,
stoppages/fouls/scores this period, timeouts used this period), league handbook state
(owed TV-timeout marks from `BreakBook`). Out: `StoppageForecast` (`p_start_15/30/60`, `type_p`
over the 9 kinds, `in_data_now`). Labels: **O** (`stop15/30/60`, `stop_type` in `brain3data`) +
**R** (handbook: an owed mark raises the floor). Metric: AUC/Brier/ECE per league and per kind,
graded live through the app path (`live_eval3`); gate: ECE ≤ 0.03, Brier skill > rules. Owner:
**GBM** (Brain v3 `stop*`, `type`) blended with **RULES** only when the rules have *evidence*
(a break in the data is never second-guessed; the current 50/50 `blend()` damaged calibration:
live ECE 0.12–0.17 vs 0.01–0.10 for the brain alone, so blend in log-odds with a learned weight
fitted on valid nights, or don't blend). Live, < 5 ms per game (Forest3 one pass).

**A2 · Dead-window length and restart countdown** (upgrade #1)
In: the stoppage that just started (kind, period, clock, crunch, league, broadcaster/app), its
dead marks so far (`DeadTracker`), the feed freshness. Out: `RestartCountdown` (`len_q10/50/90`,
`entry_q10/50/90`, `safe_entry_s` = cautious bound, `resume_at_feed`, `exact` when the restart is
in the data, `kind`). Labels: **O** (`len_s`, `entry_s` from archive wallclocks: entry → next snap /
faceoff / inbound; NHL from the game clock). Metric: pinball loss per quantile; **overrun rate**
`P(real < safe_entry) ≤ 5 %` per league × kind × crunch (brain3cal's α); median absolute error of
`entry_q50` in seconds. Owner: **GBM** (`len*`, `entry*` heads) + **RULES** floors (`FLOOR_S`) as
the hard lower bound: `safe_entry = max(floor, scale × q10)`. Live, < 5 ms. This is the one clock
`plan_entry`, `_break_remaining`, `_reel_fits`, `_crunch_replay` and `standby` must all read.

**A3 · Safe-to-cut-now / safe-to-enter-now** (upgrade #2)
In: A2's countdown for the game on screen (leave) or the destination (enter), the SyncModel
estimate (`delay_s`, `sigma_s`, `uncertain`), the path cost window `[fastest_s, cost_p90_s]`, league
(discrete vs continuous). Out: `CutSafety` (`p_dead_over_window`, `lo/hi` seconds of certified dead
time, `abstain`, `why`, `rewind_s` if a DVR rewind makes it safe). Labels: **O** from `cutgrade`
(clean cut = no play on screen at the cut; mid-play entry = picture up during a play), computed
with true wallclocks. Metric: mid-play cuts = 0 (hard), entries at a dead ball ≥ 95 %, entries
mid-play ≤ 5 % and never above today, **coverage** (share of moments not abstained) reported next
to it. Owner: **TINY** (a per-league logistic/isotonic layer on top of A2 + sigma, with a
conformal cut fitted on valid nights) wrapped in **RULES** (`leave_gate`, `plan_entry`): unsure is
unsafe, abstain is "wait". Live, < 2 ms. The LLM has no business here.

**A4 · Lock-in and big-play odds with calibrated uncertainty** (upgrade #3)
In: the brief facts (`brief.facts`), A1/A2/A5 outputs, personal flags. Out: `LockIn`
(`lock_in` none|watch|must_see, `p_big_h` for the league horizon, `p_crunch_next`, `safe_to_leave`,
`conf`, `abstain`, `stay_s`, `horizon_s`). Labels: **O** (`y`, `big_next`, `crunch_next`, `t_big`)
+ **C** gold rules (`gold.py`) which always win. Metric: the existing lines (must_see
precision/recall/lift, lock_acc vs gold, unsafe leaves, over-cautious, eta MAE, usable JSON) plus
ECE of `p_big_h` and **coverage at the don't-know cut**, per slice (crunch, early/late, garbage,
your team, league). Gate: lock_acc ≥ 95, unsafe ≤ 0.5, JSON 100. Owner: **GBM** head is the
decision; **LLM** in `/score` mode (`choices = ["none","watch","must_see"]`, one prefill, no
decoding) as a second opinion only where the head's odds sit in the uncertain band
(`|p − cut| < 0.1`); the student's `generate` answer carries the words. Where it is weak today
(NFL key AUC 0.68, NHL 0.58) the fix is **features**, not a bigger LLM: NFL drive state
(`drive_plays`, `possession_trailing`, `red_zone`, `def_timeouts` exist; add yards-per-play this
drive, hurry-up flag, 4th-down decision odds), NHL PP clock remaining and shots on the PP, pulled
goalie, and shot-attempt rate from the last 20 events. Head live < 1 ms; `/score` ≤ 0.8 s async.

**A5 · Next big play: what and when** (upgrade #4)
In: same as A4 plus league-specific state. Out: `NextBigPlay` (`kinds[]` each with `p_60`, `p_180`,
`eta_q25/50/75`; `top_kind`, `top_eta_s`). Kinds per league: NFL td/fg/turnover/explosive(20+);
NBA clutch bucket (Q4/OT ≤ 2:00, within 5)/lead change/run; NHL goal/pp_goal/empty_net_goal;
MLB run/hr/walk-off; college = base league. Labels: **O** (`sportsgrade.is_big`, `_espn_big`, WP
delta ≥ `wp_key`). Metric: AUC and lift per kind; `eta_mae_s`; **chase hit rate** (chases that
show the play live). Owner: **GBM** (extend `big60/180` to multi-head). Live, < 5 ms. Consumers:
`_chase`, `_hold_for_big`, standby MOMENT, reel timing, A10 expectations.

**A6 · Leverage and hype from win-probability swings**
In: WP series (feed WP or `analytic_wp`), score runs, lead changes, time left. Out: `Leverage`
(`wp_leverage` 0–1 = how much the next score moves WP, `swing_recent` = |ΔWP| last 2–5 min,
`hotness` 0–100, `comeback` flag, `fresh_flip` fade). Labels: **O** (`hot` target already defined
in v2; leverage is a deterministic function of the WP model). Metric: Spearman vs v2 hotness (≥ 0.95
on held-out), stability (no flicker: ≤ 1 lock change per minute from this term alone). Owner:
**RULES** + **GBM** `hot` head (already R² 0.94–0.96 on NFL/NBA; NHL 0.86, MLB 0.94). Live, < 1 ms.
This is "Hype v2": keep it inside the brain, bounded by `brain_points` caps.

**A7 · Stakes: standings, playoff/elimination, rivalry, rankings** (upgrade #5)
In: nightly pulls of ESPN standings (free), schedule remaining, rankings (`Team.rank` exists), a
static rivalry table per league (config, not code), odds (`spread`, `odds_text`), `national_tv`.
Out: `Stakes` (`stakes_mult` 0.8–1.2 like `_stakes`, `terms[]` with provenance: "WSH clinches with a
win", "loser eliminated", "rivalry", "top-10 matchup", "primetime"). Labels: **R** (deterministic
elimination/clinch math; a 1-line LLM check for unusual tiebreaks is optional), **N** ("this
mattered" taps). Metric: agreement with Nick's taps ≥ 90 %; A/B on replays: big plays live not
worse. Owner: **RULES** (offline nightly precompute per game id, cached), tiny **GBM** only if the
taps show the rules miss something. Lookup < 1 ms. Never lets a decided game past garbage time.

**A8 · Your-team and bet awareness**
In: `Personal` (teams by league, bets from config, fantasy starters, manual boosts), `GameState`.
Out: `PersonalStake` (`mine`, `my_team`, `bet_sweat` 0–1, `bet_decisive_next` = P(the next score
flips the bet), `bet_margin`, `fantasy_live_starters[]`, `labels[]`). Labels: **R** (`bets.sweat`,
`bets.margin`), **O** for `bet_decisive_next` (did the next score flip the margin sign). Metric:
Brier of `bet_decisive_next`; your-team crunch live 100 % and your-team big plays seen 100 % stay
at 100 %. Owner: **RULES** + **TINY**. Live, < 1 ms. Personal data stays in config (product rule).

**A9 · Per-game journal (narrative memory)** (upgrade #6)
In: the game's event stream (plays, scores, stoppages, injuries, ejections, milestones from
`leaders`/`spotlight`), A6 swings, A8 flags, the decider's own history for this game (when we were
on it, what we missed). Out: `GameJournal` (structured `facts[]` by rules with timestamps; `summary`
1–2 lines ≤ 160 chars by the LLM; `missed_big[]`; `watched_s`; `stake_line`). Labels: **R** for
facts; **T** for summaries (teacher writes the reference line from the same facts; student is
graded on factuality: every named fact must exist in `facts[]`). Metric: fact precision ≥ 98 %
(no invented facts: the `teacher_words` leak check generalised), ROUGE-L vs teacher ≥ 0.5 on held-
out games, latency. Owner: **RULES** (facts) + **LLM** `generate` ≤ 100 tokens, async, every 60–120 s
per hot game (on screen + top 2 candidates), never for garbage-time or quiet games (`due()` logic).
The journal replaces "last 8 plays" as the brief's history and is what the phone shows.

**A10 · Expectations and storyline (`ReasonerView`)**
In: A4, A5, A2, A9. Out: `ReasonerView` as the contract already states, with `expectations[]` as
`(what, within_s, p)` triples **filled from A5's numbers, not from prose**; `storyline` and
`lock_reason` from A9/LLM. Labels: **O** (expectations graded as A5), **T** (words). Metric: the
A4/A5 lines plus "expectation hit rate" (what happened within `within_s` with p ≥ 0.5). Owner:
**RULES** assemble; **LLM** words. Async, TTL 60 s; the tick reads the cache (5 ms budget).

### 3B. Cross-game decision capabilities

**B1 · Jump-or-stay** (upgrade #8)
In: A2 countdown for the game on screen (`safe_entry_s`), A4/A5 for it (big right after the
restart?), personal flags, the reel offer (B2: `seconds`, `value`), path costs both ways, margin.
Out: `JumpOrStay` (`decision`, `reel_s`, `ev_jump`, `ev_stay`, `budget_s`, `why`, `abstain`).
Labels: **O** + **R** hindsight label exactly as `jump.py` (never mid-play, crunch first, NFL drives
first, your team late, big right after, value ≥ 1.0, ≥ 3 clips), **T** words, **C** hard-case
review. Metric: `reels_in_dead_time_pct` = 100, `big_missed_on_reel` = 0, `agree_pct`,
`jump_recall_pct` ≥ 80 on hard cases. Owner: **RULES** EV (`core.ev` units: points per second over
the window) fed by GBM numbers; the LLM only writes `why`. Live, < 5 ms.

**B2 · Catch-up reel: clip value, selection, ordering** (upgrade #7)
In: ESPN clips (`clips_from_summary`), matched play (`match_play` → PBP row: WP delta, score, crunch,
your team, bet), seen-state (`_saw`, `shown`), league weights, age. Out: `ReelPlan` (`clips[]` each
with `value`, `seg_s`, `order`, `reason`; `total_s`; `expected_value`; `drop_if_short[]`). Labels:
**O** (play importance = WP delta, scoring, crunch; freshness), **N** (reel skip/keep/"show more of
this" taps), **T** (headline → play kind when `match_play` fails). Metric: reel value captured vs
the oracle set; Nick keep-rate ≥ 80 %; 5+ clips in 30–60 s ≥ 95 % of offers. Owner: **TINY/GBM**
ranker (pairwise on taps once there are ~200) over **RULES** features; **LLM** only to parse odd
headlines offline. Build offline/async (ffmpeg); ranking < 20 ms.

**B3 · Multi-game scheduling: what to pre-tune on the hidden stick** (upgrade #9)
In: A1/A2 for the game on screen (break odds, countdown), A4/A5/A8 for every candidate, crunch-soon
flags, replay queue, reel offer, `SwitchPaths` costs per target (Prime vs YTTV vs flip), `my_tip_at`,
the decider's `want`. Out: `Preload` (`target`, `class`, `p_next_180`, `saves_s`, `hold_until`,
`why`, `alternatives[]`). Labels: **O** from replays: the switch the decider actually made within
180 s (and its cost). Metric: preload hit rate ≥ 70 %, flip share of switches, switch < 3 s on
≥ 95 % (once the TV path is measured), re-tunes per hour (hysteresis). Owner: **GBM** `p_next_180`
over **RULES** classes (keep NEXT/HOME/REPLAY as hard overrides). Live, < 20 ms.

**B4 · Crunch arbitration and league order**
In: all crunch candidates, personal, closeness, league order (config), A2 for each (next snap
airing), A3 for the hop. Out: `CrunchPlan` (`pick`, `hold`, `hop_ok`, `return_by`, `why`). Labels:
**R** (Nick's order NFL > CFB > NBA > rest; your team/bet first) + **O** (crunch plays live).
Metric: crunch live ≥ 95 % of the priority winner (feasible ceiling shown), your-team crunch 100 %.
Owner: **RULES**. Live. (Not an ML task; listed so nobody makes it one.)

**B5 · Explanations: "why I switched"** (upgrade #10)
In: `Action(ev, ev_stay, terms, reason, guard)`, the two games' journals (A9), A2/A3 numbers.
Out: `Explanation` (`line` ≤ 140 chars for the phone, `terms[]` numeric, `guard`, `counterfactual`
one line: "stayed because…"). Labels: **T** (teacher writes the reference from the same terms),
**N** (thumbs on the phone). Metric: factuality ≥ 98 % (every number/name in `line` exists in
`terms`/journal), thumbs-up ≥ 80 %. Owner: **RULES** template first (always available); **LLM**
`generate` ≤ 60 tokens async to make it read well. Never blocks a switch.

**B6 · Sync self-check**
In: SyncModel estimates per channel, boundary/scorebug/tap samples, disagreement between sources.
Out: `SyncHealth` (`sigma_now`, `drift_s_per_h`, `uncertain`, `needs_sample`, `action`:
none|widen|ask_tap|prefer_prime_sample). Labels: **O** (taps and score-bug samples are ground truth
for the predicted delay). Metric: delay error < 2 s on ≥ 95 % of samples; time spent `uncertain`.
Owner: **TINY** (Kalman already) + **RULES**. Live, < 1 ms. The watchdog reads it (C4).

### 3C. The watchdog

**C1 · Fault classification**
In: `Obs` v2 (today's fields + `sync_health`, `feed_staleness_s` per source, `llm_server` health
`rss_mb/peak`, `hidden_stick_state` playing|paused|prompt|frozen|black, `tv_input`, `last_switch`
outcome, `tick_overruns`). Out: `FaultReport` (`faults[]` of `Fault(code, kind, severity, component,
detail, since, count)`), kind ∈ the 13 `KIND_HELP` kinds + `sync_drift`, `feed_stale`,
`tick_overrun`, `hidden_stick_dead`. Labels: **R** (`policy.KNOWN`, `rules`), **T** paraphrase +
blind-classify agreements (`wd_paraphrase/wd_classify`), **N** (Nick marks a false alarm). Metric:
kind accuracy ≥ 90 % per kind on held-out wordings, unusable JSON 0. Owner: **RULES** for known
signatures; **LLM** `/score` over the kind list for unseen wordings (one prefill, ≤ 0.8 s) instead
of free-form JSON. 60 s loop.

**C2 · Action choice with the guard** (upgrade #10)
In: `FaultReport`, context (crunch on screen, away, deploy in progress, restarts last hour,
governor level). Out: `WatchdogVerdict` (`action`, `target`, `why`, `source` rule|model|guard,
`defer_until` when crunch is on). Action set grows from 5 to: `none, notify, restart_service,
reconnect_stick, prune_disk, reload_app(stick, app), dismiss_prompt(stick), resync(channel),
retune_hidden(target), drop_model, pause_autopilot(reason), ask_user(question)`. Labels: **R**
(`teach_novel` extended per kind), **C** (Claude reviews the action table), **O** (did the fix
clear the fault within 2 checks: the realized outcome). Metric: ≥ 95 % correct actions on held-out
errors, every kind ≥ 90 %, 0 unsafe (guard blocks), **fix success rate** (fault gone after action)
≥ 80 %, no action during crunch except when the app is down. Owner: **RULES** + **LLM** (`/score`
over the action list, then `generate` ≤ 40 tokens for `why`). 60 s loop, ≤ 2 s.

**C3 · Self-diagnosis (nightly and on demand)**
In: `data/traces.jsonl`, `agent.jsonl`, scorecards of the night (mid-play entries, dead air, big
plays missed, reel misses), sync samples, memory/thermal history. Out: `NightReport` (`misses[]`
each with `kind`, `game`, `t`, `probable_cause`, `evidence`, `suggested_fix`; `top_3`; `gates_hit`).
Labels: **C** (Claude's own root-cause write-ups become the reference), **N**. Metric: cause
agreement with Claude ≥ 80 % on a 20-case review set; report ≤ 25 lines. Owner: **RULES** gather,
**LLM** `generate` offline (no budget pressure; ≤ 400 tokens), **CLAUDE** when a fix needs code
(D5). Offline.

**C4 · Picture-state detection**
In: ADB media-session state, screenshot compressibility (`UNIFORM_BYTES_PER_PX`: DRM-black vs real
picture), OCR text from a Prime frame, position/timeline advance. Out: `PictureState`
(`state` playing|paused|prompt|frozen|black|home|unknown, `conf`, `since_s`). Labels: **R** +
**N** (a "stuck" tap). Metric: ≥ 95 % state accuracy on a labelled set of 200 frames; false "frozen"
≤ 1 %. Owner: **RULES** (+ a tiny image classifier only if OCR/text fails). 10–15 s loop.

### 3D. Server-side teacher and BRAIN

**D1 · Label factory (round 2+)**
In: ESPN archive (3 seasons + current, 6 leagues), time split by whole nights, locked nights never
downloaded. Out: `MomentRow` (brief + facts + hindsight + gold + hardness + head_p) and
`TeacherAnswer` (grounded words, blind calls). Labels: **O** + **C** gold rules + **T**. Metric:
teacher blind beats today's rules on valid nights before its words are trusted (`blind_train_every`
check), leak checks (no hindsight phrases, no locked nights, no brain outputs in the brief until the
brain is retrained before `test_start`). Owner: **TEACHER** (32B hardest-first, 14B tail). Offline,
Kaggle (30 GPU-h/week).

**D2 · Hard-case mining and the gold queue**
In: `hardness()` (head wrong, near the cut, must_see, real big play followed, crunch, your team,
rule disagreement), jump `hard`, Nick's taps. Out: `GoldCase` (row id, proposed gold, rule fired,
teacher opinion, reviewer, status). Metric: gold set ≥ 400 per league with ≥ 40 % must_see; inter-
rater agreement (Claude vs teacher) ≥ 90 %. Owner: **CLAUDE** writes/reviews rules; **TEACHER**
proposes. Offline.

**D3 · The handbook: per-league rulebook and TV-timeout structure (versioned)**
In: the rules already encoded in `breaks.py`/`cuts.py`/`garbage.py`/`engine.py`, measured archive
distributions, broadcaster/app (ESPN, CBS, FOX, NBC via YTTV, Prime TNF/NBA), season. Out:
`HandbookEntry` (league, season, broadcaster, stoppage kind, trigger rule, exclusions, `len_q10/50/90`
measured, `restart_cue`, source note). Metric: every `MEDIAN_S`/`FLOOR_S`/`LEN` constant traces to
an entry; nightly drift check (measured vs handbook, flag > 15 %). Owner: **RULES** as data (YAML
in `redzone/`), **CLAUDE** maintains; the **TEACHER** reads it in its system prompt so its words
match the app's rules. Offline. Contents per league are listed in §5.

**D4 · Nightly replay grading and gate reports**
In: the night's archive + traces. Out: `GateReport` per league and slice (the architecture §7
lines), plus the train/valid/test gap for each model. Metric: the gates. Owner: **RULES** (CI).
Offline.

**D5 · Auto-repair (BRAIN)**
In: `NightReport` (C3), failing gate lines, traces. Out: a GitHub issue → `claude-code-action` PR →
gates → safe deploy (status queue #5/#30). Metric: PR passes CI + scorecards no worse; mid-play
lines never up. Owner: **CLAUDE**. Offline, on Nick's OAuth token (he adds it).

**D6 · Knob auto-tune**
In: `core.*` knobs with `LIMITS`, replays on train+valid, forward-chaining folds. Out: `KnobSet`
with mean and worst-fold scores. Metric: worst fold not worse than today; locked set once per
release. Owner: **RULES** (search) — never the LLM. Offline.

**D7 · RedZone-as-teacher (imitation, legal path)**
In: Nick's own YTTV NFL RedZone (live/DVR), hidden-stick score-bug OCR every ~2 s (DRM-black gate:
a 5-minute Sunday check first), aligned to ESPN PBP. Out: `WhipAroundLabel` (t, game shown, why
class: TD live / red-zone drive / 2-min / whip at score). Metric: "RedZone agreement %" as a
scorecard line (our gates still win: your teams, no commercials). Owner: **RULES** align; **GBM**
imitation features. Offline.

### 3E. The REFLEX student: what it owns

Own (and train adapters for): (1) reasoner words `storyline/why/next` and the `/score` second
opinion on `lock_in` in the uncertain band (A4/A10); (2) journal summaries (A9); (3) jump `why`
(B1); (4) explanations (B5); (5) watchdog kind and action classification for *unseen* wordings via
`/score`, plus `why` (C1/C2); (6) nightly self-diagnosis prose (C3, offline mode, no budget).
Do **not** own: any timing number, any probability the decider acts on without a calibrated head
behind it, crunch arbitration, the guard. One adapter per job on the shared base (`agent/model/
<job>/`), ≤ 1.5 GB RSS total including the KV cache (`MAX_PROMPT_TOKENS` 3072 ≈ 0.1 GB).

---

## 4. LLM or classic ML: the split, with latency budgets for the M2

**Measured/estimated speed of Qwen3-1.7B-4bit through mlx_lm on the 8 GB M2 Air** (the app, the
sticks' ADB links and polling run at the same time; the governor can drop the model when the Mac is
hot or low on memory): prefill ≈ 500–900 tok/s, decode ≈ 40–70 tok/s idle, roughly half under load
or thermal throttling. `agent/llm.py` reports the watchdog's short JSON (~250-token prompt, ~35-token
answer) at "about a second"; the reasoner's 160-token answer is 3–5 s. MLX is not re-entrant, so
requests queue one at a time. Training on the Mac ran at ~30 tok/s and starved it (`lora.yaml`
note): never train locally. Everything below is a budget to **measure** with `/health` and the
`ms` field, not a promise.

| Mode | Prompt | Output | Budget | Use for |
|---|---|---|---|---|
| `/score` (one prefill, logprobs over 3–12 choices, no decoding) | ≤ 250 tokens | 0 decoded | **≤ 0.8 s**, async | lock_in second opinion, watchdog kind/action, jump decision check, yes/no "is this fact in the journal" |
| `generate`, short JSON | ≤ 250 tokens | ≤ 48 tokens | **≤ 1.5 s**, async | watchdog `why`, jump `why`, explanation line |
| `generate`, narrative | ≤ 400 tokens (journal facts + brief) | ≤ 100 tokens | **≤ 3 s**, async, TTL 60 s | storyline, journal summary, `next` |
| `generate`, offline | any | ≤ 400 tokens | no budget, idle hours | nightly self-diagnosis |

**Duty cycle:** at most one call every 20 s per hot game (`reasoner.min_interval_s`), hot games =
on screen + top 2 candidates, so ≤ 9 calls/min worst case ≈ 25–40 % busy. Target ≤ 6 calls/min.
Nothing inside the 3 s tick waits on the model (architecture: reasoner 5 ms **cached**). If the
server is down or the governor says no, every consumer uses the rules' template (degraded mode,
never an error). Keep the answer key order (`lock_in` first) so `/score` can read it with one prefill.

**Right tool for the job**

| Capability | LLM? | Why |
|---|---|---|
| Stoppage odds/type, length quantiles, restart countdown (A1, A2) | **No**: GBM + rules | numeric, needs calibration and < 5 ms; per-kind distributions are a regression problem; rules carry the handbook |
| Safe to cut / enter (A3) | **No**: tiny + rules | 0-mid-play is a hard constraint; needs conformal coverage, not prose |
| Lock-in odds (A4) | GBM decides; LLM `/score` second opinion in the uncertain band only | the brief is 150–250 tokens of structured facts the GBM already sees; the LLM adds judgment on rare patterns, cheaply, only when the head is unsure |
| Next big play (A5), leverage (A6) | **No** | learned from millions of outcome rows; LLM would be slow and uncalibrated |
| Stakes (A7), personal (A8) | **No**: rules on free standings data; LLM optional offline for odd tiebreak rules | deterministic math; provenance matters |
| Journal summary, storyline, `next` wording (A9, A10) | **Yes** (`generate`, async) | language over structured facts is exactly what a 1.7B does well after distillation; graded for factuality |
| Jump-or-stay (B1) | decision **no** (EV); `why` yes | the label is Nick's rules + hindsight; the EV needs A2's countdown |
| Reel ranking/ordering (B2) | **No** (ranker); LLM offline to parse odd headlines | taps + PBP outcomes train a ranker; ordering is rules |
| Scheduling / pre-tune (B3) | **No** | a 180 s forecast from numbers the tick already has |
| Explanations (B5) | **Yes** (template first, LLM polish async) | phone text; never blocks |
| Watchdog known faults (C1/C2) | **No** (rules) | one right answer |
| Watchdog unseen wordings / novel errors | **Yes** (`/score` over kinds and actions, then `why`) | generalising to new error text is the one thing the watchdog adapter is for; guard keeps the last word |
| Self-diagnosis (C3) | **Yes**, offline | prose over traces, no deadline; Claude for code fixes |
| Teacher labels (D1), gold (D2), handbook (D3), auto-repair (D5) | 32B teacher / Claude | server side, free GPU hours or Nick's subscription |
| Knob tuning (D6) | **No** | forward-chaining search; the LLM would overfit by eloquence |

---

## 5. Sport-specific knowledge the models must carry (the handbook, D3)

Numbers are what the repo already measured on `data/archive` (Nov 2026) or sourced in
`breaks.py`; they belong in a versioned YAML with `season` and `broadcaster`, re-measured nightly.

**NFL (and CFB on NFL rules)**
- Commercial after a score (~160 s, restart = kickoff; then kickoff → first snap ≥ 25 s), after a
  punt (~150 s), often after a turnover (~143 s, 50 %), two-minute warning (~167 s), end of Q1/Q3
  (~200 s), halftime (~857 s, floor 800). A team timeout is ~50 s and usually no commercial; an
  "Official Timeout" is one. Replay review ~217 s median (floor 80), injury ~80 (floor 18),
  penalty ~50, huddle ~34 (floor 14), kickoff dead ~41.
- Clock rules: 40/25 s play clock; the clock stops on incompletions, out of bounds, penalties,
  change of possession; inside 2:00 the two-minute warning; OT (10 min, both teams possess unless a
  TD; playoffs differ). CFB: clock stops on first downs only inside 2:00 since 2023; OT is
  alternating possessions from the 25 (2-pt tries from the 3rd OT). Four timeouts a side in CFB
  halves vs three in the NFL.
- RedZone principles as gold: every TD live, red-zone drives (≤ 20 yards to goal), two-minute
  drills, never a commercial, whip-around at scores.
- Crunch: last 2:00 of a one-score (≤ 8) game or OT. Garbage: 2nd half ≥ 25; Q4 ≤ 8:00 ≥ 17;
  ≤ 2:00 ≥ 12; ≤ 0:45 ≥ 9; or WP ≥ 99 % in Q4.

**NBA**
- Mandatory timeouts: the first dead ball under 7:00 and under 3:00 of each quarter if none has
  been called; the **first two timeouts of a quarter are the long ones (~188 s, floor ~100–107)**
  whoever calls them, #3+ are short (~107 s, floor 15). End of Q1/Q3 ~171 s (floor 145), halftime
  ~933 s (floor 800). Two team timeouts max in the last 3:00 of Q4. Coach's challenge/review ~153 s.
  Shooting foul → free throws (dead ~22–25 s per sequence; the last free throw is itself the
  restart cue). Inbound → up to a 24 s shot clock with nothing logged (`SILENT_S`).
- Clutch bucket (what counts as big): Q4/OT, ≤ 2:00, within 5 before the shot. Crunch: last 3:00
  within 7 (graded last 2:00 within 5) or OT. Garbage ladder per `garbage.py`.
- ESPN wallclock freezes for stretches (8 % of timeouts): windows there are uncertified.

**Men's college basketball (CBB)**
- Media timeouts at the first dead ball under 16/12/8/4 minutes of each half (~184 s); a team
  timeout within 30 s before a mark (or after it) becomes the media timeout. Halves of 20 min,
  halftime ~980 s, 30 s shot clock, 5-min OT. Halves map to NBA quarters for the rules
  (`pro_period`). Fouls: bonus at 7 team fouls (1-and-1), double bonus at 10.

**Women's college basketball (WCBB)**
- Quarters of 10 min; one media timeout per quarter at the first dead ball under 5:00 (~174 s); end
  of quarter ~186 s; halftime ~983 s; bonus at 5 team fouls per quarter (two shots).

**NHL**
- TV timeouts at the first stoppage after 6, 10 and 14 minutes **elapsed** (~149 s, floor 130),
  never after a goal or icing, on a power play, in the last 30 s of P1/P2 or the last 2:00 of P3.
  Goal dead time ~48 s (floor 35), whistle ~37, icing ~30, penalty ~56, review ~200 (floor 100),
  team timeout ~99 (one per game, 30 s on the ice), intermission ~1131 s (floor 1080–1135).
- Power play 2:00 minor / 5:00 major, ends on a PP goal (minor); 5-on-3; pulled goalie ("empty net")
  in the last ~1:30 down one or two; OT 3-on-3 for 5 min then a shootout (regular season). Crunch:
  last 3:00 of P3 within 1, or OT/SO. Garbage: P2 ≥ 5, P3 ≥ 4, P3 ≤ 10:00 ≥ 3, last minute ≥ 2 with the
  trailing goalie still in. The restart cue is the faceoff (logged).

**MLB**
- Between innings ~175 s (floor 115), pitching change ~178 s (floor 60), between batters ~29 s
  (floor 8), between pitches ~19 s (floor 6; the pitch clock is 15/18 s). Two outs → the next out
  ends the half-inning. Extras start with a runner on second. Leverage index (`estimate_li`) and
  the walk-off spot drive the moment score. Crunch: 8th inning on within 2 runs; garbage: 5th+ ≥ 8,
  7th+ ≥ 6, 9th ≥ 5.

**Broadcast/app layer (all leagues)**
- Stream delay per channel (YTTV ~20–45 s, Prime longer; measured per tune), YTTV shows change on
  the quarter hour (boundary samples), Prime frames are OCR-readable (score bug), YTTV/Peacock are
  DRM-black. Switch costs (defaults): YTTV 3 s, Prime 8 s (eval config 27 s), ESPN 7 s, HDMI flip
  1–4 s; SmartThings `setInputSource` measured median 1.98 s.

---

## 6. JSON schemas

Compact JSON-Schema style; `t` fields are epoch seconds in **feed** time unless named `_screen`,
durations end in `_s`, probabilities are 0..1. Every probabilistic object carries `conf`,
`abstain` and `model_version`; consumers treat `abstain: true` as "use the rules".

### 6.1 StoppageForecast (A1)
```json
{
  "$id": "StoppageForecast",
  "type": "object",
  "required": ["game_id", "at", "p_start_15", "p_start_30", "p_start_60", "type_p", "in_data_now", "model_version"],
  "properties": {
    "game_id": {"type": "string"},
    "at": {"type": "number", "description": "feed wallclock of the event this reading is about"},
    "p_start_15": {"type": "number"}, "p_start_30": {"type": "number"}, "p_start_60": {"type": "number"},
    "type_p": {"type": "object", "additionalProperties": {"type": "number"},
               "description": "keys: none,tv_timeout,team_timeout,review,free_throws,injury,score_break,period_end,other; sums to 1"},
    "in_data_now": {"type": "boolean", "description": "a break is already in the data: no guess"},
    "owed_mark": {"type": ["string", "null"], "description": "handbook mark still owed this period, e.g. 'nba_under_7:00'"},
    "why": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "conf": {"type": "number"}, "abstain": {"type": "boolean"}, "model_version": {"type": "string"}
  }
}
```

### 6.2 RestartCountdown (A2)
```json
{
  "$id": "RestartCountdown",
  "type": "object",
  "required": ["game_id", "kind", "started_at", "len_q", "entry_q", "safe_entry_s", "exact", "model_version"],
  "properties": {
    "game_id": {"type": "string"},
    "kind": {"type": "string", "description": "dead-window kind from cuts.after_kind, e.g. tv_timeout, score, timeout_long, free_throw, intermission"},
    "started_at": {"type": "number"},
    "len_q": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "[q10,q50,q90] total dead time, restart included"},
    "entry_q": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "[q10,q50,q90] seconds from now until the snap/inbound/faceoff/pitch"},
    "safe_entry_s": {"type": "number", "description": "max(floor, scale*q10): a trip planned on this overruns <= 5% of the time"},
    "floor_s": {"type": "number"},
    "resume_at": {"type": ["number", "null"], "description": "feed time play resumes when known from the data (clock moved, faceoff logged, next snap)"},
    "exact": {"type": "boolean"},
    "restart_cue": {"type": "string", "enum": ["snap", "kickoff", "inbound", "last_free_throw", "faceoff", "pitch", "unknown"]},
    "conf": {"type": "number"}, "abstain": {"type": "boolean"}, "model_version": {"type": "string"}
  }
}
```

### 6.3 CutSafety (A3)
```json
{
  "$id": "CutSafety",
  "type": "object",
  "required": ["game_id", "direction", "window_screen", "p_dead", "abstain", "decision", "why"],
  "properties": {
    "game_id": {"type": "string"},
    "direction": {"type": "string", "enum": ["leave", "enter"]},
    "window_screen": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                      "description": "[t0,t1] screen time the picture changes over (fastest path .. p90 cost)"},
    "p_dead": {"type": "number", "description": "P(no play on screen anywhere in the window)"},
    "certified_dead_s": {"type": "number", "description": "seconds of surely-dead time after t1 (0 if none)"},
    "sigma_s": {"type": "number", "description": "the sync doubt used"},
    "rewind_s": {"type": "number", "description": "DVR rewind that makes the landing dead (0 = none)"},
    "abstain": {"type": "boolean"},
    "decision": {"type": "string", "enum": ["safe", "wait", "unknown_discrete_ok"]},
    "why": {"type": "string", "maxLength": 120},
    "model_version": {"type": "string"}
  }
}
```

### 6.4 LockIn v2 (A4; the student's JSON keeps today's 7 keys first, in order)
```json
{
  "$id": "LockIn",
  "type": "object",
  "required": ["lock_in", "safe_to_leave", "confidence", "storyline", "why", "next", "stay_s"],
  "properties": {
    "lock_in": {"type": "string", "enum": ["none", "watch", "must_see"]},
    "safe_to_leave": {"type": "string", "enum": ["yes", "no"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "storyline": {"type": "string", "maxLength": 100},
    "why": {"type": "string", "maxLength": 140},
    "next": {"type": "string", "maxLength": 100, "description": "what to expect and when: 'within 1:15' or 'in 45 s'"},
    "stay_s": {"type": "integer", "minimum": 0, "maximum": 900},
    "p_big_h": {"type": "number", "description": "head odds of a big/crunch play within horizon_s (calibrated)"},
    "p_crunch_next": {"type": "number"},
    "horizon_s": {"type": "integer", "description": "NFL/NHL 180, NBA 120, MLB 300"},
    "abstain": {"type": "boolean", "description": "confidence below the valid-night cut: the rules decide, the moment is escalated"},
    "source": {"type": "string", "enum": ["head", "model", "rules", "gold"]},
    "model_version": {"type": "string"}
  }
}
```

### 6.5 NextBigPlay (A5)
```json
{
  "$id": "NextBigPlay",
  "type": "object",
  "required": ["game_id", "at", "kinds", "top_kind", "model_version"],
  "properties": {
    "game_id": {"type": "string"}, "at": {"type": "number"},
    "kinds": {"type": "array", "items": {
      "type": "object",
      "required": ["kind", "p_60", "p_180"],
      "properties": {
        "kind": {"type": "string", "description": "nfl: td|fg|turnover|explosive; nba: clutch_bucket|lead_change|run; nhl: goal|pp_goal|en_goal; mlb: run|hr|walkoff"},
        "p_60": {"type": "number"}, "p_180": {"type": "number"},
        "eta_q": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "[q25,q50,q75] seconds"},
        "for_team": {"type": ["string", "null"]}
      }}},
    "top_kind": {"type": "string"}, "top_eta_s": {"type": ["number", "null"]},
    "conf": {"type": "number"}, "abstain": {"type": "boolean"}, "model_version": {"type": "string"}
  }
}
```

### 6.6 Leverage (A6) and Stakes (A7)
```json
{
  "$id": "Leverage",
  "type": "object",
  "required": ["game_id", "wp_home", "wp_leverage", "swing_recent", "hotness"],
  "properties": {
    "game_id": {"type": "string"},
    "wp_home": {"type": "number"},
    "wp_leverage": {"type": "number", "description": "4*wp*(1-wp) scaled by lateness"},
    "swing_recent": {"type": "number", "description": "sum |dWP| over the league's vol_w window"},
    "hotness": {"type": "number", "minimum": 0, "maximum": 100},
    "comeback": {"type": "boolean"}, "fresh_flip_s": {"type": ["number", "null"]},
    "lead_changes": {"type": "integer"}, "run": {"type": ["string", "null"], "description": "e.g. 'BOS 12-0'"}
  }
}
```
```json
{
  "$id": "Stakes",
  "type": "object",
  "required": ["game_id", "stakes_mult", "terms"],
  "properties": {
    "game_id": {"type": "string"},
    "stakes_mult": {"type": "number", "minimum": 0.8, "maximum": 1.2},
    "terms": {"type": "array", "items": {
      "type": "object",
      "required": ["kind", "points", "text", "source"],
      "properties": {
        "kind": {"type": "string", "enum": ["clinch", "eliminate", "playoff_race", "rivalry", "ranked", "national_tv", "close_spread", "both_good", "both_bad", "streak", "milestone"]},
        "points": {"type": "number"}, "text": {"type": "string", "maxLength": 80},
        "source": {"type": "string", "description": "standings@date, config.rivalries, espn.odds ..."}
      }}},
    "computed_at": {"type": "number"}, "ttl_s": {"type": "integer", "default": 3600}
  }
}
```

### 6.7 PersonalStake (A8)
```json
{
  "$id": "PersonalStake",
  "type": "object",
  "required": ["game_id", "mine", "bet_sweat", "labels"],
  "properties": {
    "game_id": {"type": "string"},
    "mine": {"type": "boolean"}, "my_team": {"type": ["string", "null"]},
    "bet_sweat": {"type": "number"}, "bet_margin": {"type": ["number", "null"]},
    "bet_decisive_next": {"type": ["number", "null"], "description": "P(the next score flips the bet's sign)"},
    "bets": {"type": "array", "items": {"type": "object", "properties": {
      "kind": {"type": "string", "enum": ["moneyline", "spread", "total", "prop"]},
      "team": {"type": ["string", "null"]}, "line": {"type": ["number", "null"]}, "side": {"type": ["string", "null"]},
      "status": {"type": "string", "enum": ["covering", "push", "needs", "cashed", "dead"]}}}},
    "fantasy_live_starters": {"type": "array", "items": {"type": "string"}},
    "boosted": {"type": "boolean"},
    "labels": {"type": "array", "items": {"type": "string"}, "description": "what the phone shows, e.g. 'your bet: WSH -3.5 · needs 2'"}
  }
}
```

### 6.8 GameJournal (A9)
```json
{
  "$id": "GameJournal",
  "type": "object",
  "required": ["game_id", "updated_at", "facts", "summary"],
  "properties": {
    "game_id": {"type": "string"}, "updated_at": {"type": "number"},
    "facts": {"type": "array", "maxItems": 40, "items": {
      "type": "object", "required": ["t", "kind", "text"],
      "properties": {
        "t": {"type": "number"},
        "kind": {"type": "string", "enum": ["score", "lead_change", "run", "comeback", "big_play", "injury", "ejection", "milestone", "stoppage", "we_left", "we_returned", "we_missed", "stake"]},
        "text": {"type": "string", "maxLength": 90},
        "period": {"type": ["string", "null"]}, "clock": {"type": ["string", "null"]}
      }}},
    "summary": {"type": "string", "maxLength": 160, "description": "LLM, 1-2 lines; every name/number must appear in facts"},
    "stake_line": {"type": "string", "maxLength": 80},
    "missed_big": {"type": "array", "items": {"type": "string"}},
    "watched_s": {"type": "number"},
    "summary_source": {"type": "string", "enum": ["llm", "template"]},
    "summary_at": {"type": "number"}
  }
}
```

### 6.9 ReasonerView (A10; the architecture's contract, expectations numeric)
```json
{
  "$id": "ReasonerView",
  "type": "object",
  "required": ["game_id", "at", "ttl_s", "lock_in", "lock_reason", "value_bonus", "storyline", "expectations"],
  "properties": {
    "game_id": {"type": "string"}, "at": {"type": "number"}, "ttl_s": {"type": "integer", "default": 60},
    "lock_in": {"type": "string", "enum": ["none", "watch", "must_see"]},
    "lock_reason": {"type": "string", "maxLength": 100},
    "value_bonus": {"type": "number", "minimum": 0, "maximum": 20},
    "storyline": {"type": "string", "maxLength": 100},
    "expectations": {"type": "array", "maxItems": 4, "items": {
      "type": "object", "required": ["what", "within_s", "p"],
      "properties": {"what": {"type": "string"}, "within_s": {"type": "integer"}, "p": {"type": "number"}}}},
    "safe_to_leave": {"type": "string", "enum": ["yes", "no"]},
    "abstain": {"type": "boolean"}, "source": {"type": "string", "enum": ["head", "model", "rules"]}
  }
}
```

### 6.10 JumpOrStay (B1; the student keeps decision/reel_s/why first, in order)
```json
{
  "$id": "JumpOrStay",
  "type": "object",
  "required": ["decision", "reel_s", "why"],
  "properties": {
    "decision": {"type": "string", "enum": ["jump", "stay"]},
    "reel_s": {"type": "integer", "minimum": 0, "maximum": 120},
    "why": {"type": "string", "maxLength": 140},
    "budget_s": {"type": "number", "description": "safe_entry_s - trip there - trip back - margin"},
    "ev_jump": {"type": "number"}, "ev_stay": {"type": "number"},
    "rule": {"type": "string", "enum": ["jump", "too_short_predicted", "never_mid_play", "crunch_first", "your_team_late", "must_see", "nfl_drive_first", "big_right_after", "not_worth_it", "abstain"]},
    "return_by": {"type": ["number", "null"], "description": "feed time we must be back on the home game"},
    "confidence": {"type": "number"}, "abstain": {"type": "boolean"}
  }
}
```

### 6.11 ReelPlan (B2)
```json
{
  "$id": "ReelPlan",
  "type": "object",
  "required": ["built_at", "total_s", "clips", "expected_value"],
  "properties": {
    "built_at": {"type": "number"}, "total_s": {"type": "number", "minimum": 30, "maximum": 60},
    "expected_value": {"type": "number"},
    "clips": {"type": "array", "minItems": 5, "items": {
      "type": "object", "required": ["id", "game_id", "league", "order", "seg_s", "value", "reason"],
      "properties": {
        "id": {"type": "string"}, "game_id": {"type": "string"}, "league": {"type": "string"},
        "headline": {"type": "string"}, "play_at": {"type": ["number", "null"]},
        "order": {"type": "integer"}, "seg_s": {"type": "number", "minimum": 5, "maximum": 11},
        "value": {"type": "number"},
        "value_terms": {"type": "object", "properties": {"importance": {"type": "number"}, "wp_delta": {"type": "number"}, "crunch": {"type": "boolean"}, "mine": {"type": "boolean"}, "bet": {"type": "boolean"}, "freshness": {"type": "number"}, "league_weight": {"type": "number"}}},
        "reason": {"type": "string", "maxLength": 80},
        "drop_order": {"type": "integer", "description": "which clip goes first if the window shrinks (1 = first to drop)"}
      }}},
    "seen_rule": {"type": "string", "description": "how 'seen' was decided: screen log from snap to whistle"}
  }
}
```

### 6.12 Preload (B3)
```json
{
  "$id": "Preload",
  "type": "object",
  "required": ["at", "target", "class", "p_next_180", "why"],
  "properties": {
    "at": {"type": "number"},
    "target": {"type": "string", "description": "tuner target, e.g. youtubetv:espn or prime:<event>"},
    "game_id": {"type": ["string", "null"]},
    "class": {"type": "string", "enum": ["next", "home", "replay", "break", "moment", "best"]},
    "p_next_180": {"type": "number", "description": "P(the decider switches to this target within 180 s)"},
    "saves_s": {"type": "number", "description": "tune cost avoided if the guess is right"},
    "hold_until": {"type": "number"},
    "back_s": {"type": ["number", "null"], "description": "replay class: rewind from the live edge"},
    "why": {"type": "string", "maxLength": 100},
    "alternatives": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {"target": {"type": "string"}, "p_next_180": {"type": "number"}}}}
  }
}
```

### 6.13 Explanation (B5)
```json
{
  "$id": "Explanation",
  "type": "object",
  "required": ["switch_id", "at", "verb", "from", "to", "line", "terms"],
  "properties": {
    "switch_id": {"type": "string"}, "at": {"type": "number"},
    "verb": {"type": "string", "enum": ["stay", "switch", "visit", "reel", "replay", "return"]},
    "from": {"type": ["string", "null"]}, "to": {"type": ["string", "null"]},
    "line": {"type": "string", "maxLength": 140, "description": "phone text; every number/name must be in terms or the journals"},
    "terms": {"type": "object", "properties": {"ev": {"type": "number"}, "ev_stay": {"type": "number"}, "rate": {"type": "number"}, "friction": {"type": "number"}, "stay": {"type": "number"}, "guard": {"type": ["string", "null"]}}},
    "gate": {"type": "string", "description": "why it was safe: 'dead ball on screen, 38 s certified'"},
    "counterfactual": {"type": "string", "maxLength": 100},
    "source": {"type": "string", "enum": ["template", "llm"]}
  }
}
```

### 6.14 Watchdog: Obs v2, FaultReport, Verdict v2 (C1, C2)
```json
{
  "$id": "WatchdogObs",
  "type": "object",
  "required": ["api_ok", "api_fail_streak", "log_age_s", "live_games", "crunch_on_screen", "sticks_offline", "offline_min", "away", "route_issue", "disk_free_mb", "restarts_last_hour", "errors"],
  "properties": {
    "api_ok": {"type": "boolean"}, "api_fail_streak": {"type": "integer"}, "log_age_s": {"type": "number"},
    "live_games": {"type": "integer"}, "crunch_on_screen": {"type": "boolean"},
    "sticks_offline": {"type": "array", "items": {"type": "string"}}, "offline_min": {"type": "number"},
    "away": {"type": "boolean"}, "route_issue": {"type": "string"},
    "disk_free_mb": {"type": "number"}, "restarts_last_hour": {"type": "integer"},
    "errors": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {"sig": {"type": "string", "maxLength": 160}, "n": {"type": "integer"}}}},
    "feed_stale_s": {"type": "object", "additionalProperties": {"type": "number"}, "description": "per source: espn_poll, fastcast"},
    "sync": {"type": "object", "properties": {"sigma_s": {"type": "number"}, "uncertain": {"type": "boolean"}, "drift_s_per_h": {"type": "number"}}},
    "llm": {"type": "object", "properties": {"loaded": {"type": "boolean"}, "rss_mb": {"type": "number"}, "peak_rss_mb": {"type": "number"}, "ms_last": {"type": "number"}}},
    "hidden_stick": {"type": "string", "enum": ["playing", "paused", "prompt", "frozen", "black", "home", "unknown"]},
    "on_screen_stick": {"type": "string", "enum": ["playing", "paused", "prompt", "frozen", "black", "home", "unknown"]},
    "tv_input_ok": {"type": ["boolean", "null"]},
    "last_switch": {"type": "object", "properties": {"ok": {"type": "boolean"}, "cost_s": {"type": "number"}, "age_s": {"type": "number"}}},
    "tick_overruns": {"type": "integer"}, "governor": {"type": "string", "enum": ["ok", "warm", "hot", "critical"]}
  }
}
```
```json
{
  "$id": "FaultReport",
  "type": "object",
  "required": ["at", "faults"],
  "properties": {
    "at": {"type": "number"},
    "faults": {"type": "array", "items": {
      "type": "object", "required": ["code", "kind", "severity", "component", "detail", "since", "count"],
      "properties": {
        "code": {"type": "string"},
        "kind": {"type": "string", "enum": ["core_loop", "component", "stick_link", "storage", "person", "upstream", "network", "cosmetic", "resource", "permission", "prompt", "paused", "frozen", "sync_drift", "feed_stale", "tick_overrun", "hidden_stick_dead", "tv_input"]},
        "severity": {"type": "string", "enum": ["info", "warn", "error", "critical"]},
        "component": {"type": "string"}, "detail": {"type": "string", "maxLength": 200},
        "since": {"type": "number"}, "count": {"type": "integer"},
        "kind_source": {"type": "string", "enum": ["rule", "model"]}, "kind_conf": {"type": "number"}
      }}}
  }
}
```
```json
{
  "$id": "WatchdogVerdict",
  "type": "object",
  "required": ["action", "target", "why"],
  "properties": {
    "action": {"type": "string", "enum": ["none", "notify", "restart_service", "reconnect_stick", "prune_disk", "reload_app", "dismiss_prompt", "resync", "retune_hidden", "drop_model", "pause_autopilot", "ask_user"]},
    "target": {"type": "string", "description": "stick host, channel target, app name, or ''"},
    "why": {"type": "string", "maxLength": 200},
    "source": {"type": "string", "enum": ["rule", "model", "guard", "governor"]},
    "defer_until": {"type": ["number", "null"], "description": "crunch on screen: act after it"},
    "expect_cleared_in_checks": {"type": "integer", "default": 2},
    "confidence": {"type": "number"}
  }
}
```

### 6.15 NightReport (C3)
```json
{
  "$id": "NightReport",
  "type": "object",
  "required": ["night", "gates", "misses", "top_3"],
  "properties": {
    "night": {"type": "string", "pattern": "^[0-9]{8}$"},
    "gates": {"type": "array", "items": {"type": "object", "properties": {"gate": {"type": "string"}, "scope": {"type": "string"}, "status": {"type": "string", "enum": ["PASS", "FAIL", "PENDING"]}, "value": {"type": "string"}}}},
    "misses": {"type": "array", "items": {
      "type": "object", "required": ["kind", "game_id", "t", "probable_cause", "evidence"],
      "properties": {
        "kind": {"type": "string", "enum": ["mid_play_cut", "mid_play_entry", "dead_air", "big_missed", "crunch_missed", "reel_overrun", "sync_error", "switch_slow", "fault_unrecovered"]},
        "game_id": {"type": "string"}, "t": {"type": "number"},
        "probable_cause": {"type": "string", "maxLength": 160},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "suggested_fix": {"type": "string", "maxLength": 160},
        "owner": {"type": "string", "enum": ["DECISION", "MOMENT/BREAK", "LOCAL AI", "SWITCHING", "SYNC", "PLATFORM", "INTEGRATION"]}
      }}},
    "top_3": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
    "source": {"type": "string", "enum": ["rules", "llm", "claude"]}
  }
}
```

### 6.16 Teacher side: MomentRow, TeacherAnswer, GoldCase, HandbookEntry (D1–D3)
```json
{
  "$id": "MomentRow",
  "type": "object",
  "required": ["id", "league", "night", "split", "brief", "target", "y", "big_next", "crunch_next", "garbage", "crunch", "late", "mine", "head_p"],
  "properties": {
    "id": {"type": "string", "pattern": "^[a-z]+:[0-9]+:[0-9]+$"},
    "league": {"type": "string"}, "night": {"type": "string"}, "split": {"type": "string", "enum": ["train", "valid", "test", "locked"]},
    "brief": {"type": "string", "description": "exactly what the student sees live; no brain line until the brain is retrained before test_start"},
    "target": {"$ref": "LockIn"},
    "export_target": {"$ref": "LockIn"},
    "y": {"type": "integer"}, "big_next": {"type": "integer"}, "crunch_next": {"type": "integer"},
    "t_big": {"type": ["number", "null"]}, "t_break": {"type": ["number", "null"]}, "next_text": {"type": "string"},
    "garbage": {"type": "boolean"}, "crunch": {"type": "boolean"}, "late": {"type": "boolean"}, "mine": {"type": "boolean"},
    "head_p": {"type": "number"}, "hard": {"type": "number"}, "gold": {"type": "boolean"},
    "jump": {"type": ["object", "null"], "description": "dead-ball rows only: type, p10_s (-> entry_q10 once A2 ships), window_s, clips[], text, label, hard"}
  }
}
```
```json
{
  "$id": "TeacherAnswer",
  "type": "object",
  "required": ["id", "task", "model", "text"],
  "properties": {
    "id": {"type": "string", "description": "<row id>|blind | <row id>|grounded | <row id>|jump | wd|<kind>|<j>"},
    "task": {"type": "string", "enum": ["reasoner_blind", "reasoner_grounded", "jump_blind", "wd_paraphrase", "wd_classify", "journal_grounded", "explain_grounded"]},
    "model": {"type": "string"}, "text": {"type": "string"},
    "kind": {"type": ["string", "null"]},
    "followups": {"type": "array", "items": {"type": "object", "properties": {"item": {"type": "string"}, "text": {"type": "string"}}}},
    "leak_check": {"type": "string", "enum": ["ok", "hindsight_phrase", "future_fact", "unparsed"]}
  }
}
```
```json
{
  "$id": "GoldCase",
  "type": "object",
  "required": ["id", "proposed", "rules_fired", "status"],
  "properties": {
    "id": {"type": "string"},
    "proposed": {"$ref": "LockIn"},
    "rules_fired": {"type": "array", "items": {"type": "string", "enum": ["garbage_never_must_see", "your_team_late", "nfl_drives_first", "nhl_power_play", "never_miss_big"]}},
    "teacher_opinion": {"type": ["object", "null"]},
    "hardness": {"type": "number"},
    "reviewer": {"type": "string", "enum": ["claude", "nick", "none"]},
    "status": {"type": "string", "enum": ["proposed", "accepted", "rejected", "rule_changed"]},
    "note": {"type": "string", "maxLength": 200}
  }
}
```
```json
{
  "$id": "HandbookEntry",
  "type": "object",
  "required": ["league", "season", "kind", "trigger", "len_q", "restart_cue", "source"],
  "properties": {
    "league": {"type": "string"}, "season": {"type": "string"}, "broadcaster": {"type": ["string", "null"]},
    "kind": {"type": "string", "description": "dead-window kind (cuts.after_kind) or break kind (breaks.LEN key)"},
    "trigger": {"type": "string", "maxLength": 200, "description": "rule text, e.g. 'first stoppage after 6/10/14 min elapsed, not after a goal/icing/on a PP/last 30 s P1-2/last 2:00 P3'"},
    "exclusions": {"type": "array", "items": {"type": "string"}},
    "len_q": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "floor_s": {"type": "number"},
    "restart_cue": {"type": "string"},
    "measured_n": {"type": "integer"}, "measured_on": {"type": "string"},
    "drift_pct": {"type": ["number", "null"], "description": "nightly: measured median vs this entry"},
    "source": {"type": "string", "description": "rulebook section, broadcast research note, or archive measurement"}
  }
}
```

---

## 7. Notes for whoever builds this (findings from the read)

- The grading-time "I don't know" (`grade.py _abstain`) has no runtime counterpart: `Reasoner.read`
  uses the head's level and ignores `confidence`. Upgrade #3 is mostly plumbing plus a per-league
  cut stored in the adapter's metadata.
- `jump.py` plans reels on a league-wide p10 per break type; the Brain v3 `entry10` head plus
  `brain3cal` scales are the drop-in replacement, and the EV should use `safe_entry_s`, not p10.
- `breaks.blend()` mixes rule and brain probabilities 50/50 in probability space; the live ECE of
  the blend (0.12–0.17) is far worse than either alone. Blend in log-odds with a weight fitted on
  valid nights, or let the brain own the forecast when the rules have no evidence.
- The NFL and NHL `key` heads are the weak spots (AUC 0.68 / 0.58). Both leagues have state the
  features barely see: NFL drive tempo and 4th-down odds, NHL PP time remaining and shot rate.
  Add features before adding model size.
- The student's `parse_answer` in `reasoner.py` uses a greedy `\{.*\}` regex; the teacher's
  `extract_json` is bracket-balanced and tolerates think blocks. Use one parser (the balanced one).
- `KIND_HELP` already names 13 fault kinds (including `prompt`, `paused`, `frozen` for the sticks)
  while `make_data.NOVEL` labels 6; the watchdog's data generator should cover all 13 before the
  next Kaggle round, and the action set should grow with them (C2).
- Switch costs differ between `default_config.yaml` (YTTV 3, Prime 8, flip 1) and
  `agent/kaggle/assets/eval_config.yaml` (5.5, 27, 7.5, flip 4). Jump labels and EV must use the
  measured `SwitchPaths` costs, not either constant.
- Everything above keeps the no-overfitting rules: time splits by whole night, locked ≥ 25 %,
  per-slice gates (crunch, your team, early/late, garbage, league, app), the train/valid/test gap
  in every report, leakage checks in CI.
