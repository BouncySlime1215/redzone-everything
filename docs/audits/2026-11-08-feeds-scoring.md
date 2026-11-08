# Review 2026-11-08: data in and how it is judged (feeds, scoring, grader, gates)

Scope: `redzone/sources/*`, `redzone/scoring/*`, `redzone/models.py`, `redzone/lab/*` (leverage, oracle, outcome_label, splits, brain3data), `redzone/grading.py`, `scripts/ci_scorecard.py`, `scripts/release_gate.py`. Read-only; nothing was run except tiny pure-function probes (leverage, Merger). Checked against `docs/research/2026-11-08/live-data-feeds.md` and `leverage-excitement.md`.

Severity: S1 breaks Rule #1 or freezes the TV. S2 wrong choice, lost time, or a metric/gate that cannot see a failure. S3 cleanup.
No S1 found in this area. The runtime join path (engine.high_leverage, stoppage_for, merge) is sound for pro leagues; the weaknesses are in the college leagues and in what the grader and gate can see.

## Findings

### F1 (S2, ERROR) Grader high-leverage floor is blind to CFB, CBB, WCBB (known; extended)
`redzone/lab/leverage.py:36` `rule = CLOSE_LATE.get(league)` with `league` as passed (no `.base`). Probe:
`high_leverage(CFB, 4th, 2:00, 20-17) = False`, `CFB OT tied = False`, `CBB 2nd half 1:00, 60-59 = False`, `WCBB Q4 1:00 = False` (NFL same state = True).
Everything that depends on CLOSE_LATE:
- `lab/sportsgrade.py:185` and `lab/backtest.py:1133` fill `cuts_high` / `mid_play_cuts_high`, which feed `scripts/northstar.py` line "high_leverage_leaves" and `web/phone_v2.py:367`. College leaves can never count as high leverage.
- `scoring/engine.py:49` `LEAVE_CLOSE_LATE` is a copy; `tests/test_leave_floor.py:58` only asserts the two TABLES are equal, so the live decider (uses `g.league.base` + `pro_period`, correct for college) and the grader disagree in behavior while the test is green.
- Extra bug even after a `.base` fix: `lab/leverage._left_s` and `high_leverage` do not use `models.pro_period`. CBB has 2 halves, so `per == last (4)` is never true for CBB, and the clock is the half clock. Fix both together (`pro_period(league, per, left)`), plus `league.base` for the table.
- Why it matters: `decider.leave.low_leverage` is [] by default, so no live damage today. It matters the day "cfb"/"cbb" are added to that list: the grader would report 0 high-leverage leaves while the decider is the only guard.
Test: parametrized test over all 7 leagues asserting `lab.leverage.high_leverage(lg, play) == engine.high_leverage(GameState(...))` for the same state (close-late, OT, blowout, clock None).

### F2 (S2, GAP) The grader, oracle and scorecards ignore college leagues entirely
- `grading.is_big` (`grading.py:101-117`) tests `league == League.NFL / NHL / MLB / NBA`, never `.base`: CFB, CBB, WCBB return False, so "big plays" = 0.
- `grading.is_crunch` (`grading.py:128`) `crunch.row("graded", league)`: the crunch table has only nfl/nba/nhl/mlb rows, so a college league gets `None` and `inside()` returns False: crunch plays = 0. No `pro_period` either.
- `lab/oracle.py:60-70` calls both, so the oracle ceiling for college is empty.
- `scripts/ci_scorecard.py` `cards()` only runs `nfl`, `nba`, `nhl` (plus demo); `release_gate.HELDOUT` lists the same three. MLB, CFB, CBB, WCBB have no scorecard and no gate row, yet CFB is #2 and NCAAB #4 in Nick's order and UNC football/hoops are "his teams". A gate that has no row cannot fail.
- Consequence: "Anything that misses its gate ships OFF" cannot be applied to 4 of 7 leagues. Rule #1 (0 mid-play entries) is not measured for them.
Fix: use `league.base` plus `pro_period` in `is_big`/`is_crunch`; add `cfb`/`cbb` to `cards()` when archive data exists; until then add a PENDING gate row per unmeasured league so the report says so.

