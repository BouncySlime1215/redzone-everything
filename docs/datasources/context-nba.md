# NBA context facts (Data Crew B, worker B4)

The NBA comes first (Nick, Nov 5, 2:00 AM). These facts use the shard format in
[context-tokens.md](context-tokens.md): one parquet per league-season, the `tokens.COLUMNS` columns, and 16 buckets
fit on TRAIN games only (tip before **2022-10-01**). Seasons are named by their start year, so 2024 means 2024-25.
Code: `redzone/context/nba.py`, the NBA config in `redzone/context/standings.py`,
`scripts/context/nba_context.py` and `scripts/context/nba_injuries.py` (Actions). Tests: `tests/test_context_nba.py`.

## Sources and license lines

| id (terms.json) | what | license line | where it runs |
|---|---|---|---|
| `hoopr` | sportsdataverse hoopR NBA schedules, `espn_nba_schedules/nba_schedule_<END year>.parquet` (GitHub release) | `sportsdataverse hoopR schedules (MIT; ESPN-derived data); personal-use, not for commercial release` | here (github.com) |
| `derived-standings` | Elo and playoff Monte Carlo computed from the hoopR results | `derived from sportsdataverse hoopR NBA schedules (MIT; ESPN-derived data); personal-use, ...` | here |
| `shufinskiy_nba_data` | cdnnba_<y>.tar.xz: only gameId, first timeActual and tricodes, used for the id crosswalk (no rows kept) | Apache-2.0 packaging over NBA.com; personal-use | here |
| `nba_l2m` | atlhawksfanatic/L2M `1-tidy/L2M/L2M_stats_nba.csv` (2015-2025 Last Two Minute reports) | `atlhawksfanatic/L2M (MIT) over the NBA's public Last Two Minute reports (official.nba.com); credit both; personal-use, ...` | here |
| `nba_injury_archive` | official injury report PDFs `ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMPM.pdf` (Dec 2018 on) | `NBA official injury report, public; personal-use, not for commercial release` | **Actions only** |
| `nba_ref_assignments` | official.nba.com/referee-assignments (daily, 9 AM ET) | `NBA official referee assignments (official.nba.com), public; personal-use, ...` | **Actions only, live nights** |

hoopR is ESPN-derived, so it is plan B and tagged personal-use. On robots.txt and terms: this container cannot reach
nba.com, so nobody has read robots.txt yet. `Polite.robots_ok` in nba_injuries.py fetches robots.txt for each host
before the first request. The run aborts if the path is disallowed or if robots.txt answers 401/403, and it treats an
unreachable robots.txt as disallow-all. Requests are spaced 1.5 s apart and carry a named User-Agent.

## IDs

`game_id` is the ESPN event id, the same id the hoopR play table uses (`redzone/noggin/factory.py`). The NBA.com id
(`00224xxxxx`, used by the nba-cdn play rows) goes in the `nba_game_id` column of the schedule CSVs. It is matched on
the tricode pair plus the nearest first-action time (≤ 6 h) and covers 7,709 of 7,712 cdn games (2020-21 on). Before
2020 there is no crosswalk. L2M games still join for every season, on ET date + tricode pair.

## Fields

**nba-stakes** (derived, asof = tip − 30 min, only from results final ≤ asof). Fields: `wins, losses, win_pct,
div_rank, games_back, elo, elo_diff, elo_win_prob, elo_diff_abs, playoff_odds(+_if_win, _if_loss, _swing),
clinch_status, stakes_swing_max, stakes_swing_sum`, and `elimination_game` for playoffs, play-in and the Cup final.
The `playoff_odds` target depends on the era:
- 2020-21 on: a top-6 seed (direct playoff spot). This era also gets `top10_odds`, `top10_odds_swing` (top 10 =
  playoffs or play-in) and `playin_odds` (P of seed 7-10).
- 2015-16 to 2019-20: top 8.
- Through 2014-15: division winners plus 7.

Elo uses K 10 with a log MOV multiplier, home edge 80 and regression 1/4. Seasons 2002-03 and 2003-04 only warm Elo
up. 400 sims with common random numbers. Ties are broken at random (no NBA tiebreak rules). Relocated teams (NJ→BKN,
SEA→OKC) restart at 1500 Elo, because ESPN abbreviations change.

**nba-rest** (derived, asof = tip − 30 min). It uses only the team's earlier tip times and cities, never a result.
Fields: `rest_days` (ET days between games − 1, capped at 10), `back_to_back`, `games_in_last_7`, `road_trip_game`,
`home_stand_game`, `travel_km` (great circle between arena cities) and `tz_shift_h`. Arena cities are in
`redzone/context/venues.csv` (rows `league=nba`, `venue_id=nba:<city>`, which includes neutral sites). When a game's
city is missing, the home team's arena from that static table is used.

**nba-tv** (new, asof = tip − 30 min). Fields: `national_tv` (0/1) and `tv_network` (value_str: ABC > NBC > TNT >
ESPN > Prime Video > Peacock > NBCSN > NBA TV). The values come from ESPN's national broadcast field. Canada-only
feeds (TSN, Sportsnet) do not count as national. The same data is in
`/mnt/project-files/data-b/nba-national-tv.csv` (columns: date, home, away, network, national, networks,
broadcast_known, game_id, nba_game_id, start_utc, season, game_type), 2020-21 on. In 2021-22 and 2022-23, ESPN listed
local broadcasters for only ~27% of games (`broadcast_known`). The national list looks complete (about 365 per
season).

