# TNF feed race, JAX@BAL, Thu Nov 5 2026 (tracker item 4)

Owner: crew D. Crew A measures, and lane 2 answers interface questions only. Nick's 9:50 AM order: shadow only, $0, public endpoints, no betting UI. The score bug is read as numbers only, never frames.

**Question:** which source first sees each score change, and by how much does it lead the Prime screen?

| Source | Where it runs | What is logged |
|---|---|---|
| `polymarket` | cloud box (`poly_live_trigger.py`) | receive time of a p_home jump of at least 3 points within 30 s |
| `espn_scoreboard`, `espn_summary` | cloud box (`feed_race.py espn`) | receive time of each score change; 2 s polls each, staggered, conditional GETs |
| `app_feed` | Mac, the live app as is (nothing new runs there) | `data/sync.jsonl` score rows: the feed's first-seen = `t - z` |
| `screen` | Mac, the score-bug read (#290) | `data/sync.jsonl` rows with kind `score`: `t` = when the new score reached the TV |

The app's own source race (`recordings/*/race.jsonl`, A2) still ranks its feeds against each other as before.

## Run

1. About 8:00 PM ET, on Colab or Kaggle CPU (not the Mac): `bash scripts/markets/run_race.sh`
2. After the game, read-only and with no Mac-side experiment: copy `data/sync.jsonl` off the Mac, plus `sync.jsonl.1` if it rotated (concatenate both).
3. Grade:
   `python scripts/markets/feed_race.py grade --espn out/race/espn.jsonl --poly out/markets_live/polymarket/nfl/20261105.jsonl --sync sync.jsonl --game-key nfl:20261105:JAX@BAL --target prime --since 2026-11-06T01:00:00Z --out out/race/grades.json`

`lead_s` = screen onset - source first-seen. A positive value means the source had the score before the TV showed it. The output gives the median, min, max and share ahead of the screen per source, plus the leader.

## Caveats

- Polymarket fires on big swings, not on every score. A late field goal in a blowout may not move the price 3 points, so its `n` is smaller.
- The cloud box and the Mac must both be NTP-synced. The residual is about 0.1 s.
- Matching is by time: a source's sighting must fall within 120 s of the screen onset. Two scores inside 120 s can swap. The ESPN rows carry the score, so check those by hand.
