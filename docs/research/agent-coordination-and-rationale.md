# Making RedZone legible and safe for many AI agents

Research note, 2026-11-08. Three questions: how to map the codebase so agents see how the systems connect,
how parallel agents should coordinate, and how to record why things were done. 40 sources were checked
against their arXiv or publisher page (title, authors, date, id). Venues come from the arXiv
comment field or the proceedings; where neither names one, the paper is listed as a preprint. Numbers are
from abstracts unless a note says otherwise. Nothing in this note is a measured RedZone result.

---

## 1. Summary: the 11 techniques with the strongest evidence, ranked

Ranking weighs effect size, how many independent papers agree, and how well the setting matches ours (one
Python repo, many short-lived coding agents). Cost assumes about 63k lines and the existing `scripts/citymap.py`.

| # | Technique | Evidence | Cost to adopt here |
|---|---|---|---|
| 1 | **Isolated workspaces and a central planner that merges through tests** (branch, worktree, merge, then a test gate) | CAID (Geng & Neubig 2026): +25.6 pts on PaperBench and +14.7 on Commit0 over a single agent. CooperBench (2026): agents that coordinate by chatting do about 30% worse than one agent doing both tasks. Agent PRs on GitHub (2026): 41.7% textual conflict rate for cross-agent concurrent pairs vs 19.8% for same-agent pairs. | Low: we already use worktrees and `integration/next`. The new piece is a written claim per task. |
| 2 | **A graph-shaped repo map handed to the agent** (files, symbols, import and call edges, ranked) | RepoGraph (ICLR 2025): new open-source SOTA on SWE-bench when plugged into four frameworks. LocAgent (ACL 2025): 92.7% file-level localization, +12% Pass@10 resolution. KGCompass (2025): 58.3% on SWE-bench Lite, and 89.7% of the bugs it localized had no location hint (multi-hop only). CGM (2025): 43.0% on Lite with an open 72B model. CodexGraph (NAACL 2025) and Aider's repo map do the same thing. | Low: `docs/citymap.json` already has import edges and owners. Add call edges and a ranked, token-budgeted view. |
| 3 | **Localize, then edit, then validate, as fixed phases** | Agentless (FSE 2025): 32.0% on Lite at $0.70 per issue with no free-form agent loop. AutoCodeRover (ISSTA 2024): AST-aware search tools, 19% at $0.43. Trajectory studies (ASE 2025; 2025): failing runs are longer and loop on search or fix without running tests. | Free: a rule in CLAUDE.md plus a PR checklist line. |
| 4 | **A narrow, purpose-built agent-computer interface** (small tools with guardrails such as lint-on-edit, compact search output) | SWE-agent (NeurIPS 2024): the interface design alone took SWE-bench pass@1 to 12.5%. Anthropic (2025): rewriting tool descriptions cut later agents' task time by 40%. | Low: `scripts/` already acts as the ACI. Make outputs short and machine-readable. |
| 5 | **Short, instruction-only context files; no prose repo overview** | AGENTS.md efficiency (2026): −28.6% median runtime, −16.6% output tokens, same completion rate. Evaluating AGENTS.md (2026): overall success does not rise, cost rises more than 20%, agents follow instructions well, and repo overviews do not help. Agent READMEs (2025): 2,303 files studied; only 14.8% state security rules. Lost in the Middle (TACL 2023): content buried mid-context gets used less. | Free: trim CLAUDE.md to rules and commands, and point to the generated map rather than describing the code. |
| 6 | **Dependency-aware change planning** (follow the edges of a change to find what else must change) | CodePlan (FSE 2024): 5 of 6 repos passed validity checks vs 0 of 6 for the same context without planning. | Medium: a `citymap --impact <file>` command that lists importers, contracts and tests. |
| 7 | **Explicit state machines for agent and system workflows** | StateFlow (COLM 2024): +13 and +28 pts success over ReAct, at 3-5x lower cost. CodeR (2024) and MetaGPT (ICLR 2024): fixed task graphs and SOPs beat free chat. | Already started (`redzone/fsm.py`). The rest is persistence and visibility. |
| 8 | **Structured traces of every action, with reasons, as a first-class artifact** | MAST (2025): 14 failure modes found in 1,600+ traces, κ=0.88. AgentOps (2024): a taxonomy of what to trace (goals, plans, tools, guardrails). Anthropic (2025): full production tracing was needed to debug non-deterministic agents. Bouzenia & Pradel (ASE 2025): when thought and action disagree, the run is failing. | Low: `decision_records` exists for the app. Add a transition log for machines and an agent-action log. |
| 9 | **Distilled experience memory** (lessons and workflows induced from past runs and re-injected) | AWM (ICML 2025): +24.6% and +51.1% relative on Mind2Web and WebArena. ACE (ICLR 2026): +10.6% on agent benchmarks; incremental "playbook" edits avoid context collapse. ExpeL (AAAI 2024) and Reflexion (NeurIPS 2023): verbal lessons improve later tries. | Free: `lessons.md` already has this shape. Keep the edits incremental and add Signals. |
| 10 | **Lightweight decision records (ADRs), drafted by an LLM and checked by a human** | Nygard (2011): the format of context, decision, status and consequences, with supersede and never delete. Dhar et al. (ICSA 2024) and DRAFT (2025): LLMs draft useful ADRs but fall short of human level. Context Matters (EASE 2026): the last 3-5 records are the best context. Zhou et al. (TOSEM 2025): rationale recall 0.63-0.72 but precision about 0.27, and 1.6-3.2% of arguments may mislead. | Free to low: decisions.md is already a log. Add a status field and supersede links. |
| 11 | **"Why" in every commit message** | Tian et al. (ICSE 2022): a good message says what and why, and about 44% of real messages fall short. Zeng et al. (2025): an LLM judge with CoT and few-shot examples rates messages close to humans, so a CI check is feasible. | Low: a commit template plus a cheap CI lint. |

