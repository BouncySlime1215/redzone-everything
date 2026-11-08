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
| P0 | Process rules: the AI-documentation rules, debugging/validation/merge rules, "why / routes / relied on by" for every piece of code, locked test results, harness | Wave-1 research read; gate items in section 2 done and back-filled; CI enforces them | **active** |
| P1 | Switching accuracy (timing only, not game choice): find the bottlenecks, then join like NFL RedZone, ~60-90 s behind live, on both HDMIs, Prime and YouTube TV | Section 3 bars met on live tests listed in [test-queue.md](test-queue.md) | next |
| P2 | The brain (Noggin): decides which game and when | Section 4 | later |
| P3 | Audit + plan loops after P1 and after P2 | Section 5 | later |

## 2. Process gate items (process agents append; coordinator closes)

| Id | Finding (link) | Fix | Back-filled? | Closed |
|---|---|---|---|---|
| G-01 | Organization standard for every report and finding (docs/STANDARD.md, Nick 2026-11-08) | `scripts/doc_index.py` + CI; wave-1 moved to docs/agents/<stream>/ | Nov 8 research/audits still in old layout: back-fill pending | open |
| G-02 | W1-RULES: a fix needs a regression test (not only auto-fix PRs) | `scripts/fix_needs_test.py` + CI + PR template Repro/Root-cause | — | open |
| G-03 | W1-RULES: Stop hook so a turn can't end with checks failing | `.claude/hooks/stop_gate.py` + settings | — | open |
| G-04 | W1-RULES: debugging protocol (reproduce, bisect, root cause) | runbook `debugging.md`, `scripts/bisect_test.sh`, `debugger` subagent, lessons `Repro:`/`Bisected-to:` | lessons back-fill | open |
| G-05 | W1-RULES: merge safety is prose only | runbook `merge.md`, `merge_group` CI trigger, rerere; branch protection is Nick's click | — | open |
| G-06 | W1-RULES: the one plan isn't enforced | `scripts/plan_check.py` (sections, owners), handoff size cap | old plan files marked superseded | open |
| G-07 | W1-CONTEXT: context card on every file read (why / routes / relied on / tests / invariants) | PostToolUse Read hook + card builder from citymap; module `Why:` header check with ratchet | ~360 module headers back-filled from decisions/lessons | open |
| G-08 | W1-ACCESS + W1-SEALED + W1-TAMPER: locked test data and results leak to agents and public CI | one combined design: deny rules + fail-closed hook (G8), strip locked scores from CI output, sealed private grader repo, hash-chained results log | eval data split moved; old CI summaries | open |
| G-09 | W1-HARNESS: guard.py misses the terminal and computer-use tools, quoted `adb`, Grep and Glob; `git push` needs an `ask` rule | harden `.claude/hooks/guard.py` + deny/ask rules; `autoContinueAtUsageLimit: false`; hourly fresh coordinator tick instead of one immortal session | scheduled tasks re-pointed | open |

## 3. P1 switching accuracy (coordinator)

Bars (Nick, 2026-11-08):
- NFL: join after a play, preferably after the replay, seconds before the snap. Never late into a play.
- Basketball: may join mid-possession only right after a made or missed shot (the walk/transition up court). If the
  moment is a transition play, divert to the prior safest point (previous possession's bucket, transition or dead
  ball, whichever is soonest).
- Pace like RedZone: several switches and clips per minute when the action is there (measured: median 5 per
  10 min, 9 busy, 14 max on NFL Sundays, [redzone-numbers](research/2026-11-08/redzone-numbers.md)), 60-90 s
  behind live, 0 commercials on screen, 0 late joins.
- Works on both HDMI inputs and on both Prime Video and YouTube TV.

Steps: (1) instrument every stage of a switch (decision, tune, scrub/hold, flip, picture) and measure on recorded
nights + live tests; (2) rank bottlenecks; (3) fix packages A-C of [the fix plan](plans/2026-11-08-fix-plan.md) that
touch timing; (4) live tests from [test-queue.md](test-queue.md) until the bars hold.

## 4. P2 the brain (later; research streams start now)

Nick's goal: a model that feeds every decision (which game, crunch time, all plays, switch management) and can
help fix mistakes. Open question for Nick (see section 7): "billions of parameters on a basic Mac" vs the 8 GB Mac.

## 5. Audits and planners (P3)

After P1 and after P2: auditors review and report to planners; planners write only the "Planner section" below.
At most 2 audit rounds when code and prod work; then 1-2 loops of safety measures, then move on.

### Planner section (planners write here only)

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

- 2026-11-08: organization standard adopted (docs/STANDARD.md); wave-1 reports moved to docs/agents/<stream>/ with
  front matter; gate items G-01..G-08 opened. Standing streams scheduled (process 3x/day, training and data daily).
- 2026-11-08: plan created; wave-1 research launched (process rules, code comprehension, locked results x3,
  Mac harness, single-plan practice). Merge of integration/next into PR #423 under full-suite test.

## Brief

Nick, 2026-11-08 (summary): establish the rules AI documentation calls for; research debugging, code checking,
validation, merging, organization, finding bug sources; every time an AI views code it should know why it exists,
where it routes and what relies on it. Frequent agent searches into our own rules; worth-fixing findings pause work
until fixed and back-filled. Then P1 switching accuracy, then P2 the brain, with standing research and data agents,
auditors and planners, locked hidden test results, and a low-interaction Mac harness. The coordinator manages all
workflows from this one plan, keeps a test queue, and runs the TV tests.
