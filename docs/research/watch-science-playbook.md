# Watch science playbook (from the Nov 4 attention-science review)

The goal: the best night of sports, never missing a moment that matters. Rank on suspense, replay on surprise, and judge every change by whether the night was worth it, not by minutes watched.

## 2. The unifying model: anticipation, uncertainty, resolution, release

One loop explains most of the findings. Each stage has a brain account, a psychology account and a
math account.

```
  CARE ──► ANTICIPATION ──► UNCERTAINTY PEAKS ──► RESOLUTION ──► RELEASE / RESET
  (stake)   (build-up)        (the snap, shot,       (surprise,      (dead ball,
                               power play)            belief jumps)   wind-down)
     ▲                                                                    │
     └───────────── next game with care × suspense ◄──────────────────────┘
```

### 2.1 Care: the multiplier

- **Psychology.** Zillmann's disposition theory: suspense is hope or fear about an uncertain
  outcome *you have a stake in*. Without a stake, uncertainty is just noise. (Moderate.)
- **Brain.** Favoured-team success and rival failure engage the ventral striatum; favoured-team
  losses engage regions associated with conflict and negative affect (Cikara et al. 2011; Duarte et
  al. 2017). Small samples. (Moderate.)
- **Math.** Treat care as a weight `w_g` on each game: high for WSH and UNC, medium for playoff
  implications and followed players, low for neutral games. Paul & Weinbach-style ratings data
  suggest neutral viewers drift away from blowouts while fans of the winning side do not (weak,
  abstract-level).

### 2.2 Anticipation: the build-up is part of the reward

- **Brain.** Ventral striatum activity rises while waiting for a possible gain, in proportion to
  its size; outcome receipt engages more medial prefrontal cortex (Knutson et al. 2001; Oldham et al.
  2018 meta-analysis). The split is relative, not absolute. Caveat: these are monetary rewards to
  the participant; vicarious sports outcomes are plausible but untested. (Strong for the task.)
- **Brain, information.** Monkeys value early information about a coming reward even when it cannot
  change it, and dopamine neurons signal that preference (Bromberg-Martin & Hikosaka 2009).
  (Moderate.)
- **Psychology.** Curiosity peaks at a gap you can almost close (Loewenstein 1994).
- **Implication.** Arriving early, at the dead ball, delivers the anticipation phase. Arriving late
  delivers only the outcome, and often mid-play. Rule #1 is not just a technical rule; it protects
  the most motivating part of the moment.

### 2.3 Uncertainty: what to rank on

- **Math (strong theory).** Ely, Frankel & Kamenica (2015) define, for a belief `p_t` (here, win
  probability):
  - suspense: `S_t = E[(p_{t+1} − p_t)^2]` (the expected squared move on the next play), which is
    leverage;
  - surprise: `U_t = |p_t − p_{t−1}|` (the size of the move that just happened).
  For a binary next event, outcome variance `p(1−p)` peaks at 0.5, which is "close and late"
  formalised. Bayesian surprise as KL divergence over the full outcome distribution (Itti & Baldi
  2009) generalises this to overtime, extra innings and multi-outcome states.
- **Data (moderate).** Minute-level audiences rise with suspense and surprise in Wimbledon tennis
  (Bizzozero et al. 2016; surprise mattered more, both mattered more late), Premier League soccer
  (Buraimo et al. 2020; plus "shock" against the favourite), and NBA national broadcasts (Solow et al.
  2024; modest, concentrated late). On Twitch CS:GO, suspense slightly reduced leaving (0.27 pp per
  SD) and surprise did little (Simonov et al. 2023). The literature disagrees on surprise, so weights
  must be fit on our own nights.
- **Brain (weak).** A sustained dopamine ramp peaking at p = 0.5 (Fiorillo et al. 2003) is disputed;
  it may be an averaging artefact (Niv et al. 2005). Justify "close games first" with audience data,
  not this.

**Key design consequence.** Suspense looks forward, so it can be acted on at a dead ball. Surprise
looks backward: by the time it is measured, the play is over. **Rank switches on suspense. Use
surprise only for replays, "what you missed" and evaluation.**

### 2.4 Resolution: prediction error

