---
id: D-CONFIRM
title: Data confirmation for every 10M+ dataset in the math/ML and sports pools (true size, licence, training, $0 Kaggle fetch, brain-task link)
date: 2026-11-09
stream: data
author: data confirmation agent (Claude Sonnet 5.5)
status: draft
supersedes: []
sources: 85
verified: 73
affects: []
gate: []
---

# D-CONFIRM: 10M+ datasets, confirmed from the owner's page

Nothing downloaded, nothing signed up, no secrets read. Source and verified counts are a hand tally (about plus or minus 3). Web text was treated as data.

## Rules used

- **Pass** = all four: 10M+ real data points stated on the owner's page; licence text shown and it allows use for a private, commercial-free project; training not barred; fetchable at $0 in a free Kaggle notebook with no paid account.
- **Unclear counts as no.** A card tag that says Apache-2.0 beside "research purposes only" is unclear. A Kaggle copy uploaded by a third party does not count as the owner's licence.
- Non-commercial (NC) licences pass for Nick's personal use. They fail only if the page also bars training or research-unrelated use.
- "Stated" counts come from the page. Derived arithmetic is never counted.
- Kaggle limits: the official Kaggle docs page did not render. Secondary sources say about **20 GB** for `/kaggle/working` (plus a scratch disk that is wiped at session end). Datasets attached as notebook input and files streamed from Hugging Face or HTTPS do not count toward the 20 GB per those sources (unverified). Internet in a notebook is free but needs a phone-verified Kaggle account (not confirmed by the pages I could open).

## Table

