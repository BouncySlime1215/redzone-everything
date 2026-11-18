# History fetch: grow the play-by-play corpus (B-01)

Do this when: Track B needs more events (corpus is about 6% of the plan) or you want to know what is on disk.
Code: `scripts/fetch_history.py` and `scripts/history/`. Output: `data/history/` (git-ignored, never committed).
Why this exists: V2 plan, Track B section B0 (`redzone-agents/v2/tracks/B-foundation-model.md`) and WORKPLAN row B-01.

## 1. See what you have (no network, 2 seconds)

```sh
python scripts/fetch_history.py --report            # per-league games, events, events/game, wall-clock games, % of the 30M target
python scripts/fetch_history.py --report --by-season
python scripts/fetch_history.py --verify            # ledger vs files; exit 1 on any problem
python scripts/fetch_history.py --licences          # source registry (terms, status, findings); writes MANIFEST.json
```

## 2. Fetch (ESPN)

```sh
python scripts/fetch_history.py --plan --seasons 2025-2016                     # estimate, offline
python scripts/fetch_history.py --accept-restrictive --leagues nba,cbb --seasons 2025-2023 --max-games 200   # a smoke run
python scripts/fetch_history.py --accept-restrictive --leagues nba,cbb,nfl,cfb,wcbb,nhl,mlb --seasons 2026-2017   # the big run
```

- `--accept-restrictive` is required. It records that you accept ESPN's restrictive terms for this personal project
  (decisions.md 2026-11-05 11:20 PM). Every shard keeps the tag `personal-use, not for commercial release`.
- Safe to stop at any moment (Ctrl-C finishes the request in flight; `kill -9` is fine too). Run the same command again:
  finished games are skipped, nothing is fetched twice, a half-written file is never read.
- One writer per folder (`data/history/.lock`). A second run says so and exits.
- Rate: at most 1 request per second overall; the floor is in the code and cannot be lowered. Honest User-Agent
  `redzone-personal-research/1.0`. 429/5xx back off (2, 4, 8 ... seconds, `Retry-After` honoured). A 403 stops the run
  (exit 3). Eight failed requests in a row stop it (exit 4). Any other error is listed and the next run retries it (exit 1).
- Evaluation nights are never fetched: on or after the league's first evaluation night (`scripts/history/leagues.py`,
  equal to `agent/kaggle/sports_common.EVAL_FIRST_NIGHT`) and the locked window 2025-12-12 to 2026-01-05.
- Preseason is skipped unless you pass `--include-preseason`. Games that are not final are skipped.

Expected volume and time for the big run (ESTIMATE, from the B0 arithmetic; the real numbers come from `--report`):

| 10 seasons | games | events | requests | hours at 1/s |
|---|---:|---:|---:|---:|
| NBA | 13,000 | 6.6M | 15,000 | 4.2 |
| Men's college | 59,000 | 30.3M | 60,500 | 16.8 |
| Women's college | 55,000 | 27.5M | 56,500 | 15.7 |
| NFL | 2,850 | 0.5M | 3,100 | 0.9 |
| College football | 8,000 | 1.4M | 8,200 | 2.3 |
| NHL | 14,000 | 4.4M | 16,000 | 4.4 |
| MLB | 25,000 | 14.0M | 27,000 | 7.5 |

Measured in the 2026-11-13 smoke run: 0.87 requests/second sustained (the cap is 1), about 19 KB on disk per NBA game and 13 KB per NFL
game, about 480 plays per NBA game and 187 per NFL game. Hours above are at 1/s; divide by 0.87 for the likely wall time.

Cheaper route for three leagues: the hoopR and wehoop files hold ESPN's own rows for NBA (2002+), men's college (2003+) and women's
college (2005+) in one file per season, so those leagues cost about 11 requests per ten seasons instead of 15,000 to 60,000
(section 3). `--plan` prints both. With the file route for NBA, men's and women's college and ESPN-direct for NFL, college
football, NHL and MLB, ten seasons are about 54,000 requests: 15 hours at 1/s, about 17 at the measured rate. Whichever route is
used, the same game appears in both; B-02 deduplicates across providers (corpus rule 3).

