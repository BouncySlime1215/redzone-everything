---
id: R-MEMORY
title: "Long-horizon memory and handoffs for coding agents"
date: 2026-11-08
stream: rules
author: "unrecorded (2026-11-08 rules research agent)"
status: reviewed
supersedes: []
sources: 37
verified: 35
affects: []
gate: []
---

# Long-horizon memory and handoffs for coding agents

Research note, 2026-11-08. Read-only analysis; nothing here is a measured RedZone result unless marked `[local]`.
Question: what keeps a multi-week, many-session, compaction-prone Claude project coherent, and how should RedZone
(solo dev with ADHD, 63k lines, `decisions.md` at 154 KB) arrange memory, handoffs and checks.

Source rules: every entry in section 4 was opened (arXiv abstract page, publisher page or repo page) unless tagged
**[snippet-only]** (seen only in search results; X/Twitter pages returned HTTP 402). Numbers come from abstracts or
the fetched page, not full-paper reads, unless noted. Dates are the page's own; **[older]** = before Aug 2026.
This extends, and does not repeat, `agent-coordination-and-rationale.md` (AWM, ACE, ADRs, fragments) and
`opus-5-5-harness-and-time.md` section 6-7 (memory hooks); where they already propose something I say so.

## 0. What I measured in the repo `[local]`

- `docs/memory/decisions.md`: 154,209 bytes, 1,296 lines, 241 dated entries in 10 sections. One section ("AI") holds
  141,015 bytes (200 entries). Median entry head line is 115 chars; the p90 is 518; the bytes are mostly indented
  continuation lines under each entry (the `- 2026-` head lines total only 46.8 KB). 84 entries are dated Nov 4 and
  73 Nov 5. The word "supersed" appears once; "override" 30 times; there are no status fields. So reversals are
  written as prose and a reader cannot tell which decisions are still binding.
- `scripts/context_pack.py` pastes CLAUDE.md, the whole `handoff.md` (23 KB), 120 lines of `status.md`, **every
  decision in full** (154 KB), lesson titles, PRs, scorecards. Roughly 50k tokens (estimate, 4 bytes/token) before the
  new session has done anything, and the pack is dominated by the one file nobody can scan.
- `handoff.md` has 12 sections; sections 8 (what got done), 10 (lessons) and 11 (log sources) are history, not
  state. `lessons.md` (43 KB, 27 entries) already has literal `Signals:` and `redzone/memory.py` matches them to
  errors: this is the one retrieval path that exists and works.
- `check_memory.py` checks links and that a PR touching a default or gate adds a dated line. It does not check
  that any claim is still true.
- Per-user Claude memory: 7 files in `~/.claude/projects/<proj>/memory/` with a `MEMORY.md` index (fits the 200
  line / 25 KB load limit by a wide margin).

## 1. Ranked techniques with evidence

Ranking weighs effect size, independence of sources, recency, and fit with "one repo, many short-lived Claude
sessions, one human". Cost is for RedZone.

