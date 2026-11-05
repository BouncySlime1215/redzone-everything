# Context fact shards (Data Division B -> lane 1 vocab v2)

Format agreed with lane 1 (Nov 5, 12:27 AM). Code: `redzone/context/tokens.py`.

- One parquet per league-season: `<league>/<season>.parquet` (stakes shards under `<league>-stakes/`).
- Columns: `game_id` (as in the play table), `team` (null for a game-level fact), `asof_utc` (strictly before the
  fact applies: kickoff for pregame, the minute for live), `field` (fixed names, listed per crew doc),
  `value` (float), `value_str` (categorical values such as a referee or umpire id; null for numbers),
  `bucket` (0-15, quantile edges fit on TRAIN games only and saved as `edges.json`; null for categorical),
  `kind` (`new` = an external fact we did not hold, `derived` = computed from data we already hold),
  `source` (scout.json id), `license` (license line; B-plan rows include "personal-use, not for commercial release").
- Lane 1 maps each field to one token per game or team, at kickoff and at each change, and counts `new` and
  `derived` apart in the ledger.
- Post-game facts (attendance, game length, excitement index) carry a `_postgame` field suffix and an asof after the
  game ends, so they can only feed later games.
- Lines and every other price series are crew D's (`out/markets/...`), never in B shards.

Crew docs: [B1 landing](context-b1-landing.md), [buzz](context-buzz.md), [stakes and weather](context-stakes-weather.md).
Data lands in the private project folder and the private Kaggle dataset, never as raw rows in this public repo.