### Run it on Kaggle (the big run; the Mac stays free)

Kaggle can reach ESPN (the Claude cloud container cannot: docs/data-sources.md). Until the Kaggle token exists (N1) the code goes
up through Chrome like the other jobs ([kaggle-run.md](kaggle-run.md)): a private dataset holding `scripts/fetch_history.py` and
`scripts/history/`, a CPU notebook with Internet on, and one cell:

```sh
mkdir -p /kaggle/working/history && cp -r /kaggle/input/<previous-output>/history/. /kaggle/working/history/ 2>/dev/null  # resume
python /kaggle/input/<code>/scripts/fetch_history.py --accept-restrictive --root /kaggle/working/history \
    --leagues nba,cbb,nfl,cfb,wcbb,nhl,mlb --seasons 2026-2017 --max-minutes 660
python /kaggle/input/<code>/scripts/fetch_history.py --report
```

`--max-minutes 660` stops cleanly inside Kaggle's 12-hour session. Each session adds about 39,000 requests (about 11 hours at
1 per second); attach the last output as input to the next session and the same command carries on. No secret is involved.

## 3. hoopR and wehoop files (whole seasons, sha256 pinned)

```sh
python scripts/fetch_history.py --accept-restrictive --source hoopr_nba --seasons 2025-2002
python scripts/fetch_history.py --accept-restrictive --source hoopr_mbb --seasons 2025-2003
python scripts/fetch_history.py --accept-restrictive --source wehoop_wbb --seasons 2025-2005
```

One parquet per season from `raw.githubusercontent.com` (robots.txt there: 404, no rules). A season file that touches a fenced
night is skipped whole (NBA 2026 touches the locked window). The ledger holds bytes, sha256, ETag and, with pyarrow, rows and games.
`nflverse_pbp` (blocked) and `cfbfastr_pbp` (unverified) refuse and print the finding; see `python scripts/fetch_history.py --licences`.

## History depth: how far back ESPN goes (measured 2026-11-13)

Method: `--probe`. For each league and season, read the calendar, then 2 scoreboard units spread through the season and 4 final games
from each (8 games a season; 2 in the first pass); count plays and the share with a wall clock and a game clock. Numbers only; no data kept.
The sample is spread, not random, so read it as indicative. Fenced nights were never sampled. About 1,100 requests in total.

| League | Schedule (calendar) back to | Plays present from | Plays per game, old vs recent | Games with plays (sampled) | Wall clocks from | Game clock |
|---|---|---|---|---|---|---|
| NBA | 1995 or earlier (scan stopped there) | between 2002 and 2004 (2001 and 1998: none) | 2004: 425, 2010: 438, 2019: 483, 2024: 468 | 100% from 2004 | all games 2016+, half of 2013, none 2010 and before | all seasons |
| Men's college | 2001 | about 2006 (2004: none) | 2007: 262, 2010: 329, 2019: 287, 2024: 320 | 2007 50%, 2010 75%, 2013 75%, 2016+ about 100% | 2022+ only (2019 and before: none) | all seasons |
| Women's college | 2001 | about 2010 (2007 and before: almost none) | 2010: 358, 2019: 323, 2024: 328 | 2010 12%, 2013 38%, 2019 80%, 2024 100% | 2022+ only | all seasons |
| NFL | 1999 | 2001 at least (1998: no games) | 2001: 171, 2010: 168, 2019: 178, 2024: 189 | 100% every sampled season 2001 to 2024 | 2016+ (2013 and before: none) | all seasons |
| College football (FBS board) | 1999 | 2001 at least (2004: 6 of 8) | 2001: 206, 2010: 177, 2019: 177, 2024: 181 | 75% to 100% | 2016+ | all seasons |
| NHL | 1996 | 2007 (2004: only 13 plays a game; 2001: none) | 2007: 269, 2010: 295, 2019: 319, 2024: 318 | 100% from 2007 | 2022 or 2023 on (2019 and before: none) | all seasons |
| MLB | 1995 or earlier (scan stopped there) | 2004 (2001 and 1998: none) | 2004: 640, 2010: 645, 2019: 591, 2024: 602 | 100% from 2004 | 2024 only; 2022 about 19% | none (baseball has no clock) |

