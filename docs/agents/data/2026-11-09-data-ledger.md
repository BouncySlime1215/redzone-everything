---
id: D-LEDGER
title: "The one ledger of what data RedZone really has: deduplicated sources, unique tokens, license tiers and justified model size"
date: 2026-11-09
stream: data
author: data ledger agent (Claude Opus 5.5), data job 9
status: draft
supersedes: []
sources: 13
verified: 13
affects: []
gate: []
---

# D-LEDGER: what we really have

Inputs opened: the 9 data reports dated 2026-11-09, `planner/2026-11-09-the-mind.md` §7-8, `noggin-foundation-model.md` (NFM doc, for the corpus table), `data/catalog.md` (Nov 8, for file sizes) and `docs/memory/decisions.md`. No web work, nothing downloaded, no secrets read. Row counts are the reports' own. **Every token count and every model size below is an estimate.** No shard was tokenized for this ledger.

## Step 1 (under 2 minutes)

Nick: decide whether Yambda and GiftEvalPretrain stay out. Recommended answer: **out**. They add 14.2 B tokens of music and generic forecasting, and 0 direct sports tokens.

## Bottom line

1. **Direct sports tokens we can use today: about 0.57 B** (clean plus Nick's personal-use ruling). That justifies **about 28 M parameters** from scratch, and costs about 2 T4-hours.
2. **The earlier 565 M from-scratch estimate no longer holds.** It needs 11.3 B unique tokens. Direct data is 20x short. Even all tokens under today's rulings (about 5.0 B) justify only about 248 M, and most of those tokens are not about sports.
3. **The 1 to 2 B live-brain target survives as a fine-tune** of Qwen3-1.7B, because the base was pretrained elsewhere. Its training is not limited by our token count. Its limit is memory on the 8 GB Mac, which is still unmeasured.
4. **Zero rows of direct viewer preference and zero rows of US broadcast cut labels exist in anything licensed.** More rows do not fix this. Nick's own logs and a hand log of real RedZone do.

## License tiers

| Tier | Meaning | Rule |
|---|---|---|
| **T1 Clean** | License text permits this use. Non-commercial (NC) licenses are flagged and pass for personal use, per D-CONFIRM's rule | Applied consistently here. D-VIDEO marked NC as "no"; this ledger overrides that |
| **T2 Personal-use sports** | Nick's standing ruling, 2026-11-05 11:20 PM ET: "data = B ... since it's a personal project" (`docs/memory/decisions.md`, read at source) | Public sports endpoints with restrictive terms. Every dataset and every model trained on them is tagged "personal-use, not for commercial release". Public endpoints only; no logins, paywalls, CAPTCHAs or bot-block bypasses; respect rate limits and robots |
| **T3 Open question** | Non-sports research-only or license-conflict sets, plus login-gated sets. The ruling covers sports data only | Nick has not ruled |
| **B Blocked** | Unknown provenance, scraped from unnamed sites, owner terms bar it, or no license stated | Unclear counts as no |

D-CONFIRM's FAIL verdicts on the NBA sets are superseded by the ruling. Women's college basketball is off (Nick, Nov 4 and Nov 8), so wehoop's about 250 M tokens from the NFM table are excluded everywhere.

## Token rules

| Class | Tokens per item | Why |
|---|---|---|
| Play-by-play event, ball, pitch | 6 | Measured vocab v1 (NBA 2025: 3.76 M tokens over 625,000 events) |
| Timestamped interaction with outcome (recommender, session logs) | 3 | Item, time gap, outcome. Judgment |
| Music listens | 2 | Heavy repeat listening. Judgment |
| Tabular ad rows with no user or sequence key | 0.1 | A sequence model cannot use them and feature values repeat. Judgment, could be 3x either way |
| Autocorrelated sensors and utilization | 0.005 to 0.05 per sample | About 1 token per independent window. Judgment |
| Wikimedia pageviews | 1 per article-month | 3 M persistent articles x 135 months. An hourly series repeats itself |
| Forecasting corpora | 1 per 50 points | Seasonal series. Judgment |

The judgment rows are good to about 3x either way. The direct rows are far tighter, with one exception: CBB.

"Direct" means real sports event logs or sports broadcast labels. "Six-league" is the subset in Nick's league order. Rows are never summed across units; only tokens are.

## 1. The deduplicated table

### T1 Clean, direct sports

| Source (each counted once) | Real data points, unit | License | Tokens/item | Est. unique tokens | Direct |
|---|---|---|---|---|---|
| Retrosheet play-by-play | 16,756,076 plays, 208,684 games, 1908-2025 | Free for any use with credit | 6 | 100.5 M | Six-league (MLB) |
| nflverse pbp (mirrors: `ran-codes`, `benbaldwin`, `toddsteussie`, `CeeWill75`) | 1,239,130 plays | CC-BY-4.0 | 6 | 7.4 M | Six-league (NFL) |
| Cricsheet (mirrors: `ariadaikalam`, `amarharolikar`, `veeralakrishna`) | 5,852,078 balls | ODC-By | 6 | 35.1 M | Other sport |
| SoccerNet-Echoes, counted as one original-language Whisper run | 923,181 commentary segments (4.39 M rows are three overlapping runs) | CC BY 4.0 (D-FIELDS-A says Apache-2.0; both permit) | 10 (cross-check: 500 games of speech is about 9 M tokens) | 9.2 M | Other sport, machine text |
| KBO play-by-play (`slothman3878`) | 100 K to 1 M rows (size tag; midpoint used) | CC BY 4.0 | 6 | 3.0 M | Other sport |
| SoccerTrack v2 | 21,428 ball events | CC BY 4.0 | 6 | 0.13 M | Other sport |
| IDSSE / DFL tracking | 7 matches, about 945 K frames (inferred) | CC BY 4.0 | 0.05 | 0.05 M | Other sport |
| **Subtotal** | | | | **155.5 M** (six-league 108.0 M) | |

Conditional upgrade: if the `Sarthak213/doosra-cricket` provenance check passes (D-CATALOG §3), cricket becomes 11,615,100 balls, adding 34.6 M tokens. Not counted.

### T1 Clean, not direct

| Source | Real data points, unit | License | Tokens/item | Est. unique tokens |
|---|---|---|---|---|
| KuaiRand-27K | 323,464,444 interactions, including 1,186,059 random exposure | CC BY 4.0 (Zenodo) vs CC BY-SA (site); follow SA | 3 | 970 M |
| GDELT events | 250 M+ records (floor) | Unrestricted, cite | 3 | 750 M |
| OTTO | 230,567,389 session events | CC BY 4.0 | 3 | 692 M |
| Zvuk | 244,673,551 listens | CC BY 4.0 (mirror status unstated) | 2 | 489 M |
| Wikimedia pageviews (one upstream: official dumps 153.48 B rows; HF `jeremycochoy` 238 M windows; Zenodo 2024) | 153,479,054,298 hourly rows to about 2018 | CC0 | 1 per article-month | 405 M |
| Lichess broadcast | 101,228,895 half-moves with clocks | CC BY-SA 4.0 | 2 | 202 M |
| KuaiRec | 12,530,806 interactions | CC BY 4.0 / SA | 3 | 38 M |
| Azure VM trace V2 | 1.9 B utilization readings | CC BY 4.0 / MIT | 0.02 | 38 M |
| KASANDR | 17,764,280 offer clicks | CC BY 4.0 | 2 | 36 M |
| SoccerMon (privacy check first) | 6 B+ GPS positions | CC BY 4.0 | 0.005 | 30 M |
| Wikipedia clickstream | 25 M pairs, one month | CC0 | 1 | 25 M |
| INSTANCE seismic | 1,291,579 waveforms | CC BY 4.0 | 10 | 13 M |
| Amazon ESCI (LTR) | 2,621,738 judgments | Apache-2.0 (repo) | 3 | 8 M |
| EsportsBench | 2,782,828 results | CC BY-SA 3.0 | 2 | 6 M |
| AudioSet labels | 2,085,544 segments | CC BY 4.0 | 1 | 2 M |
| Small production labels: UCI TV News 129,685 shots (6), MovieCuts 173,967 cuts (3), ClipShots 166,756 transitions (2), AutoShot 11,606 shots (2) | as listed | CC BY 4.0; repo MIT, data terms to check | as listed | 1.6 M total |
| Tail: Backblaze (558,148,607 drive-days, but **21,609 failures**; 6 per failure), PAMAP2, FHAR field hockey IMU | as listed | as reported | low | 0.3 M total |
| **Non-NC subtotal, not direct** | | | | **3,705 M** |

NC flagged, passes for personal use:

| Source | Real data points, unit | License | Tokens/item | Est. unique tokens |
|---|---|---|---|---|
| Criteo 1TB | 4.37 B rows | CC BY-NC-SA 4.0 | 0.1 | 437 M |
| Aalto keystrokes | 136 M keystrokes | Research and NC with attribution | 1 | 136 M |
| KuaiSAR | 19,664,885 actions | CC BY-NC-SA | 3 | 59 M |
| Criteo Attribution | 16.5 M rows | CC BY-NC-SA | 3 | 50 M |
| MAESTRO, Criteo Uplift, QVHighlights | 7.04 M notes; 13,979,592 rows; 10,000+ videos | NC variants | 1; 0.1; 10 per video | 8.5 M total |
| **NC subtotal** | | | | **690 M** |

Licensed, with no count stated, so not counted: OpenF1 telemetry, Tennis Match Charting (5,000+ matches), SC2EGSet, CESNET, LargeST, `mediawiki_history`, Lakh MIDI, Capture-24 hours.

Excluded from all totals on purpose: **Lichess standard games**, 7.14 B games, CC0. At about 70 plies per game (my assumption) that is about 500 B tokens. It would "justify" 25 B parameters and swamp every other number while teaching nothing about dead balls.

Also out of scope: the LLM text corpora in `catalog.md` (FineWeb, C4 and others). They are general text for a language model's pretraining, which the open base already has, and they do not help a sports event model.

### T2 Personal-use sports (Nick's ruling)

| Source | Real data points, unit | Terms | Tokens/item | Est. unique tokens | Direct |
|---|---|---|---|---|---|
| hoopR / ESPN CBB men's pbp 2006-2026 | **Count not confirmed.** About 0.9 GB. File-size scaling from NBA gives about 160 M tokens; NFM doc says about 300 M. Central estimate used | ESPN, personal | 6 | **200 M (160-300 M)** | Six-league |
| NBA play-by-play (one feed: `deocheng` is the largest; also `szymonjwiak` 18.26 M, `wyattowalsh`, `cdechoch`, `brains14482`, `eoinamoore`, `chevronronson`, hoopR, `choucsan`, `DescribeEvents`) | About 18.6 M events | NBA "private, non-commercial" | 6 | 111.6 M | Six-league |
| NHL api-web and shifts (cross-check: `jjerlee` 5.4 M events = 32 M tokens; MoneyPuck 2,079,359 shots are a subset, not added) | Count not confirmed | NHL "private, non-commercial" | 6 | 40 M (NFM estimate) | Six-league |
| Statcast pitches (`Jensen-holm`; `jab13`, `yasunorim`, `pschale`, `s903124` are subsets) | 8,478,976 pitches | MLB non-bulk wording, covered by ruling | 6, less 1.9 M overlapping plate appearances already in Retrosheet (my arithmetic) | 39.5 M net | Six-league |
| ESPN CFB pbp 2004-2025 | Count not confirmed; about 22 seasons at about 1 M tokens each | ESPN, personal | 6 | 20 M (NFM-consistent estimate) | Six-league |
| **Subtotal** | | | | **411 M** | |

Classified restrictive in `terms.json` but with no count: StatsBomb (open data, not counted). The BigQuery NCAA table (over 4 M or 42 M plays, sources conflict) is the same games as hoopR and is not added.

Repo wall-clock games (NBA 17,219, NFL 3,811, NHL 9,218, planner §7) are a **subset** of the above. They are not extra tokens, but they are the most valuable subset for timing.

### T3 Open question (Nick has not ruled)

| Source | Real data points, unit | Issue | Tokens/item | Est. unique tokens |
|---|---|---|---|---|
| Yambda (true total; the 5.3 B figure double counts nested subsets) | 4,785,859,887 events | Apache tag plus "scientific and research purposes" | 2 | 9,572 M |
| GiftEvalPretrain | 230 B points, 4.5 M series | Apache tag plus research-only | 1 per 50 | 4,600 M |
| Open Bandit (`zozonext` CC BY copy) | About 22.9 M rows | Owner page states no license | 3 | 69 M |
| MIND | 15 M+ impressions | Microsoft Research License | 3 | 45 M |
| KuaiLive | 5,357,998 interactions | CC BY vs NC-SA conflict | 3 | 16 M |
| Panda-70M, InternVid-10M | 70.7 M, 10 M clips (machine-cut, captions) | NC research; InternVid gated | 0.1 | 8 M |
| Mr.HiSum | 31,892 videos | Academic NC vs CC BY conflict, plus YouTube terms | 20 per video | 0.6 M |
| SportsShot, MultiSports (need Nick's login) | 30 K+ shots; 38,630 tubes | CC BY-NC, gated | 2 | 0.14 M, direct |
| **Subtotal** | | | | **14,310 M** (direct 0.14 M) |

### B Blocked

| Source | Real data points, unit | Why blocked | Tokens/item | Est. unique tokens | Direct |
|---|---|---|---|---|---|
| Amazon Reviews 2023 | 571.54 M reviews | No license shown | 3 | 1,715 M | No |
| NYC TLC trips | About 1.5 B rows | No license | 1 | 1,500 M | No |
| Polymarket trades | 1,266,885,846 trades (sports share unknown; NBA subset 10.05 M) | Terms bar scraping and data mining; not a sports API, so outside the ruling | 1 | 1,267 M | No |
| OpenDota | 1,191,768,403 matches | License conflict; match-level | 1 | 1,192 M | No |
| Goodreads, Twitch LiveRec | 225.4 M; 124 M interactions | Owner terms unknown; no license | 3 | 1,048 M total | No |
| YouTube-8M, MovieLens, EMS, FEMA, football odds (37.0 M, unnamed sites), `eatpizzanot` (17.05 M, built on football-data.co.uk, which bars AI use), GHCN, Time Series Pile, smaller | As in D-CONFIRM, D-CATALOG | As reported | 0.02 to 3 | About 565 M total | No |
| `Maiyarasu` soccer events | 4,409,194 events | Provenance unproven | 6 | 26.5 M | Other sport |
| NHL_DB | 53.6 M rows | No license, no card, scraper source not named | 0.2 (tracking frames, and overlaps the NHL feed) | 10.7 M net | Six-league |
| SoccerNet-v2 labels | 301,883 events (actions, camera changes, replays) | Label license not stated; owner known | 3 | 0.9 M | Other sport |
| **Subtotal** | | | | **7,326 M** (direct 38 M) | |

Not summed because bulk access or count is unconfirmed: GDELT TV ngrams 16.9 B, ZTF 5.32 B light curves, Time-300B, BDB tracking (Nick must accept rules). CBA odds, LoL timelines, game-time tweets and Puffer are blocked and are inside the "smaller" row above.

**Cheapest unblocks worth asking about:** SoccerNet-v2 labels (the only broadcast cut and replay labels that exist; a one-line ruling or a note from the owner) and Puffer (the closest thing to live TV viewing logs; ask Stanford).

## 2. Totals

| Tier | Six-league | Direct | All tokens (excl. Lichess standard) |
|---|---|---|---|
| T1 Clean | 108 M | 155 M | 4,551 M (of which NC 690 M) |
| T2 Personal-use sports | 411 M | 411 M | 411 M |
| T3 Open question | 0 | 0.14 M | 14,310 M |
| B Blocked | 11 M | 38 M | 7,326 M |

Plausible ranges: direct T1+T2 is 0.53-0.67 B (CBB uncertainty). All tokens at T1+T2 is about 2-10 B (judgment rows).

Cross-check, partly circular: the NFM doc's 0.8 B minus women's college is 0.55 B, close to this ledger's 0.57 B direct. NBA, NFL, MLB and cricket here come from the reports' counts, but NHL, CFB and the top of the CBB range are borrowed from the NFM doc, so the agreement is weak evidence.

## 3. Ranked by how directly each helps the brain

Task scores: S strong, M some, W weak, 0 none.

| Rank | Source | Tier | Moment value | End-of-play timing | Cut pacing | Viewer preference | Note |
|---|---|---|---|---|---|---|---|
| 1 | nflverse pbp | T1 | S | M (via join to ESPN wall clocks) | M (reconstruct minimum switches) | 0 | Top league; has WP, EPA |
| 2 | NBA hoopR / ESPN | T2 | S | S | M | 0 | Most model headroom: table R² 0.37, ceiling about 0.67 |
| 3 | ESPN CFB | T2 | S | S | M | 0 | League #2, count unconfirmed |
| 4 | hoopR CBB | T2 | S | S | M | 0 | Largest direct pool; UNC |
| 5 | NHL api-web and shifts | T2 | W | S | M | 0 | Value ceiling about 0.00 per planner, so use it for timing and stoppages |
| 6 | Statcast + MLB feed | T2 | M | M | W | 0 | League last |
| 7 | Retrosheet | T1 | M | **0** | W | 0 | Big and clean but no wall clocks, and much of it is old baseball |
| 8 | SoccerNet-v2 labels | B | W | M | **S** | 0 | Only real director-cut record; soccer |
| 9 | Echoes commentary | T1 | M | W | 0 | W | Weak labels, soccer |
| 10 | KuaiRand random-exposure rows | T1 | 0 | 0 | 0 | M | The useful part is 1.19 M rows, not 323 M; short video only |
| 11 | Cricsheet | T1 | M | W | 0 | 0 | Analog sport, not in league order |
| 12 | UCI TV News, ClipShots | T1 | 0 | W | W | 0 | Break versus content, cut finding only |
| 13 | Wikimedia, sports titles only | T1 | W | 0 | 0 | W | Hourly at best: ranks games and teams, not moments |

**Own data outranks all of these for what is missing:** the ESPN wall-clock shards, R-NUMBERS (92 window-hours), and Nick's night logs. Viewer preference has **0 direct rows** anywhere public. Cut pacing has **0 US rows**. A one-hour hand log of real RedZone (about 60 minutes, no recording) is the cheapest missing truth.

**Large and likely useless for this product. Say no even though they are big:**

| Set | Size | Why |
|---|---|---|
| Lichess standard | 7.14 B games | No dead balls, no broadcast, no audience |
| Wikimedia at full scale | 153 B rows | Hourly; only the sports-title slice is usable |
| GiftEvalPretrain | 230 B points | Open forecasters (chronos-2, TimesFM 2.5) already carry it; use them zero-shot |
| Yambda, Zvuk | 4.79 B, 245 M | Music skips; transfer to sports is untested |
| Criteo 1TB | 4.37 B | Ad clicks; no user sequence |
| SoccerMon | 6 B | Athlete load, health data, autocorrelated |
| Azure, Backblaze | 1.9 B; 558 M | Servers and disks; Backblaze is really 21,609 events |
| GDELT, OTTO, Aalto | 250 M+; 231 M; 136 M | News, shopping, typing |

## 4. Resize

**Estimates.** Rule: N = D / 20 (Chinchilla, about 20 unique tokens per parameter, as the planner used). FLOPs = 6ND = 120 N². Time = 2,222 T4-hours x (N / 1 B)², calibrated at 1.2e20 FLOPs = 2,222 T4-h (15 TFLOPS achieved). Free quota 30 GPU-hours a week. One pass at D tokens. Repeating data up to about 4 epochs costs 4x the time but does not justify more parameters.

| Scenario | Count | Unique tokens D | Justified N | T4-hours | Weeks at 30 h |
|---|---|---|---|---|---|
| S1 Clean only | Six-league | 0.108 B | 5.4 M | 0.06 | under 0.01 |
| | Direct | 0.155 B | 7.8 M | 0.13 | under 0.01 |
| | All | 4.55 B | 228 M | 115 | 3.8 |
| **S2 Clean + Nick's ruling (where we are)** | Six-league | 0.519 B | 26 M | 1.5 | 0.05 |
| | **Direct** | **0.567 B** | **28 M** | **1.8** | **0.06** |
| | All | 4.96 B | 248 M | 137 | 4.6 |
| S3 S2 + research-only (Yambda, GiftEval, etc.) | Direct | 0.567 B | 28 M | 1.8 | 0.06 |
| | All | 19.3 B | 964 M | 2,063 | 69 |
| S4 Hypothetical: S3 + every blocked set (do not plan on this) | Direct | 0.605 B | 30 M | 2.0 | 0.07 |
| | All | 26.6 B | 1.33 B | 3,930 | 131 |

Direct range under S2: 26-33 M parameters. If the cricket check passes, direct gains 34.6 M tokens, adding about 1.7 M parameters.

**Against Nick's targets:**

| Target | Tokens needed from scratch | Time | Verdict |
|---|---|---|---|
| 1-2 B live brain, fine-tuned from Qwen3-1.7B | 0 new from scratch; the base was trained elsewhere (family on 36 T tokens) | 1-3 T4-h (planner) | **Holds for training.** "Live" does not yet: planner §8 budgets 0 LLM during games in v1 and about 2 GB overnight. Qwen3-1.7B is 0.98 GB of 4-bit weights, and its footprint on this Mac is unmeasured |
| 1.7 B from scratch | 34 B | 6,422 T4-h, 214 weeks | No |
| **About 565 M from scratch** | **11.3 B** | 709 T4-h, 23.6 weeks | **No longer holds** |
| NFM-S 25 M (planner's challenger) | 0.5 B | 5.6 T4-h at 2 B tokens seen | **Fits the direct data. This is the right size** |
| Earlier 40 M compute-optimal | 0.8 B | 3.6 T4-h | Superseded: it counted women's college, which is off |
| ~100 M world model (decisions.md, sized for B) | 2 B | 22 T4-h | 3.5x over the direct-justified size. Only if the S-to-M curve says size pays |

**Plainly on the 565 M:** I could not find its derivation in the repo docs or the reports; it was built on data targets that were not measured. Measured against what exists, direct sports data is 20x too small to support it. The only way to reach 11.3 B unique tokens is S3's all-token count, where 74% of tokens are music listens and generic forecasting series under research-only wording. A model that size would be mostly a music-and-forecasting model. The 20:1 rule is also an upper bound here, because NFL next-3-minute value has an estimated R² ceiling near 0.10 at any size.

## 5. What to fetch first

Check the factory manifest first: NBA, NFL and NHL wall-clock shards are already built, so fetch only gaps. All of this on Kaggle, none on the Mac. Tag every T2 item "personal-use, not for commercial release" and throttle.

| # | Fetch | Tier | Size | Why this order |
|---|---|---|---|---|
| 1 | nflverse pbp and schedules | T1 | About 0.5 GB | Top league, clean, no key |
| 2 | hoopR / ESPN NBA pbp 2002-2026 | T2 | About 0.4 GB | Most model headroom |
| 3 | ESPN CFB pbp 2004-2025 | T2 | About 1.0 GB | League #2; settles an unmeasured count |
| 4 | hoopR CBB pbp 2006-2026 | T2 | About 0.9 GB | Largest direct pool; measuring it settles the 160-300 M range |
| 5 | NHL api-web and shifts | T2 | About 5-10 GB (estimate), throttled | Timing and stoppages |
| 6 | Retrosheet (with credit notice) | T1 | About 0.3 GB | Clean MLB |
| 7 | Statcast (`Jensen-holm` parquet) | T2 | 0.83 GB | Pitch grain |
| 8 | Cricket provenance check, then cricket | T1 | 31 MB deliveries | Optional, +34.6 M tokens |
| 9 | KuaiRand-Pure and 1K | T1 | 47 MB; 1.14 GB | Random-exposure preference proxy. Skip the 9.9 GB 27K unless Pure shows signal |
| 10 | UCI TV News, ClipShots labels, SoccerNet-Echoes | T1 | Under 1 GB | Small break and commentary labels |

Do not fetch: anything in the "useless" table above. Pretrained forecasters (chronos-2, TimesFM) replace the forecasting corpora.

## Not verified

Token counts are not measured from shards. CBB, CFB and NHL counts are not confirmed by any owner page. All T1 non-direct tokens-per-item values are judgment. Provenance of the 565 M figure was not found. The Chinchilla ratio is for language models and is applied here by analogy. Kaggle's quota definition (session vs GPU hours) is still unchecked. The ~70 plies per chess game and the 1.9 M Statcast-era plate appearances are my own arithmetic or memory, not from the reports.
