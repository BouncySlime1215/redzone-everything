# Decisions

Every decision Nick made, newest first, with the date and the reason. Times are ET unless they
say UTC. **A PR that changes a default (`redzone/default_config.yaml`) or a gate
(`docs/gate_baseline.json`, `scripts/release_gate.py`) adds a line here**; `scripts/check_memory.py`
enforces it in CI. Gate changes in architecture.md §7 need a line too (by hand).

Format: `- YYYY-MM-DD: **decision.** Why.`

## Prime NBA multiview (Nov 3, 7:45 PM)

- 2026-11-03: **Prime NBA multiview ships OFF (`tuners.prime_multiview: false`).** It turns on only after a live Prime NBA night passes its gate: >= 95% right-tile switches under 2 s (>= 20 switches) and 0 mid-play entries. Why: the key paths are guesses until `scripts/prime_multiview_probe.py` records them on the real sticks.
- 2026-11-03: **No team names in code; exclusions and priorities live in the personal config** (`score.personal.multiview_exclude` / `multiview_pin`, "league:ABBR", shipped empty). Why: a build we sell must carry none of Nick's data.

## Release candidate 1 (integration/release, Nov 3)

- 2026-11-03: **Brain features ship off in RC 1 (`brain.enabled: false`).** The brain models were trained on seasons that include the locked test nights, so their numbers are optimistic. With the brain off, the locked NFL/NBA/NHL cards are identical, so no gate row depends on a leaky model. It turns back on after the time-cutoff retrain if the gate holds.
- 2026-11-03: **NBA's whistle-wait cap goes 120 → 180 s (`decider.entry.max_wait_s.nba`).** Chosen on the fit split (train + valid), never on the locked nights: 120/180/300/no cap gave mid-play entries 7/4/5/3, and no cap cost big plays seen 90.1 → 85.9 %. Full NBA card: entries mid-play 12 → 7, missed on entry 9 → 10, dead air 20.2 → 20.0 %.
- 2026-11-03: **NBA entries wait for a whistle, capped at 120 s (#21, over #28's rewind-only).** On the full NBA card, entries mid-play drop 30 → 14 and missed on entry 14 → 9, while dead air goes 19.4 → 19.9 %.
- 2026-11-03: **The release gate is judged on the locked test split plus robustness cards** (sync ±3 s, a 10 s stale feed), with `docs/gate_baseline.json` rebuilt from 5f0e6a1 on that split (the no-overfitting rule).

## Auto-fix (#30, merged Nov 3-4)

- 2026-11-04: **`autofix` config block added (enabled, at most 3 issues a day, refile after 7 days).** The watchdog files errors it can't fix as GitHub issues for the auto-fix brain. The brain reads `docs/memory/lessons.md` and the runbooks first, and adds a lesson with each fix PR. Kill switches: `autofix.enabled` on the Mac and the repo variable AUTOFIX_ENABLED=false on GitHub; the workflow runs only from the default branch, and fixes land only as draft PRs behind the guard and the gates.

## Process and budget

- 2026-11-04 (planned): **Move to a new Claude account Wed 5 PM.** Everything an AI needs lives
  in this repo ([new-environment.md](../new-environment.md)), so memory work lands before then.
- 2026-11-03 4:20 PM: **Pace usage to reach ~90% at 5 PM Wed.** Checkpoints ≤55% at 10 PM,
  ≤62% at 8 AM, ≤78% at 1 PM. At most 2 working threads (1 overnight, midnight to 8 AM).
  Priority: tonight's release, the decision core, memory systems, the Kaggle round-2 fix, the rest.
  Why: the old account has to last until the move.
- 2026-11-03 3:11 PM: **All Claude work stops at 88–90% of weekly usage**; Nick pauses the
  project at 88–89%. Why: he keeps the last 10% for school. From 75%: no new threads, finish and merge.
- 2026-11-03 3:38 PM: **Cut `integration/release` from a green `integration/next` by 7 PM**
  with the gate report attached; flags OFF for anything that misses.
- 2026-11-03 1:29 PM: **"Stop asking me questions, everything is run by you."** The coordinator
  decides trade-offs within the rules and pings Nick only for actions that need his hands.
- 2026-11-03: **Coordination first.** Manage and integrate before starting anything new; one
  integration branch (`integration/next`), one owner per area, max 5 working threads.
- 2026-11-03: **$0 budget; never enter passwords or tokens.** Nick adds secrets himself.
- 2026-11-02: **No build until he says "build" or "go".** "Lock in" means plan harder.

## Gates and quality

- 2026-11-04 12:45 AM: **Timing gate rows added** (overnight plan item 6): "entries mid-play = 0" and
  "landings in dead time >= 95%" (cuts away + entries, from the new `landings_in_dead_pct`), with the
  landing-offset histograms (`leave_to_restart_hist`, `entry_to_restart_hist`) on every league card.

- 2026-11-03 1:18 PM (approved 1:20): **"95% good minimum" release gate** on the locked test set,
  fresh nights and the e2e night test (full table: [architecture.md §7](../architecture.md)).
  A part that misses ships OFF behind its flag. Why: he wants every measure at 100%; 95% is the floor.
- 2026-11-03 1:02 PM: **Target 100% on every measure** (0 mid-play entries, 0 missed on entry,
  0 dead air, 0 cuts, 100% your-team). Where 100% is impossible, chase the computed ceiling.
- 2026-11-03 1:29 PM: **No overfitting.** Split by time; a locked test set of ≥25% of nights,
  scored once per release candidate; fresh nights reported separately; 5-fold forward-chaining CV
  (mean and worst fold); leakage checks in CI; per-slice reports; robustness at ±3 s sync, a 10 s
  stale feed, one stick down.
- 2026-11-03: **No PR may raise mid-play entries, plays missed on entry or dead air** without a
  measured reason at the top of the PR. Why: he rejected #2/#3 drafts that traded these for headlines.
- 2026-11-03: **Self-review before each PR and list known risks in it.**

## Product

- 2026-11-03 3:05 PM: **NFL commercials and dead air go to 0**, with replays when every game is
  in a break. #21 accepted NBA dead air +0.5 and demo commercials +0.5 for NBA mid-play entries 38 → 15.
- 2026-11-03: **Crunch overlaps:** top game live, the other time-shifted on the second stick,
  a highlights catch-up after the crunch, then rejoin live. League order NFL > CFB > NBA > NHL > MLB,
  your team first.
- 2026-11-03 17:06 UTC: **PR #2 ships as "Keep drive gain, 9.7% dead air".** Drives live
  13.1 → 19.6%, big plays live 29.9 → 33.6%, NFL dead air 8.6 → 9.7% (demo-day commercials 15.7 → 15.6%).
- 2026-11-03: **PR #8 (sync): NBA trade 124 → 132 accepted** as its measured reason.
- 2026-11-03: **PR #4 (Hype v2) held** until #2's dead-air fix; later folded into Brain v3 (#4 closed).
- 2026-11-03: **PR #1 (trip budget) approved as is.** Order #1, then #3 after its entry fix, then #2.
- 2026-11-03: **No Mac as the HDMI source.** Keep the two sticks and the TV.
- 2026-11-02: **Never Peacock.** NBC/NBCSN come through YouTube TV (Peacock can't be screenshotted for sync).
- 2026-11-02: **Sync is fully automatic:** no taps, no mic, no camera.

## AI

- 2026-11-03 2:37 PM: **Two tiers.** REFLEX = a local ~2 GB distilled student (Qwen3-1.7B, 4B only
  if needed) that says "I don't know" when unsure. BRAIN = Claude auto-fix through a GitHub issue →
  `claude-code-action` draft PR on his subscription token (`CLAUDE_CODE_OAUTH_TOKEN`, he adds it),
  capped at 3 runs a day, live only from `main` after the release.
- 2026-11-03 1:20 PM: **Local AI ≤ ~2 GB RSS** (4-bit), lazy load, unload when idle, background
  priority. **All training on Kaggle (2× T4) or cloud, never the Mac.**
- 2026-11-03 2:38 PM: **REFLEX reasoning add-ons are ablated one by one**; ship only what moves the gates.
- 2026-11-03 3:01 PM: **Round 2 excludes brain features** until the leak-free brain retrain lands
  (the NFL brain had trained on locked 2025 nights).
- 2026-11-03 10:05 PM: **Deploy test step: 3600 s timeout, speed budgets skipped at background
  priority** (`RZ_TESTS_LOW_PRIORITY=1`), and the e2e night pins the host (governor) so the Mac's live
  app can't restart the night's engine. Every other test still blocks a deploy. RC 3 = RC 2 + these.