| Dataset | True data points (stated, unit) | Licence (owner's text) | Training allowed | Fetch at $0 | GB | Brain task link |
|---|---|---|---|---|---|---|
| **PASS** Zvuk (Kaggle `alexxl/zvuk-dataset`) | 244,673,551 listen interactions | CC BY 4.0, no restrictions listed; uploader looks like the KDD'24 paper author, mirror status not stated | yes | yes: attach as Kaggle input | 2.66 | weak (music arrival times; no sports) |
| **PASS** GDELT 1.0/2.0 events | "over a quarter-billion event records" (250M floor) | "unlimited and unrestricted use for any academic, commercial, or governmental use"; cite GDELT | yes | yes: HTTP daily zips (`data.gdeltproject.org/events/`), no account | not stated per file; reduced file 1.1 | weak (news events, not play-level) |
| **PASS** Wikimedia pageview time series (HF `jeremycochoy/wikimedia-pageview-timeseries-raw`) | 238,040,330 rows (series windows); points per row derived, not counted | CC0-1.0 (follows upstream Wikimedia CC0) | yes | yes: `snapshot_download` with `allow_patterns` | 686 listed (640 per file table); one hourly file about 2 | indirect (public attention spikes) |
| **PASS** Backblaze Drive Stats | 558,148,607 drive-day rows; only **21,609 failures** | no named licence; terms: cite Backblaze, may sell derivative works, may not sell raw data | yes (derivatives allowed) | yes: quarterly ZIP over HTTPS | 1.2 to 2.1 zip; about 12 unzipped per quarter | weak (time to failure; 21.6k events is the real power) |
| **PASS** Lichess standard games (HF) | 7.14B games | CC0-1.0 | yes | yes: HF `streaming=True` or one month parquet | total not stated; stream a month | weak (decisions under a clock; no sports) |
| **PASS** Criteo 1TB click logs (HF `criteo/CriteoClickLogs`, Criteo's own org) | 4.37B rows (Criteo page: "4B event lines") | CC BY-NC-SA 4.0: share and adapt for NonCommercial purposes only; ShareAlike | yes (NC; no AI clause) | yes: ungated HF parquet, daily partitions (Criteo's own page asks for a click-accept) | about 1,000 raw per the page title; per-day not stated, stream a day | weak (ad clicks) |
| **PASS** Azure Public Dataset VM trace V2 | 1.9B utilisation readings (V1 1.2B) | page lists CC-BY-4.0 and MIT without saying which covers data; both allow use | yes | yes: GitHub release assets, no account shown | not stated | weak (queue load; not sports) |
| **PASS** SoccerMon (Zenodo 10033832) | "over six billion" GPS positions (33,849 subjective + 10,075 objective reports) | CC BY 4.0; page does not say athletes are anonymised | yes (privacy check first) | yes: Zenodo HTTPS | 99.1 total; one team-season zip 16.4 | weak (athlete load, not broadcast; heavily autocorrelated) |
| **PASS** Aalto 136M keystrokes | 136M keystrokes, 168,000 volunteers | "free to use for research and non-commercial use with attribution" | yes (NC) | yes: one zip over HTTPS | 1.4 zip, 16 unzipped | weak (typing, not viewing) |
| FAIL Yambda (HF `yandex/yambda`, Kaggle mirror) | flat-5b 4.79B rows; all subsets 5.32B; 50m subset 47.8M | Apache-2.0 tag, but card says "published exclusively for scientific and research purposes" | unclear = no | yes (HF `data_dir="flat/50m"`; Kaggle mirror) | 195 total; Kaggle mirror 15.5 | weak (music) |
| FAIL Salesforce GiftEvalPretrain (HF) | 230B points, 4.5M series | Apache-2.0 tag plus "research purposes only in support of an academic paper" | unclear = no | HF files; viewer broken | 975 | indirect (pretrains forecasting; non-sports) |
| FAIL LOTSA (HF `Salesforce/lotsa_data`) | 1,346,185 series (below 10M); points not stated | Apache-2.0 tag plus "research purposes only" | unclear = no | HF | 925 | indirect |
| FAIL Time-300B (HF `Maple728/Time-300B`) | not stated (size tag n>1T only) | Apache-2.0 tag; source datasets not named, so upstream terms unknown | unclear = no | HF; viewer broken | 1,240 | indirect |
| FAIL chronos_datasets (HF) | 10M-100M rows (tag only); no total | "other"; no per-subset licence on page | no | HF | not stated | indirect |
| FAIL Open Bandit Dataset (ZOZO) | about 26M rows (page) | **none stated** on research.zozo.com or the repo (package is Apache-2.0, data not covered); asks to cite | unclear = no | yes: direct zip from research.zozo.com, no account | not stated | weak (logged bandit feedback) |
| FAIL MIND (Microsoft) | more than 15M impression logs | Microsoft Research License Terms, accept-to-download; text not shown, page says "for research" | unclear = no | accept button, then download; Azure copy | not stated | weak |
| FAIL Baidu-ULTR | not stated on any page | official GitHub: no licence, "currently inaccessible for download due to legal restrictions with Baidu", access by emailing the author; HF mirrors (`philipphager`) are CC BY-NC 4.0, third-party | no | official: no; mirror: yes | mirrors 41 to 188 (pages disagree) | weak |
| FAIL YouTube-8M | about 8M videos; features for 1.9B frames (2016 blog) | no licence on any page I could open (research.google.com redirects away) | unclear = no | official host not confirmed; Kaggle copies are third-party (Unknown or CC BY-SA) | under 1,500 | indirect (generic video features) |
| FAIL NYC TLC trip records | about 1.5B rows to 2018 (Microsoft page via the earlier report); TLC page gives no counts | none stated; "no representations as to accuracy" | unclear = no | yes: parquet over HTTPS | not stated | weak |
| FAIL NYC EMS dispatch | about 30.4M (inferred from column stats; no row count shown) | none stated | unclear = no | yes | not stated | weak |
| FAIL OpenFEMA NFIP policies | 74.7M (earlier report; FEMA page 404 for me) | "solely for statistical research or as a reporting record"; no licence named | no | yes (API) | not stated | none |
| FAIL Polymarket trades (HF `SII-WANGZJ/Polymarket_data`) | 1,266,885,846 trades; 2,026,704,633 raw fills; about 6.6B records in all files | README: MIT, "free for commercial and research use"; HF tag shows no licence. Polymarket's terms bar scraping and data mining tools, give users a "personal, limited" licence over Data "in raw, derived, aggregated or anonymized form", and are silent on AI | unclear = no | yes: HF per-file download or streaming | 279 total; `trades.parquet` 45.8 | indirect (market win-probability paths; sports share unknown; wallet fields must be dropped) |
| FAIL NHL_DB (HF `RentoSaijo/NHL_DB`) | 53.6M rows | **none anywhere**: no card, BOOTSTRAP.md has no terms; data scraped by an R scraper (source not named) | no | yes: HF parquet, ungated | 20.99 whole repo | **direct** (puck and player coordinates per event, replays index) |
| FAIL NBA archive (HF `cdechoch/nba-data-archive`) | rows not stated (tag 10M-100M) | Apache-2.0 card; upstream NBA terms allow stats only "for legitimate news reporting or private, non-commercial purposes", bar use "in connection with any website, product, or service that features a database", say nothing on AI | unclear = no | yes: HF parquet; viewer broken | 4.03 | **direct** (play-by-play, shot detail) |
| FAIL NBA play-by-play (Kaggle `szymonjwiak`) | 18,255,730 events, 37,928 games | CC BY-NC-SA 4.0 set by the uploader, scraped from NBA.com; owner terms as above | unclear = no | yes: Kaggle input | 2.15 | **direct** |
| FAIL NBA Database (Kaggle `wyattowalsh/basketball`) | 13M+ play-by-play rows (page) | CC BY-SA 4.0 by the uploader; upstream source not named | no | yes | 4.66 | **direct** |
| FAIL OpenDota dump | 1,191,768,403 matches (match level) | owner blog: no licence, cite them; Academic Torrents listing: CC BY-SA 4.0; Valve upstream unchecked | unclear = no | torrent for the full set; only three 4 GB sample CSVs on Google Cloud Storage | samples 4 each; `match_skill.gz` 1.72 | weak |
| FAIL AddBiomechanics core | over 24M frames (71.47 h, 273 people) | CC BY 4.0 | yes | one zip over plain HTTP; size not stated, about 90 GB derived from the page's "4 hours at 50 Mbps" | about 90 (derived) | weak |
| FAIL Atari-HEAD | 328M gaze samples (arXiv abstract) | no dataset licence stated; hosting not stated | unclear = no | not shown | not stated | weak |
| FAIL MovieLens 32M | 32M ratings | terms in the README, not on the page; GroupLens "typically do not permit public redistribution"; request form | unclear = no | no (use needs agreeing to terms and a request form) | 0.239 zip | weak |
| FAIL GDELT TV news ngrams | about 16.9B n-gram records | GDELT's unrestricted terms | yes | not shown: pages say only "TV NGram" download blog; BigQuery access not confirmed free | not stated | weak (broadcast word counts) |
| FAIL Twitch interaction dynamics (Kaggle `pypiahmad`) | 124M interactions, 15.5M users | Kaggle copy: Apache 2.0 by a third party; the owner's GitHub (JRappaz/liverec) states **no licence** | unclear = no | yes: Kaggle input | 19.0 | indirect (live audience over time) |
| FAIL Goodreads reviews (Kaggle `pypiahmad`) | 225,394,930 interactions | Kaggle copy: Apache 2.0, third party; UCSD owner terms not opened; user-level data | unclear = no | yes | 31.4 | none |
| FAIL Nova 50M transactions (Kaggle) | 50M interactions, **synthetic** (page says "synthetically generated") | Apache 2.0 | n/a (synthetic is never counted) | yes | 8.35 | none |

## Notes that change a decision

1. **Nine sets pass, none is a direct brain link.** The direct sets (NHL tracking, NBA play-by-play) all fail on licence. Pass count by link: indirect 1 (Wikimedia), weak 8.
2. **Backblaze and SoccerMon inflate the total.** Backblaze has 21,609 events in 558M rows. SoccerMon GPS rows are autocorrelated and carry athlete health data (anonymisation not stated). Without Backblaze the pass total is about 20.28B.
3. **Open items from the reports, now answered:**
   - Criteo: CC BY-NC-SA 4.0 (Criteo's terms page and its HF repo agree). Fine for Nick's personal use, weak link.
   - Open Bandit: still **no licence** on the owner's page. Stays blocked. Email address on the page: the paper authors (Saito) and ZOZO Research.
   - Baidu-ULTR: official copy says legally inaccessible; only third-party HF mirrors (CC BY-NC 4.0). Size and session counts are not stated on any page.
   - NHL_DB: no licence, no card, data scraped; stays blocked until the owner (HF user RentoSaijo) states terms.
   - Snippet-only Kaggle sizes now from Kaggle's API: Zvuk 2.66 GB, Twitch 19.0 GB, NBA 2.15 GB, Nova 8.35 GB (synthetic), Goodreads 31.4 GB (the earlier 8.7 GB was a different upload), QVHighlights 145 GB with an empty description and no file list.
   - Yambda: the "research only" sentence is the blocker. A personal sports model is not clearly "scientific research", so it stays no until the owner says otherwise.
4. **Time-series pretraining sets (GiftEvalPretrain, LOTSA, Time-300B, chronos) all fail.** Use the Apache-2.0 pretrained forecasters (chronos-bolt, TimesFM 2.5, Toto, per the math report section C) zero-shot instead.
5. **Wikimedia HF series is the only indirect pass.** It is third-party processed from Wikimedia CC0 dumps; the official Wikimedia analytics datasets are CC0 per the dumps legal page, but the monthly files state no row counts, so they are not counted here.

## Below 10M or count not stated (not in the table)

- AudioSet 2,085,544 segments (CC BY 4.0, labels only; 20.9M embeddings is derived, not counted).
- Clubhouse 9.7M, MSLR-WEB30K (30,000 queries; pair count not stated), SF fire/EMS 7.49M, MoneyPuck 2.08M shots, cricket mirror 5.11M, MLB Statcast 1.44M, EsportsBench 2.78M, SoccerTrack v2 21,428 events.
- Count not stated, so unconfirmed: OpenF1 (CC BY-NC-SA 4.0, personal learning use stated, no row count), BTS on-time (no count or terms), trentmkelly Polymarket snapshots (CC BY 4.0, 15 GB, no rows, wallet fields), Wikimedia official pageview files, GHCN-Daily ("nearly 2 billion data values" on NOAA's page, but NOAA's page states no licence and the AWS registry page says CC0, so it is borderline: licence conflict across two NOAA pages, left out), ZTF DR24 (release notes PDF unreadable), Movebank (account needed), NFL Big Data Bowl (no count; Kaggle competition rules need Nick).

## Not verified

Kaggle's official disk and internet rules (docs page empty); Zvuk uploader's authorship; Azure's per-file sizes; Criteo per-day size; Lichess monthly file sizes; Yambda per-subset GB; Polymarket upstream AI wording (silent); NBA terms beyond what the page shows.

## Next step (under 2 minutes)

Decide whether Yambda's "scientific and research purposes" wording is acceptable for a personal sports model. If yes, add the Yambda 50M subset (47.8M rows) to the pass list.
