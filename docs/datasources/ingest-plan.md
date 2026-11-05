# Ingest plan: the Kaggle data factory for the Noggin world model

Ranked plan for filling the Noggin's private Kaggle dataset. Source details and license verdicts
are in [sources-catalog.md](sources-catalog.md). Each cycle, planners fill in
[data-ledger.md](data-ledger.md).

Ground rules (same as the catalog): $0, Nick adds every key himself, Kaggle CPU only (never the
Mac), raw data stays in the **private** Kaggle dataset, nothing raw goes into this public repo, and
nothing synthetic goes into eval or the locked test set.

## 1. Running total of realistic data points

"Counted" values are de-duplicated: a second feed of the same events counts only for what it adds.

| Bucket | Green | Yellow | Notes |
|---|---:|---:|---|
| Real, observed | 6.52B | 10.99B | Yellow excludes the ESPN-derived releases |
| Real, derived (WP, GEI, nfl4th, travel, standings, records) | 0.20B | 0.04B | Computed from the rows above; adds no new facts |
| **Real total** | **6.72B** | **11.02B** | **17.7B legal real values** |
| ESPN-derived releases (hoopR NBA + MBB), only if Nick says yes | | +2.25B | Would bring real to ~20.0B |
| Synthetic, stored cache | 2.0B | | Slate re-mixes and WP rollouts; generated on demand beyond this |
| **Stored total** | | | **~19.7B (~22.0B with ESPN)** |

Where the real 17.7B comes from (biggest first): SportVU tracking 3.0B, Wikimedia pageviews 3.0B,
BDB tracking 2.0B, Retrosheet 1.53B, GHCNh weather 1.2B, shufinskiy NBA 1.1B, Open-Meteo 1.0B,
MoneyPuck 0.9B, Punt tracking 0.55B, Clickstream 0.5B, GDELT 0.5B, nflverse pbp 0.45B, Statcast
(scoped) 0.4B, MMLM 0.27B, Polymarket 0.25B, everything else ~1.0B.

### The honest note: values are not information

"Tens of billions" is reachable by the cell count, but cells are a poor measure of what the model
learns from.

1. **Distinct real events are the true budget.** After de-duplication the legal corpus holds about
   **40M play events** (NFL ~1.3M, CFB ~3.5M, NBA ~17M, MLB ~15M plays plus ~4M scoped pitches,
   NHL ~2.2M shots, CBB ~1M from MMLM). With the ESPN releases it is about 70M, almost all of the
   gain in CBB. That is the size of the world model's real token stream.
2. **Distinct real slates cap the planner.** There are only about 6-8k real multi-league
   slate-nights since 2008. That is the ceiling on independent real self-play episodes.
3. **Tracking is huge but narrow.** SportVU (3.0B) is one half-season; Punt (0.55B) is ~6.7k
   punts. Use them to train small detectors, then distill those into pbp-only labels. Do not let
   them dominate world-model batches.
4. **Synthetic saturates.** World-model pretraining keeps gaining from re-timed and counterfactual
   data up to about **3x the real token volume**. Stop rule: when two straight 2x increases of
   synthetic data move held-out **real-night** loss by under 1%, stop generating. For the planner,
   slate re-mixing helps up to about **1-2M distinct slates** (~200x the real nights); past that the
   mixes repeat game pairs, and only new real nights and our live logs add signal.
5. **Derived values add nothing new.** WP, GEI, solar position, travel and playoff-odds summaries
   are functions of data we already hold. They are useful features, but they are not new data.
   Solar position is computed on the fly and not stored.
6. **Our live-night logs are worth the most per row** (real latency, mid-play entries).
   Weight them 10-50x. Nick's presses and overrides are **controls only, never a training label or weight**
   (the self-taught rule); they may appear only as an evaluation-only line.

## 2. Ranked waves

### Wave 1: green, no action from Nick (start now)

Ordered by new information per GB for the Noggin's heads (next event, time to it, next stoppage,
must-watch).

