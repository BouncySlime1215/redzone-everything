# The Noggin master plan: becoming THE sports AI

> **Superseded by [docs/PLAN.md](../../PLAN.md)**, the only plan. Kept as history and input.

Draft for Nick, Wed Nov 4, 2026, ~5:45 PM. **Nothing in here is sent to the project until Nick says go.**
The data catalog (about 20B data points, 54 GB) is held until the model is chosen.

---

## 1. The north star in one line

An AI that predicts every live game with honest uncertainty, plans what to watch like a chess engine
on probabilistic futures, decides when you learn things to maximize drama, never joins mid-play
(provably), and gets better every night while you sleep.

---

## 2. The architecture (beats "AlphaZero")

| Layer | What it does | Built from ideas |
|---|---|---|
| **Drama math (the goal)** | Suspense = how much a game could swing next; surprise = how much it just did. Computed every second from win odds and betting markets. No hand weights | 19-26 |
| **World model** | One brain for every sport: predicts the next events, *when* they happen, and the next break, with uncertainty | 2, 3, 9-18 |
| **Belief tracker** | Where each game really is right now, given feed lag and TV delay | 29, 44 |
| **Planner** | Searches scenario trees two levels deep: the next 15 min and the next dead ball. Thinks harder when crunches collide | 4, 27-36 |
| **Information design** | Decides what you learn and when: catch-up order, spoiler hiding | 1, 20, 32 |
| **Safety floor** | Never join mid-play, with a mathematical guarantee, and abstains when unsure | 6, 47, 48 |
| **Reflex** | The planner squeezed into a <5 ms model on the Mac | 38, 46 |
| **Mind (3B)** | Explains its choices and handles odd cases. Not the core timing | |
| **The lab** | An adversary invents nightmare nights, an AI scientist runs experiments, new brains are graded on old nights before going live | 5, 7, 8, 35-37, 50 |

---

## 3. All the ideas (58)

**Legend:**
- ⭐ = new for live sports viewing, as far as we know (to be verified by the research team before we claim it).
- 🟢 = can start this week.
- 🟡 = needs the world model first.
- 🔴 = company stage (licenses, users, money).

### The first 8 (frontier)
1. ⭐🟢 **Information design over what the viewer knows**: reveal order and spoiler control to maximize suspense.
2. 🟢 **Neural point processes** (Hawkes / transformer TPP): continuous-time prediction of every next event and break.
3. 🟡 **Diffusion "dream" world model**: sample thousands of plausible next-5-minute futures per game.
4. 🟡 **Test-time compute**: instant in quiet moments, deep search when crunches collide.
5. ⭐🟡 **Adversary that designs nightmare nights** (unsupervised environment design).
6. ⭐🟢 **Conformal risk control**: a provable bound on mid-play joins, with an abstain option.
7. 🟢 **Off-policy evaluation**: grade a new brain on old nights without running it live.
8. 🟢 **AI scientist runs the lab**: agents propose, train, grade and write up 24/7.

### World model and representation
9. 🟢 **One event language for every sport**: possession, stoppage, score and clock primitives shared across leagues.
10. 🟡 **JEPA-style latent world model**: predict in "meaning space", so it's robust to noisy or late feeds.
11. 🟡 **Mixture of experts**: a shared trunk, plus league experts and a dedicated crunch expert.
12. 🟡 **Multi-scale time**: play, possession or drive, quarter, and the whole night.
13. 🟡 **Graph networks over lineups**: who's on the floor changes clutch odds.
14. ⭐🟡 **Retrieval-augmented world model**: pull the 50 most similar historical moments ("this looks like Bills-Chiefs 2022") and reason from them.
15. 🟡 **Cross-sport transfer**: does football teach hockey crunch time? Measured, not assumed.
16. 🟡 **"Game DNA" embeddings**: each team and each game gets a style fingerprint (pace, volatility, comeback-proneness).
17. 🟢 **Calibrated uncertainty** (deep ensembles / evidential heads): it knows what it doesn't know.
18. 🟡 **Causal world model**: answers what-ifs ("if they call timeout here, what happens?").

