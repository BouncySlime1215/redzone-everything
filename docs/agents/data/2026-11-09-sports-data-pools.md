---
id: D-FIELDS-A
title: Sports data pools by subcategory - largest open sources, licences, Kaggle fetch, and paid vendors (scope changed from 13 academic fields)
date: 2026-11-09
stream: data
author: research agent (Claude), D-FIELDS-A
status: draft
supersedes: []
sources: 70
verified: 52
affects: []
gate: []
---

# Sports data pools by subcategory

Scope note: the coordinator replaced the 13-field list with sports subcategories. The original
13-field findings are kept short in Appendix A. Nothing was downloaded; no secrets read; nothing
signed up. "Opened" = the page or API response was fetched and read. "Snippet-only" = seen only in
a search result. Unit of "data point" is stated on every row. Counts are the source's own.

## 0. Bottom line

1. Only a few sports pools reach 50 million real rows with a licence that looks usable:
   prediction markets (Polymarket trades, 1.27 B), soccer athlete GPS (SoccerMon, 6 B positions),
   audience interest (Wikimedia pageviews, CC0), and Dota 2 match records (1.19 B matches,
   match-level not event-level). One more (NHL_DB, 53.6 M tracking-like rows) has NO licence
   shown, so it is blocked until Nick or the owner clarifies.
2. Every league play-by-play set is far below 50 M. Real totals: cricket 5.11 M ball rows;
   NBA 18.26 M events in the biggest single set (snippet-only); MLB pitch sets about 1.4 M;
   NFL, CFB, WNBA, NCAA, tennis, F1: not measured by me (sizes in MB, not rows). Do not pad these.
3. Licence traps found: football-data.co.uk says its data is NOT for "data training products
   using automated bots/scrapers/AI" (opened). A Hugging Face mirror (eatpizzanot/soccer-dataset,
   17.05 M rows, tagged CC-BY-4.0) is built partly from it, so reject it. A card licence does
   not override upstream terms.
4. The big annotation sets (action spotting, highlights) are small by nature (tens of thousands
   of labelled events). They cannot reach 50 M and the biggest (SoccerNet) is NDA-gated.

## 1. Summary table (replaces the old 13-field table)