| # | Technique | Evidence (verified) | Cost here |
|---|---|---|---|
| 1 | **Hand the successor a context-bearing note, not just the repo** (summary or structured). | Handoff Debt (arXiv 2606.02875, Jun 2026, v2 Aug 30): 75 source tasks, 724 takeover runs per model, 3 models. Versus repo-only takeover, context-bearing handoffs cut median agent events 20-59% and prompt tokens 42-63%. Solve-rate gains were smaller and model-dependent. The abstract does not say which note format won. | Low: a 60-line template (section 2b). |
| 2 | **Keep rules and progress in files that are re-read from disk, never only in chat.** | Compaction Cliff (2608.22752, Aug 2026): Claude Code's /compact prompt on Sonnet 4.6 kept 53% of safety rules after one compaction and 10% after five, over 20 production configs. Claude Code docs: project-root CLAUDE.md is re-read from disk and re-injected after /compact; conversation-only instructions are lost. ATWZ (2607.22917, Jul 2026) lists the same four failures: state lost, compaction blurs detail, decisions trapped in chats, handoff prompts rewritten each time. | Low. Already in CLAUDE.md; add a PreCompact/SessionStart hook pair (section 2e). |
| 3 | **Small always-loaded layer; detail one hop away and loaded on demand.** | Anthropic (Lance Martin, 2026-11-06): injected memory must stay small, an incorrect note propagates everywhere; Claude Code auto memory loads only the first 200 lines or 25 KB of `MEMORY.md`. Claude Code docs: CLAUDE.md under about 200 lines; imports do not save context. Letta context repositories (2026-02-12): file tree always in prompt, files have frontmatter, defrag target "15-25 focused files". Chroma Context Rot (2025-07-14) **[older]**: all 18 models degrade as input grows, even on simple tasks; LongMemEval prompts of about 300 tokens beat about 113k. | Medium: split decisions.md (section 2c). |
| 4 | **Anchor each remembered claim to the code or config it depends on, and refuse to trust it when the anchor changed.** | EA-Graph (2608.04278, Aug 2026): claims tied to content hashes of the exact spans they depend on; stale fact on the path means refuse. 42 sessions over 7 synthetic worlds: Haiku 4.5 median F1 1.000 with anchors vs 0.270 prose notes vs 0.286 no memory (wins in all 7 worlds, p=0.0156); Sonnet 5 hit the ceiling in every condition so nothing separated. Synthetic repos, small n. | Medium. Config-key and symbol anchors are cheap; full artifact graph is not needed. |
| 5 | **Ground "done" in an external check, not the agent's own judgement.** | Progress mirage (2607.25152, Jul 2026): an agent that graded its own cycles claimed improvement in all 54 and 56% of them measured zero or worse; the best deployed state eroded 19%; even a judge reading artifact, diff and history accepted real regressions 44% of the time. Where success could be checked from the artifact, the mirage fell to zero. Anthropic harness post (2025-11-26) **[older]**: feature list in JSON where agents may only flip `passes`, because models alter JSON less than Markdown; a startup routine re-runs a basic end-to-end test before new work. | Low-medium. RedZone already has scorecards and the release gate; make the handoff cite them. |
| 6 | **Store verified rules, the reason they are true, and tested facts. Do not store what the environment already shows. Do not prescribe a fixed "what to record" rule.** | Anthropic 2026-11-06: store "how Claude knew"; in Pokemon Fable 5 kept about 10 files (4 badges) while Sonnet 3.7 wrote 413 files of visible state (1 badge); fixed rules such as "record only failures" lowered Opus 5 and Opus 4.7 gains. CL-Bench database task mean reward with memory: Opus 5.5 0.655 to 0.838, Sonnet 5.5 0.644 to 0.833 (vendor benchmark). | Free: a rule in the memory guide. |
| 7 | **Write the rationale and a supersession link on every durable instruction.** | "Why does CLAUDE.md keep growing" (2608.11095, Aug 2026): 247,694 instruction lifetimes in 1,867 repos; prompts grew 226%; older instructions are less likely to be deleted; once rationale is lost, safe removal is costly ("catastrophic remembering"). Prompt comments carrying the reason removed 99.3% of excess instructions in IFEval tests. Context Matters (2604.03826, Apr 2026): for drafting ADRs, a recency window of 3-5 prior records works best (750 repos). | Low per entry; one-off migration for 241 entries. |
| 8 | **Offline consolidation ("dreaming") that checks notes against transcripts, proposed as a diff.** | Anthropic 2026-11-06: in Pokemon a wrong landmark note blocked the run at Mt. Moon; after dreaming corrected it Claude reached Cerulean City at action 1,935 and the uncorrected run never passed the mountain. Wiki alone 13% to 48% over 128 dream sessions; wiki+search 61% vs search alone 53%, about 20k tokens saved per question; one wiki grew to 1.77x its corpus (size risk). Managed Agents dreaming (page dated 2026-05-19; search results say announced May 6): Harvey reports about 6x completion, vendor-reported. OpenAI cookbook: consolidation "the most sensitive and error-prone stage". | Medium. Weekly cloud job opening a PR; never auto-merge (section 3). |
| 9 | **Progressive disclosure retrieval: index first, then fetch by id.** | claude-mem README: `search` returns about 50-100 tokens per hit, `get_observations` about 500-1,000 per fetched id, claims about 10x savings (vendor claim, not independently measured). Anthropic context engineering (2025-09-29) **[older]**: keep lightweight references (paths, queries) and load just in time; hybrid often best. | Low. This is what `why.py` and the index in 2c do, with deterministic keys and no vector store. |
| 10 | **Record failed attempts and the reason, and retrieve them before repeating work.** | ReasoningBank (2509.25140, v2 Mar 2026, ICLR 2026): distils strategies from self-judged successes and failures and beats stores of raw trajectories or successes only (the abstract gives no numbers). Practitioner pattern in handoff docs (claude-tips, search snippet **[snippet-only]**): list what did not work so it is not retried. | Low: a "Do not retry" block (2b). |
| 11 | **Compact at a decision point, not a token threshold.** | SelfCompact (2606.23525, Jun 2026, v2 Jul 10): model-called compaction plus a fire/hold rubric; up to 18.1 points on math, 5-9 on agentic search, 30-70% lower per-question cost; math and search benchmarks, not coding. Context Compaction Theory (2608.01326, Aug 2026): formal model, summary generation equals one-way communication complexity, no experiments in abstract. Factory (2025-12-16) **[older]**: probe-based scores Factory 3.70, Anthropic 3.44, OpenAI 3.35 of 5; artifact trail was the weakest dimension for all three (2.45, 2.33, 2.19). X advice to compact near 60% context **[snippet-only]**. | Low: policy, not code. |

