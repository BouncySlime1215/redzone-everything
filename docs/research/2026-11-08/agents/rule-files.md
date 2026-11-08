# Agent rule files in practice: what changes behaviour, what gets ignored

Researched 2026-11-08. Scope: CLAUDE.md, AGENTS.md, .cursor rules, `.claude/rules`, skills,
subagent definitions, hooks. Builds on, and does not repeat,
`docs/research/agent-coordination-and-rationale.md` (section B already cites Agent READMEs,
Lulla et al. and Gloaguen et al.) and `docs/research/opus-5-5-harness-and-time.md`.

Evidence labels used below:
- **[opened]** I opened the page or PDF and read the text I cite.
- **[older]** published before August 2026 (still opened, but weigh less).
- **[snippet-only]** seen only in search snippets or a secondary article; X/Twitter pages
  returned HTTP 402, so no tweet was opened directly.
- **[anecdote]** one developer's account, no controlled test.

## 0. The answer in six lines

1. A rule file is advice the model may decline. Anthropic's own docs say so and tell you to move
   zero-exception rules into hooks, deny rules or CI.
2. Hard rules lose strength in two measurable ways: over a long session (compliance falls about
   5.6% in odds per generated function) and at compaction (rules kept: 53% after one
   `/compact`, 10% after five, in one study; violations 0% to 30% in another).
3. File length, rule position and splitting into several files showed NO measurable effect on
   adherence in the one controlled factorial study (25 to 500 lines). Size is a cost and
   attention issue, not a proven adherence lever. Do not over-claim it.
4. Wording matters, and differently per model: conditional ("if X then Y") phrasing is worst;
   on Sonnet 5 prohibitive ("never ...") phrasing was the worst of four styles; on Opus 5 plain
   imperatives won.
5. Generic overviews and architecture tours do not raise task success and cost more tokens.
   Specific traps, commands and non-default conventions are what files are good for.
6. Mature repos converge on: one canonical file plus symlinks, nested per-directory files,
   skills for procedures, and a script or CI check behind every rule that matters.

## (a) Ten ranked rules for writing rules

Ranked by (strength of evidence) x (cost of getting it wrong for RedZone).

### 1. If a rule has zero exceptions, enforce it outside the prompt. Prose only explains.
- Anthropic best practices [opened, live doc]: "If something has to happen every time, make it a
  hook, a deny rule, or a managed setting. CLAUDE.md is for guidelines, and Claude is allowed to
  decide a guideline doesn't apply." Also: adding IMPORTANT "probably won't fix it".
- Memory docs [opened]: CLAUDE.md is delivered as a user message, "no guarantee of strict
  compliance"; a PreToolUse hook blocks regardless of what Claude decides.
- McMillan 2605.10039 [opened, older, 11 May 2026]: 1,650 sessions; its practitioner table says
  pair in-session hooks with post-hoc linters and CI, because "a missed instruction may go
  unnoticed" without tooling.
- Anecdote [opened]: Krupa (30 Jun 2026): a verification rule at line 36 of a 129-instruction file
  behaved like no rule; swapping it for a PreToolUse hook that exits 2 stopped the violations
  (n=1, no method). Same story in the March 2026 minatoplanb post and issue #32198.
- Cap the enforced set: Anthropic says if you reach about forty hooks/deny rules, most are
  probably preferences. Hooks for preferences should be deleted.

### 2. Put hard rules where compaction cannot erase them, and never only in chat.
- Governance Decay 2606.22528 [opened, 21 Jun 2026]: 1,323 episodes, 7 model families; violations
  0% with the policy in context, 30% after compaction (59% for some models); 0% if the
  constraint survived the summary, 38% if dropped. "Constraint Pinning" restored 0%.
- Compaction Cliff 2608.22752 [opened, 24 Aug 2026, CIKM 2026]: Claude Code `/compact` on Sonnet
  4.6 kept 53% of safety rules after one round, 10% after five (20 production configs).
- Lost in Context Compaction 2608.11242 [opened, 31 Jul 2026, v2 11 Oct 2026]: compactors kept 17%
  of injected side constraints on average; an inbox agent deleted emails without approval in 30
  of 100 sessions after compaction vs 0 before.
- What Claude Code does about it [opened]: root CLAUDE.md and unscoped rules are re-read from
  disk after `/compact`; nested CLAUDE.md and `paths:`-scoped rules come back only when a
  matching file is read again; instructions given only in conversation are lost. Skills are
  re-attached from the most recent invocation (first 5,000 tokens each, 25,000 shared; oldest
  dropped first).
- Rule for RedZone: a rule that must hold goes in root CLAUDE.md (re-read) AND a hook/CI check.
  Anything that lives only in handoff.md, a chat message, or a subagent prompt is fragile.
  Re-inject volatile reminders with `SessionStart` matcher `compact` (see section d).

### 3. Write only what the agent cannot infer: traps, commands, non-default conventions.
- Gloaguen 2602.11988 [opened, Feb 2026, v3 29 Sep 2026]: context files did not generally improve
  success; repository overviews were not helpful; instructions were generally followed; cost
  rose over 20% on average. Conclusion: use files for non-standard practices, evaluate before
  deploying.
- Khatri 2607.27250 [opened, 28 Jul 2026]: 288 runs, Claude Code (sonnet-4-6) and Codex CLI
  (gpt-5.5), 3 Python repos; no correctness change from always-on AGENTS.md or on-demand wiki
  (p = 1.00 for Claude); but fewer blind full-suite test runs (3.67 to 2.44 to 1.67 on opshin,
  exploratory) and fewer cache-creation tokens. Context files changed process, not outcome.
  Low power: they say a 10 point effect needs 120 to 200 tasks.