| Subcategory | Best sources found | True total (source-stated, unit) | Reaches 50 M? | Licence OK to train? | Kaggle fetch OK? |
|---|---|---|---|---|---|
| NFL | nflverse pbp (catalog); Big Data Bowl tracking (Kaggle, snippet) | not measured (catalog gives MB only); tracking rows not stated | not shown | nflverse CC-BY-4.0 (catalog); BDB rules not read | yes (nflverse HTTP); BDB needs Nick's Kaggle rules acceptance |
| CFB | SportsDataverse CFB pbp (catalog) | not measured | not shown | ESPN terms, personal use (catalog) | yes |
| NBA | cdechoch/nba-data-archive (HF); szymonjwiak (Kaggle, snippet); wyattowalsh (Kaggle, snippet) | HF set: 4.03 GB, rows not stated, HF tag 10M-100M; Kaggle set 18,255,730 events | not shown (tag says 10 M-100 M) | HF card Apache-2.0 but upstream stats.nba.com terms not checked; Kaggle set CC BY-NC-SA 4.0 | yes (HF parquet) |
| WNBA | wehoop (catalog); Kaggle NBA+WNBA set (snippet, Apache-2.0) | not measured | no evidence | unclear | yes |
| NCAA basketball | SportsDataverse (catalog); BigQuery ncaa_basketball (not opened) | not measured | not shown | ESPN terms | yes |
| NHL | RentoSaijo/NHL_DB (HF); MoneyPuck (catalog) | NHL_DB 53.6 M rows (puck+player coordinates per event); MoneyPuck 2,079,359 shots | yes, but only NHL_DB | NHL_DB: NO licence shown, card page 404 -> blocked; MoneyPuck non-commercial + credit | yes (HF parquet, stream) |
| MLB | Retrosheet, Statcast (catalog); Kaggle Statcast 2024-25 (snippet); Harvard Dataverse MLB 1904-2026 | Kaggle: 1,443,802 pitches; Dataverse 16.9 MB CC0 | no | Retrosheet free with credit; MLB feeds non-commercial | yes |
| Soccer (events) | StatsBomb open data; Wyscout public set (Kaggle ~520 MB, snippet); Kaggle European Club (2 M events, snippet) | counts mostly snippet-only: 941,009 / ~1.27 M / ~2 M events | no | StatsBomb: LICENSE.pdf not read (page asks attribution, silent on ML); football-data.co.uk REJECTED | yes |
| Soccer (tracking / sports science) | SoccerMon (Zenodo); SoccerTrack v2 (HF); Bundesliga 16-17 (Harvard Dataverse) | SoccerMon: 33,849 subjective + 10,075 objective reports with "over six billion" GPS positions; SoccerTrack v2: 10 matches, 21,428 ball events | yes (SoccerMon only) | SoccerMon CC BY 4.0 (athlete anonymisation not stated); SoccerTrack v2 CC BY 4.0 pseudonymised | SoccerMon is 99.1 GB: stream a team-season zip only |
| Tennis | Sackmann Match Charting Project (GitHub); HF tennis-sackmann-archive | 5,000+ matches charted; point rows not stated | no | CC BY-NC-SA 4.0 (non-commercial, personal OK) | yes (raw GitHub CSV) |
| Golf | none open found; Data Golf (paid) | n/a | no | n/a | n/a |
| Cricket | Cricsheet; HF amarharolikar mirror | Cricsheet 23,241 matches; mirror 5.11 M ball rows | no | mirror ODC-By; Cricsheet match-data licence page not opened (registers are ODC-By) | yes |
| Rugby | sporting-events.org (snippet) | not established | no | unknown | n/a |
| F1 / motorsport | OpenF1 (API) | 3.7 Hz car telemetry from 2023; rows not stated | not shown | CC BY-NC-SA 4.0 | yes (REST, no key, 3 req/s) |
| Olympics | none usable found (Sports-Reference family forbids ML, per catalog) | n/a | no | n/a | n/a |
| Combat sports | none verified | n/a | no | n/a | n/a |
| Esports | OpenDota dump; SC2EGSet (Zenodo); EsportsBench; MAICHEN Polymarket-Esports | OpenDota 1,191,768,403 matches (match-level); SC2EGSet 22.1 GB, 71 tournament archives, rows not stated; EsportsBench 2,782,828 rows | yes (OpenDota, match-level, not event logs) | OpenDota page states no licence (Academic Torrents listing says CC BY-SA 4.0, snippet-only); SC2EGSet CC BY 4.0; MAICHEN none -> reject | OpenDota is a torrent (not Kaggle-friendly); SC2EGSet via Zenodo HTTP |
| Betting and prediction markets | Polymarket trades (HF SII-WANGZJ); Kalshi historical API; The Odds API; Betfair Basic | Polymarket 1,266,885,846 trades, 4,049,900 markets, 279 GB | yes | MIT on card; upstream Polymarket terms not checked; wallet addresses must be dropped; sports share not labelled | yes (stream trades.parquet) |
| Player tracking (all sports) | NHL_DB, SoccerMon, SoccerTrack v2, BDB | see above | yes (SoccerMon; NHL_DB if licensed) | see above | see above |
| Action spotting / highlights | SoccerNet labels (gated); Voxel51 SoccerNet-V3 (HF, MIT); SoccerTrack v2 events | SoccerNet v1: 6,637 annotations over 500 games (snippet); SoccerTrack v2 21,428 | no, by nature | SoccerNet needs NDA (Nick); HF V3 MIT | yes for labels; video not needed |
| Commentary transcripts | SoccerNet-Echoes (HF); LFC J-League | Echoes 450 MB, counts not stated; LFC 40 matches | no | Echoes Apache-2.0; LFC REJECT (non-commercial research only, no redistribution) | yes |
| Audience interest | Wikimedia pageviews; Zenodo hourly derivative; Twitch/YTLive (catalog) | Zenodo derivative: ~5-6 M pages per day, 4.8 GB per month; official monthly files 6.3 GB + 8.8 GB bz2, rows not stated | yes (about 10 days of the derivative; inference) | CC0 (official); CC BY 4.0 (derivative) | yes (HTTP) |
| Biomechanics / sports science (large only) | SoccerMon; AddBiomechanics; Capture-24; PAMAP2 | AddBiomechanics 24 M frames; Capture-24 3,883 h; PAMAP2 3,850,505 rows; SoccerMon 6 B | yes (SoccerMon) | all CC BY 4.0; AMASS non-commercial | yes |

