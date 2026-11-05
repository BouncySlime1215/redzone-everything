# Markets division (Data type D): tick contract

Owner: DATA PLANNER D (markets). Nick, 12:05 AM Nov 5 2026: live odds + line jumps, prediction-market
public prices (Kalshi, Polymarket). **Model inputs only** (importance / closeness / volatility). Never shown on
the TV or phone, never a nudge, never a betting hook, never a training *label*. Tag on every file and model:
`personal-use, not for commercial release` (plan B).

## Rules for every collector

- Public, unauthenticated endpoints only. No accounts, no logins, no wallets, no keys we create, no CAPTCHA or
  bot-block bypass, no HTML scraping of sites whose terms forbid it. Respect documented rate limits and back
  off on 429. Free tiers only; anything paid needs Nick's OK.
- Runs in the cloud (Kaggle / Colab / GitHub Actions). **Never on the Mac.** The dev container's allowlist
  blocks these hosts, so collectors are tested offline against fixtures and run on Kaggle.
- No wallet addresses or user ids are stored (drop them; if a join needs one, store a salted hash).
- Point-in-time only: every tick carries the UTC time it was observed/traded. Nothing after a play may be
  joined to that play (grader time-split audit).

## Tick row (JSONL, one file per source/league/day)

Path: `out/markets/<source>/<league>/<YYYYMMDD>.jsonl`, plus `out/markets/<source>/manifest.json`.

| field | type | meaning |
|---|---|---|
| `source` | str | `polymarket`, `kalshi`, `nflverse_lines`, `sbro`, `odds_api`, ... |
| `league` | str | `nfl cfb nba cbb nhl mlb` (product) or any other league slug for pretraining (`epl`, `wnba`, `npb`...) |
| `game_key` | str | `<league>:<YYYYMMDD local start>:<away>@<home>` with normalized team abbreviations; `""` if unmatched |
| `ext_game_id` | str | the source's own game id when it has one (nflverse `game_id`, so planner B joins without a crosswalk); `""` otherwise |
| `market_id` | str | source market / token / ticker id |
| `market_kind` | str | `moneyline`, `spread`, `total`, `prop`, `future` (futures/props are kept but tokenized separately) |
| `phase` | str | `pregame`, `live`, `open`, `close`, `settled` |
| `ts` | float | UTC epoch seconds of the price |
| `p_home` | float or null | implied probability the home side wins (vig removed when two sides are known) |
| `line` | float or null | spread (home, negative = favored) or total points, when `market_kind` is spread/total |
| `volume` | float or null | traded volume in the interval (source units, contracts or USD) |
| `n_trades` | int or null | trades in the interval |
| `interval_s` | int | bar width (60 for 1-minute candles, 0 for a single snapshot) |
| `license` | str | the source's one-line license verdict (copied from the ledger) |
| `tag` | str | always `personal-use, not for commercial release` |

## Tokens (vocab v2 market block; lane 1 assigns the base offset)

`scripts/markets/tokens.py` turns ticks into market events, 6 tokens each (same width as v1 events):
`MKT_SRC`, `MKT_KIND+PHASE`, `MKT_P` (home prob, 32 buckets), `MKT_JUMP` (signed change since the last
emitted event, 15 buckets), `MKT_DT` (log time since last emitted event), `MKT_ACT` (log volume bucket).
An event is emitted on the first tick, on every jump of >= 2 probability points (or >= 0.5 points of
line), on phase change, and otherwise at most once per 5 minutes. Ticks that change nothing are dropped,
so tokens count information, not clock.

Counting (ledger): **new-information tokens** = market events from observed prices. **Derived tokens** =
anything we compute from those (vig-removed probs re-expressed, entropy, volatility windows) and are
counted separately with novelty 0.