**nba-l2m** (L2M games only, i.e. close games). These are game-level fields:
- **new, asof = tip − 30 min:** `ref_crew_chief_id`, `ref_2_id`, `ref_3_id` (value_str `ref_<NBA official id>`).
  Assignments are public at 9 AM ET on game day.
- **new, `*_postgame`, asof = next day 09:00 ET:** `l2m_calls_graded_postgame`, `l2m_incorrect_calls_postgame`
  (IC + INC) and `l2m_incorrect_noncalls_postgame`. Use them only as prior-game features or aux targets.
- **derived:** `l2m_ref_incorrect_rate_prior` (mean incorrect calls over the crew's earlier L2M games that ended
  before this tip, pooled across the crew) and `l2m_ref_games_prior` (also summed over the crew).

**Injuries** (Actions; new, team level, asof = the report's own ET timestamp). For each game, the latest report at
least 30 min before tip is used. Fields: `inj_out_n, inj_doubtful_n, inj_questionable_n, inj_probable_n,
inj_report_age_min, inj_not_submitted`, and `inj_star_out`: how many of the team's top-2 players by season minutes in
games that tipped before this one (hoopR player_box) are listed Out or Doubtful. **Needs live verification:** the
parser was tested on a fixture written from the known layout, not on a real PDF.

**Referees, live** (Actions): same `ref_*_id` fields, with names mapped to official ids through the L2M name table.
Names not in that table become `refname_<slug>`. Facts are written only before tip.

## Landed (Nov 5, `/mnt/project-files/data-b/`, manifest-b4.json)

| shard dir | seasons | games | facts | new | derived |
|---|---|---|---|---|---|
| nba-stakes | 2004-2026 | 28,542 | 832,494 | 0 | 832,494 |
| nba-rest | 2004-2026 | 29,649 | 411,630 | 0 | 411,630 |
| nba-tv | 2019-2026* | 7,621 | 10,526 | 10,526 | 0 |
| nba-l2m | 2014-2024 | 4,484 | 35,698 | 26,804 | 8,894 |

\* hoopR has no broadcast data before 2019-20.

2026 (2026-27) covers games played through Nov 4, plus upcoming games within 8 days (asof = run time).

## Schedule CSVs (for run_buzz backfill)

`redzone/context/schedules/nba.csv.gz` (234 KB, 29,649 games 2004-05 to 2026-27, with `nba_game_id`) and `nfl.csv.gz`
(58 KB, nflverse) are committed. This is fine because the repo is public and the files hold only teams, times and ids.
Columns: `game_id, league, home, away, start_utc, season`. The context-buzz workflow passes every non-NFL file there
as `--schedule` (run_buzz reads `.gz`). Full versions with scores are at
`/mnt/project-files/data-b/schedules/{nba,nfl}.csv`. To refresh, run
`python scripts/context/nba_context.py --raw RAW --commit-schedules --nfl-games games.csv`.

## Needs a cloud run

- Injury backfill: push a branch `data-b-nba-run/2024-10-22_2024-12-01` (≤ ~6 weeks per branch).
  `.github/workflows/context-nba.yml` writes the output to `data-b-out/nba-inj-<from>_<to>`.
- Live injuries and referees: `workflow_dispatch mode=live`. Hourly scheduling needs a Kaggle scheduled notebook,
  because `schedule` only fires from main (same note as context-buzz).
- Referee history before 2015, and for non-L2M games, is not held. The GitHub-hosted officials datasets we know of
  (nbadb / wyattowalsh Kaggle) were not reachable here, so that part is skipped.

## Hold (Nov 5, 2026, 2:15 AM ET)

The injury-report and referee-assignment jobs (nba.com hosts) are **on hold**. The guardrails require respecting a
site's terms as well as robots.txt, and nba.com's terms of use have not been read yet (this cloud container cannot
reach nba.com). `context-nba.yml` refuses to run until `docs/datasources/terms.json` has
`"nba_terms_review": "allowed"`. If the terms forbid automated collection, these jobs are dropped and NBA injuries
come from another public source (for example hoopR/ESPN injury fields under plan B, after their own check).
The first test run (Nov 1-4, 2025) was cancelled while queued, before any request to nba.com.

### Terms review (Nov 5, 2026, 2:35 AM ET)

nba.com's terms of use (21 sections) were read. They contain no clause on robots, scraping, crawling or AI use.
They allow downloading material "for your personal, noncommercial use" (section 1), but forbid republishing
Basketball Content (section 1). NBA Statistics may be used only for private, noncommercial purposes, with credit to
NBA.com, and not in a public database of comprehensive league statistics (section 9).

Result: the pull is allowed for this personal project, but **not on GitHub Actions**, because that job pushes its
outputs to a public branch. The hold in `context-nba.yml` stays. The pull runs on Kaggle CPU instead and writes only to the private
`context-features` dataset, still checking robots.txt first. Command for the overseer:
`python scripts/context/nba_injuries.py injuries --from 2018-12-01 --to <today> --out /kaggle/working/nba-inj` (add `--player-box` hoopR player_box parquet files for star_out)
(see the script's `--help`). The license line credits NBA.com.

### Kaggle queue (crew B, NBA)

1. `nba_injuries.py injuries` (above), private dataset only.
2. nbadb (Kaggle `wyattowalsh/basketball`, terms id `nbadb`, restrictive, so personal-use): attach as a notebook input,
   read the dataset's licence field first, then take `officials` per game (fills referee history beyond L2M; crew ids
   kind new, prior-only tendency z derived) and `game_info.game_time` as `game_length_postgame` (post-game, asof next
   day; a cross-check on summed break lengths for Data A and crew F).