Reading guide: "Reaches 50 M" is only claimed where a source page states a count at or above
50 M. Row counts that were only a Hugging Face size-category tag are labelled as such.

## 2. Findings by subcategory

### 2.1 Prediction markets and betting (strongest link to "value of a moment")
- **Polymarket trades** (HF `SII-WANGZJ/Polymarket_data`, opened): 1,266,885,846 processed trades,
  2,026,704,633 raw order-fill events, 4,049,900 markets, 2022-11-21 to 2026-10-04, 279 GB total
  (orderfilled.parquet alone 127.2 GB), MIT on the card. Source is Polygon blockchain logs plus
  Polymarket Gamma API metadata. No category column, so sports markets must be found by slug or
  question text; the sports share is unknown. Wallet addresses are present: drop them (pseudonymous
  but identifiable by linkage). Polymarket's own API terms were not read: check before use.
  Link: price paths of live-game markets are a market-implied win probability, useful as a label
  for "which game has the next big moment". Unique (no overlap with catalog).
- **trentmkelly/polymarket_historical_data** (opened): CC BY 4.0, 15 GB, five-minute snapshots and
  order-book depth since 2026-07-23; rows not stated; also carries holder wallet fields (drop).
- **Kalshi historical API** (official docs opened): trades and candlesticks older than a cutoff via
  `/historical/*`. Auth, rate limits and usage terms are not stated on that page. Snippet-only
  claims that no key is needed: verify.
- **The Odds API** (opened): free 500 credits per month, includes historical snapshots back to
  2020; covers NFL, CFB, NBA, WNBA, NCAA, NHL, MLB, cricket, tennis, golf, rugby league, soccer.
  Terms on model training not read. Credits cap volume far below 50 M.
- **Betfair Historic Data**: Basic tier is free (1-minute odds, no volume); Advanced and Pro are
  paid (snippet-only; prices not confirmed).
- **Rejected:** football-data.co.uk (opened; forbids AI training use); HF `MAICHEN/Polymarket-Esports-
  Winner-L5` (66.9 GB, 100 M-1 B rows by tag, no dataset card, no licence, gated by HF-info consent).
- Small and fine: HF `minmaxxxer/nba-closing-lines-ats-2025-26`, `nhl-closing-odds-2025-26` (CC BY 4.0,
  1K-10K rows).

### 2.2 Player tracking and sports science
- **SoccerMon** (Zenodo, opened): CC BY 4.0, 99.1 GB in five files, two elite women's teams over
  2020 and 2021 seasons, 33,849 subjective reports, 10,075 objective reports, "over six billion"
  GPS position measurements. Page does not say whether athletes are anonymised and these are
  health-and-load records of named-team athletes: treat as conditional until the paper's data
  statement is read. Real and over 50 M, but GPS positions at high frequency are autocorrelated
  (fewer independent samples than the count suggests).
- **NHL_DB** (HF, opened viewer only): 53.6 M rows, Parquet, columns are game/event ids, timestamps,
  puck coordinates (raw and normalised), up to 16 player slots with coordinates. Licence, seasons
  and source are not shown and the dataset card URL returned 404. Highest-value NHL candidate but
  blocked: ask the owner or find the origin before any use.
- **SoccerTrack v2** (HF, opened): 10 university matches, 934 minutes, 21,428 ball-action events,
  145 GB, CC BY 4.0, pseudonymised. Small but clean labels of ball events.
- **Bundesliga 2016-17 spatiotemporal data** (Harvard Dataverse via DataCite record): CC0, four zips
  about 4.97 GB; frame count and source not read (landing page returned nothing). Snippet-grade.
- **NFL Big Data Bowl** (Kaggle): 10 Hz tracking per NFL marketing (snippet); per-season rows and
  the Kaggle rules/licence not verified. Competition data usually needs rules acceptance by Nick.
- **Driveline openbiomechanics**: snippet says strictly non-commercial.