- **Brain (strong).** Phasic dopamine encodes reward prediction error: above baseline for better
  than expected, near baseline for fully expected, below for worse (Schultz et al. 1997). As cues
  become predictive, the signal moves earlier, to the cue. Dopamine also tracks salience and effort,
  so "dopamine = engagement" is an overclaim; say "consistent with prediction-error learning".
- **Implication.** Resolutions only feel like something if the outcome was genuinely uncertain.
  A predictable feed goes flat; so does a feed whose switch patterns the viewer can predict.

### 2.5 Release and reset: why valleys matter

- **Habituation (strong).** Repeated identical stimuli lose their effect; rest or change restores
  it (Rankin et al. 2009). Novelty engages the dopaminergic midbrain (Bunzeck & Düzel 2006).
- **Capacity (moderate).** Each cut forces re-orientation; fast pacing plus arousing content
  overloads processing (Lang et al. 1999).
- **Arousal regulation (weak as applied).** Adaptive gain theory (Aston-Jones & Cohen 2005) suggests
  focused engagement alternates with exploration. Excitation transfer (Zillmann 1971) suggests
  arousal from one moment colours the next. Neither has been tested on a switching sports feed.
- **Cost (moderate).** High-stakes games produce real stress responses: acute cardiac emergencies
  rose about 2.66-fold in Munich on German World Cup match days (Wilbert-Lampen et al. 2008,
  observational, one tournament). This is a population signal, not individual risk, but it argues
  against stacking peaks for hours.
- **Implication.** A good night has shape: build, peaks, breaths, a real ending. Peak-only cutting is
  a hypothesis to test, not a default.

### 2.6 Memory of the night: peak-end

Retrospective ratings track the peak and the end about as well as the overall average, and almost
ignore duration (Alaybek et al. 2022, strong). What Nick remembers tomorrow is the biggest moment and
the last one. This is the strongest argument against chasing minutes.

---

## 4. The RedZone playbook: 22 mechanics

Each mechanic has: mechanism, evidence strength, how to measure, and the North Star line it moves
(names as in [northstar.md](../northstar.md)). Every one must hold **mid-play cuts + entries at 0**
and must not raise plays missed on entry or dead air. Any that touches a default goes in
decisions.md and ships off behind a flag until it passes the release gate.

### A. Where to go (ranking)

**1. Suspense score as the core ranking input.**
- Mechanism: expected squared change in win probability over the next play or possession,
  `S = E[(ΔWP)^2]`, computed per game at each dead ball. Forward-looking, so it fits Rule #1.
- Evidence: strong theory (Ely et al. 2015); moderate audience data in four settings.
- Measure: suspense-at-entry per switch (log next to entries_mid_play); correlation with Nick's
  👍/👎; per-league WP calibration error on the locked set before turning it on.
- North Star: Crunch live (NFL/NBA/NHL); Big plays seen live; Right game agreement.

**2. Care × suspense (disposition weighting).**
- Mechanism: multiply suspense by a stake weight: WSH and UNC high, playoff implications and
  followed players medium, neutral games low. Keep his teams as a hard constraint, not a learned
  weight the optimiser can trade away (Kleinberg et al. 2024).
- Evidence: moderate (Zillmann; Knobloch-Westerwick et al. 2009; Cikara et al. 2011).
- Measure: your-team metrics; override rate on non-team games.
- North Star: Your-team crunch live; Your-team big plays seen.

**3. Late-game multiplier, concave.**
- Mechanism: weight suspense up as the clock runs down (the effect is concentrated late in tennis,
  NBA and soccer data). Apply a log transform so near-ties do not dominate.
- Evidence: moderate (Bizzozero et al. 2016; Solow et al. 2024).
- Measure: crunch-live share; switches per hour.
- North Star: Crunch live.

**4. "Upset in progress" term.**
- Mechanism: surprise weighted by how unlikely the current state was pre-game ("shock"). Pre-game
  line used only as a prior, never shown.
- Evidence: moderate (Buraimo et al. 2020), one league.
- Measure: 👍 rate on upset switches vs other switches.
- North Star: Right game agreement.

**5. KL-divergence surprise over the full outcome distribution.**
- Mechanism: handles overtime, shootouts and extra innings better than |ΔWP|; same measure ranks
  "what you missed".
