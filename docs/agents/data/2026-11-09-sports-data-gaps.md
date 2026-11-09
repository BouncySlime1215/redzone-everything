---
id: D-SPORTS-GAPS
title: Sports data gaps - portal searches, bigger league sets, long-tail sports, Kaggle metadata confirmation
date: 2026-11-09
stream: data
author: research agent (Claude), D-SPORTS-GAPS
status: draft
supersedes: []
sources: 79
verified: 58
affects: []
gate: []
---

# Sports data gaps (follow-up to D-FIELDS-A)

Nothing was downloaded; nothing signed up; no repo secrets read. "Opened" = page or API response
fetched and read. Web-page text was treated as data. "Confirmed" = a count stated on the source's own
page or its public metadata API. "Snippet-only" = search snippet or estimate. Unit of a "data point"
is stated on every row (play, pitch, event, ball, shot, frame). Counts are not padded and not
synthetic.

## 0. Bottom line

1. **Kaggle numbers are now checkable.** The public Kaggle metadata API
   (`/api/v1/datasets/view/<owner>/<slug>` and `/datasets/list?search=`) answered through the fetcher
   with title, licence, bytes, update date and full description. Kaggle pages do not render, but
   this API does. It confirmed the NBA 18.26 M figure (CC BY-NC-SA 4.0).
2. **MLB was low because the old figure was one 2-season Kaggle set.** Real totals: Retrosheet
   16,756,076 plays in 208,684 games (free incl. commercial, with credit; opened). Statcast pitches on
   Hugging Face: 8,478,976 exact rows (MIT card), but MLB's own copyright text allows only
   "individual, non-commercial, non-bulk use", so Statcast is NOT cleared for training.
3. **Biggest new confirmed numbers:** MLB Retrosheet 16.76 M (was 1.44 M); cricket 5,852,078 balls
   (was 5.11 M); NFL 1,239,130 plays (was unmeasured); NBA 18.6 M events (was 18.26 M).
4. **Unique, licence-checked, count-confirmed total: about 44.5 M data points across all leagues**
   (section 4). Still no single league beyond NBA and MLB passes 10 M with a clear licence. NHL_DB
   (53.6 M rows) stays blocked: no licence.
5. **Portals:** Google Dataset Search rendered (100+ hits, mostly Kaggle and marketplaces).
   Azure Open Datasets, data.gov, data.europa.eu, re3data: nothing usable. OpenML: API blocked (HTTP
   412), search snippets show only tiny baseball tables. Details in section 2.
