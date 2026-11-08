---
id: W1-RULES
title: Debugging, validation, merge and organization rules for coding agents
date: 2026-11-08
stream: rules
author: wave-1 rules agent
status: draft
supersedes: []
sources: 28
verified: 25
affects: [.claude/settings.json, .claude/hooks/, .claude/agents/, scripts/, .github/workflows/ci.yml, docs/PLAN.md]
gate: []
---
# Rules for debugging, validation, merging, organization, one plan

Wave-1 process research, 2026-11-08. Scope: what agent documentation and recent papers require of coding
agents, how to enforce each rule mechanically here, and what this repo already does. It does not repeat
[rule-files](../../research/2026-11-08/agents/rule-files.md), [coordination-sota](../../research/2026-11-08/agents/coordination-sota.md),
[safe-agents-realtime](../../research/2026-11-08/agents/safe-agents-realtime.md) or
[memory-and-handoffs](../../research/2026-11-08/agents/memory-and-handoffs.md); where one of them already holds a
finding, this note links the repo status only.

Method: every source in section (c) was opened on 2026-11-08 unless marked "snippet only". Repo state checked in
`.claude/` (3 hooks, 3 subagents, no Stop or PostToolUse hook), `scripts/`, `.github/workflows/ci.yml`
(jobs lint, memory, graders, data, test, scorecard, report; no `merge_group` trigger), `docs/coordination.md`.
Also checked: no `CODEOWNERS`, no `docs/cards/`, no `docs/reviews/`, `docs/claims/` empty, `docs/handoff.md` is 305 lines.

## (a) Rules table

Priority: P1 fix now, P2 next, P3 later. "Done?" is yes / partly / no for this repo today.

