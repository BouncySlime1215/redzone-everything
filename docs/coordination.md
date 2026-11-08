# How agents work together on RedZone

The protocol for many Claude sessions on one repo. Every rule names what enforces it. Evidence:
[research/2026-11-08/agents/](research/2026-11-08/agents/) (rule files, coordination, safety, memory) and
[agent-coordination-and-rationale](research/agent-coordination-and-rationale.md). How the code connects:
[system-map.md](system-map.md).

## 1. Roles

| Role | Who | Does | Never |
|---|---|---|---|
| Goal owner | Nick | Sets North Star lines, approves releases and grader changes, creates permission files | Reviews every PR |
| Planner | The one INTEGRATION session | Writes task cards, orders them by `citymap --impact` overlap, re-plans after each merge batch | Writes product code |
| Builder | One fresh session per card, own worktree and branch | Implements one card; marks BLOCKED when stuck | Touches protected paths, another card's files, or its own gate |
| Verifier | `rule1-reviewer` subagent, fresh context, read-only | Reconstructs intent from the diff, checks Rule #1 and gaming, writes `docs/reviews/<branch>.md` | Edits code; sees locked-set detail |
| Gatekeeper | CI scripts (deterministic) | Rule #1 counters, scorecards, claims, protected paths, leak checks | Judges taste |
| Merger | INTEGRATION running the train | Folds fragments, runs tier-2 tests on a batch of ≤3, merges to `integration/next` | Merges to `main` or releases without Nick |

## 2. Limits

- **3 builders + 1 verifier + INTEGRATION** by default; 5 builders only when cards are independent and behind
  pace. Stop starting cards when more than 2 PRs wait for review. (Scaling past review capacity loses.)
- Per card: at most 8 attempts; after 3 loops on the same error, stop and mark BLOCKED.
- Usage: no new cards from 75% of the weekly cap; all work stops at 88% with `docs/handoff.md` current
  ([runbook](memory/runbooks/usage-pause.md)).
- Reads go to the `Explore` subagent (Haiku). No unprompted fan-out of subagents.

## 3. Task cards

A card is `docs/cards/<id>.md`, written by the planner. A builder reads the card, not the chat history.

```
north_star_line: <docs/northstar.md metric key>    expected_delta: <number, or "no regression">
baseline_sha: <sha>        files: <globs, the claim>        forbidden: protected paths (section 5)
commands: <exact verify commands>
done_means: <what the verifier will check>
max_iterations: 8          blocked_ok: true
```

A card without an expected delta or a "no regression" line does not start.

## 4. Claims (one writer per file)

1. Before the first edit: `python scripts/claims.py new <card> --files <globs>`, commit the claim on your branch.
2. CI runs `scripts/claims.py check`: a PR that changes a file inside another open claim fails.
3. Claims expire after 24 h; `scripts/claims.py done <card>` when merged.
4. Shared files are never claimed: `decisions.md`, `lessons.md`, `status.md`, `handoff.md`, generated docs.
   Append a fragment (`docs/memory/decisions.d/<pr>.md`); the train folds it in.
5. Cards whose `--impact` sets overlap run one after another, not in parallel.

## 5. Hard rules and what enforces them

| Id | Rule | Enforced by | If blocked |
|---|---|---|---|
| G1 | Never read or print secrets (Read, Grep, Glob, every shell tool) | `.claude/hooks/guard.py` | Ask Nick |
| G2 | No push or merge to `main`, `codex/resume-redzone`, `integration/release`; no force-push; PRs target `integration/next` | guard.py + permission deny rules; `git push` always asks | `gh pr create --base integration/next` |
| G3 | No adb (bare, quoted or by path), stick or TV scripts, or computer-use tools (the TV may be in use) | guard.py; Nick unlocks with `data/.agent_hw_ok` | Use `tests/fake_adb.py` or the simulator |
| G4 | No training or calibration on the 8 GB Mac | guard.py | Kaggle or CI |
| G5 | No real deploy | guard.py; Nick unlocks with `data/.agent_deploy_ok` | `scripts/deploy.py --dry-run` |
| G6 | Protected paths are read-only to agents: `.claude/`, `.github/`, `tests/conftest.py`, release gate, leak check, northstar, eval scripts and locks, gate baseline, grader freeze, results ledger | guard.py + `scripts/autofix_guard.py` in CI | Write BLOCKED, or open a separate grader PR for Nick touching nothing else |
| G7 | Never Peacock | guard.py (+ code check, fix plan) | Pick another provider |
| G8 | Nobody in a Claude session reads locked test data or results, scores the locked split, or downloads full CI artifacts | guard.py (Read, Grep, Glob, shell; the one rule that fails closed) + deny rules; design is [W2-LOCKED](agents/locked-results/2026-11-09-combined-design.md) | `results/summary/latest.json` or CI's PASS/FAIL summary |
| R1 | Rule #1: never join mid-play; never leave a high-leverage moment | CI counters on replayed nights; `rule1-reviewer` on risky PRs | Ship the change OFF behind its flag |
| W1 | Every state change says why | `redzone/fsm.py` (tests fail without a why) | Pass a short reason code |
| M1 | A default or gate change adds a decisions line | `scripts/check_memory.py` | Add the fragment |
| W2 | Every commit has a `Why:` line | `scripts/commit_why.py` in CI | Amend with one sentence of reason |
| V1 | No stop on red: fast checks for the changed files pass before a turn ends | `.claude/hooks/stop_gate.py` (Stop; main sessions only, so parallel workers do not block each other) | Fix it, or write `BLOCKED: <reason>` |
| F1 | A fix PR has a regression test and a repro; no PR deletes or skips tests | `scripts/fix_needs_test.py` in CI | Add the test, or `No-test: <reason>` for the test only |
| D1 | Debug in order: reproduce, minimal test, map, bisect, root cause | [debugging runbook](memory/runbooks/debugging.md), `scripts/bisect_test.sh`, `debugger` subagent | Stop after 3 loops, mark BLOCKED |
| MG1 | Merge, don't rebase, shared branches; keep both sides' intent; full suite before push | [merge runbook](memory/runbooks/merge.md); `merge_group` in CI; branch protection (Nick) | `BLOCKED:` rather than edit a test or gate |
| P1 | One plan, with its required sections; handoff stays short | `scripts/plan_check.py` in CI; `scripts/check_memory.py` | Write into docs/PLAN.md; mark other plans superseded |

