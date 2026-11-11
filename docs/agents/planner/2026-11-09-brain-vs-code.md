---
id: P-BRAINCODE
title: "The boundary: what is brain, what is code"
date: 2026-11-09
stream: planner
author: planner 2 (brain vs code)
status: draft
supersedes: []
sources: 16
verified: 12
affects: [redzone/decider/decider.py, redzone/decider/core.py, redzone/contracts/guard.py, redzone/engine.py, redzone/control.py, redzone/noggin/priority.py, redzone/noggin/pick.py, redzone/noggin/live.py, redzone/decider/cuts.py, scripts/fault_drill.py]
gate: []
---

# The boundary: what is brain, what is code

## Answer in 10 lines

1. **The brain outputs numbers and words. It never outputs an action.** Code turns numbers into cuts. Anything that moves a stick or the TV is code.
2. **Three classes.** Code: rules, facts, limits, recovery. Brain: value, suspense, words. Brain proposes and code vetoes: every choice among legal options. There is no fourth path to the TV.
3. **The line is the look-ahead.** Inside the 40-65 s the feed already knows, the future is a fact, so code schedules it. Past that edge the future is a guess, so the brain estimates its value.
4. **Shield in two places.** Before the brain: code hands it only legal options (pre-shield). At the flip: code re-checks with fresh data (post-shield). Both, because a flip can land up to 25 s or more after the decision.
5. **Rule #1 has no single gate today.** `HARD_RULES` and `core.guarded` are not on the live path; `Decider.decide` is 12 filter stages after `_decide`, and `cut_veto` branches per switch kind. PICK produced 10 mid-play cuts through a path that skipped the leave gate. Build the one gate first.
6. **Timing is not the constraint.** Forest inference measured here: 0.43 ms for 12 games, 1.05 ms p99 for 32. Real choice points are about 0.1-0.2 per second. An LLM on the cut path is still out: its seconds-long replies miss the 2.5 s flip.
7. **Brain fails, the show goes on.** Slow, missing or invalid brain output falls back to a code baseline in the same tick. Baseline is Planner 1's ladder.
8. **Where the brain earns its keep** is which 2-4 games to hold, collisions and Tier 3. PICK beat a hand rule by 11 points in a schedule replay. **Where it doesn't:** predicting goals (NHL Predict barely beats base rate), so those stay rewinds.
9. **The shield is only as good as its facts.** A shield protects relative to its model. Delay and dead-window errors are Rule #1's real risk, so sync work outranks brain work for Rule #1.
10. **Four disagreements with Planner 1** are in section 7: moment order, hold length, learning Nick's taste, and handing ladder steps 3-4 to the brain.

## The analysis

### 1. What the outside evidence says

