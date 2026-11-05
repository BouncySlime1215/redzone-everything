# Markets D3: free odds history and line moves (open -> close, forward snapshots)

Owner: worker D3, markets crew (planner D). Code: `scripts/markets/lines_history.py`; tests:
`tests/test_markets_lines.py` (fixtures in `tests/fixtures/markets/lines/`). Output follows
`contract.md`: `out/markets/<source>/<league>/<YYYYMMDD>.jsonl` plus `manifest.json`. Every row is tagged
`personal-use, not for commercial release`, and every row carries `ext_game_id` (the nflverse `game_id`,
the Odds API event id, or `""` for SBRO).

Scope: the scout's `v2-lines` row (lane 1) covers **closing** pregame numbers. D3 adds the **open -> close
move** (the line *jump*) and forward multi-snapshot data, so the market block can carry a `MKT_JUMP`
for pregame and not only a level.

## Sources (ranked by new market events)

Events are counted the way `scripts/markets/tokens.py` counts them: one per (market, phase) tick that
carries new information. Open + close for spread and total, plus a moneyline, gives 3 to 5 events per game.
Snapshots give one event per (snapshot, market).

| # | Source | Leagues | Years | Open/close or snapshots | Est. games | Est. new market events | License line |
|---|---|---|---|---|---|---|---|
| 1 | SBRO historical xlsx, **Wayback copies only** (`web.archive.org/cdx/search/cdx?url=sportsbookreviewsonline.com/scoresoddsarchives/*`, then fetch `.../<ts>id_/<url>`) | NBA, NFL, NHL, MLB, NCAAF, NCAAB | about 2007-08 to 2021-22 (MLB 2010-2021; NCAAB lines patchy before 2010) | Open + Close (spread and total for football/basketball; moneyline and total for NHL/MLB), close ML, close puck/run line | ~145k (NBA 19k, NHL 19k, MLB 29k, NFL 4.3k, NCAAF 12k, NCAAB ~60k) | **~650k** (~4.5 per game) | yellow-B personal use: no license stated, odds are weakly copyrightable facts, Nick approves and logs; Wayback only; never hit live SBR or run the FinnedAI scraper |
| 2 | football-data.co.uk CSVs (pretraining only: `epl`, `bundesliga`, ...) | ~22 European divisions | Pinnacle closing columns (`PSC*`) from 2012-13; all-book closing (`*C*`: `B365CH`, `AvgCH`, `MaxCH`, O/U and AH closing) from 2019-20 | Pre-close snapshot (taken Fri/Tue) + closing, for 1X2, O/U 2.5 and Asian handicap | ~110k with both columns | ~600k (~6 per match) | yellow-B personal use: the site says files are for private individuals; no redistribution, credit the site; pretraining only, never a product league |
| 3 | nflverse/nfldata `games.csv` (= nflreadr `load_schedules`) | NFL | 1999-present (moneylines from ~2006) | **Closing only** (`spread_line`, `total_line`, `home/away_moneyline`, spread and total prices). The dictionary does not name the provider or call it close; the community treats it as closing | ~7.3k | ~21k total; ~7k **new** beyond SBRO (1999-2006 and 2022-present) | yellow-B personal use: nflverse packaging is open (credit nflverse); the line values come from third-party books; model input only |
| 4 | The Odds API free tier, forward snapshots (`/v4/sports/{sport}/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=american`) | All six | Forward only, from the day Nick adds the key | **Observed snapshots** (real `ts`), median consensus over books: vig-free p_home, home spread, total | NBA 1230 + NHL 1312 + NFL 272 + CFB ~800 + CBB Saturday slates per season | ~18k per year (2 NBA snapshots/night x 3 markets, 1 NHL, 4 NFL windows, 1 CFB, 1 CBB h2h) | yellow: terms allow storage and ML use, no resale or redistribution; historical endpoints cost 10x, so never |
| 5 | Kaggle "NFL Scores and Betting Data" (tobycrabtree, spreadspoke) | NFL | 1966-present (spread from 1979) | Closing favorite spread and total only | ~14k | ~28k; ~0.1M tokens new for 1966-98 | yellow, pending: Nick reads the Kaggle license field; use only if CC0, CC-BY or ODbL; stays on Kaggle; skip the HF mirror |
| - | Kaggle NBA/NHL/MLB odds uploads | various | ~2007-2025 | Mostly closing, some open/close | overlaps SBRO | ~0 new (most are SBRO or Covers/SBR scrapes) | yellow only if the license field is CC0/CC-BY/ODbL **and** the stated provenance is not a red-list site; otherwise red |
| - | GitHub "historical odds" repos | various | - | - | - | 0 | red: the ones found (`scrapeOP` and forks) scrape OddsPortal; `pwu97/bettingtools` only re-packs SBRO NBA 2018-20 moneylines. No CC/MIT-licensed original odds history found |

Red list (unchanged, never scraped): sportsbooks, Covers, Action Network, VSiN, Sports Odds History
(Covers-owned), Betfair historical (account, not US), ESPN odds/pickcenter, OddsPortal, Sports-Reference
Vegas columns, Kalshi (see catalog).

## Conversion rules (what the code does)