Lower-ranked or negative findings:
- Lock-free shared editing (CodeCRDT 2025) ran from +21.1% faster to 39.4% slower depending on the task, and
  still left 5-10% semantic conflicts. Not worth it here; git isolation is simpler.
- Chat-heavy multi-agent role-play (ChatDev, ACL 2024) has no numeric gains in its abstract.
- Anthropic (2025) says multi-agent setups are a poor fit for most coding, where tasks share context and
  depend on each other. Parallelize only work that is truly independent.

---

## 2. Recommendations for this repo

This repo already has more than most of the papers assume: `scripts/citymap.py` produces
`docs/citymap.md` and `.json` (import graph, owners, serves tags, orphans), `docs/memory/decisions.md` and
`lessons.md` with CI enforcement, lanes in `docs/lanes/`, nightly `decision_records` JSONL, and
`redzone/fsm.py` with `redzone/machines/{control,decider,platform}.py` served at `GET /api/machines`. The
recommendations below build on those pieces.

### (a) Map the codebase so agents see how the systems connect

1. **Add call edges and machines to the city map (techniques 2 and 6).** Extend `scripts/citymap.py`
   (ast only, no new dependency) with three edge types beyond imports: `calls` (module A calls a
   public function of B), `reads`/`writes` for the `data/*.jsonl` and DB tables named in string literals,
   and `machine` (which module calls `.to()` on which `Spec`). LocAgent, KGCompass and RepoGraph all
   gain from multi-hop edges that plain imports miss. About 2-3 hours.
2. **Add a ranked, token-budgeted slice: `python scripts/citymap.py --focus redzone/decider/cuts.py --tokens 1500`.**
   It prints the 1-2-hop neighbourhood, signatures only, ranked by how often other code references it, like Aider's graph-ranked map
   (default budget 1k tokens). Agents run this instead of reading 20 files. About 1-2 hours.
3. **Add an impact command: `--impact <file>`.** It lists importers, the contracts that file implements,
   the tests that import it, the machines it drives, and the north-star lines it serves. This follows
   CodePlan's "follow the change's dependency edges" idea. PRs paste the output into the risk section. About 1 hour.
4. **Generate the machines doc from the specs, not by hand.** `scripts/machines_doc.py` writes
   `docs/machines.md` (states, allowed edges and owner module for each `Spec`, as a Mermaid diagram) and runs
   `--check` in CI, the same way citymap does. The machines are then part of the map. About 1 hour.
5. **Keep CLAUDE.md for rules and commands only (technique 5).** Delete narrative overviews and point to
   `citymap --focus`. The 2026 AGENTS.md studies show instructions get followed while overviews cost
   tokens without helping. Put the hard rules (never join mid-play, secrets, the Mac is light) in the first
   20 lines, because content in the middle is under-used. About 20 minutes.