- Evidence: moderate (Itti & Baldi 2009; Baldi & Itti 2010), but the step from gaze to game-belief
  is an analogy.
- Measure: replay-pick agreement with Nick; NHL OT coverage.
- North Star: Big plays live or replayed within 10 min.

**6. League order as a hard rule, suspense within it.**
- Mechanism: NFL > CFB > NBA > NCAAB > NHL > MLB stays a stated preference in config. Suspense
  ranks within tiers and lets a lower-league crunch beat a higher-league non-crunch only as the rule
  already defines.
- Evidence: moderate (Kleinberg et al. 2024; stated preferences outperform engagement, Milli et al.
  2025).
- Measure: the league-order metric once mixed-league nights are graded.
- North Star: League order respected.

### B. When to go (timing)

**7. Arrive at the dead ball before the big play.**
- Mechanism: anticipation is itself rewarding; the build-up is the most motivating part. Predict the
  next high-leverage snap (4th down, final possession, power play start, bases loaded with two outs)
  and switch at the preceding stoppage.
- Evidence: strong for anticipation (Knutson 2001; Oldham 2018); transfer to sport is inference.
- Measure: seconds of pre-play build-up seen per big play; entries_mid_play.
- North Star: Big plays seen live; Landings in dead time; Mid-play cuts = 0.

**8. Minimum dwell time plus hysteresis.**
- Mechanism: every cut costs re-orientation; overload comes from fast pacing plus arousing content.
  Require a switch-cost margin before leaving a game.
- Evidence: moderate (Lang 2000; Lang et al. 1999). Also matches gambling regulators' speed-of-play
  logic (UKGC 2025, our analogy).
- Measure: switches per hour (guardrail, not target); ping-pong count (A→B→A within N minutes).
- North Star: Mid-play cuts = 0; Right game agreement.

**9. Open thread, resolved at its next dead ball.**
- Mechanism: the urge to return to an interrupted activity (Ovsiankina). When leaving an
  unresolved situation, tag it and give it priority at its next dead ball, normally within about
  3 minutes of game time.
- Evidence: moderate, direction only (Ghibellini & Meier 2025).
- Measure: open threads returned to; median time to resolution; cap visible threads at 1-2.
- North Star: Big plays live or replayed within 10 min.

**10. Fill breaks with the best other live game.**
- Mechanism: boredom arises from low arousal and pushes attention to the phone. Treat commercials,
  halftime and intermissions as known dead balls.
- Evidence: moderate (Eastwood et al. 2012, conceptual).
- Measure: dead_air_pct.
- North Star: Dead air.

**11. Surprise drives replays, never cut-ins.**
- Mechanism: surprise is measured after the play; cutting on it lands late or mid-play. Use it to
  queue a replay at the next dead ball.
- Evidence: strong logic from the definitions; moderate data.
- Measure: replays shown within 10 min; entries_mid_play unchanged.
- North Star: Big plays live or replayed within 10 min.

### C. What the viewer sees (presentation)

**12. 2-3 s orientation beat on every switch.**
- Mechanism: teams, score, clock, situation. Reduces re-orientation cost and supports competence
  (flow, SDT).
- Evidence: moderate (Lang; Sherry 2004; Ryan et al. 2006).
- Measure: "what game is this?" moments; overrides within 30 s of a switch.
- North Star: Right game agreement.

**13. One-question context line.**
- Mechanism: frame the information gap the next play closes ("Down 3, 1:12 left, no timeouts").
  Only questions the game will answer soon.
- Evidence: moderate (Loewenstein 1994; Kang et al. 2009).
- Measure: 👍 rate with vs without the line (A/B by night).
- North Star: Right game agreement.

**14. "Why we're here" tag tied to the real score.**
- Mechanism: explainable picks increase agency; tying any excitement label to a logged threshold
  keeps it honest (the reverse of losses disguised as wins).
- Evidence: moderate (Lukoff et al. 2021; Dixon et al. 2010 as the anti-pattern).
- Measure: "alerted but nothing happened" rate.
- North Star: Right game agreement.

