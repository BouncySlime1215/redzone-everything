# Context: BUZZ (Wikipedia pageviews, Wikipedia edits, GDELT news)

Data Crew B (context and hype), worker B2. Team-level "how much attention is on this team right now"
signals, landed as context facts in the vocab v2 hand-off format (`redzone/context/tokens.py`: one parquet
per league-season, columns `game_id, team, asof_utc, field, value, value_str, bucket, kind, source, license`).
All rows are `kind = new` (external information we did not hold). Buckets are left null: the planner fits
them on TRAIN games only (`tokens.fit_edges`).

Runs only on GitHub Actions (`.github/workflows/context-buzz.yml`) or Kaggle CPU; never on the Mac.

## Sources and license lines

| source id | endpoint | what we read | license line (in every row, plus the personal-use tag) |
|---|---|---|---|
| `wikimedia-pageviews` | REST `wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}` (from 2015-07-01); hourly dumps `dumps.wikimedia.org/other/pageviews/YYYY/YYYY-MM/pageviews-YYYYMMDD-HH0000.gz` | daily / hourly view counts of each team article (+ its former titles; `en` + `en.m` lines in dumps) | `Wikimedia pageview data CC0` |
| `wikimedia-edits` | EventStreams SSE `stream.wikimedia.org/v2/stream/recentchange`; MediaWiki API `prop=revisions&rvprop=timestamp` | edit timestamps of each team article (wiki=enwiki, namespace 0) | `Wikimedia EventStreams, CC0 metadata` |
| `gdelt-gkg` | GDELT 2.0 GKG 15-min files `data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip` (+ `lastupdate.txt`) | per article: organizations, all-names, page title (matched, then discarded), average tone | `GDELT: free and open, cite The GDELT Project` |

Every license line is suffixed with `personal-use, not for commercial release` (B-plan rule). Team metadata
comes from Wikidata (CC0) and the link lists of two Wikipedia list pages (titles only).

Never stored: raw dump/zip files (streamed and dropped in memory), article text, URLs, page titles, names,
usernames, IPs, edit comments. Only per-team counts per time bin.

Politeness: User-Agent `redzone-research/0.1 (personal project; github.com/BouncySlime1215/redzone-everything)`
on every request (also `Api-User-Agent`); Wikimedia <= 5 req/s (they allow 100), one dump download at a time;
GDELT <= 1 file/s, sequential; exponential backoff and `Retry-After` on 429/5xx; small API responses cached.

## Fields

| field | when (asof_utc) | value |
|---|---|---|
| `pageviews_z` | kickoff - 1 s | views of the last full UTC day before game day, z vs the 28 days before it |
| `edit_rate_z` | kickoff - 1 s | edits in the 24 h ending at the last 15-min boundary <= asof, z vs the 28 days before (same-length windows) |
| `news_volume_z` | kickoff - 1 s | GDELT articles mentioning the team, same 24 h window, z vs the 28 days before |
| `news_tone` | kickoff - 1 s | mean GDELT tone of those articles (raw, about -10..+10) |
| `pageviews_live_z` | end of each pageview hour | that hour's views x 24 vs the 28 full UTC days before the hour's day |
| `edit_rate_live_z` | end of each 15-min bin | edits in that bin vs the 15-min bins of the previous 28 days |
| `news_volume_live_z` | each GDELT file time (bin end) | articles in that 15-min file vs the previous 28 days of files |
| `news_tone_live` | each GDELT file time | mean tone of that file's articles for the team (only if >= 1 article) |

The four pregame names are the ones the crew plan uses; the `_live` names are separate fields because a
15-min/1-h z has a different spread than a 24-h z (the planner can merge or rename them).
Live facts cover kickoff - 6 h to kickoff + game length (NFL 4 h, CFB 4.5 h, NBA 3 h, CBB 2.5 h, NHL 3 h,
MLB 4 h) and carry the game_id of the game they lead up to / are in.

## As-of rules (leakage)

- A time bin is usable only from its END. A window `[a, b)` uses bins with start >= a and end <= b, and
  b <= asof_utc always.
- Baselines are the same-length windows of the 28 days strictly before `a`; a z needs at least
  max(5, K/8) covered baseline windows (pageviews_live_z: 7 days), else no row.
- Coverage is tracked per source (and per team for REST pageviews): bins we never looked at are unknown,
  not zero. Window values are rates per covered bin, so sampled GDELT backfill (stride) stays unbiased.
- Pageview dump `HH` is treated as the START of the hour (usable at HH+1:00); if Wikimedia's stamp is the
  hour's end, we are one hour late, never early.
- GDELT file stamped T covers [T - 15 min, T) and is usable at T.
- z is clipped to +-10 and the std is floored at 1e-3 + 5% of the mean (sparse edit counts).
- Tests: `tests/test_context_buzz.py` perturbs every value at/after asof and checks the facts do not move.

## Teams

- `redzone/context/teams_seed.csv`: hand-checked NFL 32 / NBA 30 / NHL 32 (+ Arizona Coyotes, inactive,
  for history) / MLB 30, with the abbr each league's play tables use (nflverse `LA`, `LV`, `WAS`; NBA `BKN`,
  `GSW`; NHL `UTA`, `VGK`; MLB `ATH`, `CWS`), the enwiki title, former titles of moved articles
  (Washington_Redskins, Oakland_Raiders, San_Diego_Chargers, St._Louis_Rams, Cleveland_Indians,
  Oakland_Athletics, Utah_Hockey_Club) and former abbrs (OAK, SD, STL, ...). Lets the pullers run before
  the Wikidata step.
