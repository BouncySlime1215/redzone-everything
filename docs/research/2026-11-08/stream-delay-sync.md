---
id: R-DELAY
title: "Measuring and controlling stream delay (research, 2026-11-08)"
date: 2026-11-08
stream: harness
author: "unrecorded (2026-11-08 research agent)"
status: reviewed
supersedes: []
sources: 22
verified: 11
affects: []
gate: []
---

# Measuring and controlling stream delay (research, 2026-11-08)

Goal: per stream, know "seconds the picture is behind real time" to about +-1 s (1 sd <= 0.5 s), and hold a stick at a fixed offset behind live.
Verification tags: **[F]** = I fetched and read the source this session. **[S]** = seen only in search results (title/snippet), not opened. **[R]** = measured in this repo (not external). Older than 2025 is marked "(old)". Nothing here was run on the real TV.

## 0. Bottom line

The repo already has most of the machinery (Kalman filter `redzone/decider/sync.py`, per-tune live-edge meter `redzone/sync_edge.py`, score-bug OCR `redzone/screen/scorebug.py`). The gap to +-1 s is not the filter. It is (a) the unknown per-channel absolute latency L, (b) the bias and lag of the *reference* timestamps (the data feed, the capture time of a frame), and (c) the 4 s sigma floor that the gates were tuned on. Three cheap moves, in order: fuse the "frame clock" epoch (Prime) and the per-tune live-edge E (YTTV) as high-rate relative meters; make the absolute anchors (score bug and clock) fewer-but-better by timestamping frames honestly and fitting the running clock over many frames; then close the loop on holds using E as feedback.

## 1. Top findings, ranked by how much they tighten delay uncertainty

| # | Finding | Why it matters for +-1 s | Evidence |
|---|---|---|---|
| 1 | **A player-side clock beats any picture anchor.** A relative meter that reads every second (E = wall - position_now, flat while playing, rises 1.000 s/s while paused) has sd ~0.1-0.5 s. Prime's epoch-ms position gave wall - position = 6.4 s median, sd ~0.1 s. Both need only ONE absolute anchor per tune to become an absolute delay. | Turns sparse +-2 s anchors into a per-second estimate. Biggest single gain. | [R] `sync_edge.py` docstring (Nov 5 stick tests). Mechanism matches Media3's `getCurrentLiveOffset` = real time minus playback position [F: Media3 live docs]. |
| 2 | **Fusing a stable offset K across many noisy anchors.** Prime: arena-vs-frame-clock K ~9.6 s; 20 reads at sd 1.5 s fuse to 0.34 s. Averaging only works if the error is zero-mean; shared bias (feed timestamp rounding, bug update lag) does not average out. | Gets the absolute anchor under 0.5 s. Needs bias measured, not assumed. | [R] `sync_edge.py` `frame_k`; statistics are standard inverse-variance. |
| 3 | **Reference timestamps are the real error floor.** A direct sports data feed was 6-10 s behind ground truth in a 2017-18 NBA/EPL study, median ~7 s direct and ~20 s via websites. NHL api-web ran 16.7 s p50 behind the ice in this repo. A "wallclock" in a feed has its own lag and rounding. | The anchor "feed play time vs frame time" cannot beat the feed's own timing error. Prefer feeds with true event timestamps (NBA `timeActual`) and compare feed-vs-feed to learn each one's bias. | Wang & Kuzmanovic 2018 (old) [F]; [R] `decider/screen.py` SOURCE_LAG_S comment. |
| 4 | **Frame timestamps are the second floor.** `adb screencap` runs ~2 s per frame and up to ~7 s on one stick, so "frame time" is only known to about half the gap. | Fix: bracket each capture (t_request, t_done), use the midpoint, and calibrate the capture latency once against a visible stopwatch. A UVC HDMI capture delivers frames with a steady, measurable pipeline latency, but see HDCP risk in section 3. | [R] `scorebug.py` MAX_GAP_S note; capture-card latency figure NOT found in any source (measure it). |
| 5 | **Fit the running game clock over a window instead of using single samples.** A running clock read at 1 Hz for 10-20 s gives a slope check (must be -1 +- 0.05) plus an intercept with sd ~ (display resolution 0.3-0.6 s)/sqrt(N), and it rejects OCR misreads by residual. Current code uses one reading against one bracketing pair at 1.5 s. | Cuts clock-anchor sd from 1.5 s toward ~0.5 s *before* feed bias. Needs format and monotonic checks, which the patent literature on scoreboard OCR also relies on [S]. | [R] `decider/sync.py` `clock_sample`; [S] USPTO scoreboard OCR patents (regex pattern + temporal consistency). |
| 6 | **Robust, change-point-aware filtering.** The standard Kalman filter is optimal for Gaussian noise but is known to be too sensitive to delay outliers; practice clips at ~3 sigma or uses margin/LP estimators. Our delay is piecewise constant (jumps only at tune, seek, pause, stall), so model it as constant + explicit jump events rather than a continuous random walk. | Fewer wrongly-widened intervals, faster re-sync after a seek. | Bletsas 2005 [F]; Pinkovich & Shimkin 2011 [S]; Justia patent 20180048457 [S]; [R] `sync.py` GATE=3, Q_DELAY. |
| 7 | **Audio: loudness surges (+-3 s) are weak; cross-correlation is strong but needs a reference signal at a known time.** Landmark fingerprints (Shazam style) are robust to noise and give offset to frame resolution; Panako handles time-scale and pitch changes up to 10 %. But the reference must be audio whose real-time offset is known. Candidates: the OTA broadcast (Phenix measured ~17-19 s behind the field, a *stable* reference); a second stick's audio (not audible at the same time). | Only worthwhile if a low-latency reference audio exists. Costs hardware (antenna + tuner), so ask Nick first ($0 rule). Mic-only surge detection stays +-3 s. | Wang 2003 [F]; Panako 2014 [F]; [S] Twente thesis 2014 (playout delay by fingerprint, results not read). |
| 8 | **Closed captions are a weak anchor.** Live captions trail speech by ~2-6 s depending on human vs ASR; vendor claims of ~2 s are best-case. Variance is larger than our target. | Use only as a sanity check (right half of the game), not as a meter. | [S] 3Play vendor page; [S] accessibility glossary. Low confidence. |

