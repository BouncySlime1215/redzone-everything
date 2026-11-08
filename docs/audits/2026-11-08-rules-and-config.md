---
id: A-RULES
title: "Audit 2026-11-08: the rules, the gates, the config defaults"
date: 2026-11-08
stream: audit
author: "unrecorded (2026-11-08 audit agent)"
status: reviewed
supersedes: []
sources: 0
verified: 0
affects: ["CLAUDE.md", "redzone/default_config.yaml", "redzone/contracts/flags.py", "scripts/release_gate.py", "scripts/northstar.py"]
gate: []
---

# Audit 2026-11-08: the rules, the gates, the config defaults

Scope: CLAUDE.md, architecture, northstar/roadmap, status, handoff, decisions, runbooks, north-star memory,
`default_config.yaml`, `contracts/flags.py`, `scripts/release_gate.py`, `scripts/northstar.py`.
Read-only review; every finding checked against the quoted lines. Types: ERROR / CONFLICT / GAP.

## Summary table

| id | sev | type | one line |
|---|---|---|---|
| R1 | S1 | CONFLICT | Release gate passes up to 5% mid-play entries (per-night path); Rule #1 says 0 |
| R2 | S1 | GAP | North Star and release gate score Rule #1 only for NFL/NBA/NHL; CFB, CBB, MLB never graded; live nights PENDING |
| R3 | S2 | ERROR | `flags.*` kill switches are never read by any production code |
| R4 | S2 | GAP | Release gate is not on the deploy path; "never above today" baseline is frozen at Nov 3 (NBA 10/20 mid-play) |
| R5 | S2 | CONFLICT | Deploy runbook / CLAUDE.md vs actual practice (unmerged branch, live-game bypass) |
| R6 | S2 | CONFLICT | Leave rule stated three ways (CLAUDE.md, architecture, Nov 7 pass bar); CLAUDE.md never updated |
| R7 | S2 | CONFLICT | League order: Nov 4 decisions say NFL = CFB; CLAUDE.md, memory, architecture, config say NFL > CFB |
| R8 | S2 | CONFLICT | WCBB: Nov 4 "turn it off" decision never reached the config; Nov 8 adds it to buffered_jump |
| R9 | S2 | GAP | Release gate has no commercial / dead-air / mid-break gate; real 0% your-team crunch reads PENDING |
| R10 | S2 | CONFLICT | `league_promotion()` checks entries only, not leaves; silently skips missing metrics |
| R11 | S2 | CONFLICT | status.md (Nov 4), handoff §5/§6 (Nov 7 22:45), roadmap/northstar (Nov 6) are stale vs code and decisions |
| R12 | S3 | CONFLICT | Pass bar "applies to every sport" but buffered/jump default leagues omit NHL, MLB; defaults differ from the Nov 7 23:35 "restored" list |
| R13 | S3 | CONFLICT | Usage pause 88% (CLAUDE.md, runbook) vs 90% (memory, status); Nov 3-4 pacing text is stale |
| R14 | S3 | CONFLICT | wcbb weights sit below NHL/MLB in `score.league_weight` / `my_team_league` but above NHL in `league_priority` |
| R15 | S3 | CONFLICT | North Star "nights" count league-nights; per-league locked NBA/NHL have 4 nights each |
| R16 | S3 | GAP | `check_memory.py` accepts any edit to decisions.md for a default change |
| R17 | S3 | GAP | Entry defaults leave Rule #1 unguarded outside NBA/NHL/NFL overrides; Prime phone taps still land mid-play |

## Findings

### R1 (S1, CONFLICT) The release gate lets mid-play entries through
`scripts/release_gate.py:150-155` (the path used whenever a card has `per_night`, which the locked cards do):
```
st, det = rate_gate(rows_n, 95.0)
rows.append(("entries at a dead ball >= 95%", name, st, det))
...
rows.append(("entries mid-play <= 5%", name, st, f"... judged with the row above"))
```
Only the legacy branch (`elif n:`, line 165) adds `add("entries mid-play = 0", ...)`. So a card with 4% mid-play
entries PASSES. `docs/architecture.md:294` also writes the bar as "<= 5 % of entries". CLAUDE.md Rule #1 and
decisions (Nov 4 3:15 PM) say 0 is hard, and `northstar.py` `mid_play` FAILs on any single event.
Two bars for one rule; the one that gates a release is the loose one.
Fix: in the `per` branch add a 0-event row (reuse `zero_bound`), and change architecture §7 to "0 (hard)".
Test: card with `per_night` and 1 mid-play entry in 300 must FAIL.