6. **Burn down the 180 unowned modules.** Every module gets an owner lane in architecture.md, so the
   map can answer "who do I ask". About 1 hour per lane, and it can be split across agents.

### (b) Coordination rules for parallel agents

Evidence: CAID, CooperBench, the GitHub PR conflict study, MAST and Anthropic all agree on four things.
Isolate the work, plan centrally, merge through tests, and do not coordinate by chatting.

1. **One worktree and one branch per task. No agent edits another task's worktree.** This is CAID's
   isolated workspace. It is already the norm here; make it a rule.
2. **Write a claim before editing.** Add `docs/claims/<task-id>.json` (or a table in status.md) with fields
   `{task, agent/session, branch, files_glob, machines, started, expires}`. A CI step and a pre-commit
   hook fail when a PR touches a file inside another open claim's glob. This turns CooperBench's
   "incorrect expectations" failure into a mechanical check. Claims expire after 24 h. About 2 hours.
3. **Plan by the dependency graph.** The INTEGRATION planner runs `citymap --impact` on each queued task
   and does not run tasks with overlapping impact sets in parallel. Overlapping tasks go in sequence.
   The GitHub study found 84% of conflicted files were source files, so dependency manifests are not the
   main risk.
4. **Hot files get a single owner.** `default_config.yaml`, `decisions.md`, `status.md`, `citymap.*` and
   `machines/*` are touched by most PRs. Agents append to a per-PR fragment (`docs/memory/decisions.d/<pr>.md`)
   and the integration train folds the fragments in, the way towncrier does changelogs. Fewer add/add
   conflicts, which were about 42% of conflicts in the 2026 study. About 1-2 hours.
5. **Handoffs carry a contract, not a story.** Every subagent brief has four parts: objective, output
   format, tools, and limits (from Anthropic 2025). Results go to a file and the reply is a path, which
   avoids the "game of telephone". The `handoff.md` entry uses fixed fields: state, next step, open
   claims, risks.
6. **Merge only through a test gate.** CAID's gains come from structured integration checked by tests.
   Keep the integration train as the only path into `integration/next`. MAST's third failure category is
   missing or weak task verification.
7. **Cap parallelism by independence, not by thread count.** The 5-thread cap is fine. A sixth task waits
   if its impact set overlaps an open claim.

### (c) Keep records of why things were done

**In code**
1. **Every transition must give a reason.** Make `why` required in `Machine.to()`, enforced by a lint
   or a test that greps the call sites, and use short stable codes (`dead_ball`, `guard_veto:<name>`).
   StateFlow and MAST both depend on the trace saying why the state changed.
2. **Docstring header on decision modules:** one line on what it does (the citymap "role" already reads
   this) and one line `Why: decisions.md YYYY-MM-DD <anchor>`, so code links back to the decision.

**In commits and PRs**
3. **Commit template:** subject, then `Why:`, then `North Star:` and `Delta:` lines. Tian et al. show
   the "why" is what goes missing. A CI lint checks that `Why:` exists. Zeng et al. support a later
   LLM-judge check of quality, but run it on Kaggle or in CI, never on the Mac.
4. **Make decisions.md a light ADR log.** Keep the one-line format and add `status:` with
   accepted, superseded-by or reverted. Never delete entries. When an agent drafts one, give it the last
   3-5 entries as context (Context Matters, EASE 2026), and require a human or reviewer to check it,
   because Zhou et al. report precision of about 0.27 for LLM-generated rationale.
5. **lessons.md stays the experience memory** (AWM, ACE, ExpeL). Edit it incrementally and never rewrite
   it wholesale; ACE warns that rewrites lose detail ("context collapse"). Keep `Signals:` literal
   so lessons can be retrieved by log words.

**At runtime**
6. **Persist machine transitions.** `fsm.Machine._move` appends
   `{ts, machine, key, old, new, why, commit}` to `data/transitions.jsonl` (rotated, local only, never
   uploaded), and `export_decision_records.py` joins it. Today `history` is a 5-to-N deque in memory and is
   lost on restart. `GET /api/machines?name=` should serve `recent` from the file. About 1-2 hours.
7. **Log illegal transitions once per edge with a counter.** fsm.py already does this. Add the counter to
   the night summary so a wrong transition table shows up on the scorecard.