Service latency context (end to end, behind the field unless noted). These are single-event spotter or tester numbers, not steady-state, and they are NOT comparable to our 34.9 s edge measurement:

| Service | Number | Source |
|---|---|---|
| Super Bowl LIX (Feb 2025): Tubi 41, NFL+ 51, Hulu Live 62, DirecTV 63, Sling 64, YouTube TV 67, Fubo 78 s | 62 data points, 7 platforms | TV Technology on Phenix [F] |
| Super Bowl LX (Feb 2026): broadcast ~19, cable 38, Peacock 48, Hulu Live 53, YouTube TV 53 s | Stats Perform spotters at the stadium | The Desk [F] |
| Prime Video, AFC Wild Card, Jan 2025: Fire TV ~6 s, LG ~8 s **behind the ABC broadcast feed** (not the arena) | One tester | Rayburn, Streaming Media blog [F] |
| Netflix NFL Christmas, Dec 2024: MacBook 16, Fire TV 14, Roku 10, Apple TV 10, iPad 3 s behind OTA | Viewers compared OTA with a stream | Rayburn [F] |
| Prime glass-to-glass target "~10 s" / "significantly less than 5 s" | Amazon statements | StreamTV Insider [S, page returned 403] |
| ESPN app (2017, old): 10-13 s desktop, 29-31 s mobile | Wowza test | [S] |
| Fox One, ESPN+ : no 2025-26 published measurement found | | gap |

Reading these against our data: Prime is relative to broadcast in Rayburn's number, and broadcast itself is ~17-19 s behind the field [F: both Phenix reports], so ~24-27 s arena-based is plausible, yet our own Nov 4 NBA score-bug reads gave 15.8-16.2 s [R]. Treat per-event published numbers as priors with sd of several seconds. The repo's per-channel measured values are far tighter than anything published, so spend effort on measurement, not on priors.

Other facts used in the design:
- Live playlists must keep at least three target durations (RFC 8216 6.2.2), and `EXT-X-PROGRAM-DATE-TIME` maps wall clock to media time (4.3.2.6) [F]. Where an app exposes that mapping (Prime epoch positions), delay is read directly.
- Media3 players (Fire TV apps built on it) control live offset with `targetOffsetMs`, `min/maxOffsetMs` and a +-speed controller (`minPlaybackSpeed`/`maxPlaybackSpeed`, default control via `LivePlaybackSpeedControl`). A pause longer than the live window raises `ERROR_CODE_BEHIND_LIVE_WINDOW` and the app must jump back to the live edge [F]. We cannot set these in YouTube TV or Prime; they explain why long holds can snap back, and why a stick that stalls then "catches up" changes E.
- Apple Vision text confidence is nearly useless: a forum report says values are almost always 0.5 or 1.0 [F]. WWDC19 session 234 lists ~0.25 s fast vs ~2 s accurate on a small image [S]. So validate OCR by format and time consistency, not confidence.