- Anthropic [opened]: "only holds what Claude couldn't work out from the code itself"; the
  Segner post (18 Jun 2026) says treat CLAUDE.md as an index pointing to other files.
- Zed `.rules` [opened]: "Rules should be traps to avoid, not maps to follow"; architecture
  descriptions go stale and the agent can read code. Cloudflare workers-sdk AGENTS.md: "Prefer
  authoritative configuration and documentation over copying details into this file".
- Agent READMEs 2511.12884 [opened, v2 9 Aug 2026]: files grow by small additions; security (14.8%)
  and performance (14.5%) rules are rare. For RedZone the equivalent gap is the safety invariants.

### 4. State a must-do as a direct imperative. Avoid "if ... then" phrasing. Test per model.
- Tsuchida et al. 2611.04367 [opened, 3 Nov 2026]: one policy, four wordings, 86 tasks,
  compliance % (prescriptive / explanatory / conditional / prohibitive):
  - Claude Code + claude-opus-5: 78.0 / 77.2 / 64.2 / 70.7
  - Claude Code + claude-sonnet-5: 71.6 / 49.4 / 35.6 / 17.3
  - qwen-code + gpt-oss-120b: 6.7 / 20.4 / 0.8 / 0.4
  - qwen-code + Qwen3-Coder-Next: all under 1.5 (no wording effect)
  Their advice: avoid conditional, default to prescriptive or explanatory, test each model.
  Caveats: one policy, one wording per style, 10 repos. The closest to our model is opus-5.
- Tension to be honest about: Zhang et al. 2604.11088 [opened, 13 Apr 2026] found on SWE-bench
  Verified (Opus 4.6, over 5,000 runs, 25,532 scraped rules) that every individually beneficial
  rule was a negative constraint ("do not refactor unrelated code") and every individually
  harmful one a positive directive; random rules gave the same +13.8 points as curated ones,
  which they read as context priming, not content. Different outcome (task pass rate on a
  subset, not marker compliance). Reconciliation: negative constraints are good scope limiters
  ("do not touch unrelated files"); for a required action ("run X before opening a PR") use a
  plain imperative with a checkable artifact.

### 5. Make every rule checkable, with the command or the observable artifact in the line.
- Memory docs [opened]: "Use 2-space indentation" beats "format code nicely"; contradicting
  instructions are picked from arbitrarily (also between user-level and project-level rules).
- SSD Nodes, Matt Connor, 25 Oct 2026 [opened, blog]: four reasons rules fail: never in context,
  too vague to check, conflicting code in context outweighs it, buried far back in a long
  session. Test by running the same task ten times in fresh sessions and counting violations.
- Harness-IF 2608.11727 [opened, 12 Aug 2026]: 12 models, 256 rules scored from execution
  evidence; overall 72.1 to 85.9%; every model is worse on "against-prior" rules (rules that
  oppose its defaults) by 3.6 to 7.4 points. Rules that match defaults are free; rules that
  fight defaults need the strongest mechanism, so they are the ones to put in hooks.
- uv AGENTS.md [opened]: each line names a command or pattern ("use `cargo update --precise`").

### 6. Expect decay inside one session; re-surface and verify mid-flight, not just at the start.
- McMillan [opened, older]: each extra function generated in a session was associated with 5.6%
  lower odds of compliance (OR 0.944), non-monotonic; median first omission at function 4; the
  effect reproduced on a second codebase and on Opus 4.6. Task identity mattered more than any
  file-structure variable (26.2 point gap between two tasks). Marker was a trivial comment, so
  absolute rates do not transfer.
- HANDBOOK.md 2607.25398 [opened, 28 Jul 2026]: 65 agentic tasks, strict pass at most 36.2%
  (best of 30 configs), most under 25%; failure modes: a plausible in-environment request
  overrides the standing policy, a required check is run then ignored, rule details lost over
  long horizons, and compliance is reported that was not achieved.
- So "done" must be verified by a check the agent does not control: a Stop hook or CI, not a
  self-report. Issue #32198 [opened, 8 Mar 2026, closed "not planned" by stale bot, no staff
  reply] is the canonical failure: a CRITICAL Definition-of-Done gate was skipped, 14 issues
  found afterwards.

### 7. Give each rule one home and one owner; no duplicates, no contradictions.
- Docs [opened]: contradicting files resolved arbitrarily; `/doctor prompt-audit` finds stale,
  conflicting or dead-reference instructions; HTML block comments in CLAUDE.md are stripped
  before injection (free maintainer notes).
- Convention for several tools: one canonical file, others point at it. Seen in transformers
  (root AGENTS.md and CLAUDE.md are 13-byte links to `.ai/AGENTS.md`), Zed (6-byte links to
  `.rules`), MCP python-sdk (`CLAUDE.md` is the single line `@AGENTS.md`).
- Loading gotcha [opened, SSD Nodes + HN]: since v2.1.277 AGENTS.md is read only when no CLAUDE.md
  exists in the chain, so use `@AGENTS.md` if both exist.

### 8. Progressive disclosure, but know what each layer costs and when it loads.
- Anthropic Segner post [opened, 18 Jun 2026]: CLAUDE.md for always-true facts, rules (path-scoped)
  for constraints, skills for repeatable procedures (name and description only until used),
  subagents for side work that would clutter context, hooks for deterministic actions.
