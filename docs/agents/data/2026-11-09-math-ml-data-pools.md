---
id: D-FIELDS-B
title: Math and machine learning data pools by subcategory, with pretrained models and a paid-source table (replaces the first 13-field scope; old findings kept as an appendix)
date: 2026-11-09
stream: data
author: data research agent (Claude Sonnet 5.5)
status: draft
supersedes: []
sources: 113
verified: 99
affects: []
gate: []
---

# D-FIELDS-B: math and ML, by subcategory

Nothing downloaded. No secrets read. No sign-ups. Source and verified counts are a hand tally (about plus or minus 3).

Rules used: sizes come from the source's own page; **not stated** means the page gave no count; **derived** means my arithmetic, never counted as a stated figure; **synthetic** rows are never counted. "50M" means 50 million real data points in the unit named.

## Step 1: what to do first (under 2 minutes)

Open the Kaggle dataset `alexxl/zvuk-dataset` (CC BY 4.0, 244,673,551 interactions, 2.66 GB). It is the smallest real file that already clears 50M timestamped events.

## Headline

- **Reach 50M with a stated figure:** point-process-style event streams, time series, queueing, game theory (Lichess), sequence models/event forecasting, recommender logs, click logs, pretraining corpora.
- **Reach 50M only as derived:** survival analysis rows (558M, but only 21.6k failures), audio-video (embeddings ~20.9M derived: below 50M).
- **Do not reach 50M (true totals below):** causal inference (26M real), learning to rank (counts not stated), offline RL with real logged decisions (26M), saliency/attention, Bayesian, information theory (no dataset exists; they are methods).
- Pretrained open models already carry much of the time-series knowledge (section C). For time series, use them zero-shot before training on raw rows.

## A. Summary table (by subcategory)