### R2 (S1, GAP) Rule #1 is measured only for 3 of 6 leagues, and not live
`scripts/northstar.py:49-50` `LEAGUES = ("nfl", "nba", "nhl")` (asserted subset of `NICKS_LEAGUES`, which has six).
Every row (mid_play, high_leverage_leaves, landings_dead, big_live, crunch) loops `LEAGUES`.
`release_gate.py:22-28` `HELDOUT` and `ci_scorecard.py` cover nfl/nba/nhl/demo only.
CFB is league #2, UNC (cfb, cbb) is a first-priority team, and the Nov 7 live runs were CFB/NBA, yet "Mid-play
entries 0.00 PASS (0 in 1678 landings)" in `docs/northstar.md:15` says nothing about them. `league_order` is
PENDING ("per-league scorecards can't see a cross-league overlap", northstar.md). "Live nights" section is
PENDING: "no live night collected yet" (northstar.md:145), so the real TV (jump logs, `buffered_jump_try.py`
PASS/FAIL windows) never enters the North Star. CLAUDE.md says every PR must show before/after
`entries_mid_play` and that "0 mid-play entries is hard", but a CFB/CBB/MLB PR has no card to show.
Also `league_overrides` comment (default_config.yaml:925-927): CBB, CFB, WCBB, MLB "stay on live behavior until graded",
and no grader exists for them.
Fix: add cfb/cbb to `LEAGUES` via `.base` card reuse (CFB plays NFL rules; there is an eval-data archive per
decisions.md:284), and ingest the pass-bar window verdicts as the live-night source.
Test: northstar table must contain a `mid_play` row per league in `NICKS_LEAGUES`, PENDING (not absent) when no card.