### 2.3 Audience interest
- **Wikimedia pageviews** (opened): official analytics datasets are CC0. Monthly files for 2026-09
  are 6,326,820,535 bytes (user) and 8,803,066,102 bytes (automated), bz2. Row counts not on the
  pages I opened. Daily per-article rows with hourly letters exist from 2015 (pageview_complete
  readme). **Zenodo hourly derivative** (opened): Jan 2024, 4.8 GB, CC BY 4.0, about 5-6 M pages per
  day (pages with 10+ views). Link: spikes in a team's or event's page views track public interest
  (storylines, rivalries). Overlaps the enwiki dump in the catalog only in subject, not in data.
- Catalog already has Twitch (124 M interactions), YTLive, KuaiLive: not repeated.

### 2.4 Esports
- **OpenDota dump** (blog opened): 1,191,768,403 public Dota 2 matches, Mar 2011 to Mar 2016, three
  torrents (matches, player_matches, match_skill), 4 GB samples on Google Cloud Storage. Page states
  no licence; asks for citation. Match-level, not tick-level; useful for outcome priors, weak for
  "dead-ball timing". Torrent download is awkward in a Kaggle notebook (use the 4 GB samples).
- **SC2EGSet** (Zenodo, opened): CC BY 4.0, 22.1 GB, 71 tournament archives 2016-2024, derived game
  states from replays. Replay and event counts are not stated on the page. SC2ReSet is Other-NC.
- **EsportsBench** (HF, opened): 2,782,828 rows, 42.4 MB, CC BY-SA 3.0, match results across 20 games.
- **gptilt/lol-esports-matches** (opened): 138k games, 174k matches, CC BY-SA 3.0, no event
  timeline columns.
- A true "huge event log" for esports at 50 M or more was not found with a stated count and a clear
  licence. Do not count OpenDota as event logs.

### 2.5 League data (play-by-play and results)
- **NBA** `cdechoch/nba-data-archive` (opened): Apache-2.0 card, 4.03 GB parquet, shot detail
  1996/97-2025/26, nbastats PBP 1996/97-2024/25, pbpstats 2000/01-2024/25, matchups, v3 PBP. Rows not
  stated (tag 10 M-100 M). Collected from stats.nba.com, cdn.nba.com, pbpstats.com: upstream terms
  unchecked, so "licence OK" is unproven. Overlaps catalog's hoopR NBA pbp (same underlying events).
- **Cricket** `amarharolikar/ipl-international-cricket-ball-by-ball` (opened): 5.11 M ball rows,
  odc-by, source not named. **Cricsheet** (opened): 23,241 matches; JSON; licence page not opened.
- **Tennis** Match Charting Project (opened): CC BY-NC-SA 4.0, 5,000+ matches. Non-commercial: fine
  for Nick's personal system, not for a product.
- **MLB, NFL, CFB, NCAA, WNBA, NHL (non-tracking)**: no new large source beat the catalog. Kaggle
  Statcast 2024-25 has 1,443,802 pitches (snippet-only).
- **F1** OpenF1 (opened): free, no key, 3 requests per second, car telemetry at 3.7 Hz since 2023,
  CC BY-NC-SA 4.0. Probably tens of millions of telemetry rows per season but no count on the page.

### 2.6 Action spotting, highlights, commentary
- SoccerNet labels: 500 games, 6,637 annotations in the 2018 paper (snippet)
  (goals, cards, substitutions); video and labels need an NDA (already in catalog; Nick must do it).
  HF `Voxel51/SoccerNet-V3` is MIT (1K-10K rows, tag only). HF `OpenSportsLab/SoccerNet-GAR`
  licence "other".
- Commentary: SoccerNet-Echoes only (soccer, 450 MB, Apache-2.0, already in catalog).
  **Live Football Commentary (AIST)**: 40 J-League matches; terms limit to non-commercial academic
  research and forbid redistribution: reject. No multi-sport open commentary corpus with a
  clear licence was found.

## 3. Catalogs of catalogs: what worked