**15. Quiet "also close" strip.**
- Mechanism: resolves curiosity about other games without forcing a switch; the honest inverse of
  FoMO ("you won't miss anything that matters").
- Evidence: moderate (Bromberg-Martin & Hikosaka 2009; Przybylski et al. 2013 for the FoMO side).
- Measure: overrides to strip games; plays_missed_entry.
- North Star: Big plays live or replayed within 10 min.

**16. Variety only as a tiebreaker; no fixed switch sting.**
- Mechanism: habituation to repeated cues; novelty helps only when leverage is equal.
- Evidence: strong (Rankin et al. 2009; Bunzeck & Düzel 2006).
- Measure: novelty-tiebreak rate; check it does not raise entries_mid_play.
- North Star: Right game agreement.

### D. The shape of the night

**17. Peak-end ordering of catch-ups and the finale.**
- Mechanism: memory tracks peak and end. Guarantee a live look at every game-deciding final
  possession; order a catch-up reel so its biggest moment and a strong closer land last; end on the
  last close finish, then a clear "Slate complete, 0 live games" state instead of filler.
- Evidence: strong (Alaybek et al. 2022).
- Measure: end-of-night rating; test whether peak and end predict it better than mean suspense
  (expect about equal).
- North Star: Crunch live; Your-team crunch live.

**18. Deliberate valleys (test, not default).**
- Mechanism: staying on one game through a calmer stretch may make peaks land harder than
  wall-to-wall peak cutting.
- Evidence: weak (Aston-Jones & Cohen 2005; Zillmann 1971).
- Measure: A/B by whole night on 👍 rate and end-of-night rating.
- North Star: Right game agreement.

**19. Wind-down after the last high-leverage window.**
- Mechanism: fewer switches, a recap rather than hunting for the next spike. After midnight, switch
  only for his teams or crunch time.
- Evidence: moderate (physiological cost; sleep displacement inference).
- Measure: late-night hours watched (guardrail); end-of-night rating on late nights.
- North Star: none directly; protects the welfare metric in §6.

### E. Control and connection

**20. Commitment and agency controls.**
- Mechanism: "stay here", "take me to X", "only my teams", "stop after this game", "end at 11:30"
  (waits for a dead ball). People want help with self-control and these tools work.
- Evidence: strong (Allcott, Gentzkow & Song 2022); moderate (Lukoff et al. 2021; Cho et al. 2021).
- Measure: control use; overrides decaying over time; phone press to TV latency.
- North Star: Phone press to TV p95; Right game agreement.

**21. One-tap 👍/👎 per switch as the training label.**
- Mechanism: satisfaction signal alongside engagement, as YouTube moved to (Zhao et al. 2019).
  Randomise a small share of near-tie decisions, only among already safe dead-ball options, for
  off-policy evaluation.
- Evidence: moderate.
- Measure: label volume per night; inverse-propensity estimates.
- North Star: Right game agreement (currently PENDING for lack of taps).

**22. Followed players and optional group mode.**
- Mechanism: parasocial involvement raises priority for players Nick follows; group mode blends
  guests' dispositions.
- Evidence: strong-but-correlational (Tukachinsky et al. 2020); weak (co-viewing).
- Measure: evaluate group mode separately from the solo gates.
- North Star: Right game agreement (low priority).

---

## 5. The line: mechanics we will not use

### 5.1 The list