Findings that cut against doing more:
- Bigger context files are not free: Evaluating AGENTS.md (2602.11988, v3 Sep 29 2026) reports inference cost up over 20% on average from context files; an efficiency study on 124 PRs (2601.20404, Mar 2026) found 28.64% lower median runtime and 16.58% lower output tokens with an AGENTS.md present. Both can be true: a short instruction file helps, a long one costs. (Output-token claims are from abstracts; success-rate details need the full papers.)
- Vector memory is not the default for coding agents: a source study of eleven harnesses (arXiv 2609.00006, submitted Jul 15 2026; the arXiv id and date do not match, so treat the listing with caution) says none retrieves code with embeddings. Mem0 (2504.19413) and Zep (2501.13956) report gains on chat benchmarks (LOCOMO, LongMemEval), not on coding.
- Memory is an attack and drift surface: MemoryGraft (2512.16962, Dec 2025) plants fake "successful experiences" that a retrieving agent imitates, persisting across sessions. MemoryAgentBench (2507.05257, v4 Jun 2026): no tested method masters retrieval, test-time learning, long-range understanding and selective forgetting; selective forgetting is the weak one.
- Kun Chen on X (undated in results, **[snippet-only]**) advises turning Claude Code auto memory off because stale memory misled the agent. Treat as an anecdote that supports the staleness checks, not as a measured result.

## 2. Design for RedZone

### 2a. Memory layout (three tiers, one owner each)

```
Tier 0: always loaded (hard budgets, CI-enforced)
  CLAUDE.md                       <= 80 lines   rules + commands, each rule ends "<!-- why: D-... -->"
  docs/handoff.md "capsule"       <= 80 lines / 6 KB   state, next action, binding decisions (ids), do-not-retry
  ~/.claude/.../MEMORY.md         <= 120 lines  Nick's preferences only (user-sourced, never repo facts)

Tier 1: loaded on demand by key (file, config key, machine/state, log word)
  docs/memory/decisions.md        <= 150 lines  INDEX + "standing invariants" (section 2c)
  docs/memory/decisions/<topic>.md  <= 25 KB each, ~8-10 topic files
  docs/memory/decisions.d/<pr>.md   per-PR fragments folded by the integration train (already proposed)
  docs/memory/lessons.md          keep format; add anchors:
  docs/memory/runbooks/*.md       procedures
  docs/status.md, docs/roadmap.json   board

Tier 2: archive, never auto-loaded
  docs/memory/journal/YYYY-MM-DD.md   one line per finished task (Stop hook; proposed earlier, not verified as built)
  docs/memory/archive/handoff-YYYY-MM-DD.md   old "what got done" sections moved out of handoff
  data/ transcripts, scorecards (local)
```

What each store holds (Anthropic: verified rules plus how known; not what is visible):
- **Decisions:** what Nick chose, the reason, the scope, and evidence. Source tag `source: nick | test | agent`.
- **Lessons:** symptom, cause, fix, `Signals:`. Failed attempts with the reason go here too (a lesson whose fix was
  "stop doing X").
