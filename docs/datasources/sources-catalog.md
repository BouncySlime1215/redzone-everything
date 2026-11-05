# Data sources catalog (Noggin world model)

What we can legally collect for the Noggin, family by family. Each source got a researcher's
proposal and then a separate license review. The numbers here are the reviewer's **realistic**
counts (values = rows x populated fields, nulls and duplicate feeds removed), not the proposals.

- **Green** = free to use; at most a credit line.
- **Yellow** = usable with conditions (attribution, a key Nick adds himself, private storage, a
  rule tab Nick accepts, or a risk Nick signs off). The condition is in the reason column.
- **Red** = not using. Those sources sit in [Not using, and why](#not-using-and-why) at the bottom.

Rules that apply to every row: $0 only, never touch keys (Nick adds them as Kaggle secrets), never
scrape a site whose terms forbid it, collect on Kaggle CPU (never the Mac), keep raw data in the
**private** Kaggle dataset, and never commit raw rows to this public repo.

**Betting data is a hidden check only** (overseer, Nov 4 2026). That covers closing lines, CFBD `/lines`, SBRO and Polymarket. Use it only to calibrate and evaluate our WP, crunch and stakes heads. It is never a model feature, a training label or a weight, and it is never shown on the TV or phone (playbook §5: no betting hooks).

"Counted" = the new, de-duplicated values this source adds to the running total in
[ingest-plan.md](ingest-plan.md). It is lower than "realistic" when another source already holds
the same events.

M = million values, B = billion values.

## 1. NFL and CFB play-by-play and deep stats

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| nflverse play-by-play (nflfastR) | NFL | 1999-2025, wall clock 2011+ | 450M (450M) | GitHub release parquet, `nflreadpy.load_pbp()` | Yellow: CC-BY-4.0, credit "nflverse"; facts come from NFL feeds, so keep raw rows private | $0 | Main NFL token stream; `time_of_day` gaps label real dead-ball and break lengths |
| nflverse participation (NGS 2016-22, FTN 2023+) | NFL | 2016-2025 | 10M (10M) | `load_participation()` | Yellow: CC-BY-4.0; FTN seasons CC-BY-SA 4.0 ("FTN Data via nflverse"); NGS part private | $0 | Personnel and formation tokens; hurry-up personnel means short dead balls |
| FTN charting | NFL | 2022-2025 (verify 2022) | 4.5M (4.5M) | `load_ftn_charting()` | Yellow: CC-BY-SA 4.0; share-alike only if we publish a derived dataset (log in decisions.md first) | $0 | `is_no_huddle` is a direct tempo label for the time-to-next-snap head |
| nflverse Next Gen Stats aggregates | NFL | 2016-2025 | 0.7M (0.7M) | `load_nextgen_stats()` | Yellow: weakest nflverse table legally (NFL never licensed it); private, attributed; optional | $0 | Big-play offense priors |
| nflverse schedules, officials, injuries, rosters, depth charts | NFL | 1999+ (officials 2015+, injuries 2009+) | 15M (15M) | `load_schedules()`, `load_officials()`, `load_injuries()`, `load_depth_charts()` | Yellow: CC-BY-4.0; pull schedules from nflverse-data (Lee Sharpe's nfldata repo has no license file); lines and weather fields are pregame only (leakage) | $0 | Slate builder, coach and referee IDs, closeness prior, crew flag rate = expected dead balls |
| NFL Big Data Bowl tracking (2019-2026) | NFL | 2017-2023 seasons (varies by year) | 2.0B (2.0B) | Join each competition, attach in a Kaggle notebook; data never leaves Kaggle | Yellow: competition rules (non-commercial research); Nick reads and accepts each year's Rules tab; older years may not allow late joins | $0 | Pre-snap events (`huddle_break_offense`, `line_set`, motion; 2025 release only) train a snap-imminent detector |
| NFL Punt Analytics (2018) NGS tracking | NFL | 2016-2017 | 550M (550M) | Kaggle competition attach | Yellow, may turn red: if the Rules tab says "competition use only", drop it. Only ~6.7k punts, so low diversity | $0 | Punt and return timing, so we never cut in during a live return |
| CollegeFootballData.com API (plays, drives, games, lines, media, venues, coaches) | CFB | 2001-2026 (WP and PPA ~2014+) | 150M (150M) | REST v2, free key Nick registers; Academic tier 3,000 calls/month with unc.edu (Free 1,000). Plays by year+week is ~430 calls | Yellow: terms allow model training and caching; ban redistributing raw data, so private only; quota shared with CBBD; paid tiers ($1-$30/mo) and live plays (Tier 2) are off | $0 | CFB tokens, `/games/media` network per game (break cadence), lines, venues (lat/lon, elevation) |
| cfbfastR-data (CFBD-based release only) | CFB | 2002-2025 | 700M (0, overlaps CFBD) | GitHub release parquet | Yellow: repo has no license; use only as a CFBD quota saver, private; the ESPN-processed release is red | $0 | Bulk CFB history without burning API calls |
| nfl4th decision values | NFL | 2014+ precomputed; 1999+ by recompute | 2.3M (2.3M, derived) | R package, run on Kaggle over nflverse pbp | Green: MIT; outputs are ours. Never use `get_4th_plays()` (it pulls ESPN live) for history | $0 | `go_boost` labels coach aggression and decision suspense |

## 2. NBA and men's college basketball

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| shufinskiy/nba_data (stats pbp, pbpstats possessions, data.nba x/y, cdn wall clock, shots, matchups) | NBA (+WNBA) | 1996-97 to 2024-25 | 1.1B (1.1B; ~600M unique events) | Bulk tar.xz from GitHub or the Kaggle mirror; never touches stats.nba.com | Yellow: repo Apache-2.0 covers packaging only; NBA.com ToS §9 = private, non-commercial, credit NBA.com, no queryable database. Confirm which seasons the cdn feed covers | $0 | Core NBA corpus; possession start types; cdn `timeActual` gives real dead-ball seconds |
| nbadb (wyattowalsh) Kaggle "basketball" | NBA | 1946-present (pbp ~1996+) | 550M (200M new: officials, arenas, tracking aggregates) | Attach the Kaggle dataset in a notebook | Yellow: Nick reads the dataset's license field before attaching; NBA.com ToS §9 | $0 | Referee crews, arenas, pace aggregates |
| NBA SportVU tracking 2015-16 (linouk23) | NBA | Oct 2015 to Jan 2016, ~631 games | 3.0B (3.0B) | 7z JSON from GitHub, unzip with py7zr on Kaggle | Yellow, Nick sign-off: repo has no license; NBA/STATS content; private training only, never re-upload | $0 | Dead-ball vs live-ball detector from motion, distilled to pbp-only "safe to cut" labels. Huge count, one half-season |
| NBA Last Two Minute reports (atlhawksfanatic/L2M) | NBA | 2014-15 to 2024-25, 4,479 games | 3.5M (3.5M) | Bulk CSV from GitHub | Green: MIT repo over public NBA reports; credit both | $0 | League-picked crunch-time games; replay reviews = long safe stoppages |
| NBA official injury report archive (sportsdataverse-data) | NBA | 2018-12 to present | 12M (12M) | Release assets (parquet) | Yellow: release license unverified; public NBA documents under ToS §9; never poll the PDFs ourselves | $0 | Leakage-free star-out prior for slate ranking |
| cbbdata (Barttorvik T-Rank archive) | CBB | 2008-present | 90M (90M) | R package with a free key Nick registers | Yellow: MIT package, no stated data terms; cached polite pulls, private; KenPom endpoints excluded (paid) | $0 | Day-by-day pregame margin and tempo, leakage-free |
| Kaggle March Machine Learning Mania (2026; 2020 for MEvents) | CBB men and women | 1985-2026 (events 2015-19) | 270M (270M) | Kaggle competition attach | Yellow: Nick accepts each competition's rules and reads the data-use clause; never redistribute | $0 | Massey ordinals (100+ rating systems) disagreement = upset prior; coaches table; third CBB event feed |
| cdn.nba.com liveData (forward collection) | NBA | 2026-27 on (history is in shufinskiy) | 30M per season (30M) | One polite fetch per finished game (~10 a night) | Yellow: NBA.com ToS §1 and §9 personal non-commercial; check robots.txt first; data only | $0 | Fresh nights for the locked set and forward CV; exact break lengths |
| hoopR NBA pbp (ESPN feed, sportsdataverse releases) | NBA | 2002-2026 | 850M (850M, ESPN-gated) | Pre-built GitHub release parquet only; never call ESPN | Yellow, default OFF until Nick decides: Disney ToU bars AI training and automated extraction; a third-party release gives no license. Two reviewers in other families rated ESPN-derived tables red | $0 | Second NBA feed, cross-check |
| hoopR men's college basketball pbp (`load_mbb_pbp`) | CBB | 2006-2026 | 1.4B (1.4B, ESPN-gated) | Pre-built release parquet only | Yellow, default OFF until Nick decides (same ESPN/Disney issue). Biggest CBB corpus, covers UNC every season | $0 | CBB media-timeout grammar; UNC up-weighting |
| wehoop (WNBA + women's college) | WNBA, WBB | 2002+/2005+ | 1.1B (0, optional) | Release parquet | Yellow, ESPN-gated and out of scope; drop first | $0 | Extra basketball grammar for pretraining only |
| ncaahoopR_data (Luke Benz) | CBB | ~2015-2025 useful | 400M (0, duplicates hoopR MBB) | Shallow clone | Yellow, ESPN-gated: no license; only the spread-aware WP column is new | $0 | CBB drama label (optional) |

## 3. NHL and MLB

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| Retrosheet event files + game logs + ejections/umpires/parks | MLB | pbp 1910s-2025 (pitch sequences reliable ~1988+); game logs 1871-2025 | 1.53B (1.53B) | Bulk zips, expanded with Chadwick tools on Kaggle | Green: any use including commercial; the exact Retrosheet notice must appear in the README, dataset card and model card | $0 | Baseball token stream, our own WE/leverage tables, game-duration and HP-umpire labels; best seed for synthetic games |
| Chadwick Bureau register | MLB | All time | 15M (15M) | GitHub CSV | Green: ODC-BY 1.0, credit Chadwick Bureau | $0 | One player ID across Retrosheet, Statcast and Lahman |
| MoneyPuck shots + game-by-game tables | NHL | 2007-08 to 2026-27 | 900M (900M) | Only the listed CSV/zip files on moneypuck.com/data.htm | Yellow: free for non-commercial use, credit MoneyPuck.com clearly; anything off the page needs approval | $0 | NHL's legal training history: xG, rush/rebound, empty-net flags, time since last event |
| Baseball Savant Statcast (pybaseball), scoped | MLB | 2021-2025 scoped (2008+ only with MLBAM OK) | 400M scoped (400M); 1.1B full | `pybaseball.statcast()` one day per call, with sleeps, on Kaggle | Yellow at the red edge: MLBAM notice allows only "individual, non-commercial, non-bulk use". Scoped pull, private; Nick emails MLBAM before any full backfill | $0 | Per-pitch WE/RE deltas = leverage label; velocity fade predicts pitching changes |
| Stathletes Big Data Cup | Women's pro, OHL | 2021-2025 | 3M (3M) | GitHub CSV | Yellow: non-profit research only, credit Stathletes, read each year's legal.md | $0 | Pass and zone-entry sequences the NHL never publishes |
| Lahman Baseball Database | MLB | 1871-2025 | 10M (10M) | Bulk CSV | Yellow: CC BY-SA 3.0; share-alike only on a published derivative; leave out the Seamheads Negro Leagues tables | $0 | Manager tenure and team-strength priors |

## 4. Betting markets (signal only, never wagering)

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| Polymarket documented APIs (+ Polygon on-chain fills) | All six | 2022-present (dense 2024+) | 250M (250M) | gamma-api, clob `/prices-history`, data-api `/trades`; no key; back off on limits | Yellow, Nick sign-off: §4.2 data bar targets capital-market clients, not individuals; never scrape the HTML site; read-only (US persons cannot trade, no VPN); hash or drop wallet addresses; log in decisions.md | $0 | Hidden calibration/eval check only (overseer, Nov 4): per-minute in-game probability vs our WP/crunch heads. Never a feature or training label, and nothing betting-related is ever shown |
| CFBD `/lines` | CFB | 2013-present | 0.65M (in CFBD row) | ~13 calls total | Yellow: same CFBD terms | $0 | Opening vs closing spread, total = pace |
| Kaggle NFL Scores and Betting (tobycrabtree) | NFL | 1966-present | 0.11M new (1966-98) (0.11M) | Kaggle dataset, stays in Kaggle | Yellow: Nick checks the license field; use only if CC0, CC-BY or ODbL; skip the HF mirror | $0 | Older NFL closeness priors |
| Sportsbook Reviews Online archive (Wayback only) | NFL, NBA, NHL, MLB (+CFB/CBB via Wayback) | ~2007-2022 | 5M (5M) | Archived .xlsx through the Wayback CDX, slow; never run the FinnedAI scraper | Yellow, Nick approves and logs it: no license, odds are weakly copyrightable facts | $0 | Hidden calibration/eval check only: closing lines vs our WP (only free NHL/MLB line history) |
| The Odds API free tier (forward only) | All six | Forward | 3M per year (3M) | Free key Nick adds; ~1 snapshot a day fits the credit cap | Yellow: terms allow storage and ML training; no resale; historical is paid, so never | $0 | Morning slate prior (closeness, total) |

## 5. Coaching and decision behavior

Most coach decisions live in sources already listed (nflverse pbp, nfl4th, CFBD, MoneyPuck,
Retrosheet substitutions). New here:

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| Wikidata head-coach tenures (P286 + P580/P582) | All six | Modern era, patchy for college | 0.06M (0.06M) | WDQS SPARQL (60 s limit, User-Agent) or the dump | Green: CC0 | $0 | Coach ID for every game in every league; interim-coach flag |
| Retrosheet `sub` records + Lahman Managers | MLB | 1897-2025 | in Retrosheet/Lahman rows | as above | Green / Yellow as above | $0 | PITCHING_CHANGE token with manager ID (2-3 minute dead ball) |
| MLB Stats API live feed (live nights only) | MLB | Tonight's games | 0 training | Poll only games on Nick's slate | Yellow for live viewing only. The live-night auto-log (#118) must not archive MLB payloads into a corpus; log only our own decisions and timestamps | $0 | Pitching change or review just started = safe switch away |
| Football Zebras replay tables | NFL | ~2014-2020 | 1.3k (0, skip) | Hand transcription only | Yellow but skip: nflverse `replay_or_challenge` covers it | $0 | none |

## 6. Narratives, news and hype (structured counts only, never text)

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| Wikimedia pageviews (pageview_complete daily + hourly for game nights) | All six | 2007-12+ (clean 2015-07+) | 3.0B (3.0B) | Stream-filter the dumps on Kaggle; REST API for single articles; User-Agent; few parallel downloads | Green: CC0 (dumps legal page). Pre-2015 is legacy pagecounts (desktop, bots): separate, noisier feature | $0 | Pregame hype prior; next-day surge = hindsight "should have watched" label |
| Wikipedia revision metadata (stub-meta-history) + EventStreams | All six | 2001+; live | 200M (200M) | Stream-parse stub dumps (no text); one SSE client | Green: metadata only; drop usernames and IPs | $0 | Edit velocity = minute-level "something big just happened" |
| Wikipedia Clickstream | All six | 2017-11+ monthly | 500M (500M) | Monthly TSV, filtered | Green: CC0 | $0 | Narrative graph; casual-fan interest |
| Wikidata JSON dump (sports subgraph) | All six | Snapshot | 10M (10M) | Stream-filter `latest-all.json.bz2` (~100+ GB); never land it in the dataset | Green: CC0 | $0 | Rivalries, records held, geography, the ID crosswalk |
| Wikipedia season/broadcast/ratings tables | All six | ~1970+ (NFL), patchy elsewhere | 1.5M (1.5M) | Enterprise HTML or wikitext dumps, parsed on Kaggle | Yellow: CC BY-SA 4.0; a published table must be CC BY-SA and credit Wikipedia; facts only | $0 | National window, primetime slot, flex history, viewership |
| GDELT 2.0 GKG + Events/Mentions | All six | 2015-02+ | 500M (500M) | Stream raw 15-min CSVs on Kaggle (safer) or BigQuery sandbox with partition pruning; never enable billing | Yellow: free and unrestricted, cite the GDELT Project; common-name matching is noisy | $0 | Pregame story volume and tone; coach hot-seat index |
| GDELT TV 2.0 API | All six | 2009+ | 20M (20M) | Throttled REST | Yellow: cite GDELT and Internet Archive; flaky; low priority | $0 | Mainstream-crossover hype |
| Bluesky Jetstream (counts only) | All six | Forward | 10M per season (10M) | Public WebSocket during game windows | Yellow: counts only, no text, handles or DIDs; honor AI-consent flags when they ship | $0 | Live crowd heart rate |
| YouTube Data API highlight views | All six | Current | 0.3M (0.3M) | Free key Nick adds | Yellow: Developer Policies bar derived metrics and keeping data past 30 days; live short-window use only unless Google confirms in writing | $0 | Hindsight label only if confirmed |
| Google Trends official API (alpha) | All six | Rolling ~5 yrs | 0 until approved | Nick applies; manual CSV exports in small volume are fine | Yellow: never pytrends or scraping | $0 during alpha | DC and NC regional interest |
| Public viewership reports (Nielsen, league/network PR) | All six | ~2000+ | 0.1M (0.1M) | Hand entry with source URL | Yellow: facts only, no bulk scraping | $0 | Calibration target for the hype head |
| Records and milestone chases (derived) | MLB, NFL (+Wikidata) | 1871+ / 1999+ | 15M (15M, derived) | Computed from Lahman, Retrosheet, nflverse | Yellow: inherits CC BY-SA (Lahman) and the Retrosheet notice; no NHL API | $0 | Record-watch trigger (no-hitter alive, 1 HR from 500) |

## 7. Context and environment

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| NOAA GHCNh hourly stations (AWS) | All (outdoor focus) | 1973+ dense | 1.2B (1.2B) | `s3://noaa-ghcnh-pds`, no sign-in; ~300 distinct stations | Green: CC0 / public domain (a few Canadian stations carry WMO Res. 40 commercial limits; irrelevant here) | $0 | Present-weather codes (thunder, snow, fog) predict delays and sloppy games |
| NOAA GOES GLM lightning (AWS) | NFL, CFB, MLB outdoor | 2017+ | 30M (30M) | `noaa-goes16/18/19` buckets (GOES-19 is East since 2025); pull game windows only | Green: public domain | $0 | 8-mile lightning rule = predictable 30+ minute delays |
| FiveThirtyEight data repo (Elo, forecasts) | NFL, NBA, MLB, NHL | to June 2023 | 9M (9M) | GitHub CSV; mirror to the private Kaggle dataset now | Green: CC BY 4.0 datasets, MIT code; check each file's last date | $0 | Pregame strength prior; upset-in-progress trigger |
| Open-Meteo historical (ERA5) | All | 1999+ | 1.0B (1.0B) | ~350 deduped grid cells x 12 variables = ~300k weighted calls, under 10k/day over ~1 month. Or ARCO-ERA5 on Google public data (no call quota) | Yellow: CC BY 4.0, free tier non-commercial; calls are weighted (long ranges and >10 variables cost more) | $0 | Hourly in-game weather for every venue |
| Travel, rest, timezone, altitude (GeoNames + USGS EPQS, derived) | All | 1999+ | 8M (8M, derived) | ~1,500 elevation calls, then computation | Yellow: GeoNames CC BY 4.0; build only from green/yellow schedules | $0 | Body-clock hour, back-to-backs, altitude fade |
| Point-in-time standings and playoff odds (our Monte Carlo, derived) | All | 2000+ | 14M (14M, derived) | Vectorised sims on Kaggle; store summaries only | Yellow: inputs must be green/yellow; Elo uses only prior games (CI leakage check) | $0 | Stakes/leverage term for the planner |
| OpenStreetMap stadium geometry | NFL, CFB, MLB | Current | 0.01M (0.01M) | Geofabrik extract + osmium, never hammer Overpass | Yellow: ODbL, "(c) OpenStreetMap contributors" | $0 | Field bearing x wind for kicks |
| Solar position (pvlib / astral) | Outdoor | Any | 115M (0, compute on the fly) | Computed | Green: BSD-3 / Apache-2.0 | $0 | Twilight and glare priors; not stored |

## 8. Broadcast, viewing and synthetic

| Source | Leagues | Years | Realistic (counted) | Access | Verdict + reason | Cost | Noggin use |
|---|---|---|---|---|---|---|---|
| League media-timeout rule tables (hand-coded) | All six | By rule era | 0.009M (0.009M) | Read rulebooks, paraphrase into YAML in the repo | Green: rules are facts; do not copy PDF text | $0 | Hard priors: CBB under-16/12/8/4, NBA after 7:00 and 3:00, NHL after 14/10/6, MLB inning breaks and pitch clock |
| Win probability and Game Excitement Index (derived) | All six | 2002+ | 200M (200M, derived) | Run open packages over the pbp we hold; MLB WP from our own Retrosheet run-expectancy model | Green: outputs are ours (never use the MLB winProbability endpoint) | $0 | Hindsight oracle reward; running GEI as a world-model target |
| Our live-night logs (TraceEvents, auto-log #118) | Whatever is live | 2026+ | 20M per season (20M) | Nightly upload to the private Kaggle dataset | Green: ours; redact tokens and device IDs | $0 | Gold labels: real latency, mid-play entries (weight 10-50x). Nick's overrides: evaluation only, never a label (self-taught rule) |
| SYNTHETIC A: slate re-mixer + play re-timer | All | n/a | 1.0B stored cache (synthetic) | Our simulator on Kaggle CPU | Green: seed only from green/yellow sources | $0 | Planner self-play; collision-heavy slates |
| SYNTHETIC B: WP counterfactual rollouts | All | n/a | 1.0B stored cache (synthetic) | Our code | Green: same seed rule; never enters eval or the locked set | $0 | Rare states: comebacks, OT, walk-offs, coach-policy branches |

---

## Not using, and why

| Source | Family | Why not | What replaces it |
|---|---|---|---|
| ESPN site/core API, direct pulls (summaries, wallclock, WP, pickcenter, broadcasts) | NFL/CFB, NBA/CBB, betting, narratives, broadcast | Disney ToU §2.B bars robots/scripts "for the purposes of creating or developing any AI Tool, data mining or web scraping"; §2.A excludes AI training. A slow rate does not fix it. The live app's existing personal use is a separate question for Nick | nflverse `time_of_day` and `wp`; CFBD wallclock and media; our own WP models |
| sportsdataverse ESPN game-info tables (officials, attendance, broadcasts) | Context | Same Disney issue; a GitHub release grants no license | CBBD (same CFBD key) for CBB games and venues; network from Nick's own stick logs |
| nflverse snap counts and PFR advanced tables | NFL | Sports Reference terms (§5 item 11) forbid AI/ML training and automated access | nflverse participation and FTN |
| NHL.com APIs in bulk (api-web pbp, shift charts, HTML reports, EDGE, standings, fastRhockey NHL rows) | NHL, coaching, context | NHL.com ToS bans "unauthorized spidering, scraping, or harvesting" and entering content into a database. Reviewers split (two yellow, two red); we take the cautious verdict. Nick may accept the risk later in decisions.md | MoneyPuck for history; standings from final scores; live polling of tonight's games only |
| MLB Stats API (GUMBO, winProbability) in bulk | MLB, broadcast, context | MLBAM notice: "individual, non-commercial, non-bulk use" only; a 44k-game pull is bulk | Retrosheet history; live polling of tonight's slate only, never archived |
| Natural Stat Trick | NHL | No license, 403 on automated fetch, some features paywalled | Rebuild the metrics from MoneyPuck x/y |
| KenPom | CBB | Paid ($95/yr) | cbbdata Torvik archive |
| Kalshi market data | Betting | Developer Agreement §3: API use only for a member's own trading; §3.1 bans storing data otherwise; needs a KYC account | Polymarket documented APIs |
| ESPN odds/predictor/WP feeds | Betting | Disney ToU as above; sportsdataverse mirrors carry no license | nflverse `vegas_wp`; our own WP |
| Sports Odds History | Betting | Now owned by Covers.com, which forbids scraping | Our own playoff-odds sims |
| Betfair Historical Data | Betting | Needs a Betfair account; not available to US residents | Polymarket |
| Sportsbooks and line-movement sites (DraftKings, FanDuel, Covers, Action Network, VSiN), Sports-Reference Vegas columns | Betting | Terms forbid scraping; paid Apify actors change nothing | CFBD lines, nflverse lines, The Odds API |
| Reddit Data API (game-thread velocity), Pushshift / Arctic Shift dumps | Narratives, broadcast | Terms bar ML training on user content without permission; new apps need approval; dumps are unlicensed | Bluesky counts, Wikipedia edit velocity |
| Google Trends via pytrends or scraping | Narratives | Against Google ToS | Official alpha only, if approved |
| X/Twitter API | Narratives | Paid | Bluesky |
| Nielsen paid feeds, Sports Media Watch bulk scraping | Narratives | Paid / terms | Hand-entered public press releases |
| NBA referee crews for NBA/NHL from ESPN tables | Context | ESPN-derived | Drop the feature (nbadb officials cover NBA) |