| Do not build | Why (harm) | Why (law and regulators) | Why (brand) |
|---|---|---|---|
| **Betting of any kind**: odds, bet prompts, live-bet overlays, affiliate links, wager-driven switching, betting volume as a signal | In-play and micro-bets are associated with problem gambling (Killick & Griffiths 2019; Russell et al. 2019); credit and debt effects after legalisation (Hollenbeck et al.; Baker et al. 2024) | State gambling advertising, age-gating and responsible-gambling rules; Massachusetts 205 CMR 257 bars using data on risky gambling to promote wagering | The obvious monetisation route and the highest-risk one. Recommend a decisions.md line: **RedZone never adds betting hooks.** (Pre-game lines as a hidden data prior are fine.) |
| **Artificial variable-ratio tricks**: random "mystery switch", fake near-misses, teases that often fail to pay off | Manufactured prediction error; erodes trust | Deceptive design under the FTC dark-patterns framing if ever distributed | Live sport already supplies honest uncertainty |
| **Overstated hype**: "CRUNCH TIME" banners, stings or alerts on routine moments | Closest analog to losses disguised as wins (Dixon et al. 2010, 2014, 2015) | Manufactured urgency is a textbook dark pattern (FTC 2022) | Teaches Nick to ignore alerts |
| **Infinite-autoplay traps**: chaining into blowouts or replays after the slate ends, auto-resume after he turns it off, hidden off switch | Removed stopping cues raise consumption (Schaffner et al. 2025; Lopez et al. 2024) | EU named autoplay and infinite scroll in its preliminary TikTok findings (6 Feb 2026); DSA minors guidelines recommend autoplay off by default for minors | Session length is not remembered as better (duration neglect) |
| **Pull-back notifications**: "come back, a game is close" when he has walked away; manufactured FoMO nags | FoMO linked to unmet needs and lower well-being (Przybylski et al. 2013, correlational) | DSA minors guidelines, NY SAFE for Kids Act (no overnight notifications to minors from 25 Jan 2027) | Allowed only: alerts he subscribed to, off by default, quiet hours on |
| **Streaks, badges, "you watched X hours"** | Rewards time, not moments | Streaks named in DSA Art. 28 guidelines | Wrong objective made visible |
| **Paid or chance-based rewards**: loot boxes, "spin for a bonus game", collectible drops | Loot-box spending associated with problem gambling (Garea et al. 2021; Spicer et al. 2022) | Belgium treated paid loot boxes as illegal gambling (2018) | No benefit for a sports viewer |
| **Targeting vulnerable states**: using time of night, session length, tiredness or "tried to turn it off" to *increase* retention | A model trained on long sessions learns to exploit impulsivity (Kleinberg et al. 2024) | Anti-targeting logic of 205 CMR 257 (analogy) | These signals may only *reduce* intensity |
| **Watch time as an objective or gate** | Time is an upward-biased proxy for value (Allcott et al. 2020, 2022) | Engagement-optimised hidden ranking is what US youth litigation targets | Goodhart's law: clicks → clickbait, time → filler |
| **Rewarding raw arousal**: fights, injuries, ejections, crowd-noise spikes as if they were suspense | Engagement objectives select for negative arousal (Milli et al. 2025) | — | Score outcome-relevant belief movement only |
| **Fake social proof**: "12,000 watching", invented crowd cues | Deceptive | FTC dark patterns; DSA Art. 25 | — |
| **Taunting rival content**: "rival is losing!" alerts or graphics | Rival-misfortune pleasure correlated with willingness to harm rival fans (Cikara et al. 2011) | — | Public repo |
| **Camera or biometric sensing** of the viewer | Privacy | Biometric privacy laws | Not needed |
| **Peacock** | — | — | Standing rule |

### 5.2 Why the line is where it is

The four-feature model in §3.1 gives the rule: **maximise honest reward density (more real moments
per hour, zero dead air, perfect dead-ball timing), keep stopping cues, never misrepresent outcomes,
never couple to money or chance.** RedZone is autoplay by nature, so the stopping cues are not
optional: end-of-slate state, user-set end time, wind-down.

### 5.3 Legal context (low today, rising on distribution)

- **EU.** Preliminary DSA findings against TikTok (6 Feb 2026) name infinite scroll, autoplay, push
  notifications and the personalised recommender, and say add-on wellness tools do not cure a design
  risk. Preliminary, contested, no fine. Art. 28 minors guidelines (July 2025) recommend autoplay and
  night-time pushes off by default for minors. RedZone has no DSA exposure as a personal tool.
- **US federal.** The FTC treats dark patterns as an enforcement priority (2022 staff report; Epic
  $245M; Amazon $2.5B in Sept 2025). These cases concern money and consent, not attention.
- **US states.** NY SAFE for Kids Act final rules (28 Jul 2026; effective 25 Jan 2027) restrict
  "addictive feeds" for minors: content selected from user behaviour rather than chosen by the user.
  An AI game-picker that learns from passive viewing plausibly fits that definition (our inference,
  untested). Nick's hand-set league and team order is user-chosen ordering, which is the defensible
  default. California SB 976 is still in litigation.
