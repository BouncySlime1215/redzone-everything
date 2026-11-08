# Catch-up and condensed viewing: research for the "drive catch-up story"

Date: 2026-11-08. Scope: research only, no code changes. Marks: **[V]** = I opened the page and read it;
**[S]** = confirmed only from a search-result snippet (page blocked or not opened), treat as lower trust;
(old) = before 2024. Numbers not attributed to a source are RedZone design proposals, not findings.

## 0. Bottom line

1. Every big streamer that built a "joined late" feature converged on the same shape: a short auto-built
   playlist of key plays, then an automatic drop into live. Prime's Rapid Recap is capped at 2 minutes and up to
   13 highlights [V]. Peacock, Apple and YouTube TV use the same pattern [V]. Nobody has published retention data
   for it, so the evidence is product convergence, not experiments.
2. RedZone already has most of the parts: `reel.py` (clips + keyword scoring + trim + concat),
   `decider/buffered.py` (dead-ball-only cut and landing, parked hidden stick) and `buffer_rc8.py`
   (behind-live depth). The missing piece is a *drive* selector and a *story plan* that talks to the buffered
   cut. It is a planner, not new video tech.
3. The one hard problem is data lag: ESPN's own clips post 4.5 to 14 min after the play (`reel.py` header), so
   the drive in progress has no clips. Text (play-by-play) is available at once. Plan for a text-first story
   with clips as an upgrade, and DVR segments only after the seek cost is measured.
4. Speeding up playback to "catch up" is supported only by lecture studies. Keep it out of v1.

## 1. Ranked techniques with evidence

