---
id: D-VIDEO
title: Video production and broadcast editing data for RedZone's cut model
date: 2026-11-09
stream: data
author: data-7 (Claude Sonnet 5.5)
status: draft
supersedes: []
sources: 50
verified: 33
affects: []
gate: []
---

# D-VIDEO: video production and broadcast editing data

Pages were opened with WebFetch (33 opened) plus search results (about 17 more, marked "search only"). Page text was treated as data. Nothing was downloaded or signed up for. Repo secrets were not read. Only annotations, metadata, labels and timestamps are listed; the videos behind them are copyrighted and rejected.

## Bottom line

- **True total against 250M: not reached with usable data.** Honest counts by unit, after dropping duplicates:
  - Licensed for training and shown on the page: about 1.74M mixed-unit rows. Of those, only about 308K are shot or transition boundaries (ClipShots 166,756 + UCI 129,685 + AutoShot 11,606). The rest is commentary text (1.34M segments), play-by-play (86,549 rows) and 1,890 hockey frames.
  - Everything listed, including non-commercial, gated or licence-unclear: about 83.1M mixed-unit rows. Panda-70M alone is 70.7M of that, and its cut intervals come from a detector (TransNetV2), not people.
  - YouTube-8M adds 1.9 billion frame-level feature vectors (unit: frames at 1 fps). That alone beats 250M, but its licence was not shown on a page I opened, the content is generic YouTube, and it holds no cuts. It is not counted above.
- **No public data was found for NFL, NBA, NHL or CFB director switching, score-bug reading, or the aired record.** Details under Gaps.
- **Unit warning:** rows below are clips, shots, transitions, text segments, frames, videos or tubes. Never add across units.

## Table

Training allowed: "yes" only when a licence on an opened page permits it. Non-commercial (NC) and unclear count as "no" until Nick confirms. "Kaggle $0" means fetchable in a free notebook with no purchase; "login" means Nick must sign in or accept terms himself.