| Pool | Result |
|---|---|
| Hugging Face API search (nba, soccer, football, tennis, cricket, esports, formula1, nhl) | worked; gave licence, tag size, downloads; best discovery route |
| Zenodo API | worked for SC2 sets; keyword relevance poor for generic queries |
| DataCite API | worked; noisy; surfaced the Harvard Dataverse Bundesliga set and MLB CC0 set |
| Harvard Dataverse search | worked; no useful sports tracking hits |
| AWS Open Data registry | listing page truncated; no sports set seen in the part read (not conclusive) |
| Kaggle Datasets | pages do not render for the fetcher; sizes and licences are snippet-only |
| Google BigQuery public data | page redirected; not opened (NCAA basketball set exists per memory, unverified) |
| Academic Torrents | blocked by a browser check |
| Google Dataset Search, re3data, data.gov, data.europa.eu, Azure Open Datasets, OpenML | not done (JS-only or ran out of scope); open follow-up |

## 4. Free Kaggle notebook: how to fetch, $0

1. Kaggle notebook with Internet on (needs phone verification, free; snippet-only; disk and RAM
   figures conflict between sources, check Settings).
2. Hugging Face: `datasets.load_dataset(..., streaming=True)` or `hf_hub_download` of one parquet
   file. No token needed for public sets.
3. Zenodo, Wikimedia, GitHub raw: plain HTTPS.
4. AWS open buckets: boto3 with unsigned requests (`aws s3 ls --no-sign-request` works for the
   IBL, OpenNeuro, Allen and GDELT buckets in Appendix A).
5. Big sets (SoccerMon 99 GB, Polymarket 279 GB): stream or pick one file, reduce to features, keep
   only the small output. Do not mirror.
6. Nothing goes to the Mac.

## 5. Paid sources (Nick's decision only; nothing signed up)

| Vendor | What | Published price | Terms on model training |
|---|---|---|---|
| Sportradar | official feeds, many sports | none published (contact page, opened) | terms page opened has no ML/AI wording (no ToU text shown) |
| Stats Perform / Opta | event and tracking data | not found | not found |
| Genius Sports (owns Second Spectrum, bought 2021 for $200 M, snippet) | tracking, official league data | not found | not found |
| Second Spectrum | NBA, EPL, MLS tracking | not found (see Genius) | not found |
| PFF | NFL grades and charting | page returned 404/403 | not found |
| SportsDataIO | NFL, NBA, MLB, others | no official list; third-party estimates $250-$2,000 per month, Discovery Lab $99-149 per month (secondary, snippet) | page opened: Discovery Lab personal non-commercial only; commercial licences can cover modelling and ML; generative training not addressed; resale forbidden |
| Sportmonks (soccer) | events, odds, fixtures | opened: EUR 29 / 99 / 249 per month (monthly billing), 14-day trial, free plan (limits not shown), odds add-on EUR 15, history beyond three seasons add-on from EUR 29 | not read |
| API-Football (api-sports) | soccer and other sports | free 100 requests per day; Pro/Ultra/Mega about $19/$29/$39 (secondary, conflicting) | not read |
| The Odds API | odds, all major sports | opened: free 500 credits; $30, $59, $119, $249 per month | not read |
| Data Golf | golf rounds, strokes gained | price not on page (Scratch Plus membership), 45 requests per minute | not stated |
| Betfair Historic Data | exchange odds | Basic free; Advanced and Pro priced per package (unconfirmed) | not read |

## 6. Gaps and risks

- NFL, CFB, NCAA, WNBA, MLB, tennis row counts: not measured here; the catalog has MB sizes only.
- NBA `cdechoch` and Polymarket: upstream terms not read; card licences may not govern.
- SoccerMon and Polymarket carry person-level fields (athlete data, wallet addresses): strip before
  any training and check anonymisation.
- Several Kaggle numbers are snippet-only and may be stale.
- Kaggle's own terms for notebooks were not opened (docs page gave no values).

## Appendix A. Original 13 academic fields (short; link strength is for RedZone)