| # | Subcategory | Link to brain task | Best large sources (opened) | True total (unit) | License OK | Kaggle fetch OK | 50M? |
|---|---|---|---|---|---|---|---|
| M1 | Probability / stochastic processes | medium (moment value, base rates) | no dedicated data; use Lichess + Backblaze below | see M8, M4 | n/a | n/a | via others |
| M2 | Point processes (Hawkes, arrivals) | **strong** (when is next event) | EasyTPP sets (taxi, retweet, taobao, stackoverflow, amazon, volcano, earthquake); big real streams: Zvuk, Twitch, Yambda, GDELT | EasyTPP taxi: ~2k sequences, ~74k events (my estimate from visible sequences); others not stated. Large streams: Zvuk 244,673,551; Twitch 124M; Yambda 4.79B | EasyTPP Apache-2.0; Zvuk CC BY 4.0; Twitch Apache-2.0 (Kaggle) | yes | yes (streams), no (benchmarks) |
| M3 | Time series | **strong** | GiftEvalPretrain, Time-300B, Wikimedia pageview series, chronos_datasets, LOTSA | GiftEvalPretrain 4.5M series, 230B points; Wikimedia HF 238,040,330 article rows (derived 1,025 points each) | Apache-2.0 (GiftEval, Time-300B), CC0 (Wikimedia), chronos "other" | yes | **yes** |
| M4 | Survival analysis | medium (time-to-event: when does a play end) | Backblaze Drive Stats | 558,148,607 drive days; **21,609 failures** | no named licence; cite Backblaze, do not sell data | yes (zip or Iceberg) | rows yes; events no |
| M5 | Bayesian inference | weak (method, not data) | none needed | n/a | n/a | n/a | n/a |
| M6 | Optimization and scheduling | strong, kept as math only | Azure traces, NYC TLC (see appendix) | Azure V2 ~1.9B readings | CC BY 4.0 | yes | yes |
| M7 | Queueing theory | strong (one screen, many games) | Azure Functions/VM traces, TLC | see appendix | CC BY 4.0 / TLC no licence | yes | yes |
| M8 | Game theory | medium (decisions under clocks) | Lichess standard games | 7.14B games (HF viewer) | CC0 | yes | **yes** |
| M9 | Information theory | weak (method) | none; compute on any stream | n/a | n/a | n/a | n/a |
| M10 | Causal inference | medium (logged feedback) | Open Bandit (ZOZOTOWN) | 26M impressions | **licence not stated**, cite paper | page download | **no** |
| L1 | Sequence models / event forecasting | strong | Yambda, Zvuk, Twitch, GDELT, Lichess | Yambda 4.79B; Zvuk 244.7M; Twitch 124M | Yambda Apache-2.0 "research only"; Zvuk CC BY 4.0 | yes | **yes** |
| L2 | Time-series forecasting benchmarks | strong | GIFT-Eval family, Monash archive, LOTSA | GiftEvalPretrain 230B points; Monash 30 datasets (total not stated) | Apache-2.0; Monash "research only", no licence | yes | yes |
| L3 | Learning to rank | medium (rank live games) | MSLR-WEB30K | >30,000 queries; pair count not stated | online agreement required | OneDrive link, unverified | **unknown** |
| L4 | Recommender logs (very large) | medium | Yambda 4.79B, Criteo 4B lines, Zvuk, Twitch, MIND 15M | as listed | Yambda Apache-2.0; Criteo terms not shown; MIND Microsoft Research License | Yambda, Zvuk, Twitch yes | **yes** |
| L5 | Click and attention logs | medium (what viewer watches) | Criteo 1TB, MIND, Yambda played-percentage | Criteo "4B event lines"; MIND 15M impressions | Criteo unverified; MIND research-only | Criteo host unconfirmed | yes (Criteo) |
| L6 | Offline RL | medium-weak | Open Bandit (real); Minari/D4RL are simulated | real: 26M; simulated not counted | see M10 | yes | **no** |
| L7 | Video action spotting, highlights | strong but sports-specific is other researcher's | YouTube-8M, QVHighlights, SoccerNet features, SoccerTrack | YT8M ~8M videos, 1.9B frame features; others small or unstated | YT8M licence not on page; QVHighlights "other"; SoccerNet features GPL-3.0 | features yes; raw video no | features yes |
| L8 | Saliency and attention | medium | DHF1K (catalog), Kaggle eye-tracking sets | all small | mixed (CC0 to NC) | yes | **no** |
| L9 | Multimodal audio-video | medium | AudioSet, VGGSound, FSD50K | AudioSet 2,085,544 segments; VGGSound >200k clips; FSD50K 51,197 | CC BY 4.0 labels; **no audio provided** | embeddings only | **no** |
| L10 | Pretraining corpora linked to sequences | strong | GiftEvalPretrain, Time-300B, chronos_datasets, Wikimedia | 230B points (GiftEval); Time-300B 1.24 TB, count not stated | Apache-2.0 / "other" | yes | **yes** |

---

## B. Subcategory detail

### M2 Point processes and L1 event forecasting

- **EasyTPP on HF** (`easytpp/taxi`, retweet, taobao, stackoverflow, amazon, volcano, earthquake): Apache-2.0. Taxi has about 2k sequences (train 1.4k, validation 200, test 400) and 36-38 events per visible sequence, so about 74k events (my estimate). Counts for the other six are not stated. These are benchmark-scale, not 50M.
- **Real large timestamped streams** (use for training arrival-time models):
  - Zvuk (Kaggle `alexxl/zvuk-dataset`): 244,673,551 interactions, 382,790 users, 1,506,950 tracks, about four months, CC BY 4.0, 2.66 GB.
  - Twitch Interaction Dynamics (Kaggle `pypiahmad/twitch-interaction-dynamics`): 124M interactions, 15.5M users, 465k streamers, 6,148 ten-minute steps, 43 days, Apache-2.0, 19 GB. Same data as the catalog's LiveRec row; Kaggle copy gives a licence (Apache-2.0).
  - Yambda (HF `yandex/yambda`, also Kaggle `ploshkin/yambda` 13.8 GB, Apache-2.0): about 4.79B interactions (4.65B listens, 89.3M likes, 11.6M dislikes), 1M users, 9.39M tracks; timestamps in 5-second bins; 50M, 500M and 5B subsets. Authors say "published exclusively for scientific and research purposes."
  - GDELT events: "over a quarter-billion records" (page). Free for any use with attribution.
