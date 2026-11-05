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
## Synthetic and teacher rows (Data planner E, EXP-1400..1499)

Every synthetic row is counted apart from real, tagged synthetic=true and "personal-use, not for commercial
release", and the cap is code: `python scripts/synth_build.py check SYN` fails if any league's synthetic tokens
exceed 3x its real tokens (EXP-097). Self-play token equivalents are passed in with `--other-synthetic`.

| Source | Kind | Built from | Where | License | Counts toward 3x cap | Status |
|---|---|---|---|---|---|---|
| Home/away mirror | augmentation, vocab v1 | factory token shards, train nights only (`--train-before`) | SYN/<league>/<year>.mirror.npy | inherits the source shard; personal-use | yes | code ready (EXP-1401) |
| Tempo re-time (live gaps x 0.9-1.1, breaks untouched) | augmentation, vocab v1 | same | SYN/<league>/<year>.retime.npy | inherits; personal-use | yes | code ready (EXP-1401) |
| Teacher 32B answers | teacher-v1 side channel (aux target only) | EXP-086 teacher_out.jsonl, train nights only | SYN/teacher/<league>.npz + text.jsonl | personal-use (Qwen3 Apache-2.0 model; plan B moments) | yes | waiting on the EXP-086 run (EXP-1400) |
| Teacher 32B causal commentary | teacher-v1 mode 2 (may be context) | build_moments train plays, brief only | SYN/teacher/<league>.npz + text.jsonl | personal-use (plan B moments) | yes | notebook ready (EXP-1402) |
| Factory self-play | PICK rows (#122, #152) | real seasons, re-timed slates | factory shards | personal-use | yes, as token equivalents | lane ① runs it |

## Data Division A (play-by-play crew)

New pbp sources land as play-table shards (scripts/pbp/schema.py) and are counted in
[pbp-ledger.md](pbp-ledger.md), generated from the landed shards' sidecars. Every token there is a real event (new
information). Nov 5 2026 05:00 UTC: StatsBomb open data, all 80 competition-seasons, 3,961 matches, 13.9M events,
83.5M v1 tokens, 0 games dropped. Tagged personal-use (StatsBomb user agreement, credit StatsBomb; terms.json class restrictive). Held privately in the
project files under data-a/pbp/statsbomb (135 MB).
Nov 5 2026 06:05 UTC: SportVU NBA tracking 2015-16, 621 games (94 not in the dump, 4 dropped), 2.05M rows of 1 Hz
wall-clock summaries (ball position, speed, handler, live/dead; side table: team speed, spacing, ball height). Counted
as tracking, apart from events. Personal-use tag; derived numbers only, never re-uploaded. data-a/pbp/nba_sportvu + sportvu_extra (103 MB).

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

### Cycle 16 (2026-11-05), crew B: NBA first (Nick 2:00 AM)

| Source | Verdict | Status | Seasons held | Games | Facts (tokens) | New | Derived | License line |
|---|---|---|---|---:|---:|---:|---:|---|
| Our NBA standings, Elo, playoff/play-in odds sims (stakes swing) | derived (hoopR schedules, plan B) | done | 2004-05 to 2026-27 | 28,542 | 832,494 | 0 | 832,494 | derived from hoopR ESPN schedules; personal-use, not for commercial release |
| NBA rest, back-to-back, travel, time-zone shift | derived | done | 2004-05 to 2026-27 | 29,649 | 411,630 | 0 | 411,630 | same |
| NBA national TV (hoopR ESPN broadcast fields) | yellow-B | done | 2019-20 to 2026-27 | 7,621 | 10,526 | 10,526 | 0 | hoopR/ESPN schedules; personal-use, not for commercial release |
| NBA Last Two Minute reports (atlhawksfanatic/L2M) | green | done | 2014-15 to 2024-25 | 4,484 | 35,698 | 26,804 | 8,894 | L2M MIT over public NBA reports; credit L2M and the NBA |
| NBA official injury report archive | yellow-B | code ready (Actions run) | | | | | | NBA official injury report, public; personal-use, not for commercial release |
| Wikipedia pageviews + edits (buzz, test run) | green | done (test) | NFL Oct 5-8 2025 | 13 | 1,352 | 1,352 | 0 | Wikimedia CC0 |
| Wikipedia pageviews + edits (buzz, test run 2) | green | done (test) | NBA + NFL Nov 1-4 2025 | 31 | 3,016 | 3,016 | 0 | Wikimedia CC0 |
| Wikipedia + GDELT news (buzz, test run 3, GDELT fix) | green / yellow (cite GDELT) | done (test) | NBA + NFL Nov 2-4 2025 | 22 | 2,894 | 2,894 | 0 | Wikimedia CC0; GDELT free and open, cite The GDELT Project |
| NFL Next Gen Stats weekly (nflverse release), pregame team form | yellow-B (upstream NFL) | done | NFL 2016-2026 | 2,858 | 44,579 | 44,579 | 0 | nflverse release (CC-BY-4.0) of NFL Next Gen Stats; personal-use, not for commercial release |
| **Cycle 16 total** | | | | | **1,342,189** | **89,171** | **1,253,018** | |