| # | Rule | Sources (section c) | Enforce here | Done? | Pri |
|---|---|---|---|---|---|
| 1 | Reproduce first: have the exact command that shows the bug and its output before editing | S1, S8, S9 | Bug card / PR section "Repro: command + output before"; CI check on PR body for a `Repro:` line when the title starts `fix` | no (advice only in lessons "Symptom") | P1 |
| 2 | Write a failing test that reproduces the bug, then fix (fail-to-pass) | S1, S12, S9 | `scripts/fix_needs_test.py` in CI: a `fix` PR must add or change a file under `tests/` or carry `No-test: <reason>`; reuse the logic already in `scripts/autofix_guard.py` | partly (auto-fix PRs only) | P1 |
| 3 | The new test must fail on the base commit and pass on the head | S12, S22 | CI step: check out base `redzone/`, run only the new tests, expect red; then head, expect green | no | P2 |
| 4 | Map symptom to code path before editing: trace the flow, find callers, name the root cause in one line | S8, S15, S16 | `scripts/why.py`, `docs/system-map.md`, `citymap --impact`; add "Root cause (one line, file:line)" to PR template and `lessons.md` (CI: new lesson needs `Cause:`) | partly (tools exist, no required field) | P1 |
| 5 | Bisect with an automated check, not by reading: `git bisect run` with exit 0 good, 1-124 bad, 125 skip | S10, S7 | `scripts/bisect_test.sh <pytest-node-id> [night]` wrapper plus runbook; exit 125 when the build or data is missing | no | P2 |
| 6 | Use a known-good oracle and delta-debug against it when everything fails at once | S7 | Recorded nights plus `northstar` baseline are the oracle; add runbook step "swap one module back to base, rerun the night" | partly (baselines exist, no procedure) | P3 |
| 7 | Keep a running note of failed approaches and surprises so the next fresh agent skips them | S2, S7, S18 | Handoff "do-not-retry" block (exists); add "Tried and failed" lines to the bug card; Stop hook reminds when a fix took more than 3 loops | partly | P2 |
| 8 | After repeated failures stop and mark BLOCKED, do not thrash (failed runs are longer and more variable) | S15, S20 | Already rule in coordination §2 (3 loops) and §7; add a `Stop` hook counter: same failing test id 3 times in a session, write `BLOCKED:` to the card | partly (prose only) | P2 |
| 9 | Ask for evidence: the command run and its output, not "all tests pass" | S1, S24 | PR template "Evidence" block with pasted pytest summary line; CI comment prints counts from junit (CI already has junit) and fails if body count differs | partly (before/after table exists) | P2 |
| 10 | A Stop hook makes the check part of "done": the turn cannot end until ruff, changed-file tests and `check_memory` pass | S1, S20 | `.claude/hooks/stop_gate.py`, wired under `Stop` in `.claude/settings.json`; exit 2 with stderr; honor `stop_hook_active`; note the harness ends the turn after eight blocks in a row | no | P1 |
| 11 | Verify with a fresh-context reviewer that never saw the work; ask for correctness only, skip style | S1, S21 | `rule1-reviewer` exists; make it required: CI fails a risky-path PR with no `docs/reviews/<branch>.md` (coordination §6 says required, nothing checks) | partly | P1 |
| 12 | Agents mark done on a self-report that is often wrong; re-run verification in a clean environment | S24, S25, S22 | CI is the clean rerun (yes); add the held-out check already in release_gate for every PR touching decider; keep locked set hidden | yes for gates, partly for PRs | P3 |
| 13 | Never remove or edit tests to make them pass; tests are the acceptance signal | S2, S22, S25 | `autofix_guard.py` (auto-fix) and G6; extend the deleted/skipped/xfail diff check to all PRs to `integration/next` | partly | P1 |
| 14 | Fast sampled mode and short output so test logs do not flood context or waste hours | S7 | `pytest -q --maxfail=3 -x` in Stop hook; `scripts/ci_scorecard.py --nfl-n 2 --nights 4` exists; keep failure lines as `ERROR <reason>` on one line | partly | P3 |
| 15 | Rebase or merge the latest base into the branch before the final test run; test the combination, not the branch alone | S11, S23 | Add `merge_group` to ci.yml `on:`; GitHub rule "require branches up to date" (strict) plus merge queue on `integration/next` (Nick sets in Settings) | partly (train runs tier 2 on batches; no queue) | P1 |
| 16 | Never merge red: required checks must report and pass; a failing PR leaves the queue | S11, S23, S5 | Required status checks on `integration/next` and `integration/release`; document in `docs/ci.md`; the train script refuses a non-green head sha | partly (prose in integration.md, no verified branch protection) | P1 |
| 17 | Resolve conflicts keeping both sides' intent; textual match is not correct; best LLM resolution is under 60 percent, and 26.5 percent whole-file exact match on real worktrees | S13, S14 | Runbook `merge.md`: after any conflict resolution re-run full tests plus scorecards for touched leagues, list each conflicted hunk in the PR with "kept A because / kept B because"; `git config rerere.enabled true` in worktree setup | no | P1 |
| 18 | Sync conflicts early and often (agents pull, merge, push, release lock); workers can resolve their own conflicts, a separate integrator can bottleneck | S6, S7 | Keep claims (optimistic); add `git merge-base --is-ancestor origin/integration/next HEAD` check to the Stop hook (warn when the branch is behind by more than N commits) | partly | P2 |
| 19 | Organized sessions: one worktree and branch per task, named sessions, resume by name or PR | S1, S8 | Branch name `claude/<card-id>-<slug>`; CI check on branch name; `claude --from-pr` documented in `docs/agents/README.md` | partly (worktrees used, no naming check) | P3 |
| 20 | State in files not chat: progress file plus git log read at session start; commit at the end of each session in a clean, mergeable state | S2, S7, S18 | SessionStart hook (exists) prints handoff age and claims; add handoff line cap (60-80 lines) to `check_memory.py` (now 305 lines) | partly | P2 |
| 21 | One plan, living, self-contained: required sections Progress, Surprises & Discoveries, Decision Log, Outcomes; update at every stopping point | S18, S19 | `docs/PLAN.md` has phases and log but no Surprises or Outcomes sections; `scripts/plan_check.py` verifies headings, owners, date of last log line | partly | P1 |
| 22 | Sections owned by roles; one writer per section; others append to a log, never rewrite (state coordination through files with optimistic writes, not locks) | S6, S18, S19 | `.github/CODEOWNERS` mapping PLAN sections is not possible per section, so split: `docs/PLAN.md` (coordinator only) plus `docs/plan.d/<agent>-<date>.md` fragments the coordinator folds in; CI fails a PR that edits PLAN.md without the coordinator label | no (PLAN §0 says owner, nothing enforces) | P1 |
| 23 | Plan before code; review the spec and plan before implementation; plan items are checkboxes the agent ticks | S19, S8 | Task card requires `done_means` and commands; add `docs/cards/` (does not exist yet) and a card linter | no (cards specified, none exist) | P2 |
| 24 | Layered instruction files with the nearest file winning, size capped (Codex stops at 32 KiB; Gemini splits with imports) | S3, S27, S28 | Add short `AGENTS.md` in `redzone/decider/`, `redzone/control/` with "why / routes / relied on by"; size check in `check_memory.py` | partly (CLAUDE.md only at root) | P3 |
| 25 | Fail-closed on agent-created PRs: workflows on agent pushes need a human look, and an agent's own approval does not count | S5, S4 | Already: protected paths G6, Nick approves; keep `.github/workflows/` protected; add rule "agent never approves its own PR" to coordination §1 | yes | P3 |

