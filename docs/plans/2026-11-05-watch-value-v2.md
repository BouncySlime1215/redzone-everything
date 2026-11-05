# Watch Value v2: spec

**Source:** Nick, 9:05 AM and 9:35 AM ET Nov 5 2026. Watch Value is the product's core. It should rank live
moments better than a human whip-around producer and better than a generic video recommender, using
published research and real engagement data, with every claim tested by % of oracle on locked nights.

**Owners:** Data G owns the components, labels and their experiments (EXP-1700..1799). Lane ① owns the model,
the ranking objective and the RL parts (§5, marked). Data E owns the Teacher section (§6). Data H's recap
labels (EXP-1800/1804) run on the same label harness. Order: **NBA first, then NFL**.

**Guardrails (unchanged, they bind every section):**
- The objective is **"worth it"**: the moments a fan would choose to see if they knew what was coming. An
  engagement label measures what people found worth watching. Longer viewing is not a goal, and watch time
  is never a grade.
- No dark patterns (Mathur et al. 2019): no cliffhanger baiting, no artificial delay of a payoff, no
  nudges. Public docs use neutral wording ("engagement").
- **Nick's own viewing and button presses are never labels** (HARD RULE, no training signal from Nick).
- Heart-rate and arousal evidence comes only from **published** studies, never from new recordings.
- An engagement label is a **training target, never a feature**. It is known only after the moment
  (test writers' rule; enforced by the byte-identical-features test in #317 and the EXP-302 leak checks).
- Every component ships flag-off. It earns a place only through its own pre-registered row, graded by
  % of oracle (`redzone/lab/oracle.py`) on valid nights, with locked read once by the integrator.

## 1. The objective

For a candidate game *g* at decision time *t*, with a buffer of *B* seconds (the stream is shown *B* s behind
the feed, so the next *B* s of play-by-play are already known when we choose):

```
WV2(g, t) = E[ peak engagement of g over (t, t + B + H] ]      the moment ahead, part KNOWN (buffer) and part expected
          × stakes(g)                                         how much this game matters (season, rivalry, your team)
          × novelty(g, t)                                     habituation: the 5th 3-pointer in a row is worth less
          − whiplash(t)                                       the cost of one more switch right now
```

- The first factor splits into a **known** part, the realized surprise inside the buffer (§2.1), and an
  **expected** part beyond the buffer, which comes from suspense and anticipation (§2.2, §2.3).
- The weights are learned. §5 trains a pairwise ranker across games live at the same minute, because the
  product's real decision is "which game now". No weight is hand-tuned on any split.
- **Why this can beat a recommender:** a feed recommender ranks clips after the fact, from engagement history
  (Covington et al. 2016). Live sport has no history for the next play. With the buffer, the next *B* s are a
  *known* outcome, so the surprise term is an observation rather than a forecast. No published recommender
  has that on live events.
- **Why this can beat a producer:** a whip-around producer watches a handful of feeds with human attention
  limits, and joins at whatever moment the cut lands. We see every game's play-by-play plus the buffer, and
  never join mid-play (rule #1). The comparison is honest only against the hindsight oracle (% of oracle).
  There is no licensed producer log to grade against directly (EXP-079 stays on hold).

## 2. Components

Every row: what it is, the research behind it, the real data that labels or validates it, and its experiment.
"Derived" means computed from our own play-by-play and WP (novelty 0). It can be a feature or a baseline, but
it never counts as new human data.

### 2.1 Surprise, known inside the buffer (prediction error)
- **Research.** Dopamine neurons fire on reward prediction error, the gap between what happened and what was
  expected (Schultz, Dayan & Montague 1997). Bayesian surprise, the size of the belief update, predicts where
  people look (Itti & Baldi 2009). For sport, surprise is the realized change in beliefs about the winner
  (Ely, Frankel & Kamenica 2015). Minute-by-minute TV audiences rise with surprise in tennis (Bizzozero,
  Flepp & Franck 2016) and in soccer (Buraimo, Forrest, McHale & Tena 2020).
- **Feature (derived, legal live):** `surprise_buf` = max |ΔWP| over the plays already in the buffer for game *g*.
  It is known before we show anything, so it is an input, not a leak.
- **Validated by:** Bluesky mention spikes (G-a) and Wikipedia edit spikes (B) in the minutes after the play.
- **EXP-1705:** WV + `surprise_buf` beats WV on % of oracle (NBA, then NFL). Arm A3 of EXP-1704 already tests
  the drama baseline as a label.

### 2.2 Suspense (expected belief change ahead)
- **Research.** Suspense is the variance of next-period beliefs, so it is highest when the next play can swing the
  game (Ely, Frankel & Kamenica 2015). Audiences respond to it (Bizzozero et al. 2016; Buraimo et al. 2020).
- **Feature (derived):** `suspense` = E[|ΔWP| next play | WP decile, time left], fit on train nights only
  (`scripts/attention_labels.py fit_suspense`). #166's crunch state is a coarse version of it.
- **EXP-1705** (same row; suspense and buffer surprise are ablated separately and together).

### 2.3 Anticipation ramps
- **Research.** Dopamine ramps up as a reward gets closer (Howe et al. 2013). The nucleus accumbens tracks the
  anticipation of reward size (Knutson et al. 2001). People value the wait for a good outcome itself
  (Loewenstein 1987).
- **Feature (derived):** time-to-likely-payoff, for example an NFL drive in the red zone, an NBA final-possession
  setup, or an NHL power play. Each is built from play-by-play state, not from text.
- **Label:** the engagement spike in the minutes before the payoff, versus after it.
- **EXP-1706:** an anticipation term beats WV without it on % of oracle. It must also not raise mid-play entries:
  joining *before* a payoff is the point, and joining *during* the play is never allowed.

### 2.4 Habituation and novelty decay
- **Research.** Repeated stimuli lose response (Thompson & Spencer 1966; Rankin et al. 2009). Collective
  attention to news decays on a fixed curve (Wu & Huberman 2007), and memory of cultural items decays in
  two phases (Candia et al. 2019).
- **Feature (derived):** the count of same-type events in this game in the last *N* minutes, and the time since
  our last visit to it.
- **Label:** whether the spike per event shrinks as same-type events repeat (Bluesky, Wikipedia edits).
- **EXP-1707:** first, measure the decay on engagement labels (a report, not a pass/fail). Then a novelty
  multiplier beats WV without it on % of oracle.

### 2.5 Stakes and peak-end
- **Research.** People judge an episode by its peak and its end (Fredrickson & Kahneman 1993; Kahneman et al.
  1993). Stakes (standings, rivalry, playoffs) raise audience demand (the suspense papers above control for
  them).
- **Feature (real context, B):** standings and playoff odds, rivalry table (`meta/rivalries.csv`), your team
  (WSH/UNC first), pregame Wikipedia interest (`pageviews_z`, B; pregame, so legal as an input).
- **Label (real, game level):** published TV viewers per game (G2: league and network press releases, numbers
  only), and post-game Wikipedia views.
- **EXP-1708:** `stakes` learned against game-level viewers beats B's EXP-091 importance on % of oracle. The
  peak-end part: protect the last minutes of close games (already the crunch floor; this row only measures it).

### 2.6 Collective attention and contagion velocity (the main real label)
- **Research.** Attention to an item spreads and decays in measurable response classes (Crane & Sornette 2008,
  YouTube views; Lehmann et al. 2012, Twitter hashtags).
- **Labels (real, human):**
  - S1 Wikipedia edits per team article, 15 min, B (backfill of train, dev and locked nights is running);
  - S2 Wikipedia pageviews, 1 h, B;
  - S3 Bluesky mentions per minute, G (live from Nov 5 2026);
  - S4 highlight view velocity per play, C;
  - S5 prediction-market volume spikes, D (volume only; prices stay inputs).

  Twitch viewer curves (S6) wait on Nick's key. Reddit stays out because its terms bar ML training on user
  content.
- **EXP-1704** (pre-registered, PR #314): each source is a separate run in the Holm family
  `wv-attention-labels`. Arms are swing only, swing plus label, a within-game placebo, and WP drama math. A
  source ships as a label only if it beats all three. **EXP-1701** first checks that the Bluesky signal tracks
  the realized |ΔWP|.

### 2.7 Arousal regulation and whiplash
- **Research.** Arousal carries over between stimuli (Zillmann 1971, excitation transfer). People pick media to
  regulate arousal (Zillmann 1988, mood management). Arousal has an optimal middle, where too little bores and
  too much repels (Berlyne 1960).
- **Feature (derived):** switches in the last 5 and 15 minutes, and the time since the last switch.
- **Label:** none from viewers (Nick's own presses are never labels). The term is graded only through
  % of oracle with the scorecard's switches/hour shown beside it.
- **EXP-1709:** a learned whiplash penalty keeps % of oracle within its CI while cutting switches/hour, or it
  raises % of oracle. Either outcome is reported.

### 2.8 Narrative transport and storylines
- **Research.** Absorption in a story ("transportation") changes how people experience and judge it (Green &
  Brock 2000).
- **Features and labels:** Data H's recap mentions (which plays people wrote about; EXP-1800/1804, a label on the
  shared harness). B's storyline tags (EXP-093, numbers and URL only). The Teacher's per-play story read (§6) is
  an input only.

### 2.9 Published physiology as a prior check
- **Research.**
  - Cardiac emergencies rise on high-stakes match days (Wilbert-Lampen et al. 2008, *NEJM*).
  - Fans' cortisol rises during a final (van der Meij et al. 2012, *PLoS ONE*).

  These studies cover whole games or days, not single plays. G3 searches for published per-moment fan
  heart-rate data under open licenses, and anything it finds is used only as reported.
- **Use:** an eval-only sanity check (**EXP-1710**). WV's ranking of game states (close and late vs blowout,
  high stakes vs low) agrees in direction with the published findings. It is never a training label, and no
  new recordings are made.

## 2b. STAY or LEAVE with full context (Nick, 9:45 AM; lane ① builds the model)

The decision after each play in the game on screen: stay, or leave for the best alternative. **Hard floors,
not learned:** never leave a high-leverage moment (close and late, or OT: `redzone/lab/leverage.py`), and never
join mid-play. The learned part decides only inside those floors.

| Part | What it is | Research | Real data | EXP |
|---|---|---|---|---|
| Game state | WP trajectory (level, slope, volatility), leverage, current run, comeback odds, possessions left, stakes | WP models per play (Lock & Nettleton 2014); runs and momentum are weaker than fans think, so a run enters only as measured WP volatility (Gilovich, Vallone & Tversky 1985; Miller & Sanjurjo 2018) | play-by-play + brain-track WP (A); stakes (B) | EXP-1712 |
| The play's consequence | ΔWP, Δ suspense, Δ stakes (for example a playoff-odds move), Δ narrative (a record or a storyline beat) | surprise and suspense (Ely, Frankel & Kamenica 2015); peak-end (Fredrickson & Kahneman 1993) | WP (derived); playoff-odds sims (B); recap mentions (H) and storyline tags (B, EXP-093) | EXP-1712 |
| Rollout value vs best alternative | value of staying over the next horizon minus the value of the best other live game, with each rollout from the world model or the WP sims | marginal value theorem: leave a patch when its rate drops below the best available elsewhere (Charnov 1976); information foraging (Pirolli & Card 1999); search for the best alternative (Weitzman 1979) | every live game's state and the buffer (A); engagement labels to check rollouts (§2.6) | EXP-1713 |
| Switch cost and hysteresis | leave only when the alternative beats staying by a margin that grows with recent switches | entry and exit under uncertainty create a band of inaction (Dixit 1989); arousal carry-over (Zillmann 1971) | our own scorecards (switches/hour, dead air); never Nick's presses | EXP-1709 (whiplash) + EXP-1713 |

- **EXP-1712:** STAY/LEAVE with game state plus play consequence beats #166's crunch state on % of oracle, NBA
  then NFL, with **0 high-leverage leaves** and **0 mid-play entries** (hard: one violation fails the row).
- **EXP-1713:** adding rollout value against the best alternative, with a learned hysteresis band, beats EXP-1712.
  Switches/hour and dead air are reported beside it, and neither may rise without a measured reason.

## 3. Data sources: status

| Source | Owner | Role | Status |
|---|---|---|---|
| Play-by-play + WP (all derived terms) | A / brain track | features | landed |
| Wikipedia edits / views, live | B | labels S1/S2 | backfill running (NBA first) |
| Wikipedia pregame interest, standings, rivalries | B | stakes features | landed (EXP-091) |
| Bluesky mentions per minute | G | label S3 | live nightly from Nov 5 2026 |
| Highlight view velocity per play | C | label S4 | waits on YOUTUBE_API_KEY (Nick) |
| Market volume | D | label S5 | waits on D's volume field |
| Twitch sports viewer curves | G | label S6 | waits on Nick's Twitch key |
| Published per-game TV viewers | G2 | stakes label | planned |
| Recap mentions | H | label | EXP-1800/1804 |
| Teacher per-play read | E | feature | gated on EXP-1404 |

## 4. Experiments (Data G block)

| id | component | grade | status |
|---|---|---|---|
| EXP-1701 | Bluesky signal check | Spearman vs realized \|ΔWP\|, beats clock-only | queued (8 nights) |
| EXP-1704 | engagement labels S1-S5 | % of oracle, Holm family, beats placebo and drama math | pre-registered (#314, #317) |
| EXP-1705 | buffer surprise + suspense features | % of oracle, NBA then NFL | outline |
| EXP-1706 | anticipation term | % of oracle; mid-play entries = 0 | outline |
| EXP-1707 | habituation / novelty | decay report, then % of oracle | outline |
| EXP-1708 | stakes vs game-level viewers | % of oracle vs EXP-091 | outline |
| EXP-1709 | whiplash penalty | % of oracle within CI with fewer switches/hour, or higher | outline |
| EXP-1710 | published physiology direction check | eval-only, agree in direction | outline |
| EXP-1712 | STAY/LEAVE: game state + play consequence (§2b) | % of oracle; 0 high-leverage leaves, 0 mid-play entries | outline |
| EXP-1713 | STAY/LEAVE: rollout vs best alternative + hysteresis (§2b) | % of oracle vs EXP-1712; switches/hour, dead air reported | outline |
| EXP-1711 | Teacher-feature ablation (E's section; id from G's block) | % of oracle per league and slice | outline, gated on EXP-1404 |

An outline row gets its full pre-registration (docs/prereg/exp-17NN.md) **before** any of its data is joined.
Each one uses the shared harness or the #166 trainer, seeds 0/1/2, a night-cluster bootstrap, valid nights for
development, and locked nights read once by the integrator. A row that fails does not ship. One that passes
ships flag-off until the release gate.

## 5. Model and learning (lane ① owns, to fill)

<!-- LANE 1: model and RL parts. Proposed by G, lane 1 decides:
- Pairwise ranking across games live at the same minute (LambdaRank; Burges et al. 2005), with labels from §2.6 and
  pairs only between games that were both live and both covered by the label source.
- The buffer DP and oracle use the realized surprise inside B and the model beyond B.
- Inverse RL from whip-around producers (Ng & Russell 2000; Ziebart et al. 2008) needs a licensed producer log;
  stays on hold (EXP-079) unless one appears.
- Distill to Reflex for the live path. -->

_Lane ① fills this section._

## 6. Teacher and synthetic data (Data E)

From Data E's section (Nov 5):
- **The 32B Teacher's per-play read is a feature, never a label.** Its importance, lock-in, likeliest next event
  and confidence (EXP-1402, 66k plays, private) stand in for a producer's read of story and stakes. It enters
  WV only if EXP-1404 (#300) shows within-context AUC with a CI lower bound > 0.50 on the outcome label. EXP-1402's
  NBA 0.955 was graded on a clock-rule label. Ablation: **EXP-1711**.
- **Suspense and surprise come from the WP model, not the LLM.** The Teacher's next-event and confidence fields
  are at most a prior where WP is weak (NHL, MLB), and they must pass the same ablation. Inside the buffer,
  realized surprise wins. The Teacher helps beyond the window.
- **Synthetic data:** mirror and re-time augmentations are for train nights only, capped at 3x real. Mirrored
  copies carry the original engagement label or none, because engagement has a home-market bias. **Engagement
  labels are never synthesized, imputed or Teacher-filled.** A night without real labels stays unlabeled and
  shows as PENDING.

## References

- Berlyne, D. E. (1960). *Conflict, Arousal, and Curiosity.* McGraw-Hill.
- Bizzozero, P., Flepp, R., & Franck, E. (2016). The importance of suspense and surprise in entertainment demand:
  Evidence from Wimbledon. *Journal of Economic Behavior & Organization*, 130, 47-63.
- Buraimo, B., Forrest, D., McHale, I. G., & Tena, J. D. (2020). Unscripted drama: Soccer audience response to
  suspense, surprise, and shock. *Economic Inquiry*, 58(2), 881-896.
- Burges, C., et al. (2005). Learning to rank using gradient descent. *ICML*.
- Candia, C., Jara-Figueroa, C., Rodriguez-Sickert, C., Barabási, A.-L., & Hidalgo, C. A. (2019). The universal
  decay of collective memory and attention. *Nature Human Behaviour*, 3(1), 82-91. doi:10.1038/s41562-018-0474-5
- Charnov, E. L. (1976). Optimal foraging, the marginal value theorem. *Theoretical Population Biology*, 9(2),
  129-136.
- Covington, P., Adams, J., & Sargin, E. (2016). Deep neural networks for YouTube recommendations. *RecSys*.
- Crane, R., & Sornette, D. (2008). Robust dynamic classes revealed by measuring the response function of a
  social system. *PNAS*, 105(41), 15649-15653.
- Dixit, A. (1989). Entry and exit decisions under uncertainty. *Journal of Political Economy*, 97(3), 620-638.
- Ely, J., Frankel, A., & Kamenica, E. (2015). Suspense and surprise. *Journal of Political Economy*, 123(1),
  215-260. doi:10.1086/677350
- Fredrickson, B. L., & Kahneman, D. (1993). Duration neglect in retrospective evaluations of affective episodes.
  *Journal of Personality and Social Psychology*, 65(1), 45-55.
- Gilovich, T., Vallone, R., & Tversky, A. (1985). The hot hand in basketball: On the misperception of random
  sequences. *Cognitive Psychology*, 17(3), 295-314.
- Green, M. C., & Brock, T. C. (2000). The role of transportation in the persuasiveness of public narratives.
  *Journal of Personality and Social Psychology*, 79(5), 701-721.
- Howe, M. W., Tierney, P. L., Sandberg, S. G., Phillips, P. E. M., & Graybiel, A. M. (2013). Prolonged dopamine
  signalling in striatum signals proximity and value of distant rewards. *Nature*, 500, 575-579.
- Itti, L., & Baldi, P. (2009). Bayesian surprise attracts human attention. *Vision Research*, 49(10), 1295-1306.
- Kahneman, D., Fredrickson, B. L., Schreiber, C. A., & Redelmeier, D. A. (1993). When more pain is preferred to
  less: Adding a better end. *Psychological Science*, 4(6), 401-405.
- Knutson, B., Adams, C. M., Fong, G. W., & Hommer, D. (2001). Anticipation of increasing monetary reward
  selectively recruits nucleus accumbens. *Journal of Neuroscience*, 21(16), RC159.
- Lehmann, J., Gonçalves, B., Ramasco, J. J., & Cattuto, C. (2012). Dynamical classes of collective attention in
  Twitter. *WWW*.
- Lock, D., & Nettleton, D. (2014). Using random forests to estimate win probability before each play of an NFL
  game. *Journal of Quantitative Analysis in Sports*, 10(2), 197-205. doi:10.1515/jqas-2013-0100
- Loewenstein, G. (1987). Anticipation and the valuation of delayed consumption. *The Economic Journal*, 97,
  666-684.
- Mathur, A., et al. (2019). Dark patterns at scale: Findings from a crawl of 11K shopping websites. *Proceedings of
  the ACM on Human-Computer Interaction*, 3(CSCW), 81. doi:10.1145/3359183
- Miller, J. B., & Sanjurjo, A. (2018). Surprised by the hot hand fallacy? A truth in the law of small numbers.
  *Econometrica*, 86(6), 2019-2047.
- Ng, A. Y., & Russell, S. (2000). Algorithms for inverse reinforcement learning. *ICML*.
- Pirolli, P., & Card, S. (1999). Information foraging. *Psychological Review*, 106(4), 643-675.
- Rankin, C. H., et al. (2009). Habituation revisited: An updated and revised description of the behavioral
  characteristics of habituation. *Neurobiology of Learning and Memory*, 92(2), 135-138.
- Schultz, W., Dayan, P., & Montague, P. R. (1997). A neural substrate of prediction and reward. *Science*, 275,
  1593-1599. doi:10.1126/science.275.5306.1593
- Thompson, R. F., & Spencer, W. A. (1966). Habituation: A model phenomenon for the study of neuronal substrates
  of behavior. *Psychological Review*, 73(1), 16-43.
- van der Meij, L., et al. (2012). Testosterone and cortisol release among Spanish soccer fans watching the 2010
  World Cup final. *PLoS ONE*, 7(4), e34814.
- Weitzman, M. L. (1979). Optimal search for the best alternative. *Econometrica*, 47(3), 641-654.
- Wilbert-Lampen, U., et al. (2008). Cardiovascular events during World Cup soccer. *New England Journal of
  Medicine*, 358, 475-483.
- Wu, F., & Huberman, B. A. (2007). Novelty and collective attention. *PNAS*, 104(45), 17599-17601.
- Ziebart, B. D., Maas, A., Bagnell, J. A., & Dey, A. K. (2008). Maximum entropy inverse reinforcement learning.
  *AAAI*.
- Zillmann, D. (1971). Excitation transfer in communication-mediated aggressive behavior. *Journal of Experimental
  Social Psychology*, 7, 419-434.
- Zillmann, D. (1988). Mood management through communication choices. *American Behavioral Scientist*, 31(3),
  327-340. doi:10.1177/000276488031003005

**Citation check (Nov 5 2026, 14:40 UTC):** all 34 references were checked against a publisher, DOI, PubMed,
RePEc or proceedings page before this spec left draft. Two were corrected (Rankin et al.'s full title; Mathur et
al.'s venue). None is unverified.