- Limits [opened]: CLAUDE.md under 200 lines; SKILL.md under 500 lines; skill
  `description` plus `when_to_use` cut at 1,536 characters, so trigger phrases must come first;
  `disable-model-invocation: true` for side-effecting skills (deploy, TV tests).
- Subagents [opened]: they receive the CLAUDE.md hierarchy except Explore and Plan, and a custom
  one can opt out with `omitClaudeMd: true`; frontmatter hooks run only while that subagent is
  active, settings.json hooks fire inside subagents too; "restate in the delegation prompt" any
  rule that must reach them; tool allowlists are the real control.
- Counter-evidence: McMillan found no adherence gain from splitting into several files, and
  path-scoped rules vanish after compaction until a matching file is read. Splitting saves
  context cost; it does not buy compliance. Never move a hard rule out of the root file only to
  keep it short.

### 9. Add a reason in one clause, not a paragraph; do not shout.
- Anthropic [opened]: "Older models sometimes needed the emphasis. Current ones follow a plainly
  worded line, and piling on emphasis mostly makes Claude over-apply the rule." Segner: explain
  the reasoning and give examples.
- Tsuchida [opened]: adding a reason did not change compliance for the stronger model (77.2 vs
  78.0 on opus-5) and helped on gpt-oss (20.4 vs 6.7) but hurt on sonnet-5 (49.4 vs 71.6). So a
  reason is cheap insurance for other models, not a booster for ours.
- Practitioner [opened, anecdote]: John Lee (DEV, "Aug 6", year not shown on page): a file with ten
  ALWAYS lines makes the eleventh meaningless; cut a 500-line file to about 10 lines and behaviour
  improved. Repos do use graded verbs deliberately: uv's NEVER / ALWAYS / DO NOT / PREFER / AVOID
  tiers, Zed's single labelled "HARD RULE". Pattern: very few top-tier words, each with a reason.

### 10. Govern the file like code: high bar to add, tripwires to detect reading, prune on a schedule.
- Zed `.rules` "Rules Hygiene" [opened]: a new rule must be non-obvious, repeatedly encountered and
  specific; agents propose additions under a "Suggested .rules additions" PR heading, humans
  decide; no drive-by additions; crate-specific rules go in that crate's file.
- Tripwires [opened]: Zed's HARD RULE makes the agent prepend a fixed notice to README.md and
  forbids removing it; Ghostty says that if asked to open an issue or PR the agent must write a
  fixed sentence into the diff. Both turn "did the agent read and obey this?" into something a
  reviewer can see. Cheap canary: a one-line marker the agent must echo (for example in a PR
  body) that CI greps.
- Claude Code team habit [snippet-only, secondary summaries of a Jan 2026 Boris Cherny thread; I
  did not open the thread]: one checked-in CLAUDE.md the whole team edits; mistakes become rules
  via `@claude` on PRs.
- Evidence that this matters: Agent READMEs [opened] show files grow by frequent small additions
  and become hard to read; HumanLayer [opened, Nov 2025, older] keeps its root file under 60
  lines, quoting 150 to 200 reliably followed instructions as an estimate (secondhand, not a
  measured benchmark).

## (b) Proposed rewrite outline for RedZone CLAUDE.md (outline only; CLAUDE.md untouched)

### Diagnosis of the current file (71 lines, about 4 KB)
- Length is fine. The problems are structural:
  1. Line 3 sends every session to read handoff.md (305 lines), status.md (177) and
     architecture.md (379): about 860 lines before any task. That is the real context bill and
     the opposite of "overviews are not helpful" (Gloaguen).
  2. About 12 hard rules are prose-only: never touch secrets; never merge to `main`; every PR
     targets `integration/next`; no training or heavy jobs on the Mac; 88% usage pause; at most
     5 threads; no disruptive TV tests; never use Peacock; Rule #1 (never join mid-play);
     release-gate no-overfitting rules; Co-Authored-By line; before/after table.
  3. Rule #1 is a nested negative plus exceptions (leave mid-play allowed only at low leverage,
     never leave at high leverage). The conditional structure is the style that tested worst
     (Tsuchida). Its real enforcement is the measured PR delta, which already exists (PR
     template, CI comment, scorecards).
  4. Nothing project-level is committed under `.claude/` (the main checkout has an empty
     `.claude/`; the worktree has none). Hooks exist only user-level and are not RedZone-specific.
  5. The user-level ~/.claude/CLAUDE.md (ADHD rules) is also read by every subagent, which can
     pollute machine-consumed subagent output. To verify: set `omitClaudeMd: true` on
     data-returning subagents, or confirm the formatting is harmless there.

### Target shape: root CLAUDE.md of about 35 to 45 lines, five blocks
1. **North star + where to start (6 lines).** One-sentence north star; one link to
   docs/northstar.md; ONE start pointer: "Run `python scripts/context_pack.py` (it prints live
   status)". Drop the three-doc reading chain; keep links as an index only.
2. **Never-list, each with its enforcement and what to do when blocked (10 to 12 lines).**
   Format: `- <plain imperative>. Enforced by <hook|deny|CI>. If blocked: <next action>.`
   This keeps the rule in the root file (survives compaction) and tells the agent the
   remediation. The prose never claims to be the enforcement.
3. **Rule #1 restated as a positive procedure plus the check (5 lines):** "Join only when the
   leverage module says dead ball; leave only per `redzone/lab/leverage.py`. Every PR shows the
   before/after table (entries_mid_play, plays_missed_entry, dead_air_pct); CI fails if it is
   missing or worse without a reason at the top." Keeps the product invariant, moves the
   judgement to the scorecard.