6. **Golf, rugby, Olympics, combat:** still nothing usable. Golf has ShotLink-derived strokes
   (about 1 M per season by the package's own estimate) but the upstream licence is restricted.

## 1. League tables

Licence column: **Yes** = permissive text read and upstream clear. **Cond** = personal or
non-commercial only, or upstream terms not proven. **No** = forbids or unknown. Fetch column:
Kaggle notebook, $0, no signup beyond a Kaggle account Nick already has.

### 1.1 NFL

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| nflverse play-by-play mirror, HF `ran-codes/nfl_pbp_all` | 1,239,130 plays (464,557,559 bytes parquet; datasets-server) | Yes: card MIT; nflverse repo CC-BY-4.0 (opened) | Yes (parquet) | Confirmed |
| Kaggle `benbaldwin/nflfastr` (nflfastR plays since 1999) | rows not stated; 1.99 GB | Yes (CC0 on Kaggle) | Yes | Confirmed licence; same plays as the row above, not added |
| Kaggle `toddsteussie/nfl-play-statistics-dataset-2004-to-present` | rows not stated; 877 MB; plays 2004-2019 | Yes (CC0) | Yes | Confirmed licence; overlaps nflverse, not added |
| NFL Big Data Bowl tracking (Kaggle competition, 2017 GitHub repo) | per-frame rows not stated; 2017 repo: 1 game tracked at 10 fps | Cond: rules acceptance by Nick; GitHub repo states no licence | Gated | Snippet-only; competition API returned 401 |

Unique NFL total used: **1,239,130 plays**. No NFL tracking count confirmed.

### 1.2 CFB

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| SportsDataverse `cfbfastR-data` (ESPN and CFBD sources) | not stated (releases, sizes only) | No: repo states no licence; ESPN source | Yes (GitHub releases) | Unconfirmed count |
| Kaggle `keonim/nfl-game-scores-dataset-2017-2023` | NFL, not CFB; 56 MB | Yes (CC0) | Yes | n/a |

Unique CFB total used: **0 confirmed.** Repo opened; it carries no licence file text on the page.

### 1.3 NBA

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Kaggle `deocheng/nba-data-1946-2026` | about 18.6 M play-by-play events 1997-2026; 7.79 M shots; 77,974 games; 6.33 GB | Cond: Apache-2.0 on card; NBA terms (opened) allow "private, non-commercial" stats use only, bar "comprehensive database" sites | Yes | Confirmed (description) |
| Kaggle `szymonjwiak/nba-play-by-play-data-1997-2023` | 18.26 M events, 37,928 games, 6.32 M shots; 2.15 GB | Cond: CC BY-NC-SA 4.0 + NBA terms | Yes | Confirmed (metadata API) |
| Kaggle `wyattowalsh/basketball` | more than 13 M play-by-play rows; 65,000+ games; 4.66 GB | Cond: CC BY-SA 4.0 + NBA terms | Yes | Confirmed |
| Kaggle `brains14482/nba-playbyplay-and-shotdetails-data-19962021` | rows not stated; 13.09 GB; NBA and WNBA | Cond: Apache-2.0 | Yes | Count unconfirmed |
| Kaggle `eoinamoore/historical-nba-data-and-player-box-scores` | rows not stated; 1.87 GB; PBP from 1996 | Cond: CC0 + NBA terms | Yes | Count unconfirmed |
| HF `cdechoch/nba-data-archive` | datasets-server returned no size; tag 10M-100M | Cond: Apache-2.0 | Yes | Count unconfirmed |
| HF `dcayton/nba_tracking_data_15_16` (SportVU 2015-16, 600+ games) | rows not stated; card 22.3 kB (loader only) | No: no licence; Stats Perform data | n/a | Snippet-only; do not use |

All NBA sets come from the same NBA stats feed, so they overlap almost fully. Unique NBA total
used: **18.6 M events** (the largest confirmed single set, not a sum). Shot rows are a subset
of events. Not added: 13 M (wyattowalsh), 18.26 M (szymonjwiak).

### 1.4 WNBA

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Kaggle `brains14482/...` (NBA and WNBA plays and shots) | rows and WNBA share not stated; 13.09 GB | Cond: Apache-2.0 | Yes | Count unconfirmed |
| SportsDataverse `wehoop-data` | not stated; archived repo, ESPN source | No: no licence on page | Yes | Count unconfirmed |
| Kaggle small WNBA sets (box scores, gamelogs, 3 MB or less) | n/a | various | Yes | Too small |

Unique WNBA total used: **0 confirmed.**

### 1.5 NCAA basketball

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| BigQuery `ncaa_basketball.mbb_pbp_sr` | "over 4 million" plays per a training-lab answer key; 2009 onward | Unconfirmed: Google public dataset (Sportradar-sourced); terms not read | Needs a Google Cloud project | Snippet-only |
| Kaggle `robbypeery/college-basketball-pbp-23-24` | rows not stated; 2.69 GB; one season | No: licence Unknown | Yes | Count unconfirmed |
| SportsDataverse `hoopR-data` (ESPN MBB) | not stated; archived 2026-09-18 | No: no licence | Yes | Count unconfirmed |

Unique NCAA total used: **0 confirmed.** BigQuery page pages did not list the dataset (opened
the public-data docs page; it names only samples such as gsod, natality, wikipedia).

### 1.6 NHL

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| HF `RentoSaijo/NHL_DB` (puck and player coordinates) | 53.6 M rows (from earlier report) | No: no licence shown | Yes | Carried over, blocked |
| Kaggle `owensingh38/nhl-play-by-play-data-2010-2026` | rows not stated; 818 MB; 2010-11 to 2025-26; from NHL API via wsba_hockey | Cond: MIT on card; NHL terms not read | Yes | Count unconfirmed |
| MoneyPuck shots | 2,079,359 shots (from earlier report, not re-opened) | Cond: non-commercial and credit | Yes | Carried over |
| Kaggle `s903124/nhl-playbyplay-data-from-2007` | rows not stated; 336 MB | Cond: LGPL-3.0 | Yes | Count unconfirmed |

Unique NHL total used: **2,079,359 shots** (licence-conditional). 53.6 M stays blocked.

### 1.7 MLB

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Retrosheet play-by-play (all games file) | **16,756,076 plays** in 208,684 games; 1908-2025 seasonal event files | **Yes**: "free to make any desired use", incl. selling, with credit (notice.txt, plays.html opened) | Yes | Confirmed |
| HF `Jensen-holm/statcast-era-pitches` | **8,478,976 pitches**, 118 columns, 826 MB; 2015-present | **No for bulk**: card MIT, but MLB copyright text allows only "individual, non-commercial, non-bulk use" | Yes | Confirmed count |
| HF `jab13/mlb-statcast-dataset` | 2,966,881 pitches | No: no licence on card; same MLB source | Yes | Confirmed count; overlaps row above |
| Kaggle `yasunorim/mlb-statcast-bat-tracking-2024-2025` | 2024-2026 pitches; 317 MB; CC0 packaging only | No: card says MLB terms govern | Yes | Metadata confirmed; this is the "1.44 M" set |
| Kaggle `pschale/mlb-pitch-data-20152018` | 2015-2018 pitches; 1.06 GB; rows not stated | No: "Data files (c) Original Authors" | Yes | Count unconfirmed |
| Chadwick `retrosplits` (Retrosheet-derived, ODbL) | not stated | Yes (ODbL per search) | Yes | Snippet-only; overlaps Retrosheet |

Why the old 1.4 M was low: it counted only 2024-25 pitches. All Statcast-era pitches are 8.48 M. Statcast
is the largest pitch-level set but the licence blocks it. Retrosheet plays are plate-appearance
events (pitch sequence strings only), so pitches and plays are different units and are not added.
Unique MLB total used: **16,756,076 plays** (licence-clear).

### 1.8 Soccer (events and tracking)

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| HF `Maiyarasu/football_analytics` | 4,409,194 events (22 columns, 33 event types) | Unproven: card MIT, source not named (looks StatsBomb-style, my inference) | Yes | Count confirmed; provenance unproven |
| Transfermarkt datasets (GitHub, dcaribou) | 1,270,000+ game events; 88,000+ games; 1.89 M appearances | Cond: repo CC0-1.0, but scraped from Transfermarkt; its terms not read | Yes | Confirmed (repo text) |
| Wyscout public events (figshare 7770599; Kaggle `aleespinosa`) | events not stated on either page; zip 77 MB; Kaggle 3.23 GB (2017/18 five leagues, World Cup, Euro 2016) | Conflict: figshare and Kaggle say CC BY 4.0; SoBigData catalogue snippet says non-commercial | Yes | Count unconfirmed |
| Kaggle `secareanualin/football-events` | 941,009 events, 9,074 games | No: licence Unknown | Yes | Confirmed |
| IDSSE / Sportec (DFL), HF `pysport/idsse-data` | 7 matches, tracking 25 fps plus events; 2.63 GB; about 135,000 frames per match (my arithmetic, not stated) | Yes: CC-BY 4.0, cite DFL and paper | Yes | Confirmed licence; frames inferred |
| SkillCorner open data (GitHub) | 10 A-League matches, tracking 10 fps; frame count not stated | Cond: MIT repo; asks for credit | Yes | Confirmed scope |
| StatsBomb open data (GitHub) | competitions and matches not counted on page | Unproven: LICENSE.pdf unread; page asks for attribution, silent on ML | Yes | Count unconfirmed |
| Kaggle `atomscott/soccertrack` | 6.83 GB video and GNSS; counts not stated | Cond: LGPL-3.0 | Yes | Count unconfirmed |
| Kaggle `ekrembayar/metrica-sports-football-tracking-data` | 183 MB; no description | No: licence Unknown | Yes | Count unconfirmed |

SoccerMon (6 B GPS positions) and SoccerTrack v2 stay as in the earlier report. Unique soccer total
used: **0 licence-checked events**; **1.27 M** (Transfermarkt) and **4.41 M** (unproven
provenance) listed apart (section 4).

### 1.9 Cricket

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Kaggle `ariadaikalam/the-ultimate-ball-by-ball-cricket-dataset` (Cricsheet Tests, ODIs, T20Is, 12 leagues) | **5,852,078 balls**; 14,585 matches; 205,902 wickets | Yes for Cricsheet part: ODC-By (cricsheet.org/licensing opened; commercial allowed with attribution) | Yes (2.4 GB) | Confirmed (description) |
| Same set, Cricinfo tracking file | 4,468,782 tracked balls (extra columns on the same balls); 1,654,041 text predictions | Unproven: Cricinfo terms not read | Yes | Confirmed count; not added |
| Kaggle `veeralakrishna/cricsheet-a-retrosheet-for-cricket` | 7,470 matches; 4.72 GB | Cond: CC0 on Kaggle; Cricsheet is ODC-By | Yes | Confirmed matches only |
| Earlier HF mirror `amarharolikar/...` | 5.11 M balls | ODC-By | Yes | Superseded by 5.85 M |

Cricsheet's own site lists 23,241 matches (earlier report); the 5.85 M balls cover 14,585 of them
(the set's own scope). Unique cricket total used: **5,852,078 balls.**

### 1.10 F1 and motorsport

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Jolpica (Ergast successor) | 26,313 race results | Unproven: code Apache-2.0; data terms in TERMS.md, not opened | Yes (REST) | Confirmed count (small) |
| OpenF1 | telemetry 3.7 Hz from 2023; 24 race sessions in 2025; rows not stated | No for a product; CC BY-NC-SA 4.0, personal use allowed | Yes (3 req/s) | Count unconfirmed |
| FastF1 | not stated | Unknown; most features need an F1TV subscription (paid) | No ($) | Rejected |
| Kaggle F1 telemetry sets | 0.2 MB to 250 MB each; mixed licences | mixed | Yes | Small |

Unique F1 total used: **26,313 results** (not play-level). No telemetry count is confirmed.

### 1.11 Golf

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| golfastR hosted ShotLink strokes (2012 onward, PGA Tour) | about 1 M strokes per season (package's own estimate) | No: package MIT covers code; ShotLink redistribution restricted (Data Golf forum snippet) | Yes | Snippet-grade count |
| Kaggle `samanthastaheli/tournamentshots` | 19 MB; "web scrapped from PGA TourCast" | No: licence Unknown; scraped | Yes | Count unconfirmed |

Unique golf total used: **0.**

### 1.12 Rugby

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| RADL (GitHub JFrusher) | one synthetic sample match | CC BY 4.0 data | Yes | Synthetic: exclude |
| RugbyEvents (broadcast video benchmark) | not stated | not seen | n/a | Snippet-only |
| Oval Insights event data | 70+ event types, one competition 2020/21 | No: commercial provider | n/a | Snippet-only |

Unique rugby total used: **0.**

### 1.13 Olympics

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Olympedia (olympedia.org) | not stated; statistics page 404 | Unproven: opened to the public by IOC permission, no licence | n/a | Snippet-only |
| Kaggle Olympedia derivatives (Joseph Cheng etc.) | to 2022 | Unknown | Yes | Snippet-only |
| Taiwan open data, participation results | not event-level | Open Government Data License 1.0 | Yes | Wrong grain |

Unique Olympics total used: **0.**

### 1.14 Combat sports

| Dataset | True data points | Licence OK | Fetch OK | Status |
|---|---|---|---|---|
| Kaggle UFC sets (about 20 sets, 0.2 to 5 MB, CC0 or MIT labels) | fight or round level; rows not stated; scraped from ufcstats.com | Unproven: upstream site terms not read | Yes | Metadata confirmed; counts unconfirmed |
| Boxing punch stats | none found | n/a | n/a | Not found |

Unique combat total used: **0** (tiny and scraped).

## 2. Portals searched (the six that were open follow-ups)

| Portal | Method | Result |
|---|---|---|
| Google Dataset Search | opened search page (rendered, "100+ datasets") | Hits were Kaggle NBA, WNBA, college basketball, NFL sets, marketplaces (Spartera, Bright Data), Roboflow. One licence shown (DbCL 1.0 for an NBA Kaggle set). No new source beyond Kaggle. |
| re3data | API opened, query "sports" | One sports repository (NCAA Student-Athlete Experiences Data Archive: survey data about people, reject); two mocap databases (CMU, HDM05). No play-by-play or tracking repository. |
| data.gov | API returned 404; HTML search for "play-by-play" returned 532,480 datasets, top 20 "most popular" are unrelated | Nothing relevant seen. Not conclusive. |
| data.europa.eu | API opened: 65,407 hits for "sport tracking match" | Visible items: Danish spectator counts, French most-bet matches, a Greek match list, a Zenodo human-motion set. No league event data. |
| Azure Open Datasets | catalog page opened | Taxis, labour, safety, MNIST, MIND; no sports. |
| OpenML | API blocked (HTTP 412); web search only | Only tiny baseball tables (1,340 rows, 26 rows). Not conclusive. |

Extra discovery route that worked: Kaggle `datasets/list?search=` (public metadata API) and
Hugging Face `datasets-server.huggingface.co/size` for exact row counts.

## 3. Kaggle snippet-only figures now re-checked

| Item in earlier report | Re-check | Result |
|---|---|---|
| szymonjwiak NBA, 18,255,730 events | Kaggle API opened | 18.26 M events, 37,928 games, 6.32 M shots, CC BY-NC-SA 4.0, updated 2026-04-17. **Confirmed.** |
| wyattowalsh NBA | Kaggle API opened | CC BY-SA 4.0, 4.66 GB, more than 13 M PBP rows. **Confirmed.** |
| yasunorim Statcast, 1,443,802 pitches | Kaggle API opened | CC0 packaging, 317 MB, 2024-2026 seasons, "MLB's terms govern". Row count not on page; 1.44 M is plausible for two seasons but **unconfirmed here**. |
| aleespinosa Wyscout, about 520 MB | Kaggle API opened | 3.23 GB, CC BY 4.0, 2017/18 five leagues plus World Cup and Euro. Earlier size was wrong. |
| Kaggle European Club (2 M events) | not re-found | Replaced by `secareanualin/football-events`: 941,009 events, licence Unknown. |
| NFL Big Data Bowl | Competitions API 401 | **Still unconfirmed.** |
| Kaggle notebook disk and RAM figures | not retested | **Still unconfirmed.** |

Public Kaggle metadata is a mirror of what the dataset owner typed. A Kaggle licence label does
not override upstream terms (MLB, NBA, ESPN, Sportradar). Treat each CC0 label on scraped data as
Cond.

## 4. Totals, unique, no double counting

Rule: where sets share an upstream source, only the single largest confirmed set counts. Units are
plays, events, balls or shots, not mixed with pitches.

| Tier | Meaning | League and set | Count |
|---|---|---|---|
| A | Count confirmed, licence permits, upstream clear | NFL nflverse plays | 1,239,130 |
| A | same | MLB Retrosheet plays | 16,756,076 |
| A | same | Cricket Cricsheet balls | 5,852,078 |
| | | **Tier A subtotal** | **23,847,284** |
| B | Count confirmed, personal or non-commercial only | NBA events (largest single set) | 18,600,000 |
| B | same | NHL MoneyPuck shots (carried over) | 2,079,359 |
| | | **Tier B subtotal** | **20,679,359** |
| | | **A + B** | **44,526,643** |
| C | Not counted: licence blocked, provenance unproven or count unconfirmed | MLB Statcast pitches (MLB non-bulk) | 8,478,976 |
| C | | NHL_DB rows (no licence) | 53,600,000 |
| C | | Soccer events, `Maiyarasu` (provenance unproven) | 4,409,194 |
| C | | Transfermarkt events (scraped, ToS unread) | 1,270,000+ |
| C | | `football-events` (licence Unknown) | 941,009 |
| C | | NCAA BigQuery plays (snippet) | over 4,000,000 |
| C | | Golf ShotLink strokes (estimate, restricted) | about 1,000,000 per season |

Comparison with the earlier report (D-FIELDS-A):

| League | Earlier | Now (licence-checked) | Change |
|---|---|---|---|
| NFL | not measured | 1,239,130 plays | new |
| CFB | not measured | 0 confirmed | no change |
| NBA | 18.26 M (snippet) | 18.6 M confirmed (Cond) | +0.34 M; Kaggle now confirmed |
| WNBA | not measured | 0 confirmed | no change |
| NCAA | not measured | 0 confirmed (BigQuery snippet over 4 M) | no change |
| NHL | 2.08 M shots clear; 53.6 M blocked | 2.08 M; 53.6 M still blocked | no change |
| MLB | about 1.4 M pitches | 16.76 M plays clear; 8.48 M pitches blocked | 11.6x more |
| Soccer events | 0.94 to 2 M (snippets) | 0 licence-checked; 4.41 M and 1.27 M unproven | no clean gain |
| Cricket | 5.11 M | 5.85 M | +0.74 M |
| F1 | not measured | 26,313 results; telemetry unmeasured | small |
| Golf, rugby, Olympics, combat | none | none | no change |

Total of all leagues reaches about 44.5 M licence-checked. **No league alone reaches 50 M.** Adding
the C tier would pass 50 M only by counting blocked NHL_DB or Statcast.

## 5. Risks and open items

- NBA licence is the weakest "Cond": NBA terms (opened) allow stats only for "private, non-commercial"
  use and bar use on sites with a comprehensive stats database. A personal TV controller fits; a
  shared service or published model may not. Nick's call.
- MLB: Retrosheet is clear; Statcast is not. Ask MLB or stay with Retrosheet.
- The mlb.com terms page returned HTTP 406; only the older `copyright.txt` was read.
- NHL_DB and Maiyarasu: ask the owner or find the origin before any use.
- Wyscout: two licence statements conflict; the SoBigData catalogue page could not be fetched.
- Retrosheet attribution text must appear with any use; read notice.txt wording at use time.
- Count unconfirmed: NFL tracking, CFB, WNBA, NCAA, NHL rows, F1 telemetry, StatsBomb matches,
  Statcast years (2015-present is from the card's example, not a stated range).
- BigQuery needs a Google Cloud project; not signed up here.

## Sources

Tags: [O] opened and read, [S] snippet-only (search result), [F] fetch failed.
Query families on one host count as one source.

1. [O] Hugging Face API listings (statcast, play-by-play, pitch, nfl, mlb, hockey, basketball, wnba, ncaa, cricket, nba, football)
2. [O] datasets-server.huggingface.co/size (Jensen-holm, ran-codes, Maiyarasu, jab13; cdechoch and dcayton returned no count)
3. [O] https://huggingface.co/datasets/Jensen-holm/statcast-era-pitches (and raw README)
4. [O] https://huggingface.co/datasets/ran-codes/nfl_pbp_all
5. [O] https://huggingface.co/datasets/Maiyarasu/football_analytics
6. [O] https://huggingface.co/datasets/dcayton/nba_tracking_data_15_16
7. [O] https://huggingface.co/datasets/pysport/idsse-data
8. [O] https://www.retrosheet.org/notice.txt
9. [O] https://www.retrosheet.org/game.htm
10. [O] https://retrosheet.org/downloads/plays.html
11. [O] https://retrosheet.org/datause.html
12. [S] web search, Retrosheet totals (205,886 games and 16,538,512 plays in an older figure; superseded by item 10)
13. [O] https://github.com/nflverse/nflverse-data/releases
14. [O] https://github.com/nflverse/nflverse-data
15. [F] https://github.com/nflverse/nflverse-pbp (504)
16. [O] Kaggle API view szymonjwiak/nba-play-by-play-data-1997-2023
17. [O] Kaggle API view wyattowalsh/basketball
18. [O] Kaggle API view yasunorim/mlb-statcast-bat-tracking-2024-2025
19. [O] Kaggle API view aleespinosa/soccer-match-event-dataset
20. [O] Kaggle API view brains14482/nba-playbyplay-and-shotdetails-data-19962021
21. [O] Kaggle API view deocheng/nba-data-1946-2026
22. [O] Kaggle API view pschale/mlb-pitch-data-20152018
23. [O] Kaggle API view s903124/mlb-statcast-data
24. [O] Kaggle API view toddsteussie/nfl-play-statistics-dataset-2004-to-present
25. [O] Kaggle API view secareanualin/football-events
26. [O] Kaggle API view owensingh38/nhl-play-by-play-data-2010-2026
27. [O] Kaggle API view ariadaikalam/the-ultimate-ball-by-ball-cricket-dataset
28. [O] Kaggle API view veeralakrishna/cricsheet-a-retrosheet-for-cricket
29. [O] Kaggle API view eoinamoore/historical-nba-data-and-player-box-scores
30. [O] Kaggle API view robbypeery/college-basketball-pbp-23-24
31. [O] Kaggle API view samanthastaheli/tournamentshots
32. [O] Kaggle API view benbaldwin/nflfastr
33. [O] Kaggle API list searches (pitch by pitch, nba, nfl, statcast, wnba, college football, tracking, nhl, college basketball, cricket, f1, soccer, big data bowl, golf, ufc)
34. [F] Kaggle competitions API (401)
35. [O] https://datasetsearch.research.google.com/search?query=play-by-play%20sports
36. [F] catalog.data.gov API (404, two tries)
37. [O] https://catalog.data.gov/dataset?q=play-by-play
38. [O] data.europa.eu search API (two calls)
39. [F] OpenML API (HTTP 412, two tries)
40. [S] web search, OpenML sports datasets
41. [O] re3data API, query sports
42. [S] web search, re3data sports repositories
43. [O] https://learn.microsoft.com/en-us/azure/open-datasets/dataset-catalog
44. [O] https://docs.cloud.google.com/bigquery/public-data
45. [F] console.cloud.google.com NCAA basketball product page (header overflow)
46. [S] web search, BigQuery ncaa_basketball (over 4 M plays, lab answer key)
47. [O] https://baseballsavant.mlb.com/csv-docs
48. [F] https://www.mlb.com/official-information/terms-of-use (406)
49. [O] http://gdx.mlb.com/components/copyright.txt
50. [O] https://www.nba.com/termsofuse
51. [O] https://github.com/nfl-football-ops/Big-Data-Bowl
52. [S] web search, Big Data Bowl 2026/2027 tracking terms
53. [O] https://cricsheet.org/about/
54. [O] https://cricsheet.org/licensing/
55. [O] https://openf1.org/
56. [O] https://api.openf1.org/v1/sessions?year=2025&session_name=Race
57. [O] https://api.jolpi.ca/ergast/f1/results.json?limit=1
58. [F] https://api.jolpi.ca/ergast/f1/laps.json (400)
59. [O] https://github.com/jolpica/jolpica-f1
60. [O] https://docs.fastf1.dev/
61. [O] https://api.figshare.com/v2/articles/7770599
62. [F] https://www.nature.com/articles/s41597-019-0247-7 (authentication redirect)
63. [F] https://ckan-d4s1.d4science.org/dataset/public_data_set_of_spatio-temporal_match_events_in_soccer_competitions (connection refused)
64. [S] web search, Pappalardo Wyscout set (non-commercial label on the d4science catalogue)
65. [S] web search, DFL IDSSE dataset (figshare 10.6084/m9.figshare.28196177)
66. [O] https://github.com/dcaribou/transfermarkt-datasets
67. [O] https://github.com/SkillCorner/opendata
68. [O] https://github.com/statsbomb/open-data
69. [O] https://github.com/sportsdataverse/cfbfastR-data
70. [O] https://github.com/sportsdataverse/wehoop-data
71. [O] https://github.com/sportsdataverse/hoopR-data
72. [O] https://github.com/sportsdataverse/sportsdataverse-data
73. [O] https://cran.r-universe.dev/golfastr/doc/manual.html
74. [S] web search, golf ShotLink open data
75. [S] web search, rugby open event data
76. [S] web search, UFC and boxing open data
77. [S] web search, Olympics open data
78. [F] https://www.olympedia.org/statistics/olympedia (404)
79. [O] https://zenodo.org/api/records/3531797 (irrelevant student notebook; no Olympic event data)