- **Honest note:** none are sports. They teach arrival-time and next-event modelling; the sports transfer is untested.
- **Fetch:** `load_dataset("yandex/yambda", data_dir="flat/50m", ...)` per its card; Kaggle datasets attach to a notebook. Start with the 50M subset.

### M3, L2, L10 Time series, forecasting benchmarks, sequence pretraining corpora

- **Salesforce GiftEvalPretrain** (HF): 4.5M series, 230 billion data points, 71 univariate and 17 multivariate datasets, 13 frequencies, 975 GB, Apache-2.0. Page says no leakage versus the GIFT-Eval test split. Viewer fails to load; use the files or the GIFT-Eval repo.
- **Time-300B** (HF `Maple728/Time-300B`): 1.24 TB, Apache-2.0; point count **not stated** on the page (name implies 300B; I do not count the name). Loading via the time-moe GitHub repo.
- **autogluon/chronos_datasets** (HF): 67 subsets, 10M-100M rows (size tag), series count and observations not totalled, licence "other" (check per subset). Includes `training_corpus_tsmixup_10m` (~8.56M rows). TSMixup is built by mixing series: counts of mixed rows are partly synthetic, so exclude it from "real".
- **Wikimedia pageview time series** (HF `jeremycochoy/wikimedia-pageview-timeseries-raw`): 238,040,330 article rows, hourly windows of 1,025 points, daily 1,025, weekly 148, 2011-12-01 to 2016-10-02, CC0-1.0. Derived: 238M x 1,025 ≈ 2.4e11 points (arithmetic; the viewer counts rows). Stored size 686 GB in header vs ~640 GB in the README table.
- **LOTSA** (HF): card shows 1,346,185 rows (series), 174 subsets, 925 GB; observation count not on page; "apache-2.0" tag but "research purposes only" text.
- **Monash archive**: 30 datasets (58 variants), "research purpose only", no licence named.
- **Link:** a forecasting model for "audience, score margin or arrival rate over time" fits directly; these corpora are non-sports, so they pretrain the generic part.
- **Fetch:** HF datasets or files in a Kaggle notebook; most are TB scale, so stream one subset. **Pretrained forecasters (section C) mean we may not need these rows at all.**

### M4 Survival analysis

- **Backblaze Drive Stats:** 558,148,607 lifetime drive days, 21,609 failures, 2013 to Q2 2026 (page). 2025 alone 115,638,676 rows. Quarterly zips 270 MB to 2.1 GB; also read-only Apache Iceberg copy on B2.
- **Caveat that matters:** only 21,609 failure events. The 558M rows are censored observations. Statistical power is set by events, not rows.
- **Terms:** no named licence; open-source use terms (cite Backblaze, do not sell the data itself; derivative works may be sold); the terms/training clause was not summarised on the page. Treat as ok for personal research, unverified for training.
- Patient survival sets (SUPPORT, METABRIC) rejected: patient data.

### M5 Bayesian, M9 information theory, M1 probability

No dataset is needed or exists as a pool. Use any stream above plus libraries. Calling these "50M" would be padding. Weak link: they are methods (calibration, entropy of play outcomes), not data.

### M8 Game theory

- **Lichess standard games** (HF `Lichess/standard-chess-games`): 7.14 billion rows (viewer), CC0 1.0, fields include ratings, result, termination, TimeControl string, full move text. Clock time column absent from the schema; whether move text carries clock comments was **not verified**. The monthly database page (database.lichess.org) confirms CC0 and `.pgn.zst` monthly files, 166 monthly files from 2013-01 to 2026-10; per-month game counts for standard were not visible to me.
- **Link:** sequential decisions under a clock with a known game tree, a cheap lab for "leave or stay" policies. Not sports.
- **People check:** player handles are public pseudonyms; do not join to real identities.
- Variant totals seen on the page (antichess 38.3M, atomic 29.8M, chess960 30.2M, crazyhouse 31.0M) are not needed.