- **Open questions:** only in the capsule, max 3, each with an owner and a date; closed ones become decisions.
- **Never store:** current scores, scoreboard state, anything `git log`, `docs/northstar.md` or `/api/*` shows.
- **Size limits:** table in 2f. Reason: 200 lines/25 KB is the platform's own load limit for the index; beyond it
  content is silently dropped.

### 2b. Handoff template (replaces handoff.md sections 1-7; target 60-80 lines)

YAML front matter (machine-read by `context_pack.py` and CI), then short prose. The Anthropic harness post chose JSON
for pass/fail fields because models edit it less than Markdown; keep status in front matter, narrative in Markdown.

```markdown
---
as_of: 2026-11-08T14:05Z        # UTC, written by script, not by the agent
sha: 42e2ee8f                   # HEAD when written; CI warns if not an ancestor or >40 commits old
branch: codex/resume-redzone
author: session-name
next_action: "run `python scripts/release_gate.py heldout.json`; if mid-play entries > 0 stop and read lesson L-..."
nick_next: "approve or reject PR #NNN (about 2 minutes)"   # exactly one, ADHD rule
threads:                         # at most 5, mirrors status.md
  - {id: T1, owner: session-a, branch: feat/x, state: in_review, claims: ["redzone/decider/cuts.py"]}
binding: [D-2026-11-07-03, D-2026-11-06-11]   # decision ids that constrain this work
---
## Now (2 sentences max)
## Done since last handoff   (each item: claim + evidence = command, scorecard path or PR; no evidence = "unverified")
## Do not retry               (attempt, why it failed, evidence/lesson id)
## Unverified / assumptions   (anything not checked against code or a run)
## Open questions for Nick    (max 3; each has the default we will take if no answer by <date>)
## Risks / traps              (links to lesson ids)
## Verify first               (<= 5 commands a fresh session runs before trusting this page)
```

Why these fields: Handoff Debt supports context-bearing notes; the plur9 checklist (DEV, "Oct 3", year not shown)
uses Current task / Confirmed decisions / Evidence / Completed / Still unverified / Next action; progress-mirage
supports "evidence or unverified". The older sections 8, 10, 11, 12 move to `archive/` and `lessons.md`.

Rules for writers: update the capsule before stopping and before `/compact`; a fresh session must run "Verify first"
and say what differs before editing (Anthropic harness startup routine).

### 2c. decisions.md split and index plan

Problem (measured above): one 141 KB section, no status, no scope, nothing retrievable except by reading.

Steps (about 2-3 hours of agent time plus 15 minutes of Nick review; do it on a branch to `integration/next`):
1. **Mechanical split.** One-off script `scripts/split_decisions.py` (stdlib): parse `^- YYYY-MM-DD:` heads with their
   indented continuation, keep the verbatim text. Assign an id `D-YYYY-MM-DD-NN` in file order. Never delete or
   rewrite wording (ACE warns that wholesale rewrites lose detail).
2. **Topic assignment.** Group the 200 "AI" entries into about 8-10 topic files by keyword (cuts/switching, decider and
   scoring, noggin/brain, sync and timing, tuners/TV, eval and gates, auto-fix and platform, data and Kaggle,
   process/budget). Do it with a keyword map, then one agent pass proposes the leftovers; Nick spot-checks 10.
3. **Entry header.** Add three lines under each entry, filled by an agent from the text, reviewed once:
   `status: active | superseded-by D-... | reverted`, `scope: <paths / config keys / machines>`,
   `anchors: <path::symbol or config.key=value>`. Mark `source: nick` when the entry cites Nick's words.
   Supersession is the key value: the 30 "override" and 10 "instead of" mentions are the candidates.
4. **Index file.** `docs/memory/decisions.md` becomes: (a) the format rules (existing header), (b) **Standing
   invariants**: at most 30 one-line active decisions that constrain most work (Rule #1, no Peacock, $0, gates ship
   OFF, Mac is light, etc.), each with its id, (c) a topic table: file, count active/superseded, newest date.
5. **Retrieval tool `scripts/why.py`** (stdlib, no embeddings; deterministic like the harness study found elsewhere):
   `why.py redzone/decider/cuts.py`, `why.py decider.switch_cost`, `why.py --machine control --state X`,
   `why.py --signal "score bug"`, `why.py --since 2026-11-05 --status active`. Output: id, status, one-line, path. It
   reads the headers only; the agent opens the full entry by id. This is the claude-mem 3-step idea (index, then
   fetch) without a database.
