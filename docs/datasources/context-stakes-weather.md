# Context: stakes and conditions (Crew B, worker B3)

What a game *means* (standings, playoff odds, how much this one game swings them) and what it is
*played in* (weather, TV slot, poll ranks). Rows follow the context-token format in
`redzone/context/tokens.py`: one parquet per league-season, columns
`game_id, team, asof_utc, field, value, value_str, bucket, kind, source, license`. Buckets are 16
quantile bins fit on TRAIN games only (`edges.json` beside the shards).

| Shard folder (`/mnt/project-files/data-b/shards/`) | Code | Status | Facts | Games | kind |
|---|---|---|---|---|---|
| `nfl-stakes/1999..2026.parquet` | `scripts/context/stakes.py` | **landed** (run here, 22 s) | 208,488 | 7,413 | derived |
| `mlb-stakes/2000..2025.parquet` | `scripts/context/stakes.py` | **landed** (run here, 94 s) | 1,787,386 | 61,634 | derived |
| `nfl-weather/`, `mlb-weather/` | `scripts/context/weather.py` | code + tests; needs a cloud run (open-meteo.com is blocked here) | – | – | new |
| `cfb-context/` | `scripts/context/cfbd_context.py` | code + tests; needs `CFBD_API_KEY` and a cloud run | – | – | new |

Train cutoffs: NFL games before 2022-09-01, MLB before 2022-03-01, CFB before 2022-08-01.

## 1. Stakes (`redzone/context/standings.py`)

League-generic: a `LeagueConfig` gives divisions per season, the playoff rule per season
(`per_div` automatic bids + `wildcards` per conference), Elo settings and the sim count. Input is
one schedule + results table (`redzone/context/schedules.py`: nflverse `games.csv`, Retrosheet
`GL<year>.TXT`).

| Field | Per | Meaning |
|---|---|---|
| `wins`, `losses`, `win_pct` | team | regular-season record from completed games only (ties = half) |
| `div_rank`, `games_back` | team | rank in division by win pct; games behind the division leader |
| `elo`, `elo_diff`, `elo_win_prob` | team | online Elo (NFL K=20, HFA 48, 538 margin multiplier; MLB K=4, HFA 24), regressed 1/3 to 1500 each season |
| `elo_diff_abs` | game | closeness prior |
| `playoff_odds` | team | P(make playoffs), Monte Carlo of the remaining schedule with Elo frozen at asof (NFL 1,000 sims, MLB 500) |
| `playoff_odds_if_win`, `playoff_odds_if_loss` | team | the same with THIS game forced each way (common random numbers for every other game) |
| **`playoff_odds_swing`** | team | \|if_win - if_loss\|, in [0,1]: the stakes number |
| `stakes_swing_max`, `stakes_swing_sum` | game | max and sum of the two teams' swings |
| `clinch_status` | team | `clinched_div` / `clinched_playoff` / `eliminated` / `alive`, mathematical and conservative |
| `elimination_game` | game | 1 for NFL playoff games (Elo fields are emitted too) |

Playoff formats: NFL 12 teams 1999-2019 (1999-2001: 3 divisions + 3 wild cards a conference; 2002-2019:
4 + 2), 14 since 2020. MLB 8 teams to 2011, 10 for 2012-2021, 16 in 2020 (top 2 per division + 2),
12 since 2022. Ties in the sims are broken at random (no league tiebreak rules).

**asof rule (no leakage).** NFL: asof = kickoff - 30 min; a game's result counts once
kickoff + 4 h <= asof, so a 4:25 PM game sees the 1 PM results. MLB (the logs have no start
times): asof = 09:00 UTC on game day; a result counts from 09:00 UTC the next day, so a
doubleheader's game 2 never sees game 1. Elo, standings and the sim all use only those results.
Unplayed games in the next 8 days get asof = run time (the live pregame case; the 2026 NFL shard
holds the week-9/10 games pending tonight). `tests/test_context_stakes.py` flips the result of game
g and every later game and checks g's facts are byte-identical.

Elimination proof sketch: if at least `per_div` division rivals already have more points than my
maximum and at least (all bids) conference teams already have more points than my maximum, the
non-division-winners among them fill every wild card ahead of me. Clinch: fewer than `per_div`
rivals can reach my points (division), or fewer than `wildcards` conference teams can (playoffs).

Known simplifications: the remaining-schedule list is the schedule as later played (Retrosheet
logs hold only games that happened; rare cancellations are invisible). nflverse `gametime` is
missing for 1999 (13:00 ET assumed).