### What counts as worth watching (reward)
19. ⭐🟢 **Suspense + surprise** from win odds and markets (Ely, Frankel and Kamenica), as THE reward.
20. ⭐🟢 **Spoiler-aware value**: a moment is worth less once you already know how it ended.
21. 🟡 **Rarity bonus**: records and first-evers, scored by how surprising the world model finds them.
22. 🟢 **Hindsight attention labels**: Wikipedia pageview and highlight-view spikes show, after the fact, what people cared about.
23. 🟡 **Inverse RL from human producers**: learn RedZone's and NBA CrunchTime's taste from their choices.
24. 🟡 **Multi-objective tuning**: drama vs dead air vs switch whiplash on one Pareto dial.
25. 🟢 **Whiplash cost**: too many cuts is learned as a cost, not hard-coded.
26. 🟡 **Train directly on regret vs the hindsight oracle.**

### Planning
27. 🟡 **Stochastic MuZero-style search** over the learned world model.
28. 🟡 **Hierarchical options**: "follow this game until X" (macro) and "cut on this dead ball" (micro).
29. 🟢 **Belief-state planning**: a particle filter per game for feed and TV lag.
30. ⭐🟡 **Value of information**: peek with the hidden stick when uncertain, before committing.
31. 🟢 **Two-stick scheduling as an optimization problem** over a 10-minute horizon, with learning inside it.
32. ⭐🟢 **Catch-up reel ordering as a scheduling problem** that keeps suspense intact.
33. 🟡 **Risk-sensitive policy (CVaR)**: optimize the worst nights, not just the average, so must-sees never get missed.
34. 🟢 **Anytime planning**: a decision is always ready and gets better as time allows.
35. 🟡 **Population league of rival planners** (the AlphaStar trick).
36. 🟡 **Auto-curriculum**: one game, then two, then a full Sunday slate, then nightmare nights.

### How it learns
37. 🟢 **Offline RL on real logged nights first** (conservative methods), then online practice.
38. 🟡 **Expert iteration**: the planner teaches the Reflex, the Reflex speeds up the planner, repeat.
39. 🟡 **Continual learning** with replay, so it never forgets last season.
40. 🔴 **On-device personalization** (federated) for the company version.
41. ⭐🟡 **Realism filter on synthetic nights**: a judge model keeps only practice nights indistinguishable from real ones.
42. 🟢 **Curriculum by difficulty** (see 36).
43. 🟡 **In-game meta-learning**: adapt to tonight's rhythm in minutes (this ref crew, this broadcast's ad pattern).
44. 🟢 **Online Bayesian updates** of each broadcast's break rhythm during the game.
45. 🟢 **Active learning**: simulate and study next the nights the model is most unsure about.
46. 🟡 **Model merging + quantization-aware training**: league adapters merged, built to run small on the Mac.

### Safety and evaluation
47. ⭐🟡 **Formal verification of the floor rules**: prove the policy can't join mid-play under its own model.
48. 🟢 **Shadow mode every night**: the candidate runs silently next to live and gets scored.
49. 🟢 **Doubly robust off-policy dashboards** (pairs with 7).
50. 🟢 **Red-team agents** that hunt for nights that break the brain.
51. 🟢 **Drift detection**: notice when a league's broadcast patterns change and recalibrate automatically.
52. 🟡 **Interpretability probes**: see what the world model actually learned (clock? score? momentum?).

### Data and sensing
53. ⭐🟢 **Wall-clock gaps between plays become free break labels.**
54. 🟢 **Rulebook "break owed" countdown** (NBA, CBB, NHL TV-timeout rules).
55. ⭐🟢 **Betting-line microstructure as a news sensor**: sudden line moves flag injuries and events before the feeds do.
56. 🟢 **Media-session bursts on the sticks as automatic dead-ball labels** from our own nights.
57. 🟡 **Broadcast simulator** (ad pods, delays, glitches) calibrated to our live logs.
58. 🟢 **Every live night logged as gold data**, auto-labeled.

