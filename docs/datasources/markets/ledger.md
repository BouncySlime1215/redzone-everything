# Markets ledger (Data Division D)

New-information tokens = emitted market events from observed prices x 6 (`scripts/markets/tokens.py`).
Derived tokens (anything we compute from prices) are counted on their own row with novelty 0.
Every row is tagged `personal-use, not for commercial release` (plan B). Inputs only: never shown, never a nudge.

| Source | Leagues | Years | Verdict (license line) | Status | Est. new-info tokens | Landed new-info tokens | Derived tokens | Nick action |
|---|---|---|---|---|---:|---:|---:|---|
| Polymarket public APIs ([polymarket.md](polymarket.md)) | six product + pretraining | 2024-07+ | yellow-B: documented public read APIs need no account; ToS §4.2 bars bots on the Site and data resale by market firms, §5.1 personal revocable license; personal-use, not for commercial release | code ready, Kaggle run pending (EXP-1300) | ~10-11M product, +12-24M pretraining | 0 | 0 | none |
| SBRO archive via Wayback ([lines.md](lines.md)) | NFL NBA NHL MLB CFB CBB | ~2007-2022 | yellow-B: no license stated, odds are facts; Wayback copies only; personal-use, not for commercial release | converter ready | ~3.9M (~650k events) | 0 | 0 | approve + log in decisions.md |
| football-data.co.uk ([lines.md](lines.md)) | European soccer (pretraining) | 2000+ | yellow-B: site allows private individuals only; personal-use, not for commercial release | parked until Nick OKs | ~3.6M | 0 | 0 | OK for personal use |
| nflverse games.csv closing lines | NFL | 1999+ | yellow-B: nflverse packaging open, lines sourced from books; personal-use, not for commercial release | converter ready (`ext_game_id` = nflverse game_id for planner B) | ~42k | 0 | 0 | none |
| The Odds API free tier (forward) | six | forward | yellow: terms allow storage and ML use, no resale; never the paid historical endpoints | converter + 350-credit/month schedule ready | ~0.1M/yr | 0 | 0 | add Kaggle secret ODDS_API_KEY |
| Kaggle spreadspoke NFL | NFL | 1966+ | pending: Nick reads the license field (CC0 / CC-BY / ODbL only) | not started | ~0.17M | 0 | 0 | check license field |
| Kalshi ([kalshi.md](kalshi.md)) | - | - | red: Developer Agreement §4.1.1 requires Kalshi membership to use the API; §3.1 bans storing API data | not pulled | - | - | - | optional: ask Kalshi for written research permission |