- `scripts/context/wikidata_teams.py` -> `data/context/teams.csv` + `rivalries.csv`: resolves the seed
  titles (redirects -> moved titles become former_titles), adds Wikidata QID, current home venue (P115 with
  no end time) and its P625 coordinates; adds CFB (FBS) and CBB (D-I men's) programs from the link lists of
  "List of NCAA Division I FBS football programs" / "List of NCAA Division I men's basketball programs".
  College `abbr` = program name ("North Carolina Tar Heels"); schedule strings like "North Carolina"
  resolve by unique prefix. Rivalries: items of class "sports rivalry" with P1923/P710 participants (may be
  sparse; empty file is fine).
- GDELT matching uses multi-word names only (full name, aliases, title text); one-word nicknames
  ("Giants", "Kings") never match. A CFB and CBB program of the same school share a name, so both get the
  article (it is about the school's program; only that league's games use it).

## How to run

```sh
# offline tests (no network)
python -m pytest -q tests/test_context_buzz.py

# on a runner / Kaggle
python scripts/context/wikidata_teams.py --out data/context
python scripts/context/run_buzz.py live
python scripts/context/run_buzz.py backfill --from 2023-10-01 --to 2023-11-01 --max-files 600 --stride 4 \
    --schedule nba_2023.csv            # non-NFL backfill needs schedule CSVs (game_id,league,home,away,start_utc)
# single sources
python scripts/context/wiki_pageviews.py history --from 2015-07-01 --to 2025-07-01
python scripts/context/wiki_edits.py listen --seconds 3300      # long-running SSE listener (Kaggle)
python scripts/context/gdelt_buzz.py backfill --from 2024-01-01 --to 2024-01-08 --max-files 200
```

GitHub Actions: push a branch `data-b-run/<from>_<to>` (e.g. `data-b-run/2023-10-01_2023-11-01`), or put
`scripts/context/buzz_chunk.json` (`{"from","to","max_files","stride","leagues"}`) on any `data-b-run/**`
branch. Output goes to `data-b-out/backfill-<from>_<to>` (re-push the same branch to resume: covered files
are skipped). Hourly live: GitHub fires `schedule` only from the default branch and we never merge to main, so live runs come from a Kaggle scheduled notebook calling `run_buzz.py live` (or a workflow_dispatch); output goes to `data-b-out/live`.
Both also upload a 30-day artifact. Out branches hold `facts/<league>_<season>.parquet`, the count signals
and coverage (needed to resume and for baselines), `manifest_<mode>.json` and `meta/teams.csv`.

Schedules: NFL from nflverse `games.csv` (its game_ids); live mode adds ESPN scoreboards for the other
leagues with game_id `espn_<league>_<id>` (the planner crosswalks them to the play-table ids). Season =
start year (MLB: calendar year).

## Cadence, cost and size

| pull | cadence | cost |
|---|---|---|
| REST daily views | once for history (one request per title covers 2015-now), then per team playing within 36 h | ~630 requests for all history |
| hourly dump | live: newest 1-2 hours each run; backfill: hours around games, `--hour-files` per run | ~40-70 MB streamed per hour file, ~10 s parse |
| revisions | live: all titles since the last covered bin each hour (~630 small requests, ~2 min); backfill: 29 days before the chunk + the chunk | small |
| GDELT GKG | live: newest 4 files; backfill: every `--stride` file | ~10-30 MB per file, 2-4 s; one year at stride 4 = 8,760 files, ~130 GB transfer, ~6-10 runner-hours |
| SSE | optional long-running listener (`wiki_edits.py listen`) | whole recentchange stream, filtered in memory |

Teams: ~124 pro + ~134 FBS + ~364 D-I men's basketball = ~620 titles.

Facts per game (both teams): 8 pregame + live (per team: hours + 2 x 15-min bins + tone bins over the
6 h + game-length window). Full coverage estimate per season (tone fires on ~50% of bins):

| league | games/season | facts/game | facts/season (= tokens, 1 per fact) | parquet |
|---|---|---|---|---|
| NFL | 285 | ~250 | ~70k | ~1.5 MB |
| CFB (FBS) | ~800 | ~260 | ~210k | ~4 MB |
| NBA | ~1,320 | ~210 | ~280k | ~5 MB |
| CBB (D-I) | ~5,500 | ~180 | ~1.0M | ~18 MB |
| NHL | ~1,400 | ~210 | ~290k | ~5 MB |
| MLB | ~2,475 | ~250 | ~620k | ~11 MB |

Backfill at GDELT stride 4 gives a quarter of the 15-min GDELT live rows, and hourly pageview facts only
for the dump hours processed; 2016-2025 x 6 leagues at full coverage is ~25M facts. Signals (team counts)
add ~10-15 MB per year of GDELT at stride 4, ~2 MB per year of pageviews and edits.

## Open items for the first live run

- Confirm the pageview dump hour convention (start vs end of hour) on one file against REST hourly.
- Check `teams.csv`: moved titles reported in the log, college program count (~134 FBS / ~364 D-I),
  any college schedule names that do not resolve (logged as "unresolved team names").
- Rivalry coverage on Wikidata (possibly empty).
- GDELT `http://` only (their TLS has been unreliable); a 404 for a 15-min slot is normal (skipped).