## (b) Top 5 gaps to fix now

Most of these touch protected paths (`.claude/`, `.github/`, CI). Per G6 an agent writes the files outside
protected paths and Nick (or a separate grader-style PR) lands the protected ones.

### Gap 1: a fix PR does not have to carry a regression test (rules 1, 2, 13)
Only `autofix_guard.py` demands a test, and only for auto-fix. Human and agent fixes can merge with none, so a bug can return.
Files:
- new `scripts/fix_needs_test.py`: reuse `autofix_guard` diff helpers; fail when the PR title or any commit starts with `fix` and no `tests/` file changed, unless `No-test: <reason>` is in the PR body. Also fail on deleted tests, new skip/xfail (call the same functions).
- new `tests/test_fix_needs_test.py`.
- `.github/workflows/ci.yml` job `memory`: add a step after `commit_why.py` (protected; Nick lands it).
- `.github/pull_request_template.md`: add `## Repro (command and output before the fix)` and `## Root cause (file:line, one line)`.
- `docs/memory/lessons.md` header: require `Test:` line (several lessons already have it).

### Gap 2: no Stop hook, so "done" is a claim (rules 9, 10, 8)
Anthropic documents a `Stop` hook that blocks the turn with exit code 2 until a check passes; this repo has only PreToolUse, SessionStart, PreCompact.
Files:
- new `.claude/hooks/stop_gate.py`: stdlib only; read stdin JSON; if `stop_hook_active` and the same failure repeats, write `BLOCKED:` and allow; else run `ruff check` on changed files, `pytest -q -x` on tests matching changed modules, `check_memory.py`; print failures to stderr; exit 2. Fail open on a crash (same as guard.py).
- `.claude/settings.json`: add the `Stop` block (and `SubagentStop`). Protected path, so Nick lands it.
- `docs/coordination.md` §5: add rule id `V1` "no stop on red" pointing at it. Never run the Mac-heavy scorecards from here (G4); fast tests only.

### Gap 3: no debugging protocol (rules 1, 4, 5, 7)
Lessons record symptom and cause after the fact, but nothing tells an agent the order: reproduce, trace, bisect, root cause, test, fix.
Files:
- new `docs/memory/runbooks/debugging.md` (add to `runbooks/README.md`): the order above in 7 steps, using `scripts/why.py` to map time and symptom to the state machine and sha, then `citymap --impact` for callers, then bisect; "Tried and failed" list; stop after 3 loops.
- new `scripts/bisect_test.sh`: `git bisect start BAD GOOD` then `git bisect run python -m pytest -q -x <node id>`, mapping missing-data or build error to exit 125 (documented git behavior).
- new `.claude/agents/debugger.md` (read-only, fresh context, outputs a root-cause note only; protected path, Nick lands it).
- `docs/memory/lessons.md` template: add `Repro:` and `Bisected to:` lines.

