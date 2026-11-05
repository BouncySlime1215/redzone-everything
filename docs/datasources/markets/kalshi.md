# Kalshi (markets division, worker D2)

**Verdict: red. The Developer Agreement binds anyone who touches the API (§1), requires API users to be
Exchange Members (§4.1.1), and limits use to a member's own trading with no storing (§3, §3.1). This is
an outright ban on non-member access, not just a restrictive term, so plan B does not cover it. No
collector was written.** Re-check if Kalshi publishes a market-data license or research terms.

Checked 2026-11-05 (web, docs.kalshi.com + the agreement PDF). The container cannot reach Kalshi hosts,
so nothing was called live.

## License (the deciding text)

Source: Kalshi Developer Agreement v1.1, https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf
(no calendar date. docs.kalshi.com links no other terms or market-data license as of today.)

- §1: "Accessing or using Kalshi's API shall be considered a binding acceptance of this Agreement."
  This covers keyless reads too.
- §4.1: "In order to use the API, you are required to: 4.1.1. You must be a Member of Kalshi's
  Exchange and comply with Kalshi's Rulebook." It also requires contact info (§4.1.3).
- §3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange;
  all other usages are disallowed".
- §3.1: bans "Collecting, caching, aggregating, or storing data or content accessed via the API
  except for purposes of facilitating your own trading on Kalshi", and sharing with third parties
  without written authorization.

How this maps to the division rule (contract.md, plan B):
- Plan B lets us use public endpoints that need no auth even when the terms are restrictive. Kalshi's
  read endpoints do serve data without a key.
- But the terms make membership (a KYC account) a condition of using the API at all. That makes it
  an account requirement by contract, which is the red case: "terms forbid access by non-members
  outright". Our only use, collecting and storing ticks for training, is also exactly what §3.1 bans.
- No mirror or relay changes this. Apify "Kalshi scrapers", third-party "metadata APIs" (for example
  anysite or dflow) and the like pull from the same API under the same agreement.

## Endpoints (verified in docs, for the record only)

All endpoints use the base `https://external-api.kalshi.com/trade-api/v2`. The docs now give this
base. The older `api.elections.kalshi.com` and `trading-api.kalshi.com` hosts appear only in older
guides.

The OpenAPI pages mark these with `security: []` (no auth). The market-data quick start says: "No
authentication headers are required for the endpoints in this guide."
(https://docs.kalshi.com/getting_started/quick_start_market_data)

- `GET /series`, `GET /series/{series_ticker}`
- `GET /events`, `GET /events/{event_ticker}`
- `GET /markets` (with `series_ticker`, `event_ticker`, `status`, cursor paging), `GET /markets/{ticker}`
- `GET /markets/{ticker}/orderbook`, `GET /markets/trades`
- `GET /series/{series_ticker}/markets/{ticker}/candlesticks`: requires `start_ts`, `end_ts` and
  `period_interval` (1, 60 or 1440 minutes). It returns bid/ask OHLC and trade price
  OHLC/mean/previous, volume and open interest.
- `GET /historical/cutoff`, `/historical/markets`, `/historical/markets/{ticker}`,
  `/historical/markets/{ticker}/candlesticks`, `/historical/trades`. Settled markets older than the
  cutoff move here (https://docs.kalshi.com/getting_started/historical_data).

Price units: current docs give prices as fixed-point **dollar strings** (for example `"0.5600"`),
contracts as 2-decimal strings. Integer **cents** were the legacy format. Any future parser must
accept both.

## Rate limits

https://docs.kalshi.com/getting_started/rate_limits gives a token budget only for authenticated
traffic. The Basic tier gets 200 read tokens/s for event contracts. The page documents no limit for
unauthenticated reads. §4.3 of the agreement asks for exponential backoff with bounded retries.

## Coverage and yield (if it ever turns green)

- Sports game-winner series (NFL, CFB, NBA, CBB, NHL, MLB) started trading roughly in early 2025.
  Before that, sports coverage was mostly futures. Neither start date could be confirmed from here.
- Estimate after the contract's jump filter: about 40 to 120 market events per live game for 1-minute
  candles on the game-winner market.

## Alternatives

- Polymarket public price APIs, the division's other prediction-market source.
- nflverse / CFBD lines for pregame and close.
- The Odds API free tier for live line jumps.

Ask Nick only if he wants to request written research authorization from Kalshi (§3.1 allows sharing
"with prior written authorization").

License line for the ledger: `red: Kalshi Developer Agreement §1/§4.1.1 requires Exchange membership to use the API; §3.1 bans storing data except for own trading`.

## Re-check, Nov 5 2026 14:20 UTC (Nick 9:05 AM asked for Kalshi "per its terms" as a fast trigger)

Still **red**. The Developer Agreement v1.1 has not changed. §1: accessing the API is acceptance. §3: use is limited to a member's own trading. §3.1: no collecting, caching or storing data. §3.5: no use of the API "for benchmarking or competitive purposes" or to monitor performance, which covers a feed-lag race. §4.1.1: you must be a Kalshi member. No non-member, research or public-data exception exists, and the only written-permission clause covers sharing (§3.1). The trigger therefore uses Polymarket's public market WebSocket (`scripts/markets/poly_live_trigger.py`, yellow-B, shadow log only).
