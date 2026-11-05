# Tokenizer v2: where each context field comes from (scout, Nov 5 2026)

Nick, 11:50 PM Nov 4: expand each play from ~6-10 tokens to 50-100 with the state at that instant, and count
**new-information** tokens (external facts) apart from **derived** tokens (computed from pbp we already hold).
Lane 1 owns the tokenizer; this page is the scout's map of sources for each field. Ledger rows: `v2-*` in
[scout.json](scout.json), rendered in [scout-ledger.md](scout-ledger.md). Viewer logs are out (Nick 11:50 PM);
only Nick's own live nights stay as gold labels.

Rule for every context field: take the value **as known at that instant** (the injury report as of kickoff, the
line before the play, standings before the game). Anything later is leakage and fails the grader's time-split audit.

| v2 field | Kind | NFL | CFB | NBA | CBB | NHL | MLB | Verdict | Gap |
|---|---|---|---|---|---|---|---|---|---|
| WP before/after, leverage | derived | nflverse wp / our model | our model (CFBD WP with key) | our model | our model | our model | our Retrosheet RE model | green | none |
| Expected points | derived | nflverse ep | CFBD ppa (key) or ours | n/a | n/a | n/a | run expectancy (ours) | green | none |
| Margin, run, lead changes | derived | pbp | pbp | pbp | pbp | pbp | pbp | inherits source | none |
| Timeouts / fouls / bonus / challenges left | derived | pbp timeout cols | pbp | pbp (fouls from events) | pbp | n/a (one timeout) | mound visits from GUMBO (B) | inherits | CBB media timeouts from rule YAML |
| Lineup on field/floor/ice + minutes, rest | derived | nflverse participation 2016+ | none | shufinskiy pbpstats lineups | none | api-web shifts (B) | lineup from GUMBO/Retrosheet | yellow / yellow-B | CFB, CBB lineups |
| Player season-to-date, career | derived | nflverse player stats | CFBD (key) | shufinskiy box | hoopR box (B) | api-web (B) / MoneyPuck | Retrosheet / Lahman | inherits | none |
| Possession / drive state, pace | derived | pbp | pbp | pbp | pbp | pbp | outs/base state | inherits | none |
| Standings, playoff odds, rivalry | derived | our Monte Carlo over schedules; Wikidata rivalries (CC0) | same | same | same | same | same | green | none |
| Rest, travel, timezone, altitude | derived | schedules + GeoNames/USGS | same | same | same | same | same | yellow (CC BY) | none |
| Ref crew tendencies | derived from crew ids | flags/game per crew | none | whistles/game per crew | none | penalties/game per crew | ump zone size (Statcast B) | inherits | CFB, CBB |
| Streaks, records in reach | derived | nflverse + history | CFBD | shufinskiy | hoopR (B) | api-web (B) | Lahman + Retrosheet | inherits | none |
| Play-text tokens | derived | pbp desc | pbp desc | pbp desc | pbp desc | pbp desc | pbp desc | inherits; numbers and fixed vocab only, no free text | none |
| **Pregame line + total** | **new** | nflverse schedules | CFBD /lines (key pending) | SBRO via Wayback 2007-22; ESPN pickcenter (B) 2022+ | same | same | same | yellow / yellow-B | CFB until Nick's CFBD key |
| **Live line state** | **new** | Polymarket 1-min prices (B) 2024+ | same | same | same | same | same | yellow-B | nothing free before 2024 |
| **Weather / venue** | **new** | NOAA GHCNh + nflverse roof/surface | NOAA + OSM venue | indoor | indoor | indoor | NOAA + park factors | green | none |
| **Ref / ump crew ids** | **new** | nflverse officials 2015+ | none found | nbadb officials | none found | api-web officials (B) | Retrosheet umps | yellow / yellow-B | CFB, CBB |
| **Injury status at game time** | **new** | nflverse injuries 2009+ | none | NBA official report archive 2018-12+ | ESPN injuries (B) | ESPN injuries (B) | ESPN injuries (B) | yellow / yellow-B | NBA before Dec 2018 |
| **National TV slot** | **new** | nflverse schedules | Wikipedia tables | Wikipedia tables | Wikipedia tables | Wikipedia tables | Wikipedia tables | yellow (CC BY-SA facts) | patchy before 2010 |

Honest count (scout estimate, cycle 13): new-information context adds ~24M unique tokens (one copy per game);
fed on every play it is ~0.4-0.6B tokens, but that repetition is not new knowledge. Derived state adds ~5.7B
tokens (~95M held plays x ~60) and zero new knowledge. New event streams found this cycle: ~1.3B tokens.

Augmentation (home/away mirror, re-timing) is synthetic: it goes in data-ledger.md's synthetic row and counts
toward the 3x-real cap, never here.
