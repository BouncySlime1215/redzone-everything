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

### Cycle 14 (2026-11-05), Data F: broadcast formats

| Source | Wave | Verdict | Status (planned / pulling / done / blocked) | Seasons held | Rows | Fields | Values added this cycle | Values held (cumulative) | Stored GB | Kaggle shard / version | HF backup (yes / no, not allowed) | Attribution in README + model card (yes / no) | Nick action needed | Notes |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| League rulebooks + ops pages (NBA Rule 5, NCAA MBB/FB, MLB pace of play; Wikipedia for gaps) | 1 | green (facts, paraphrased) | done (v1) | 2025-26 / 2026 rules | 27 | 10 | 270 | 270 | 0.00 | repo `redzone/scoring/broadcast/break_rules.yaml` | yes | yes (source_url per row) | none | NFL and CFB TV formats mostly unverified; NHL marks from secondary sources |
| Measured break lengths (eval-data-v1 archive, ESPN summaries, train nights) | 1 | yellow (ESPN-derived, plan B personal-use) | done (v1) | NFL 2025, NBA/NHL Oct 2026 | 14,169 breaks → 120 cells | 7 | 840 | 840 | 0.00 | repo `redzone/scoring/broadcast/break_lengths.json` | no, not allowed | yes | none | Derived numbers only; locked nights never read |
