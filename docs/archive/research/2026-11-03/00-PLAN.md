# Model capabilities: research synthesis and ranked plan (Nov 3, 2026)

Five parallel research passes (Fable, max effort) cover:
1. inputs and sources
2. capabilities and tasks
3. switch timing
4. architecture and distillation
5. evaluation and the learning loop

The full reports sit next to this file (01-05). This page is the merged, ranked work queue.

## The one finding that changes everything

**The live app has about 0 s of data lead; the backtests assumed about 35 s** (report 03).
- With polling, ESPN runs 35-49 s behind the play (median: NBA 38, CBB 35, NFL 45, NHL 49 s, measured
  from `data/recordings`). That is about the same as the stream delay (~45 s).
- The backtests use `feed_lag=8`, so every "0 mid-play cuts" scorecard had roughly 30 s more warning than
  the live app really gets.
- FastCast (ESPN push) recovers 15-25 s of that. So do league-origin feeds (NHL `api-web.nhle.com`,
  `cdn.nba.com` liveData, MLB `feed/live`) and screen signals (Prime score-bug/clock OCR, which has no
  delay at all).

**First job (about 1 h):** expose `feed_lag` in `scripts/eval.py` / `eval_sports.py`, re-run with the
measured per-league lags, and treat that as the honest baseline from now on.

## Architecture decision (reports 02 + 04 agree)

**Split the brain.**
- **Timing** (stoppage onset, length, restart, safe-to-cut, jump-or-stay EV) belongs to calibrated
  GBM/hazard models plus league rules: about 0.5 ms and about 2.4 MB, exported like Brain v3.
- **The 1.7B LLM writes words only:** per-game storyline/memory, "why I switched", jump rationale, and
  watchdog novel-error classification. It runs async, never inside the 3 s tick, and degrades to
  templates.
- Keep Qwen3-1.7B-4bit (bake off Qwen3.5-2B text-only and LFM2.5-1.2B). Total RSS is about 1.4-1.6 GB.
- Derive confidence from GBM probability plus decision-token logprob, never from self-report.

## Ranked work queue

### A. Timing: tonight and tomorrow
1. **Honest baseline:** run the backtests with the measured per-league feed lag (above).
2. **FastCast on for every league,** with per-source lag logged. Verify the NFL/NHL/MLB topics. Use
   league-origin feeds where faster:
   - NHL: `landing` `clock.running` / `inIntermission`. The flip back to running IS the faceoff. Use the
     stoppage `reason` codes too.
   - NBA: `timeActual` in milliseconds.
   - MLB: pitch `startTime` / `endTime` and `inningState`.
3. **One shared restart countdown** that every decision reads: `len` / `entry` q10/q50/q90 plus
   `safe_entry`.
   - Replace `jump.py`'s league-wide p10 and the fake 0.6x/1.6x quantiles.
   - Use Kaplan-Meier tables first, then a discrete-time boosted hazard (LightGBM), then conformal lower
     bounds (CQR plus adaptive conformal across nights).
   - Condition on elapsed time. For example, an NFL TV timeout at 150 s elapsed has 32 s left (p10 14 s).
4. **Rule features:**
   - The broadcast market (national vs local). NBA timeouts run 2:45 vs 3:15. MLB breaks run 2:15
     local, 2:40 national and 3:10 in the playoffs.
   - The full TV-timeout rules: NBA Rule 5, NHL's 7 exceptions, NCAA's "first 2nd-half team timeout
     becomes the media timeout", NFL's 4 breaks per quarter, the CFB 2-minute timeout, MLB timers.
   - NHL: ESPN labels only about a third of TV timeouts, so infer the rest from window length.
5. **Per-(app, channel, tune) stream-delay Kalman filter.** Its fast measurement source is the OCR'd game
   clock on Prime versus the feed clock. Use the delay quantiles asymmetrically.
6. **Commit rule:** optimal stopping. Cut or enter only when `P_safe >= 99.5%` over the about 3.5 s
   flip-plus-settle window. Use the measured SmartThings switch (median 1.98 s) as the trip cost, not the
   HOME 5-50 s model. The 2 s flip is what makes "switch more often" safe.
7. **New scorecard lines** (`cutgrade` / `sportsgrade`):
   - landing- and leave-offset histograms
   - % of landings in dead time
   - mid-play per 100 switches with a Wilson CI (about 600 clean switches are needed to prove < 0.5%)
   - missed big plays
   - safe windows found vs taken
8. **Hidden-stick prediction:** `P(next switch target in 180 s)`, so every switch is a pre-tuned flip.

