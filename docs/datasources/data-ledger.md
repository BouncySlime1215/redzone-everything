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

### Cycle 14 (2026-11-05), crew B (context and hype): first landing (EXP-1100)

Context facts in lane ①'s vocab v2 hand-off format ([context-tokens.md](context-tokens.md)). One fact = one token
per game or team; `new` (external facts) and `derived` (computed from data we hold) are counted apart. Lines are crew
D's and are not in these rows. Data is in the private project folder (`data-b/shards/`), not in this repo.

| Source | Verdict | Status | Seasons held | Games | Facts (tokens) | New | Derived | License line |
|---|---|---|---|---:|---:|---:|---:|---|
| nflverse schedules, injuries, officials, team stats | yellow | done | NFL 1999-2026 | 7,548 | 163,413 | 96,398 | 67,015 | nflverse CC-BY-4.0, credit nflverse |
| Retrosheet game logs + event-file `info` records | yellow | done | MLB 2000-2025 | 61,634 | 555,991 | 494,357 | 61,634 | Retrosheet: free with its required notice ("The information used here was obtained free of charge from and is copyrighted by Retrosheet...") |
| Our standings, Elo and playoff-odds sims (stakes swing) | yellow (derived) | done | NFL 1999-2026 | 7,413 | 208,488 | 0 | 208,488 | derived from nflverse schedules (CC-BY-4.0) |
| Our standings, Elo and playoff-odds sims (stakes swing) | yellow (derived) | done | MLB 2000-2025 | 61,634 | 1,787,386 | 0 | 1,787,386 | derived from Retrosheet game logs (Retrosheet notice) |
| Open-Meteo archive weather (NFL, MLB outdoor) | yellow (CC BY 4.0, non-commercial) | planned (cloud run) | | | | | | Open-Meteo CC BY 4.0, credit Open-Meteo |
| CFBD media, rankings, records, talent, games | yellow | blocked (Nick's CFBD key) | | | | | | CollegeFootballData.com free tier, credit CFBD |
| **Crew B total** | | | | 69,182 | **2,715,278** | **590,755** | **2,124,523** | |