8. **Add an agent-action log for the coding agents.** Each session appends
   `{session, task, branch, step: localize|edit|validate, files, cmd, result, why}` to
   `docs/agent-log/<date>.jsonl`, which is small and contains no secrets. That is enough to run MAST-style
   failure tagging and to spot loops (Bouzenia & Pradel: repeated search or fix without a test).

### Suggested order

1. Make `why` required and persist transitions, (c)6 and (c)1. About 2 hours.
2. Add claims and the overlap check, (b)2 and (b)3. About 3 hours.
3. Add `citymap --focus` and `--impact`, (a)2 and (a)3. About 3 hours.
4. Add the commit `Why:` lint and the decisions `status:` field, (c)3 and (c)4. About 1 hour.
5. Generate the machines doc, (a)4. About 1 hour.

---

## 3. Annotated bibliography

### A. Codebase mapping and context retrieval

1. **Ouyang, S., Yu, W., Ma, K., Xiao, Z., et al. (2025). "RepoGraph: Enhancing AI Software Engineering with Repository-level Code Graph." ICLR 2025.** arXiv:2410.14684. https://arxiv.org/abs/2410.14684
   Builds a line-level graph of definitions and references, then gives the agent k-hop ego-graphs around
   relevant symbols. Plugged into four existing frameworks it improves SWE-bench and set a new open-source
   SOTA. *Relevance:* direct model for adding reference edges to citymap and for `--focus`.

2. **Liu, X., Lan, B., Hu, Z., Liu, Y., et al. (2024). "CodexGraph: Bridging Large Language Models and Code Repositories via Code Graph Databases."** arXiv:2408.03910 (published at NAACL 2025). https://arxiv.org/abs/2408.03910
   Stores the repo as a graph database (modules, classes, functions; contains, calls, inherits) and lets
   the agent write graph queries. Competitive on CrossCodeEval, SWE-bench and EvoCodeBench. *Relevance:*
   a queryable citymap.json is a cheap version of this.

3. **Chen, Z., Tang, X., Deng, G., Wu, F., et al. (2025). "LocAgent: Graph-Guided LLM Agents for Code Localization." ACL 2025.** arXiv:2503.09089. https://arxiv.org/abs/2503.09089
   A heterogeneous graph (files, classes, functions; imports, invokes, inherits) with search and traverse
   tools. Up to 92.7% file-level localization and +12% Pass@10 resolution; a fine-tuned 32B model
   matches proprietary models at about 86% lower cost. *Relevance:* the strongest evidence that call edges
   help localization.

4. **Yang, B., Ren, J., Jin, S., Liu, Y., et al. (2025). "Enhancing repository-level software repair via repository-aware knowledge graphs" (KGCompass).** arXiv:2503.21710. https://arxiv.org/abs/2503.21710
   A knowledge graph linking issues and PRs to code entities narrows search to the 20 most relevant
   functions. 58.3% on SWE-bench Lite at about $0.20 per repair, and 89.7% of the localized bugs needed
   multi-hop traversal. *Relevance:* linking decisions, lessons and PRs to modules makes the "why" part of
   the map.

5. **Tao, H., Zhang, Y., Tang, Z., Peng, H., et al. (2025). "Code Graph Model (CGM): A Graph-Integrated Large Language Model for Repository-Level Software Engineering Tasks."** arXiv:2505.16901. https://arxiv.org/abs/2505.16901
   Feeds the repo graph into the model through an adapter, inside an agentless graph-RAG pipeline. 43.0% on
   SWE-bench Lite with open Qwen2.5-72B, +12.33% over the previous best open-model method.
   *Relevance:* more evidence that graph structure beats flat retrieval. The training it needs is out of scope here.

6. **Bairi, R., Sonwane, A., Kanade, A., Vageesh D C, et al. (2023/2024). "CodePlan: Repository-level Coding using LLMs and Planning."** arXiv:2309.12499 (published at FSE 2024). https://arxiv.org/abs/2309.12499
   Treats repo-wide edits as planning: incremental dependency analysis and change-impact analysis decide
   the next edit. 5 of 6 repos pass validity checks vs 0 of 6 without planning. *Relevance:* basis for
   `citymap --impact`.

7. **Ma, Y., Yang, Q., Cao, R., Li, B., et al. (2024). "Alibaba LingmaAgent: Improving Automated Issue Resolution via Comprehensive Repository Exploration"** (earlier titled "How to Understand Whole Software Repository?"). arXiv:2406.01422. https://arxiv.org/abs/2406.01422
   Condenses the repo into a knowledge graph and explores it with Monte Carlo tree search. +18.5% relative
   over SWE-agent on Lite; solved 16.9% of in-house Alibaba Cloud issues automatically, 43.3% with human
   help. *Relevance:* an industrial confirmation that a whole-repo summary graph helps.

