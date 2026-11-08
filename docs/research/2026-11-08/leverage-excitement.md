# Leverage and excitement models: what is most worth watching right now (2026-11-08)

Scope: research only. No code changed, no secrets read. Every source below was opened or returned by a
search on 2026-11-08; "verified" says how far. Where I could not confirm a claim it is marked UNVERIFIED
and nothing is built on it. Older than 2024 is marked (old).

Repo read first: `redzone/scoring/gameflow.py`, `redzone/scoring/engine.py`, `redzone/lab/leverage.py`,
`redzone/crunch.py`, `redzone/scoring/garbage.py`, `redzone/lab/splits.py`, `redzone/lab/worth.py`,
`docs/research/watch-science-playbook.md`. The playbook already covers suspense/surprise theory
(Ely et al.), so this note does not repeat it; it adds win-probability (WP) model evidence, leverage
index, excitement indices, cross-sport normalization, evaluation and $0 data.

## 0. Findings that change what we do (read this first)

1. **Likely grader blind spot (verify by test).** `redzone/lab/leverage.py:high_leverage()` looks up
   `CLOSE_LATE.get(league)` with the league as passed, and the table has only NFL, NBA, NHL. CFB, CBB and
   WCBB are not keys, so they would return False ("not high leverage") when `league` is the college member;
   `engine.high_leverage()` handles this with `g.league.base`, the lab version does not. Also the table's
   NBA row assumes period 4 is the last regulation period, but CBB has 2 halves (a period-2 finish is never
   `per == last`). Impact: "high-leverage mid-play leaves" (a North Star line) may be undercounted for
   CFB/CBB in `lab/sportsgrade.py:185` and `lab/backtest.py:1133`. Not run, read only. Cheapest fix is
   `league.base` plus `pro_period`, then re-score; it can only make the grader stricter.
2. **WP is itself noisy, so thresholds on it need slack.** Brill, Yurko & Wyner (2024) show play-by-play
   WP estimates are high-variance: valid 90% coverage needed a mean interval width near 6.3% WP in their
   simplified football simulation. Pipping-Gamón & Wyner (2026) find no global miscalibration for NFL 2018-2024
   but an excess of extreme losing-team peaks in NBA (about 1.3 points in the upper 5% tail). So rank on a
   smoothed suspense, not on raw one-play WP deltas, and report intervals clustered by game.
3. **Suspense beats surprise for switching; the data disagree on surprise.** Twitch CS:GO: suspense cut
   leaving by 0.27 pp per SD, surprise had little effect. Premier League: suspense, surprise and "shock"
   all mattered. This matches the playbook rule (rank on suspense, replay on surprise).
4. **The "Solow 2024 NBA" citation in the playbook is UNVERIFIED.** Two searches found no such paper. The
   closest verified item is an unpublished Berkeley job-market paper using minute-by-minute NBA ratings
   (Scott M. Kaplan, no title retrieved). Do not cite Solow until someone finds it.
5. **Cross-sport normalization has no published recipe.** I found none. Section 2 is a design hypothesis
   (percentile of a common-horizon suspense), to be tested, not a cited result.

## 1. Ranked techniques with evidence

