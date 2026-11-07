# OVERSEER HANDOFF — Thu Nov 5, 2026, ~10:10 AM ET (account switch today)

Read this first if you are the overseer on Nick's other account. Then docs/handoff.md (STATUS lines), docs/sync/*, docs/kaggle/*.

## Who / what
- Nick (UNC student) = owner. Overseer = director of RedZone Everything (self-hosted Mac app, FastAPI :8778, launchd com.redzone.everything)
  running his Samsung TV like NFL RedZone with two Fire TV sticks (.150 4K Plus = HDMI1, .181 4K 2023 = HDMI2) + SmartThings input switching.
- Work runs in the claude.ai project "Training model for redzone" (coordinator + lanes + data division + integrator):
  https://claude.ai/code/project/chan_011rPfhndEhhnAPuwfcdBQK8 . The overseer owns Colab/Kaggle training and sync research.
- Live checkout ~/Documents/Codex/2026-11-02/task-2/redzone is DETACHED on the deployed release: NEVER commit there; commit via a
  separate worktree of codex/resume-redzone. Commits end "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>".
- Nick prefs: ADHD mode (lead with action, short, numbered, plain words); NBA first then NFL; he is "done directing" — act autonomously.

## TONIGHT (deadline 6:30 PM ET; Nick: "get ALL of it done today, unlimited lanes")
Games: NBA ~7 PM; TNF JAX@BAL 8:15 PM (Prime); MIA@GSW 10 PM NBA TV (the only national NBA game on YouTube TV).
Coordinator ordered (10:00-10:05 AM) to run ~20-30 agent threads (builder+tester+integrator per item), readiness tables 12/3/6 PM:
- T1 time-shift BUFFER in SHADOW on every game (timing.behind_live_s; no screen change) + stay/leave logging with full game context.
- T2 replay sim of all recorded nights as if buffered + per-league buffer sweep (45/60/75/90 s).
- T3 windowed-hindsight oracle teacher (best schedule using only what the buffer reveals; exact optimizer inside window; learned beyond-window value).
- T4 YouTube TV sync test bank (58 tests, docs/sync/yttv_tests, run order ui_10 → ui_07 → ads_02 → truth_10 → ui_08) on MIA@GSW.
- T5 TNF: hidden Prime stick at live edge reads the scorebug (Nick-approved) = fastest NFL feed; race vs espn_fastcast/summary + Kalshi.
- T6 faster feeds: nba_cdn primary polled 1 s; NHL api-web; MLB statsapi.
- T7 SEEK_TO helper (both apps advertise SEEK_TO) + rewind/ads reliability per channel; hidden stick only; key fallback.
- T8 Watch Value v2 spec (PhD-level, multi-discipline, real attention labels) + game-context stay/leave model.
- T9 RC8 "buffer ON" release candidate, gated: Nick flips it only if tonight's shadow beats live with 0 mid-play entries.
- T10 models: ladder host-RAM fix, speedrun2 re-pin to 8b2ea86, EXP-103 grader fix.
Overseer job tonight: every 30 min check coordinator + Colab, relaunch runs when pins land, grade results, push STATUS, never touch the visible stick.

## NICK DECISIONS (log-worthy)
- 9:10 AM: TIME-SHIFT BUFFER is the main architecture (60-90 s behind live; Nick cap 90 s); the brain = editor. Buffer shrinks as feeds/sync
  improve but NEVER reaches 0 (adaptive per league: feed p95 + sync error + tune/seek + margin; hard floor ~10-15 s).
- Watch Value = the unique value proposition: frontier, multi-discipline (neuro/psych/socio/physiology/math/ML), trained on REAL attention data;
  guardrail "worth it", no dark patterns, neutral words in public docs; Nick's own viewing/buttons are never labels.
- Stay/leave from FULL game context + consequence of each play on the rest of the game (WP, future suspense, stakes, narrative).
- 3:30 AM: code MAY screenshot the Prime picture to read the scorebug (numbers only, never frames). YouTube TV video is DRM-black: never bypass.
- Plan B data (personal use): ESPN-derived + restrictive public APIs allowed for training; never published; tag personal-use.
- Gmail: filter trashes notifications@github.com mail containing "BouncySlime1215/redzone-everything" (verification mail untouched).
- NHL re-enabled 9:50 AM (was off "just last night").