8. **Zhang, Y., Ruan, H., Fan, Z., Roychoudhury, A. (2024). "AutoCodeRover: Autonomous Program Improvement." ISSTA 2024.** arXiv:2404.05427. https://arxiv.org/abs/2404.05427
   Gives the agent AST-level search APIs (search_class, search_method_in_class) instead of raw grep. 19% on
   Lite at about $0.43 per issue. *Relevance:* scripts should expose structure-aware queries, not file dumps.

9. **Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). "Agentless: Demystifying LLM-based Software Engineering Agents."** arXiv:2407.01489 (published at FSE 2025). https://arxiv.org/abs/2407.01489
   A fixed three-phase pipeline (hierarchical localization, repair, patch validation) with no autonomous
   loop. 32.0% on Lite at $0.70, the best open-source result at that cost. *Relevance:* supports the
   localize, edit, validate rule for every agent task.

10. **Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., et al. (2024). "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering." NeurIPS 2024.** arXiv:2405.15793. https://arxiv.org/abs/2405.15793
    Shows that the interface (compact file viewer, search that caps output, lint-on-edit that rejects broken
    edits) drives agent performance: 12.5% on SWE-bench, 87.7% on HumanEvalFix. *Relevance:* our
    `scripts/` are the ACI. Keep their output short and structured, and make them fail loudly.

11. **Aider (2023-, industry documentation). "Repository map."** https://aider.chat/docs/repomap.html
    Sends a compact map of files and key symbol signatures, chosen by a graph ranking over file dependencies
    within a token budget (default 1k, `--map-tokens`). Not peer reviewed. *Relevance:* the practical
    template for `citymap --focus --tokens`.

12. **Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., Liang, P. (2023). "Lost in the Middle: How Language Models Use Long Contexts." TACL.** arXiv:2307.03172. https://arxiv.org/abs/2307.03172
    Accuracy is highest when the relevant information is at the start or end of the context and drops in
    the middle. *Relevance:* put the hard rules at the top of CLAUDE.md and keep it short.

### B. Context files for agents

13. **Chatlatanagulchai, W., Li, H., Kashiwa, Y., Reid, B., ..., Adams, B., Hassan, A. E., Iida, H. (2025). "Agent READMEs: An Empirical Study of Context Files for Agentic Coding."** arXiv:2511.12884. https://arxiv.org/abs/2511.12884
    Studies 2,303 CLAUDE.md, AGENTS.md and Copilot files from 1,925 repos. Most cover tests (75.9%),
    implementation (70.8%) and architecture (68.1%); few cover security (14.8%) or performance (14.5%).
    *Relevance:* our CLAUDE.md is typical. Its secrets rule is a strength.

14. **Gloaguen, T., Mündler-Sasahara, N., Müller, M. N., Raychev, V., Vechev, M. (2026). "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?"** arXiv:2602.11988. https://arxiv.org/abs/2602.11988
    Across agents and models, context files (written by people or by LLMs) do not generally raise task
    success and add more than 20% inference cost. Instructions are followed, but repo overviews do not help.
    *Relevance:* the main reason to keep CLAUDE.md to rules only and generate overviews on demand.

15. **Lulla, J. L., Mohsenimofidi, S., Galster, M., Zhang, J. M., Baltes, S., Treude, C. (2026). "On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents."** arXiv:2601.20404. https://arxiv.org/abs/2601.20404
    On 124 PRs across 10 repos, an AGENTS.md file brought 28.64% lower median runtime and 16.58% fewer
    output tokens with comparable completion. *Relevance:* context files do pay off in efficiency, which
    balances #14.

### C. Multi-agent coordination

16. **Hong, S., Zhuge, M., Chen, J., Zheng, X., et al. (2023/2024). "MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework."** arXiv:2308.00352 (published at ICLR 2024). https://arxiv.org/abs/2308.00352
    Encodes standard operating procedures as an assembly line of roles that pass structured documents
    (PRD, design, API spec) through a shared message pool, with intermediate verification. *Relevance:*
    handoffs should be structured artifacts with fixed fields, not free chat.