### F3 (S2, CONFLICT) Release gate lets mid-play entries through on the per-night path
`scripts/release_gate.py:144-163`. The hard line `add("entries mid-play = 0", ...)` exists only in the `elif n:` branch (no `per_night`). When `per_night` is present (eval_sports.py always writes it for NBA/NHL, `eval_sports.py:193-203`) the only entry gates are the 95% bar (so up to 5% mid-play, e.g. NHL 2/140 = 1.4% passes if nights >= 8 and the CI lower bound is >= 95) and "never above today" (baseline NBA is 10/20 mid-play, NHL 2/140, demo 2/183, `docs/gate_baseline.json`, so "today" is not zero). CLAUDE.md: "0 mid-play entries is hard". `league_promotion` does require `mid == 0`, so the two gates disagree.
Fix: emit `entries mid-play = 0` in both branches (keep the 95% row as the softer statistic). Test: `gates()` with a per_night card having 1 mid-play entry must contain a FAIL.

### F4 (S2, GAP) The gate never checks that the cards were graded on the locked split
`release_gate.main` reads only `["cards"]` and ignores `slice.split`; metrics do not echo the split (`grep split scripts/eval*.py` shows it only filters nights). `HELDOUT["split"]="test"` is a string for the report, not a check. A JSON made with `ci_scorecard.py --split all` (train nights included) can produce PASS. Fix: have each scorecard write `"split"` and `"nights"` into its metrics, and make `gates()` FAIL "pinned data/split" if split != "test". Test: feed a card with split "all" and expect FAIL.

### F5 (S2, CONFLICT) Two crunch gates can never FAIL
`release_gate.py:176` and `:206`: `add("crunch live >= 95% or feasible ceiling", ..., cr >= 95, ..., pending=cr < 95)`. Any value under 95 (including 0%) becomes PENDING; `main` returns 1 only on FAIL (`:285`). Same for `your-team crunch live = 100%` (`:200`): `pending=v == 0.0`, so a real 0% (every your-team crunch play missed) reads as "no plays" and is PENDING (the TODO in the file admits it). Crunch time is a North Star pillar and the baseline is 72.7% NFL. Fix: have scorecards emit counts (`crunch_n`, `crunch_seen`) and FAIL on a measured value under the bar unless a computed feasible ceiling explains it; keep PENDING only for n == 0.