## 2. Concrete design for RedZone

Each item: file, change, expected gain, gate. Nothing here is implemented. Per CLAUDE.md, every default or gate change needs a line in `docs/memory/decisions.md`, ships OFF behind a flag until the locked-set gate passes, and a PR must show entries_mid_play, plays_missed_entry, dead_air_pct before/after.

1. **Honest frame times** (`redzone/screen/scorebug.py`, `redzone/screen_ocr.py`, `redzone/sync_probe.py`).
   Record `t_req` and `t_done` for each screencap; use the midpoint with sd = (t_done - t_req)/sqrt(12) plus a calibrated constant. Calibrate once per stick by capturing an on-screen stopwatch (a test app or the Mac's own clock shown on the stick). Replace the fixed "half the capture gap" with this per-frame sd. Gain: removes the largest unmodelled term on slow-capture sticks (HDMI1 ~7 s per frame).

2. **Clock-window fit** (`redzone/decider/sync.py` next to `clock_sample`, fed by `scorebug.py`).
   Collect (frame_mid_wall, bug_clock_s) over a running-clock stretch (no stoppage in the feed, clock monotone). Robust-fit slope and intercept (Huber or RANSAC). Accept only if slope within 1 +- 0.05 and residual sd < 0.7 s. Output one sample with sd = max(0.4, fit sd) instead of 1.5 s. Keep `clock_sample` as the fallback. Needs a feed with true wall times (NBA `timeActual`); for ESPN or NHL feeds first learn the feed's own bias, see item 3.

3. **Learn each feed's timestamp bias** (`redzone/decider/screen.py` SOURCE_LAG_S, `redzone/decider/delaymodel.py`).
   Where two sources describe the same play (NBA `timeActual` vs ESPN wallclock), log their difference per game and fuse it into the anchor sd and mean. Today SOURCE_LAG_S is a hand-set (min, max). Also use "no play yet" lower bounds only as bounds. Evidence base: 2018 study [F] and the repo's NHL 16.7 s [R].

4. **Promote the relative meters to first-class measurements** (`redzone/sync_edge.py` -> `redzone/decider/sync.py` `SyncBook.position`).
   Already partly done (`EPOCH_POSITION`, `edge` rows, `frame_k`). Extend: feed `E` (YTTV) and epoch delay (Prime) into the filter every second with sd 0.1-0.5 s, and let anchors only estimate the constant (K or L) per tune. Do not let anchors move d between events. The filter's prior `Q_DELAY = 0.02^2` per s then rarely matters. Gain: absolute sd limited by the single constant, not by anchor rate.

5. **Change-point model, not random walk** (`decider/sync.py` `ChannelSync._predict`, `reset`, `shift`).
   Events: new tune (forget origin), pause/seek (add known shift with sd 0.2-0.5 s from E), stall (E rose while playing, drift alarm), ad-pod burst. Between events assume constant d. Replace the hard 3-sigma `GATE` with a Student-t (or Huber) weight so one bad OCR read does not trigger "uncertain" for 300 s; keep the two-in-a-row restart rule.

6. **Sigma floor** (`decider/screen.py` `MIN_TAP_SIGMA_S=4`, `SCREEN_SIGMA_S=4`, `attach_sync(min_sigma_s)`).
   The gates were tuned on a 4 s floor: a too-narrow interval cut plays mid-play (stated in the code comments). So do NOT just lower it. Lower it per app only after a forward-chaining replay (`scripts/ci_scorecard.py`, `eval.py --sigma`) shows no rise in mid-play cuts; the NoiseCal (`NoiseCal.sigma`, min_n 20, min 2 s) is the right mechanism: it already learns real error from innovations. Target: YTTV floor 1.5 s once edge-based sd < 0.6 s; Prime 2 s.

7. **Hold control loop** (`redzone/tuners/yttv_seek.py`, `redzone/control.py` held[target], `redzone/buffer_rc8.py`).
   Facts from the repo: a tune lands at the live edge repeatable to ~0.5 s, and a 30 s pause raised E by 30.01 s [R]. So the lever is time: pause for exactly (target - current) seconds, resume, then read E at 1 Hz for ~5 s and compare with E_land + target. If |error| > 0.7 s, correct with a short pause or one seek step, then stop (do not hunt). Record the command-to-effect latency (adb round trip) per stick as its own distribution and subtract it. For seeks, quantization is set by the app's skip size: do coarse seek then trim with a timed pause. Respect DVR window limits: Media3-style players throw `BEHIND_LIVE_WINDOW` when too far back [F]; the actual YTTV/Prime DVR depth was not found in any source, so measure it. Keep the margin: never pause past `window - 30 s` (value is a placeholder to be measured).

8. **Audio, only if cheap** (`redzone/screen/audio.py`).
   Step 1 (free): replace RMS surge with spectral-flux onset detection and require one match to a feed event in the plausible window; expect improvement over 3 s sd but this is a hypothesis to test, not a result. Step 2 (needs hardware, ask Nick): an OTA tuner as reference audio, landmark matching (Wang 2003 style) of room audio against it for the same network gives relative offset to well under 1 s; OTA's own latency (~17-19 s in two Phenix reports [F]) is then a per-market constant to calibrate once against the score bug.

9. **HDMI capture on the Mac** (new, optional; no file exists today, the repo uses adb screencap).
   Only worthwhile for sources that are not HDCP-blanked. Note YouTube TV already comes out black in screencap [R]; a capture card would also be blanked by HDCP (section 3). For Prime, screencap works, so a card mainly buys steady, low-jitter frame times. A UVC capture on macOS is read with AVFoundation; the MS2109 class supports 1080p30 MJPEG over USB 2.0 and has a mono-audio quirk on macOS (forum reports [S]); no measured latency was found, so measure it with an on-screen stopwatch before relying on it. Cost money: ask first.

10. **What not to build:** closed-caption timing as a meter; any HDCP stripping splitter (see risks).

Expected error budget if items 1-7 land (hypotheses, to be validated on the locked night set): YTTV per-tune constant sd ~0.4-0.6 s (edge repeatability 0.4 [R]) + hold error ~0.5 s => ~0.7 s sd, i.e. ~+-1.4 s at 2 sd; Prime epoch + K ~0.35 s + 0.1 s => ~0.4 s sd. Not yet measured end to end.

## 3. Risks

- **HDCP.** Fire TV output is often encrypted and basic capture cards show black; the common workaround is an HDCP-stripping splitter [S: AFTVnews, OBS forum]. That is DRM circumvention and is off the table here. So HDMI capture will not give YouTube TV pixels. Use overlay/UI text, media session and audio instead.
- **The reference lies.** Feed timestamps (6-10 s old-study range, 16.7 s NHL here) and score-bug update lag are biases that averaging cannot remove. A narrower interval built on a biased mean cuts plays mid-play, which breaks Rule #1. Prefer late-biased (safe side) estimates.
- **Non-stationarity.** Ad insertion, CDN failover, ABR rebuffers and OTA/cable differences shift delay; per-event published numbers swing by 15 s between events. Keep the stall detector and alarm; re-anchor after every tune.
- **Published numbers are vendor or one-tester data.** Phenix/Stats Perform sell low-latency tech; Rayburn's are small samples; different search summaries gave conflicting Tubi figures (26 s vs 41 s) and I used the one in the fetched article (41 s). Super Bowl load is not a regular-season Tuesday.
- **Kalman optimism.** Gaussian assumptions understate tails; use NoiseCal on residuals to inflate sigma, and report worst fold, not just mean, per the release gate.
- **Overfitting.** Few nights of anchors. Time-split by whole night, locked set scored once.
- **Capture and adb jitter.** `adb` round trips and `uiautomator` dumps (1-3 s CPU on the stick, unmeasured effect on visible picture) can themselves add stutter; keep hidden-stick-only probes as `sync_probe.py` does.
- **Mic privacy and false triggers.** Room audio surges from the viewer; keep numbers-only retention as in `audio.py`.
- **Cost.** OTA tuner or capture card needs money: ask before buying.

## 4. Bibliography

External (accessed 2026-11-08):
1. TV Technology, "Study: Super Bowl Streaming Delays Topped 60 Seconds on Many Platforms," Feb 10 2025 (Phenix LIX). https://www.tvtechnology.com/news/study-super-bowl-streaming-delays-top-60-seconds-on-many-streaming-platforms [F]
2. The Desk, Stats Perform/Phenix Super Bowl LX latency, Feb 2026. https://thedesk.net/2026/02/stats-perform-phenix-latency-super-bowl-lx/ [F]
3. D. Rayburn, "Prime Video's Exclusive Stream of the NFL AFC Wild Card Game Looking Great," Streaming Media Blog, Jan 12 2025. https://www.streamingmediablog.com/2025/01/prime-afc-wildcard.html [F]
4. D. Rayburn, "Live Blogging Netflix's Christmas NFL Stream," Streaming Media Blog, Dec 25 2024. https://streamingmediablog.com/2024/12/netflix-xmas-day.html [F]
5. P. Wang and A. Kuzmanovic, "What's the Score? A First Look at Sports Live Data Feed Services," Northwestern/Northeastern, 2018 (old; 2017 measurements). https://networks.cs.northwestern.edu/website/publications/gi18.pdf [F]
6. A. Bletsas, "Evaluation of Kalman Filtering for Network Time Keeping," IEEE TUFFC 52(9), 2005 (old). https://www.telecom.tuc.gr/~aggelos/files/TUFFC05.pdf [F]
7. A. Wang, "An Industrial-Strength Audio Search Algorithm," ISMIR 2003 (old). https://www.princeton.edu/~cuff/ele201/files/Wang03-shazam.pdf [F]
8. J. Six and M. Leman, "Panako: A Scalable Acoustic Fingerprinting System Handling Time-Scale and Pitch Modification," ISMIR 2014 (old). https://archives.ismir.net/ismir2014/paper/000122.pdf [F]
9. Android Developers, "Live streaming" (Media3 ExoPlayer). https://developer.android.com/media/media3/exoplayer/live-streaming [F]
10. R. Pantos and W. May, RFC 8216, HTTP Live Streaming (2017, old; sections 4.3.2.6, 6.2.2). https://www.rfc-editor.org/rfc/rfc8216 [F, first 100k chars]
11. Apple Developer Forums, "VNRecognizedText confidence values are only 0.5 and 1.0," Nov 2021 (old, one report, no replies). https://developer.apple.com/forums/thread/695693 [F]
12. Apple WWDC19 session 234, "Text Recognition in Vision Framework" (old). https://devstreaming-cdn.apple.com/videos/wwdc/2019/234rye7511ohnx5dmvl/234/234_text_recognition_in_vision_framework.pdf [S: speed numbers via search snippet]
13. D. Pinkovich and N. Shimkin, "Clock Synchronization Using Maximal Margin Estimation," 2011 (old). https://webee.technion.ac.il/shimkin/PAPERS/PinkovichSynchMED2011.pdf [S: abstract via search; PDF not read]
14. Ma et al., "Tracking Low-Precision Clocks With Time-Varying Drifts Using Kalman Filtering," IEEE/ACM ToN, 2011 (old). https://unpaywall.org/10.1109%2FTNET.2011.2158656 [S]
15. Hamilton et al., "ACES: Adaptive Clock Estimation and Synchronization Using Kalman Filtering," MobiCom 2008 (old). https://sites.cc.gatech.edu/home/jx/reprints/Hamilton%20et%20al.%20-%202008%20-%20ACES%20Adaptive%20Clock%20Estimation%20and%20Synchronization.pdf [S]
16. US patent application 20180048457, clock synchronization over time-varying and lossy networks (Kalman too sensitive to outliers). https://patents.justia.com/patent/20180048457 [S]
17. AFTVnews, Fire TV and capture cards (HDCP black screen). https://www.aftvnews.com/?p=15224 [S]
18. University of Twente thesis, "Playout delay of TV broadcasting," 2014 (old). https://essay.utwente.nl/64744 [S: page text not retrievable]
19. 3Play Media, live virtual caption encoding (vendor claim, ~2 s vs 4-6 s). https://www.3playmedia.com/services/features/live-virtual-caption-encoding/ [S]
20. StreamTV Insider, "Amazon sets livestreaming sports latency goal of 'significantly less than 5 seconds'." https://www.streamtvinsider.com/video/amazon-sets-live-streaming-sports-latency-goal-significantly-less-than-five-seconds [S; fetch returned 403]
21. Sports Video Group, "Super Bowl LIX: Tubi leads all streaming platforms in least lag time, according to Phenix report," Feb 10 2025. https://www.sportsvideo.org/2025/02/10/super-bowl-lix-tubi-leads-all-streaming-platforms-in-least-lag-time-according-to-phenix-report/ [S]
22. YouTube Help, "Understand live streaming latency." https://support.google.com/youtube/answer/7444635 [S]

Repo [R]: `redzone/sync_edge.py`, `redzone/sync_probe.py`, `redzone/decider/sync.py`, `redzone/decider/screen.py`, `redzone/screen/scorebug.py`, `redzone/screen/audio.py`, `redzone/screen/boundary.py`, `redzone/screen_ocr.py`, `docs/instant-switching.md`, `docs/sync/yttv_tests/README.md`.

Gaps I could not fill: published latency for ESPN+/Fox One in 2025-26; measured USB capture latency on macOS; YouTube TV and Prime DVR window depth; Twente thesis accuracy; Pinkovich result details; Moon-Skelly-Towsley 1999 (not retrieved, not cited).
