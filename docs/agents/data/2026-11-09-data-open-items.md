---
id: D-OPEN
title: "Open data items settled: cricket provenance, StatsBomb American football, CBB row count, five catalog retries, and the two zeros"
date: 2026-11-09
stream: data
author: data open-items agent (Claude Sonnet 5.5), data job 15
status: draft
supersedes: []
sources: 34
verified: 26
affects: []
gate: []
---

# D-OPEN: what the open items came to

Inputs: D-LEDGER and D-CATALOG (read in full). Nothing downloaded as data, nothing signed up, no repo secrets read. Web text treated as data. "Verified" = a page or API response was fetched and read. Counts below are the owner's own or measured from file metadata; token figures use the ledger's 6 tokens per play event (judgment, unchanged).

What I did fetch, for the record: small repo metadata (StatsBomb `games.json` 80 KB and `LICENSE.pdf`, the AWS registry's 1,497 YAML descriptors about 2.5 MB, OpenML's dataset list) and the last ~100 KB footer of each hoopR/cfbfastR parquet file by HTTP range request. A footer holds the exact row count; no row data was read. I installed `pyarrow` in a scratchpad venv for that.

## Step 1 (under 2 minutes)

Nick: decide whether `statsbomb/amf-open-data` (Tom Brady's 124 games only) stays out. Recommended answer: **out**. Its agreement bars redistribution and commercial use, grants no training right, and it adds almost no NFL coverage.

## Bottom line

1. **Direct tokens (T1 + T2): 0.567 B to about 0.595 B (+28.6 M).** Cricket upgrade passes (+34.6 M). CBB is measured and lands slightly below the central guess (-6.0 M).
2. **CBB uncertainty is gone: 32,332,257 rows, so about 194 M tokens**, not 160-300 M. The ledger's 200 M central estimate was within 3%.
3. **Neither zero moved.** No public per-minute US audience record tied to game state. No public US sports broadcast cut or replay labels. One new proxy exists for ad breaks (US TV news only, section 5).
4. **Five catalogs retried.** AWS is fully walked and has nothing. OpenML, Figshare: nothing. data.gov and BigQuery cannot be enumerated without a login or a working API, so they stay "not conclusive".

## 1. Settled by checking

### 1a. `Sarthak213/doosra-cricket`: Cricsheet-derived? **Yes, pass**

| Check | Result |
|---|---|
| Owner's README (raw file, opened) | States the data "is from Cricsheet" and credits it. Licence ODC-By 1.0 for the data and the derived tables. The card summary I read earlier never named a source; the README does |
| Counts (datasets-server) | `deliveries` 11,615,100 rows; `matches` 22,983; `missing_matches` 2,930 ("Matches Cricsheet lists as missing or withheld"); `people` 18,554 |
| Cricsheet's own count (cricsheet.org/downloads) | 23,241 matches (men 18,527, women 4,714); 386 withheld; ODC-By per D-CATALOG |
| Record format | `matches` rows carry Cricsheet's JSON fields (`data_version` 1.2.0, `revision`, `balls_per_over`, `eliminator`, `method`, `player_of_match`). First row `match_id` 1000851 is the ESPNcricinfo id of Australia v South Africa, Perth, 3 Nov 2016 (search result), matching the row's date, teams and result |
| Mirror date | Built 2026-09-28, matches to 2026-09-17, so it can trail Cricsheet's current list |

Not closed: Cricsheet's site has no per-match page I could open (`/match/ID/` and `/matches/ID.json` returned 404), so I could not compare ids one by one. The owner states the source and licence in the README, which is the standard the rules ask for. Residual risk: low.

**Effect:** cricket goes from 5,852,078 to 11,615,100 balls (replaces, not adds). Tokens 35.1 M to 69.7 M, **+34.6 M**. HF `lohith-ashwa/cricket_analytics_dataset` is the same data; still counted once.

### 1b. StatsBomb / Hudl American football open data: **124 games, Brady only; counts as no**

| Item | Fact (source) |
|---|---|
| Content | Named #TB12DB: every game involves NE or TB (124 games, seasons 2016-2022: 18, 16, 16, 17, 20, 19, 18). Not league-wide. `games.json` (opened) |
| Files | 124 high-frequency tracking `json.gz` files (1.26 GB, GitHub tree sizes); 39 low-frequency tracking files (830 MB); events CSV for 6 seasons (about 305 MB); plays CSV for 6 seasons (about 32 MB) |
| Counts | **Not stated anywhere**: no play, event or frame totals, no tracking rate on the repo page. Not turned into rows from bytes |
| Licence | Repo has no SPDX licence (GitHub API returned none). `LICENSE.pdf` opened: "StatsBomb Public Data User Agreement". The user may not "distribute, reproduce, sell or in any way provide the data to any external or third party" or "commercially exploit the data or any analysis derived" from it; must credit StatsBomb with its logo when publishing analysis; "asks" users to register name and email; may be withdrawn at any time. Framed as a "research tool". No statement that model training is allowed |
| Verdict | **Unclear = no.** A model trained on it would be an "analysis derived from" the data, and "research" is the only stated purpose. Even if Nick ruled it in, the volume is small (124 games, about 16,000 plays by my arithmetic at ~130 plays a game) and the games are one quarterback's |

D-CATALOG's "real lead for NFL tracking" is downgraded: it is 1 quarterback's games, not NFL tracking.

### 1c. CBB row count: **32,332,257 rows**

Method: exact `num_rows` from each parquet footer in `sportsdataverse/hoopR-mbb-data`, `mbb/pbp/parquet/` (men's only, ESPN play rows, 62-64 columns). Total bytes 855,681,814 (matches the 855.7 MB listing).

| File group | Rows |
|---|---|
| 2003 | 5,182 |
| 2006-2010 | 3,983,267 |
| 2011-2015 | 6,915,181 |
| 2016-2020 | 8,984,091 |
| 2021-2025 | 9,349,286 |
| 2026 (2025-26 season) | 2,915,731 |
| 2027 (2026-27, in progress) | 219,519 |
| **All files** | **32,332,257** |

Tokens at 6 per row: **194.0 M** (2006-2026 only: 32,107,556 rows, 192.6 M). Ledger central 200 M, so **-6.0 M**. The 160-300 M range is closed to a point. Caveats: ESPN rows include non-play rows (timeouts, end of period), same convention as the NBA figure; 6 tokens per row is still the measured NBA vocab. The BigQuery NCAA table (>4 M plays) is a subset of the same games and is not added.

Cross-checks done in the same pass (not applied to the total):

| Feed | Measured | Ledger | Note |
|---|---|---|---|
| hoopR NBA, `nba/pbp/parquet` | 14,275,802 rows (2002 on) | 18.6 M (`deocheng`) | Different feed; ledger counted the larger one. Unchanged |
| cfbfastR-data CFB, `pbp/parquet` | 2,456,357 rows, 2002-2021 only (14.7 M tokens) | 20 M (estimate) | That folder stops at 2021. 2022-2025 not located. **CFB stays an unconfirmed estimate**; the measured floor is 14.7 M |

## 2. New direct totals

| Line | Before | After | Change |
|---|---|---|---|
| Cricket tokens | 35.1 M | 69.7 M | +34.6 M |
| CBB tokens (T2) | 200 M | 194.0 M | -6.0 M |
| T1 direct subtotal | 155.5 M | 190.1 M | +34.6 M |
| T2 direct subtotal | 411 M | 405 M | -6 M |
| **Direct, T1 + T2** | **566.5 M (0.567 B)** | **595.1 M (0.595 B)** | **+28.6 M** |
| Six-league direct | 519 M | 513 M | -6 M (cricket is not six-league) |

Resize effect (ledger rule, tokens / 20): justified size from direct data goes from 28.3 M to about 29.8 M parameters. The ledger's conclusion (right size about 25-30 M; 565 M from scratch does not hold) is unchanged. Plausible range now about 0.58-0.61 B, since CBB no longer carries the largest uncertainty; CFB and NHL are still estimates.

## 3. Catalog retries

| Catalog | What worked | Result |
|---|---|---|
| **AWS Open Data** | The registry's GitHub repo (`awslabs/open-data-registry`) lists 1,497 dataset descriptors. I fetched all of them and searched the names and descriptions for sports, leagues, TV, broadcast, advertising, viewership | **Complete walk. 0 sports event, tracking, TV or ad datasets.** Only athlete physiology: Norwegian Endurance Athlete ECG (CC BY 4.0), cycloergometer test (ODC-By), treadmill test (PhysioNet health licence). Plus GDELT, already counted |
| **OpenML** | REST list: 7,120 active datasets, with row counts | About 50 sports-named sets, all small mirrors of public tables. Largest: Domestic-Football-results 1,078,214 rows (match results, no events); `nba-shot-logs` 128,069; `BNG(baseball)` 1,000,000 is a synthetic generator, not real. Licence field empty on all. **Nothing new, nothing direct** |
| **Figshare** | POST search API works (the GET path D-CATALOG used does not). 12 queries: play-by-play NFL and NBA, hockey event data, baseball tracking, football tracking, plus TV viewership, ad detection, replay, ratings and attention | Sports hits are paper supplements (survey tables, a hockey wearable impact figure, an NBA home-advantage .jmp). **0 play-level sets.** All five broadcast and viewing queries returned empty |
| **data.gov** | `catalog.data.gov` UI loads, but the `q` parameter is ignored (returned a generic list of 532,480 datasets); the CKAN API paths return 404 | **Not enumerable by a public endpoint.** Inconclusive. No sports event or TV viewing sets expected from federal publishers |
| **BigQuery public datasets** | `docs.cloud.google.com/bigquery/public-data` (opened) names no catalog and no count; the listing lives in Cloud Marketplace, a script-rendered page behind a Google sign-in. A listing API call needs credentials | **Not enumerable without login, so not done.** Known sports set unchanged: `ncaa_basketball` (Sportradar "_sr" tables; ">4 million plays" is from a Google lab answer key, the "42 M" claim is not corroborated; terms unread). Counts as no, and is a subset of the CBB games above |

Net from retries: **0 new rows.** Two catalogs (data.gov, BigQuery) remain unread; I do not claim they are empty.

## 4. Viewer preference: still zero

Two searches (one extended) for public per-minute or per-second US audience data tied to game state, including replication packages on Dataverse, openICPSR, Zenodo and OSF:

- **Nothing found.** Minute-level audience curves exist only as press charts (a single Super Bowl LV minute chart) or as proprietary products (iSpot second-by-second; Nielsen, Rentrak). Not downloadable.
- **Academic work uses it but does not release it.** Alavy, Gaskell, Leach and Szymanski (English football, minute-by-minute audience, finds viewing follows match progression and falls when a draw looks likely) and a Cork study of hurling use proprietary audience data; I found no data-availability link. NFL papers found are game-level Nielsen (Carney and Fenn, 496 games in 2000-2001; Lynch and Patel 2025) with no data release.
- **Closest open set:** Data in Brief, 11 lab viewers of one 2018 World Cup match, 62,380 observations (already in D-CATALOG, soccer, lab, not US). Useful as a method. Zenodo dual-screen eye-tracking (64 lab participants, TV clips, not live sports).

**Direct viewer preference rows: 0. No change.** The method is public even though the data is not: the minute-level finding (viewers leave when the outcome looks settled) supports the leverage rule in CLAUDE.md, and Nick's own logs remain the only source.

## 5. US broadcast cut labels: sports still zero, one news proxy for ad breaks

Searches (one extended, three standard) for labeled replays, camera cuts and commercial breaks:

| Dataset | What it labels | US sports? | Licence | Verdict |
|---|---|---|---|---|
| **GDELT Television News Advertising Inventory Files (AIF)** (blog post opened) | One row per second per broadcast: NEWS, ADVERTISING or UNCAPTIONED. 2009 to at least 2020, over 1.1 billion seconds. ABC, CBS, NBC evening news and their SF affiliates, CNN, MSNBC, Fox News, CNBC, Bloomberg, Fox Business, Telemundo, Univision, a few SF stations. Derived from closed-caption mode codes, in "captioning time" (lags video by up to 5-10 s). Author's spot checks found no errors | **No.** News channels only; no sports programming listed | Post states none. GDELT's site (opened) says all GDELT datasets are for unlimited, unrestricted use, including commercial, with citation | **T1 clean, not direct.** The only US broadcast ad-break timing at scale. Not tokenized (per-second labels are autocorrelated; one token per segment would be a few tens of millions at most, judgment). Not added to any total |
| UCI TV News Channel Commercial Detection | Shot-level features, 150 h, 5 news channels | No | CC BY 4.0 | Already in the ledger |
| SoccerNet-v2 | 301,883 labels: camera shots, replay grounding, 500 games | No (soccer) | Label licence not stated (blocked, as in ledger) | Still blocked |
| NHL event-detection set (arXiv 2004.06172) | 10 NHL broadcast games with ads and replays in the video, labeled for events | Yes, tiny | Not stated | Too small; no licence |
| Political TV Ad Archive (Internet Archive) | Ad airings with station and time | No (political ads) | Not found | No |
| SoccerHigh, "Comprehensive Dataset of Broadcast Soccer Videos" | Shot boundaries | No | Latter described as not public | No |
| MSU TV Commercial Detector, patents | Tools, not data | n/a | n/a | n/a |

**Zero for US sports broadcast cut, replay and ad-break labels: unchanged.** The new fact: ad-break structure of US TV is publicly labeled at second level for news. It can test an ad-break detector but cannot teach "dead ball to commercial" for sports, because news breaks follow a different clock.

## 6. Risks and what is still open

- Cricket: id-by-id comparison with Cricsheet was not possible (no per-match page). Passes on the owner's stated source and licence.
- CBB: row counts include non-play ESPN rows; the 6-token rate is the ledger's.
- CFB: measured floor 14.7 M for 2002-2021; the real figure depends on where 2022-2025 live. NHL count still unconfirmed.
- data.gov and BigQuery were not enumerable. The next move for either needs a Nick-side login or a browser session; both are outside "public endpoints only".
- GDELT AIF: lag and news-only scope; the sample rows in the post use "ADVERTISEMENT" while the text says "ADVERTISING", so check the label in the files before any use.

## Next step (under 2 minutes)

Nick: reply "out" on the StatsBomb Brady set, and add the +28.6 M and the CBB 194 M to the ledger's direct row.

## Sources

Tags: [O] opened, [S] search snippet only, [F] failed.

1. [O] huggingface.co/api/datasets/Sarthak213/doosra-cricket
2. [O] datasets-server.huggingface.co `/splits`, `/size`, `/first-rows` for Sarthak213/doosra-cricket [F: first-rows on config `default`, then read `matches`]
3. [O] huggingface.co/datasets/Sarthak213/doosra-cricket/raw/main/README.md
4. [O] cricsheet.org/downloads (both halves), cricsheet.org/matches/ ; [F] cricsheet.org/match/1000851/, /matches/1000851.json, /register.csv
5. [S] web search: Cricsheet 1000851 Australia South Africa Perth (ESPNcricinfo match id)
6. [O] github.com/statsbomb/amf-open-data: README, `LICENSE.pdf` (5 pages), `data/games.json`, git tree listing; [F] contents API (301)
7. [O] raw.githubusercontent.com/sportsdataverse/hoopR-mbb-data parquet footers, 23 files, plus GitHub contents listing
8. [O] sportsdataverse/cfbfastR-data `pbp/parquet`, 20 footers
9. [O] sportsdataverse/hoopR-nba-data `nba/pbp/parquet`, footers
10. [O] github.com/awslabs/open-data-registry tree plus 1,497 `datasets/*.yaml` [F: registry.opendata.aws/roda/ndjson 404]
11. [O] openml.org REST `data/list` (7,120 datasets) [F: tag and name filters mostly empty; second page HTTP 412]
12. [O] api.figshare.com `articles/search` (POST), 12 queries
13. [O] catalog.data.gov dataset page [F: CKAN API 404]
14. [O] docs.cloud.google.com/bigquery/public-data [S] web searches for BigQuery sports datasets [F] kaggle.com/datasets/ncaa/ncaa-basketball (empty page)
15. [S] web searches: minute-by-minute NFL audience replication data; Nielsen minute-level NFL papers; Alavy et al. data availability; set-top-box live sports open data
16. [S] web searches: sports broadcast replay and ad-break datasets; TV commercial detection datasets; NBA/NFL broadcast shot datasets; Political TV Ad Archive
17. [O] blog.gdeltproject.org/?p=12454 (AIF Captioning Time dataset); [O] gdeltproject.org/about.html
18. Earlier reports read: D-LEDGER, D-CATALOG