### M10 Causal inference and L6 offline RL

- **Open Bandit Dataset** (ZOZO): 26M impressions, logged bandit feedback with propensity scores from a real A/B test of two bandit policies, item and position and click. **No licence on the page**; asks to cite arXiv 2008.07146. Counts as real logged decisions: 26M. **Below 50M.** Do not train until the licence is confirmed.
- **Minari/D4RL** (Farama): 31 D4RL, 28 MuJoCo, 57 Atari (all Expert) datasets; simulated environments (my inference); licence not on page; not counted.
- Criteo uplift and counterfactual sets: listed, no counts or licence shown.

### L3 Learning to rank

- **MSLR-WEB30K**: more than 30,000 queries, 136 features per query-URL pair, 3.7 GB; WEB10K 1.2 GB. Pair count **not stated**. Online agreement required. Hosted on OneDrive (not confirmed fetchable from Kaggle).
- **Baidu-ULTR** (HF): returned 401 to my fetch; not verified, nothing cited. Yahoo LTR and Istella not opened.
- **True total:** unknown; probably tens of millions of rows at best, unverified.

### L4, L5 Recommender and click logs

- Yambda, Zvuk, Twitch above. Criteo 1TB click logs: page says "4B event lines", 1 TB, licence not shown (terms did not render). MIND: more than 15M impression logs, ~1M users, ~160k articles, Microsoft Research License (research use, accept terms), also on Azure Open Datasets.
- Kaggle large hits (size only, counts not opened): MyAnimeList 1.9 GB, Goodreads 8.7 GB (Apache-2.0), Behance 1.1 GB, Nova 50M transactions 2.9 GB (title only), Clubhouse 9.7M 2.8 GB (CC0).
- **People check:** Goodreads, Clubhouse, Behance are user-level social data: exclude unless anonymised and permitted.

### L7 Video action spotting and highlight detection

- **YouTube-8M**: about 8 million video URLs, over 500,000 hours, 4,800 labels, frame-level features for 1.9 billion frames (one per second, Inception-V3), under 1.5 TB (blog page, 2016 post). Licence not on that page. Features only; raw video is not provided. Link: pretraining on generic video features; sports categories not verified.
- **QVHighlights** on Kaggle (`ungabunga12/qvhigh-dataset-vmr`): 143.8 GB, licence "Other (specified in description)". Video count not opened.
- **HF `OpenSportsLab/SoccerNet-ActionSpotting-Features`**: GPL-3.0 tag, 81 downloads, size not read. SoccerNet video labels are gated (catalog).
- **Kaggle `atomscott/soccertrack`**: 6.77 GB, LGPL-3.0. Soccer only.
- **True total:** only YouTube-8M clears 50M (1.9B frame vectors), and it is not sports. Sports-specific action data belongs to the other researcher.

### L8 Saliency and attention

Kaggle eye-tracking sets found are small (0.15-6 GB), mostly faces or gaze direction, mixed licences (CC0, PDDL, CC BY-SA, NC). None has near 50M. Overlap with the catalog's DHF1K. **True total: well under 50M; no source verified at scale.**

### L9 Multimodal audio-video

- AudioSet: 2,085,544 ten-second segments, 527 classes, labels CC BY 4.0, ontology CC BY-SA 4.0; **raw audio not provided**; 1 Hz embeddings only. Derived: ~20.9M one-second embedding vectors (2.0855M x 10), below 50M.
- VGGSound: over 200,000 ten-second clips, over 550 hours, 300+ classes, CC BY 4.0; the page says download links are no longer available and the CSV only lists YouTube URLs.
- FSD50K: 51,197 clips, 108.3 hours, per-clip CC0/BY/BY-NC/Sampling+.
- **True total below 50M.** Use pretrained audio models instead (section C).

---

## C. Pretrained open models and embeddings (may beat raw rows)

