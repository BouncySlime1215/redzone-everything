# Cycle 05 plan (Start Planner), Wed Nov 4, 19:55 to 21:25 UTC (90-min clock)

**New loop rules (Nick, 2:45 PM ET):** cycles are 90 minutes on a clock. At T+90 the End Planner reviews
whatever has landed, and the Start Planner writes the next plan within 10 minutes. **No idle threads:** each
lane below has 3+ ranked tasks. When a task finishes or blocks (CI, Kaggle, hardware), go straight to the
next one; waiting on CI is not work. **Kaggle goes to the overseer** (factory pack cdf86ef is launching now).
Lab hardware (TV + sticks) is on all day, so use it for anything that needs real timing.
**From:** [cycle 04 review](../04/review.md) (f7e53bb: % of oracle 64.8 dev / 59.1 locked, unchanged, because no
candidate change landed; nothing promoted), Nick's live-lab sync findings (12:10 PM) and the 3:05 PM overseer queue (backlog.md, top).
**Rules as always:** graded on locked unseen nights, flags OFF until they pass, mid_play 0, no signal from
Nick, North Star delta (dev + locked, live + candidate) and a RAM/CPU line in every PR.

**Where we are (dev, live column, post-#121 gates):**
- Big live: NFL 25.2 / NBA 67.6 / NHL 41.4, against ≥95% of oracle.
- Seen within 3 min: 49.7 (bar 99).
- Landings in dead time: 97.5 (bar 98).
- press_to_tv: 8.75 s (bar 3).
- Playbook coverage: 33/78.
- Candidate (lane 2 stack #116 → #125 → #131/#132): mid_play 0, NFL big live ~63 dev / 60 locked, σ p50 3 s.
- Real-hardware risk: the measured YTTV seek makes today's entries mid-play (locked NFL 12, NHL 14) until #132 lands.

**Coverage push this cycle:** 33 → **≥ 50** marked playbook tests. Each lane owns the uncovered entries
listed under it; a marked night-lab test counts even when the behaviour behind it stays flag-off.

## ① Noggin training
1. **Reflex on existing shards while the factory runs:** train the Pick/possession policy (#129) on
   whatever shards exist, forward CV by whole nights. Report rows per league and the decider-level result,
   not only the rollout. Bar: NBA crunch live with the policy ≥ the rule-based possession default on dev
   and locked, mid_play 0, % of oracle up.
2. **Oracle self-play eval:** a harness that grades any policy against the oracle on synthetic nights
   (factory self-play) as well as archived ones, with a pct_of_oracle row per source. This is the eval
   the billions of rows will feed.
3. **Mind salvage (#138) to green**, V7 support only. Then SAFE-04 (ensemble + abstain), with a marked test.
- Coverage: SAFE-04.

## ② Noggin logic / sync
1. **Land the rule #1 seek stack:** #132 must follow #133 (take #132's `_rewind`: one wake press, not two),
   plus the crunch pre-cue recovery (the ~4-pt locked crunch cost of #132). Bar: mid_play 0 with the measured
   seek on dev and locked; crunch locked back to its pre-#132 level.
2. **Promotion blocker (cycle 04 review):** the candidate passes every rule #1 gate on locked, but it is
   worse than live on NHL crunch (crunch plays aired during a catch-up replay 2 → 10; crunch live 89 → 76)
   and on 2 NFL your-team big plays. Never start a catch-up with NHL crunch due inside it, and fix the 2
   NFL plays. Bar: release_gate.py on locked has no candidate line worse than live.
3. **MediaSession content anchor** (in flight): the burst offset (~0.3 s) goes into the per-stick filter.
   Bar: σ p50 below 3 s with coverage ≥ 95%; NFL/NHL big live and GAME-04 re-graded.
4. **Self-calibration, sync half (#140)** to green: `data/calib/YYYY-MM-DD.json` `sync` section, 3-MAD guard.
5. **NHL whistle detection** (#131 follow-up), then the entry-rewind extra-behind.
- Coverage: SAFE-02, SAFE-03, SAFE-05, SYNC-01..03, GAME-07, GAME-08, GAME-11..14.

## ③ Performance + controls
1. **GO NOW ≤ 3 s press-to-TV on real hardware** (lab is on): measure each segment (phone → Mac → stick → TV)
   with the pre-tune and cold paths split; fix the largest app-side segment. Bar: a real p95 row in northstar.
2. **#136 to green:** (c) timing half (same calib file, `timing` section), (d) pre-game warm-up, (e) buffer
   watchdog STK-06 with the `buffer_lost` fault.
3. **Stick assignment (overseer b, reframed):** with two sticks every new tune goes to the hidden stick, so
   "hottest game on the 4K Plus" can't hold at zero extra flips. The levers are the night's first assignment
   and a standby planner that weighs each stick's tune speed (4K Plus pre-tunes 2.33 s vs 3.74 s p50). Build
   the per-stick standby model in the backtest first so the delta is measurable. Flag `decider.stick_assign.v2`.
4. **LOCK IN hold** end to end on real hardware (BTN-04 release, BTN-06 phone offline).
- Coverage: BTN-04, BTN-06, STK-01, STK-02, TV-01, MAC-02..05.

## ④ App + integration hardening
1. **Night health report (#137)** to green, then its follow-ups: misses linked to the replay viewer, and
   PENDING handling for a night with no games.
2. **#139 LAN redaction** merge; then the night-lab fault kinds for the FEED/NET/APP entries.
3. **Locked switch to eval-data v3 on Fri Nov 6:** prep it now so it's a one-line flip.
- Coverage: FEED-02, FEED-04..07, NET-01..03, APP-01..07, MAC-01, MAC-08, SLATE-01, SLATE-02.

## ⑤ Features (reopened; lowest, never preempts ①-④)
1. **Teacher decision-record export:** one JSONL per night, objective outcomes only, no taps.
2. **Replay viewer oracle overlay:** the oracle's schedule next to what we did, plus missed-big-play markers.
3. North Star trend page from docs/northstar-history.jsonl (read-only).
4. **"I missed that"** records a catch-up reel request for the next dead window (3-min rule); reels stay OFF.
(Button states are already merged as #96.)

## Outside research, one focused lookup per lane (Start Planner, 20:05 UTC)
- **① DAgger, not plain behavior cloning.** Our oracle is a DP over the archived night, so it can label
  *any* state, including the off-oracle states our own policy drifts into. Train the possession policy
  (#129) with DAgger rounds: roll out the policy, ask the oracle what it would do at each visited
  state, aggregate, retrain. This fixes the covariate shift that plain imitation leaves, which is the
  usual reason a policy that "beats the rule in rollout" doesn't beat it inside the decider. Report the
  decider-level result per round.
  Sources: [Ross et al., DAgger (arXiv 1011.0686)](https://ar5iv.labs.arxiv.org/html/1011.0686),
  [Imitation Learning survey (arXiv 2106.12177)](https://arxiv.org/pdf/2106.12177).
- **② NHL TV timeouts are scheduled.** There are three per period, at the first stoppage after the 6:00,
  10:00 and 14:00 elapsed marks (not after an icing; it moves to the next stoppage; verify the exact exceptions in the rulebook). That
  gives a predictable ~2-min dead window per segment for whistle detection, the replay-and-resume
  repay and the NHL replay window. Faceoff timing after icing is regulated (no line change for the
  icing team). Encode both as priors in NHL whistle detection.
  Sources: [NHL rules](https://en.wikipedia.org/wiki/National_Hockey_League_rules),
  [NHL rulebook 2021-22](https://media.nhl.com/site/asset/public/ext/2021-22/2021-22Rules.pdf),
  [the hurry-up faceoff](https://thehockeywriters.com/the-death-of-the-hurry-up-faceoff/).
- **③ ADBTuner / ah4c (Channels DVR community)** tune YouTube TV channels on Fire TV and Android TV over
  adb with deep links (`am start` with a channel intent) instead of key sequences. Read their YTTV tune
  recipes and measured tune times before the GO NOW probe (#145). A deep-link tune could cut the
  cold path well under today's 8.5 s. Read-only research; nothing is installed on the Mac.
  Sources: [ADBTuner thread](https://community.getchannels.com/t/adbtuner-a-channel-tuning-application-for-networked-google-tv-android-tv-devices/36822/251),
  [ah4c thread](https://community.getchannels.com/t/androidhdmi-for-channels-ah4c-a-virtual-channel-tuner-using-hdmi-encoder-s-streaming-stick-s/38976/300).
- **④ espnscrapeR / sportsdataverse** document ESPN's free pbp endpoints, including the per-play
  wallclock, for NFL and CFB. Use them as the reference for the feed-fault tests (FEED-0x) and the
  night report's schema checks.
  Sources: [espnscrapeR get_nfl_pbp](https://rdrr.io/github/jthomasmock/espnscrapeR/man/get_nfl_pbp.html),
  [cfbfastR news](https://sportsdataverse.r-universe.dev/cfbfastR/NEWS).
- **⑤** No outside lookup needed (UI over our own data).

**Lab window:** the TV and sticks belong to lane ③ from 3:10 to 3:45 PM ET for the GO NOW probe (#145).
**Promotion bar:** the question is with Nick. Until his answer comes, every lane keeps producing candidates.

## Integrator (at T+0 and T+90)
- **Merge order:** #133 then #132 (never release one alone) → #116/#125 → #139 → #137/#136/#140 → lane 1.
- **T+90 (21:15 UTC review):** review cycle 05 with the coverage count and every lane's delta; the Start Planner writes cycle 06
  within 10 min.
