---
id: D-CATALOG
title: Catalog walk - Kaggle, Hugging Face, Zenodo, Harvard Dataverse, Figshare, AWS Open Data, BigQuery searched by query for new 1M+ sports and viewing data
date: 2026-11-09
stream: data
author: data catalog-walk agent (Claude Sonnet 5.5), job 11
status: draft
supersedes: []
sources: 160
verified: 148
affects: []
gate: []
---

# D-CATALOG: walking the catalogs instead of relying on known names

Nothing downloaded. Nothing signed up. No repo secrets read. Web text treated as data. Source and verified counts are a hand tally (about plus or minus 5): "verified" = a page or API response was fetched and read. Row counts are only the ones the owner's page or the Hugging Face datasets-server states; sizes in bytes are never turned into rows. "Training allowed" is judged from the original owner's terms; a re-uploader's licence tag does not count; unclear counts as no.

Read first: D-SPORTS-GAPS (44.5 M baseline), D-FIELDS-A, D-VIEWERS, D-CONFIRM.

## 0. Bottom line

1. **New real, unique, licence-clear sports data: about 1.54 M points.** One set: a field-hockey wearable (1,543,960 IMU samples, CC BY 4.0). Weak link to game moments.
2. **One conditional upgrade: cricket 5.85 M -> 11.6 M balls (+5.76 M).** A Hugging Face parquet has 11,615,100 deliveries from 22,983 matches (Cricsheet lists 23,241). Cricsheet's own licence is ODC-By (opened). The card names no source, so "this is Cricsheet" is my inference. One check closes it (section 3). If it holds, the licence-checked total moves from 44.5 M to about 51.8 M and passes 50 M for the first time.
3. **Big finds that stay blocked (unclear = no): about 44.5 M.** Football odds movements 37.0 M price updates (MIT tag, scraped from unnamed odds-comparison sites), CBA basketball odds 3.04 M (same collector), League of Legends timelines 3.2 M (Riot API terms unread), game-time tweets 1.26 M (Twitter terms).
4. **New direct sports-viewing data: 0.** Every viewing-adjacent set I found is under 1 M rows, unreleased, or has no stated count. The one live-TV-viewer set (Data in Brief, 11 viewers, 62,380 observations) is far under 1 M.
5. **Catalog walking found little that known-name searches had not.** Most Kaggle and Hugging Face sports sets are re-uploads of the same NBA, NFL, Retrosheet, Cricsheet, StatsBomb, SkillCorner and Statcast feeds. They are counted once or not at all (section 4).

## 1. What was walked

| Catalog | How | Pages / queries | Result |
|---|---|---|---|
| Kaggle | `/api/v1/datasets/list?search=` (20 per page, by votes or recency) plus `/datasets/view/` for detail | 54 list pages (54 queries, 6 of them page 2 or recency re-sorts) + 21 detail views | Works. Gives bytes and owner licence; row counts only when the owner typed them. No sort-by-size exists, so I sorted by hand and filtered by bytes. |
| Hugging Face | `/api/datasets?search=&filter=size_categories:` and `datasets-server.../size` for exact rows, plus 8 dataset cards | 26 search pages + 11 size lookups + 8 cards | Works. Size tag is a bucket; exact rows need datasets-server (one returned HTTP 500). |
| Zenodo | `/api/records?q=` | 9 queries | Sports hits are tiny (largest sports IMU set 100 MB, 24 players). Relevance ranking is poor. |
| Harvard Dataverse | `/api/search?q=` plus 3 record views | 7 queries (total hits 117,939 / 8,498 / 5,753 / 5,3xx / 163 / 6,618 / 2,843) | Two relevant records (tweets, Bundesliga tracking); one trap (section 4). |
| Figshare | API GET search returned recency junk (search is POST) plus a web search | 2 | Nothing new. Only the already-listed Wyscout set. Not conclusive. |
| AWS Open Data | registry search page (first ~55 of the list read) plus tag page (404) plus web search | 3 | No sports dataset seen. Not conclusive (about 90% of the list unread). |
| BigQuery public | marketplace page failed (parse error); 2 web searches and a Google blog read | 3 | Only `ncaa_basketball.mbb_pbp_sr` (Sportradar). Row count conflicts: "over 4 million" vs "more than 42 million plays". Unconfirmed, terms unread. |
| Direct viewing searches | 2 extended web searches plus Kaggle/HF/Zenodo/Dataverse viewing queries | counted above | Section 5. |

