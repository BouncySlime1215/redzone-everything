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

The B0a target (30M events) is reached by NBA + NFL + CFB + NHL + men's college over ten seasons (about 43M by this arithmetic,
about 29 hours); the full plan (about 85M) is about 52 hours. Run it in 10 to 12 hour Kaggle sessions or on the Mac in
the background; re-run the same command after each stop. Disk: about 20 KB per game compressed, so about 3.5 GB for 177,000 games.

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