4. **Commands (6 lines):** pytest, ruff, scorecard for touched leagues, release_gate,
   check_memory. Add "prefer targeted tests" (uv, Ghostty both say so; Khatri saw fewer blind
   full-suite runs).
5. **Memory pointers (4 lines):** decisions.md and lessons.md rules; glossary link. Keep "a PR
   that changes a default or gate adds a decisions line (CI enforces)" as the model line: rule,
   plus the enforcement named.

KEEP verbatim in spirit: north star order, branch map, $0 budget (needs a question to a human,
cannot be a hook), Mac is light, Nick's secrets rule, "never use Peacock".
CUT: the three-doc reading chain, the 5-thread pacing explanation (keep the number, link
status.md), the release-gate paragraph (becomes a path-scoped rule), duplicated PR rules
(live in the PR template).

### What moves where

| Rule today | New home | Why (evidence) |
|---|---|---|
| Never read or print secrets | `permissions.deny` for `Read(.env*)`-style paths in committed `.claude/settings.json` + one root line | Anthropic: "A file that must never be read" is deterministic territory. |
| Never merge to `main`; PRs target `integration/next` | GitHub branch protection (free, server-side) + PreToolUse Bash hook that exits 2 on `git push` to main and on `gh pr create` without `--base integration/next` | Zero-exception rule. Stderr text tells the agent the right command. |
| No training or heavy jobs on the Mac | PreToolUse Bash hook blocking `scripts/train_*.py`, `build_training*.py`, `calibrate_brain3.py` (verify the list) unless an env flag marks a Kaggle or cloud run | Zero exceptions on an 8 GB machine running the live app. |
| No disruptive TV tests while Nick watches; Never use Peacock | PreToolUse hook on TV/stick-driving scripts (`tv_practice.py`, `controls_drill.py`, `fault_drill.py`, `stick_cec.py`, `tv_*`) requiring an explicit "ok to disturb" file Nick creates; a pytest or CI grep that fails if Peacock appears in config | Both are hard "never" with a physical cost. |
| 88% usage pause | `UserPromptSubmit` or `SessionStart` hook that reads a usage flag file and adds a factual line; runbook stays in docs | Only if a usage number is machine-readable; otherwise leave as prose and say so. |
| At most 5 threads | One line + status.md; optional SessionStart hook that prints current thread count as a fact | Coordination state is volatile; Anthropic says hooks for state that changes. |
| Co-Authored-By trailer; before/after table; decisions line | `commit-msg` or CI check; PR template (exists); `check_memory.py` (exists) | Already mostly mechanical. Remove duplicate prose. |
| Release gate and no-overfitting (time split by night, locked set scored once, forward-chaining CV, leakage) | `.claude/rules/eval.md` with `paths:` for `scripts/eval*.py`, `scripts/release_gate.py`, `docs/gate_baseline.json`, `docs/grader_freeze.json`; plus `scripts/autofix_guard.py` already protects those paths | Only matters when touching eval; loads then. Caveat: path rules vanish after compaction until a matching file is read again, so keep the one-line gate summary in root. |
| Rule #1 leverage details | `.claude/rules/decider.md` with `paths:` for `redzone/decider/**`, `redzone/lab/leverage.py`, `redzone/fsm.py` | Same. |
| "Run the integration train", deploy, rollback, Kaggle run, TV setup, stick offline | Skills with `disable-model-invocation: true` for side effects (`integration-train`, `deploy`, `rollback`, `tv-setup`); runbooks stay in docs/memory/runbooks as the skill's reference files | Skills load name and description only; side-effect skills cannot be fired by the model on its own. |
| PR self-review (risks, before/after table, North Star line) | Skill `redzone-pr` (user and model invocable) | Repeatable procedure, long, not needed every turn. |

### Subagents (3, not 10)
Mature repos keep this small (Home Assistant: 2 in `.claude/agents`; PostHog has many
domain specialists). For RedZone:
1. `scorecard-runner`: tools Read, Grep, Glob, Bash; runs `ci_scorecard.py` for the leagues
   touched and returns ONLY the before/after table. Keeps heavy output out of the main context.
   `omitClaudeMd: true` is an option if the user-level ADHD file is a problem (verify first).
2. `rule1-reviewer`: read-only; checks a diff for new mid-play entries, high-leverage leaves,
   missing decisions line. It reports, never edits. Pair with the existing
   `silent-failure-hunter`.
3. `eval-leak-checker`: read-only; checks night-split and locked-set rules on eval diffs.
Rules for each: restate the two or three relevant rules in the body (subagents may not see
everything), restrict `tools`, no `Agent` tool unless needed.

### Hooks to commit in `.claude/settings.json` (all `command` type, not `prompt` or `agent`)
- `PreToolUse` Bash: git and gh guard (branch, base, force-push), heavy-job guard, TV-test guard.
  Exit 2 with a stderr message that names the correct alternative.
- `PostToolUse` Edit|Write: `ruff check --fix` on touched Python files (the vercel/ai pattern:
  `pnpm fix` after every Write or Edit).
- `Stop`: run a fast subset (`ruff check .`, `check_memory.py`, a changed-files test run) and exit 2
  if it fails. Mind the documented block cap of 8 consecutive blocks.
- `SessionStart` (startup, resume, compact): print 5 to 10 factual lines (branch, thread count,
  top of status queue, "Rule #1 table required for PRs"). Per the hooks docs, write facts, not
  imperatives: "Imperative, system-style wording can trigger prompt-injection defenses".