Ranked by (fit to RedZone's constraints) x (strength of evidence). Constraints: $0, light Mac, replays only on the
hidden stick, Rule #1.

| # | Technique | What the evidence says | Fit / verdict |
|---|---|---|---|
| 1 | **Key-plays playlist ending in an automatic jump to live** | Prime Rapid Recap: for viewers who join late; AI-compiled; at most 2 min; up to 13 highlights; then placed into live play automatically (Sportsvideo 2024 [V]; Amazon [V]). Peacock "Catch Up with Key Plays" for NBA 2025-26 (announced 12 May 2025) [V]; Apple "Key Plays" carousel for MLS, generated "on the fly" with ML, covering goals, penalties, red cards, key shots and saves (June 2024) [V]; YouTube TV Key Plays lets you "catch up by jumping through key plays" [V]. | **Build first.** It is the proven shape. Use Prime's 2 min / 13 as a ceiling, not a target. Our 30-60 s / 5+ clip reel is already inside it. |
| 2 | **Select plays from play-by-play data, not from video** | Commentary/text alone found soccer key events "close to" video-based spotters with zero video compute (Chakraborty et al. 2025 [V]). Alignment of text to video time is the hard part: MatchTime had to re-align SoccerNet commentary with a trained model and 49 hand-timed matches (Rao et al., EMNLP 2024 [V]). | **Fits.** ESPN play text + `wallclock` + `scoringPlay` already feed `reel._summary_plays` and `match_play`. For football the drive structure is in the feed. Extend `headline_points` style scoring to drive plays; add leverage from `lab/leverage.py`. |
| 3 | **Rejoin on the next dead ball, short-hop style** | RedZone's own producers cut on the 40-second play clock: after a play they have that window to go back, so they fit one or two plays from other stadiums; Hanson says this is not possible in basketball or hockey without missing live action (Front Office Sports Q&A [V]). | **Already the design** (`buffered.cut_now`, `lands`). Rejoin = land on a dead ball with the next snap within `lead_max_s`. |
| 4 | **Event markers on the scrub bar + spoiler control** | DAZN Key Moments: goals, penalties, red cards, shots off post/bar, period starts; clicking jumps to just before the action; a toggle shows only markers you have already seen [V]. Live markers appear within minutes of the action. | **Idea to copy:** spoiler-safe mode = only plays up to the landing point; never show anything that happens after the rejoin point. |
| 5 | **Audio excitement as a re-ranker** | Baseball: commentator pitch, formants and spectral centroid, crowd separated by a non-stationarity measure, correlate with human excitement ratings (Boril et al., Interspeech 2010 [V], old). Golf: crowd cheer + commentator tone + player gestures (Merler et al., 2017 [V], old), demonstrated live over four days. Small CNN on Mel-spectrograms: about 89% audio, 83% video accuracy, ensemble better (Della Santa and Lalli, 2025 [V]). Audio-only GRU beat several audio-visual methods; loudness alone is not enough, distributions overlap heavily; vocal/commentary audio is more informative than background (Xu et al., MMSports 2026 [V]). Wimbledon/IBM used crowd noise + player reactions + stats, editors finalise (2018 press, [S], old). | **Optional v2.** Cheap on a pre-built reel or a DVR segment, but it only helps choose *within* a game, and the play-by-play already ranks plays. Gate on a measured lift over text-only ranking. Open tools: YAMNet (521 classes incl. Cheering, Applause, Crowd [S]), ffmpeg, librosa. Mac is light: run offline or cloud, measure RSS. |
| 6 | **Condensed games (post-game)** | NBA condensed games: edited broadcast, commercials removed, available next morning; format later described as highlights per team and all possessions [S, NBA support page blocked]. DAZN "Game in 40" and "Sunday in 60" [S]. MLB.TV condensed games exist; reported lengths conflict, do not rely on one [S]. | **Not for catching up live.** Useful only as a design reference for pacing (all possessions, no dead time). |
| 7 | **Whip-around / Big Inning** | Whip-around = many simultaneous games with look-ins. MLB Big Inning is a live streaming-only whip-around show with live look-ins [S]; MLB Whiparound (FS1, nightly, ended 2020) was quick-turnaround highlights of in-progress games [V]. | That is RedZone's job already, not the catch-up story. |
| 8 | **Faster playback to "catch up to live"** | Lectures only. Murphy et al. 2022 (Appl. Cogn. Psychol. 36(1), DOI 10.1002/acp.3899) [S]: little comprehension cost up to 2x, worse beyond. Meta-analysis 2021 (DOI 10.1177/07356331211043535) [S]: 1.4-1.5x small significant cost (g = -0.212), 1.8-2.0x not significant but larger (g = -0.362). Huang, Du and Yang 2025, Frontiers in Psychology (12 studies) [V]: acceleration raised cognitive load (g = 0.59) and lowered retention test scores (g = -0.41). Nothing found on sports. Catch-up math: playing at speed s closes a gap G in G/(s-1) seconds; a 40 s gap at 1.25x takes 160 s, at 1.5x 80 s. | **Skip in v1.** Maybe later: <=1.25x on the *parked* standby stick, shadow-tested, never during live action on the visible stick. Existing products (Fubo, YouTube TV) do catch-up by skipping, not by speeding [S]. |
| 9 | **On-screen text summary vs video** | No study found that compares sports recaps directly. Indirect: a PLOS ONE health-communication study reported higher *perceived* understanding with video or plain-language summaries, with similar test scores [S]; a Birmingham thesis found text soccer reports were skim-read [S]. | Use a short text slate (<=3 s) as the *opener* of the story, because it is free and exists the moment the data does; video carries the rest. |

## 2. Design: the drive catch-up story, mapped to files

### 2.1 What plays

The **story** is a pre-rolled package for game G that the viewer sees just before landing in G.

- **Slate (3 s, burned-in text):** "Q3 4:12 | WSH 17, DAL 14 | WSH ball, own 31 | Drive: 6 plays, 41 yds." Built from the
  summary's current drive and score. Needs ffmpeg `drawtext` (check the local ffmpeg build has it before
  committing; not verified here).
- **Plays (up to 4 x 5-8 s):** the drive's biggest plays, chronological, with the largest last (peak-end;
  playbook rule 17, Alaybek et al. 2022 as cited there). Plus a "last big play before this drive" only if it was
  a score or turnover.
- **Length:** 20-45 s total for a drive story. Hard ceiling 60 s (existing `reel.max_s`). Below `reel.min_s`
  (30 s) the existing code returns "no reel"; a drive story needs its own lower floor (proposed 12 s) because
  one short drive is legitimately short. These bounds are proposals; Prime's 2 min cap is the only published number.
