---
id: D-MATH-2
title: Math and ML second pass - licence-clean replacements for the 29 failed big sets, plus open pretrained models and 1-2B base models
date: 2026-11-09
stream: data
author: data research agent (Claude Sonnet 5.5)
status: draft
supersedes: []
sources: 83
verified: 72
affects: []
gate: []
---

# D-MATH-2: second pass

Nothing downloaded, nothing signed up, no secrets read. Web text treated as data. Source and verified counts are a hand tally (about plus or minus 3); "verified" means I opened the owner's page or API record.

Read first (not repeated): D-FIELDS-B (`2026-11-09-math-ml-data-pools.md`) and D-CONFIRM (`2026-11-09-data-confirmation.md`).

## Step 1 (under 2 minutes)

Open `https://zenodo.org/records/10439422` (KuaiRand, 322M logged recommendations, includes randomly exposed items). It is the biggest new set that clears every rule.

## Rules (same as D-CONFIRM)

- **PASS** = owner's page states 10M+ real data points, licence text allows training (unclear = no), fetchable at $0 in a free Kaggle notebook.
- NC and SA licences pass for Nick's personal, non-redistributed use; I flag them.
- Counts are as stated on the page. "Derived" is my arithmetic and is never counted.
- Third-party mirrors do not count as the owner's licence, except where noted.
- Link to brain task: **direct** (sports play or timing), **indirect** (generic next-event, timing, what-to-show-next or public attention), **weak** (method transfer only). **No set below is direct.** The direct sports sets still fail on licence (D-CONFIRM).

## Headline

- **6 new PASS** (plus 1 flagged-conditional) from catalogs the first pass skipped: OTTO, KuaiRand, KASANDR, Criteo Attribution, Criteo Uplift, Wikipedia hourly page views 2024, and Open Bandit via a CC BY copy.
- They cover subcategories that had zero passes: **recommender and click logs with real timestamps, causal / unbiased feedback, logged bandit data, delayed-outcome timing.**
- **Still nothing** that passes for: learning to rank at 10M+, offline RL on real decisions beyond Open Bandit, point-process benchmarks at 10M+, and open time-series pretraining corpora (GiftEvalPretrain, LOTSA, Time-300B, Monash stay blocked by "research only" wording).
- For time series, use the Apache-2.0 pretrained forecasters zero-shot (models table). Best open 1-2B base model: **Qwen3-1.7B-Base** (Apache-2.0).

## Table 1: data