Licenses: `derived from nflverse schedules (CC-BY-4.0)`; Retrosheet rows carry the Retrosheet
notice ("The information used here was obtained free of charge from and is copyrighted by
Retrosheet..."). Both add the personal-use tag. Source id `derived-standings`.

Run: `python scripts/context/stakes.py --league nfl --games games.csv --out <shards>/` and
`--league mlb --retro-dir <GL files>`; both finish in under two minutes on 4 cores.

## 2. Weather (`scripts/context/weather.py`)

Open-Meteo historical archive (`archive-api.open-meteo.com/v1/archive`, ERA5-based), **CC BY 4.0,
free for non-commercial use, credit Open-Meteo**, under 10,000 calls a day. One call per
venue-season (start..end date of that venue's games): NFL 1999-2026 = 711 calls, MLB 2000-2025 =
617 calls, so one day covers both. Responses are cached on disk; `--max-calls` (default 9,000)
stops a run below the daily cap. `--plan-only` counts calls with no network.

| Field | kind | Notes |
|---|---|---|
| `temp_f` | new | 2 m temperature at the kickoff hour |
| `wind_mph` | new | 10 m wind |
| `precip_mm` | new | precipitation in the hour ending at the kickoff hour |
| `snow` | new | 1 if snowfall > 0 in the kickoff hour or the hour before |
| `roof_open` | derived | NFL retractable roofs, from the nflverse `roof` column |

Only outdoor games: nflverse `roof` in {outdoors, open}; MLB parks with `roof = outdoors` in
`redzone/context/venues.csv` (retractable MLB parks are skipped: open/closed is not in the logs).
asof: archive rows = kickoff - 1 min (the reanalysis value stands in for the kickoff forecast);
`forecast` mode calls `api.open-meteo.com/v1/forecast` for games in the next 36 h with asof = fetch
time, and drops any game whose kickoff has already passed. Sources `open-meteo-archive` /
`open-meteo-forecast`. MLB kickoff = 13:10 local (day) or 19:10 local (night) from the D/N flag.

Venues: `redzone/context/venues.csv` (league, venue_id, name, team, lat, lon, tz, roof, seasons),
keyed by nflverse `stadium_id` (current + historical outdoor sites, international games) and
Retrosheet park id (all 30 current parks + historical/special-event parks; 4 one-off parks have no
row and are skipped). CFB: `cfbd_context.py venues` writes rows in the same columns from CFBD
`/venues`; feed them with `--venues` and a `--games-csv`.

Fallback (documented, not wired): NOAA NCEI Global Hourly / ISD (public domain),
`noaa_isd_url(station, start, end)`; needs a venue to station crosswalk.

## 3. CFB context (`scripts/context/cfbd_context.py`)

CollegeFootballData API, bearer key from the env var `CFBD_API_KEY` only (Kaggle / Actions
secret; never printed, logged or cached). License line: **CollegeFootballData.com API, free tier,
credit CFBD**. Free tier was 1,000 calls/month when written (check collegefootballdata.com/key).
Calls are per season, never per game: 6 a season + 1 for venues, 2014-2025 = 73 calls; the cache
makes reruns free; `--max-calls` 150 is a hard stop. `/lines` is crew D's and is not called.

| Endpoint | Fields | asof / leakage rule |
|---|---|---|
| `/games` | `neutral_site`, `conference_game`, `venue_id` | pregame facts |
| `/games` | `prev_game_excitement`, `prev_home_attendance` | POST-game numbers: only from the team's earlier game with start + 5 h <= asof |
| `/games/media` | `tv_network`, `tv_tier` (broadcast/cable/conference/stream), `national_tv` | pregame |
| `/games/weather` | `temp_f`, `wind_mph`, `precip` (raw CFBD unit), `snow` | outdoor games only; may need a Patreon tier (401/403 is skipped) |
| `/rankings` | `ap_rank`, `coaches_rank`, `cfp_rank` | latest regular-season poll of a week before the game's week; bowls use the last regular poll, never the post-bowl final |
| `/records` (Y-1) | `prev_season_win_pct` | previous season only |
| `/talent` | `talent` | 247 roster composite, published pre-season |

All rows asof = kickoff - 1 h, kind new, game_id = the CFBD game id (lane 1 crosswalks it).

## Tests

`tests/test_context_stakes.py` (toy 4-team league: leakage flip test, asof < kickoff, swing and
odds in [0,1], picker counts, mathematical clinch/elimination, NFL/MLB bid counts per era, pregame
rows), `tests/test_context_weather_cfbd.py` (Open-Meteo fixture in the documented response format,
unit checks, one call per venue-season, domes skipped, forecast-after-kickoff dropped, CFBD
fixtures for prior-week polls, prior-game post-game stats, media tiers, indoor weather, venues
hook, missing key).


**Chunked Actions runs (Nov 5):** push `data-b-weather/nfl-<first>-<last>`; outputs land on `data-b-out/weather-nfl-<first>-<last>`. Each chunk fits its own edges, so a chunk after the train cutoff has empty edges; once all chunks land, merge them and refit edges on train games before handing to lane 1.