6. **Fragments.** New decisions go to `decisions.d/<pr>.md` with the same header; the train folds them into the topic
   file (already proposed in `agent-coordination-and-rationale.md` 2(b)4). Update `check_memory.py` (`DECISIONS`
   constant) to accept either location so the "default or gate change needs a dated line" rule keeps working.
7. **Drafting help.** When an agent drafts a decision, `why.py` gives it the 3-5 most recent entries in the same topic
   (Context Matters result). A human or test confirms before `status: active`.

### 2d. `scripts/context_pack.py` changes

Today: everything in full, no budget, no staleness signal. Change to a budgeted, keyed pack:
1. `--budget-tokens 12000` default (estimate by bytes/4). Print a size table at the top (part, bytes, tokens). Exit
   non-zero under `--check` when over budget; add `--check` to the `memory` CI job.
2. Parts in priority order, each trimmed to its cap: CLAUDE.md (full, <=80 lines), handoff capsule (full), standing
   invariants, status board top 40 lines, decision index, lesson titles with ids, open PRs, scorecard deltas.
3. `--files a,b` and `--task "text"`: add the decisions and lessons whose `scope`/`anchors`/`Signals` match, headers
   only, plus the `citymap --impact` slice if that command exists. Default `--files` from `git diff --name-only
   origin/integration/next` so a mid-PR session gets its own context.
4. **Truth header** at the top: `handoff as_of` age in hours, commits since `sha`, count of STALE anchors, expired
   decisions, and a line "run `why.py --stale` before trusting memory" when non-zero.
5. Take the handoff from `LIVE_REF` only when it is newer than the working-tree copy; print which one and its age.
6. Never paste decisions in full. Remove the 154 KB block. Expected pack: about 10-12k tokens (estimate) versus about
   50k now.
7. Emit the "Verify first" commands from the capsule as a checklist the new session must tick before editing.

### 2e. Hooks that make compaction safe (extends `opus-5-5-harness-and-time.md` Q4.7)

- **PreCompact:** stdout becomes compaction instructions (that note cites a 10,000 character cap; I did not re-verify
  it). Emit: "Preserve: Rule #1, the capsule's next_action, active claims, binding decision ids, unverified items,
  files edited this session. Drop: tool output."
- **SessionStart (matcher `compact`) and normal start:** print the capsule and `why.py --stale`. Compaction Cliff shows
  summaries drop rules; Claude Code re-reads CLAUDE.md from disk, so put every must-never-drop rule there, not in chat.
- **Stop hook:** append one journal line (goal, files, result, next). If the capsule is older than the last commit by more
  than N minutes, print "update the capsule" (do not block).
- Compaction policy for Nick: compact at task breaks, `/clear` between tasks, write the capsule first. A pure
  token-threshold auto-compact is the failure SelfCompact describes.

### 2f. Staleness and truth checks in CI (`scripts/check_memory.py` gains these)

Block (fail the job):
1. Links resolve (exists).
2. Size budgets: CLAUDE.md <= 80 lines, capsule <= 6 KB, each topic file <= 25 KB, decisions.md index <= 150 lines,
   context pack under budget.
3. A PR that changes a default, gate or guarded file adds a decision (exists), and the entry has `status`, `scope`.
4. Supersession integrity: every `superseded-by` target exists and is `active`; no cycles.
5. Handoff front matter parses; exactly one `next_action` and one `nick_next`; `sha` is an ancestor of HEAD.

Warn on the PR (comment, not block; block only for entries tagged `gate`):
6. **Anchor drift.** For each decision/lesson `anchors:`, compute a hash of the config value (YAML parse) or the AST span
   of the symbol (`ast`, stdlib) and compare with `docs/memory/anchors.lock.json`. States: FRESH, STALE (anchor
   content changed), ORPHAN (anchor missing). The PR author must re-verify, supersede, or refresh the lock with a note.
   This is EA-Graph's hash-over-spans idea at the cheapest granularity. File-level hashes over-invalidate (in their
   test about 88 of 96 behaviours flagged for about 17 affected), so use symbol or key level.