| # | Dataset (owner page) | True count (stated) | Licence (owner text) | Training OK | Free Kaggle fetch | Size | Subcategory | Link | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **KuaiRand** (`kuairand.com`, Zenodo 10439422, USTC) | 322,278,385 interactions (27K version, 15 policies), 32.0M items, 27,285 users; plus 1,186,059 randomly exposed interactions | Zenodo record: CC BY 4.0. kuairand.com badge: CC BY-SA 4.0. **Pages disagree**; both allow training, SA only bites if shared | yes | yes: HTTPS from Zenodo, no account | 27K archive 9.89 GB; 1K 1.14 GB; Pure 47 MB | recommender logs, causal (random exposure = unbiased feedback), sequence | indirect (what to show next, with randomised exposure to debias) | **PASS** (flag: SA vs BY) |
| 2 | **OTTO recsys** (GitHub `otto-de/recsys-dataset`; Kaggle `otto/recsys-dataset`, owner Otto) | 216,716,096 train events + 13,851,293 test events (page README says about 220M); 12.9M train sessions | CC BY 4.0 (GitHub and Kaggle API agree); code MIT | yes | yes: Kaggle input | Kaggle API: 12.06 GB (list view said 2.13 GB; pages disagree) | event sequences, recommender logs (click, cart, order with ms timestamps) | indirect (next-event and session sequences) | **PASS** |
| 3 | **Open Bandit** copy (HF `zozonext/open-bandit`, org ZOZO NEXT) | "~22.9M rows" (default subset); research.zozo.com says about 26M | HF tag CC BY 4.0. The original research.zozo.com page and GitHub still state **no licence**. Org page does not mention ZOZO Research | yes if the HF tag is accepted | yes: HF CSV or Parquet | 11.99 GB stored | causal, bandit logs, offline RL (propensity logged) | indirect (counterfactual policy evaluation for "which game next") | **PASS, conditional**: licence is on a company page, link to the original authors unverified; treat as yes only if Nick accepts that |
| 4 | **KASANDR** (UCI 385, Kelkoo logs) | 17,764,280 instances (15,844,718 train + 1,919,562 test), 2016-06-01 to 2016-07-01 | CC BY 4.0 | yes | yes: `de.tar.bz2` over HTTPS | 900.5 MB | recommender click logs, sequences | weak (offer clicks, no sports) | **PASS** |
| 5 | **Criteo Attribution** (HF `criteo/criteo-attribution-dataset`, Criteo's own org) | 16.5M rows (impressions with timestamp and conversion timestamp; conversion totals not stated) | CC BY-NC-SA 4.0 | yes (NC, personal) | yes: HF CSV or Parquet | not stated (about 10M-100M tag) | delayed-outcome timing, point-process-style | weak to indirect (time from event to outcome) | **PASS** |
| 6 | **Criteo Uplift** (HF `criteo/criteo-uplift`) | 13,979,592 rows ("Key figures"; the text says 25M, a conflict, I use the lower stated figure) | CC BY-NC-SA 4.0 | yes (NC, personal) | yes | 311 MB | causal (randomised treatment, uplift) | weak | **PASS** |
| 7 | **Wikipedia Hourly Page Views 2024** (Zenodo 19562496, MIT author) | "over 2 billion discrete data points per month" (about 3M pages per month); row count not stated | CC BY 4.0 (upstream Wikimedia CC0 per D-CONFIRM) | yes | yes: Zenodo HTTPS, monthly Parquet | 15.9 GB for 12 months (about 1.3 GB per month) | time series, attention | indirect (public attention spikes). **Overlaps** the first pass's HF Wikimedia set (same upstream); do not double count | **PASS** |
| 8 | CESNET-TimeSeries24 (Zenodo 13382427, CESNET) | 66 billion IP flows aggregated into series; 275,000+ IPs, 283 institutions; **series points not stated** | CC BY 4.0 | yes | yes (full 40 GB archive too big; use 0.77 GB institution subnets or 0.17 GB sample) | 41.5 GB | time series, anomaly | weak (network traffic) | licence passes; **count not stated**, so not counted |
| 9 | LargeST traffic (GitHub `liuxu77/largest`, Kaggle) | 8,600 sensors, 2017-2021 at 5-minute (my arithmetic, not stated); **observations not stated** | dataset CC BY-NC 4.0, code MIT | yes (NC) | yes: Kaggle | not stated | time series | weak | licence passes; **count not stated** |
| 10 | Wikimedia `mediawiki_history` dumps | row counts not stated; full edit history with timestamps | CC0 ("All Analytics datasets ... CC0") | yes | yes: HTTPS bz2, files up to about 2 GB | not stated | event sequences | weak | licence passes; **count not stated** |
| 11 | Amazon ESCI shopping queries (GitHub `amazon-science/esci-data`) | 2,621,738 judgments (130,652 queries); page also shows 2,621,288, small conflict | repo Apache-2.0; no separate dataset line | likely yes | yes | not stated | **learning to rank** | indirect (ranking labels) | below 10M, licence line covers the repo; **not a pass** but the only open LTR set that clears the licence test |

### Checked and still failing (new this pass)

| Dataset | Why it fails |
|---|---|
| Criteo counterfactual banner set (`ailab.criteo.com`) | about 100M examples, 250 GB, **no licence stated** |
| Criteo PrivateAd (HF) | CC BY-SA 4.0 tag, but only a "10M-100M" size tag, no exact count, no description |
| Amazon Reviews 2023 (HF `McAuley-Lab`) | 571.54M reviews stated, but **no licence tag or terms** on the page |
| Time Series Pile (HF `AutonLab/Timeseries-PILE`) | MIT tag and "1.23 billion timestamps" stated, but the card says upstream licences "must be checked individually" (Monash is research-only; others unnamed). Size also conflicts (8.71 vs 20.085 GB) |
| Time-HD (HF `Time-HD-Anonymous`) | Apache-2.0 tag, card is an empty template, anonymous owner, 703,792 rows |
| Monash forecasting archive (`forecastingdata.org`) | "intended to use only for research purpose"; Kaggle copy tagged CC BY is third-party |
| London Smart Meters (Zenodo 4656091) | Zenodo says CC BY 4.0, but it is the Monash redistribution and the archive's own page says research only: unclear |
| Danish heat meters (Zenodo 6563114) | CC BY 4.0 but **count not stated** (about 79.4M is my arithmetic) and the topic is energy only |
| Buildings-900K (NREL BuildingsBench) | no dataset licence on the page (BSD-3 covers code) |
| Baidu-ULTR mirrors | CC BY-NC 4.0 mirrors are third-party; official copy not downloadable (D-CONFIRM) |
| Yahoo LTR (HF) | licence "other" |
| D4RL / Minari (HF `farama-minari`) | no licence tag; simulated, not real decisions |
| DanmakuTPP (HF, Bilibili live comments) | no licence, gated, count not stated, 530 MB. Live-stream arrival events would be a good fit; ask the authors |
| NBA quarter events (HF `DescribeEvents`, MIT) | direct for sports, but 291,091 events (8.6 MB) and upstream NBA terms unaddressed |
| Bluesky post dumps (HF) | personal user content, mixed or missing licences; skipped on privacy |

### Catalogs searched

| Catalog | What I got | Use |
|---|---|---|
| Hugging Face (licence filters: cc-by-4.0, apache-2.0, mit; search for ranking, bandit, uplift, clickstream, session events, point process) | Found Open Bandit CC BY copy, Criteo sets, Time Series Pile. Search is name-only; several queries (offline RL, sequential recommendation) returned empty | best for names |
| Zenodo (API, licence field per record) | Found KuaiRand, CESNET, Wikipedia hourly 2024, Danish meters. Licence is a real field there, which makes it the cleanest source for this test | best for licences |
| Kaggle API (`license=cc`, `minSize`) | 10 or fewer hits per query, all **third-party uploads** except OTTO; owner-vs-uploader check is manual | noisy |
| UCI | KASANDR passes; page states licence, count and date range | good |
| Google Dataset Search | opened: 38 results for "temporal point process"; mostly synthetic or sota2.com test entries; **no licence shown on any hit** (page says "License from data provider") | names only |
| re3data | API opened: 55 repositories for "time series"; lists repositories, not datasets or counts | breadth |
| data.europa.eu | "time series": 144,099 results; licence facet not returned | breadth |
| DataCite | API opened: 0 hits for "point process" dataset with CC0 filter; no facets | breadth |
| data.gov (CKAN) | both URL forms returned 404; **not covered** | none |
| OpenML | API returned 412 twice; **not covered** | none |
| Azure Open Datasets | catalog page opened (about 25 entries: NYC TLC, 311 and 911 calls, MIND, labor stats); NYC TLC has no licence (D-CONFIRM), MIND is research licence; nothing new passes | none new |

## Table 2: models

All licences read from the owner's HF card or API record. Parameter counts as stated on the page.

### 2a. Time-series and event-sequence models

| Model | Params | Licence (owner page) | Training data stated | Notes | Verdict |
|---|---|---|---|---|---|
| `amazon/chronos-bolt-base` | 205M (card table) | Apache-2.0 | "nearly 100 billion time series observations"; datasets not listed | zero-shot; the inference library is "intended for research purposes" but the model has no such limit | usable, best-documented licence |
| `amazon/chronos-2` | 120M | Apache-2.0 | subset of Chronos Datasets, subset of GiftEval Pretrain, synthetic | **new this pass**; handles past and known-future covariates (score, clock, leverage); context 8,192, horizon 1,024; 30 adapters and 18 finetunes in its tree | usable; best fit for covariates |
| `google/timesfm-2.5-200m-pytorch` | 0.2B | Apache-2.0 | GiftEvalPretrain, Wikimedia Pageviews (to Nov 2023), Google Trends top queries, synthetic | "not an officially supported Google product"; no fine-tuning guidance on card | usable |
| `Datadog/Toto-Open-Base-1.0` | 151M | Apache-2.0 | over 2 trillion points; about 1T Datadog internal (no customer data), GiftEval Pretrain, Chronos data; about one third synthetic | multivariate, zero-shot focus; no fine-tuning guidance | usable |
| `thuml/sundial-base-128m` | 128,329,680 | Apache-2.0 | not read | generative forecaster, Tsinghua | licence yes, data not checked |
| `AutonLab/MOMENT-1-large` | 346,368,520 | MIT | not read; trained on the Time Series Pile (see Table 1 fail row) | forecasting, classification, anomaly, imputation | licence tag MIT, but upstream data mix is unclear |
| `ibm-granite/granite-timeseries-ttm-r3` | 1,414,514 | Apache-2.0 | not read | tiny; 1.7M downloads; can fine-tune on a Mac | usable for small local fine-tunes |
| `Maple728/TimeMoE-200M` | 453,196,800 (tag) | Apache-2.0 | trained on Time-300B (whose own terms are unclear, D-CONFIRM) | | weights Apache, data chain unclear |
| `time-series-foundation-models/Lag-Llama` | not read | Apache-2.0 tag | not read | probabilistic, decoder-only | licence tag only |
| `NX-AI/TiRex` | not read | **NX-AI community licence** (`license:other`) | Chronos datasets, GiftEvalPretrain | text not read | **no** (unclear) |
| `Salesforce/moirai-2.0-R-small` (first pass) | 11.4M | CC-BY-NC-4.0 | | | personal use only |

**Caution on the Apache rows:** Chronos-2, TimesFM 2.5, Toto and TiRex list GiftEvalPretrain as training data, and D-CONFIRM found that set carries "research purposes only" wording. The model cards themselves state Apache-2.0 with no such limit, so I count the weights as clear; the data chain is a residual risk Nick can accept or not.

### 2b. Open 1-2B base models for the live brain (8 GB M2 Mac)

| Model | Params | Licence | Context | Training size | Published memory | Fine-tuning allowed | Verdict |
|---|---|---|---|---|---|---|---|
| **`Qwen/Qwen3-1.7B-Base`** | 1.72B total, 1.4B non-embedding | Apache-2.0 | 32,768 | family trained on 36T tokens (not model-specific) | Qwen docs (GPU, Transformers): **3,412 MB BF16** at 1 input token, 4,213 MB at 6,144, 6,902 MB at 30,720; Int8 2,229 MB at 1 token. HF `mlx-community/Qwen3-1.7B-4bit` stores **979,502,864 bytes** (about 0.98 GB) | yes (Apache) | **best pick** |
| `Qwen/Qwen2.5-1.5B` | 1.54B, 1.31B non-embedding | Apache-2.0 | 32,768 | not stated | none on card | yes | strong fallback |
| `HuggingFaceTB/SmolLM2-1.7B` | 1.7B | Apache-2.0 | not stated | 11T tokens | card prints **3,422.76 MB** in bf16 | yes (card explains SFT and DPO recipe) | strong fallback, most transparent |
| `allenai/OLMo-2-0425-1B` | 1B | Apache-2.0 | 4,096 | about 4T + 50B mid-training; data and code open | none; weights alone about 4 GB in F32 (my estimate) | yes | cleanest data provenance, short context |
| `ibm-granite/granite-4.0-1b-base` | 1.6B | Apache-2.0 | 128K | about 15T tokens | none | yes | fine, newest; less community tooling (unverified) |
| `meta-llama/Llama-3.2-1B` | 1.23B | Llama 3.2 Community Licence: 700M-MAU clause, acceptable-use policy, "Built with Llama", derived model name must start with "Llama" | 128K | not read | none | yes, with those terms; **gated** (login) | passes for personal use; naming and gating are friction |
| `google/gemma-3-1b-pt` | 1.0B | Gemma terms plus prohibited-use policy; **gated** | 32K | not read | none | terms not read in full | unclear until terms read, counts as no |
| `LiquidAI/LFM2-1.2B` | 1.17B | LFM Open License v1.0 (text not read); post-trained, not base | 32,768 | not read | none | not read | **no** (unclear, not a base model) |

**Memory facts for the 8 GB Mac.**
- Published figures are all inference. **No page gives Mac fine-tuning memory.** The mlx-lm LoRA page names levers (QLoRA, batch size `--batch-size` default 4, `--num-layers` default 16, `--grad-checkpoint`) but only gives a figure for a 32 GB machine.
- Closest published fine-tune number: Unsloth lists a 3B model at **3.5 GB (QLoRA 4-bit)** and **8 GB (LoRA 16-bit)** as minimums; it has no 1-2B row.
- Practical reading, not a published fact: run Qwen3-1.7B in 4-bit (about 1 GB weights) on the Mac for inference; fine-tune on Kaggle (project rule: no training on the Mac).
- Qwen3-1.7B-Base is used by 610,155 monthly downloads and not gated, which makes it easy to pull in a Kaggle notebook.

## What I could not confirm

- Whether ZOZO Research endorses the `zozonext` CC BY tag for Open Bandit.
- Which KuaiRand licence applies (Zenodo says CC BY 4.0, repo says CC BY-SA 4.0).
- Kaggle notebook disk and internet rules (official page not opened; D-CONFIRM unverified too).
- OTTO size on Kaggle (12.06 GB by one view, 2.13 GB by another).
- Full terms for Gemma, LFM2 and NX-AI community licences; training data and sizes for Sundial, MOMENT, TTM, Lag-Llama.
- data.gov, OpenML (API errors), Istella22 (no licence found), Yahoo/MSLR terms.

## Open questions for Nick

1. Accept the `zozonext` CC BY tag as the licence for Open Bandit? (default: yes for personal use, flagged)
2. Accept Apache-2.0 forecaster weights even though they were trained partly on research-only corpora? (default: yes)