Reading it:

- Older seasons are not thinner where they exist: plays per game are within about 20% of recent seasons (men's college 2007 and NHL 2007 are the lowest, about 15 to 18% under).
  What older seasons lack is **wall clocks**: before 2016 for NBA, NFL and college football, before 2022 for college basketball, NHL and MLB.
  Those games go in the separate "no wall clock" tier (corpus rule 2); the records keep `wall_frac` so B-02 can split them.
- Coverage is where older seasons are **incomplete**: men's college before 2016 and women's college before 2019 have plays for only
  part of the games (12% to 75%). The fetcher records the rest as `no_plays` and abandons a season whose first 25 games all lack plays.
- Season windows: first season with plays is NBA about 2003, men's college about 2006, women's college about 2010, NFL and college football
  2001 or earlier, NHL 2007 (2004 is nearly empty), MLB 2004. The first seasons were not narrowed to the exact year.
- College plays per game in ESPN summaries are about 320, not the 513 assumed in the Track B arithmetic, so the college event
  totals there are probably about 35% too high for ESPN-direct (sample of 8 to 16 games a season; hoopR rows per game were not measured for college).
- Individual games can be missing plays in any season (one of two sampled NBA 2022 and NHL 2022 games had none).

## 4. Pack for Corpus v1 (B-02)

```sh
python scripts/fetch_history.py --pack     # data/history/shards/espn_<league>_<season>.jsonl.gz + MANIFEST.json
python scripts/license_check.py data/history/MANIFEST.json
```

Shards are deterministic (same games, same sha256). Each line is one game: `id`, `plays`, `matchup`, `date`, so
`scripts/noggin_tokens.games_of` reads them as is. Games with no wall clock are stored too; `wall_frac` on each record
and `wall_games` per shard let B-02 put them in the separate tier (corpus rule 2). B-02 still applies the F-09 leak fence.

## 5. What the records keep

Plays only, pruned: ids, type, clock, period, scores, team id, athlete ids, wall clock. Dropped: names, headshots, links,
and the hindsight fields (win probability, odds, box score), so nothing here can leak the final state into a feature.

## 6. Licences and the robots finding (read 2026-11-13)

- ESPN API hosts (`site.api.espn.com`, `sports.core.api.espn.com`, `site.web.api.espn.com`): `robots.txt` answers 403, a CDN
  deny page, not a rules file. RFC 9309 and the older `sports_common.PoliteFetcher` read a 4xx as "no rules"; the Nov 9 routes
  note ([2026-11-09-allowed-routes.md](../../agents/data/2026-11-09-allowed-routes.md)) read it as a bot block. This tool follows
  the first reading, records the answer in `data/history/robots_espn.json` and the manifest, and stops on any 403 from a real
  call. Disney's Terms of Use (section 2.B, quoted in that note) prohibit automated extraction. Nick's Nov 5 ruling allows
  ESPN-derived data for personal use; the strict reading is his call. To switch ESPN off, set its `status` to `blocked` in
  `scripts/history/licences.py`.
- hoopR and wehoop data repos: CC BY 4.0 (LICENSE.md read); the underlying ESPN rows keep the personal-use tag.
- nflverse: CC BY 4.0, but the only documented download is on github.com, whose robots.txt closes it. Needs Nick's call on route A
  (api.github.com, 60 requests an hour) in the Nov 9 note. Not fetched.
- cfbfastR-data: no LICENSE file. Not fetched.

## 7. If it goes wrong

| What you see | Cause | Do |
|---|---|---|
| `another fetch is running` | a second run on the same folder | wait, or stop the other one |
| exit 3, `BLOCKED: HTTP 403` | the host refused us | stop; do not change the User-Agent; tell Nick |
| exit 4, `circuit breaker` | 8 failed requests in a row | check the network; rerun later |
| `TLS verification failed ... install certifi` | no CA bundle | `pip install certifi` (httpx already depends on it) |
| `--verify` lists a file with no ledger line | a kill between the file and the ledger | rerun; the next fetch adopts it |
| `refusing to write ... not git-ignored` | `--root` inside a repo that would track it | use the default or a folder outside the repo |