### Gap 4: merge safety is prose, not settings (rules 15, 16, 17, 18)
No `merge_group` trigger, no verified branch protection, nothing on conflict resolution. Merge-Bench and SWE-Git-Bench show LLM conflict resolutions are wrong often even when they look right.
Files:
- `.github/workflows/ci.yml`: add `merge_group:` to `on:` (protected; Nick).
- GitHub settings (Nick, not a file): branch protection on `integration/next` and `integration/release`: required checks lint, memory, graders, data, test, scorecard; require up to date; merge queue; block force push. Record the exact settings in `docs/ci.md`.
- new `docs/memory/runbooks/merge.md`: rebase or merge latest `integration/next` first; on any conflict list each hunk with "kept A / kept B / both" and why; rerun full `pytest` and scorecards for touched leagues; never resolve by editing a test or gate; a red batch is bisected, not retried.
- `docs/integration.md`: link the runbook; train refuses a non-green head sha.
- `.claude/hooks/session_facts.py`: print `git config rerere.enabled` and set it for the worktree (hook edit is protected; else put one line in `docs/new-environment.md`).

### Gap 5: the one plan is owned by prose only (rules 20, 21, 22, 23)
`docs/PLAN.md` says "owner of the whole file: the coordinator", but any agent can edit it, section 2 is written by process agents, and it lacks Surprises and Outcomes. Cursor found shared-status-file coordination made agents timid and locks slowed 20 agents to 2-3; optimistic writes and fresh starts worked better.
Files:
- `docs/PLAN.md`: add sections "Progress", "Surprises & Discoveries", "Decision Log" (one-line entries with date and link), "Outcomes"; keep the Planner and process sections as pointers.
- new `docs/plan.d/README.md` and `docs/plan.d/<agent>-<date>.md` fragments: agents append findings (section 2 rows, log lines) there; the coordinator folds them into PLAN.md. This is the same pattern as `docs/memory/decisions.d/`.
- new `scripts/plan_check.py` (add to `.github/workflows/ci.yml` job `memory`): required headings present; PLAN.md changed only on branches named `plan/*` or by a PR labeled `coordinator`; log is dated newest-first; PLAN.md under 150 lines.
- `scripts/check_memory.py`: cap `docs/handoff.md` at 80 lines (now 305), link it from PLAN.
- `docs/coordination.md` §1: add the rule "only the coordinator edits PLAN.md; everyone else writes a fragment".

Not in the top 5 (next): rule 3 (new test must fail on base), rule 11 (`docs/reviews/<branch>.md` required by CI), rule 24 (nested AGENTS.md with "why / routes / relied on by").

## (c) Bibliography

All opened 2026-11-08 unless marked. Dates are the page's own date or the arXiv submission date; "undated" means the page shows none.