Guard failure policy: a hook that can't start or times out blocks (`onFailure`); a crash inside guard.py allows the
call and logs to `data/guard_errors.log`, and CI re-checks. Regexes read command text only, so auto mode's classifier
is the backstop for a script that calls adb internally.

**Status 2026-11-08:** the guard hardening above, V1, the `debugger` subagent and the CI steps for F1, P1 and MG1
live in `docs/agents/patches/2026-11-08-process-gates.patch` until Nick applies it (`git apply`). The scripts and runbooks are in the tree.

## 5b. Finding out why

- `python scripts/why.py 20:42` - everything around 8:42 PM: state moves with reasons, jumps with vetoes, who owned
  the TV, standby rows. Rows carry `sha`, the code that made the call.
- `python scripts/why.py --night 2026-11-07` - the night in numbers: jumps, failures, vetoes, what held the TV.
- `python scripts/why.py --machine control.standby` - one machine's saved moves (survive restarts).
- `python scripts/why.py <file or config key>` - the decisions and lessons behind it, newest first.
- `GET /api/machines` (now) and `GET /api/machines/history?name=...` (saved) on the running app.

## 6. Merge path

1. PR into `integration/next` with the card id. Tier 1 CI: lint, unit tests, claims, protected paths,
   state-machine doc, memory links, scorecards for touched leagues.
2. Risky PRs (decider, engine, control, fsm/machines, defaults, gates, a flag turned ON) get the
   `rule1-reviewer`; its file is required, its findings are flags for Nick, not votes.
3. The train batches ≤3 green PRs, runs tier 2 (full suite, scorecards, leak check), bisects a red batch.
   A PR gets 2 queue tries, then goes back with evidence.
4. Tier 3 (locked set `release_gate.py`, fresh nights) runs on `integration/next` before a release cut.
5. Release: `integration/next` → `integration/release` → `codex/resume-redzone`, approved by Nick.

## 7. Anti-gaming

- The locked set is scored only by CI and returns PASS/FAIL plus the Rule #1 counters, never per-night detail.
- CI fails on deleted, skipped or xfail tests, loosened thresholds, new `except: pass`
  (`scripts/autofix_guard.py`), and literal eval game or night ids in product code.
- A measured delta far above the card's expectation is a flag for the verifier, not a win.
- **BLOCKED is a success.** A builder that hits a conflict between its change and a test or gate stops and
  writes `BLOCKED: <reason>`; it never edits the test. The planner does not re-issue the same card unchanged.
- No unattended loop merges anything. Night work produces PRs and findings only.

## 8. Handoffs and memory

- State lives in files, not chat: the card, the claim, `docs/handoff.md`, decisions, lessons.
- Before stopping or compacting, update the handoff: next action, open claims, unverified items, do-not-retry.
- A fresh session runs `python scripts/context_pack.py` and the handoff's "Verify first" commands before
  editing. The SessionStart hook prints branch, handoff age and open claims.
- Every finished fix adds a lesson with literal `Signals:`; every user decision a dated decisions line.

## 9. Changing these rules

A new rule must be non-obvious, seen more than once, and checkable. Agents propose rules in the PR body under
"Suggested rule changes"; Nick decides. A rule that matters gets a hook or CI check, or it is advice.