### Hype and attention (Nick, 5:50 PM)
59. 🟢 **Hype predictor**: math that forecasts the attention peaks of the next 10 minutes, built from suspense curves, public minute-by-minute viewership research, social/pageview spikes and market moves.
60. 🟡 **Retention model**: predict the moments a viewer would drift (long dead stretches, blowouts) and fill them before they happen (pre-tuned cuts, catch-up reels, "meanwhile" moments).
61. ⭐🟡 **Arousal pacing**: alternate tension and release the way the best broadcasts and films do. Peak-end rule: end each block on a high.
62. 🟢 **Guardrail**: optimize for "worth it" (would you rather have watched this?), not compulsion. No dark patterns, no gambling nudges, and no camera, mic or body sensors on Nick (his own rule).

### Company (later)
- 🔴 **One shared cloud brain per game**, with a personal ranking per user: compute per game, not per viewer.
- 🔴 **Drama API** for broadcasters, fantasy, bars and live betting.
- 🔴 **Automated whip-around producer** for rights holders: "CrunchTime for every sport, every night".
- 🔴 **Licensed video and audio**: crowd roar and announcer pitch as excitement labels.

---

## 4. The plan and ETA

These are honest ETAs given $0, Kaggle (30 GPU-h/week plus TPU on your one account) and 5 lanes. Each phase only goes live by passing your bar on locked nights:
- never join mid-play
- NBA/NHL crunch
- your teams
- everything else no worse than live

### Phase 0: foundations (now → Fri Nov 6)
1. **Drama math v1** (19, 20): suspense and surprise from win probability and odds, as a scorecard line and a Watch Value input.
2. **One event language** spec (9) and the ingest of the held data catalog, wave 1 (nflverse, NBA pbp archive, NHL, Retrosheet).
3. **City map merged** (#165); brain migrations start.
4. **V8 Mind**: the crunch-stay fix plus the NBA leak audit (last GPU hours this week).
5. **Shadow mode nightly** (48) and **off-policy grading** (7, 49).

**Done when:** drama shows on the scoreboard, and the first event-token shards are on Kaggle.

### Phase 1: timing brain (→ ~Wed Nov 11)
1. **Point-process timing model** (2) for breaks, scores and crunch entry/exit, plus the **break-owed countdown** (54) and **free break labels** (53).
2. **Belief tracker** (29, 44) for feed and TV lag.
3. **Conformal safety layer** (6) on every entry.
4. **Catch-up ordering with spoiler control** (1, 20, 32).

**Done when:** mid-play entries are 0 with a stated guarantee, and live-or-replayed-within-3-min beats live on locked nights.

### Phase 2: world model v1 (→ ~Wed Nov 25)
1. Pretrain the **~100M-parameter world model** on event tokens (9-12, 17), on Kaggle GPU + TPU.
2. **Retrieval of similar moments** (14).
3. **Planner v1**: search over the world model plus **offline RL on real nights** (27, 29, 37), and **value of information** (30).
4. **Adversary nights + curriculum** (5, 36, 42).

**Done when:** the planner beats the hand-built decider on % of oracle value on locked nights in at least 2 leagues.

### Phase 3: better than chess (→ ~mid-December)
1. **Stochastic MuZero-style planner** plus a **population league** (27, 35).
2. **Hierarchical planning** (28).
3. **Think-harder-in-crunch** (4).
4. **Risk-sensitive policy** (33).
5. **Reflex distillation** (38, 46).
6. **Formal check of the floor rules** (47).

**Done when:** ≥95% of oracle value on big plays live per league, and press-to-TV ≤3 s on real hardware.

### Phase 4: the self-improving machine (→ January playoffs / Super Bowl as the showcase)
1. **Nightly learning while you sleep** (39, 43, 51).
2. **AI scientist lab** (8, 50).
3. **Dream futures** (3).
4. **Inverse RL from producers** (23).
5. Demo-ready.

### Phase 5: the company (spring 2027 →)
- Drama API and the whip-around producer pitch.
- Licensed video and audio.
- Shared cloud brain.
- Data from consenting users.

**The biggest ETA risk is GPU compute, not ideas.** If Google's free TPU Research Cloud application gets approved, Phases 2-3 move up about 1-2 weeks.

---

## 5. What I need from Nick
1. **"go"** on this plan (it becomes the official direction, sent to the planners).
2. **"skip Savant"** or **"pull it"**.
3. **The college football data key**: add it as a Kaggle secret when you have 3 minutes.
4. Optional: I draft the **TPU Research Cloud** application, and you submit it.