### R3 (S2, ERROR) The feature flags do nothing
`redzone/contracts/flags.py:56` `enabled()` is the only reader of `flags.<name>`. Grep over `redzone/`, `agent/`,
`scripts/` finds no caller outside `tests/test_contracts.py`. `default_config.yaml:837-852` sets 15 flags to true,
and `architecture.md` §3.7 says "A flag that is off means the subsystem runs in its degraded mode", §3.8 says the
supervisor "turns that part's flag off" after three overruns, §7 says a part that misses its gate "ships OFF behind
its feature flag (`flags.*`...)". Flipping `flags.reasoner` / `flags.reel` / `flags.highlight_jump` etc. changes
nothing; subsystems are actually gated by their own keys (`reasoner.enabled`, `replay.*`, `brain.enabled`,
`noggin.*`). `highlight_jump: true` while status.md says the highlight jump (#18) was closed OFF (NBA 82% < 95%).
Why it matters: the phone settings page, rollback and the budget supervisor all rely on a kill switch that is
not wired. Fix: either wire `flags.enabled()` into each subsystem's entry guard, or delete the block and correct
architecture §3.7-3.9/§7 to name the real keys. Test: for each `Flag`, set false and assert the subsystem hook is skipped.

### R4 (S2, GAP) The release gate does not guard what is deployed; its baseline is frozen
- `.github/workflows/ci.yml:282-288`: the gate only fails the job when `base_ref == codex/resume-redzone`.
  `docs/memory/runbooks/deploy.md:6` deploys `integration/release`, and `scripts/deploy.py` never calls
  `release_gate` (grep: no match). status.md records RC 4 deployed with "59 pass, 10 fail, 27 pending".
  Failing gates are reported, not enforced, on the path that reaches the TV.
- `docs/gate_baseline.json` is "codex/resume-redzone 5f0e6a1" (Nov 3): NBA `entries_mid_play` 10 of 20, NHL 2, demo
  2. "Entries mid-play never above today" therefore PASSES an NBA card at 50% mid-play. The ratchet was never moved
  down although the live numbers are far better.
Fix: refresh the baseline per release (CLAUDE.md says a gate change needs a decisions line); make deploy.py run the
gate (or at least refuse when a hard Rule #1 row FAILS). Test: baseline NBA mid-play 10 must not be the allowed ceiling.

### R5 (S2, CONFLICT) Deploy rules vs practice
`runbooks/deploy.md:5` "Only when no game is live and Nick isn't watching"; `:14` "Never deploy from `main` or an
unmerged branch"; CLAUDE.md "`codex/resume-redzone` is the base and what the Mac runs" and "No disruptive TV
tests while Nick is watching". `handoff.md:80-82` live build is `claude/buffered-jump 0b0f8534`, and §9 describes
a wrapper that "bypasses busy()" for deploys during live games. decisions.md (Nov 7 12:08 "You can deploy
whatever", and the deploy gate option line ~1210) explains it, but neither CLAUDE.md nor the runbook says so.
An agent reading the runbook will refuse; one reading the handoff will deploy an unmerged branch in-game.
Fix: write the Nick authorization (date, scope: busy() bypass allowed, health watch still required, never mid
his-watching tests) into CLAUDE.md and deploy.md, and say which branch the Mac runs.

### R6 (S2, CONFLICT) Three leave rules
1. CLAUDE.md:17 "Leaving mid-play is allowed only at low leverage" (and `northstar.py` `low_leverage_leaves` is INFO).
2. `architecture.md:292` "Mid-play cuts: 0 (hard)"; release_gate `mid_play_cuts == 0`.
3. decisions.md (Nov 7 22:15/22:17, "pass bar"): 0 mid-play leaves, stricter than Rule #1, "applies to every sport".
Config agrees with 2 and 3: `decider.leave.low_leverage: []` (never). Only CLAUDE.md, the first file any agent reads,
still states the looser rule, and nothing in northstar.md / roadmap mentions the 75-jumps-in-10-min bar
(grep: `jump|buffered|pass bar` = 0 hits in CLAUDE.md and northstar.md). Fix: put the pass bar and the "no mid-play
leave" line in CLAUDE.md and in northstar METRICS; mark the low-leverage allowance as superseded.

### R7 (S2, CONFLICT) NFL vs CFB order
decisions.md:354 (Nov 4 3:15 PM) "league order NFL = CFB > NBA > NHL, MLB" and :389 (Nov 4 3:47 PM) same;
`decider.py:2340` `_value_league_rank` implements it ("NFL = CFB (Nick, Nov 4 3:15 PM)") and
`default_config.yaml` `decider.crunch.value_league_prior` comment repeats it. Everything else says NFL > CFB:
CLAUDE.md, redzone-north-star.md (Nov 3), architecture §7 ("NFL > CFB"), `models.LEAGUE_ORDER`,
`core.league_priority`, `plan.py`, `noggin/priority.py`. The newest decision wins, so either CLAUDE.md or the
code is wrong; today the value path (off by default, `by_value: false`) and the strict path would break a
simultaneous NFL/CFB crunch differently. Fix: record which holds (likely NFL = CFB for crunch overlaps only), update CLAUDE.md,
and make `league_order` and `_value_league_rank` read one source.

### R8 (S2, CONFLICT) Women's basketball
decisions.md:473-475 (Nov 4 7:55 PM): "women's basketball is OFF ... The default_config change (wcbb false, out of
league_priority) is a separate PR." Four days later `default_config.yaml` still has `mode.leagues.wcbb: true`
(l.7), `teams.wcbb: [UNC]` (l.22), `college.groups.wcbb` (l.28), `league_priority` includes wcbb (l.474), and the
Nov 8 decision (decisions.md:1263) adds wcbb to `buffered_jump.leagues` (l.416) with no mention of the reversal.
WCBB games can take a stick and win overlaps. Fix: ask Nick; if still off, set the defaults and remove from
`buffered_jump.leagues`; if now on, log the reversal and lift the NICKS_LEAGUES exclusion.

### R9 (S2, GAP) Gates that do not measure the North Star
- No release-gate row for "no commercials" / dead air / mid-commercial entries. `northstar.md` shows Entries
  mid-commercial 41.5% FAIL and Dead air 19.2% FAIL, but `release_gate.py` has no such row, so they cannot block.
- `release_gate.py:200-201`: your-team crunch live `pending=v == 0.0`. A genuine 0% (every your-team crunch play
  missed) prints as "no plays in this slice" and is PENDING, never FAIL. Code has a TODO acknowledging it.
- "crunch live >= 95%" is PENDING when under 95 (never FAIL) until a ceiling exists that "is not built".
Fix: emit counts in the scorecards and FAIL on n>0 and 0%; add a mid_break row to `gates()`.

### R10 (S2, CONFLICT) Promotion check ignores leaves
`release_gate.py:233-262` `league_promotion` FAILs only on `entries_mid_play != 0`. It never reads `mid_play_cuts` or
high-leverage leaves, which CLAUDE.md says no PR may raise. Metrics whose key is absent are skipped
(`if a is None or b is None: continue`, l.249), so a candidate card missing `dead_air_pct` or `plays_missed_entry`
is judged on fewer lines and can PASS. Fix: require `mid_play_cuts == 0` and treat a missing line as PENDING.

### R11 (S2, CONFLICT) Stale docs that mislead
- `status.md` header "Last update 2026-11-04 05:45 UTC" (file mtime Nov 7), claims "updated on every merge";
  queue lists #133/#132 etc. and "Pacing ... 89% at 5 PM Wed". It has no jump mode, buffered jump or pass bar.
- `handoff.md` §5/§6 (Nov 7 22:45): "buffered_jump ... leagues nfl, cfb" (default is now five leagues);
  next step 1.3 "Add NBA and CBB to buffered mode" is done (decisions Nov 8, config l.416); step 1.1 "screen_game_unknown
  after a restart" is addressed in code (`engine.py:2920`, `decider.py:712`, `control.py:2329`). §7 still says "max 5
  working threads" while Nick (Nov 7 22:35) said do it yourself.
- `roadmap.md` rank 1 is the NOGGIN foundation model; Nick's Nov 7 21:05 order is mechanics first. Generated from
  Nov 6 `fce4fd8`.
Fix: have the northstar workflow also regenerate status date; in handoff mark done items; add the pass bar to roadmap.json.

### R12 (S3) Jump / buffered league defaults
Decision Nov 7 23:35: `jump_mode.leagues` "restored to [cfb, nfl, nba, nhl, cbb]" (runtime only). Default is
`[cfb, nfl]` (l.383). Because `_jump_league` unions in `buffered_jump.leagues`, NBA/CBB work once buffered is on,
but NHL and MLB never join the rotation by default, while the pass bar says every sport and NHL is #5 in the order.
Fix: set the default to the intended list or write down that NHL/MLB are out of the pass bar.

### R13 (S3) Usage cap numbers
CLAUDE.md and `usage-pause.md`: stop at 88%. `redzone-north-star.md` and `status.md`: stop at 90%, pause at 88-89%.
`usage-pause.md` pacing (Nov 3-4 percentages) is obsolete. Use 88% everywhere.

### R14 (S3) wcbb ranking inconsistent
`score.league_weight` wcbb 0.9 (= MLB, below NHL 0.95); `my_team_league` wcbb 0.6 (below NHL 0.72, MLB 0.64)
but `core.league_priority` puts wcbb before NHL. architecture §7 lists "NFL > CFB > NBA > the rest (NHL, MLB)",
dropping CBB from the order. Moot if R8 turns wcbb off; otherwise align them.

### R15 (S3) "Nights" are league-nights
`northstar.py:260` builds `ev` from every league card row, so "(18)" nights on the locked set is 10 NFL + 4 NBA + 4 NHL.
The `>= 8 nights` PASS bar for pooled Mid-play entries is met by pooling, but per league NBA and NHL locked sets
have 4 nights, below `MIN_NIGHTS = 8` (northstar.md:78), so no per-league NBA/NHL rate can ever PASS until
eval-data-v3. Label the column "league-nights" and show per-league status.

### R16 (S3) Decision rule is weak
`scripts/check_memory.py` only checks that `decisions.md` changed when `default_config.yaml`, `gate_baseline.json` or
`release_gate.py` changed (l.21). Any line counts. R4/R8 show defaults drifting without matching entries.
Fix: require the changed key names to appear in the added decisions lines.

### R17 (S3, GAP) Rule #1 defaults outside the promoted leagues
Global defaults: `decider.entry.hold_only: [nhl, nba]`; `start_timed: false` (decisions.md:825: 6 of 8 NHL dev
mid-play entries were first picks); `unknown_wait_leagues: []`; `conformal.enabled: false`; `possession_start: "off"`;
`user_timed_targets: [youtubetv]` ("Prime taps go as before", comment: 15 taps landed mid-play). `league_overrides`
fix NBA/NHL/NFL only. CFB, CBB, MLB run the ungated defaults with no scorecard (see R2). Phone taps to Prime
are arguably a Nick control, but the decision should say so.

## What's solid
- League order has ONE definition (`models.LEAGUE_ORDER` / `league_order(cfg)`), read by core, plan, noggin
  priority, ledger; no stray ordered copies found. Teams: `teams:` has WSH for nfl/nba/nhl/mlb and UNC for cbb/cfb/wcbb.
- `default_config.yaml` parses with no duplicate keys at any depth (the Nov 7 dropped-`leave:` bug is gone).
- Defaults checked against decisions and matching: `replay.scrub_max_play_age_s` 95 and `park_after_tune_s` 22
  (Nov 8, the later 110 -> 95 wins), `buffered_jump.leagues`, `hold_chunk_s` 25, `keep_standby_s` 120,
  `timing.behind_live_s.resume_settle_s` 6, `decider.uncalibrated_sigma_s` 8, `crunch.mine_first`, `yttv_seek.*` off,
  `label_reader`/`scrub_entry`/`edge_model` off, `tuners.yttv_local_search` false, `replay.yttv_seek: scrub`.
- Ships-off discipline holds: `jump_mode`, `buffered_jump`, `hardening.*`, `noggin.pick/belief/priority` (globally),
  `decider.dwell`, `lock_wait`, `break_floor_s`, `timing.behind_live_s` are all off by default and the decisions say why.
- `northstar.py` models Rule #1 correctly in its own terms (entries hard 0 with rule-of-three, leave leverage split,
  mid_break line, honest FAIL/PENDING, bootstrap by night); `release_gate.rate_gate` never counts PENDING as pass.
- Runtime-only settings (Nick's live toggles) are recorded in decisions.md as runtime-only, with dates.
- Memory docs exist and link: glossary, runbooks, new-environment, context_pack, fetch_eval_data all present.