7. Expired: `expires:` passed (risky-default decisions; proposed in the earlier note).
8. `Signals:` strings in lessons that no longer appear anywhere in `redzone/` or recent logs (dead retrieval keys).
9. Handoff older than 24 h while commits landed since; "Done" items with no evidence.

Weekly (scheduled, off the Mac, opens a PR; never merges itself):
10. **Consolidation pass ("dream"):** read the week's journal lines and the capsule history, propose: merge duplicate
    lessons, mark superseded decisions, move closed open-questions to decisions, list notes contradicted by code.
    Anthropic's finding is that this fixes wrong notes; OpenAI's cookbook warns consolidation is the error-prone step,
    so the output is a reviewed diff with each change citing the transcript or commit that justifies it.

### 2g. Retrieval summary

| Key | Mechanism | Exists? |
|---|---|---|
| Log word / error | `lessons.md` `Signals:`, `Memory.hints()` | yes |
| File or config key | decision `scope`/`anchors`, `why.py <path>` | new |
| State machine state | decisions tagged with machine and state names from `redzone/machines/*` | new |
| Time | `why.py --since`, journal files | new |
| Task text | `context_pack.py --task` keyword match on headers | new |
Embeddings are not needed at this size (about 250 entries); add only if keyword retrieval measurably misses.

## 3. Risks

- **Migration error.** Splitting and tagging 241 entries can mis-assign status or scope. Mitigation: keep verbatim text,
  generate headers, Nick samples 10, run `check_memory.py` before merge, keep the old file in `archive/` for one
  release.
- **Agent-written memory is unverified.** MemoryGraft shows retrieved "successful experiences" get imitated. Mitigation:
  `source:` tag; only `nick` or `test`-confirmed entries count as binding; agent entries start `status: proposed`.
- **Anchors rot or over-fire.** Alert fatigue turns the warning into noise. Mitigation: symbol/key granularity, warn
  only, review the first two weeks of warnings, drop anchors that fire without a real change.
- **Capsule goes stale anyway.** Mitigation: `sha` and age in the truth header, "Verify first" commands, Stop-hook reminder.
  The checks catch age, not correctness; correctness comes from running the commands.
- **Over-engineering for a solo dev.** Cost is real: about 6-8 hours total. Stage it (section 5) and stop when the
  pack is small and `why.py` answers; skip the weekly dream job until two weeks of journals exist.