| Rank | Source | Counted | Stored | Why this rank |
|---:|---|---:|---:|---|
| 1 | Media-timeout and break rule tables (hand-coded YAML) | 9k | ~0 | Near-deterministic "break owed" prior for CBB, NBA and NHL; directly serves Rule #1 |
| 2 | Our live-night logs (auto-log #118) | 20M/season | 0.2 GB/season | Gold labels; the only real latency data (overrides: evaluation only, never a label) |
| 3 | Retrosheet events + game logs + Chadwick register | 1.55B | ~2 GB | The only green play-by-play; any use allowed; best seed for synthetic baseball |
| 4 | NBA L2M reports | 3.5M | ~0.01 GB | League-picked crunch-time games, review stoppages |
| 5 | FiveThirtyEight Elo/forecasts (mirror now, before it disappears) | 9M | ~0.05 GB | Pregame priors and upset-in-progress trigger |
| 6 | Wikidata sports subgraph + coach tenures | 10M | ~0.1 GB | Crosswalk IDs and coach IDs for every league |
| 7 | Wikimedia pageviews (2015+ clean; game-night hourly) + revision metadata | 3.2B | ~11 GB | Hindsight "should have watched" label and edit-velocity breakout sensor |
| 8 | NOAA GLM lightning (game windows only) + GHCNh stations | 1.23B | ~3 GB | Delay prediction (8-mile rule), present-weather codes |
| 9 | Wikipedia Clickstream | 0.5B | ~2 GB | Attention graph; lowest priority in this wave |

### Wave 2: yellow, attribution and private storage only (no key, no sign-off)

| Rank | Source | Counted | Stored | Condition |
|---:|---|---:|---:|---|
| 1 | nflverse pbp, participation, FTN, schedules, officials, injuries, depth charts, NGS | 0.48B | ~0.8 GB | Credit nflverse; FTN is CC-BY-SA; private raw rows |
| 2 | shufinskiy/nba_data | 1.1B | ~4 GB | NBA.com ToS §9: private, credit NBA.com |
| 3 | MoneyPuck listed files | 0.9B | ~2 GB | Non-commercial, credit MoneyPuck.com |
| 4 | Derived from Waves 1-2: nfl4th, WP/GEI, records, travel, standings | 0.24B | ~1 GB | Build only from green/yellow inputs; leakage check in CI |
| 5 | Open-Meteo (or ARCO-ERA5, no call quota) | 1.0B | ~2.5 GB | CC BY 4.0; ~300k weighted calls over a month, under 10k/day |
| 6 | NBA injury archive, Lahman, Stathletes | 25M | ~0.2 GB | Per-source credit lines |
| 7 | GDELT GKG (raw CSV stream) + GDELT TV | 0.52B | ~6 GB | Cite GDELT; never enable BigQuery billing |
| 8 | Wikipedia tables, OSM geometry | 1.5M | ~0.02 GB | CC BY-SA / ODbL on anything published |

### Wave 3: yellow, needs Nick to do something (about 2-10 minutes each)

| Rank | Source | Nick's action | Unlocks |
|---:|---|---|---|
| 1 | CollegeFootballData (Academic tier, unc.edu) | Register, add the key as a Kaggle secret | 150M CFB values; also the CBBD pool for CBB games and venues |
| 2 | Big Data Bowl 2019-2026 | Open each competition, read and accept the Rules tab | 2.0B tracking values (attached in Kaggle, 0 GB of quota) |
| 3 | March Machine Learning Mania 2026 + 2020 | Accept rules, read the data-use clause | 270M CBB values, Massey ordinals, MEvents |
| 4 | cbbdata (Torvik) | Register the free key | 90M day-by-day CBB ratings |
| 5 | nbadb Kaggle dataset | Read its license field | 200M new NBA values (officials, arenas) |
| 6 | NFL Punt Analytics | Read the Rules tab; drop if "competition use only" | 550M punt tracking values |
| 7 | Kaggle NFL Scores and Betting | Check the license field (CC0 / CC-BY / ODbL only) | 0.11M |
| 8 | The Odds API (forward) | Register the free key | 3M/year |
| 9 | YouTube Data API, Google Trends alpha | Key / alpha application | ~0 durable until Google confirms |

### Wave 4: yellow, needs Nick's risk sign-off (one line each in docs/memory/decisions.md)

| Rank | Source | Risk Nick accepts | Unlocks |
|---:|---|---|---|
| 1 | NBA SportVU 2015-16 | No license; private training only | 3.0B frames for the dead-ball detector |
| 2 | Statcast scoped 2021-2025 | MLBAM "non-bulk" wording; email MLBAM before a full backfill | 0.4B (1.1B with MLBAM OK) |
| 3 | cdn.nba.com forward collection | NBA.com personal-use terms; ~10 fetches a night after games end | 30M/season of fresh-night eval data |
| 4 | Polymarket documented APIs | ToS silent on ML; read-only, no wallets kept | 0.25B in-game probability values |
| 5 | Bluesky counts | Community norms on AI use; counts only | 10M/season |
| 6 | SBRO Wayback archive | No license on odds files | 5M |

### Wave 5: Nick's call on ESPN-derived data (default OFF)

hoopR NBA (0.85B) and men's college basketball (1.4B) come from pre-built sportsdataverse
releases of ESPN data. Disney's terms bar AI training, and a third-party release grants no license.
Without them, CBB (Nick's UNC league) has only ~1M real events (MMLM 2015-2019). This is the
single biggest gap in the plan; Nick decides it in decisions.md. NHL.com bulk pulls stay red unless
Nick also accepts that risk there.

### Wave S: synthetic (after Waves 1-3)

1. Fit per-league, per-network break-length models from the wall-clock sources.
2. SYNTHETIC A, slate re-mixer and re-timer: oversample slates where 2-3 games hit crunch time
   in the same minute. Cache about 1B values, generate the rest on demand.
3. SYNTHETIC B, WP counterfactual rollouts with coach-policy branches. Cache about 1B values.
4. Apply the saturation stop rule from section 1. Tag every synthetic row `is_synthetic=true`;
   the eval loader refuses those rows.

## 3. Storage math (215 GB private Kaggle quota + Hugging Face private backup)

Estimates are compressed parquet (zstd), about 1-3 bytes per value; tracking floats cost more.

| Block | Values | Stored |
|---|---:|---:|
| Wave 1 (green) | 6.5B | ~18 GB |
| Wave 2 (yellow, attribution) | 4.3B | ~17 GB |
| Wave 3 sources stored by us (CFBD, cbbdata, Odds API) | 0.25B | ~1 GB |
| Wave 3 Kaggle-attached (BDB, Punt, MMLM, nbadb) | 3.0B | 0 GB (attached, not copied) |
| Wave 4 (SportVU, Statcast, Polymarket, cdn, Bluesky, SBRO) | 3.7B | ~12 GB |
| Synthetic cache | 2.0B | ~6 GB |
| **Total stored (no ESPN)** | **~19.7B** | **~54 GB** |
| ESPN releases if approved | 2.25B | +5 GB |

- Headroom: ~150 GB. Working cap: stay under **120 GB** so new seasons, live logs and one previous
  dataset version fit. Assume old dataset versions count against the quota; keep at most two.
- Never land a full dump in the dataset: the Wikidata JSON (~100+ GB), enwiki stub history (tens
  of GB) and GDELT raw (several TB) are stream-filtered inside a session and only the filtered
  parquet is saved.
- Each 12 h session writes one shard; collectors are resumable by shard ID.
- **Hugging Face private backup** (verify the current free private-storage limit on HF before
  relying on it; do not assume). Back up only: green sources, CC-BY/CC0 sources (nflverse, 538,
  Open-Meteo, GDELT), our derived tables, synthetic caches and live logs, about 30 GB. Never back
  up Kaggle competition data (BDB, Punt, MMLM), nbadb, NBA.com- or ESPN-derived data, MoneyPuck,
  MLBAM data or Polymarket pulls: their terms either keep the data on Kaggle or forbid copies.

## 4. Event-token schema

Every source maps into one row type, `NogginEvent`. One row = one event in one game. Optional
fields are null when a source does not have them.

| Field | Type | Meaning |
|---|---|---|
| `league` | enum | NFL, CFB, NBA, CBB, NHL, MLB (+ PRETRAIN_ONLY leagues) |
| `game_id` | str | Our crosswalked game ID |
| `slate_id` | str | Real slate night, or a synthetic slate ID |
| `seq` | int | Order within the game |
| `period` | int | Quarter, half, period or inning (with `half_inning` for MLB) |
| `clock_s` | float | Game clock seconds remaining (null for MLB) |
| `wall_s` | float | Wall-clock seconds since game start (null when the source has none) |
| `event_type` | enum | Shared vocabulary: SNAP, PASS, RUSH, SHOT, MADE, MISS, FOUL, FT, TIMEOUT, TV_TIMEOUT, CHALLENGE, REVIEW, INJURY, SUB, PITCH, PA_END, PITCHING_CHANGE, MOUND_VISIT, FACEOFF, STOPPAGE, GOAL, GOALIE_PULL, PENALTY, PERIOD_END, DELAY, ... |
| `stoppage` | bool | True when the event starts a dead ball |
| `stoppage_reason` | enum | TIMEOUT, TV, REVIEW, INJURY, SCORE, PERIOD, ICING, OFFSIDE, WEATHER, ... |
| `stoppage_len_s` | float | Label: wall seconds until play resumes (from wall-clock gaps) |
| `team` | str | Acting team |
| `score_diff` | int | Home minus away, before the event |
| `state` | struct | League state: down/distance/yardline, possession, count/outs/bases, skaters, shot clock |
| `timeouts` | struct | Timeouts left per team |
| `wp`, `dwp`, `leverage` | float | Win probability before the event, its change, leverage |
| `coach_id` | str | Head coach or manager of the acting team |
| `context_ref` | str | Join key to pregame context (weather, rest, Elo, stakes, hype) |
| `source_id`, `license_tag` | str | Provenance and license (green/yellow/...), used for backup and redistribution filters |
| `is_synthetic` | bool | True for generated rows; the eval loader rejects them |

How each main source maps:

| Source | Fills | Gives the labels for |
|---|---|---|
| nflverse pbp (+ participation, FTN) | All core fields; `wall_s` from `time_of_day` (2011+); `wp`; `state` with personnel | NFL next event, `stoppage_len_s`, tempo |
| BDB tracking | Pre-snap sub-events (HUDDLE_BREAK, LINE_SET, MOTION) between pbp rows | Time to snap |
| CFBD | Core fields; `wall_s` where wallclock exists; network in `context_ref` | CFB next event, break cadence by network |
| shufinskiy (stats + pbpstats + cdn) | Core fields; possession start type in `state`; `wall_s` from cdn `timeActual` | NBA next event and real break lengths |
| SportVU | Frame-level side table, linked by event ID; produces a per-second `live` label | Dead-ball detector, distilled to pbp |
| MMLM MEvents / hoopR MBB (if approved) | CBB core fields | CBB media-timeout grammar |
| MoneyPuck | SHOT/GOAL rows with `state` skaters, empty net, time since last event | NHL scoring hazard, goalie-pull |
| Retrosheet | PITCH and PA_END rows, SUB rows, `state` count/outs/bases; game duration | MLB next event, pitching changes |
| Statcast (scoped) | PITCH rows with WE/RE deltas as `dwp`/`leverage` | MLB leverage |
| Rule tables | Not rows: a `break_owed` feature computed per event | Next guaranteed stoppage |
| Polymarket | Per-minute side table keyed by `game_id` + wall minute | Market entropy, trade-gap break detector |
| Weather, GLM, Elo, travel, standings, hype | `context_ref` tables (pregame and per-hour) | Conditioning, delay risk, must-watch prior |
| Live-night logs | Our own TraceEvents, joined by `game_id` + `wall_s` | Gold mid-play and latency labels (no preference labels from Nick: self-taught rule) |

## 5. The 10 most creative ideas

1. **Wall-clock gap mining.** Diff consecutive wall times (nflverse `time_of_day`, cdn.nba
   `timeActual`, CFBD wallclock) and label every gap above the play-clock floor by the event
   before it (score, review, injury, TV). A legal, labeled break dataset with no video.
2. **Rulebook "break owed" countdown.** CBB breaks fire at the first dead ball under 16/12/8/4, NBA
   after 7:00 and 3:00, NHL after 14/10/6. A near-deterministic feature that lets the planner park
   the second stick before the break.
3. **Snap-imminent detector.** BDB 2025 tags huddle break, line set and motion. Train a tiny model
   on time from huddle break to snap so the Reflex knows a dead ball is about to end.
4. **Motion-to-pbp distillation.** Train a dead-ball detector on SportVU frames, then learn which
   pbp patterns predict that state 3-10 seconds ahead, and label every second of 30 years of pbp
   as safe or unsafe to cut.
5. **Coach-caused dead balls as the switch clock.** A hazard head for "seconds until the next coach
   stoppage" (timeout after a run, challenge, pitching change, mound visit). These are guaranteed
   dead balls.
6. **Coach fingerprint tokens.** Per coach: 4th-down go rate minus the nfl4th recommendation,
   goalie-pull seconds early per goal down, timeout-after-run threshold, hook speed. The model
   predicts this coach's next stoppage, not the league average.
7. **Collision-heavy slate re-mixing.** Rebuild real Sunday and Saturday windows from schedules,
   then oversample synthetic slates where 2-3 games hit crunch time in the same minute, the case
   the planner most needs.
8. **Lightning-rule simulator.** GOES flashes within 8 miles plus the 30-minute reset rule give
   physically grounded delay labels, and counterfactual storms on historical games.
9. **Free hindsight "should have watched" score.** Next-day Wikipedia pageview surge plus live
   edit velocity on linked pages, z-scored by fanbase. CC0 and needs no labeling.
10. **Decision suspense as must-watch.** A pending high-stakes call with a near-zero model edge
    (small `|go_boost|`, 4th-and-1 late, 2-point try down 8) predicts interest before the snap, so
    the planner switches in at the dead ball before the decision.

Runners-up: point-in-time playoff-leverage replay as a stakes token. (Dropped: Polymarket entropy as a
crunch-time label, because market data is a hidden calibration/eval check only (overseer, Nov 4), and Nick's
overrides as preferences, because of the self-taught rule.)