- **Nothing after the landing point.** The story ends at or before the picture the viewer will land on. Never a
  play that is still ahead of the buffered stick (spoiler rule, DAZN-style).

Source order (tiers, fail soft like `reel.py`):

1. **T0 text slate + play lines**, always available from `self.summaries`.
2. **T1 ESPN clips** for plays older than the posting lag (NFL about 14 min, NBA 4.5, NHL 5.5 per `reel.py`);
   reuse `clips_from_summary`, `match_play`, `ClipCache`, `build_reel`. Covers earlier drives and big plays, not
   the current drive.
3. **T2 DVR segments from the hidden stick** for the current drive. Needs a measurement first: seek latency per
   cut and how far back the DVR lets us scrub. Do not build until measured. Per-play seeks may cost seconds each
   (the repo says tune+scrub is about 55 s for a fresh tune).

### 2.2 Which stick

Replays are only played on the hidden stick (project rule). So:

1. At a dead ball on the visible stick V (game A), `buffered.cut_now` flips to the hidden stick H, which plays
   the story file. Leaving A obeys the existing leave rule (never a high-leverage moment; `lab/leverage.py`).
2. While the story plays on H (now on screen), V (now hidden) re-tunes to G and scrubs ~75 s behind live and
   parks, paused just before a snap (`park`, `park_in`, `cut_parked`). The story length, 20-45 s, is meant to
   cover part of that prep. The repo estimate for tune+scrub is 55-60 s (`prep_s` 60), so the story alone may not
   cover it: if the standby is not ready when the story ends, hold the story's last frame or fall back to the
   normal jump path. Do not improvise a live join.
3. When the story ends, flip back to V at G, landing only if `lands()` says dead ball and the next snap is within
   `lead_max_s`.

Delay accounting: the parked stick resumes where it paused, so a 40 s story adds about 40 s of screen delay for
that game. `max_delay_s` (270 s) already bounds this; the story must count against it or it is skipped.

### 2.3 How to rejoin without breaking Rule #1

- Entry to G is the existing buffered landing: dead ball, picture comes up `lead_min_s`..`lead_max_s` before the
  snap. The story never changes that gate. 0 mid-play entries stays a hard test.
- The story is only *offered* when it can finish and still land on a dead ball inside `max_delay_s`. If a
  high-leverage moment (close and late, or overtime) is approaching in G, skip the story and join; catching the
  moment beats the recap. Same logic at 5 s: if `plays_missed_entry` would rise, no story (CLAUDE.md PR rule).