## SYNC — what we KNOW (measured, Nov 5 early AM; details docs/sync/2026-11-05-yttv-live-edge-tests.md)
- Stick clocks within 0.06-0.09 s of the Mac (sd ~1 ms).
- media_session position must be extrapolated: pos_now = position + (uptime_ms - updated)*speed (YTTV refreshes only on events).
- YTTV: every deep-link tune lands at a fixed position per channel per stick (ESPN 46,803.0 ±0.3 s on .181; ~46,790 on .150; TNT 46,792.7 ±0.7)
  → exact behind-live meter (30 s pause read +30.01 s); re-link to live 2.5 s; tune 0.9-4.1 s; YTTV DAI ad spots = media_session updates every 10-16 s.
- YTTV absolute vs real game: ~20-30 s (one break: ad pod end vs nba_cdn restart). App prior 29 s.
- YTTV WEB player (tv.youtube.com, Mac Chrome, Nick signed in): #movie_player.getMediaReferenceTime() = ingest-clock epoch of the frame;
  ESPN live head = 23.2 s behind, steady to 0.1 s. Bridge web↔stick = tonight's tests.
- YTTV TV app ⋮ player menu: Audio options | Auto-pause | Broadcast delay (.181 = Moderate) | Send feedback | Stats for nerds (Off). Scrubber shows air
  time to the minute. CAREFUL: LEFT/RIGHT on the scrubber seeks. A "Who's watching?" profile picker can appear → pick Nick.
  Broadcast delay Default vs Decreased (~9-10 s) likely explains the ~10 s landing gap between sticks; Nick hasn't OK'd changing it.
- PRIME: media_session position IS an epoch (ms) = frame clock; screen 6.4 s behind Amazon's clock (19 readings); scorebug clock samples: screen
  ~16 s behind the arena → Amazon frame clock ≈ arena − 9.6 s. Stick .181 Prime once reported a 24 h counter instead (content type?) — test on TNF.
- Feed lags (median/p90): NBA nba_cdn 12.1/53 s, espn_fastcast 14.8/49, espn_summary 37.6/73; NHL espn_fastcast 33/102, summary 48.5/135.
- Scripts: docs/sync/scripts (edge_probe, t1_retune, t4_behind, burst_align, codec_logger, overnight). Diamond plan: docs/sync/2026-11-05-diamond-plan.md.

## TRAINING / DATA (reports in docs/kaggle)
- EXP-109 speedrun (A100): recipe E = Muon+WSD+modded-nanoGPT bundle+torch.compile → 2.07x faster to 0.334, best every league; compile alone 1.5x;
  Muon or bundle alone hurt. One seed — seed check (speedrun2) was INCONCLUSIVE (ran pre-fix pack; EXP-110 FIM-PP exit 1). Re-pin pending.
- EXP-107 ladder run 1 FAILED (sync.push AttributeError at checkpoint; 60m/150m CUDA OOM; 18m memorizing at 5.5 epochs). Fixed pack 8b2ea86 /
  notebook ba227738 relaunched 8:10 AM on A100 High-RAM; kernel dropped 9:15 AM (~1 h in) → tab "Resuming execution"; suspect host-RAM OOM.
  Check Manage sessions; if dead, Run all resumes from 30-min checkpoints.
- EXP-103 world model (18.6M, valid 0.334, still falling) died twice at grading (GPU/host memory). Grader fix pending (lane 1).
- EXP-086 Teacher plan B: NHL Teacher lock calls +12.7 pts over head; CBB "leak" = label tracks clock/crunch (Nick overruled "false alarm").
- EXP-1402 commentary: 66,048 causal per-play answers; importance AUC NBA 0.955 / CBB 0.957 SUSPECT (same leak pattern), NFL/CFB/MLB ~0.58, NHL 0.54.
  Output handed privately to crew E (never in the public repo).