Totals: **about 103 catalog list or search pages across about 95 distinct queries**, plus about 50 detail pages. Queries covered: play-by-play by league (NFL, NBA, NHL, MLB, CFB, NCAA, WNBA, soccer, cricket, tennis), tracking, event data, odds and line movement, in-play odds, broadcast, viewership and ratings, highlights, commentary, officiating, injuries, F1, golf, esports (Dota, LoL, CS), horse racing, marathon, MMA, rugby, volleyball, handball, cycling, fantasy, Twitch, set-top box, tweets, Reddit.

## 2. Candidates of 1 M or more not already listed

Unit is stated on every row. "Direct" = how close to a sports moment or to viewing.

| Dataset | Count (unit, who states it) | Owner licence / source terms | Training | $0 fetch | Direct | Verdict |
|---|---|---|---|---|---|---|
| Kaggle `azmanshahar/field-hockey-activity-recognition-fhar-dataset` | 1,543,960 IMU samples at 128 Hz, 33 players (owner page) | CC BY 4.0; page cites the IEEE Access 2024 and Sensors 2025 papers; I did not confirm the uploader is the paper author | yes | yes (110 MB) | weak: one athlete's motion, no game state | **Counts: +1.54 M** |
| HF `Sarthak213/doosra-cricket` (also `lohith-ashwa/cricket_analytics_dataset`, 11,725,157 rows, Apache-2.0 tag; same data, count once) | 11,615,100 deliveries in 22,983 matches (datasets-server) | card: odc-by, no source named. Cricsheet match data is ODC-By (opened); XML is CC BY 4.0. No AI clause | yes if Cricsheet-derived (inferred) | yes (parquet, 31 MB deliveries) | medium: every ball, over and wicket | **Conditional: +5.76 M over the listed 5.85 M**, replacing it, not adding |
| Kaggle `archangel0x01/football-odds-movements-2018-2026` | about 37.0 M price updates (11.6 M 1X2, 15.6 M handicap, 9.8 M totals), 63,597 matches, 4 bookmakers, minute stamps (owner page) | MIT tag. Data "gathered in October 2026 from public odds comparison sites", sites not named, so their terms are unknown. Cross-checked against football-data.co.uk, whose own terms bar AI training (D-FIELDS-A) | unclear = no | yes (2.98 GB; CSV and parquet) | indirect: pre-match market view of who is favoured; no in-play | Blocked. Best unlock: ask the owner which sites |
| Kaggle `archangel0x01/china-cba-basketball-odds-and-box-scores` | 3,035,612 price updates; 4,863 games; 98,202 player box rows | MIT tag; scraped, sites not named | unclear = no | yes (271 MB) | indirect | Blocked, same collector |
| HF `jjerlee/nhl-analytics` | "about 5.4 M play-by-play events", 17,000+ games, 2013 on (card prose; per-subset counts not shown) | MIT tag; source is the NHL API. NHL terms of service (opened): "private, non-commercial" use only; bar "unauthorized spidering, scraping"; bar entering into a database | unclear = no | yes (135 MB) | direct (events with x/y) | Blocked. Same NHL feed as NHL_DB (53.6 M, already listed): count once |
| Kaggle `nathansmallcalder/league-of-legends-ranked-post-match-and-timeline` | about 3.2 M per-minute timeline rows, 77,135 matches | MIT tag; Riot API collected; Riot terms not read | unclear = no | yes (144 MB) | off-target (esports) | Blocked |
| Harvard Dataverse `doi:10.7910/DVN/29697` | 528,501 tweets (19 NPB games) + 730,142 tweets (17 Yankees games) = 1,258,643 | CC0 by the authors; tweets collected via Twitter search API; Twitter terms not read; tweet text is third-party | unclear = no | yes (no access request) | indirect: audience reaction during games, 2013 and 2015 | Blocked; the closest "audience at the game" set |
| Kaggle `kaitoland/lichess-broadcast-chess-clock-eval` | 101,228,895 half-moves, 1,263,911 games (owner page) | CC BY-SA 4.0; source Lichess broadcast database, stated CC BY-SA 4.0 with credit to Lichess and the broadcast organisers | yes (share-alike) | yes (1.6 GB) | not a league sport; no dead balls; but live broadcast clock times | Clean, large, **off-scope**. Not added to the sports total |