- **Evidence is thin where it matters.** EA-Graph is synthetic repos and small n; Handoff Debt gives no format ranking;
  SelfCompact is not coding; Anthropic's numbers are vendor benchmarks; Harvey's 6x is vendor-reported. None
  measured on RedZone. Plan a local check: run 3 fresh sessions on the same task with old pack vs new pack and compare
  agent events and tokens (Handoff Debt's metric).
- **Rules in memory are context, not enforcement** (Claude Code docs). Hard rules (Rule #1, secrets) need tests or a
  PreToolUse hook, not only a line in a file.
- **Dream/consolidation can bloat or delete.** Anthropic saw a wiki reach 1.77x its corpus; always cap size and require a diff.

## 4. Bibliography (opened and checked unless tagged)

Official and vendor posts and docs
1. L. Martin (Anthropic), "The components of agent memory", 2026-11-06. https://www.anthropic.com/research/components-of-agent-memory (opened)
2. Rajasekaran, Dixon, Ryan, Hadfield (Anthropic), "Effective context engineering for AI agents", 2025-09-29 [older]. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents (opened)
3. J. Young (Anthropic), "Effective harnesses for long-running agents", 2025-11-26 [older]. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents (opened)
4. Anthropic, "Managing context on the Claude Developer Platform" (memory tool, context editing: +39% combined, +29% editing alone, -84% tokens on a 100-turn eval, internal), 2025-09-29 [older]. https://claude.com/blog/context-management (opened)
5. Anthropic, "New in Claude Managed Agents: dreaming, outcomes, multiagent orchestration", page dated 2026-05-19 (news coverage says announced May 6; discrepancy unresolved). https://claude.com/blog/new-in-claude-managed-agents (opened)
6. Claude Code docs, "How Claude remembers your project" (CLAUDE.md under about 200 lines; auto memory 200 lines/25 KB; CLAUDE.md re-read after /compact; memory is context, not enforcement), live page, undated. https://code.claude.com/docs/en/memory (opened)
7. Claude Code docs, "Let Claude coordinate ongoing work with Projects" (public beta; project memory `MEMORY.md` index, project instructions up to 16,000 characters, threads compact automatically), live page, undated. https://code.claude.com/docs/en/claude-projects (opened). Relevance: a possible future home for shared memory; not available on Team/Enterprise and rolling out.
8. OpenAI Cookbook, "Context Engineering for Personalization - State Management with Long-Term Memory Notes", undated (example outputs dated 2026-01-07). https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization (opened)
9. Letta, "Introducing Context Repositories: Git-based Memory for Coding Agents", 2026-02-12. https://www.letta.com/blog/context-repositories (opened)
10. Factory, "Evaluating context compression for AI agents", 2025-12-16 [older]. https://factory.com/news/evaluating-compression (opened)
11. Chroma (Hong, Troynikov, Huber), "Context Rot", 2025-07-14 [older]. https://www.trychroma.com/research/context-rot (opened)

Papers (arXiv abstract pages opened)
12. Handoff Debt: The Rediscovery Cost When Coding Agents Take Over Interrupted Tasks. KC, Budathoki. 2606.02875, 2026-06-01, v2 2026-08-30. https://arxiv.org/abs/2606.02875
13. The Compaction Cliff in Long-Running AI Agent Memory. Zerhoudi, Mitrovic, Granitzer. 2608.22752, 2026-08-24. https://arxiv.org/abs/2608.22752
14. Why Does CLAUDE.md Keep Growing? Catastrophic Remembering in Agentic Coding. Chakrabarti. 2608.11095, 2026-08-11. https://arxiv.org/abs/2608.11095
15. EA-Graph: Artifact-Anchored Verification Memory for Coding Agents under Upstream Drift. Hsu, Chi, Everett. 2608.04278, 2026-08-04. https://arxiv.org/abs/2608.04278
16. When Do Agent Loops Mistake Stagnation for Progress? Park, Choi. 2607.25152, 2026-07-27, v2 2026-09-29. https://arxiv.org/abs/2607.25152
17. Agent Team Work Zone: A Persistent Workspace for Long-Lived Claude Code Agent Teams. Wang. 2607.22917, 2026-07-24. https://arxiv.org/abs/2607.22917
18. Self-Compacting Language Model Agents. Li et al. 2606.23525, 2026-06-22, v2 2026-07-10. https://arxiv.org/abs/2606.23525
19. Context Compaction Theory. Tirmazi, Markelon, Bishop, Mitzenmacher. 2608.01326, 2026-08-02. https://arxiv.org/abs/2608.01326
20. Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (survey). Du. 2603.07670, 2026-03-08. https://arxiv.org/abs/2603.07670
21. Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents? Gloaguen et al. 2602.11988, 2026-02-12, v3 2026-09-29. https://arxiv.org/abs/2602.11988
22. On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents. Lulla et al. 2601.20404, 2026-01-28. https://arxiv.org/abs/2601.20404
23. Agent READMEs: An Empirical Study of Context Files for Agentic Coding. Chatlatanagulchai et al. 2511.12884, 2025-11-17, v2 2026-08-09. https://arxiv.org/abs/2511.12884 (2,303 files, 1,925 repos; files change by small frequent additions)
24. Context Matters: Evaluating Context Strategies for Automated ADR Generation Using LLMs. Gupta et al. 2604.03826, 2026-04-04. https://arxiv.org/abs/2604.03826
25. Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions (MemoryAgentBench). Hu, Wang, McAuley. 2507.05257, 2025-07-07, v4 2026-06-28. https://arxiv.org/abs/2507.05257
26. ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory. Ouyang et al. 2509.25140, 2025-09-29, v2 2026-03-16 (ICLR 2026). https://arxiv.org/abs/2509.25140
27. MemoryGraft: Persistent Compromise of LLM Agents via Poisoned Experience Retrieval. Srivastava, He. 2512.16962, 2025-12-18. https://arxiv.org/abs/2512.16962
28. A-MEM: Agentic Memory for LLM Agents. Xu et al. 2502.12110, 2025-02-17, v11 2025-10-08 (NeurIPS 2025) [older]. https://arxiv.org/abs/2502.12110
29. Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory. Chhikara et al. 2504.19413, 2025-04-28 [older]. https://arxiv.org/abs/2504.19413
30. Zep: A Temporal Knowledge Graph Architecture for Agent Memory. Rasmussen et al. 2501.13956, 2025-01-20 [older]. https://arxiv.org/abs/2501.13956
31. LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory. Wu et al. 2410.10813, 2024-10-14 (ICLR 2025) [older]. https://arxiv.org/abs/2410.10813
32. MemGPT: Towards LLMs as Operating Systems. Packer et al. 2310.08560, 2023-10-12 [older]. https://arxiv.org/abs/2310.08560
33. Harness Engineering: Anatomy, Architecture, and Evolution of Coding Agents. Barbaste et al. 2609.00006, listed submitted 2026-07-15 (id and date disagree). https://arxiv.org/abs/2609.00006 (opened; used only for "no embeddings" remark; abstract has no memory findings)

Open-source repos and practitioner pages
34. claude-mem (thedotmack), README: SQLite plus Chroma, 3-step progressive disclosure, Apache-2.0, about 114.8k stars at fetch time. https://github.com/thedotmack/claude-mem (opened; use only the official repo, search results showed look-alike mirrors)
35. Beads `bd` (steveyegge, org now gastownhall per search results): git/Dolt-backed task graph, `bd prime`, `bd remember`, compaction of closed tasks, MIT. https://github.com/steveyegge/beads (opened; release date not shown)
36. (removed: "Long AI agent runs need a handoff", Substack, returned HTTP 404 when opened, so it is not cited.)
37. plur9, "Stop Losing Agent Context: A Session Handoff Checklist", DEV, "Oct 3" (year not shown on page; written by an AI agent for a vendor). https://dev.to/plur9/stop-losing-agent-context-a-session-handoff-checklist-3ldh (opened)
38. "How I run Claude Code on multi-week projects" (ArtRichards agent-playbook-suite), undated, author not named; charter/milestone/status files, generated INDEX.md, lifecycle metadata, `sync-and-commit` blocks commits when docs disagree with code. https://hn.jakechampion.name/reader/https://artrichards.github.io/agent-playbook-suite/blog/ (opened via mirror)

Snippet-only (seen only in search results, not opened; dates unconfirmed)
- X, Kun Chen: disable Claude Code auto memory because stale memory misled the agent. https://x.com/kunchenguid/status/2067728608584675465 (HTTP 402)
- X, Build Fast with AI: compact near 60% context, not 90%+. https://x.com/BuildFastWithAI/status/2054554755901329477
- X, elvis (omarsar0): summary of the ATWZ paper (item 17, which I did verify). https://x.com/omarsar0/status/2083556612971913569
- X article, victor_explore, "Your coding agent forgets its rules. Here is why, in 7 fixes, each backed by a paper". https://x.com/victor_explore/article/2103492817335222547 (HTTP 402)
- cADR (YotpoLtd, LLM ADR pre-commit tool) and adr-agent (macromania): found in search, not opened. GADR (arXiv 2608.17694, ADRs from meeting transcripts): search hit only.

Not found: a practitioner report dated Oct-Nov 2026 that I could open and that gives measured results on keeping multi-week agent projects coherent. The Oct-Nov 2026 evidence here is Anthropic 2026-11-06, the August-September arXiv revisions, and the unverified X posts.

## 5. Staged plan (about 6-8 hours total, each stage independently useful)

1. (about 45 min) Write `handoff.md` capsule with the 2b template; move sections 8, 10, 11 to `archive/`. Wins at once: a 6 KB handoff.
2. (about 90 min) `scripts/why.py` plus entry headers on the 30 standing invariants only. Wins: Rule-level decisions retrievable.
3. (about 2 h) `split_decisions.py`, topic files, index, `check_memory.py` size and supersession checks.
4. (about 90 min) `context_pack.py` budget, `--files`, truth header, `--check` in CI.
5. (about 90 min) Anchors lock and drift warnings; PreCompact/SessionStart/Stop hooks.
6. (after 2 weeks of journals) weekly consolidation PR job; the three-session old-vs-new pack comparison.

Which North Star line does this move? None directly; it protects Rule #1 and the 95% gate by keeping decisions and lessons
truthful across sessions. Expected measurable delta: pack tokens (about 50k to about 12k, estimate) and fresh-session
agent events on the same task (Handoff Debt metric). Not yet measured.
