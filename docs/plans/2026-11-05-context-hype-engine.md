# Context & Hype data engine (lane 1): build notes

The planner's plan is canonical: docs/plans/2026-11-05-brain-engines.md (#241), with EXP-090..093 pre-registered
there, and the betting-as-input decision logged there. This file is lane 1's build detail under it: output
schema, runtime and delivery, and how each part is graded.

Nick, Nov 4 11:10 PM ET: "the brain is the biggest issue." Engine 1 of two. It runs 24/7 on cloud CPU
(Kaggle, Colab as a backup), never on the Mac. For every game and team in NFL, CFB, NBA, CBB, NHL and MLB it
collects "how much does this game matter, and how do people feel about it" signals. Two outputs feed Watch Value
and Game Importance: **pregame_importance** per game, and **live_buzz** per game per 15 minutes. Lane 1 owns the
data and the integrator owns the tests (engine 2, the test factory).

Serves: North Star "Watch Value / Game Importance" lines (crunch live %, your-team gates through better slate
ranking); owner: lane 1. Experiments: EXP-090 (sources 1-6 flowing), EXP-091 (pregame_importance in WV), EXP-092 (live buzz), EXP-093 (LLM reader).

## Hard rules

1. **Licenses first.** A source gets a row in [sources-catalog.md](../datasources/sources-catalog.md) (license
   line, verdict, access) before its first pull, and a row in the [data ledger](../datasources/data-ledger.md)
   once it lands. Red sources stay red: ESPN site/core API, sportsbook and line-movement sites, Reddit,
   pytrends or any Trends scraping, Kalshi, X/Twitter.
2. **Numbers, not text.** The LLM extractor stores derived numbers (hype 0-1, storyline tags from a fixed list,
   key players as IDs) plus the source URL and fetch time. It never stores the article text, never quotes it, and
   never reads a paywalled page.
3. **Allowlist only.** The extractor reads a site only once its row says the terms and robots.txt allow automated
   reading and do not forbid AI use. RSS and official feeds come first. The allowlist starts EMPTY, so wave 3 runs
   only after each site is reviewed.