- **Litigation.** A Los Angeles bellwether jury found Meta and YouTube negligent in design ($3M
  compensatory plus $3M punitive, 25 Mar 2026; under appeal). Plaintiffs reached design features and
  internal documents. **Internal wording becomes evidence**, which is why loaded wording
  should not appear in committed docs, commit messages or the North Star.

---

## 6. Metrics: worth-it over minutes

**Primary (liking, value):**

1. **Right game agreement** with Nick's 👍/👎 (already a North Star line, ≥95%).
2. **End-of-night "worth it?" rating** (1-5, one tap on the slate-complete screen).
3. **Override rate** and time-to-override per game state (survival model; the strongest negative
   label; Fader & Hardie-style heterogeneity matters even with one user because night types differ).
4. **Moment capture:** big plays seen live, crunch live, your-team lines, big plays live or replayed
   within 10 min.

**Hard constraints (never traded):** mid-play cuts + entries = 0; landings in dead time ≥95%; league
order respected; never Peacock; his teams first.

**Guardrails (must not rise without a reason at the top of the PR):** dead air; switches per hour;
ping-pong count; "alerted but nothing happened"; late-night hours watched; sessions that continue
after the slate is complete.

**Diagnostic, never a target:** total watch time. **Warning rule:** watch time up while rating or 👍
rate down is a wanting-over-liking signal and blocks the change.

**Occasional probes:** agency ("I chose what I watched tonight", 1-5) and regret ("I watched longer
than I meant to", yes/no) on the last screen, at most once a week.

---

## 7. Experiments to run on our own data

All follow the release gate: whole-night time splits, the locked set scored once per release,
forward-chaining CV (report mean and worst fold), the 95% bootstrap lower bound. Report
entries_mid_play, plays_missed_entry and dead_air_pct before and after for each. The Mac stays light:
anything that trains goes to Kaggle or the cloud.

| # | Question | Design | Primary outcome | Guardrails | Est. effort |
|---|---|---|---|---|---|
| E1 | Does suspense-at-entry beat the current ranker? | Offline replay on pinned eval data, per league; then shadow mode on fresh nights | Crunch live, big plays live | entries_mid_play, switches/hour | about 1-2 days per league |
| E2 | Are per-league WP models calibrated enough? | Reliability curves and Brier score on the locked set, including OT and garbage time | Calibration error by state | — | about half a day per league |
| E3 | How much do suspense vs surprise vs care matter for Nick? | Fit weights on his 👍/👎 and overrides with whole-night CV; compare to literature priors | Right game agreement | worst fold | after about 60 labelled decisions |
| E4 | Does the 2-3 s orientation beat / context line help? | Alternate by whole night (on/off) | 👍 rate, overrides within 30 s | dead air | about 2 weeks of nights |
| E5 | Open threads: does returning at the next dead ball recover moments? | Offline replay counting resolutions caught | Big plays live or replayed within 10 min | entries_mid_play | about 1 day |
| E6 | Peak-end: what predicts the end-of-night rating? | Regress rating on peak suspense, end suspense, mean suspense, duration | Coefficients with CIs | — | after about 20 rated nights |
| E7 | Valleys vs peak-only pacing | Alternate pacing policy by whole night | End-of-night rating, 👍 rate | entries_mid_play, switches/hour | about 3-4 weeks |
| E8 | Dwell time and hysteresis margin | Sweep offline; pick the knee where ping-pong falls without losing crunch | Ping-pong count, crunch live | plays_missed_entry | about 1 day |
| E9 | Off-policy evaluation readiness | Randomise a small share of safe near-ties; log propensities | IPS estimate variance | entries_mid_play must stay 0 | ongoing, cheap |
| E10 | Does the wind-down mode cost anything Nick cares about? | Compare late-night 👍 rate and your-team metrics with it on vs off | Your-team crunch live | late-night hours | about 2-3 weeks |
| E11 | Offline label validation | Check suspense peaks against free, terms-compliant public signals of gripping moments (no scraping, no paid APIs) | Rank correlation | — | about half a day, only if a $0 source exists |

---