| Field | Link | Best real data found | True total | Licence OK | Kaggle fetch |
|---|---|---|---|---|---|
| Neuroscience | weak | IBL brain-wide map (CC-BY 4.0, anonymous S3, bucket ibl-brain-wide-map-public) | 621,733 neurons (snippet-only; release page says 376,730 units, 459 sessions); spike count not stated | yes | yes |
| Cognitive psychology | weak | Lichess games (CC0, 7.14 B rows, HF); Race IAT 2,355,303 respondents (OSF) | 7.14 B games (chess); lab data about 5 M | Lichess yes; IAT dataset licence unstated | yes |
| Vision / psychophysics | moderate for gaze, weak for quality | Atari-HEAD 328 M gaze samples (arXiv); COCO-Search18 about 300,000 fixations | 328 M raw samples; licence unconfirmed | unconfirmed | yes |
| Kinesiology / exercise physiology | weak | Capture-24 (3,883 h, CC BY 4.0), PAMAP2 3,850,505 rows, SoccerMon | 3.85 M rows; 6 B GPS | yes | yes |
| Biomechanics | weak | AddBiomechanics 24 M frames CC BY 4.0; AMASS non-commercial | 24 M | AddBiomechanics yes | yes |
| Sports medicine | weak | none beyond SoccerMon (patient and registry data rejected) | shared | conditional | yes |
| Human factors | weak | Aalto 136 M keystrokes (research and non-commercial use) | 136 M events | conditional | yes |
| Behavioral economics | moderate | Polymarket trades (see 2.1); Choices13k 13,006 problems | 1.27 B trades | see 2.1 | yes |
| Music | weak | MAESTRO 7.04 M notes (CC BY-NC-SA); Lakh MIDI 176,581 files CC-BY 4.0 | 7.04 M notes | partial | yes |
| Film / editing | weak-moderate | MovieLens 32 M ratings; Cinemetrics over 7,000 films (snippet) | 32 M ratings (taste, not cuts) | non-commercial | yes |
| Media studies | moderate | GDELT TV ngrams 16.9 B records (stations not listed); Wikimedia pageviews | 16.9 B | GDELT: any use with citation (terms page opened) | yes |
| Linguistics | moderate | SoccerNet-Echoes; Google Books Ngrams (CC BY 3.0, count not stated) | commentary text small | Echoes yes | yes |
| Theater | weak | DraCor about 2,060 plays (mixed licences, 14 unstated) | about 2,060 plays | partial | yes |

Reached 50 M with real counts in Appendix A: cognitive psychology (chess only), kinesiology (SoccerMon),
human factors, behavioral economics, media studies. Others did not or could not be shown.

## Sources

Tags: [O] opened and read, [S] snippet-only, [F] fetch failed.