- `InstructionsLoaded`: log which rule files loaded, to debug path-scoped rules.
Add `"onFailure": "block"` where a crashing hook must not silently allow the action.

### Governance (borrowed from Zed)
Add a "Rules hygiene" block of 5 lines: three-criteria bar for new rules, agents propose rules in
the PR body, Nick (or the INTEGRATION session) decides; prune quarterly; `/doctor prompt-audit`
each release; tripwire: PR body must include a fixed line such as `Rule#1-table: attached`
that CI greps.

### Verify the rewrite, do not trust it
Run the SSD Nodes test: pick 3 rule-sensitive tasks, run each 10 times in fresh sessions
before and after, count violations; run the Skills with-and-without comparison the skills docs
recommend. This costs tokens, so cap it at one evening on the Max plan; no Mac load.

## (c) Repo pattern table

Stars from `gh api repos/<r>` on 2026-11-08. File sizes from the git tree API. I read the files
named in "What I read"; other cells come from the file listing only.

| Repo | Stars | Setup seen | Enforcement beyond prose | Pattern worth copying | What I read |
|---|---|---|---|---|---|
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | 154,987 | CLAUDE.md 2.2 KB (all about workflow security); `.claude/commands` (commit-push-pr, dedupe, triage-issue) | CI `workflow-hardening.yml` fails if the rules in CLAUDE.md are broken; exemption table in a script | Rule paired with a CI check and a documented exemption path | CLAUDE.md in full |
| [vercel/ai](https://github.com/vercel/ai) | 27,816 | AGENTS.md 16 KB; `.agents/guidelines` + `.agents/verifiers/*.ts`; `.claude/settings.json`, `.codex/hooks.json` | PostToolUse `pnpm fix` on Write/Edit; `pnpm run-verifiers --modified` | Each guideline has a verifier script; "when it flags something it is a clear violation"; bug-fix rule demands end-to-end reproduction | AGENTS.md sections, settings, hooks |
| [PostHog/posthog](https://github.com/PostHog/posthog) | 40,705 | AGENTS.md + CLAUDE.md + `.cursorrules`; `.agents/*.md` topic docs (architecture 13 KB, security 27 KB); `.claude/agents` many; skills | SessionStart setup hooks | "Read when ..." link index; directory-level AGENTS.md "wins locally"; specialist subagents | CLAUDE.md top 40 lines, settings |
| [home-assistant/core](https://github.com/home-assistant/core) | 92,069 | AGENTS.md; `.claude/agents` (2); `.claude/skills` with helper scripts | Scripts inside skills (dependency resolver, deprecation finder) | Skills ship scripts so agents run code instead of improvising; explicit "do NOT amend pushed commits" | AGENTS.md top 40 lines |
| [cloudflare/workers-sdk](https://github.com/cloudflare/workers-sdk) | 4,708 | AGENTS.md 9 KB; 7 nested per-package AGENTS.md; CLAUDE.md 132 B | n/a seen | "Start Here" first; "prefer authoritative config over copying"; read the package's own file before changing it | AGENTS.md top, headings |
| [astral-sh/uv](https://github.com/astral-sh/uv) | 91,774 | AGENTS.md 3.7 KB, flat bullets; `.codex/skills` | Security lints referenced as rules | Tiered verbs NEVER / ALWAYS / DO NOT / PREFER / AVOID; every line names a concrete tool | Most of AGENTS.md |
| [ghostty-org/ghostty](https://github.com/ghostty-org/ghostty) | 63,884 | Root AGENTS.md 1.5 KB + about 10 nested; `.agents/skills/writing-commit-messages`, `.agents/commands/review-branch` | Tripwire sentence for issue/PR requests | Tiny root, many small nested files; commands with "prefer targeted tests" | Root AGENTS.md in full |
| [zed-industries/zed](https://github.com/zed-industries/zed) | 94,468 | `.rules` 12 KB; AGENTS.md, CLAUDE.md, GEMINI.md are 6-byte links to it; `.agents/skills` (gpui-test, lint-creator) | CI enforces PR release-notes format; README tripwire | Rules Hygiene (3-criteria bar, suggest-in-PR, "traps not maps") | `.rules` and CLAUDE.md in full |
| [huggingface/transformers](https://github.com/huggingface/transformers) | 168,664 | `.ai/AGENTS.md` 3 KB; root AGENTS.md and CLAUDE.md are links; `make codex` / `make claude` wire local assets | `make style`, `make check-repo` | One source, tool-specific wiring by make target; coordination rule before opening a PR (check issue and open PRs) | `.ai/AGENTS.md` in full |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | 24,802 | AGENTS.md 8.7 KB; CLAUDE.md = `@AGENTS.md`; `.claude/commands/review-pr.md`; `.claude/skills/test-quality` (10 KB) | n/a seen | Import line instead of a copy; a quality skill for tests | CLAUDE.md |
| [obra/superpowers](https://github.com/obra/superpowers) | 314,532 | AGENTS.md 9.9 KB contributor guide aimed at the agent ("Stop. Read this section"); GEMINI.md | n/a seen | Agent told to protect its human from a bad PR, with a checklist; states a 94% PR rejection rate (their own claim, not verified); says skill text was tuned and "compliance" rewrites need eval evidence | First 60 lines |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 21,090 | `.agents/skills` (add-new-model 42 KB; `branch-context` with `append-handoff.sh`, `latest-handoff.sh`, `pr-decisions.template.md`); `.claude/settings.json` | Shell helpers write handoffs and PR decisions | Handoff and decision log maintained by scripts, close to RedZone's handoff.md and decisions.md | File listing only |
| [vercel/next.js](https://github.com/vercel/next.js) | 143,428 | `.agents/skills` (authoring-skills, backport-pr, create-pr, deploy-release-test, docs-diagrams with scripts) | n/a seen | A skill about authoring skills; procedures as skills with scripts | File listing only |
| [apache/airflow](https://github.com/apache/airflow) | 47,418 | `.agents/skills` (publish-changes, translations with per-locale files, PR draft summary) | n/a seen | Large skill library with `references/` subfolders | File listing only |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | 149,210 | Root AGENTS.md 20 KB | n/a seen | Uses MUST for type hints and tests; includes a security-analysis section; long root file | Headings only |
| [sst/opencode](https://github.com/sst/opencode) | 225,502 | Root AGENTS.md 17 KB + AGENTS.md in about 14 packages (up to 23 KB) | n/a seen | Per-package files carry the detail; root stays global | File listing only |
| [microsoft/vscode](https://github.com/microsoft/vscode) | 195,196 | `.github/copilot-instructions.md` 14.5 KB; `.agents/skills/launch` (SKILL.md 37 KB with PowerShell/bash/TS scripts) | Scripts for launch, profile bootstrap, cleanup | A skill that drives the real app for verification, like RedZone's live TV checks | File listing only |

### Patterns that recur (with counts from this sample of 17)
1. **One canonical file, others point at it** (transformers, zed, MCP SDK, workers-sdk,
   next/ai via `.agents` plus `.claude` pointers): 5 of 17 clearly.
2. **Nested per-directory files** (opencode, ghostty, workers-sdk, posthog, codex partly):
   at least 5. Root stays global, detail sits next to the code.
3. **Skills for procedures, often with scripts**: next.js, airflow, pydantic-ai, home-assistant,
   vscode, zed, ghostty: at least 7. Skills with scripts beat skills that only describe.
4. **Prose backed by a deterministic check**: vercel/ai verifiers and PostToolUse fix,
   claude-code CI workflow, transformers `make check-repo`, zed CI release-notes check,
   `uv` security lints: at least 5.
5. **Spam and coordination rules for agent PRs** (ghostty, transformers, superpowers, Home
   Assistant, zed PR hygiene): at least 5. RedZone's equivalent is the multi-agent lane rules.
6. **Short root, "read when" links** (posthog, cloudflare, ghostty). Roots range from 1.5 KB
   to 20 KB; the big ones (langchain, opencode) are the exception, not the norm.
7. **Governance of the rules themselves**: only Zed and superpowers make it explicit.
   That is the least common and the most RedZone-relevant pattern, given many parallel
   agents each tempted to add a rule.

## (d) Phrasing and enforcing hard safety rules

### Order of preference
1. **Make it impossible**: sandbox, `permissions.deny`, branch protection on the server,
   tool allowlist on the subagent. Anthropic: output scanning and prompts are not a boundary;
   "restricting tools is the real control".
2. **Make it blocked with a useful message**: `PreToolUse` hook, exit 2, stderr names the
   right alternative (stderr is fed back to Claude as the reason). Use `command` hooks;
   `prompt` or `agent` hooks ask another model and are "not deterministic either".
3. **Make "done" checkable**: `Stop` hook running the check (exit 2 sends Claude back to
   work, capped at 8 consecutive blocks), plus CI on the PR.
4. **Make it survive compaction**: root CLAUDE.md line plus `SessionStart` matcher `compact`
   with factual text, under 10,000 characters.
5. **Only then prose**, and keep the prose short, direct and specific.

### Template for a hard rule in CLAUDE.md
```
- Do not merge to main. Every PR targets integration/next.
  Enforced by: branch protection + .claude/hooks/git-guard.sh. If blocked: use
  `gh pr create --base integration/next`.
```
Why this shape: direct imperative (best wording in Tsuchida for opus-5), checkable (names the
branch), one-clause enforcement and remediation (an agent that hits the block knows what to
do next; it will not retry in a loop), and the line is in the root file so it survives
compaction.

### Placement and wording notes (what the evidence supports and what it does not)
- **Placement:** McMillan found no position effect across five positions in a 25 to 500 line
  file, and no size effect, so "put MUST at the top" is not supported for single rules in a
  file of this size. Anthropic's long-context advice to place instructions at the end applies
  to prompts, not to files. Treat position as unproven; treat compaction survival and
  enforcement as proven.
- **MUST/NEVER:** no study I found shows capitals help current Claude models; Anthropic says
  they cause over-application. The tiered NEVER/ALWAYS vocabulary in uv works as labelling
  for humans and reviewers. Use few, with a reason.
- **Examples:** the GitHub Blog analysis of over 2,500 AGENTS.md files [opened, 19 Nov 2025,
  older] says one real snippet beats a paragraph, put commands early, and use a three-tier
  boundary list (always, ask first, never); "never commit secrets" was the most common helpful
  constraint. This is descriptive of good files, not a controlled test.
- **Injected text is treated differently from file text:** hooks docs warn imperative
  system-style `additionalContext` may trigger prompt-injection defenses and be surfaced
  to you instead of used. Phrase hook output as facts.
- **Instruction hierarchy:** ManyIH-Bench 2604.09443 [opened, Apr 2026, v4 4 Sep 2026]: with up
  to 12 privilege levels frontier models reach only about 40% accuracy on conflicts; Harness-IF
  found system prompts, project files and user instructions outrank tool and skill descriptions.
  Practical read: do not rely on a precedence between your own files; remove conflicts.
- **Untrusted content:** the HANDBOOK.md failure "a plausible request in the environment
  overrides the policy" applies to RedZone agents reading live feeds, issue text or logs. Keep
  secret handling and push rights in deny rules, since prompts there can be argued around.

## Practitioner discussion, Oct to Nov 2026 (honest coverage note)
Few dated Oct-Nov 2026 practitioner posts surfaced. What I could open:
- HN item 49760187 "Claude Code now reads AGENTS.md if there is no Claude.md", about 50 days
  old on 8 Nov (so around 19 Sep 2026), 741 points, 285 comments; changelog entry dated 18 Sep
  2026. Themes: people symlinked or imported before; some say instruction files are not followed
  reliably, citing a "may not be relevant" reminder; skills in `.agents/skills` still not loaded.
- SSD Nodes, 25 Oct 2026, loading order and four failure reasons (above).
- FourWeekMBA, 8 Nov 2026, a secondary summary of Anthropic's best-practices page; it embeds an
  Addy Osmani X article and a @choblin29 X post. [snippet-only; X returned HTTP 402; the
  article itself does not verify any Anthropic affiliation for the post's author]
- Tsuchida et al., 3 Nov 2026 (paper, above) is the freshest hard evidence.
No X/Twitter content was opened. Do not cite any tweet from this document as verified.

## (e) Bibliography (verified by opening unless marked)

### Papers
1. McMillan, "Instruction Adherence in Coding Agent Configuration Files: A Factorial Study of
   Four File-Structure Variables", arXiv:2605.10039, 11 May 2026 [older]. https://arxiv.org/abs/2605.10039
2. Tsuchida, Fan, Shimari, Kula, Matsumoto, "Do Coding Agents Follow AGENTS.md? The Effect of
   Policy Wording on Instruction Compliance", arXiv:2611.04367, 3 Nov 2026. https://arxiv.org/abs/2611.04367
3. Khatri, "Do Context Files Help Coding Agents? A Two-Agent Ablation Study on Real
   Repositories", arXiv:2607.27250, 28 Jul 2026. https://arxiv.org/abs/2607.27250
4. Zhang et al., "Guardrails Beat Guidance: A Large-Scale Study of Rules, Skills, and
   Persistent Configuration for Coding Agents", arXiv:2604.11088, 13 Apr 2026 (v2 28 May)
   [older]. https://arxiv.org/abs/2604.11088
5. Gloaguen et al., "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for
   Coding Agents?", arXiv:2602.11988, 12 Feb 2026, v3 29 Sep 2026. https://arxiv.org/abs/2602.11988
6. Huang et al., "Harness-IF: Evaluating Instruction Following Across Instruction Surfaces in
   Coding Agents", arXiv:2608.11727, 12 Aug 2026. https://arxiv.org/abs/2608.11727
7. Panavas et al., "HANDBOOK.md: A Benchmark for Long-Context Agentic Instruction Following",
   arXiv:2607.25398, 28 Jul 2026 (v3 3 Aug). https://arxiv.org/abs/2607.25398
8. Zhang et al., "Many-Tier Instruction Hierarchy in LLM Agents" (ManyIH-Bench),
   arXiv:2604.09443, 10 Apr 2026, v4 4 Sep 2026, EMNLP 2026 Findings. https://arxiv.org/abs/2604.09443
9. Chen, "Governance Decay: How Context Compaction Silently Erases Safety Constraints in
   Long-Horizon LLM Agents", arXiv:2606.22528, 21 Jun 2026. https://arxiv.org/abs/2606.22528
10. Wang et al., "Lost in Context Compaction: When LLM Systems Drop Side Constraints",
    arXiv:2608.11242, 31 Jul 2026, v2 11 Oct 2026. https://arxiv.org/abs/2608.11242
11. Zerhoudi, Mitrovic, Granitzer, "The Compaction Cliff in Long-Running AI Agent Memory",
    arXiv:2608.22752, 24 Aug 2026 (CIKM 2026). https://arxiv.org/abs/2608.22752
12. Xia et al., "Diagnosing and Mitigating Context Rot in Long-horizon Search",
    arXiv:2606.29718, 29 Jun 2026, v3 25 Oct 2026. https://arxiv.org/abs/2606.29718
13. Gao and Chen, "From Agent Behaviour to Agent-Friendly Documentation", arXiv:2608.20195,
    20 Aug 2026 (557 sessions; instruction files and working notes are 60.5% of doc
    interactions; consultation is 70.2% self-initiated). https://arxiv.org/abs/2608.20195
14. Galster et al., "Harness Engineering for Agentic AI Coding Tools: An Exploratory Study",
    arXiv:2602.14690, 16 Feb 2026, v5 30 Jun 2026 (2,853 repos; context files most common,
    skills and subagents rare). https://arxiv.org/abs/2602.14690
15. Chatlatanagulchai et al., "Agent READMEs: An Empirical Study of Context Files for Agentic
    Coding", arXiv:2511.12884, 17 Nov 2025, v2 9 Aug 2026 [older]. https://arxiv.org/abs/2511.12884
16. Liu et al., "Lost in the Middle: How Language Models Use Long Contexts", arXiv:2307.03172,
    Jul 2023 [older]. https://arxiv.org/abs/2307.03172
17. Hong, Troynikov, Huber, "Context Rot: How Increasing Input Tokens Impacts LLM
    Performance", Chroma, 14 Jul 2025 [older]. https://www.trychroma.com/research/context-rot

### Official docs and Anthropic staff (live pages, fetched 2026-11-08, undated unless stated)
18. Claude Code, "Best practices". https://code.claude.com/docs/en/best-practices
19. Claude Code, "How Claude remembers your project" (memory). https://code.claude.com/docs/en/memory
20. Claude Code, "Hooks reference". https://code.claude.com/docs/en/hooks
21. Claude Code, "Subagents". https://code.claude.com/docs/en/sub-agents
22. Claude Code, "Skills". https://code.claude.com/docs/en/skills
23. Michael Segner (Anthropic), "Steering Claude Code: when to use CLAUDE.md, skills, hooks, and
    subagents", 18 Jun 2026. https://claude.com/resources/articles/steering-claude-code-skills-hooks-rules-subagents-and-more

### Practitioner posts and discussion
24. Hacker News item 49760187, "Claude Code now reads AGENTS.md if there is no Claude.md"
    (around 19 Sep 2026; 741 points, 285 comments). https://news.ycombinator.com/item?id=49760187
25. Matt Connor, SSD Nodes, "Why coding agents ignore your instructions", 25 Oct 2026.
    https://www.ssdnodes.com/learn/why-agents-ignore-your-instructions
26. Bartłomiej Krupa, "Why agents ignore your CLAUDE.md", 30 Jun 2026, updated 29 Jul 2026
    [anecdote, n=1 test]. https://bartlomiejkrupa.dev/articles/why-agents-ignore-your-claude-md/
27. John Lee, "How to Keep Your CLAUDE.md Light (and Why More Rules Make Agents Worse)", DEV,
    "Aug 6" (year not shown on page) [anecdote].
    https://dev.to/johnonlee/how-to-keep-your-claudemd-light-and-why-more-rules-make-agents-worse-34fa
28. minatoplanb, "I Wrote 200 Lines of Rules for Claude Code. It Ignored Them All.", DEV, 8 Mar
    2026 [anecdote, older]. https://dev.to/minatoplanb/i-wrote-200-lines-of-rules-for-claude-code-it-ignored-them-all-4639
29. anthropics/claude-code issue #32198, "Claude Code skips mandatory rules in CLAUDE.md
    (Definition of Done)", opened 8 Mar 2026, closed not planned 14 Apr 2026 [older].
    https://github.com/anthropics/claude-code/issues/32198
30. Gennaro Cuofano, FourWeekMBA, "Anthropic: CLAUDE.md Rules Are Requests; Hooks Are
    Enforced", 8 Nov 2026 [secondary; embedded X posts snippet-only].
    https://fourweekmba.com/ai-anthropic-claude-md-rules-are-requests-hooks-are-enforced/
31. Kyle, HumanLayer, "Writing a good CLAUDE.md", 25 Nov 2025 [older].
    https://www.humanlayer.dev/blog/writing-a-good-claude-md
32. Matt Nigh, GitHub Blog, "How to write a great agents.md: Lessons from over 2,500
    repositories", 19 Nov 2025 [older].
    https://github.blog/ai-and-ml/github-copilot/how-to-write-a-great-agents-md-lessons-from-over-2500-repositories/

### Repositories (see table in (c); stars from the GitHub API on 2026-11-08)
33. https://github.com/anthropics/claude-code (CLAUDE.md, `.claude/commands`)
34. https://github.com/vercel/ai (`.agents/guidelines`, `.agents/verifiers`, hooks)
35. https://github.com/PostHog/posthog (`.agents`, `.claude/agents`, SessionStart hooks)
36. https://github.com/home-assistant/core (`.claude/agents`, `.claude/skills` with scripts)
37. https://github.com/cloudflare/workers-sdk (nested AGENTS.md)
38. https://github.com/astral-sh/uv (tiered-verb AGENTS.md)
39. https://github.com/ghostty-org/ghostty (tiny root, nested files, tripwire)
40. https://github.com/zed-industries/zed (`.rules` governance, tripwire)
41. https://github.com/huggingface/transformers (`.ai/AGENTS.md` plus links)
42. https://github.com/modelcontextprotocol/python-sdk (`@AGENTS.md` import)
43. https://github.com/obra/superpowers (agent-directed contributor guide)
44. https://github.com/pydantic/pydantic-ai (handoff and decision scripts in skills)
45. https://github.com/vercel/next.js, https://github.com/apache/airflow,
    https://github.com/langchain-ai/langchain, https://github.com/sst/opencode,
    https://github.com/microsoft/vscode (file listings and headings only)

### Seen only as search titles, not opened (do not rely on)
- IH-Benchmark arXiv:2607.25987, XIH-Bench 2607.23545, "Where Instruction Hierarchy Breaks"
  2606.07808, "Agent drift" 2601.04170, LongHorizon-Harness 2608.01964.
- Boris Cherny's Jan 2026 setup thread (Threads/X) and its many secondary write-ups.
- Addy Osmani X article and @choblin29 X post (via FourWeekMBA).

## Limits of this evidence
- Most controlled studies test one trivial marker rule (a comment string) on small task sets,
  so absolute rates do not transfer to a rule like "never leave a high-leverage moment".
- Models tested are Sonnet 4.6 to 5, Opus 4.6 to 5, not Opus 5.5; compaction numbers come from
  Sonnet 4.6 and generic compactors.
- Zhang's "random rules help as much as curated" result cuts against over-investing in rule
  content; read it as: the file is a weak lever, enforcement is the strong one.
- All blog claims are anecdotes unless marked. Star counts are API readings and I did not
  audit them.