- Kaggle factory v1: 1.62B rows (NBA 1.06M moments + 969M self-play...). Bugs: CBB every season, NHL 2015-19 no wallclock. v2 running on c2215ad.
- Kaggle TPU ladder notebook "redzone-nfm-tpu-ladder" imported, BLOCKED on Nick's one-time Kaggle identity check (Persona).
- Colab CU ≈ 390-400 left (Pro+ ~500/mo); keep ~100 reserve.
- Deep research report: reports/Noggin training advances 2026.md (local). Free compute catalog: docs/compute/free-compute.md (local).

## OPEN ASKS FOR NICK
Kaggle ID check (TPU); purge ESPN-cache history (commit 0a470d25 in public repo; removed in c846da6e) yes/no; Broadcast Delay → Decreased on both
sticks yes/no; keys: HF_TOKEN, YOUTUBE_API_KEY, TWITCH_CLIENT_ID/SECRET, CollegeFootballData; professor email (docs/compute) for ACCESS/NAIRR.

## HARD RULES
Never join mid-play (Rule #1). Never Peacock. $0 unless Nick OKs. Never touch secrets or create accounts. No DRM bypass. Hidden stick only while Nick
watches. Weekly usage hard stop 88% (now 63%). Repo is PUBLIC: no raw ESPN/web data, no frames, no personal data. Mac stays light (8 GB).

---
## UPDATE ~1:45 PM ET (everything since 10:10 AM)

### Priorities as Nick set them today (newest wins)
1. **Watch Value / prioritization = #1 and the UVP: "which game to watch, when."** Goal: "holy sh*t, the greatest sports show I've ever watched."
   With the delay we master switching; now master PRIORITIZING. Most threads go here.
2. **Data for it, MASSIVE, newest-first** (12:10 PM): tonight's games + this season first → 2025, 2024, 2023 → older to 1999 (low weight).
   Retrain incrementally as slices land; tonight runs the best version available by 6:30 PM (minimum: this + last season).
   - 4 key-free attention sources (GO): Wikipedia pageview dumps, win-probability swings for every archived play, market/betting volume
     history, published TV ratings → joined to plays → "attention label" table (target millions of moments) → train Watch Value v1.
   - **Game-context corpus** (crew H): every NFL game since 1999 + NBA archive: previews, recaps, stakes, streaks, records, rivalries,
     weekly team reports + OFFSEASON (trades, signings, coaching, injuries, expectations). Wikipedia dumps + filtered FineWeb/CC.
     Derived numbers per (team, week) and per game → Game Importance + stay/leave. RAW TEXT PRIVATE (Kaggle) ONLY.
   - **Daily pre-game report for EVERY upcoming game** (built each morning + 1 h before start): stakes/clinch scenarios, storylines,
     rivalry/revenge, streaks, milestones, star matchups, injuries/returns, coaching hot seat, national-TV slot, weather (NFL), line
     movement + public betting %, fantasy relevance; **ROOKIES**: draft slot, draft capital spent (trade-ups/picks given), college hype;
     **FAN SENTIMENT of each team's OWN fans** (team subreddits via official API when Nick's key lands; fan sites/forums within terms;
     Wikipedia edit/pageview surges). Same features backfilled for history.
   - **Stay/leave from FULL game context**: score/time, WP trajectory, leverage, momentum, comeback odds, timeouts/fouls, stakes; and the
     consequence of each play on the rest of the game (ΔWP, Δ future suspense, Δ stakes, Δ narrative); world-model rollouts beyond the
     buffer; hysteresis; never leave high-leverage.
3. Tonight-critical (keep at minimum): T1 buffer SHADOW + stay/leave logging, T4 YTTV tests (MIA@GSW 10 PM), T5 TNF Prime scorebug feed,
   T9 RC8 buffer-ON candidate (Nick flips only if shadow beats live with 0 mid-play). T6 faster feeds = one small thread.
4. Paused until after tonight: T10 models (ladder/speedrun2/EXP-103), extra T2/T3 rounds.