4. **Betting lines are inputs to importance, never a nudge to bet** (Nick 11:10 PM; this replaces the Nov 4
   "hidden check only" rule for this use, logged in decisions.md by #241). Lines and line moves are read as market
   expectation and upset odds. Nothing about odds, lines or betting is ever shown on the TV or phone, and the
   objective never rewards betting behaviour.
5. **No leakage.** pregame_importance uses only data timestamped before kickoff. live_buzz at minute t uses only
   data up to t. A CI check holds every feature to its timestamp, and the time split is by whole night with the
   locked nights untouched.
6. $0: free tiers only. Nick adds every key himself as a Kaggle secret; no code reads or prints a key.

## Outputs (contract draft; lane 4 owns the final schema in redzone/contracts)

`context/pregame/<league>/<season>.parquet`, one row per game:
`game_id, league, start_utc, asof_utc, importance (0-1), parts {stakes, closeness, upset, rivalry, national_tv,
records, injuries, hype}, n_sources, source_urls`.

`context/buzz/<league>/<night>.parquet`, one row per game per 15 min:
`game_id, t_utc, news_volume_z, news_tone, wiki_views_z, wiki_edit_rate_z, line_move (signed, pts), buzz (0-1)`.

Each part is a z-score or probability taken against that league's own history, so leagues stay comparable.
`importance` is a learned blend fitted on train nights. Its target is the hindsight oracle's watch value for that
game (grading rule 3: real outcomes), never anything Nick tapped (no training signal from Nick).

## Sources, in waves

**Wave 1 (green or yellow-with-credit, no key, starts this cycle):**

| Source | Catalog row | What we take | Cadence |
|---|---|---|---|
| Wikimedia pageviews (REST per-article daily; hourly dumps for game nights) | §6 green CC0 | team and player article views, z vs 28-day baseline | daily + hourly |
| Wikipedia EventStreams (recent changes, metadata only) | §6 green | edit rate on team/game/player articles; no usernames or IPs | live, 1 SSE client |
| GDELT 2.0 GKG + Events (15-minute CSVs) | §6 yellow, cite GDELT | article count and average tone per team and matchup, URLs only | every 15 min |
| Schedules, national-TV slots, results | nflverse schedules (yellow CC-BY), NBA.com cdn, MLB/Retrosheet, CFBD media (key) | network, primetime, rest, rivalry flags | daily |
| Point-in-time standings + playoff odds (our Monte Carlo) | §7 yellow, derived | stakes: playoff odds swing from this game | daily |
| Records and milestones in reach (derived) | §6 yellow | e.g. "QB 98 yards from a record" | daily |
| Injuries (nflverse injuries; NBA official report archive) | §1/§2 yellow | star-out flags on the pregame side only | daily |

**Wave 2 (need Nick: a key or an approval):**

| Source | Catalog row | Needs |
|---|---|---|
| The Odds API free tier (spreads, totals, moneylines; ~1 snapshot a day plus one pregame) | §4 yellow | Nick adds the free key as a Kaggle secret |
| CFBD `/lines` (open vs close) | §4 yellow | the CFBD key (already waiting on Nick) |
| Polymarket documented APIs (pregame and live probability) | §4 yellow, Nick sign-off | Nick's OK in decisions.md |
| Google Trends official API (alpha) | §6 yellow | Nick applies; never pytrends |
| Bluesky Jetstream (post counts per team tag; no text, handles or DIDs) | §6 yellow | none; queued behind wave 1 |

**Wave 3 (LLM extraction, allowlisted sites only):** a small local model on Kaggle CPU (Qwen 0.5-1.5B, or the
Mind student) reads GDELT's article URLs that pass the allowlist, plus RSS from league and team official feeds
whose terms allow it. It returns `{hype, tags[], key_players[]}` per game, then drops the page. The allowlist
review of the first ~20 feeds is a lane 1 task, and each one gets a catalog row with its license line.

## Running 24/7 without the Mac

- A **Kaggle CPU notebook on Kaggle's scheduler** runs daily at 4 PM ET for ~12 h (the session cap), which covers
  every game night. It backfills anything missed (GDELT and the Wikipedia dumps are archived, so a gap only
  delays data, it never loses it), then polls every 15 minutes until 4 AM. Its output goes to the private Kaggle
  dataset, never to this public repo.
- A second daily slot (8 AM ET, ~1 h) builds pregame_importance for tonight's slate.
- **Delivery to the live app is an open question for Nick and lane 4.** Options: (a) the app reads the Kaggle
  dataset through the Kaggle API (a key on the Mac, which Nick adds), or (b) the notebook publishes only tonight's
  derived numbers (no raw rows) to a release asset through a fine-grained token Nick adds as a Kaggle secret.
  Until one is picked, the engine runs shadow only. Its numbers are graded offline and never read live.

## Grading (EXP-091 bars on #241; the extra checks lane 1 adds)

The bar: on test nights (time split by whole night, per league, locked untouched), the slate ranking that adds
pregame_importance to today's Game Importance beats today's on the hindsight oracle's watch value per night.
Paired, with a cluster bootstrap over nights; the CI lower bound must be above 0. ≥ 8 test nights per league,
else PENDING. Report each part's ablation, ECE for importance, and a leakage check (adversarial validation near
chance; any single-feature AUC > .98 is a red flag). live_buzz is EXP-092: does
buzz at minute t predict a big play or crunch in the next 15 minutes beyond the game-state baseline?

## First steps (lane 1, this cycle)

1. Catalog rows confirmed for the wave 1 sources (all present). The betting note and decisions line come with #241.
2. `scripts/context/wiki_pageviews.py` and `scripts/context/gdelt_buzz.py`: team -> article and team -> GDELT name
   maps for all six leagues (Wikidata IDs, so namesakes don't collide), a backfill of 2019-2025 on Kaggle, and
   the first tables.
3. Pregame parts from data we already have green: stakes (sims), closeness (our WP prior until lines are approved),
   national TV, rivalry, records.
4. EXP-090 first: sources 1-6 flowing daily into the private Kaggle dataset `context-features`; then EXP-091 graded on the backfill, in shadow.