17. **Qian, C., Liu, W., Liu, H., Chen, N., et al. (2024). "ChatDev: Communicative Agents for Software Development." ACL 2024.** arXiv:2307.07924. https://arxiv.org/abs/2307.07924
    Organizes role agents into a "chat chain" of phases, with "communicative dehallucination" (the agent
    asks for details before answering). The abstract gives no numbers. *Relevance:* phase ordering is useful;
    the chat-heavy design is the costly pattern later papers warn against.

18. **Wang, X., Li, B., Song, Y., Xu, F. F., et al. (2025). "OpenHands: An Open Platform for AI Software Developers as Generalist Agents." ICLR 2025.** arXiv:2407.16741. https://arxiv.org/abs/2407.16741
    An open platform with sandboxed runtimes, an event-stream architecture (every action and observation
    is a logged event) and multi-agent delegation, evaluated on 15 benchmarks. *Relevance:* the event
    stream is the model for an agent-action log.

19. **Chen, D., Lin, S., Zeng, M., Zan, D., et al. (2024). "CodeR: Issue Resolving with Multi-Agent and Task Graphs."** arXiv:2406.01304. https://arxiv.org/abs/2406.01304
    Predefined task graphs route issues among manager, reproducer, fault localizer, editor and verifier
    agents. 28.33% on Lite with one submission. *Relevance:* an explicit plan graph beats free coordination.

20. **Arora, D., Sonwane, A., Wadhwa, N., Mehrotra, A., et al. (2024). "MASAI: Modular Architecture for Software-engineering AI Agents."** arXiv:2406.11638. https://arxiv.org/abs/2406.11638
    Splits issue resolution into sub-agents with narrow objectives (test template, issue reproduction, edit
    localization, fix, ranking) to keep trajectories short. 28.33% on Lite. *Relevance:* scope each agent
    task tightly, which also keeps claims small.

21. **Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., et al. (2025). "Why Do Multi-Agent LLM Systems Fail?"** arXiv:2503.13657. https://arxiv.org/abs/2503.13657
    MAST taxonomy: 14 failure modes in 3 groups (system design, inter-agent misalignment, task
    verification), built from 1,600+ traces over 7 frameworks with κ=0.88, plus an LLM-judge annotator.
    *Relevance:* a ready-made label set for reviewing our agent-action logs.

22. **Khatua, A., Zhu, H., Tran, P., Prabhudesai, A., Sadrieh, F., et al. (2026). "CooperBench: Why Coding Agents Cannot be Your Teammates Yet."** arXiv:2601.13295. https://arxiv.org/abs/2601.13295
    600+ tasks where two agents build features on the same repo state. Working together gives about 30% lower
    success than one agent doing both, the "curse of coordination". Causes: jammed communication, broken
    commitments, wrong beliefs about the other agent's plan. *Relevance:* the case for written claims and
    mechanical overlap checks over agent chat.

23. **Geng, J., Neubig, G. (2026). "Effective Strategies for Asynchronous Software Engineering Agents."** arXiv:2603.21489. https://arxiv.org/abs/2603.21489
    CAID: a central manager makes a dependency-aware plan, subtasks run asynchronously in isolated git
    worktrees, and results merge with test-based verification. +25.6 pts on PaperBench and +14.7 on Commit0
    over a single agent. *Relevance:* the closest match to our setup; supports (b)1, 3 and 6.

24. **Xu, G., Subramanian, A., Karthik, N. (2026). "AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates."** arXiv:2607.04697. https://arxiv.org/abs/2607.04697
    33,596 agent PRs: 40.2% of repos have agent PRs open at the same time. Replayed merges conflict 41.7%
    of the time for cross-agent pairs vs 19.8% for same-agent pairs. 84.4% of conflicted files are source
    files, and about 42% of conflicts are structural. *Relevance:* concurrent agent PRs are normal and
    conflict often; supports the hot-file fragments in (b)4.

25. **Pugachev, S. (2025). "CodeCRDT: Observation-Driven Coordination for Multi-Agent LLM Code Generation."** arXiv:2510.18893. https://arxiv.org/abs/2510.18893
    Agents watch a shared CRDT document instead of messaging each other. 100% convergence and no merge
    failures, but results range from 21.1% faster to 39.4% slower depending on the task, and 5-10% of
    edits still clash semantically. *Relevance:* a negative result for adopting it here; textual merge
    safety is not semantic safety.