### Budget (critical)
- Weekly usage resets **Mon Nov 10, 8 PM ET**. Hard stop 88%. Burn at full scale (20-30 threads) was ~7%/h → would freeze everything
  (tonight AND NFL Sunday). Coordinator capped at **~2%/h, ≤6 threads, scripts on Kaggle/Actions do all data pulling**, target ≤76% by
  6:30 PM. Usage: 63% (10:00) → 67% (10:45) → 71% (12:40) → 72% (1:40).
- Nick asked (1:40 PM) to "go full go all day"; overseer recommended staying throttled (full speed ≈ hitting 88% by ~3 PM). His call.

### Model/compute state
- Lane 1 fixed the ladder host-RAM issue: kaggle-inputs **811688b2** (pack 3134544, RSS logging). Old ladder tab (ba227738) stuck
  "Resuming execution" since 9:15 → delete that runtime in Manage sessions, relaunch colab/nfm_a100.ipynb at blob/811688b2 (A100 High-RAM)
  — but Watch Value v1 training gets the A100 first.
- Colab CU ≈ 380-390 left. Stale Colab tabs behind "Leave site?" dialogs can be closed (runtimes already deleted).

### Things the overseer must NOT do (Nick asked; declined)
- Create accounts / log in / generate or paste API keys (hard rule, even for "throwaway" keys). Nick has 2-min steps:
  YouTube Data API key (console.cloud.google.com → YouTube Data API v3 → API key → GitHub secret YOUTUBE_API_KEY); Reddit app
  (reddit.com/prefs/apps, "script") → secrets; Twitch (dev.twitch.tv). These unlock highlight views, own-fan sentiment, Twitch spikes.
- DRM bypass of YouTube TV video ("local backdoor") — declined; legal routes used instead (web-player clock, UI, ad bursts).

### Other changes today
- Gmail (nmatta1215@gmail.com): filter trashes notifications@github.com mail containing "BouncySlime1215/redzone-everything" (+ existing
  ~300 trashed, recoverable 30 days); GitHub verification mail untouched.
- Mac volume muted at Nick's request (~7:30 AM).
- NHL re-enabled 9:50 AM.

### Readiness tables
Coordinator posts at 12, 3, 6 PM (it said it sends them to Nick directly). Overseer: every :07/:37 check usage + coordinator + Colab,
push STATUS, keep the 88% stop in mind.

## Update Nov 7, 3:15 AM: YouTube TV and Prime can run behind live

Nick's plan: (1) finish hardware: flip, ad breaks, eyes on the picture; (2) wire join, verify, fallback into the app and shadow on real games; (3) the brain, heavily trained; (4) settings. Goal now: join a chosen spot accurately. Plays seen is the model's job later.

- **YouTube TV:** overlay scrub (CENTER, LEFT x N at 15 s, CENTER). 71 of 72 across 8 channels, both sticks; 31-min live hold. Join error about -3 s, +-3 s; targets in 15 s steps. Pause does not hold. Rewind keys don't seek. media_session is wrong after a seek; the overlay label is the truth. Same-channel deep link does not return to live; go through another channel.
- **Prime:** MEDIA_PAUSE then MEDIA_PLAY holds; lands within 1-2 s; timecode is the truth. Rewind overshoots. Detail page needs a second press to start.
- **Delay:** about 25 s +-3 on NBA TV, FS1, ESPN2, from the label vs scheduled start.
- **Mac:** dev build b490ab86 deployed Nov 7 2:34 AM with Nick's OK, healthy, flags off. Auto-switching is PAUSED for dev: resume when Nick wants the show back. No Library games added.
- **Scripts:** overseer scratchpad `sync/` (ytlib.py, acc1.py, rel1.py, soak1.py, prime*.py). Threads can't reach the TV: run their stick commands and return numbers. Spec: docs/sync/2026-11-06-yttv-scrub-buffer-spec.md.
- **Replay grades still fail;** NBA with Prime pause improved mid-play 10 to 3.3. Flip onto a buffered stick still unproven. Work loop cron every 5 min, session-only.