| dataset | true data points and unit | license | training allowed | fetch at $0 | how direct |
|---|---|---|---|---|---|
| SportsShot (MCG-NJU, HF) | 1,200 videos (400 each volleyball, basketball, football), over 30K shot annotations, over 4M frames; frame-level shot-type labels (7 classes) plus transition intervals. Unit: shots and frames. 183 GB, mostly video | CC BY-NC 4.0 | no (NC) | login: gated on HF; annotation files are a small part of 183 GB | direct (sports shot cuts and shot types) |
| SoccerNet-v2 labels (action spotting, camera shots, replays) | 110,458 actions (17 classes) + 158,493 camera-change timestamps (116,687 in 200 fully annotated games) + 32,932 replay shots = 301,883 events (my sum; the paper says "around 300k"). Unit: timestamped events. 500 games | repo MIT (devkit); label licence not stated on SN-Labels HF page (356 MB, no card) | no (label licence unclear) | yes for labels via `pip install SoccerNet` or HF; videos need NDA and are not wanted | direct (camera cuts, replays, dead-ball events in broadcast sport; soccer only) |
| SoccerNet-Echoes (HF) | 4,387,121 rows across three Whisper versions (v1 1.34M, v2 1.44M, v3 1.60M) of the same commentary. Counted once: about 1.34M text segments with start/end times. Unit: ASR text segments | CC BY 4.0 | yes | yes, 566 MB, no login on page | indirect (commentary excitement vs play events; soccer) |
| SoccerHigh | 237 full matches paired with 237 official summary videos; shot boundaries used but total not reported (splits 141/51/45). Unit: matches. Mean summary 3:19, about 84% gameplay | paper CC BY-NC-SA 4.0; dataset licence not stated | no | yes, project page https://ipcv.github.io/SoccerHigh/ (not opened) | direct (which shots editors keep for highlights; soccer) |
| ClipShots | 4,039 videos, 128,636 cuts + 38,120 gradual transitions = 166,756 transitions (paper figures; GitHub page gives no totals). Unit: transitions | repo MIT; videos come from YouTube/Weibo | yes for the annotation files (MIT); videos not wanted | yes: annotations in repo (`annotations/`, `video_lists/`); videos on Baidu/Google Drive | indirect (generic cut detection, trains the cut-finder not the cut-chooser) |
| AutoShot / SHOT | 853 videos, 11,606 shots, 2,716 boundaries in 200 test videos. Unit: shots. Labels in `kuaishou_v2.txt` | repo MIT; no separate data licence | yes for labels only; data licence unstated, treat video as no | labels yes (GitHub); videos on Google Drive or Baidu | weak (short social videos) |
| TV News Channel Commercial Detection (UCI) | 129,685 shot-level instances from 150 hours, 5 news channels (3 Indian, BBC, CNN); audio-visual features per shot, commercial vs news label. Unit: shots. DOI 10.24432/C5990S | CC BY 4.0 | yes | yes (UCI zip, small) | indirect (shot features plus break/non-break label; news, not sport; closest to dead-ball breaks) |
| NBA Games (HF `choucsan/NBA_Games`) | 189 full games (about 347 h), 81,355 play-by-play rows (166 games) + 5,194 box-score rows. Unit: play-by-play rows. Video IDs only, no video | MIT | yes | yes, 66.6 MB | indirect (play-by-play clock to align with broadcast; no cut labels) |
| Mr. HiSum | 31,892 videos with frame importance scores from over 50,000 viewers each (YouTube "Most replayed"). Unit: videos (frame count not stated). Features must be added from YouTube-8M | CC BY 4.0 per README, plus YouTube terms | yes per README, YouTube terms still apply | yes, h5 and csv on Google Drive plus YouTube-8M features | indirect (crowd-derived highlight curve; mixed genres) |
| QVHighlights | over 10,000 YouTube videos, one query each, moment windows with 2-second clip saliency scores from 3 annotators (exact totals not on pages I opened). Unit: videos and clips | annotations CC BY-NC-SA 4.0 | no (NC) | yes, GitHub jsonl | weak (vlogs and news, not sport) |
| Panda-70M | 70,723,513 clips from 3,779,763 videos (full train); 10M and 2M subsets. Shot-boundary intervals added Oct 2024, predicted by TransNetV2, not human. Unit: clips (detector-cut) | non-commercial research terms (Snap, via search); upstream HD-VILA-100M licence applies | no | CSVs on Google Drive; clips need YouTube downloads, which are not wanted | weak (cuts are machine-made; generic video) |
| InternVid-10M-FLT (HF) | 10,000,000 clips with timestamps and captions (paper says 234M clips and 7M videos, but only the 10M subset is released). Unit: clips | CC BY-NC-SA 4.0 | no (NC) | login: gated on HF; 13.4 GB jsonl | weak |
| YouTube-8M | 1.9 billion frame-level feature vectors at 1 fps, 8M video URLs, 237K human-verified segment labels (search only for 237K). Unit: frames | not shown on a page I opened (Mr. HiSum README says CC BY 4.0) | no (unverified) | features are free; about 1.5 TB, too big for a free notebook; use a shard | weak (generic; sport categories present, no cuts) |
| Kinetics-GEB+ | 176,681 event boundaries in 12,434 Kinetics-400 videos (about 40K in the filtered set). Unit: boundaries (event changes, not camera cuts) | not stated | no | Google Drive links from GitHub | weak |
| MultiSports | 28,514 train + 10,116 val action tubes (38,630) in four sports; about 3,200 clips and 907K boxes (search only). Unit: tubes | CC BY-NC 4.0 | no (NC) | login: gated on HF; 65.1 GB | weak (action spotting; clips, not broadcasts) |
| MovieNet | 1,100 movies, 42K scenes, 1.1M character boxes; shot scale and movement labels (shot total not on page). Unit: scenes and boxes | OpenDataLab user agreement; no training terms shown | no | login: OpenDataLab account needed | weak (film editing, not live) |
| Cinemetrics | shot-length lists for thousands of films (sources disagree: 7,000 to 20,000; search only) | not shown | no | web only; no bulk file found | weak (pacing numbers for film) |
| HockeyAI (HF) | 1,890 frames, 7 classes (puck, player, faceoff, goal, referee, etc.); no scoreboard class. Unit: frames | MIT | yes | yes, 767 MB | weak (hockey vision, no cuts) |
| infactory camera-detection (HF) | 650 frames from 10 soccer clips, 9 shot-scale classes. Unit: frames. Full set is for sale | CC BY-NC 4.0 | no (NC) | yes, sample only | weak |
| TVSum / SumMe | 50 videos (20 raters) and 25 videos (15-18 summaries). Unit: videos | TVSum CC BY 3.0 per its GitHub (search only); SumMe not found | no (SumMe unclear; both tiny) | yes | weak |
| DirCast (esports) | 27 Dota 2 matches with director choices and caster commentary; no cut count, no release link. Unit: matches | not stated | no | not released as far as pages show | direct in idea (director switching + commentary), but esports and unavailable |
| Smart Director, USL soccer camera selection | about 99 h of 6 Chinese Super League matches (search snippet), and 13 games with 3 PTZ views vs director ground truth. Unit: matches | not stated | no | no release statement | direct in idea (real director cuts), but not available |

## Cut-rhythm numbers found (cite before using)

- Soccer broadcast (SoccerNet-v2 paper, search snippet): a camera change about every 9 seconds, replay shots average 6.8 s, about 14% of transitions are logo transitions.
- Film (Cutting and others, search snippets): average shot length fell from 8-10 s in the 1960s to 3-4 s in 2005; commercials averaged under 2 s by 1991. Average shot length hides the spread; look at the full distribution.
- SoccerHigh: official match summaries average 3:19, about 84% gameplay.
- Sports-specific shot-length tables for NFL, NBA or NHL: not found.

## Gaps (searched, nothing public found)

1. Live director or multi-camera switching with released labels for NFL, CFB, NBA, NCAA or NHL: none found. The three director sets (DirCast, Smart Director, USL) have no release statement.
2. Score-bug and game-clock reading: no labelled public set. SoccerNet used clock OCR internally; ScoreSight is an OCR tool, not data.
3. Replay detection outside soccer: a 2012 logo-transition set (28 logos, 6 sports, 3,907 minutes) exists in a paper, with no release found.
4. What a sports broadcast aired, when: only commercial EPG feeds. GDELT TV and the Internet Archive TV News Archive cover news, not sport, and no licence was shown.
5. American football play boundaries in broadcast video: only patents and a 2004 paper.

## Best three for RedZone

1. SoccerNet-v2 labels: 301,883 timestamped events for cuts, replays and dead-ball actions. Check the label licence first (unclear counts as no).
2. SportsShot: the only sports shot-boundary and shot-type set found, but NC and gated.
3. TV News Commercial Detection (UCI): 129,685 shots, CC BY 4.0, and it teaches break versus content.

Licensed and fetchable now: UCI, ClipShots and AutoShot labels, SoccerNet-Echoes, NBA Games.