| Pattern | What it says | What we take |
|---|---|---|
| Shielding ([Alshiekh 2017](#b1), [Koenighofer 2024](#b2)) | A shield either lists safe actions before the agent picks (pre-shield) or overwrites unsafe ones after (post-shield). The specification is independent of the learning objective. Fig. 3 and section 1 of the CACM survey. | Use both. The spec is Rule #1 and L1, kept apart from what the brain optimises. |
| Shield limit ([Koenighofer 2024](#b2), section 5) | "Shielding depends on a model." A shield cannot ensure an object is classified correctly by the vision system. | Our "vision system" is delay plus dead windows. That is the weak spot (risk 1). |
| No safe action ([Koenighofer 2024](#b2), section 1 and 4) | A shield cannot be computed if no safe action exists, and outside the safe region a predefined fallback must be defined. | "Stay" must always exist, and a fill ladder must exist for when staying is itself wrong (game ended). |
| Simplex and run-time assurance ([Black-Box Simplex](#b3), [Neural Simplex](#b4), [NASA](#b12), [Sha, snippet](#b13), [ASTM F3269, snippet](#b14)) | An unverified high-performance controller runs beside a simple baseline, and a decision module switches between them. Black-Box Simplex replaces proof of the baseline with runtime checks. Neural Simplex covers returning control to the learner. | Today's decider or Planner 1's ladder is the baseline. Fail over per tick, return per night. |
| RSS for self-driving ([Shalev-Shwartz 2017](#b5)) | A white-box safety model is separate from driving policy. The policy works in a semantic action space, with a learned Q-function scoring actions. Sensing is its own concern with its own error model. | Same three parts: Rule #1 (white box), option scoring (learned), sensing (delay, dead window). |
| Rules of ML ([Zinkevich](#b6)) | Launch without ML if you must, keep the first model simple, put a simple policy layer over a simple objective (Rule 13), save features at serving time (29), share training and serving code (32), measure skew (37). | Log every `BrainInput`. One feature definition (the repo's `features.context` pattern). |
| Recommenders ([Covington 2016](#b7)) | Candidate generation then a separate ranking model. | Code generates candidates, brain ranks. |
| Broadcast direction ([Chen 2013](#b8), [Chen 2018](#b9)) | Disney's assistant removes irrelevant cameras and ranks the rest, and the director still chooses. Learned camera selection exists but on a data-poor problem. | Nobody has replaced the director with a learned model in a published live system that I could find. Filter and rank is the proven shape. |
| Imitation ([Ross 2011](#b10)) and risk bounds ([Angelopoulos 2022](#b11)) | Sequential decisions break the i.i.d. assumption, and DAgger-style iteration handles it. Conformal methods bound the expected value of a monotone loss. | Matches [policy-learning](../../research/2026-11-08/policy-learning.md): imitate the oracle, shield it, conformal bounds on the safety numbers. |

I found no published study of learned switching across live games. [policy-learning](../../research/2026-11-08/policy-learning.md) reached the same conclusion. Everything above is borrowed from neighbouring fields.

### 2. What the repo already has, and the gaps

| Piece | State | Verdict |
|---|---|---|
| `noggin/priority.py` L1-L4 | Pure, tier tuple makes L1 unbreakable, 2,000-slate hostile property test. Flag off. | **Keep as the arbiter's core.** Best code in this area. |
| `MindRead` bounds | Capped at +-15 points, TTL 90 s, stale or NaN is ignored | Keep. This is the right shape for all soft brain output. |
| `decider/core.py` views, `ev`, `guarded`, `DecisionCore.decide` | Views and `ev` are used. **`core.decide` and `guarded` are not called anywhere in `redzone/`**, only in tests (`grep`, this worktree 29fdda48). | The designed shield is not the live shield. |
| `contracts/guard.py` `HARD_RULES`, `traced` | A named list with one test. Not enforced live. | Becomes the real gate's rule list. |
| `Decider.decide` | `_decide` then 12 stages: `_scenario`, `_clairvoyant`, `_crunch_guard`, `_pick`, `_hold_for_big`, `_time_entry`, `_dwell`, `_crunch_floor`, the owner hand-off, `_jump_owns`, `_restart_hold`, `_safe` | Proposers and vetoes are mixed. A learned proposer sits in the middle (`_pick`). |
| `engine.cut_veto` (engine.py:1957) and `control._exit_check` | Re-checks at the flip, but branches by `d.kind`. `_flip_to` checks `_exit_check` at 3210, 3233, 3274. | The right place, the wrong shape: the checks differ per path. |
| `buffered.cut_now` | Pure, shared with the sim, named reasons, margin in sigmas | Keep as the legal-window computation for the rotation. |
| `noggin/live.py` Timing v1 | Learned "surely still dead" bound; NFL restarts before bound 1.43% vs the fixed table's 9.1%, mean bound 6.3 s vs 13.0 s. Flag off. | A learned number inside a code rule, done right. Note the code table was the unsafe one. |
| `noggin/pick.py` PICK v1 | NBA only. 0.5 ms. Off, shadow off. | The first brain candidate. Needs the arbiter, not its current hook. |
| `reasoner/`, `scoring/brain.py`, `brain3.py`, `predict.py` | All off (`reasoner.enabled`, `brain.enabled`, `noggin.predict.leagues: []`) | Four value models, none live. The Mind planner should fold them into `GameValue`. |
| `fallback_ladder.py`, `scripts/fault_drill.py` | Ladder off by flag. Drill runs real Controller, Fetcher and Engine on fakes. | The drill gets brain faults added. |

**Why one gate matters, in the repo's own history.** Rule #1 keeps leaking through paths that have their own check:

- PICK `replace` mode: 10 mid-play cuts, because "a switch off an ended game skipped the leave gate" (noggin.md 11b).
- P1-REVIEW-01: NBA, NHL and MLB entries are not re-planned at the flip.
- P1-REVIEW-02: a single tap onto Prime joins without a landing check.
- P1-REVIEW-03: the default entry rewind ran on the visible screen.
- [safe-agents-realtime](../../research/2026-11-08/agents/safe-agents-realtime.md) already recommended a single commit gate at `_flip_to`. `redzone/invariants.py` does not exist yet.

Adding more learned proposers to this structure adds more paths. The gate comes first.

### 3. The look-ahead line (measured here)

With both sticks `B` seconds behind the live edge, the screen runs `mu = 34.9 + B` behind real life. The feed knows a play `lag` seconds after it happens. So the look-ahead is `L = mu - lag`.

Feed lag from `redzone/lab/feed_lag_profile.json` (Nov 2-3 2026, polled ESPN):

| League | n | p50 | p90 | L at B=75, p50 | L at B=75, p90 |
|---|---|---|---|---|---|
| NFL | 150 | 44.0 | 62.5 | 66 | 47 |
| NBA | 1,952 | 45.4 | 69.7 | 64 | 40 |
| NHL | 692 | 50.2 | 71.3 | 60 | 39 |
| CBB | 846 | 36.3 | 45.2 | 74 | 65 |

`buffered.py`'s docstring says the feed runs "10-30 s behind real life". The measured profile says 36-50 s at the median. I use the measured numbers. NFL's n is small and the sample is two days (risk 1).

**A Tier 1 is reachable on facts alone** if its join point is known before the stick has to start: `(event - join point) <= L - prep - 2 s`. Dev nights only, read from `data/archive` (locked and fresh excluded). Script (not in the repo): `/private/tmp/claude-501/-Users-nick-matta-Library-Application-Support-Claude-scratch-workspaces-9fd867a4-1593-493b-b94f-1f3aebd68493-688aed4e-2f6f-4b8a-9ed9-aa097c310a36-scratch-2026-11-08-ce8f23/9f563e62-788a-4e8b-a325-b193c8b21949/scratchpad/boundary/reach.py`.

| Tier 1 and join point (n) | Flip only 2.5 s | Preloaded 6.7 s | Fresh YouTube TV 17.5 s | Prime 49.5 s | Today 60 s |
|---|---|---|---|---|---|
| NFL TD, join just before the snap (1,073) | 100 / 100 | 100 / 100 | 100 / 100 | 100 / 0 | 100 / 0 |
| NBA clutch make, last shot or dead ball (148) | 91 / 81 | 91 / 76 | 85 / 57 | 26 / 0 | 1 / 0 |
| NBA clutch make, dead ball only (148) | 45 / 17 | 40 / 10 | 27 / 3 | 1 / 0 | 0 / 0 |
| NHL goal, last faceoff (423) | 52 / 38 | 49 / 33 | 41 / 20 | 6 / 0 | 0 / 0 |

Each cell is % reachable at B=75, feed lag p50 / p90. These are ceilings. They assume one game at a time, one free hidden stick and no collisions. The NFL row is a step function because ESPN's NFL wallclock is the snap.

What this says:

- **NFL Tier 1s need no prediction to reach, if prep gets under about 45 s.** The brain is not what makes touchdowns visible. Stick speed is. Today's 60 s fails at feed-lag p90.
- **NBA with Nick's shot-or-dead bar** is mostly reachable on facts at fresh-tune speed. With a dead-ball-only bar it isn't. Nick's join bar (PLAN section 3) is worth 85% vs 27% at p50.
- **NHL can't be reached this way.** About half of goals are out of reach even with a flip-only stick at p50. The brain does not rescue this, because NHL Predict test logloss is 0.146 vs 0.148 baseline at 30 s and scorecards were unchanged by it (noggin.md section 10). Those goals are rewinds, and that is a code rule.
- **Warning extends the window for football only.** 59% of NFL TDs (631 of 1,073) have an earlier red-zone snap on the same drive, 46% at least 60 s earlier, median 121 s for those with warning. A rule gives that warning at zero cost. The other 41% have none.

So:

- **Inside L, outcomes are facts.** Choosing among known moments under stick limits is scheduling. A receding-horizon solver (the live version of `lab/oracle.py`) does it, with no training.
- **Past L, you need an estimate.** How much is Game C worth in the next three minutes? That is the brain's job. It enters the solver as a terminal value, the same shape as RSS's learned Q-function over a semantic action space.

### 4. Where brains have actually paid off here

| Evidence | Result | Reading |
|---|---|---|
| PICK v1, NBA, 377 test nights, schedule replay | Big live 91.3 vs hand rule 80.1 (oracle 96.3). Dead air 16.7 vs 21.3. | Real signal for "which game" |
| PICK through the app, `breaks` mode | Big live 77.5 → 77.5 (dev), 68.8 → 68.8 (locked), crunch live locked 84.9 → 81.0 | The value did not survive contact with the mechanics. Missed the gate. |
| NBA Predict as a score term | Big live unchanged, AUC about 0.59, "the NBA sim switches about 1.2 times an hour, so a pick-time term has little to act on" | Better predictions don't matter while cuts are scarce |
| Timing v1 NFL | Bound failures 1.43% vs 9.1% | The clearest win, and it sits inside a code rule |
| NFL oracle gap (northstar.md) | Big live ceiling 91.2 vs app 40.3 | Plenty to win, but stick mechanics come first |

Order follows from this: stick speed (queue planner), then the gate, then the brain. A brain graded before the sticks can act will look useless, as PICK and Predict did. Re-grade them after the queue work lands.

### 5. The interface

New file `redzone/contracts/brain.py`. All frozen dataclasses. The brain is a pure function: `think(BrainInput) -> BrainOutput`. No clock reads, no randomness, no I/O, no stick handles.

```python
@dataclass(frozen=True)
class Option:                 # built by CODE, only legal ones
    id: str
    verb: str                 # stay | cut | rewind | reel | fill
    game_id: str | None
    moment_id: str | None
    l1_class: int             # 0 your team, 1 crunch, 2 open (code computes; brain reads only)
    earliest_s: float         # flip may start this many seconds from now
    latest_s: float           # ...and no later
    needs_prep_s: float       # 0 if parked and ready
    ends_on: str              # what ends this scene: "play_end", "setup_resolved", "clip_end"

@dataclass(frozen=True)
class BrainInput:
    schema: int
    tick: int
    now: float                # passed in, so replay is exact
    lookahead_s: dict[str, float]   # L per game: how far the feed covers the screen
    games: tuple[GameFacts, ...]    # score, clock, period, tier-events known so far, per game
    moments: tuple[Moment, ...]     # known Tier 1/2/3 not yet aired, with air_at, is_fact
    options: tuple[Option, ...]
    screen: ScreenFacts       # current scene, plays seen, seconds on this game, last 10 scenes
    hidden: tuple[StickFacts, ...]  # game, ready or not, seconds to ready
    prefs: Preferences        # L2, read only
    unseen: tuple[Moment, ...]      # catch-up debt

@dataclass(frozen=True)
class GameValue:
    game_id: str
    value_now: float          # 0-100, the scorer's scale (core.MomentView.value)
    value_next_3m: float      # 0-100: the terminal value past the look-ahead
    p_tier1_60s: float        # calibrated, 0-1
    why: str                  # short, for phone and trace

@dataclass(frozen=True)
class BrainOutput:
    schema: int
    tick: int                 # must echo BrainInput.tick
    model_id: str
    made_at: float
    ttl_s: float              # Reflex 5 s, Mind 90 s
    scores: Mapping[str, float]      # option id -> score. Unknown ids are dropped
    values: tuple[GameValue, ...]
    preload: tuple[str, ...]         # game ids, best first
    stay_one_more: float | None      # 0-1, read only at a dead ball on the current game
```

**The brain sees:** facts up to the feed head, code's list of legal options, the look-ahead, stick readiness, Nick's L2 settings. **It never sees:** his taps, thumbs or overrides (self-taught rule, decisions 2026-11-04 10:55 AM), locked nights in training, any handle to a stick or TV.

**Arbiter (code, about 40 lines, pure):**

```python
def choose(inp, out, baseline):
    pool = lowest_l1_class(inp.options)     # L1 first. The brain cannot cross it
    pool = must_show_floor(pool, inp)       # an unseen or about-to-air Tier 1 outranks the rest of its class
    if not valid(out, inp): return baseline(pool)   # schema, tick, TTL, finite, ids known
    best = max(pool, key=lambda o: (out.scores.get(o.id, -inf), o.id))
    if best.verb != "stay" and gain(best, pool, out) < margin: best = stay(pool)
    return best
```

**The shield, in order:**

| Step | Runs | Does | Today |
|---|---|---|---|
| S1 Facts | every tick | `DeadTracker`, delay ± sigma, feed freshness | exists |
| S2 Pre-shield: `legal_options()` | every tick | Rule #1 windows, leverage floor, L1 banned apps, dwell, 60 s anti-ping-pong, user pause and lock, feed stale, stick ready, must-see list | spread over 12 stages |
| S3 Arbiter | every tick | `choose` above | `noggin/wire.py` is a start |
| S4 Post-shield: `commit_gate()` | at the flip, polled until it goes | one function, same for every non-tap kind, fresh data, named veto | `cut_veto` by kind |
| S5 Audit | every decision, and nightly replay | `priority.violations()`, every veto logged with its reason | `jumps.jsonl` partly |

A second tap by Nick stays outside the shield. That's his call and existing behaviour.

**Timing budget (8 GB Mac).**

| Item | Number | Source |
|---|---|---|
| Decision tick | 0.5 s, measured 0.52 s (1.91 ticks/s) | `engine.DECIDE_EVERY_S`, P1-BOTTLENECKS |
| Real choice points | about 0.1/s for every play of 8 NFL games (59 per 10 min); 0.2/s allows for bigger slates (my allowance) | PLAN section 3 |
| Cuts at the target | 15 per 10 min = 0.025/s | P1-MAXRATE |
| PICK forest, 12 / 16 / 32 games, p50 | 0.43 / 0.53 / 0.91 ms (p99 0.58 / 0.59 / 1.05, max 4.3) | measured here today, load about 2, `nice 15`, 2,000 runs |
| Process RSS with numpy and that model | 46 MB | measured here |
| Timing v1 per open window | under 2 ms | `noggin/live.py` |
| Predict, 8 games × 3 horizons | 0.75 ms p50, 1.35 ms p99 | noggin.md |

Budgets (proposed):

- **Reflex on the event loop: p99 under 5 ms per tick (the existing noggin.md target), hard deadline 20 ms.** Anything slower runs in its own process. Today's forest leaves about 5x headroom to the target. CI enforces it.
- **Run on change, not on every tick.** New play, option set changed, or stick state changed. Refresh at most each tick, TTL 5 s.
- **Mind: separate process, 30-60 s, TTL 90 s.** Bounded like `MindRead`. Never on the cut path.
- **Deadlines by decision:** preload needs `prep` seconds (4-60 s today), cut needs flip plus lead (2.5-3.2 s plus 2-8 s), commit gate runs up to the flip. noggin.md's own Mind budget allows up to 5 s per call, which is past the flip. Published speeds for this class on M-series chips conflict (snippet-only, [b15](#b15), [b16](#b16)), so measure before any claim.
- **Memory:** CLAUDE.md says local AI ≤ ~2 GB, `noggin.md` says ~2.5 GB, governor `models_mb` says 1500. One source claims a 4-bit Llama 3.2 3B peaks near 3 GB (snippet-only). The 3-4B Mind target may not fit. Mind planner to settle with a footprint measurement, not RSS (home-reliability).

**Fallbacks.**

| Fault | Detected by | Falls back to |
|---|---|---|
| Brain slow past 20 ms, or missed its tick | deadline in the caller | last output if inside TTL, else baseline |
| Brain missing, crash, bad model file | load or call error | baseline for the night, one phone line |
| Invalid output (NaN, wrong tick, unknown id, wrong schema) | `valid()` | that option or the whole output is dropped; baseline |
| Brain confidently wrong | veto rate of its top pick, switches per hour vs baseline, Tier 1 debt age | tripwire: baseline until the next night. Thresholds set in shadow. No live retraining |
| Mind late or gone | TTL | weights go to neutral (existing `mind_points`) |
| Feed stale | `feeds.stale` | no cut, S2 drops it |
| Delay doubt too large | sigma | window shrinks to nothing, stays. Visible as a named hold |
| Stick fails | `control`, `fallback_ladder` | buffered → live → other stick → hold |
| Memory pressure | governor | Mind off first, Reflex last |
| No legal option at all | S2 returns empty | stay. If the game ended, the fill ladder |

Baseline first: today's rule pick, then Planner 1's ladder once it exists. It must be good enough to watch, because brain-off is a real night.

**Test each side alone.**

| Side | Test | Pass |
|---|---|---|
| Shield | `RandomBrain`, `AdversarialBrain` (scores the worst legal-looking option highest, NaN, huge values, wrong ids) through replay nights on the real decider with fake sticks | mid-play entries 0, high-leverage leaves 0, L1 violations 0, with **any** brain |
| Shield | mutation test: disable each S2 and S4 rule | a test fails per rule |
| Shield | `fault_drill.py` adds `brain_slow`, `brain_hung`, `brain_nan`, `brain_stale`, `brain_missing` | no stall over 1 tick, baseline takes over |
| Brain | pure function: same input, same output; no clock, no I/O | CI |
| Brain | `pct_of_oracle` on forward-chaining whole-night folds, mean and worst fold; ECE ≤ 0.10 on probabilities | existing gates, noggin.md section 4 |
| Brain | skew: replay logged `BrainInput`s and compare outputs | identical |
| Brain | latency p99 per tick at 40 games | CI budget |
| Together | shadow next to live, promoted after 7 of 7 paired nights (or 10 of 12), then locked once plus fresh nights | existing gates |

### 6. Disagreements and additions for the other planners

**Queue planner.** Prep under L is the number that matters: under about 45 s makes NFL reachable on facts at p90. Under 15 s helps NBA. A known Tier 1 or 2 inside L always beats the brain's `preload` preference. When nothing is known, the brain's `preload` ranking chooses, with `standby.Planner` as the fallback.

**Mind planner.** Own `GameValue`, `value_next_3m` and `stay_one_more`. Fold in the four value models. PICK's label is the oracle's next game, so it imitates big-play maximisation (spoiler risk below). The oracle objective must count stops and failed 4th downs.

**Risks planner.** Take the risks table below. Most important: shield model correctness and the training objective.

### 7. Disagreements with Planner 1

| Planner 1 said | I recommend | Why |
|---|---|---|
| "Which of several moments next" is BRAIN | **Split.** Value is brain, order inside L is code (solver). | Inside L it is scheduling over facts, and `lab/oracle.py` is already that kind of solver. How close a version limited to L gets is unmeasured (step 7). |
| "How long to hold" is BRAIN inside caps | **Not a duration.** Code ends scenes (play end, setup resolved). The brain answers "one more play?" at each dead ball. | Follows Planner 1's own rule that a scene ends when its play ends. Event-driven, cheap to test. |
| Relevance filter "learns his taste"; moments by "Nick's taste" | **Not allowed today.** Hand-set L2 only. | Self-taught rule, Nov 4 10:55 AM. Decision 2 asks Nick to confirm. |
| Step 8: brain replaces ladder steps 3-7 | **Steps 3-4 stay code**, as a must-show floor. Brain replaces 5-7 and orders inside 3-4 on collisions. | Every touchdown airing is a product promise. PICK missed 20 of 1,183 your-team plays even in replay. |

Agreed without change: must-see list, detectors, Rule #1 gates, scene caps, slate mode, stick mechanics, fill ladder.

One addition: Planner 1's decision 1 (moment tier before league order) needs `Ranked.key` in `priority.py` changed, since it has no moment tier today. Code, about 1 hour.

## BRAIN vs CODE

Class: **C** code, **C+n** code rule with a learned calibrated number and a code fallback (authority is still code), **B** brain (numbers or words only), **P/V** brain proposes, code vetoes.

| Decision | Class | Who does what | Evidence and owner |
|---|---|---|---|
| Must-see list, Tier 1/2/3 per league | C | Config, one list | Planner 1. Also the brain's label source |
| Event detectors | C | Parse feeds | Planner 1 |
| What counts as dead, feed covers it | C | Fact from play-by-play | `cuts.DeadTracker` |
| What counts as dead, open window | C+n | Rule is code. Number is Timing v1, table fallback | NFL 1.43% vs 9.1% failures |
| Delay and sync | C+n | Kalman or particle estimate | `decider/sync.py`, `noggin/belief.py` |
| Rule #1 join and leave | C | S2 and S4 | CLAUDE.md, shield evidence |
| When to cut, exact moment | C | Earliest legal point the solver picks | `buffered.cut_now`. 0.1-0.2 choices per second need no ML |
| Which game, inside L1 and past L | P/V | Brain scores legal options, arbiter chooses | PICK +11 points in replay |
| Which game, your team and crunch tiers | C | L1 tuple | `priority.py` |
| Your teams | C | Config only | L1, hostile property test |
| Crunch: is it crunch | C | One table | `crunch.py` |
| Crunch: two games at once | C | Alternate at dead balls, league order | Planner 1 |
| Must-show floor (every Tier 1 airs live or rewound) | C | Debt queue with 2-3 min deadline | Nick's replay window, noggin.md |
| Order of colliding Tier 1s | P/V | Brain scores, floor still applies | 42% NFL collisions (Planner 1) |
| Tier 2 vs Tier 3 fill | P/V | Brain scores | Planner 1 |
| Scene end | C | Play end or setup resolved | Planner 1's cut rule |
| Stay one more play | P/V | `stay_one_more` at dead balls, inside caps | Event-driven |
| Anti-ping-pong, minimum one play, dwell | C | S2 | Limits |
| Replay or live | C | Live if the join is reachable, else rewind | Reach table |
| Is a rewind worth the slot | P/V | Value; code enforces the 2-3 min rule | noggin.md |
| Catch-up reel: eligible clips, length, freshness | C | 30-60 s, 5+ clips, age limit | Planner 1, `reel.py` |
| Catch-up reel: order and which clips | P/V | Brain ranks, tier order is the fallback | Planner 1 |
| What to preload, known moment inside L | C | Reach rule | Section 3 |
| What to preload, nothing known | P/V | Brain `preload`, `standby.Planner` fallback | Queue planner |
| Slate mode, relevance filter on big nights | C, then P/V | Table; brain may re-rank inside it | Planner 1 |
| Suspense, storylines, stakes, value past L | B | `GameValue`, capped into score | leverage-excitement, `MindRead` |
| Why-text on the phone | B | Words only | reasoner model |
| Fill ladder | C | Fixed order | Planner 1 |
| Fault recovery in the night | C | Ladder, watchdog, governor | `fallback_ladder.py`, `fault_drill.py` |
| Diagnosing a fault | P/V | Mind may name a fix from a fixed allowlist; code executes or refuses. Off today (`hardening.watchdog`) | PLAN section 4, safe-agents-realtime |
| Training, labels, nightly review | B offline | Teacher, Kaggle only | noggin.md |

Rule of thumb, extending Planner 1's: **if it moves a stick, it's code. If it says what something is worth, it's the brain. If it picks between things code already allowed, it's both.**

## Major considerations and risks

| # | Risk | Cause | What to do |
|---|---|---|---|
| 1 | Shield correct, facts wrong | Shields depend on their model. Feed lag and delay feed every window. Lag profile is 2 days old with NFL n=150. | Re-measure feed lag per league each week. Add a live check of delay error. Sync work ranks above brain work for Rule #1 |
| 2 | Shield too tight | Every safety margin cuts rate. `margin_sigmas: 2` and stale-feed stays can starve the show. | Log vetoes by reason. Shadow compares veto cost in cuts per 10 min |
| 3 | Spoiler machine | Oracle labels favour plays that pay off. The brain learns to go only where something scores. | Oracle objective counts stops and failed 4th downs. Grade share of cuts that end in a score vs base rate |
| 4 | Baseline is weak | Simplex is only as good as its fallback. The current decider switches about 1.2 times an hour in NBA sim. | Build Planner 1's ladder as the baseline before the brain goes live |
| 5 | Mechanics hide the brain's value | PICK and Predict both looked useless through the app | Re-grade after the queue work lands |
| 6 | Chatter | PICK switched 4.2/h against the oracle's 1.4/h | Margin and dwell in S2, switches-per-hour tripwire |
| 7 | Train-serve skew | Feature code drifts. Delay is the known sim gap. | Log `BrainInput`, one feature definition, robust slices (3 s early or late, 10% drop) |
| 8 | Self-play leakage | The brain sees the future inside L. Fine live. Training must use the same L per night. | Features use `wall <= t - lag(p90)`, the existing PICK rule. CI leak check |
| 9 | Two brains in the loop | Reflex and Mind disagree or oscillate | Mind only moves bounded weights with 90 s TTL. It never picks |
| 10 | Refactor risk | 12 stages carry real fixes. A rewrite would re-open bugs. | Wrap, don't rewrite: add S4 first, move rules one at a time with the replay gate |

Not verified: the live decide-tick cost (no measurement of the whole tick), brain resident memory on a loaded night, any LLM speed on this Mac. Reach numbers are ceilings. NBA n=148 is small. Latency was measured once with load about 2.

## Decisions needed from Nick

1. **Brain size on the Mac: small and fast, with the billions offline?** Recommended yes. Reflex stays under about 100 MB, the Mind at or under 2 GB, the 32B Teacher on Kaggle. This answers PLAN section 7 question 2. The alternative is a larger model on the Mac, which risks memory pressure next to the live app and misses the flip deadline anyway.
2. **Keep the self-taught rule (nothing learned from your taps)?** Recommended yes, for now. Planner 1's "learns his taste" assumed otherwise. Your taste comes in only as settings you choose. The alternative is opt-in learning from a dedicated rating button, which would need a new label source and a new gate.
3. **May the live show ever try a slightly-worse-looking safe option, so the brain can learn from real nights?** Recommended no. The replay oracle already answers "what if", because a game doesn't react to what you watch. The alternative is rare random picks among near-ties, which makes some cuts slightly worse on purpose.

## Ordered steps

| # | Step | Hours (estimate) | Depends on |
|---|---|---|---|
| 1 | `commit_gate()`: one function at `_flip_to` for all non-tap kinds, names every veto. Close P1-REVIEW-01, -02, -03 through it | 6-8 | none |
| 2 | Replay test with `AdversarialBrain` and `RandomBrain`; mutation test per rule | 4-6 | 1 |
| 3 | `contracts/brain.py` and `valid()`, logged `BrainInput` | 3-4 | none |
| 4 | `legal_options()`: extract S2 from the 12 stages, one rule at a time, replay gate each | 10-14 | 1, 2 |
| 5 | Arbiter with today's decider as the baseline, flag off. Must be scorecard-identical with a null brain | 4-6 | 3, 4 |
| 6 | Must-show floor and moment tier in `priority.py` (after Nick's Planner 1 decision 1) | 2-3 | 5 |
| 7 | Inside-L solver from `lab/oracle.py`, flag off, graded against baseline | 8-12 | 4 |
| 8 | Brain faults in `fault_drill.py`, tripwire, latency CI | 3-4 | 5 |
| 9 | Re-grade PICK and Timing through the arbiter, after the queue work lands | 3-4, plus Kaggle | queue planner, 5 |
| 10 | Shadow on live nights, then locked once plus fresh nights | 7+ nights elapsed | 9 |

About 43-61 hours before shadow, all estimates. Nothing here blocks queue speed. Steps 1 and 2 protect Rule #1 and are worth doing regardless of any brain.

## Bibliography

Opened and read unless marked snippet-only. "Abstract" means only the abstract page was available or read.

1. <a id="b1"></a>Alshiekh, Bloem, Ehlers, Koenighofer, Niekum, Topcu, "Safe Reinforcement Learning via Shielding", 2017. https://arxiv.org/abs/1708.08611 (opened, abstract)
2. <a id="b2"></a>Koenighofer, Bloem, Jansen, Junges, Pranger, "Shields for Safe Reinforcement Learning", CACM, 2024 author version. https://pure.tugraz.at/ws/portalfiles/portal/93576031/Shields_for_Safe_Reinforcement_Learning.pdf (opened, pages 1-6 of 9)
3. <a id="b3"></a>Mehmood et al., "The Black-Box Simplex Architecture for Runtime Assurance of Autonomous CPS", 2021. https://arxiv.org/abs/2102.12981 (opened, abstract)
4. <a id="b4"></a>Phan et al., "Neural Simplex Architecture", 2019. https://arxiv.org/abs/1908.00528 (opened, abstract)
5. <a id="b5"></a>Shalev-Shwartz, Shammah, Shashua, "On a Formal Model of Safe and Scalable Self-driving Cars", 2017, rev. 2018. https://ar5iv.labs.arxiv.org/html/1708.06374 (opened, first 100,000 of 190,000 characters)
6. <a id="b6"></a>Zinkevich, "Rules of Machine Learning", Google, undated. https://developers.google.com/machine-learning/guides/rules-of-ml (opened)
7. <a id="b7"></a>Covington, Adams, Sargin, "Deep Neural Networks for YouTube Recommendations", RecSys 2016. https://research.google/pubs/pub45530 (opened, abstract)
8. <a id="b8"></a>Chen et al., "Computational Sports Broadcasting: Automated Director Assistance for Live Sports", ICME 2013, Disney Research. https://la.disneyresearch.com/publication/computational-sports-broadcasting-automated-director-assistance-for-live-sports/ (opened, abstract page)
9. <a id="b9"></a>Chen, Lu, Tian, Little, "Learning Sports Camera Selection from Internet Videos", 2018. https://arxiv.org/abs/1809.02854 (opened, abstract)
10. <a id="b10"></a>Ross, Gordon, Bagnell, "A Reduction of Imitation Learning and Structured Prediction to No-Regret Online Learning", AISTATS 2011. https://arxiv.org/abs/1011.0686 (opened, abstract)
11. <a id="b11"></a>Angelopoulos, Bates, Fisch, Lei, Schuster, "Conformal Risk Control", 2022, rev. 2025. https://arxiv.org/abs/2208.02814 (opened, abstract; assumptions not read)
12. <a id="b12"></a>Skoog, Hook, Ryan, "Leveraging ASTM Industry Standard F3269-17 for Providing Safe Operations of a Highly Autonomous Aircraft", NASA, 2020. https://ntrs.nasa.gov/citations/20200001756 (opened, record page only)
13. <a id="b13"></a>Sha, "Using Simplicity to Control Complexity", IEEE Software 18(4), 2001. Secondary descriptions only (snippet-only)
14. <a id="b14"></a>ASTM F3269-21, run-time assurance for aircraft. https://store.astm.org/f3269-21.html (snippet-only, paywalled)
15. <a id="b15"></a>"Best MLX Local LLMs on Apple Silicon in 2026". https://bestllmfor.com/guides/mlx-local-llm-apple-silicon/ (snippet-only; blog, conflicting numbers)
16. <a id="b16"></a>Personal blog on MLX, Nov 28 2024. https://personal-site-bfu.pages.dev/blog/post-2.html (snippet-only; blog, M2 Pro not base M2)