26. **Hadfield, J., Zhang, B., Lien, K., Scholz, F., Fox, J., Ford, D. (2025). "How we built our multi-agent research system." Anthropic Engineering (industry report), 13 Jun 2025.** https://www.anthropic.com/engineering/multi-agent-research-system
    The orchestrator and subagents beat a single agent by 90.2% on internal research evals, at about 15x the
    tokens of chat. Subagent briefs need an objective, output format, tools and limits. Results go to
    external storage and are passed by reference. Full tracing was needed. Most coding is called a poor fit
    for multi-agent work. *Relevance:* the brief format in (b)5; parallelize only independent work.

### D. Explicit workflows, state machines, observability

27. **Wu, Y., Yue, T., Zhang, S., Wang, C., et al. (2024). "StateFlow: Enhancing LLM Task-Solving through State-Driven Workflows."** arXiv:2403.11322 (published at COLM 2024). https://arxiv.org/abs/2403.11322
    Models a task as a state machine and keeps "process grounding" (states and transitions) separate from
    the actions inside each state. +13 and +28 pts over ReAct on InterCode-SQL and ALFWorld, at 5x and 3x
    lower cost. *Relevance:* external validation of `redzone/fsm.py`. Transitions with reasons make the
    process readable.

28. **Dong, L., Lu, Q., Zhu, L. (2024). "AgentOps: Enabling Observability of LLM Agents"** (v1: "A Taxonomy of AgentOps for Enabling Observability of Foundation Model based Agents"). arXiv:2411.05285. https://arxiv.org/abs/2411.05285
    A mapping study of AgentOps tools giving a taxonomy of artifacts to trace across an agent's lifecycle:
    goals, plans, tools and guardrails, not only LLM calls. *Relevance:* a checklist of fields for the
    agent-action log and the transitions log.

29. **Bouzenia, I., Pradel, M. (2025). "Understanding Software Engineering Agents: A Study of Thought-Action-Result Trajectories." ASE 2025.** arXiv:2506.18824. https://arxiv.org/abs/2506.18824
    Annotates RepairAgent, AutoCodeRover and OpenHands traces as thought, action and result. Successful
    runs keep thought and action aligned and balance exploring, fixing and testing. Failures loop on search
    or fix without testing. *Relevance:* log `why` next to each action and flag loops with no tests.

30. **Majgaonkar, O., Fei, Z., Li, X., Sarro, F., Ye, H. (2025). "Understanding Code Agent Behaviour: An Empirical Study of Success and Failure Trajectories."** arXiv:2511.00197. https://arxiv.org/abs/2511.00197
    Failed trajectories are longer and vary more. Agents find the right files 72-81% of the time even when
    they fail, so failures are often in the edit and validate steps. *Relevance:* after localization,
    effort should go to validation (scorecards and tests), not more search.

### E. Memory, experience and rationale

31. **Wang, Z. Z., Mao, J., Fried, D., Neubig, G. (2024). "Agent Workflow Memory."** arXiv:2409.07429 (published at ICML 2025). https://arxiv.org/abs/2409.07429
    Induces reusable workflows from past trajectories and injects the relevant ones later. +24.6% and +51.1%
    relative on Mind2Web and WebArena, with fewer steps. *Relevance:* runbooks and playbooks are workflow
    memory; derive new ones from successful agent logs.

32. **Zhang, Q., Hu, C., Upasani, S., Ma, B., et al. (2025). "Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models." ICLR 2026.** arXiv:2510.04618. https://arxiv.org/abs/2510.04618
    Treats context as an evolving playbook, updated through generate, reflect and curate steps with
    incremental, itemized edits. +10.6% on agent benchmarks. Names "brevity bias" and "context collapse"
    from wholesale rewrites. *Relevance:* lessons.md and CLAUDE.md should get small additions, never
    LLM rewrites of the whole file.

33. **Zhao, A., Huang, D., Xu, Q., Lin, M., Liu, Y.-J., Huang, G. (2024). "ExpeL: LLM Agents Are Experiential Learners." AAAI 2024.** arXiv:2308.10144. https://arxiv.org/abs/2308.10144
    The agent collects experiences, extracts natural-language insights and recalls them at inference, with
    no weight updates. Performance rises as experience accumulates. *Relevance:* same mechanism as
    lessons.md with `Signals:` retrieval.

