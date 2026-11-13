# Feed-arrival logger (C-02)

A low-priority sidecar that logs, for every row ESPN's summary and the NBA liveData feed publish, **when we first
saw it**. Replays assume an 8 s feed; measured, ESPN is about 36 s and the NBA feed about 11 s. The show never imports
it, touches no TV and no stick, and needs only plain `python3` (standard library). Code: `scripts/feedlag_logger.py`,
`redzone/lab/feedlag.py`. Numbers: `scripts/feedlag_report.py`, `redzone/lab/feedlag_report.py`.

## Start (about 1 minute)

From the repo folder, any time before the night's first tip (it also joins games already under way):

```sh
mkdir -p data/feedlag
nohup taskpolicy -b nice -n 19 python3 scripts/feedlag_logger.py --exit-when-done > data/feedlag/logger.out 2>&1 &
```

`--exit-when-done` ends it by itself after the last game of the night plus a 10-minute grace for late rows; the
default cap is 14 hours (`--max-hours`). Check it: `tail -f data/feedlag/logger.out` (one status line a minute) and
`wc -l data/feedlag/$(date +%Y-%m-%d).jsonl`. Plan without polling a game: `python3 scripts/feedlag_logger.py --dry-run`.

Options: `--leagues nba,cbb` adds ranked college basketball (ESPN only, at most `--cbb-max-games 4` at once);
`--sources espn_summary` drops the NBA feed; `--date YYYY-MM-DD` follows another slate.

## Stop

```sh
python3 scripts/feedlag_logger.py --stop
```

Sends SIGTERM to the pid in `data/feedlag/logger.pid` (checked to be this script first); it writes a `stop` row and
exits within a second. Starting twice is refused while the first runs. A restart the same night appends to the same
file and never logs a row twice.

## What it does to the feeds (all enforced in code)

- At most one request per **10 s per game and source** (the `--interval` floor; a lower value is raised to 10).
- At most `--max-rps` (default 3, **hard ceiling 4**) requests per second across everything. On a big night the cap
  wins and each feed is polled a little less often; arrival windows get wider, never the request rate higher.
- Backoff per host on 429, 5xx, 403 (ESPN), timeouts and network errors: 30 s doubling to 10 min, `Retry-After`
  honoured. A game whose NBA file is not up yet (S3 says 403/404 before the tip) backs off that game alone (15 s to 1 min).
- Conditional GETs (ETag / If-Modified-Since) on the game feeds (scoreboards are always asked for whole), gzip, an 8 s timeout, an 8 MB cap per response.
- `User-Agent: feedlag-logger/1 (low-rate metadata sidecar; at most one request per 10 s per game)`. No name, no project.
- The NBA feed is the same S3 mirror of liveData the show already polls (`redzone/sources/leagues.py`, `sources.nba_primary`).
  Nick's terms-of-use decision N4 is about adding *new* NBA CDN use; this adds one more 10-second poller on an endpoint
  the show already uses. If N4 says no, run with `--sources espn_summary`.

## What it writes

`data/feedlag/YYYY-MM-DD.jsonl`, one night per file (US Eastern, a night ends at 06:00), append only, git-ignored
(`data/`). **Metadata only**: no text, no player or team names, no scores. One JSON line per event:

| `ev` | meaning |
|---|---|
| `row` | a feed row first seen: `t` (we saw it), `prev` (we sent the poll before), `src`, `lg`, `game` (ESPN event id), `row`, `kind`, `typ`, `stop`, `stamp` (the row's own wallclock), `per`, `clk`, `pub` (NBA feed's own publish time) |
| `track` / `drop` / `game` | a game's feed started or ended being followed; `game.baseline` = rows that already existed (history, not logged) |
| `clock` | server `Date` minus our clock (a Mac clock off by more than a second would bias every lag) |
| `err` / `start` / `stop` / `slate` | backoffs and their delay, the run's settings, how it ended |

Row `kind`: shot_made, shot_miss, free_throw, rebound, turnover, foul, timeout, sub, period_start, period_end, game_end,
jumpball, violation, review, steal, block, other. `stop` is the show's own label of what the game does after the row
(`none` = live play; anything else is a dead ball).

## Read the results (about 1 minute)

```sh
.venv/bin/python scripts/feedlag_report.py                       # every night found; or --nights 2026-11-12
.venv/bin/python scripts/feedlag_report.py --write-profile       # + data/feedlag/replay_profile.json
```

(The report needs the repo's venv because it reuses `redzone.lab.timing`.) It prints, per source and per row kind: rows,
p10/p50/p90/p95/p99/max lag in seconds, and the share inside the assumed 8 s; then the **C2 coverage check**: 5+ nights,
10+ NBA games (50+ usable rows each), both sources.

**How to read the lag.** A row was published somewhere in (`prev`, `t`], so the lag is known to within one poll gap
(about 10 s). `mid` (the table) is the middle of that window minus the row's stamp; `obs` is `t - stamp`, an upper
bound that runs about 5 s high. Rows whose window is wider than 30 s (the Mac asleep, a host in backoff) are left out and
counted. Feed stamps are second-resolution for ESPN. If `clock` rows show more than ±1 s of skew, fix the Mac clock first.

**Replay input.** `--write-profile` copies `redzone/lab/feed_lag_profile.json`, replaces ESPN's NBA/college entries and the
`nba_cdn` entry with the measured lags where there are 50+ usable rows, marks them `from: feedlag`, and keeps everything
else. It never overwrites the committed file. To replay with it: `timing.LagSampler(timing.load_profile(Path("data/feedlag/replay_profile.json")), use={"nba": "nba_cdn"})`.
Putting it in place for good is a reviewed change (a number every other timing track quotes). `--basis obs` builds it from the upper bound.

## If something looks wrong

- No rows after a game started: `grep '"ev":"err"' data/feedlag/*.jsonl | tail` (a backoff names the host and delay).
- `track` rows but no `row` rows for `nba_cdn`: the NBA file appears only at the tip; check `logger.out` for the status line.
- Two copies: `--stop`, then start once. A stale pid file is cleaned by `--stop`.
- Done condition not reached (it keeps running after the last game): stop it by hand, or rely on the 14 h cap.