## 3. One check that moves the total

Cricket mirror provenance (about 5 minutes, no download of the full set): open the first 20 rows of `matches` from Sarthak213/doosra-cricket via datasets-server `/first-rows`, and compare the match ids and dates with Cricsheet's match list (23,241 matches; the mirror has 22,983 plus 2,930 "missing_matches"). If they match, cricket is 11,615,100 balls, ODC-By, with attribution. If the ids are Cricinfo ids, treat as unclear and keep the 5.85 M figure.

## 4. Found, checked and not counted

Duplicates (same underlying feed, count once, already in the baseline):

- **NBA:** `chevronronson/nba-stats-dataset` (18M+ PBP, CC0 tag), `deocheng`, `szymonjwiak`, `wyattowalsh`, `eoinamoore`, `brains14482`; HF `cdechoch`, `epinnock/polymarket-nba`. All one NBA feed; owner terms are NBA "private, non-commercial" (D-SPORTS-GAPS).
- **MLB:** `bdilday/retrosheet-events-1970-2015`, `jraddick/baseball-events-from-retrosheetorg` (1.19 GB), HF `gurleen/baseball` (5,074,566 retrosheet_plays rows). All Retrosheet; the 16.76 M figure stands. Statcast copies (`s903124`, `pschale`, `yasunorim`, Dataverse `UKPKVL` 2015-2024) stay blocked by MLB's non-bulk wording.
- **NFL:** HF `CeeWill75/FootballWeather` (about 2.39 M rows, CC0, no source named; nflverse plays joined to weather, so duplicate rows by construction), `dismalnow` and `MikeySports` nfl-stats-2021-2025 (1M-10M, CC BY 4.0 tag, player-week stats), `benbaldwin`, `toddsteussie`, `maxhorowitz`.
- **Soccer:** `eatpizzanot/soccer-dataset` plus the copies `svgwvgs`, `Oscar343`, `Sporoseseun` (same 17.05 M rows, all rejected as built from football-data.co.uk, whose terms bar AI training); StatsBomb copies `saurabhshahane` (16.1 GB), `hardikagarwal1`, `adityendrapba2021`, `riyagarg0314` (StatsBomb Public Data User Agreement, text not readable here); Wyscout copy `aleespinosa`.
- **Cricket:** `veeralakrishna`, `ariadaikalam` (Cricsheet).

Not countable, with the reason:

- **Dataverse "Line Movement Dataset (11.7 M odds snapshots)"**, `doi:10.7910/DVN/AONGK9`: the title is the collector's volume, but the three files total 3,806 bytes of aggregate tables by sport. The 11.7 M rows are not published. Rejected as padding.
- **HF `lwy93/nfl-big-data-2027`:** 24,063,859 rows but the columns are NFL Combine measurements (18 columns, no tracking), no card, no licence. Rejected; the row count is a join artefact, not 24 M events.
- **HF `theoddsgap/us-sports-betting-odds-sample-week`:** 2,553,894 rows (2,267,666 `game_lines`), CC BY-NC 4.0 tag, odds sources not named, one week of data. Unclear = no.
- **HF `SoccerNet/SN-echoes`:** 4,387,121 rows across three Whisper versions of the same audio (v3: 1,602,919, of which 923,181 original-language). Machine transcripts of broadcast commentary, CC BY 4.0 tag by SimulaMet. Counted once it is under 1 M, and the audio belongs to SoccerNet's broadcast video (NDA-gated per D-FIELDS-A). Kept as the only large broadcast-commentary text I found.
- **HF `wellness10/PMData_sports_logging_dataset`:** 27,454,445 rows, MIT tag, but 20,991,392 are heart-rate readings from 16 people's wristbands. Health logging, not sport events.
- **HF `epinnock/polymarket-nba`:** 10,052,314 trades, CC BY 4.0 tag; a subset of the Polymarket pool already listed, whose terms bar scraping (D-CONFIRM).
- **HF `MAICHEN/Polymarket-Sports-L5`:** 100 M-1 B by tag, no card, no licence.
- **HF `Homie0609/MatchTime`:** tagged 10M-100M but datasets-server reports 0 rows and the card says 78 MB, 520 games. Tag is wrong.
- **Kaggle `xfkzujqjvx97n/football-datasets`:** "5.7 M records" are Transfermarkt profile, market-value and transfer rows (CC0 tag, scraped). Not events; same source as the Transfermarkt set already listed.
- **Kaggle `hashirhalaldeen/fypdata`:** SkillCorner broadcast tracking, 9 matches, 10 Hz (owner page); frames not stated, licence text absent. About 0.5 M frames by my arithmetic, not counted.
- **Kaggle NFL Big Data Bowl mirrors** (`mathurinache` 475 MB, `ryanadamsai`, `sarthakteli18`, `llkh0a`): tracking, rows not stated, competition rules govern, uploader tags only. No.
- **StatsBomb / Hudl American football open data** (`statsbomb/amf-open-data`, 2016-2022 plays, events, low- and high-frequency tracking, S3 zips): the page gives no counts or Hz, and the "Public Data User Agreement" PDF would not render. Real lead for NFL tracking. Needs a 2-minute read of `LICENSE.pdf` and `games.json` (80 KB).
- **Horse racing** (`deltaromeo` 3.95 GB, `takamotoki` JRA 494 MB, CDLA and CC BY tags): scraped from Racing Post-style and netkeiba sources, no counts. Off-target for the league order.
- **Dota/CS/esports:** `skihikingkevin/csgo-matchmaking-damage` (CC BY-NC-SA, "over 410k rounds", rows not stated); `colabsss/esports-archive` is video metadata.
- **Small but clean:** Zenodo Hang-Time HAR (24 players), PHYAFB (about 350,000 heart-rate samples), Euroleague 21-22 play-by-play (21 MB), `eddieglush/afl-player-match-statistics` (206,722 player-matches, CC BY 4.0), HF `slothman3878/kbo_playbyplay` (CC BY 4.0, 100K-1M), Dataverse Bundesliga 2016-17 tracking (`UCKC6K`, 4.97 GB, CC0 but source and frames not stated; the DFL data is normally proprietary, so provenance is unproven; same record family as the one in D-FIELDS-A).