| Model | What it carries | Size | Licence | Training data stated | Use |
|---|---|---|---|---|---|
| amazon/chronos-bolt-base | zero-shot probabilistic forecasting | ~205M params | Apache-2.0 | "nearly 100 billion time series observations" (datasets not listed) | forecast arrival rates, margins; no training needed |
| google/timesfm-2.5-200m-pytorch | zero-shot forecasting, 1,024 context, 256 horizon in example | ~200M | Apache-2.0 | GiftEvalPretrain, Wikimedia Pageviews (to Nov 2023), Google Trends top queries, synthetic | same |
| Datadog/Toto-Open-Base-1.0 | multivariate, high-frequency operational series | ~151M | Apache-2.0 | over 2 trillion points; ~1T internal Datadog (no customer data), public GIFT-Eval Pretrain and Chronos; about one-third synthetic | multi-stream monitoring analogue |
| Salesforce/moirai-2.0-R-small | universal forecasting | 11.4M | **CC-BY-NC-4.0**, research only | GIFT-Eval subsets, Chronos mixup, KernelSynth, internal data | non-commercial personal use only |
| facebook/vjepa2-vitl-fpc64-256 | self-supervised video representation | ~0.3B | MIT | pretraining size not stated | video embeddings for highlight/dead-ball features |
| laion/larger_clap_general | audio-text embeddings | not stated | Apache-2.0 | datasets not named on page | crowd-noise embeddings, zero-shot labels |

Not opened (so not claimed): Lag-Llama, SigLIP, VideoMAE, Whisper. A 200M-parameter model runs on Kaggle's free GPU; keep Mac use to inference of small checkpoints only (project rule: Mac stays light).

---

## D. Big pools and catalogs of catalogs (what I opened)

| Pool | Result | Useful for size search? |
|---|---|---|
| Hugging Face Hub search API | works without login: `huggingface.co/api/datasets?search=...&sort=downloads`. Top "time series" hits include PretrainGiftEval, Time-Series-Library (CC BY 4.0), Timeseries-PILE (MIT), wikimedia pageviews (CC0). "click logs" and "recommendation interactions" returned almost nothing: keyword search only | yes, for names; no row counts in list |
| Kaggle Datasets API | works without login: `kaggle.com/api/v1/datasets/list?search=...&minSize=...`; `.../datasets/view/{owner}/{slug}` returns description, licence, bytes. No row counts, noisy, many NC licences. Found Zvuk, Twitch, Yambda mirror, QVHighlights, SoccerTrack | yes, best by bytes |
| AWS Open Data | registry page opened; total count not seen (view showed 13 matches); each dataset states its own licence; no-sign-request access (appendix) | yes |
| BigQuery public datasets | first 1 TB of queries per month free; querying public data needs no billing per the page; needs a Google account | yes, for GDELT-style tables |
| Azure Open Datasets | NYC yellow taxi: about 1.5B rows to 2018 (page); original terms apply | yes |
| Zenodo | API `hits.total` = 772,735 dataset records | breadth only |
| Harvard Dataverse | search total 307,659 datasets (all topics) | breadth only |
| Figshare | API gave no total | no |
| DataCite | query "point process" dataset: 82 results; first is unrelated | breadth only |
| data.europa.eu | "time series": 144,099 results, first result is an INSPIRE weather observation record; not mined | breadth only |
| Academic Torrents | page claims over 419.44 TB, no dataset count or licence note | unclear |
| Common Crawl | free by HTTPS, no AWS account; per-crawl size and licence summary not on the page; no sequential-event link | no |
| Papers With Code | redirects to HF trending papers; no longer a dataset catalog | no |
| OpenML | page text did not render; not counted | no |
| UCI, Google Dataset Search, re3data | UCI and Google Dataset Search not opened; re3data API returned 500 | not covered |
| data.gov | CKAN API URL returned 404 on my first try; not retried | not covered |

**Finding:** the catalogs of catalogs tell you the field exists; they rarely state row counts. The big real sets that passed a licence check came from HF and Kaggle.

---

## E. Paid sources (Nick decides; nothing signed up)