### F6 (S2, CONFLICT) Brain training/eval splits are hashed, not time splits, and use 15% test
`lab/brain3data.py:52-55` `night_split` = `crc32("night:"+night)` into train/valid/test at 15/15/70. `lab/datasets.py:90` `split_of` hashes the game id (a night's games land in different sets). CLAUDE.md and `lab/splits.py` require time splits by whole night, newest >= 25% locked. `brain3fit.py:142` trains on hash-"train" nights, which include about 70% of the locked nights; `brain3cal.py:135` calls hash valid/test "held". The gate text already says NBA dev-test AUC 0.999 is optimistic; this is the cause, and it is not only a PENDING item: every brain number in northstar/CI is leaked until `night_split` is replaced by `splits.league_split(...)`. Test: a CI leak check that no `brain3data` train night is in `splits.nights_for(..., "test")` (extend `splits.leaks`).

### F7 (S2, ERROR, seasonal) NHL overtime is hard-coded to 5:00 and period 5 is called a shootout
`sources/espn.py:26` `OT_S[NHL]=300`; used at `espn.py:448` and `:510` (`length = OT_S[league] if per >= 4 ...; clock = max(0.0, length - clock)`), duplicated in `lab/leverage.py:21`, `grading.py:49`, `lab/sportsdata.py:422`. Playoff OT periods are 20:00 and count up past 5:00, so `clock_s` clamps to 0.0 for the rest of OT: the game reads "0:00 left" (crunch/END_PERIOD-like) while play is live. `espn.py:514` `if period >= 5: period_type = "SO"`: a playoff 2OT is labelled a shootout (`engine.py:704-741` then picks shootout weights). Not live until April; fix before the playoffs: take OT length from `periodDescriptor.periodType`/`gameType` (api-web) or the game's season type, and call period 5 "SO" only in the regular season. Test: playoff fixture with OT clock "12:30" must give clock_s 450.

### F8 (S3, GAP, UNVERIFIED) NHL intermission clock may be read as period clock
`sources/leagues.py:137-147` takes `clock.secondsRemaining` whenever present. During `inIntermission` api-web reports the intermission countdown (my recollection of the feed; no fixture has an intermission sample: the only fixtures are final). If so, `clock_s` would read ~1000 s "left in period 1" and `merge.progress_key` would see a huge backwards clock. Fix: ignore `secondsRemaining` while `inIntermission`, set clock_s = 0. Record one intermission payload as a fixture first.

### F9 (S3, GAP) League-feed stoppages never produce REVIEW or INJURY
`leagues.enrich_nhl` (`:160-171`) maps every `stoppage` to WHISTLE unless `reason == "tv-timeout"`; `nba_stoppage` maps `instantreplay` to WHISTLE. WHISTLE is "too short to visit" (models.py) so a 2-4 minute video review is not used as a break. NHL's stoppage owner is api-web, which overrides ESPN's REVIEW text (`merge._owned_stoppage`). Fix: map `reason` values containing "video-review"/"chlg-" to REVIEW and "injury" to INJURY; NBA `instantreplay` to REVIEW. Test with fixture reasons.

### F10 (S3, ERROR, verified by probe) A fresher scoreboard poll replaces merged state and drops plays and stoppage
`merge.py:183-196` plus `_fill_gaps` (`:300-328`). A scoreboard state (no plays, stoppage UNKNOWN) with a clock 5 s further along beat a summary state (stoppage TIMEOUT, 1 play); the merged game came back `clock 295, UNKNOWN, last_play None, 0 plays`. On a stopped clock the play-count term in `progress_key` makes the summary win, so this appears when the clock is running (NFL between snaps, live play). Effects: the dead-ball flag flickers to UNKNOWN; `decider.timeline.observe` (`screen.py:919`) runs on a play-less state and then bounds the next play's first-seen window with that poll's `updated_at` (`prev_fresh`), narrowing it wrongly. Fix: when the taking-over state has no plays, carry `last_play`, `recent_plays` and stoppage from the previous leader only if its clock is within one poll, or make scoreboard states contribute only score/clock/phase. Test: the probe above as a unit test.

### F11 (S3, CONFLICT) CFB is forecast with NFL rules
`scoring/breaks.py:255` `if p in (2, 4) and 120 < clk <= 165: "two-minute warning coming"` applies to `base == NFL`, so also CFB, which has no two-minute warning; also `LEN["nfl_half"]`/team-timeout (50 s) values are NFL numbers. `espn.stoppage_for` has the same `startswith("two-minute warning")` rule but ESPN will not emit it for CFB, so only the forecast is wrong: a predicted 167 s commercial that does not come lets the planner pre-position early (lost time, not a mid-play join). Fix: gate on `not league.college`; add CFB media-timeout priors once measured.

### F12 (S3, CONFLICT) WCBB ranks above NHL in priority but below it in scores
`default_config.yaml:474` `league_priority: [nfl, cfb, nba, cbb, wcbb, nhl, mlb]`, but `score.league_weight wcbb 0.9` < nhl 0.95 (equal to mlb) and `my_team_league wcbb 0.6` < mlb 0.64. The North Star order has no WCBB; pick one and keep the two tables consistent.

### F13 (S3, CONFLICT) MLB extra innings can be garbage time, against the module's own docstring
`scoring/garbage.py:3-18` says "Never garbage: overtime/extra innings", but `threshold()` line `if per > _REGULATION[g.league] and g.league != League.MLB` explicitly exempts MLB from the OT skip, so rule `(9, None, 5)` applies in the 10th+. Rare (a 5-run margin in extras) but it contradicts the docs and the high-leverage rule (`per >= 7 and d <= 2`).

### F14 (S3, ERROR, grader leakage) Backtests see end-of-game team records
`espn.py:505-507` `if upto is not None: pass  # Records in a backtest should be what they were going in`: a no-op stub. `_team()` reads the summary's `records`, which include the game being replayed, so `Scorer._stakes` (`both_good_pct`, `both_bad_pct`) uses a record that already contains the result. In November a single game moves win_pct by 0.1-0.5. Fix: in `parse_summary(upto=...)` back out this game's result (or null W/L) for backtests; add it to the leakage check.

### F15 (S3, GAP) Freshness ignores CDN age
`sources/http.py:114-148` stamps `fetched_at` on every 200/304; `feeds.FeedBook.fresh_at` trusts it. Research R4: NHL `s-maxage=19`, ESPN scoreboard `max-age=9` can serve a body up to 19 s old as a "fresh" 200. `Age`/`Last-Modified` are never read, so a stale-but-fresh-looking game can be entered on old stoppage data. Fix: store `Age` (and S3 `Last-Modified`) in `_Cached` and subtract it in `fetched_at` for freshness.

### F16 (S3, ERROR) outcome_label tables miss leagues
`lab/outcome_label.py:35-39`: `HORIZON` lacks WCBB, so `label_game(WCBB, ...)` raises KeyError (`HORIZON[league]`); `WP_SPREAD` lacks MLB and WCBB (`game_left`/`win_prob` return None, so MLB gets only flip labels and `over` is always True). Use `league.base`-style fallback or add the rows.

### F17 (S3, ERROR) MLB `hasReview` is sticky for the whole plate appearance
`leagues.py:346` `if _get(cur, "about", "hasReview"): out.stoppage = REVIEW` runs after the pitch-event logic. `hasReview` stays true for the at-bat after the review resolves, so subsequent pitches read as a review break (a false long break). Use only an open review (last event is the review/challenge event).

## Minor notes (no id)
- `models.pro_period` with `clock_s=None` in the CBB 2nd half returns the first-half-of-period value (`2p-1`), so "late" reads False when the clock is missing; safe direction.
- `release_gate.MIN_NIGHTS = 8` while the NBA and NHL locked windows are 4 nights (20261029-20261101): those rate gates stay PENDING by construction until more nights are archived.
- `grading.DEAD_WORDS` contains "kick", "punt", "turnover": it is only used to anchor break starts (`engine._break_event`), where it is harmless, but do not reuse it for entry decisions.

## What's solid
- Live floor is correct for college: `engine.high_leverage` uses `g.league.base` + `pro_period`; `Candidate._leavable` also vetoes on crunch, lock, imminent, mine, and delays by the feed lag (`_high_lev_at`). `decider.leave.low_leverage` ships `[]`.
- Clock direction is handled consistently for NHL play clocks (count up, converted in `_play_clock`, `parse_summary`, `leverage._left_s`, `grading.play_clock`); football/NBA/CBB clocks count down and are parsed with decimals (`_clock_s`).
- `stoppage_for` is league-aware for halves vs quarters (college halftime from "End Period" in period 2 / `REGULATION//2`), NBA free throws (non-last = whistle; "1 of 2" from text for college), NHL penalties (broad list, penalty shot excluded), team rebounds, MLB lineup changes.
- Merge: regression guard with bench/rehabilitate, correction detection (score drop at same clock), per-league stoppage owner with failover, error hold, and a race log. Fetcher: per-URL locking, ETag/If-Modified-Since, host backoff caps for live hosts, size cap.
- Staleness: `FeedBook.stale` flags an ESPN outage within one poll and blocks only entering (never forces leaving), matching the North Star; push (`espn_fastcast`) counted as freshness.
- Fastcast JSON Patch implementation follows RFC 6902/6901 (ordering of `~01`, array bounds, `test` equality, move-into-self), size-capped checkpoints, `usable()` guard against no-clock documents.
- Splits: `lab/splits.py` (whole-night, locked newest >= 25%, forward-chaining folds, `leaks`, `causal`) is correct; tune.py excludes locked nights; release gate uses a cluster bootstrap over nights with a lower bound, and never counts PENDING as pass.
- Scoring order: `league_priority`, `league_weight`, `my_team_league` all agree on NFL > CFB > NBA > CBB > NHL > MLB (only WCBB is out of line, F12); closeness/garbage/crunch/estimate tables use `ByLeague` so college reads its pro row.