- **Sign:** `line` is the home spread, negative = home favored. nflverse `spread_line` is positive when
  home is favored, so `line = -spread_line`. SBRO football/basketball: the favorite's row holds the spread
  and the other row holds the total (the smaller magnitude is the spread). The rows can switch between Open
  and Close, which is exactly a favorite flip (fixture: LAL +3 open -> GSW -1.5 close). `pk` = 0, `NL` = blank.
- **p_home:** vig-free from two American moneylines (proportional normalisation). Spread and total ticks
  carry `p_home = null` because a cover price is not a win probability. The Odds API p_home is the median
  over bookmakers of each book's vig-free probability (decimal or American prices are both handled).
- **Time:** historical files have no observation clock. `ts` is the best known time: close = start - 10
  min; open = start - 6.5 days (NFL), - 6 days (CFB), - 20 h (NBA, CBB, NHL), - 18 h (MLB). SBRO has no
  start times, so starts default to 1:00 PM ET (NFL), 12:00 (CFB), 7:30 PM (NBA), 7:00 PM (NHL, CBB), 7:05
  PM (MLB). `interval_s = 0` on every row and the manifest says `ts_quality: approx`. Only the Odds API
  ticks have `ts_quality: observed`. Graders must treat approx times as "before kickoff", never as an
  in-game moment.
- **Dates:** SBRO `Date` is MMDD; season years come from the file name (`nba odds 2019-20.xlsx`): months
  at or after the season-start month belong to the first year. Blank-header odds columns after
  `PuckLine/RunLine/OpenOU/CloseOU` are named `<prev>Odds`.
- **game_key** = `<league>:<local start date>:<away>@<home>` with SBRO compact names, nflverse abbreviations
  and Odds API full names mapped to one abbreviation set (NFL/NBA/NHL/MLB). College teams fall back to an
  upper-case slug; lane 1's crosswalk can remap them later.

## Run (Kaggle, never the Mac)

```
python scripts/markets/lines_history.py --source sbro --in <dir of xlsx> --league nba --out out/markets
python scripts/markets/lines_history.py --source nflverse --in games.csv --out out/markets
python scripts/markets/lines_history.py --source odds_api --in snapshot.json --league nba --observed-ts <epoch> --out out/markets
```

SBRO fetch on Kaggle: one CDX query, then about 90 files at 1 request per 5 s with backoff on 429/503. That
is roughly 10 minutes, a one-time job. Files mixed in one folder are filtered by name prefix (`nba`, `nfl`,
`nhl`, `mlb`, `ncaaf`, `ncaab`).

## The Odds API schedule (fits the 500-credit free tier)

Cost per call is markets x regions = 3 (`us`; h2h, spreads, totals). `/sports` and `/events` are free, and
an empty slate is not charged. `ODDS_API_SCHEDULE` in the script works out to **~350 credits per 31-day
month** (test asserts <= 450):

| League | When (ET) | Credits |
|---|---|---|
| NBA | daily 11:00 and 18:45 (two snapshots, so a measured line move) | 3 + 3 |
| NHL | daily 18:30 | 3 |
| NFL | Thu 19:30, Sun 11:30, Sun 15:45, Mon 19:30 | 3 each |
| CFB | Sat 10:30 | 3 |
| CBB | Sat 11:00, h2h only | 1 |

The collector reads the `x-requests-remaining` header and stops below 30. MLB takes the NHL slot in
spring. The dense 5-minute line-jump series comes from Polymarket (D1/D2), not from here.

## What Nick must do

1. **The Odds API:** create the free key (email signup, no card) and add it as a Kaggle secret
   `ODDS_API_KEY`. No paid plan; historical endpoints stay off.
2. **SBRO via Wayback:** approve and log it in decisions.md as yellow-B (catalog §4 already lists it as
   "Nick approves").
3. **Kaggle spreadspoke** (and any Kaggle odds upload): read the license field; use only if it is CC0, CC-BY
   or ODbL.
4. **football-data.co.uk:** confirm that personal-use pretraining on European soccer is fine (the site says
   private individuals only).
5. **Policy conflict to settle:** catalog line 16 (overseer, Nov 4) says betting data is a *hidden
   calibration check only, never a model feature*. The markets contract (Nov 5 reorg) says *model inputs
   only (importance, closeness, volatility)*. Planner D and Nick need to pick one. Until then these files
   are written but should not be joined into training features.

## Coordination

- **Planner B (context and hype)** consumes these as **pregame importance inputs**: closeness (|close
  spread|, p_home near 0.5), expected pace (close total), and the size of the open -> close move as a
  "news happened" signal (injury or lineup). Join key: `ext_game_id` for NFL (nflverse `game_id`),
  otherwise `game_key`. Use the pregame rows only. Approx timestamps mean these never feed an in-game
  moment.
- **Lane 1 (`v2-lines`)** keeps the closing-number ingestion (nflverse schedules, CFBD `/lines`). D3 does
  not duplicate CFBD. When both exist for a game, prefer lane 1's close and use D3 for the open and the move.
- **tokens.py:** an open tick followed by a close tick with a >= 0.5-point line change emits a `MKT_JUMP`.
  Open -> close is a phase change, so it emits an event even when the number did not move (`MKT_JUMP` =
  0). Those still count as information: the line held.