1. [O] https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/blob/main/README.md
2. [O] https://huggingface.co/datasets/trentmkelly/polymarket_historical_data
3. [O] https://docs.kalshi.com/getting_started/historical_data
4. [O] https://the-odds-api.com/
5. [S] https://historicdata.betfair.com/ (search results; page 403)
6. [O] https://www.football-data.co.uk/data.php
7. [O] https://huggingface.co/datasets/eatpizzanot/soccer-dataset
8. [O] https://huggingface.co/datasets/MAICHEN/Polymarket-Esports-Winner-L5
9. [O] https://huggingface.co/datasets/RentoSaijo/NHL_DB
10. [F] https://huggingface.co/datasets/RentoSaijo/NHL_DB/blob/main/README.md
11. [O] https://zenodo.org/records/10033832 (SoccerMon)
12. [O] https://huggingface.co/datasets/atomscott/soccertrack-v2
13. [O] https://huggingface.co/datasets/cdechoch/nba-data-archive
14. [O] https://huggingface.co/datasets/amarharolikar/ipl-international-cricket-ball-by-ball
15. [O] https://cricsheet.org/downloads/
16. [O] https://cricsheet.org/register/
17. [O] https://github.com/JeffSackmann/tennis_MatchChartingProject
18. [O] https://openf1.org/
19. [O] https://blog.opendota.com/2017/03/24/datadump2/
20. [S] https://academictorrents.com/details/c41059d1fd3b9a0f3dfec769ac2d6468c0e955b8 (OpenDota licence, via search)
21. [O] https://zenodo.org/records/17829625 (SC2EGSet)
22. [O] https://zenodo.org/api/records?q=SC2EGSet%20StarCraft%20replay&size=5
23. [O] https://huggingface.co/datasets/EsportsBench/EsportsBench
24. [O] https://huggingface.co/datasets/gptilt/lol-esports-matches
25. [O] https://dumps.wikimedia.org/other/pageview_complete/monthly/2026/2026-09/
26. [O] https://dumps.wikimedia.org/other/pageview_complete/readme.html
27. [O] https://zenodo.org/records/17070470
28. [O] https://github.com/statsbomb/open-data
29. [F] https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf
30. [O] https://kirt.airc.aist.go.jp/corpus/en/LFC
31. [O] https://huggingface.co/datasets/SushantGautam/SoccerNet-Echoes
32. [S] https://arxiv.org/pdf/1804.04527 (SoccerNet counts, via search)
33. [S] https://www.kaggle.com/datasets/szymonjwiak/nba-play-by-play-data-1997-2023
34. [S] https://www.kaggle.com/datasets/wyattowalsh/basketball
35. [S] https://www.kaggle.com/datasets/aleespinosa/soccer-match-event-dataset
36. [S] https://www.kaggle.com/datasets/yasunorim/mlb-statcast-bat-tracking-2024-2025
37. [S] https://www.kaggle.com/competitions/nfl-big-data-bowl-2025/overview
38. [O] https://huggingface.co/api/datasets?search=nba (and soccer, football, tennis, cricket, esports, formula1, nhl; eight API listings)
39. [O] https://api.datacite.org/dois?query=%22play-by-play%22%20OR%20%22player%20tracking%22
40. [O] https://dataverse.harvard.edu/api/search?q=sports+tracking+OR+play-by-play
41. [F] https://academictorrents.com/browse.php (browser check)
42. [O] https://registry.opendata.aws/?search=tags:sports
43. [O] https://sportsdata.io/help/data-rights-and-licensing-questions
44. [S] https://sportsapis.dev/apis/sportsdata-io
45. [O] https://www.sportmonks.com/football-api/plans-pricing/
46. [S] https://www.api-football.com/pricing
47. [O] https://datagolf.com/api-access
48. [O] https://www.sportradar.com/terms-and-conditions/
49. [F] https://www.pff.com/api
50. [S] https://sbcnews.co.uk/sportsbook/2021/05/07/genius-bolsters-sports-portfolio-with-200m-second-spectrum-acquisition/
51. [S] https://sporting-events.org/data/
52. [S] https://rdrr.io/cran/sportsR/
53. [O] https://registry.opendata.aws/ibl-brain-wide-map/ (Appendix A)
54. [O] https://userinterfaces.aalto.fi/136Mkeystrokes/ (Appendix A)
55. [O] https://ora.ox.ac.uk/objects/uuid:99d7c092-d865-4a19-b096-cc16440cd001 (Appendix A)
56. [O] https://archive.ics.uci.edu/dataset/231/pamap2+physical+activity+monitoring (Appendix A)
57. [O] https://addbiomechanics.org/download_data.html (Appendix A)
58. [O] https://magenta.tensorflow.org/datasets/maestro (Appendix A)
59. [O] https://colinraffel.com/projects/lmd/ (Appendix A)
60. [O] https://huggingface.co/datasets/Lichess/standard-chess-games (Appendix A)
61. [O] https://reference-global.com/article/10.5334/jopd.ac (Appendix A, Race IAT)
62. [O] https://arxiv.org/abs/1903.06754 (Appendix A, Atari-HEAD)
63. [O] https://github.com/cvlab-stonybrook/Scanpath_Prediction (Appendix A, COCO-Search18)
64. [O] https://www.gdeltproject.org/about.html (Appendix A)
65. [O] https://blog.gdeltproject.org/television-news-ngram-2-0-dataset-16-9-billion-records/ (Appendix A)
66. [O] https://dracor.org/api/v1/corpora (Appendix A)
67. [O] https://grouplens.org/datasets/movielens/ (Appendix A)
68. [S] https://www.internationalbrainlab.com/the-brainwide-map-press (621,733 neurons, via search)
69. [O] https://storage.googleapis.com/books/ngrams/books/datasetsv3.html (Appendix A)
70. [O] https://amass.is.tue.mpg.de/license.html (Appendix A)

Note on item 6 vs 62-style entries: counts above are of distinct pages; API listings in item 38
count as one source.
