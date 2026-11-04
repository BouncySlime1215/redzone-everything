# Noggin master plan v2: the peak version

Wed Nov 4, 2026, ~6:20 PM. This replaces v1 (2026-11-04-noggin-master-plan.md).

It folds in everything from today:
- the attention-science review (22 mechanics)
- the data catalog (17.7B legal points, held)
- Watch Value's first win on locked nights
- the cut rules, game importance and playoff stakes (#163)
- the city map (merged)
- the 62 frontier ideas

**Training waits for this plan to be approved.**

---

## 0. Why this is the peak version (the short answer)

1. **The goal is right.** We optimize *suspense about things you care about, resolved soon*. That's the strongest-evidence driver of attention in the research (prediction error, anticipation, peak-end). It's math, not opinion: suspense S = E[(ΔWP)²], with no hand weights.
2. **The brain is shaped like the problem.** Choosing what to watch is a decision under uncertainty, delay and competing games. So we use a *world model* (what will happen), a *belief tracker* (where each game really is through the feed and TV delay) and a *planner* (what to watch, looking ahead like chess). That's the architecture behind the best game-playing AIs, upgraded for fog and randomness.
3. **It's sized to the data.** About 40M distinct real plays and 6-8k real slate-nights support a ~100-300M parameter world model. Bigger would memorize, not learn. Simulated nights stop helping past ~3× the real data, so we stop there.
4. **It's safe by construction.** Never joining mid-play gets a *mathematical bound* (conformal risk control) and an abstain option, not just testing.
5. **It improves itself.** Shadow mode every night, grading new brains on past nights before they go live, an adversary that invents hard nights, and agents that run experiments around the clock.
6. **It's measured on what matters.** The bar is "was the night worth it". A change that raises watch time while the night's worth drops is blocked, which keeps us out of the slot-machine trap that gets products sued.

**What would make it fail, and the guard against each:**
- **Bad win-probability inputs.** Calibrate win probability per league against betting markets.
- **A simulator that's wrong.** Real nights come first, and the realism filter rejects fake-looking practice nights.
- **Not enough GPU time.** Kaggle GPU + TPU now, and the TPU Research Cloud application.
- **Lanes drifting apart.** The city map and its checker keep them connected.

---

## 1. The architecture (final)

| Layer | Job | How it learns | Size / speed |
|---|---|---|---|
| **Goal: drama** | Care × suspense ranks where to go. Surprise ranks replays. "Worth it" is the bar | Math from win probability + market odds; care from Nick's teams/bets (preference multipliers) and game importance | Every second |
| **World model** | Predicts every game's next events, *when* they happen (point process), the next break, and crunch entry/exit, all with uncertainty | Self-supervised on ~40M real play events in one shared language across all sports, plus capped self-play | ~100-300M params, Kaggle-trained |
| **Belief tracker** | Where each game *really* is right now, given feed lag and TV delay | Particle filter per game, calibrated on our live nights | Every second |
| **Planner** | Two-level lookahead: which games over the next 15 min (macro), which dead ball to cut on (micro). Thinks harder when crunches collide | Offline RL on real nights first, then self-play against the hindsight oracle, an adversary and a league of rival planners | Cloud or Mac at ~1 Hz |
| **Presentation** | Catch-up order (peak-end, spoiler control), "meanwhile" moments, anticipation cues, open threads resolved within 3 min | Learned with the planner, from the playbook mechanics | |
| **Safety floor** | Never join mid-play (provable bound), never leave a big moment for a low one, crunch can't be stolen by a low-value game | Conformal risk control + formal checks | Always on |
| **Reflex** | The planner distilled to the Mac | Expert iteration | <5 ms, ≤2 GB total |
| **Mind (3B)** | Explains choices, edge cases, Nick's spoken preferences | Distilled from the Teacher | On the Mac, ≤2 GB |

---

## 2. The data (from the held catalog)

- **Wave 1, green (approve and go):**
  - nflverse play-by-play (1999+)
  - the open NBA play-by-play archive
  - NHL api-web play-by-play + shift charts
  - Retrosheet
  - MoneyPuck
  - Big Data Bowl tracking
  - Wikimedia pageviews (hype labels)
  - media-timeout rule tables
  - our live-night logs (gold)
- **Wave 2, yellow (Nick acts):**
  - the CollegeFootballData key
  - the ESPN-derived college/NBA mirrors (decide)
  - attribution-required sets
- **Skipped:** Baseball Savant (terms unclear), and anything paid or that forbids scraping.
- **Free labels we derive ourselves:**
  - wall-clock gaps between plays → break labels
  - rulebook "break owed" countdowns
  - media-session bursts → dead-ball labels
  - betting-line jumps → news before the feeds
- **Totals:**
  - about 17.7B legal data points (~54 GB of the 215 GB Kaggle quota, with a Hugging Face backup)
  - **about 40M distinct real plays** (what actually teaches)
  - self-play capped at ~3× real

---

## 3. The experiment queue (how ~100 ideas get tested)

All ideas live in **docs/experiments.md**, one row each: hypothesis, North Star line, cost (GPU-h / agent-h), lane, status.

- **Ranking:** expected North Star gain ÷ cost, with Rule-#1 safety items first.
- **Every experiment runs through:**
  1. off-policy grading on past nights
  2. shadow mode on live nights
  3. the locked-night gate
  4. promotion
- **Nothing reaches the TV without passing:**
  - 0 mid-play entries
  - NBA/NHL crunch ≥ live
  - your teams pass
  - everything else ≥ live
  - **worth-it ≥ live**

**Top 10 to run first (cheapest, biggest gains):**
1. Suspense score S = E[ΔWP²] as the ranking goal (care × suspense).
2. Calibrate win probability per league against market odds.
3. Point-process timing for breaks and scores, plus the break-owed countdown and free break labels.
4. Belief tracker for feed and TV delay.
5. Conformal never-join-mid-play bound with abstain.
6. Arrive at the dead ball *before* the predicted big play (anticipation).
7. Catch-up ordering: peak-end, spoilers hidden, within 3 min.
8. Learned crunch entry/exit with hysteresis.
9. Game importance (playoff race, matchup quality, NFL scarcity, postseason stakes from #163).
10. Shadow mode + off-policy grading (the machine that tests everything else).

---

## 4. The phases and ETAs

These assume $0, Kaggle (30 GPU-h/week + TPU on your one account) and 5 lanes. Each phase is done only when its gate passes on locked nights.

| Phase | What | Gate | ETA |
|---|---|---|---|
| **0: Foundations** | Experiment queue; suspense goal + win-probability calibration; one event language; Wave 1 ingest; RC6 gold-data capture; V8 Mind (crunch + leak fix); shadow mode + off-policy grading | Suspense line on the scoreboard; the first event-token shards on Kaggle | **Fri Nov 6** |
| **1: Timing brain** | Point-process timing, break-owed countdown, belief tracker, conformal floor, learned crunch entry/exit, anticipation arrivals, catch-up ordering | 0 mid-play entries with a stated bound; live-or-replayed within 3 min beats live | **~Wed Nov 11** |
| **2: World model v1** | ~100M-param pretrain on event tokens (Kaggle GPU + TPU), retrieval of similar moments, planner v1 (search + offline RL), value of information, adversary nights | Planner beats the hand-built decider on % of oracle value in ≥2 leagues | **~Wed Nov 25** |
| **3: Beyond chess** | Stochastic search over the world model, rival-planner league, two-level planning, think harder in crunch, risk-sensitive policy, Reflex distillation, formal floor check | ≥95% of oracle value on big-plays-live per league; press-to-TV ≤3 s on hardware | **~mid-December** |
| **4: Self-improving** | Nightly learning, AI-scientist lab, dream futures, learning from human producers, drift auto-recalibration | Every week beats the last on locked nights, automatically | **January playoffs / Super Bowl** |
| **5: Company** | Drama API, automated whip-around producer for rights holders, licensed video/audio, shared cloud brain, consented user data | First pilot | **Spring 2027** |

**Speed-ups:**
- A TPU Research Cloud approval moves Phases 2-3 up ~1-2 weeks.
- A clean data Wave 1 by Friday keeps everything on time.

**Biggest risk:** compute. Not ideas, and not data.

---

## 5. Decisions locked today (binding)
- **Cutting rules:**
  - Never join mid-play.
  - Leaving mid-play is OK at low leverage.
  - Down the stretch is sticky.
  - Crunch is a learned state with entry and exit.
  - A low-value game can never pull us out of crunch.
  - Crunch vs crunch is decided by value, with a buffer catch-up.
- **League order:** NFL = CFB > NBA > NHL, MLB. WSH/UNC are strong priors.
- **Promotion bar:**
  - Strict: entries 0, NBA/NHL crunch, your teams.
  - Everything else ≥ live.
  - Worth-it ≥ live.
- **Learning:** fully self-taught; Nick's buttons are controls, never labels. Manual boost is tier 0 (time-limited); bets are tier 1.
- **The line:** no betting hooks, engagement traps, fake hype, streaks or "come back" pings, and no watch-time objective. Keep loaded wording out of public docs.
- **Mac stays light:** training and data on Kaggle/HF only. Local models ≤2 GB.

## 6. What I need from Nick
1. **"go v2"**: approve this plan. It goes to the planners and Wave 1 data starts.
2. When you have 3 minutes: add the CollegeFootballData key as a Kaggle secret.
3. Optional: I draft the TPU Research Cloud application for you to submit.
