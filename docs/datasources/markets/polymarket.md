# Polymarket (markets worker D1)

Collector: `scripts/markets/polymarket.py` · tests: `tests/test_markets_polymarket.py` (fixtures in
`tests/fixtures/markets/polymarket/`) · contract: `contract.md`. Hidden calibration / eval check only (overseer,
Nov 4): never a feature, label or weight, never shown.

**License: yellow-B: documented public read APIs need no account; ToS §4.2 bars bots/scrapers on the Site and
data access/resale by capital-market firms or data distributors, §5.1 grants a personal, revocable license;
personal-use, not for commercial release.** (We never touch the HTML site, never trade, never store wallets.)

## Verified endpoints (docs.polymarket.com, read Nov 5 2026)

| Use | Endpoint | Doc |
|---|---|---|
| Sport -> tags, `ordering` | `GET https://gamma-api.polymarket.com/sports` (`sport`, `ordering`, `tags` CSV, `series`) | /api-reference/sports/get-sports-metadata-information |
| Market listing | `GET https://gamma-api.polymarket.com/markets/keyset` : `tag_id`, `closed=true`, `sports_market_types=moneyline`, `end_date_min/max`, `limit` <= 100, `after_cursor` = previous `next_cursor` (absent on last page); `offset` -> 422 | /api-reference/markets/list-markets-keyset-pagination, /market-data/fetching-markets |
| Price history | `GET https://clob.polymarket.com/prices-history` : `market` (token id), `startTs`, `endTs`, `interval` (`max all 1m 1w 1d 6h 1h`), `fidelity` (minutes, default 1) -> `{"history":[{"t","p"}]}` | /api-reference/markets/get-prices-history |
| Trades (not used yet) | `GET https://data-api.polymarket.com/v2/trades` : `condition`, `event_id`, `limit`, `cursor`; rows carry `proxy_wallet`, `name`, `pseudonym`, `bio`, `profile_image*` (all dropped by `scrub`) | /api-reference/feeds/list-trades |
| Terms | polymarket.com/tos §1, §4.1, §4.2, §5.1 | |

Rate limits (/api-reference/rate-limits): Gamma 4,000/10 s general, `/markets` 300/10 s, `/events` 500/10 s;
CLOB 9,000/10 s, `/prices-history` 1,000/10 s; Data API `/trades` 200/10 s (v1), `/v2/trades` 300/10 s. Over
the limit, requests are throttled (queued) rather than rejected. We run at ~2 req/s, back off exponentially
on 429/5xx and honor `Retry-After` / `retry_after_seconds`.

Not documented (do not rely on): `series_id` on `/markets`; `/teams` exists (`?league=nba`) but is unused.

## What the live listing showed (Gamma fetch, Nov 5 2026)

- NBA tag 745 (doc example); CFB tag 100351. Other leagues: the collector picks the league tag shared by the
  fewest `/sports` rows (drops 1 = Sports, 100639 = Games, sport-wide tags such as 100350 soccer, 28
  basketball); `--tag-id` overrides. US leagues `ordering: away`; soccer and most others `home`.
- From 2024-25 on, NBA game markets are `"<Away> vs. <Home>"`, slug `nba-<away>-<home>-<YYYY-MM-DD local>`,
  outcomes are the two nicknames, `gameStartTime` set (`nba-hou-okc-2025-10-21` = HOU@OKC; `nba-nyk-bos-2024-10-22`).
- **April 2024 playoff markets listed the home team first** and often lack `gameStartTime`; some sit under
  an `nba-dailies-<date>` event titled just "NBA". The collector skips games before 2024-07-01 rather than
  guess, and reads team order from the market question first.
- Soccer moneylines are three Yes/No markets (home win / draw / away win); only the home-win market is used
  (p_home = p(yes)); the away and draw markets are skipped since p_away != 1 - p_home.
- Product leagues map nicknames to league abbreviations (NBA/NHL league codes, ESPN-style NFL/MLB); CFB, CBB
  and pretraining leagues keep the upper-cased Polymarket slug code (provisional, needs a join table).

## Coverage and token estimate (estimates)

| League | Game markets / season (est.) | Basis |
|---|---|---|
| NBA | ~1,300 | full slate seen on opening nights 2024-10-22 and 2025-10-21 |
| NHL | ~1,350 | estimate: full slate assumed |
| MLB | ~2,450 | estimate: full slate assumed |
| NFL | ~285 | estimate: full slate |
| CFB | ~500-800 | estimate: FBS headline games, not all |
| CBB | ~1,500-3,000 | estimate: tag exists (ncaab), depth unknown |
| Soccer + others (pretraining) | ~20,000+ | ~250 `/sports` rows, many soccer/cricket/basketball leagues |

- Product leagues since 2024-07 (~2.3 seasons): ~16-19k games, one p_home series each.
- Raw ticks: 1-minute bars over [start - 24 h, start + 6 h] -> up to ~1,800 / game -> ~30M ticks (product).
- Emitted events after the contract's jump filter: ~40-120 live + ~10-40 pregame, call it ~100 / game ->
  ~1.6-1.9M events x 6 = **~10-11M new-information tokens (product leagues)**; pretraining leagues add an
  est. ~12-24M. (The catalog's 250M row counts raw ticks / all markets; tokens are far fewer.)
- Cost: ~3 history calls per game + listing pages -> ~60k requests, ~8-9 h at 2 req/s on Kaggle. $0.

## Open risks

- `/prices-history` behavior on resolved markets is undocumented; community reports coarse or empty results
  at small `fidelity` with `interval=max`, so we query explicit 12 h `startTs/endTs` windows. Verify on the
  first Kaggle run (manifest `ticks` per market); if empty, retry with `fidelity` 5-10.
- `volume` / `n_trades` are null (prices-history has price only); `/v2/trades` can fill them later, wallets dropped.