Vendor and tool docs
- S1. Claude Code, "Best practices", https://code.claude.com/docs/en/best-practices (undated, current docs; names verification, Stop hook example, /code-review, worktrees, "write a failing test that reproduces it").
- S2. Anthropic Engineering, "Effective harnesses for long-running agents", https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents (2025-11-26; feature list JSON, progress file, "unacceptable to remove or edit tests", commit at session end, e2e before pass).
- S3. OpenAI Codex, "AGENTS.md guide", https://learn.chatgpt.com/docs/agent-configuration/agents-md (redirected from developers.openai.com; undated; layering root to current dir, 32 KiB cap).
- S4. GitHub Docs, "About Copilot cloud agent", https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent (undated; one branch and one PR per task, rulesets can limit it).
- S5. GitHub Docs, "Review Copilot pull requests", https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/review-copilot-prs (undated; requester's approval does not count, workflows need "Approve and run").
- S6. Wilson Lin, Cursor, "Scaling long-running autonomous coding", https://cursor.com/blog/scaling-agents (2026-01-14; planners/workers, optimistic writes beat locks, integrator removed). Also cited in coordination-sota.
- S7. Nicholas Carlini, Anthropic, "Building a C compiler with a team of Claudes", https://www.anthropic.com/engineering/building-c-compiler (2026-02-05; CI after regressions, GCC oracle plus delta debugging, lock files, LLM-friendly test output).
- S8. Claude Code, "Common workflows", https://code.claude.com/docs/en/common-workflows (undated; "tell Claude the command to reproduce", trace flow, worktrees, plan mode).
- S9. SWE-agent, default config `instance_template`, https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/config/default.yaml (undated; steps: read code, reproduce script, edit, rerun, edge cases). The docs home https://swe-agent.com/latest/ was opened but did not describe the workflow.
- S10. Git docs, `git bisect`, https://git-scm.com/docs/git-bisect (undated; run semantics, 125 skip).
- S11. GitHub Docs, "Managing a merge queue", https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue (undated; tests PR with base and queue; `merge_group`).
- S20. Claude Code, "Hooks reference", https://code.claude.com/docs/en/hooks (undated; only the first 100k of 290k characters read: Stop, SubagentStop, TaskCompleted, PostToolUse, TaskCreated, TeammateIdle semantics).
- S21. Claude Code, "Review locally with /code-review", https://code.claude.com/docs/en/code-review-local (undated; reviews run in a forked subagent; follows CLAUDE.md).
- S23. GitHub Docs, "About protected branches", https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches (undated; strict status checks, linear history, conversation resolution).
- S26. Git docs, `git rerere`, https://git-scm.com/docs/git-rerere (undated).
- S27. AGENTS.md site, https://agents.md/ (undated; nested files, nearest wins, listed test commands get run).
- S28. Gemini CLI, GEMINI.md docs, https://google-gemini.github.io/gemini-cli/docs/cli/gemini-md.html (undated; hierarchical load, `/memory`, `@file` imports).
- S18. OpenAI Cookbook, "Using PLANS.md for multi-hour problem solving" (ExecPlans), https://developers.openai.com/cookbook/articles/codex_exec_plans (undated, page says archived; mandatory living-plan sections).
- S19. Google Developers Blog, "Conductor: context-driven development for Gemini CLI", https://developers.googleblog.com/en/conductor-introducing-context-driven-development-for-gemini-cli/ (2025-12-17; tracks with spec and plan files in the repo, reviewed before code). Details on workflow.md came from a search result only (not opened).

Papers
- S12. SWT-Bench, arXiv 2406.12952, https://arxiv.org/abs/2406.12952 (v1 2024-06, v3 2025-02-07; older, still the reference; generated reproduction tests double precision as a fix filter).
- S13. Merge-Bench, arXiv 2605.25890, https://arxiv.org/abs/2605.25890 (2026-05-25; 7,938 hunks, best models under 60 percent).
- S14. SWE-Git-Bench, NeurIPS 2026 poster, https://neurips.cc/virtual/2026/poster/139812 (date not shown; best 26.5 percent whole-file exact match, "an easier output interface does not by itself recover merge intent").
- S15. Majgaonkar et al., "Understanding Code Agent Behaviour: success and failure trajectories", arXiv 2511.00197, https://arxiv.org/abs/2511.00197 (2025-10-31; failed runs longer and more variable; agents find the right file in 72-81 percent even when failing).
- S16. Samir and Rahman, CogniGent, arXiv 2601.12522, https://arxiv.org/abs/2601.12522 (2026-01-18, v2 2026-02-13; hypothesis plus call-graph root-cause localization).
- S17. Chen et al., "Rethinking the Value of Agent-Generated Tests", arXiv 2602.07900, https://arxiv.org/abs/2602.07900 (2026-02-08, v2 2026-04-09; agent-written tests are mostly print probes; more tests did not raise success). Implication used here: require a failing assertion test for a fix, not just exploratory scripts.
- S22. ImpossibleBench, arXiv 2510.20270, https://arxiv.org/abs/2510.20270 (2025-10-23; abstract only: measures test-exploit propensity).
- S24. "Evaluating Misalignment in Coding Agents: Apparent-Success Reporting", NeurIPS 2026, https://neurips.cc/virtual/2026/168218 (date not shown; agents claim all tests pass despite a failure, a required numeric field helps but does not fix it).
- S25. SpecBench, arXiv 2605.21384, https://arxiv.org/abs/2605.21384 (2026-05-20, v 2026-09-09; visible versus held-out test gap measures hacking).

Seen in search snippets only (not opened, not used for any rule): FaultLoc, ICML 2026 (agents recover about 40 percent of root-cause lines); ReProAgent, arXiv 2607.09123; SWEPatchTest, arXiv 2610.18004; a blog survey claiming 24 of 265 "done" claims failed re-verification.

Caveats: most 2026 papers are preprints or conference listings read at abstract level; vendor docs are undated and may change; the Copilot best-practices page returned 404 so no Copilot rule here comes from it; Cursor rules docs redirected to a landing page and were not used; the OpenHands tips page returned 404.
