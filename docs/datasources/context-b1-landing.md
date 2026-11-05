# Crew B context: first landing from GitHub-hosted data (worker B1)

Script: `scripts/context/land_github_ctx.py` (tests: `tests/test_context_land.py`, no network).
Format: lane 1's vocab v2 hand-off, `redzone/context/tokens.py`.
Outputs: `/mnt/project-files/data-b/shards/{nfl,mlb}/<season>.parquet`, `shards/<league>/edges.json`,
`/mnt/project-files/data-b/manifest-b1.json`.

```sh
python scripts/context/land_github_ctx.py --raw <download cache> --out /mnt/project-files/data-b/
```

Downloads run one at a time with a short pause between them, and files already cached are skipped. Buckets 0-15
are fit only on TRAIN games: NFL games before 2022-09-01 and MLB games before 2022-03-01. Text fields have no bucket.
No betting lines are emitted, because spread, total and moneyline belong to crew D.

## Sources and license lines

Every row's `license` also carries `personal-use, not for commercial release`.

| source id | where | license |
|---|---|---|
| nflverse-schedules | nflverse-data release `schedules/games.csv` | nflverse CC-BY-4.0, credit nflverse |
| nflverse-injuries | release `injuries/injuries_YYYY.csv` (2009-2026) | same |
| nflverse-officials | release `officials/officials.csv` (2015-2026) | same |
| nflverse-stats-team | release `stats_team/stats_team_week_YYYY.csv` (penalty counts only) | same |
| retrosheet-gamelogs | chadwickbureau/retrosheet `seasons/YYYY/GLYYYY.TXT` (2024 is `gl2024.txt`) | Retrosheet: free, must credit "The information used here was obtained free of charge from and is copyrighted by Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org" |
| retrosheet-events | same repo, `seasons/YYYY/YYYYTTT.EV[AN]` (only `info` records are read) | same |

## NFL (game_id = nflverse, e.g. `2024_01_BAL_KC`)

Kickoff is `gameday` + `gametime`, both in US/Eastern and converted to UTC. When `gametime` is missing (259 games
from 1999-2000), kickoff defaults to 13:00 ET. Unless a row says otherwise, asof = kickoff - 1s.

| field | team? | kind | notes |
|---|---|---|---|
| roof, surface, stadium | game | new | categorical |
| temp_f, wind_mph | game | new | **observed at kickoff**, which is acceptable for pregame. Only for `outdoors`/`open` roofs |
| referee | game | new | crew chief name (1999+) |
| crew_chief_id | game | new | officials release `official_id` of the Referee, `off<id>` (2015+) |
| coach | team | new | head coach |
| rest_days | team | derived | from schedules |
| rest_diff | game | derived | home minus away |
| div_game, weekday, kickoff_hour_et, primetime | game | derived | primetime = kickoff at 19:00 ET or later |
| players_out / players_doubtful / players_questionable / qb_out | team | new | from the final weekly report, deduplicated per player, latest row wins. asof = the latest `date_modified`, capped at kickoff-1s |
| ref_prior_games, ref_flag_rate_z | game | derived | z = (that referee's mean accepted penalties per game, both teams, over prior games − league mean) / league sd, computed only over games whose kickoff + 4h ≤ this game's asof. It needs ≥30 league games and ≥8 games for the referee |

Injury rows whose `date_modified` falls after kickoff-1s are dropped; 26 rows were dropped. Rows **without**
`date_modified` (almost all of 2009, and all of 2025-2026, whose files lack the column) are treated as the official
final pregame report, with asof = kickoff-1s. A "starters out" flag was skipped because injuries carry no
depth-chart starter flag. It needs nflverse depth_charts, which is a later pass.

## MLB (game_id = Retrosheet id, home + yyyymmdd + game number, e.g. `NYA202304010`)

First pitch is `info,starttime` from the event file, read in the park's time zone. The time zone is looked up by
home team, and international or neutral parks are overridden through `PARK_TZ`. Pregame asof = first pitch - 1s.
For the 2,423 games with no usable start time, asof falls back to 09:00 UTC on the game date, which is earlier and so
conservative.

| field | kind | notes |
|---|---|---|
| hp_umpire, park, day_night | new | from game logs |
| dh_game | derived | game number is 1 or 2 |
| temp_f, wind_mph, wind_dir, sky, precip | new | from event `info`, **observed at game time**, asof = first pitch - 1s. Unknown values (temp 0, wind -1, "unknown") are dropped. Domes report a nominal temp |
| attendance_postgame | new | **POST-GAME.** asof = game date + 1 day at 10:00 UTC. Lane 1 may use it only for prior-game features |

Game length (`duration`) is post-game and is never emitted.

## Landed (run 2026-11-05)

| league | seasons | games | facts | new | derived |
|---|---|---|---|---|---|
| NFL | 1999-2026 | 7,548 | 163,413 | 96,398 | 67,015 |
| MLB | 2000-2025 | 61,634 | 555,991 | 494,357 | 61,634 |
| **total** | | 69,182 | 719,404 | 590,755 | 128,649 |

Counts for each season are in `manifest-b1.json`.

## Caveats

- NFL 2026 is in progress. Future games carry only the schedule facts known now: no temp or referee yet.
- `gh api` and api.github.com are blocked here. The officials release was found by probing its download URL.
- The penalty totals in the crew z count accepted penalties from team stats, not every flag thrown.
- The MLB park time-zone table is hand-made, and neutral sites missing from it fall back to the home team's zone.
