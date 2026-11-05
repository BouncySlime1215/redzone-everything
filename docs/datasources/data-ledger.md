# Data ledger

Planners fill in one block per cycle. Sources and verdicts: [sources-catalog.md](sources-catalog.md).
Waves and targets: [ingest-plan.md](ingest-plan.md).

Rules: count realistic values (rows x populated fields), de-duplicated against what is already
held. Synthetic is always its own row. Never list a red source. Never write keys or tokens here.

## Cycle template

Copy this block, set the cycle number and date, and fill every column.

### Cycle NN (YYYY-MM-DD)

| Source | Wave | Verdict | Status (planned / pulling / done / blocked) | Seasons held | Rows | Fields | Values added this cycle | Values held (cumulative) | Stored GB | Kaggle shard / version | HF backup (yes / no, not allowed) | Attribution in README + model card (yes / no) | Nick action needed | Notes |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| example: Retrosheet events | 1 | green | done | 1950-2025 | 15.0M | 100 | 1.50B | 1.50B | 2.0 | rz-data/v3 shard retro-01 | yes | yes | none | notice text added |

### Cycle totals

| Bucket | Values added | Values held | Stored GB |
|---|---:|---:|---:|
| Green, observed | | | |
| Yellow, observed | | | |
| Derived (green + yellow) | | | |
| ESPN-gated (only if Nick approved) | | | |
| Synthetic (cache) | | | |
| **Total** | | | |
| Quota used / 215 GB (working cap 120 GB) | | | |

### Saturation check

| Check | This cycle | Last cycle | Stop? |
|---|---|---|---|
| Distinct real events held | | | |
| Distinct real slate-nights held | | | |
| Synthetic : real token ratio (stop near 3x) | | | |
| Held-out real-night world-model loss after the last 2x synthetic step (stop if < 1% better twice) | | | |
| Distinct synthetic slates (stop near 1-2M) | | | |

### Blockers and decisions

- Waiting on Nick (keys, Rules tabs, sign-offs), with the date asked:
- Decisions logged in docs/memory/decisions.md this cycle:
- License changes spotted (re-check any source whose terms changed):

### Cycle 10 (2026-11-05), lane 1 rows

| Source | Wave | Verdict | Status (planned / pulling / done / blocked) | Seasons held | Rows | Fields | Values added this cycle | Values held (cumulative) | Stored GB | Kaggle shard / version | HF backup (yes / no, not allowed) | Attribution in README + model card (yes / no) | Nick action needed | Notes |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| Retrosheet events (Chadwick Bureau GitHub mirror) | 1 | green | pulling (next factory run) | 1974-2024 planned (2025+ held out) | ~12M events est. (2023 measured: 249,395 events, 2,430 games) | 6 tokens/event | 0 until the Kaggle run | 0 | ~0.15 est. (tokens only; raw deleted per season) | factory pack after #169 | yes | yes (`retrosheet_plays.NOTICE`) | none | 2023 check: home runs exact (5,868), runs 22,416 of 22,432 (99.9%), 0 ties. No wall clocks: dt tokens UNK |
| nflverse pbp 1999-2024 | 2 | yellow (attribution, private raw) | pulling (already in the factory plan) | 1999-2024 | ~1.2M plays | 6 tokens/event | 0 until the Kaggle run | 0 | ~0.1 tokens | factory pack | no (private raw) | yes | none | 1999-2000 have no wall clocks: tokens use the game-clock dt fallback; the world model pilot starts at 2001 |
| NHL shift charts (api.nhle.com) | 2 | blocked | blocked | none | 0 | | 0 | 0 | 0 | | | | yes: the open NHL card (MoneyPuck or api-web) | Same NHL.com terms issue as api-web; held with it |
| hoopR NBA / MBB (ESPN-derived) | 5 | yellow, ESPN-gated | blocked for new packs until Nick decides | (in pack 79fd9c0 shards) | | | 0 | (existing shards) | | 79fd9c0 | not allowed | yes | yes: the pilot card (Nov 5) | The world model pilot defaults to NFL only; NBA needs --allow-espn-derived |

## Data Division A (play-by-play crew)

New pbp sources land as play-table shards (scripts/pbp/schema.py) and are counted in
[pbp-ledger.md](pbp-ledger.md), generated from the landed shards' sidecars. Every token there is a real event (new
information). Nov 5 2026 05:00 UTC: StatsBomb open data, all 80 competition-seasons, 3,961 matches, 13.9M events,
83.5M v1 tokens, 0 games dropped. Tagged personal-use (StatsBomb user agreement, credit StatsBomb; terms.json class restrictive). Held privately in the
project files under data-a/pbp/statsbomb (135 MB).
Nov 5 2026 06:05 UTC: SportVU NBA tracking 2015-16, 621 games (94 not in the dump, 4 dropped), 2.05M rows of 1 Hz
wall-clock summaries (ball position, speed, handler, live/dead; side table: team speed, spacing, ball height). Counted
as tracking, apart from events. Personal-use tag; derived numbers only, never re-uploaded. data-a/pbp/nba_sportvu + sportvu_extra (103 MB).
Nov 5 2026 06:40 UTC: NBA.com stats pbp for those 621 games (287k events, 10-digit ids, joins SportVU). StatsBomb
American football (#TB12DB): 108 NFL games 2017-22, 729k events (about 60% are pathing/engagement annotations; ~0.4k
core play actions per game), plus low-frequency tracking 2021-22 (37 games, 31k rows, counted as tracking). Personal-use tag.