### B. Evaluation honesty: before any gate claim
9. **The night is the unit of evidence.**
   - Use cluster-bootstrap CIs over nights.
   - PASS needs ≥8 nights, ≥60 decisions in the slice, and the lower bound ≥95%; otherwise the result is
     PENDING.
   - Zero failures in N decisions bounds the failure rate at ≤3/N (rule of three).
   - Today, NHL "97.8%" has a 93.7% lower bound, and NBA "50% (10/20)" is noise.
10. **Fix the leaks:**
    - The e2e night replays 2 locked-set games: NHL 401892626 and NBA 401909906.
    - `espn.parse_summary` fills `g.leaders` from the final summary even with `upto`.
    - The constant 8 s lag / 45 s delay make outcomes known too early.
    - Add `scripts/leak_check.py`, specified in report 05.
11. **Split the reasoner gate:** gold encodes the future. Use rule conformance ≥99%, calibration, ON/OFF
    scorecard non-inferiority, and a conformal-risk-controlled abstain for ≤0.5% unsafe leaves.
12. **Realistic night lab:** lag jitter, late plays, missing wall clock, drops, channel spread, resyncs,
    all fitted from real traces. Run many nights × seeds and diff against golden traces.

### C. Capabilities: next
13. **Calibrated "safe to cut/enter now" with a runtime abstain:** an unsure answer counts as unsafe and
    falls back to rules. Today `Reasoner.read` ignores `confidence`.
14. **Next big play** (what it is plus ETA quantiles).
15. **Stakes layer:**
    - standings, clinch/elimination, rivalry, rankings
    - bet-decisive-next, from ESPN `probabilities` (`spreadCoverProbHome`, `totalOverProb`) and odds
      movement, both free
    - NFL/NHL key heads need drive-tempo and power-play-clock features (AUC is 0.68 and 0.58 today)
16. **Reel ranker:** play-by-play value plus Nick's taps, ordered chronologically with the best last.
    More clip sources: NHL `highlightClipSharingUrl` and MLB `/game/{pk}/content`. ESPN MP4s expire in
    48 h.
17. **Watchdog:** grow from 5 to 12 guarded actions and cover all 13 `KIND_HELP` kinds. Track fix success
    rate.
18. **Bugs found:**
    - `breaks.blend()` 50/50 hurts calibration (ECE 0.12-0.17 vs ≤0.10 for the brain alone).
    - There are two JSON parsers.
    - Switch-cost constants disagree between `default_config.yaml` and the Kaggle eval config.

### D. Training pipeline (Kaggle, free)
19. **Student recipe:**
    - LoRA rank 16-32, alpha 32, all 28 layers including MLP, LR about 2e-4.
    - fp16 frozen base plus fp32 LoRA and GradScaler, with SDPA. This also fixes the V4 OOM properly.
    - Keep `pip uninstall torchao`. Run vLLM in its own venv, set `VLLM_ATTENTION_BACKEND=TRITON_ATTN`, and
      keep the raw bf16 reader.
20. **Teacher throughput** (T4 is prefill-bound):
    - 32B TP=2 on the hardest 10%, 14B-AWQ ×2 data-parallel, and 8B-AWQ ×2 on the tail.
    - Pack 3-4 moments per prompt and compact the brief.
    - Expected yield is about 300-800k teacher-worded rows a week. Numeric labels come free from hindsight
      (play-by-play).
21. **Then soft-target KD** on decision tokens (logprobs=20) plus 3-5 on-policy distillation rounds.
22. **Historical label factory** with real wall-clock times:
    - nflfastR `time_of_day` (2011+)
    - hoopR NBA (2002+) and MBB (2006+)
    - cfbfastR (2014+)
    - fastRhockey, joined to the archived ESPN wall clock
    - MLB pitch timestamps (2008+)
    - CFBD's Academic tier is free with a unc.edu address (Nick signs up himself).

### E. Learning loop from Nick's nights
23. **Decision records:**
    - Keep one record per decision, labelled at 5 AM from what really happened.
    - Add 👍/👎 on the phone tiles, feeding the existing `feedback` table.
    - Treat overrides and skips as weak labels.
    - Send the most uncertain moments to the teacher (active learning).
    - Run a daily drift report and a weekly Kaggle retrain.
24. **Shadow mode:** a new model decides silently next to the live one and is promoted only after 7/7
    paired nights (or 10 of 12). SPRT handles auto-rollback.
25. **BRAIN:** the auto-fix opens `eval-regression` issues on dev-test data only.
    - The locked gate sits behind a GitHub approval.
    - Every fix needs a replay regression test.
    - Flipping a flag takes two keys.
    - Stop after two failed PRs per gate row.

## Legal and product notes (report 01)
- **Not free or not legal:**
  - CFBD live plays (Patreon)
  - NCAA LiveStats/Genius
  - Sportradar
  - NFL NGS
  - scraping sportsbooks
  - captions or frames from DRM streams
- **ESPN automated access** is personal-use gray territory. A product would need a data license.