| Company | What | Published price (from its page) | Terms allow model training? |
|---|---|---|---|
| Databento (databento.com/pricing) | exchange market data (tick) | historical priced per GB uncompressed, **rate not shown on page**; Standard $199/month; Plus $1,750/month license fees, annual; Unlimited $4,500/month, annual; $125 free credits for new users, expire in 6 months (signup needed, I did not sign up) | **not stated** on pricing page; ask |
| Betfair Historical Data | exchange odds history | page returned 403, price not seen | not seen |
| Nixtla TimeGPT | forecasting API | pricing page 404, price not seen | n/a (model, not data) |

Sports data vendors (Sportradar and others) belong to the other researcher and were not opened. Everything in sections B and C is $0.

---

## F. Open questions for Nick (one short list)

1. Is Yambda's "scientific and research purposes only" wording fine for a personal sports model? (default: yes, personal)
2. Do you want me to confirm the Open Bandit and Criteo licences before use? (default: skip them until then)

---

## Appendix: first-round findings (13 fields, kept short)

Full detail was in the earlier draft; headline rows only. Strong or medium links first.

| Field | Link | Datasets (opened) | True total | Licence | Fetch |
|---|---|---|---|---|---|
| Operations research / queueing | strong | Azure Public Dataset (VM V2 ~1.9B readings; repo LICENSE CC BY 4.0), NYC TLC parquet, Google Borg 2019 (CC BY 4.0) | Azure V2 ~1.9B; yellow taxi ~1.5B rows to 2018 (Microsoft page) | CC BY 4.0; TLC no licence | yes |
| Seismology / event detection | strong | INSTANCE (1,159,249 quake + 132,330 noise 3-channel waveforms, CC BY 4.0), STEAD (~1.2M, CC BY 4.0) | ~1.29M and ~1.2M waveforms (samples would be derived) | CC BY 4.0 | INSTANCE yes; STEAD host unverified |
| Meteorology (weather on games) | medium | GHCN-Daily "nearly 2 billion data values" (CC0, S3 `noaa-ghcn-pds`), ISD hourly (~600 GB), SEVIR | ~2B values | CC0 / open | yes, no-sign-request |
| Epidemiology / dispatch triage | medium | NYC EMS (~30.4M inferred), SF fire/EMS calls (7,491,628) | ~37.9M, below 50M | no licence field | yes |
| Aviation | medium | BTS on-time (count not stated; ~230-280M is my arithmetic from a snippet), OpenSky flight lists (8.1 GB, non-commercial) | not stated | BTS gov; OpenSky NC | yes |
| Finance / betting (math only) | medium | Binance public data (CC BY-NC-SA, personal OK), football-data.co.uk (free), Polymarket (history capped), Kalshi (orderbook public) | not stated | see page | Binance yes |
| Actuarial | weak | OpenFEMA NFIP policies v3 74,739,461 records; terms "statistical research" only | 74.7M | borderline | yes |
| Astronomy | medium | ZTF DR24 5.32B light curves; alert archive nightly tarballs; Rubin alerts public via brokers | 5.32B | no licence on pages | alerts huge, bulk unverified |
| Crowds / fandom | medium | Wikimedia pageviews (CC0), GDELT (>250M, free, attribution) | GDELT >250M | CC0 / free | yes |
| Audio signals (EE) | medium | FSD50K, AudioSet (labels only) | 51,197 clips | per-clip CC | FSD50K yes |
| Ecology (Movebank) | weak | 12.0B locations platform-wide, public subset not stated | unknown | per-study CC | account needed |
| Turfgrass (NTEP) | weak | click-through agreement, amount not stated | small | usage statement | no |
| Maritime AIS | weak | NOAA AIS 2009-2026, counts not stated | not stated | not stated | files |

Dropped per the new scope unless the link is strong: seismology, astronomy, turfgrass, ecology (kept above only as appendix rows).

## Not verified

Baidu-ULTR (401), Criteo licence, Open Bandit licence, Betfair and Nixtla prices (403, 404), Lichess clock comments and standard-game count per month, QVHighlights video count, Time-300B point count, YouTube-8M licence, UCI, Google Dataset Search, re3data, data.gov, OpenML total.