34. **Shinn, N., Cassano, F., Berman, E., Gopinath, A., et al. (2023). "Reflexion: Language Agents with Verbal Reinforcement Learning."** arXiv:2303.11366 (published at NeurIPS 2023). https://arxiv.org/abs/2303.11366
    The agent writes verbal self-reflections after failures into an episodic buffer. 91% pass@1 on
    HumanEval vs 80% for GPT-4. *Relevance:* foundation for post-fix lessons; the reflection must be
    written down, not kept only in context.

35. **Nygard, M. (2011). "Documenting Architecture Decisions." Cognitect blog, 15 Nov 2011 (classic, not peer reviewed).** https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions
    The original ADR format: title, context, decision ("We will..."), status (proposed, accepted, deprecated
    or superseded) and consequences, including negative ones. Short, numbered, and old ones are kept and
    marked superseded. *Relevance:* add status and supersede links to decisions.md.

36. **Dhar, R., Vaidhyanathan, K., Varma, V. (2024). "Can LLMs Generate Architectural Design Decisions? An Exploratory Empirical Study." ICSA 2024.** arXiv:2403.01709. https://arxiv.org/abs/2403.01709
    GPT-4 zero-shot writes relevant ADR decisions but below human level; GPT-3.5 few-shot and fine-tuned
    Flan-T5 come close. *Relevance:* agents can draft decision lines, and a human should check them.

37. **Dhar, R., Kakran, A., Karan, A., Vaidhyanathan, K., Varma, V. (2025). "DRAFT-ing Architectural Design Decisions using LLMs."** arXiv:2504.08207. https://arxiv.org/abs/2504.08207
    Few-shot prompting, retrieval of similar past ADRs, and fine-tuning each improve generation; evaluated
    on 4,911 ADRs. *Relevance:* retrieve similar past decisions when drafting a new one.

38. **Gupta, A., Dhar, R., Feitosa, D., Vaidhyanathan, K. (2026). "Context Matters: Evaluating Context Strategies for Automated ADR Generation Using LLMs." EASE 2026.** arXiv:2604.03826. https://arxiv.org/abs/2604.03826
    Across 750 repos, the last 3-5 prior ADRs work best as context. Retrieval helps only for cross-cutting
    decisions. *Relevance:* when an agent drafts a decisions.md line, give it the last 3-5 entries in that
    section.

39. **Zhou, X., Li, R., Liang, P., Zhang, B., Shahin, M., Li, Z., Yang, C. (2025). "Using LLMs in Generating Design Rationale for Software Architecture Decisions." TOSEM (accepted).** arXiv:2504.20781. https://arxiv.org/abs/2504.20781
    Five LLMs on 100 architecture problems: rationale precision 0.27-0.28, recall 0.63-0.72. 64-69% of
    arguments the experts did not raise were still helpful, but 1.6-3.2% may mislead. *Relevance:*
    LLM-written "why" text needs review; it tends to over-generate.

40. **Dhaouadi, M., Oakes, B., Famelis, M. (2025). "Automated Extraction and Analysis of Developer's Rationale in Open Source Software."** arXiv:2506.11005. https://arxiv.org/abs/2506.11005
    Extracts decision and rationale sentences from developer text (on Kantara) into a graph and detects
    reasoning conflicts and design erosion over time. Tested on the Linux OOM-killer and 5 other projects.
    *Relevance:* a later step could link decisions.md lines to the code they govern and flag drift.

41. **Tian, Y., Zhang, Y., Stol, K.-J., Jiang, L., Liu, H. (2022). "What Makes a Good Commit Message?" ICSE 2022.** arXiv:2202.02974. https://arxiv.org/abs/2202.02974
    Finds from about 1,600 messages that good messages summarize what changed and why. About 44% of
    messages could be improved (missing the what, the why, or both). *Relevance:* basis for the `Why:` line in
    commits.

42. **Zeng, Q., Zhang, Y., Ma, Z., Jiang, B., Sun, N., Stol, K.-J., Mou, X., Liu, H. (2025). "Evaluating Generated Commit Messages with Large Language Models."** arXiv:2507.10906. https://arxiv.org/abs/2507.10906
    BLEU and ROUGE are poor judges of commit messages. An LLM with CoT and few-shot examples reaches
    near-human agreement. *Relevance:* a cheap CI judge for "does this commit explain why" is feasible.

---

*Not cited, though seen in search results: arXiv:2606.19616 (parallel-agent duplicate work), mentioned only
by a blog. Not verified.*