Rank = value to RedZone (fit to Rule #1, evidence, $0 cost, effort). Strength is about the evidence for
the technique, not about our implementation.

| # | Technique | Evidence and strength | Cost / fit |
|---|---|---|---|
| 1 | **Forward-looking suspense** `S = E[(WP_next - WP_now)^2]` over the next decision window, per game, at each dead ball | Theory strong (Ely, Frankel & Kamenica 2015, JPE 123(1):215-260, definition of suspense as variance of next-period belief). Audience data moderate: Twitch round-level suspense lowers leaving (Simonov, Ursu & Zheng 2023, JMR 60(1)); Premier League minute audiences (Buraimo et al. 2020, Econ. Inquiry 58(2):881-896); Wimbledon (Bizzozero, Flepp & Franck 2016, JEBO; abstract not read, findings via playbook, second-hand). | Pure function of game state plus a WP model. Forward-looking, so it respects "never JOIN mid-play". Best first build. |
| 2 | **Leverage Index as the per-sport baseline** (MLB: Tango's LI, average 1.0; high above 2.0 per FanGraphs; up to about 10) | LI is the expected swing in win expectancy for a state, normalized so the average is 1.0 (Tango, 2003 post; FanGraphs library). It is the same family as suspense (a swing-potential measure, stated by Tango as "swing possibilities"). Established, widely used, not tested against viewing. | Already estimated crudely in `engine.estimate_li()`. Prefer the feed's own LI or a table from Retrosheet/RE24. |
| 3 | **Calibrated WP per league as the substrate**, then suspense on top | NFL: nflfastR WP (XGBoost; features include time, field position, score, down/distance, timeouts, spread variant; LOSO calibration error 0.0055 vs nflscrapR 0.0397, per the model's own write-up; overtime and ties excluded from that calibration). Lock & Nettleton (2014, JQAS 10(2):197-205, random forest, 2001-2012 data) is the classic (old). NHL: Pettigrew's Sloan paper is the first NHL WP with power-play states (year on the Sloan page not confirmed); our `gameflow.wp_hockey` already takes skater counts. NBA: analytic margin/time model plus ESPN's feed. | ESPN's own `winprobability` array is already read (`sources/espn.py:534`). The analytic models in `gameflow.py` are the fallback. |
| 4 | **Excitement index = total WP movement** (sum of abs WP change per play; Burke's Game Excitement Index lineage; Vecer et al. total variation) as an after-the-fact label | Used by hobbyists and Yale's Benz write-up (scaled so overtime does not inflate it); Vecer, Ichiba & Laudanovic "On Probabilistic Excitement of Sports Games" (metadata only; text not readable). Beuoy (inpredictable, 2 Mar 2026) proposes a KL-divergence version that weights early-odds shifts far more (1% to 10% scores 0.208 vs 0.024 for 41% to 50%) and reports it tracks WikiHoops thumbs ratings better than the absolute-change index, for the 2024-25 NBA season; it is not validated against viewership. | Good as an evaluation label for "was this a big moment" and as the replay/"what you missed" ranker. Not for switching (backward-looking). |
| 5 | **Late-game concavity and shock** (weight S up as the clock runs down; add surprise vs the pre-game prior) | Effect concentrated late (playbook, citing Bizzozero 2016); "shock" = gap between pre-match and current win probability adds demand (Buraimo et al. 2020 abstract read). Kaplan (unpublished) reports thrill is highly time dependent and works mostly within a game. | Small multipliers; fit on our nights. One league (soccer) for shock, so keep it a tiebreak. |
| 6 | **Bayesian surprise (KL) for multi-outcome states** | Itti & Baldi 2009, Vision Research 49(10):1295-1306: KL of posterior vs prior predicted gaze (72% of gaze shifts to above-average surprise locations; 84% for regions all observers chose) in video. The step from gaze to game belief is an analogy. | Useful for NHL OT/shootouts, extra innings, overtime where binary WP is crude. Evaluation/replay first. |
| 7 | **Basketball EPV-style possession value** | Cervone, D'Amour, Bornn & Goldsberry (2016, JASA 111(514):585-599) need optical tracking, which we do not have. | Not usable at $0 (no tracking feed). Skip; use possession count and margin. |
| 8 | **Garbage-time gating** (already in `scoring/garbage.py`) | NFL within-game ratings: lost viewership in lopsided games exists in winning teams' markets but is far smaller there (a study titled "Tale of Three Cities", FIT Publishing; authors/year not retrieved, so UNVERIFIED beyond the abstract). The 496-game 2000-01 Nielsen regression (Colorado College thesis, page returned 403; claim via search snippet) says closeness is a significant determinant. Both (old, small). | Keep. Matches "your team's blowout still loses to a close game" only for neutral fans; the Tale-of-Three-Cities nuance supports the existing "your team first" rule. |

What the viewer-preference evidence does and does not say:
- Strong enough: closeness and late timing raise demand; suspense keeps viewers in a stream.
- Weak: anything minute-level for NFL/NHL/MLB. I found no verified minute-by-minute study for those leagues,
  and no verified viewer-switching study of multi-game RedZone-style viewing. Nick's own thumbs and nightly
  "worth it" (`lab/worth.py`) remain the only ground truth for his taste. Keep them evaluation-only, as the
  repo rule says.
- Stated preference vs engagement: Kleinberg, Mullainathan & Raghavan (2024, Management Science 70(9):6336-6355)
  model users with an impulsive and a reflective self; engagement optimization can hurt utility. Supports keeping
  league order and "his teams first" as hard rules rather than learned weights.

## 2. Cross-sport scoring design for RedZone

### 2.1 Idea: one number per game per decision window, comparable across sports

For each live game g at each candidate decision point (the next dead ball), compute

    S_g = E[ (WP_after - WP_now)^2 ]   over the next real-time window H (about 90 s of play)

where the expectation uses a small per-sport outcome menu, and WP is the leading team's (or home) win
probability. Two things make this comparable:

1. **Common real-time horizon H**, not "per play". A football play, an NHL shift, an NBA possession and a
   baseball plate appearance have different durations, so "next event" is not comparable. H in
   seconds of game clock (football and baseball use expected plays/PAs in H; hockey and basketball use
   scoring rates). This is the key assumption to test.
2. **Per-league percentile (ECDF) of S_g**, computed on training nights only. A league's score is
   `q_g = ECDF_league(S_g)` in [0,1]. Then an NHL 6-on-5 with the net empty at 1-goal margin and an NFL two-minute
   drill both land near the top of their own leagues' distributions and compare as roughly equal. Tango's
   "average = 1.0" normalization is the same idea with a mean instead of a percentile; percentiles are
   preferred because suspense is heavy-tailed. Tail-matching is a hypothesis: after normalization, check
   that each league produces a sensible number of "top 1%" moments per night (NFL has the most crunch
   possessions, MLB the fewest per game-hour).

Then the ranking input is `rank_g = care_g * (q_g)^a * late_g`, with league order and "my team"
kept as hard rules in `core.league_priority` (not learned), per the playbook and Kleinberg et al.

### 2.2 Per-sport outcome menus (all $0, no tracking data)

| Sport | State used | Next-window outcomes for S | Notes |
|---|---|---|---|
| NFL / CFB | score, time, down, distance, field position, timeouts, possession | next-play result classes (TD, FG, turnover, first down, punt) with EP/WP deltas from nflfastR EP/WP; or the empirical dist from `eval-data` | nflfastR WP for NFL; CFB uses the same football shape (`gameflow.SPEC['cfb']`) with the Stern normal-margin fallback already in `engine.lead_wp`. Red zone and two-minute drill fall out of the state, no special rule. |
| NBA / CBB / WCBB | margin, time left, possession, fouls/bonus | margin distribution over H from team scoring rates (`gameflow.wp_basketball`) | CBB has two 20-minute halves: use `pro_period`/`game_elapsed`, do not reuse NBA period numbers. |
| NHL | margin, time, **skater counts** (6-on-5, power play), goalie pulled | goal-for / goal-against in H with rates conditioned on skater state (`gameflow.wp_hockey` already accepts skaters); P(empty-net goal) rises when the goalie is out | Rates need estimation from NHL play-by-play; no verified public NHL WP model with code was found (Pettigrew, a BC thesis and a hobbyist RinkStats model exist; code/licence not confirmed). Build on `wp_hockey`. |
| MLB | inning, half, outs, runners, score | `LI` from the feed or Retrosheet RE/WE tables; `S ~ (LI * sigma_avg)^2` over one plate appearance | One PA is about 3-4 min of real time, longer than H; scale S by the share of H, or use H = one PA for MLB only and document it. |

### 2.3 Mapped to real files (no code written here)

| Change | File | What |
|---|---|---|
| Suspense function `suspense(league, state, H)` using each league's WP and outcome menu | `redzone/scoring/gameflow.py` (home of `wp_basketball`, `wp_hockey`, `wp_baseball`, `SPEC`) | New pure numpy function next to the WP models. Uses the feed's WP when `g.home_win_prob` is present, else the analytic one (`engine.lead_wp` is the pattern). |
| Per-league percentile table | `redzone/scoring/brain_models/` (fitted artifacts) built by `redzone/lab/brain3fit.py` / `redzone/lab/optimize.py` | ECDF of S per league fitted on train nights only; ship as data, not code. |
| Use in ranking | `redzone/scoring/engine.py` (`closeness`, `_nfl/_nba/_nhl/_mlb`, `brain_points`) | Replace the hand-set `_K` closeness bands with `q_g`; keep `crunch_time`/`crunch_window` as a floor (union, never weaker). Ship behind a flag, off by default until it passes the release gate. |
| Stricter leave rule | `redzone/lab/leverage.py` (and `engine.high_leverage`) | Fix finding 0.1 (college leagues). Then add: high leverage if rule-table hit OR `q_g >= 0.95` OR MLB `LI >= 2.0`. Union only, so it can only get stricter (matches the module docstring: "may only make it stricter"). |
| Crunch table | `redzone/crunch.py` / `default_config.yaml: crunch:` | Leave the shipped values; use `q_g` as an additional kind (`"suspense"`), logged in `docs/memory/decisions.md` per the PR rule. |
| Garbage time | `redzone/scoring/garbage.py` | Keep. Optionally let `q_g` low plus WP over about 99% in Q4 confirm garbage; do not let your-team exceptions change. |
| Replays / "what you missed" | `redzone/scoring/brain3.py` (`MomentV3`) | Use surprise (`abs(delta WP)` or KL) and Beuoy-style KL excitement for ranking replays, never for cut-ins. |
| Evaluation labels | `redzone/lab/oracle.py`, `oracle_v2.py`, `oracle_window.py`, `outcome_label.py` | Label each decision window with the realized max swing in the next 3 minutes. |

### 2.4 Design rules

- Suspense is computed from the state before the next snap or face-off, so it works at dead balls and
  never recommends joining mid-play.
- Use the **window** WP delta, not single-play, to damp the WP noise (finding 0.2).
- League order, "my team" and Nick's bets stay hard rules; `q_g` only ranks within them and may break a
  tie between leagues when both are in crunch (the existing `league_priority` rule).
- Never present an excitement number as a measured viewing result. Beuoy's KL is validated only against
  thumbs ratings, not audience.

## 3. How to evaluate without overfitting

Use the repo's own machinery: `redzone/lab/splits.py` (time splits by whole night, locked newest >= 25%,
5 forward-chaining folds over train+valid reporting mean AND worst fold, `leaks()` and `causal()` checks),
and `scripts/release_gate.py`.

1. **Pre-register** before any run: H (90 s), the normalization (ECDF per league), the weight grid for
   `a` and the late multiplier (at most 3 free knobs), the metrics and the pass bars below. Anything
   tuned goes in `docs/memory/decisions.md`.
2. **Fit only on train nights.** ECDF tables, WP calibration and weights come from train; validation nights choose among
   the few pre-registered options; the locked test is scored once per release candidate. A night is
   never split across sets (all games of a night stay together).
3. **Layer 1: WP calibration per league** (Brier score and reliability, by quarter/period/inning), on
   validation nights. Report game-clustered bootstrap intervals, not iid ones, because plays within a game share
   an outcome (Brill et al. 2024). Skip or soften S for a league whose calibration misses.
4. **Layer 2: does S predict the next window?** At each dead ball, rank-correlate S with the realized
   max |WP swing| (or big-play flag) in the next 3 min; also top-k hit rate ("top-1 game by S contains the
   biggest swing"). Compare against the current `closeness` bands and the crunch rows as baselines. Paired
   comparison by night (bootstrap over nights, not plays). Report mean and worst forward-chaining fold.
5. **Layer 3: routing outcomes** with the existing decision-record replay (`lab/offpolicy.py`,
   `lab/backtest.py`, `lab/oracle_window.py`): plays missed on entry, regret vs oracle, dead air.
   Hard gates unchanged: entries_mid_play stays 0; high-leverage mid-play leaves and dead air do not
   rise unless a measured reason is at the top of the PR (CLAUDE.md Rule #1).
6. **Cross-sport fairness check (the unproven part).** After normalization, compare per-league rates of
   "top-5% S" moments per game-hour and the realized swing given that flag. If NHL top-5% moments
   deliver much smaller realized swings than NFL top-5%, the common horizon is wrong; fix H or add a
   per-league calibration slope, on validation nights only.
7. **Leakage and drift.** `splits.leaks()` in CI; features must satisfy `splits.causal` (no data later than
   the decision time, with feed lag). Score fresh nights after the pin separately (`splits.fresh`).
   Retire any knob whose sign flips between folds.
8. **Taste ground truth.** `lab/worth.py` (nightly 1-5, non-inferiority by bootstrap over nights) stays
   evaluation only; do not train on it. Also keep "ping-pong" and switches-per-hour as guardrails.
9. **What would make me distrust a win:** gains concentrated in one league or one night, a worst fold
   below baseline, or an improvement that vanishes when S is computed with the lag the feeds really have.

## 4. $0 models and datasets

| Resource | What | Licence / caveat |
|---|---|---|
| nflverse / nflfastR play-by-play and WP/EP | NFL PBP back to 1999 with `wp`, `epa`; releases on `nflverse/nflverse-data`; models in CRAN `fastrmodels` (XGBoost `wp_model`, `wp_model_spread`) | Code MIT; data terms are the NFL's, no explicit data licence found. Use for offline fitting only; do not redistribute. |
| SportsDataverse (cfbfastR, hoopR, wehoop, hockeyR) | CFB, NBA, WNBA, NHL PBP wrappers | Package code MIT (cfbfastR, hoopR listed MIT); upstream ESPN/NHL/Hockey-Reference terms apply, check each. |
| Retrosheet | MLB event files since 1973 for RE24/WE tables and LI | Free; asks for a prominent credit line; the site also carries an all-rights-reserved notice, so confirm terms before redistributing derived tables. |
| ESPN `winprobability` feed (already used) | Live WP per play for the sports that carry it | Unofficial endpoint; already the repo's source (`sources/espn.py`). Stability is a risk, keep analytic fallbacks. |
| MLB Stats API win-probability data with a `leverageIndex` field | A third-party wrapper documents it; I could not confirm it in the official docs | UNVERIFIED location; check one live JSON response before depending on it. |
| Local eval archive (`eval-data-v1`, `scripts/fetch_eval_data.py`) | The repo's pinned nights for the splits | Internal. |

Not found at $0: an actively maintained open NHL in-game WP with code and a clear licence; a public NFL/NHL/MLB
minute-by-minute audience dataset; any tracking data for basketball EPV.

## 5. Bibliography (verification status in brackets)

1. Ely, J., Frankel, A., Kamenica, E. (2015). Suspense and Surprise. Journal of Political Economy 123(1):215-260. DOI 10.1086/677350. [verified: EconPapers record and abstract summary; full text not read]
2. Bizzozero, P., Flepp, R., Franck, E. (2016). The importance of suspense and surprise in entertainment demand: Evidence from Wimbledon. Journal of Economic Behavior & Organization. DOI 10.1016/j.jebo.2016.07.006. [verified bibliographic record via Crossref; abstract not retrieved]
3. Buraimo, B., Forrest, D., McHale, I. G., Tena, J. D. (2020). Unscripted Drama: Soccer Audience Response to Suspense, Surprise, and Shock. Economic Inquiry 58(2):881-896. DOI 10.1111/ecin.12874. [verified: Crossref record and abstract; Crossref dates it 2019 online]
4. Simonov, A., Ursu, R. M., Zheng, C. (2023). Suspense and Surprise in Media Product Design: Evidence from Twitch. Journal of Marketing Research 60(1). DOI 10.1177/00222437221108653; CEPR DP17353 (2022). [verified: abstract and index records; full text not read]
5. Kaplan, S. M. (undated working paper). Job-market paper on thrill (suspense and surprise) and skill in minute-level NBA television ratings, UC Berkeley ARE. URL https://are.berkeley.edu/sites/are.berkeley.edu/files/job-candidates/paper/Kaplan_JMP_CurrentVersion_0.pdf. [partly verified: description from a search summary; the file returned 404 on direct fetch; title not retrieved; treat as unpublished]
6. Lock, D., Nettleton, D. (2014). Using random forests to estimate win probability before each play of an NFL game. Journal of Quantitative Analysis in Sports 10(2):197-205. DOI 10.1515/jqas-2013-0100. (old) [verified: Iowa State and EconPapers records]
7. Baldwin, B. et al. (2020). nflfastR EP, WP and CP models (post, opensourcefootball.com), and CRAN package fastrmodels (XGBoost wp_model). [verified: page read for features and calibration numbers; WP training-season range not stated there]
8. Brill, R. S., Yurko, R., Wyner, A. J. (2024; v5 Aug 2025). Exploring the Difficulty of Estimating Win Probability: A Simulation Study. arXiv:2406.16171; accepted to JQAS. [verified: arXiv abstract page]
9. Pipping-Gamón, J., Wyner, A. J. (2026). The Blown Lead Paradox: A Pathwise Calibration Benchmark for Win Probability Forecasts. arXiv:2601.18774. [verified: arXiv abstract page]
10. Beuoy, M. (2 Mar 2026). Quantifying Excitement (KL-divergence excitement index). inpredictable, https://inpredictable.substack.com/p/quantifying-excitement. [verified: page read; blog, not peer reviewed]
11. Benz, L. Game Excitement Index Part II. Yale Sports Analytics, https://sports.sites.yale.edu/node/566. [partly verified: search summary only; blog]
12. Vecer, J., Ichiba, T., Laudanovic, M. On Probabilistic Excitement of Sports Games. https://www.stat.berkeley.edu/~aldous/157/Papers/vecer.pdf. [metadata only: title/authors/keywords; text unreadable; year from file date only (about 2011); older than the scope]
13. Ekstrøm, C. T., Jensen, A. K. (2020). Having a Ball: evaluating scoring streaks and game excitement using in-match trend estimation. arXiv:2012.11915. [verified: arXiv abstract page; 1,143 NBA games 2019-20]
14. Tango, T. (30 Jul 2003). Leverage Index post, https://tangotiger.net/archives/stud0069.shtml; and Tango, Lichtman & Dolphin, The Book: Playing the Percentages in Baseball (2007). (old) [post verified: average LI set to 1.0; the book not opened]
15. FanGraphs Library: Leverage Index (LI). https://library.fangraphs.com/misc/li. [verified: low below 0.85, medium 0.85-2.0, high above 2.0, up to about 10]
16. Pettigrew, S. Assessing the Offensive Productivity of NHL Players Using In-Game Win Probabilities. MIT Sloan Sports Analytics Conference. [verified: abstract; includes power-play states; the Sloan page shows no clear year, one listing implies 2015]
17. Cervone, D., D'Amour, A., Bornn, L., Goldsberry, K. (2016). A Multiresolution Stochastic Process Model for Predicting Basketball Possession Outcomes. JASA 111(514):585-599. DOI 10.1080/01621459.2016.1141685. (old) [verified: EconPapers/abstract]
18. Itti, L., Baldi, P. (2009). Bayesian surprise attracts human attention. Vision Research 49(10):1295-1306. DOI 10.1016/j.visres.2008.09.007. (old) [verified: USC iLab and PMC records]
19. Alaybek, B. et al. (2022). All's well that ends (and peaks) well? A meta-analysis of the peak-end rule and duration neglect. Organizational Behavior and Human Decision Processes 170:104149. DOI 10.1016/j.obhdp.2022.104149. [verified: record via search; r = 0.581 for the peak-end effect on retrospective evaluations]
20. Kleinberg, J., Mullainathan, S., Raghavan, M. (2024). The Challenge of Understanding What Users Want: Inconsistent Preferences and Engagement Optimization. Management Science 70(9):6336-6355. DOI 10.1287/mnsc.2022.03683; arXiv:2202.11776. [verified: arXiv page and EconPapers]
21. "Tale of Three Cities: Intra-Game Ratings in Winning, Losing, and Neutral Markets" (FIT Publishing). https://fitpublishing.com/node/1112. [UNVERIFIED beyond a search summary: the page returned 404; authors, journal and year not retrieved]
22. Retrosheet notice and download terms, https://www.retrosheet.org/notice.txt and https://www.retrosheet.org/downloads/csvdownloads.html. [verified via search; terms ambiguous, see section 4]
23. nflverse / nflfastR repositories and data releases, https://github.com/nflverse/nflfastR and https://github.com/nflverse/nflverse-data/releases; SportsDataverse, https://github.com/SportsDataverse. [verified by search; licence caveats in section 4]

Considered and NOT cited because I could not verify them: Solow (2024) on NBA suspense and audiences
(cited in the playbook); a "Brill/Lo excitement" paper (no match; the Brill hit is the WP-estimation paper
above); Wilbert-Lampen, Rankin and other neuroscience sources are in the playbook and were not re-checked here.

## 6. Next action (under two minutes)

Open `redzone/lab/leverage.py` and `redzone/scoring/engine.py:52`, confirm finding 0.1 by calling
`high_leverage(League.CFB, <a late close play>)`; if it returns False the grader is blind to CFB/CBB.
