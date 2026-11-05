# Data H: web-scale sports text (plan, ledger, crew)

**Source:** Nick, 11:50 PM ET Nov 4 (org chart #252, ids EXP-1800..1899).
**Tag on everything:** personal-use, not for commercial release.
**Boundary:** this repo is PUBLIC. It gets code, derived numbers and alignment indexes only. Raw filtered
text lives only in the private Kaggle dataset `rz-h-webtext`. Runs on Kaggle CPU or Hugging Face only, never
the Mac. Stream and filter; never mirror Common Crawl. Respect robots and terms; no paywalls or logins.
H pauses first (with C, D, F, G) at a missed usage checkpoint.

## What H feeds

| code | consumer | what lands | judged by |
|---|---|---|---|
| (a) | Watch Value (lane ①, #166) | per-play `mention` labels: recap sentences aligned to play-by-play by date + teams | EXP-1800: lift on % of oracle |
| (b) | Game importance (EXP-014, B's context shards) | per-matchup numbers: rivalry age / meetings, "notable games" count, storyline flags, prior-meeting margin | EXP-1802 |
| (c) | the Mind (3B) sports-language fine-tune | filtered sports text tokens, private only | EXP-1803 (held until a use is graded) |

## Ledger (one line per source; estimates marked est., replaced by counts when a run lands)

Info per GB = new-information units this source adds per GB **read** (the cost we pay), not per GB stored.
Tokens are counted in two separate columns: **new-info** (facts not already in our play-by-play or B's
shards) and **derived** (re-expressions of facts we already hold, such as a recap restating the score).

| Source | License / terms | Feeds | Read cost | Kept (private) | New-info units | Derived units | Info per GB read (est.) | Verdict | Status |
|---|---|---|---|---|---|---|---|---|---|
| FineWeb (HF `HuggingFaceFW/fineweb`, recent CC dumps) | ODC-By 1.0 + Common Crawl terms of use; page text stays copyrighted, so derived numbers only in the repo | (a), (c) | URL column first (~1-2% of a dump's bytes), then text only for URL-matched row groups: est. ~30-60 GB read per dump | est. 2-5 GB sports text per dump | (a) est. 0.3-1M play mentions per dump across NFL/NBA/NHL/CFB/CBB/MLB; (c) est. 0.5-1.5B text tokens | recap score lines, final scores (dropped from labels, already in pbp) | (a) ~10-30k mentions/GB; (c) ~20-40M tokens/GB | yellow (B) | planned |
| Common Crawl raw (CC index for the late-Oct 2026 crawl) | CC terms of use; robots honored by CCBot at crawl time | (a) for NBA / NHL Oct 2026 train nights only | index lookups by recap URL pattern, WARC byte ranges only: est. < 5 GB | < 0.5 GB | est. 10-40k mentions for the eval-window train nights | as above | est. ~5-10k mentions/GB | yellow (B) | planned (waits on the crawl being published) |
| Wikipedia (HF `wikimedia/wikipedia` en, or dumps.wikimedia.org) | CC BY-SA 4.0 + GFDL; attribution in the dataset README and model card | (b), (a) for notable games, (c) | ~20 GB parquet scanned once, category + infobox filter | est. 0.5-1 GB (game, season, rivalry, player, notable-games articles) | (b) est. 3-6k rivalry pairs, ~30-60k game / season articles with stakes words; (a) mentions for notable games | infobox scores and records (already held) | (b) ~0.2k matchup features/GB; (c) ~50M tokens/GB | green (attribution) | planned |
| GitHub search (open datasets) | per repo; only permissive data licenses go to A or D | new event data for A, odds for D (not code text) | API metadata only | none here | finds handed over (below) | n/a | n/a | per repo | cycle 1 done |
| Open / public-domain sports books | public domain or CC | (c) only | not pulled | none | none | none | low | held (no use yet) | held |

## GitHub finds, cycle 1 (Nov 5, 04:55 UTC; repository search API, metadata only)

Handed to A (events) or D (odds). A repo's code license is not a data license: each needs its owner to read
the data's terms before landing.

| Repo | Code license | What it holds | Hand to | Note |
|---|---|---|---|---|
| sealneaward/nba-movement-data | MIT | SportVU NBA tracking, 2015-16 (~600 games) | A | data is the league's; B use only, yellow |
| danmorse314/hockeyR-data | none stated | NHL play-by-play scraped with hockeyR | A | overlaps A / lane ① api-web; check years before pulling |
| nlbair/openplay | NOASSERTION | open men's soccer event data with xG | A | pretraining only (not a product league) |
| JeffSackmann/tennis_MatchChartingProject | none in API (CC BY-NC-SA on the project) | point-by-point pro tennis | A | A has tennis queued; same source |
| br-g/openf1 | NOASSERTION | F1 timing, live and historical | A | pretraining only |
| flancast90/sportsbookreview-scraper | MIT | ~10 years of NFL / NBA / NHL / MLB games + odds (SBRO-derived) | D | duplicates D's SBRO line; use as a cross-check |
| masgitcode/kalshi-odds-archive | none stated | Kalshi NFL price snapshots | D | Kalshi is RED (D, Nov 5): do not use |
| JacobiusMakes/awesome-sports-betting-data | CC0 | curated list of odds datasets | D | a pointer list, not data |

## Queue order (Nick, 2:00 AM ET Nov 5: NBA first, then NFL)

Every H job runs NBA first, then NFL. NHL, MLB, CFB and CBB run only where the same code or pass covers
them at no extra cost.

1. **H1 + H2, NBA:** filter FineWeb and the late-October 2026 Common Crawl to NBA recaps first, and align
   them to NBA train nights. The NBA recap head (soft labels) trains on 2019-25 NBA games.
2. **H1 + H2, NFL:** the NFL 2025 season (train nights are inside FineWeb's crawl range), then NFL history
   for the head.
3. **H3 Wikipedia:** NBA rivalry and notable-game numbers first, then NFL, handed to B for game importance.
4. Other leagues: only as a by-product of passes 1-3.

**Known limit:** EXP-1800 needs 8 or more valid nights per league. The NBA window has 9 nights in total,
so NBA reads **PENDING** until the archive grows. This is the same gap as Nick's ask for 60 or more graded
NBA decisions per gate. NFL can be graded now. H does not grow nights itself; it labels whatever nights
the archive holds.

## Crew H (jobs, not always-on agents)

- **H1 FineWeb filter:** a Kaggle CPU notebook streams FineWeb parquet by row group, reads `url` first,
  keeps recap-shaped URLs on an allowlist of sports domains and paths (`/recap/`, `/game/`, `-vs-`,
  `/boxscore/`), then reads `text` for kept rows and runs a cheap classifier (team / league / player name
  hits from our rosters + recap patterns: final score, quarter / period words, "scored", "touchdown",
  "power play"). Output: private shards + public counts.
- **H2 aligner:** per recap, parse date + teams → game_id via our schedules; split sentences; match each
  sentence to one play by the numbers it carries. Output: the public alignment index
  (source, game_id, seq, recap hash, match score, matched fields; seq = A's play-table order, so an MLB plate appearance spans several seqs) and per-play `mention` counts.
  Refuses any date inside valid, locked or fresh splits.
- **H3 Wikipedia:** one pass over the en dump: game, season, rivalry, player, notable-games articles →
  per-matchup numbers for (b) as B's fact-shard format (`kind = new | derived`, `_postgame` suffix for
  after-the-fact facts), handed to B for game importance.
- **H4 GitHub scout:** the search above each cycle; finds go to A or D with a terms note. A (Nov 5): SportVU sent to the scout for terms; hockeyR-data skipped (overlaps api-web); openplay, OpenF1 low priority.

## Experiments (EXP-1800..1899)

- **EXP-1800** (pre-registered, [prereg/exp-1800.md](../prereg/exp-1800.md)): recap-aligned importance labels
  vs % of oracle. The gate for everything else in H.
- **EXP-1801:** the landing bar for H1 + H2 (alignment precision ≥ 90%, 0 raw text in the repo, B tag).
- **EXP-1802:** Wikipedia rivalry / storyline features as game-importance inputs (queued after B's
  EXP-091 harness).
- **EXP-1803:** Mind sports-language fine-tune on (c). Held until EXP-1800 or EXP-1802 shows a use.