- Never leave a high-leverage live play on A to start a story (rule #1, leave half).
- Story contents are for plays already in the past of the *landing* picture, so nothing in it can pre-spoil
  what the viewer is about to see live.

### 2.4 Files to touch (proposal only)

- `redzone/reel.py`: add `drive_plays(summary, game_id)` and `plan_drive_story(...)` beside `plan_reel`; reuse
  `headline_points`, `match_play`, `ClipCache`, `build_reel`. Add a slate-card encode helper.
- `redzone/decider/buffered.py`: a `story_ok(...)` predicate used before `cut_now` (fits in delay, leverage, standby
  ready); no change to `cut_now` or `lands`.
- `redzone/buffer_rc8.py`: feed `adaptive_buffer_s` / delay numbers into the predicate; no behaviour change.
- `redzone/engine.py` (`_reel_tick`, `Reeler`) and `redzone/web/server.py` (`/reel.mp4`, `/api/reel`): serve a per-game
  story next to the generic reel; `Reeler.played` marks plays seen.
- New config under `reel.drive_story.*` with `enabled: false`, bounds registered in `redzone/config.py`
  `LIMITS`. Ships OFF behind its flag until it passes its gate (CLAUDE.md release gate).
- Phone/web: a text version of the same story as a card on `redzone/web/phone_v2.py` (free; the TV needs the hidden
  stick, the phone does not).

### 2.5 How to measure (suggested gates, to be pre-registered)

- Hard: entries_mid_play = 0; no rise in high-leverage mid-play leaves or plays_missed_entry; dead_air_pct not up.
- Story: share of story offers that finished and landed on a dead ball; median story length; share skipped for
  leverage; screen-delay added.
- Quality: Nick's thumbs on "was this the right recap?" by whole night (playbook E1/E5 style); plays shown that were
  not in the actual drive (should be 0).

## 3. Risks

1. **Data lag** (clips 4.5-14 min late): the current drive cannot be video from ESPN. Text-first avoids empty stories.
2. **Time alignment**: play wallclock vs picture time is only as good as the sync edge; MatchTime shows commentary
   misalignment is a known, material problem [V]. Keep `match_play`'s rule "a guess is worse than no answer".
3. **Spoilers/mid-play**: a story that includes anything after the landing point, or lands mid-play, violates the
   product. Guard in code and in tests.
4. **Preparation time**: a 20-45 s story may not cover the 55-60 s standby prep; a visibly stalled hand-off is worse
   than no story.
5. **Added delay** per story reduces how current the picture is (max_delay_s cap).
6. **Fire TV seek cost and DVR depth are unmeasured**; T2 should not be promised.
7. **Overfitting/length**: published numbers (2 min, 13 highlights) are product choices, not tested optima.
8. **Speed-up** evidence is lectures only; risk of hurting dense, visual sports content is untested.
9. **Audio models** add CPU/RAM on an 8 GB Mac; keep off-Mac or offline, and require a measured lift.
10. **Source quality**: several vendor pages were blocked (NBA support, Fubo, PMC, sagepub); those are marked [S].

## 4. Bibliography (24 entries; [V] opened and read, [S] search snippet only)

Product behaviour
1. Sportsvideo.org, "NFL Kickoff 2024: TNF on Prime Video Continues to Push the AI Envelope With New Set of 'Prime Insights'", 10 Sep 2024. https://www.sportsvideo.org/2024/09/10/nfl-kickoff-2024-tnf-on-prime-video-continues-to-push-the-ai-envelope-with-new-set-of-prime-insights [V] (Rapid Recap: <=2 min, AI, auto into live).
2. Amazon, Prime Video TNF Next Gen Stats and AI features (2024 season). https://www.aboutamazon.com/news/aws/prime-video-thursday-night-football-next-gen-stats-ai-features [V] ("up to 13 highlights", <=2 min; no launch date on page).
3. Google, "View stats, key plays & fantasy football", YouTube TV Help. https://support.google.com/youtubetv/answer/9119412?hl=en [V]. Plus sport coverage and DVR-only catch-up per Cord Cutters News / Tom's Guide snippets [S].
4. DAZN Help, "What is Key Moments?". https://www.dazn.com/en-US/help/articles/16175359846045 [V]. DAZN "Game in 40"/"Sunday in 60": https://www.dazn.com/en-US/news/american-football/what-is-sunday-in-60-relive-every-big-moment-from-sundays-slate-on-dazn/u9qyr7235ju515egx5qxwt260 [S].
5. Comcast/NBCU, "Peacock's NBA streaming experience", 12 May 2025. https://corporate.comcast.com/press/releases/peacock-nba-streaming-experience-this-fall-intuitive-interactive-features-next-generation-of-fans [V] (Catch Up with Key Plays; no length given).
6. 9to5Mac, "Apple TV app adds new Catch Up feature for live streaming sports in MLS Season Pass", 20 Jun 2024. https://9to5mac.com/2024/06/20/apple-tv-app-adds-new-catch-up-feature-for-live-streaming-sports-in-mls-season-pass/ [V].
7. Front Office Sports, Scott Hanson Q&A on RedZone. https://frontofficesports.com/article/scott-hanson-qa-nfl-network-nfl-redzone/ [V] (40-second play clock rhythm; date not shown on fetched text).
8. Wikipedia, "MLB Whiparound". https://en.wikipedia.org/wiki/MLB_Whiparound [V] (FS1, ended 2020).
9. MLB.com, "Watch live: Big Inning streaming on MLB.TV". https://www.mlb.com/amp/news/mlb-big-inning-streaming-on-mlb-tv.html [S].
10. NBA League Pass support, "Full Game Replays and Condensed Game Availability". https://support.watch.nba.com/hc/en-us/articles/360011802673-Full-Game-Replays-and-Condensed-Game-Availability [S] (page returned 403).
11. Fubo Help, "Where Can I Find Catch Up to Live". https://support.fubo.tv/hc/en-us/articles/37780618613517-Where-Can-I-Find-Catch-Up-to-Live [S] (MLB only, needs a recording; page returned 403).

Highlight detection research
12. Boril, Sangwan, Hasan, Hansen, "Automatic excitement-level detection for sports highlights generation", Interspeech 2010, pp. 2202-2205, DOI 10.21437/Interspeech.2010-606. https://www.isca-archive.org/interspeech_2010/boril10b_interspeech.html [V] (old).
13. Merler et al., "Automatic Curation of Golf Highlights using Multimodal Excitement Features", arXiv:1707.07075, 2017. https://arxiv.org/abs/1707.07075 [V] (old).
14. Della Santa and Lalli, "Automated Detection of Sport Highlights from Audio and Video Sources", arXiv:2501.16100, Jan 2025. https://arxiv.org/abs/2501.16100 [V].
15. Xu, Sarkar, Raj, Gunawan, "Audio for Sports Highlight Detection: A Comparative Empirical Study", arXiv:2609.17923, 15 Sep 2026 (MMSports 2026). https://arxiv.org/abs/2609.17923 [V].
16. Rao, Wu, Liu, Wang, Xie, "MatchTime: Towards Automatic Soccer Game Commentary Generation", EMNLP 2024, arXiv:2406.18530. https://arxiv.org/abs/2406.18530 [V].
17. Chakraborty et al., "Do We Need Large VLMs for Spotting Soccer Actions?", arXiv:2506.17144, 2025. https://arxiv.org/abs/2506.17144 [V].
18. TechRadar, "Wimbledon 2018: How IBM Watson is serving up the best viewer experience". https://www.techradar.com/news/wimbledon-2018-how-ibm-watson-is-serving-up-the-best-viewer-experience [S] (old, vendor-affiliated).

Playback speed and comprehension
19. Murphy, Hoover, Agadzhanyan, Kuehn, Castel, "Learning in double time: The effect of lecture video speed on immediate and delayed comprehension", Applied Cognitive Psychology 36(1), 2022, DOI 10.1002/acp.3899. https://castel.psych.ucla.edu/wp-content/uploads/sites/111/2022/03/Applied-Cognitive-Psychology-2021-Murphy-Learning-in-double-time-The-effect-of-lecture-video-speed-on-immediate-and.pdf [S] (PDF unreadable by fetch; figures from search results).
20. Meta-analysis of accelerated playback, 2021, DOI 10.1177/07356331211043535. https://journals.sagepub.com/doi/10.1177/07356331211043535 [S] (authors not confirmed; page returned 403).
21. Huang, Du, Yang, "Facilitating or hindering learning: a meta-analysis of acceleration on video learning", Frontiers in Psychology, 22 Aug 2025. https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1427609/full [V].

Text vs video, tools, and in-repo
22. PLOS ONE 2019, doi:10.1371/journal.pone.0224697 (video/plain-language summaries and perceived understanding). https://pmc.ncbi.nlm.nih.gov/articles/PMC6863540 [S] (PMC page blocked by captcha; low weight).
23. FFmpeg `atempo` filter docs (0.5-100 in current builds; older 0.5-2.0; pitch-preserving). https://ayosec.github.io/ffmpeg-filters-docs/5.0/Filters/Audio/atempo.html [S] (mirror).
24. YAMNet README (521 AudioSet classes incl. Cheering, Applause, Crowd). https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/README.md [S].

In-repo context: `docs/research/watch-science-playbook.md` (rule 17 peak-end catch-up ordering; Alaybek et al. 2022 as cited there, not re-verified here), `redzone/reel.py`, `redzone/buffer_rc8.py`, `redzone/decider/buffered.py`.
