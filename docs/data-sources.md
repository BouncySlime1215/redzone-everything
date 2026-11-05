# Data sources: license, terms and rate limits

Owner: lane 1 (Noggin training). Serves: North Star "% of hindsight-oracle value achieved", through data scale (Nick, Nov 4
5:00 PM: data is the #1 bottleneck). **Rule:** a source gets a row here, with its license and terms, BEFORE its first pull.
Only free, open or personal-use sources. No paid feeds, no scraping of a site whose terms forbid it, no redistribution
beyond private Kaggle datasets. Keys are added by Nick as Kaggle secrets; no code or doc ever holds a key's value.

Every source is its own resumable shard family in the factory (`scripts/kaggle_noggin_factory.py`): one job per
league and season, with a `done` marker per shard, so a run that stops resumes where it left off.

## Status

| source | leagues | seasons | license / terms | rate limit we keep | status |
|---|---|---|---|---|---|
| nflverse-data (nflfastR pbp releases) | NFL | 1999-2025 | data CC-BY 4.0 (attribution: "nflverse / nflfastR"); nflfastR code MIT | GitHub release downloads, 1 file per season | **in use**: tokens 1999+, moments 2011+ (no snap clock before 2011: NFL 1999 has 0% `time_of_day`, 2005 has 94%) |
| sportsdataverse-data: hoopR | NBA, CBB (men's D-I) | NBA 2002+, CBB 2006+ | MIT (repo); the underlying pbp is ESPN's, personal non-commercial use | GitHub release downloads | **in use** |
| sportsdataverse-data: wehoop | WCBB | 2006+ | MIT (repo); underlying ESPN data as above | GitHub release downloads | **dropped** (Nick, Nov 4 7:55 PM: not one of his leagues; out of every pack and the ledger) |
| ESPN site API, FBS board (`groups=80`) | CFB | 2004-2025 | as the ESPN row below | as below | **in use** from pack 610dde4's successor; cut 20250801 (the 2025 season stays unseen) |
| cfbfastR-data | CFB | 2002-2020 only | repo has no license file; CFBD-derived, no wall clocks | | not used (stale; ESPN covers it with wall clocks) |
| ESPN site API (`site.api.espn.com`) | all | per league | no published license; personal, non-commercial use only | ≤ 1 request/s, cached per game, from Kaggle only (blocked from the cloud container) | **in use** (the live parser's own feed) |
| NHL api-web (`api-web.nhle.com` pbp, `api.nhle.com` shift charts) | NHL | 2011-2019 (where ESPN has no wall clocks) | no published terms; public, unauthenticated | ≤ 2 requests/s per job (`nhl_apiweb.MIN_GAP_S`), cached in the season file | **in the plan** (pack after 30ca1c5): tokens with game-clock DT; shifts kept for vocab v2. First Kaggle run checks robots.txt |
| collegefootballdata.com (CFBD) | CFB | 2001+ | free tier with an API key; their terms ask for attribution | the free tier's monthly call budget; cache every response | **blocked on Nick**: he adds the key as a Kaggle secret (we never see its value) |
| Baseball Savant / Statcast | MLB | 2015+ | MLB's terms of use restrict automated access for commercial use; personal research use is common but not explicitly granted | ≤ 1 request/2 s, cached | **skipped** (Nick, Nov 4 6:10 PM: terms unclear, MLB is last and out of season) |
| Retrosheet event files | MLB | 1910s+ | free with the attribution notice Retrosheet requires | static downloads | candidate (no pitch data, but full event history) |
| Live nights (Nick's own app logs) | all | from now | our own data | none | **gold data**: the archive the app already records; tagged `real_live` in the ledger |
| Self-play slates (`noggin_selfplay`) | all | derived | synthetic, from the above | none | in use; ×5-10 per real night, counted separately as synthetic |

## Rules for every pull

1. **Leakage:** nights on or after `factory.EVAL_FIRST_NIGHT` are never downloaded or tokenized, and the locked and scorecard
   nights (`split.BIG_CUTS` / `locked_v1`) never enter a training shard, whatever the source.
2. **Rate limits:** the per-source limit above is a floor on politeness, not a target. Every response is cached, so a
   re-run never re-downloads.
3. **Attribution:** CC-BY sources are credited in docs/noggin-foundation-model.md and in the Kaggle dataset description.
4. **Redistribution:** shards go only to private Kaggle datasets under Nick's account.
5. **The ledger:** every factory run prints a DATA LEDGER (tokens per league and source, real vs synthetic, crunch, OT,
   comeback and blowout coverage), it is saved in `manifest.json["ledger"]`, and every factory PR quotes it.

## Reachability from the cloud container (checked Nov 4 2026)

raw.githubusercontent.com and GitHub release downloads work. ESPN, api-web.nhle.com, baseballsavant.mlb.com and
api.collegefootballdata.com do not connect, so every pull from those runs on Kaggle.
