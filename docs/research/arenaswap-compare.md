# ArenaSwap vs RedZone (Nov 4, 2026)

ArenaSwap ([github.com/hiteacheryouare/arenaswap](https://github.com/hiteacheryouare/arenaswap), v2.2.0) is a
TypeScript browser extension. It polls ESPN for 31 leagues, scores every live game 0-100 ("PowerScore") and
switches the browser tab to the best one.

**License and how we used it.** The repo now carries an ISC license (`LICENSE`, `packages/powerscore/LICENSE`).
We were told to treat it as all rights reserved, so we did anyway: we read it for ideas, facts and data
sources only. No code, comments or data tables were copied, and we ran none of its code. Everything below
that we took is our own Python.

## Feature by feature

| Feature | ArenaSwap | RedZone | Worth taking? (why) |
|---|---|---|---|
| Excitement score | 5 signals (closeness, late game, momentum, lead changes, comeback) with progress curves and decay half-lives per sport, plus win-prob "hugging 50%" ±5 | Per-league rule moments (red zone, PP/empty net, leverage index, crunch) × stakes × league weight, learned brain v3, reasoner, garbage-time detector | No. Ours is richer and graded against big plays. Their per-sport decay half-lives match what our History fade already does. |
| When to switch | Score delta ≥ a sensitivity threshold (5 levels), cooldown (45 s default), optional fixed delay | Decider: dead-ball-only entries, whistle wait, break model, two sticks pre-cued, 0 mid-play cuts as a hard rule | No. Their open issue #226 asks for "wait for a break in play before switching", which we ship. |
| Multiview | Proposed only (issue #230, "RedZone-style Multiview") | Two sticks + Prime NBA multiview tuner (off until gated) | No. We're ahead. |
| Postseason stakes | Round ladder: points by distance from the trophy, read from `competitions[0].notes[].headline` | Stakes multiplier knows records, spread, national TV. Nothing for playoffs | **Yes: implemented** (`redzone/scoring/postseason.py`, off). |
| Series state (elimination, Game 7) | Proposed (issue #158): `competitions[0].series` wins + `totalCompetitions` | None | **Yes: implemented**, same module. |
| Ranked matchups | Proposed (#158) | Ranks parsed (`Team.rank`) but used only to filter college boards | **Yes: implemented** as a stakes term. |
| Upset watch | Proposed (#157) | Shipped (engine `upset` term from the spread) | No, we have it. |
| No-hitter watch | Proposed (#159) | Shipped (`score.mlb.no_hitter`) | No, we have it. |
| Hockey PP / empty net | Proposed (#163), via ESPN core `situation` | Shipped from NHL api-web `situationCode` | No, we have it, from a faster feed. |
| Two-minute drill | Proposed (#166) | NFL drive model (`scoring/drive.py`) and crunch | No. |
| "Stall" penalty (score frozen 8+ polls) | Flat −15/−25 | Break detection from play-by-play, break-length model | No. Ours is the real thing. |
| Standby stream when all games are dull | All registered games under a threshold → a chosen fallback tab | `redzone/standby.py` | No. |
| Adaptive polling | 12-25 s by score, 40 s at intermission, 2-3 min dormant, up to 30 min in an offseason; floors at ESPN's `cache-control: max-age` (12 s on the scoreboard) | Fixed per-source intervals, FastCast push, `http.get(min_interval=)` | Maybe (rank 3). Saves requests on the 8 GB Mac; doesn't move a North Star line. |
| Basketball win prob from the scoreboard | Proposed (#168): `situation.lastPlay.probability.homeWinPercentage` on the scoreboard for basketball, saving a 50-60 KB summary fetch | We read it from the scoreboard for NFL only; basketball WP comes from the summary or our model | Maybe (rank 2). Needs a scorecard run (it changes `home_win_prob` timing). |
| Favorite team bonus | Flat +10 points | L1 tier 0: your team first by construction | No. |
| Day guide / heat timeline | Full-day timeline with a predicted heat curve | Phone page | No (not a North Star line). |
| Notifications on switch | Browser notification with the reason | Phone page reasons | No. |

## Leagues and free data sources they use that we don't

All from ESPN's public site API, the same host we use: `https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard`
(and `/summary?event={id}` for win probability and box score). Scoreboard responses carry
`cache-control: max-age=12`, so polling faster than 12 s returns the same bytes. Paths (facts):

| Sport | ESPN path(s) we don't poll |
|---|---|
| Basketball | `basketball/wnba`, Olympic men's/women's (`basketball/mens-olympics-basketball`, `.../womens-olympics-basketball`) |
| Football | `football/ufl` |
| Hockey | `hockey/mens-college-hockey`, Olympic men's/women's |
| Baseball / softball | `baseball/college-baseball`, `baseball/college-softball`, `baseball/world-baseball-classic`, `baseball/olympics-baseball` |
| Soccer | `soccer/usa.1` (MLS), `usa.nwsl`, `eng.1`, `esp.1`, `ger.1`, `ita.1`, `mex.1`, `uefa.champions`, `uefa.europa`, `fifa.world`, `fifa.wwc`, Olympics |

None of these are in the North Star's league order (NFL > CFB > NBA > NCAA basketball > NHL > MLB), so adding
them moves no line. College hockey is the closest fit (UNC has no D-I team). Their issue #163 also names ESPN's
core `situation` resource for hockey (`sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events/{id}/competitions/{id}/situation`,
`powerPlay` / `emptyNet`); we already get both from NHL api-web.

## Where we're clearly ahead

1. **Switch timing.** They switch on a score delta plus a cooldown, so they land mid-play; their top open issue
   (#226) is the problem our Rule #1 and decider solve. We enter only at dead balls, measured per landing.
2. **Breaks and dead air.** They spot a commercial by the score not moving for 8 polls. We model breaks from
   the play log, forecast their length and pre-cue the second stick.
3. **Faster feeds.** NHL api-web, the NBA live-data mirror, MLB statsapi, FastCast push. They poll one ESPN
   scoreboard.
4. **Measured.** North Star scorecards, locked nights, release gate. They calibrate thresholds with a
   simulator on synthetic games.
5. **Hard rules.** League order and your teams are L1 tiers that no score can cross; their favorite is +10 points.

## Pull-in list (ranked)

| # | Item | North Star line it moves | Status |
|---|---|---|---|
| 1 | **Postseason stakes**: elimination game, decider (Game 7 / one-game final), rounds left to the title, ranked matchups, from fields already on the ESPN scoreboard | Crunch live (NFL/NBA/NHL) and big plays seen live in the postseason: the right game wins ties when two are equally close; % of hindsight-oracle value | **Done, OFF** (`score.postseason.enabled`) |
| 2 | Basketball win prob from the scoreboard's `situation.lastPlay.probability` | Big plays seen live, NBA (fresher WP swing signal); fewer summary fetches | Next: needs an NBA scorecard before/after |
| 3 | Poll floor from the response's `cache-control: max-age`, and slower polls for leagues with nothing live today | Switch time / phone press p95 indirectly (less load on the 8 GB Mac) | Later |
| 4 | Feed the `playoff` feature to Noggin live reads (`postseason.playoff_flag`) | Same as Noggin Timing (dead-air) | Hook ready; the decider lane wires it |

### What shipped (item 1)

- `redzone/scoring/postseason.py`: `parse` (ESPN event → `Stakes`), `bonus`, and three hooks:
  `stakes_bonus(g, cfg)` (add to the engine's stakes multiplier, capped at +0.15), `mind_terms(g, cfg)`
  (a Noggin L3 `playoff_race` term, 0..1) and `playoff_flag(g)` (the Noggin `playoff` feature).
- `GameState.stakes`, filled by the ESPN scoreboard parser (data only) and carried by the merger.
- Config `score.postseason.*` in default_config.yaml, `enabled: false`.
- Not wired into `scoring/engine.py`, the decider or the Noggin: those lanes own them. Wiring is one line
  each: `s += postseason.stakes_bonus(g, c)` in `Scorer._stakes`, and `MindRead(postseason.mind_terms(g, cfg), now)`.
- Tests: `tests/test_postseason.py`, on the real 2025 World Series Game 7 and 2026 Game 5 scoreboards.
