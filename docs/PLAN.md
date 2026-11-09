# RedZone plan (the only plan)

This is the one plan for the repo. Other plan files (docs/plans/*, roadmap, status, cycles) are history or
inputs; when they disagree with this file, this file wins. Owner of the whole file: the coordinator session.
Each section names who may write it. Started 2026-11-08 from Nick's brief (quoted in "Brief").

## 0. How this plan runs (coordinator)

- Phases go in order. A phase moves on only when its exit check passes.
- **Process gate:** when a process-research agent finds something worth fixing, product work pauses, the fix
  is made and back-filled across the existing code and docs, then work resumes. Gate items live in section 2.
- Every agent reports into files under `docs/agents/` (one file per stream). Raw results never go in chat.
- Usage pacing: all Claude work stops at 88% weekly. Standing agents run on a schedule sized to the remaining
  budget (section 6). Research agents default to Sonnet, reads to Haiku.
- Hard limits that no instruction overrides (safety rules for this assistant): no creating accounts, no typing
  passwords, API keys or tokens into anything, no reading secret files. Nick creates accounts and pastes keys
  himself; agents say exactly where and what. Everything else (code, deletes, Chrome for research and settings
  Nick approved, decisions not involving money) is the coordinator's call.

## 1. Phases

| # | Phase | Exit check | Status |
|---|---|---|---|
| P0 | Process rules: the AI-documentation rules, debugging/validation/merge rules, "why / routes / relied on by" for every piece of code, locked test results, harness | Wave-1 research read; gate items in section 2 done and back-filled; CI enforces them | done |
| P1 | Queue games fast and switch accurately (build plan P-BUILD): route truth, queue core, scrub and tune speed, commit gate; then join like RedZone, 60-90 s behind live, both HDMIs, Prime and YouTube TV | Section 3 bars met on live tests listed in [test-queue.md](test-queue.md) | **active** |
| P2 | The brain (Noggin): decides which game and when | Section 4 | later |
| P3 | Audit + plan loops after P1 and after P2 | Section 5 | later |

## 2. Process gate items (process agents append; coordinator closes)

| Id | Finding (link) | Fix | Back-filled? | Closed |
|---|---|---|---|---|
| G-01 | Organization standard for every report and finding (docs/STANDARD.md, Nick 2026-11-08) | `scripts/doc_index.py` + CI; wave-1 moved to docs/agents/<stream>/ | Nov 8 research/audits still in old layout: back-fill pending | closed: Nov 8 research and audits have front matter and are in the generated index |
| G-02 | W1-RULES: a fix needs a regression test (not only auto-fix PRs) | `scripts/fix_needs_test.py` + CI + PR template Repro/Root-cause | — | closed |
| G-03 | W1-RULES: Stop hook so a turn can't end with checks failing | `.claude/hooks/stop_gate.py` + settings | — | closed |
| G-04 | W1-RULES: debugging protocol (reproduce, bisect, root cause) | runbook `debugging.md`, `scripts/bisect_test.sh`, `debugger` subagent, lessons `Repro:`/`Bisected-to:` | lessons back-fill | closed |
| G-05 | W1-RULES: merge safety is prose only | runbook `merge.md`, `merge_group` CI trigger, rerere; branch protection is Nick's click | — | closed; branch protection is Nick's click (docs/ci.md) |
| G-06 | W1-RULES: the one plan isn't enforced | `scripts/plan_check.py` (sections, owners), handoff size cap | old plan files marked superseded | closed |
| G-07 | W1-CONTEXT: context card on every file read (why / routes / relied on / tests / invariants) | PostToolUse Read hook + card builder from citymap; module `Why:` header check with ratchet | ~360 module headers back-filled from decisions/lessons | built and wired; 89 of 411 module headers done, 322 remain (ratchet), back-fill in waves |
| G-08 | W1-ACCESS + W1-SEALED + W1-TAMPER: locked test data and results leak to agents and public CI | one combined design: deny rules + fail-closed hook (G8), strip locked scores from CI output, sealed private grader repo, hash-chained results log | eval data split moved; old CI summaries | free tier closed (W2-LOCKED): verdict-only CI, hash-chained ledger, G8; private grader repo, bot account and branch protection are Nick's clicks (~45 min, in the design) |
| G-09 | W1-HARNESS: guard.py misses the terminal and computer-use tools, quoted `adb`, Grep and Glob; `git push` needs an `ask` rule | harden `.claude/hooks/guard.py` + deny/ask rules; `autoContinueAtUsageLimit: false`; hourly fresh coordinator tick instead of one immortal session | scheduled tasks re-pointed | closed; autoContinueAtUsageLimit and Auto mode are Nick's settings |

## 3. P1 queue and switching (coordinator)

Goal: any reachable game parked on the hidden stick fast, cut only at dead balls. Build:
[P-BUILD](agents/planner/2026-11-09-build-plan.md), waves 0-2 and T1-T3.

Nick's bars (2026-11-08, unchanged):
- Rule #1: never join mid-play. Leave mid-play only at low leverage; never a high-leverage moment.
- NFL: join after a play, preferably after the replay, seconds before the snap. Never late into a play.
- Basketball: join mid-possession only right after a made or missed shot. If that moment is a transition play, use the prior
  safest point (previous bucket, transition or dead ball, whichever is soonest).
- Pace: 2-4 games for a few minutes at a time, 60-90 s behind live, 0 commercials on screen, 0 late joins. Rotating across
  many games and catching up on the rest is section 4 work.
- Football must-show: every touchdown and major play: turnover, 10+ yard gain, field goal, big 3rd down, 4th down, large EPA
  (5-point win-probability swing until nflverse EPA). Plays the live rotation can't reach become catch-up clips.
- Both HDMI inputs, both Prime Video and YouTube TV.

Queue bars (P-MAJOR section 12, all p90 unless noted):
- YouTube TV ready: 45 s is the gate, 30 s the target, 20 s the stretch. Scrub failure under 10%. Flip 2.5 s p50.
- Prime: tune 25 s, long lead only (about 90-105 s to ready) until a test shows it can seek. NBA plans around this.
- A failed tune advances the queue inside its budget. Nothing waits 75 s or more.
- No route flagged "may be blacked out" is ever ready. The phone lists what can't be reached tonight.
- Model ceiling on 2-4 games: 13-15 jumps per 10 min. Must-see seen is the bar; cut rate is an output.

Phases, each exits on its check:
1. Queue core, routing, scrub, Prime, commit gate (waves 0-1). Exit: tests pass, flags off, scorecards unchanged.
2. Queue owns the hidden stick, phone card (wave 2). Exit: L4 meets the YouTube TV gate on a hidden stick.
3. Full NFL Sunday, hidden stick, app alone on the Mac (T3). Exit: 7 h with no stuck stick; failures listed.
4. Live on screen, flags on one league at a time. Exit: 95% gates on the locked set plus fresh nights.

## 4. P2 the show and the brain

Show first, then brain. One Tier 1/2/3 list per league. Tier first, league order as tiebreak, your teams first, crunch first.
Every Tier 1 airs live or rewound by its route's deadline: 2-3 min on YouTube TV, clip lag on routes that can't seek.
A cut lands before one play, ends when the play ends, and needs a payoff. Stops and failed 4th downs count.

The brain outputs numbers and words, never an action. Code lists the legal options, the arbiter picks, the commit gate has
the last word at the flip. Baseline runs when the brain is off, late or invalid. Models: small trees live under 100 MB, no
LLM during games, large models offline on free Kaggle only, nothing learned from Nick's taps. First live surface: preload.
Exit per model: beats the state table on the worst fold, 7 of 7 shadow nights, then locked once plus fresh nights.

## 5. Standing order, audits and planners (P3)

Order of work: Rule #1 and its facts (sync, delay), then stick speed, then the gate, then the brain. No model is graded
before the sticks can act on it. Only the near-term list runs until a live Sunday holds.
Always: one writer per file; new behaviour ships off; frozen graders stay frozen; locked set once per release; no TV test that
disturbs Nick; hidden stick first; no training on the Mac; game nights the Mac runs RedZone only; all Claude work stops at 88%.
After P1 and after P2: auditors report to planners; planners write only the section below. At most 2 audit rounds when code
and prod work, then 1-2 loops of safety measures, then move on.

### Planner section (planners write here only)

Also update the P1 row in section 1 to read "Queue and switching", with "Section 3 phases" as its exit.

## 6. Standing agent streams

| Stream | Agents | Writes to | Cadence |
|---|---|---|---|
| Process: our AI rules and how we work | 1 | `docs/agents/process-findings.md` | scheduled, budget-paced |
| Training efficiency, new techniques | 2 | `docs/agents/training-research.md` (shared) | scheduled; ramps up in P2 |
| Free large datasets | 2 | `docs/agents/data-catalog.md` (shared database of what we have and how much) | scheduled |

## 7. Questions for Nick

1. Weekly usage: round-the-clock agents vs the 88% stop (44% used now, resets Nov 11). Proposed cadence in section 6.
2. Brain size: billions of parameters cannot run inside the 8 GB Mac's ~2 GB budget next to the live app, and
   pre-training at that scale is not $0. Options: fine-tune an open ~1-3B model on free GPUs and run it 4-bit
   (~1-2 GB), or keep the brain small and fast with a bigger model offline.

## 8. Log (coordinator, newest first)
- 2026-11-09 overnight: build plan waves 0, 1, 1b, 2 and most of 3 merged one at a time on PR 423: route truth, queue core and wiring, queue feed, typed failures, scrub speed, app tuners and the purchase guard, commit gate and its adversarial fixes, Ticket scan, moments, brain contract, legal options, arbiter, slate and fill, phone queue, screen clock, Mac preflight and drills, sim mix, audits, show scorecard. Brain data: 17 jobs, reports in docs/agents/data/, ledger D-LEDGER. All new features are OFF. Full suite 5,619 passed, 0 failed before WP-26 merged. None of it has run on the real TV. Open: backup-route flip, NBA 8-11 s after-shot joins against the frozen grader, Nick launches the Kaggle runs.

- 2026-11-09: five planners (docs/agents/planner/) and the build plan (P-BUILD) done; sections 3-5 replaced with the build plan.
  Wave 1 launched: route truth, queue core, scrub speed, app tuners, commit gate, Sunday Ticket scan. TV off.

- 2026-11-08 night: P0 process gate closed (G-01..G-09). P1 sprint packages A-H, R, M, S merged one at a time; full suite
  4359 passed, 0 failed. Not yet run on the real TV. Live tests are hidden-stick only until Nick says otherwise.

- 2026-11-08: organization standard adopted (docs/STANDARD.md); wave-1 reports moved to docs/agents/<stream>/ with
  front matter; gate items G-01..G-08 opened. Standing streams scheduled (process 3x/day, training and data daily).
- 2026-11-08: plan created; wave-1 research launched (process rules, code comprehension, locked results x3,
  Mac harness, single-plan practice). Merge of integration/next into PR #423 under full-suite test.

## 9. Progress (coordinator)

Phase status is section 1; gate items are section 2. Now: P0, closing G-02 to G-09.

## 10. Surprises & Discoveries (coordinator; one line, date, link)

- 2026-11-08: `docs/handoff.md` is 305 lines against an 80-line target ([W1-RULES](agents/rules/2026-11-08-debug-validate-merge.md) gap 5). `check_memory.py` holds it at 305 until it is trimmed.

## 11. Decision Log (coordinator; one line, date, link)

Nick's decisions live in [decisions.md](memory/decisions.md). Decisions about this plan go here.

- 2026-11-08: only one plan; `docs/plans/*` carry a superseded line, checked by `scripts/plan_check.py`.

## 12. Outcomes (coordinator; filled when a phase exits)

None yet.

## Brief

Nick, 2026-11-08 (summary): establish the rules AI documentation calls for; research debugging, code checking,
validation, merging, organization, finding bug sources; every time an AI views code it should know why it exists,
where it routes and what relies on it. Frequent agent searches into our own rules; worth-fixing findings pause work
until fixed and back-filled. Then P1 switching accuracy, then P2 the brain, with standing research and data agents,
auditors and planners, locked hidden test results, and a low-interaction Mac harness. The coordinator manages all
workflows from this one plan, keeps a test queue, and runs the TV tests.