## 5. Viewing data: where the walk ended

| Item | Count | Verdict |
|---|---|---|
| Data in Brief: TV viewer response to live sport and sponsor messages (World Cup 2018 group match, 11 viewers) | 62,380 observations | Direct for attention vs game minute, but far under 1 M. Open licence (CC) per the paper. Worth a read as a method, not as training volume. |
| arXiv 2603.23773, minute-level livestream audience (18 creator channels) | 2.9 M observations | Not sports; paper page links no data release. |
| IEEE DataPort IPTV channel-switch sequences (300 viewers) | 3.9 MB file | Needs sign-up on another site. Rejected (D-VIEWERS). |
| Kaggle Twitch top-games snapshots Jan-Mar 2026 (`felipefalkiner`) | "millions", not stated | CC BY-NC-SA tag, Twitch API terms unread, 30-minute snapshots of PT-BR streams, not sports. No. |
| Kaggle `xvivancos/tweets-during-cavaliers-vs-warriors` | not stated (394 MB, 45 minutes of #NBAFinals tweets) | CC0 tag; Twitter streaming terms. Unclear = no. |
| Dataverse 29697 tweets (section 2) | 1,258,643 tweets | Blocked. |
| Kaggle small ratings sets (World Series TV ratings 1969-2022, halftime-show viewers) | under 10 KB | Tiny, annual. |

**New viewing total: 0 licence-clear, 0 direct.** The gap in D-VIEWERS stands: no open minute-by-minute audience log tied to game state exists in these catalogs. The two realistic routes remain Nielsen-style panel data (paid) and building the log from Nick's own two sticks.

## 6. Totals

| Line | Points | Note |
|---|---|---|
| Baseline (D-SPORTS-GAPS A + B) | 44,526,643 | NFL 1.24 M, MLB 16.76 M, cricket 5.85 M, NBA 18.6 M (NC), NHL shots 2.08 M |
| + FHAR field hockey IMU | +1,543,960 | clean, weak link |
| **New firm total** | **46,070,603** | |
| + cricket mirror upgrade (replace 5.85 M with 11.6 M) | +5,763,022 | conditional on section 3 |
| **New total if cricket check passes** | **51,833,625** | first time above 50 M |
| Found but blocked (unclear = no) | about 44.5 M | football odds 37.0 M, CBA odds 3.04 M, LoL 3.2 M, tweets 1.26 M; NHL 5.4 M not added (same feed as NHL_DB) |
| New direct sports-viewing | 0 | |

All three figures are real rows counted once. None is synthetic. Chess (101 M moves) is excluded as not a league sport.

## 7. Risks and open items

- Catalog coverage is partial. Kaggle search returns 20 per page and I read one or two pages per query; a long-tail set could sit on page 3. Figshare and AWS were not walked in full (Figshare search is POST-only; AWS list about 90% unread). BigQuery's catalog page would not load.
- Kaggle detail pages carry row counts only when the owner typed them. Many strong leads (BDB mirrors, Hudl AMF, NCAA BigQuery) stay unconfirmed for that reason.
- The cricket upgrade rests on one inference (section 3).
- NBA and NHL stay "private, non-commercial" at the owner level; football-data.co.uk's AI-training bar reaches the odds set through its cross-check, not its collection, so the odds set's real exposure is the unnamed odds-comparison sites.
- The football odds set was collected in October 2026 and its Kaggle page lists no files; the file list could not be verified.

## Next step (under 2 minutes)

Open `https://huggingface.co/datasets/Sarthak213/doosra-cricket/viewer/matches` and compare the first match ids with cricsheet.org. That decides whether cricket counts as 11.6 M.

## Sources

Tags: [O] opened and read, [S] snippet only, [F] fetch failed. Query families on one host are counted by page.

1. [O] Kaggle `datasets/list` x54 queries listed in section 1 (play-by-play recency; nfl; nba pages 1-2; nfl page 2; mlb; ncaa; wnba; hockey and nhl events; baseball pitch; soccer event data; soccer commentary; football tracking; football 2026 events; tennis point-by-point; cfb; cricket; tracking; odds, line movement, in-play; viewership, TV viewership, ratings, tweets, reddit, set-top box, watch-time, Twitch, live stream, highlights, broadcast, injuries, referees, F1, golf, esports, Dota, LoL, CS, UFC, horse racing, marathon, rugby and volleyball, cycling, fantasy, Big Data Bowl, SportVU, statsbomb, sports million rows)
2. [O] Kaggle `datasets/view` x21: archangel0x01 (football odds, CBA), xfkzujqjvx97n, zygmunt/betfair-sports, kaitoland, azmanshahar, heshamelalamy47, stevefza, dhruvirana07 x1, saurabhshahane, hashirhalaldeen, alandu20, skihikingkevin, chevronronson, owensingh38, kapastor, xvivancos, felipefalkiner, deltaromeo, nathansmallcalder, takamotoki
3. [O] Hugging Face `api/datasets` x26 (sports 10M-100M, play-by-play, football, basketball, nhl, hockey, baseball, soccer, tracking, nfl, odds, commentary, cricket, tennis, formula1, livestream, match events, esports, wearable, viewership, sport 100M-1B, match, player, game, stats, nba)
4. [O] datasets-server `/size`: lwy93, theoddsgap, SoccerNet/SN-echoes, PMData, BBuckz, gurleen, Sarthak213, lohith-ashwa, epinnock, Homie0609 [F: jjerlee, HTTP 500]
5. [O] HF cards: lwy93, SN-echoes, theoddsgap, Sarthak213, CeeWill75, jjerlee, epinnock, MatchTime
6. [O] Zenodo `api/records` x9 (sports tracking; play-by-play; football events; basketball/hockey/baseball; broadcast; player tracking; ice hockey; event data soccer; GPS athletes)
7. [O] Harvard Dataverse `api/search` x7 plus dataset records 29697, AONGK9, UCKC6K
8. [O] Figshare API (GET search, junk) ; [S] web search for Figshare sports
9. [O] registry.opendata.aws search page (first 100,000 characters) ; [F] registry tag page (404) ; [S] web search AWS sports
10. [S] web searches: BigQuery sports public datasets; [F] Google Cloud marketplace page (parse error); [O] Google Cloud blog (March Madness pipeline)
11. [O] github.com/statsbomb/amf-open-data (page, raw README, API directory listings) ; [F] LICENSE.pdf (not rendered)
12. [O] cricsheet.org/downloads, /register, /licensing
13. [O] nhl.com/info/terms-of-service ; [F] nhl.com/info/terms-of-use (error page)
14. [S] web searches: live sports streaming audience data; TV viewing logs ; [O] arxiv.org/abs/2603.23773
15. [O] Earlier reports read first: D-SPORTS-GAPS, D-FIELDS-A, D-VIEWERS, D-CONFIRM
