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
| G1 | Never read or print secrets | `.claude/hooks/guard.py` | Ask Nick |
| G2 | No push or merge to `main`, `codex/resume-redzone`, `integration/release`; no force-push; PRs target `integration/next` | guard.py + permission deny rules | `gh pr create --base integration/next` |
| G3 | No adb, stick or TV scripts (the TV may be in use) | guard.py; Nick unlocks with `data/.agent_hw_ok` | Use `tests/fake_adb.py` or the simulator |
| G4 | No training or calibration on the 8 GB Mac | guard.py | Kaggle or CI |
| G5 | No real deploy | guard.py; Nick unlocks with `data/.agent_deploy_ok` | `scripts/deploy.py --dry-run` |
| G6 | Protected paths are read-only to agents: `.claude/`, `.github/`, `tests/conftest.py`, release gate, leak check, northstar, eval scripts and locks, gate baseline, grader freeze | guard.py + `scripts/autofix_guard.py` in CI | Write BLOCKED, or open a separate grader PR for Nick touching nothing else |
| G7 | Never Peacock | guard.py (+ code check, fix plan) | Pick another provider |
| R1 | Rule #1: never join mid-play; never leave a high-leverage moment | CI counters on replayed nights; `rule1-reviewer` on risky PRs | Ship the change OFF behind its flag |
| W1 | Every state change says why | `redzone/fsm.py` (tests fail without a why) | Pass a short reason code |
| M1 | A default or gate change adds a decisions line | `scripts/check_memory.py` | Add the fragment |
| W2 | Every commit has a `Why:` line | `scripts/commit_why.py` in CI | Amend with one sentence of reason |

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
