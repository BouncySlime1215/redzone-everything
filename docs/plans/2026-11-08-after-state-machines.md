# After the state machines: plan checked against the research (2026-11-08)

Done before this plan: inventory of implicit state machines, `redzone/fsm.py` + tests, every machine
converted (52 specs in `redzone/machines/`, `GET /api/machines`, `docs/state-machines.md` generated and
checked in CI).

Sources: [R-A] [agent-coordination-and-rationale.md](../research/agent-coordination-and-rationale.md)
(42 sources), [R-B] [opus-5-5-harness-and-time.md](../research/opus-5-5-harness-and-time.md) (43 sources).

## Did the research agree with the original next steps?

| Original next step | Research verdict | Change |
|---|---|---|
| Review the conversion, run tests + sim | Agrees: "merge only through a test gate" (R-A b6, CAID/MAST). | Add: make `why` required in `Machine.to()` and persist transitions to `data/transitions.jsonl` (R-A c1, c6). It's part of finishing fsm, so it goes here. |
| Map the whole codebase vs the North Star | Agrees on the goal, **disagrees on the form**. A prose map goes stale and costs tokens. Graph maps with multi-hop edges find the right file far more often (LocAgent 92.7%, KGCompass 89.7% multi-hop) (R-A a1-a3). | Extend the existing `scripts/citymap.py` with `calls`, `reads`/`writes` (data files) and `machine` edges, plus `--focus <file> --tokens N` and `--impact <file>`. The map is generated and CI-checked, then used for the review. |
| Find errors and rules that conflict with the goal | Agrees, with a fixed localize → verify sequence (Agentless; R-A technique 3) and cheap models for reading (R-B Q2: Explore on Opus costs 40x Haiku on input). | Reviewers work per subsystem from `citymap --focus`, on Sonnet. Every finding cites file:line and a North Star line, and is checked against the code before it's reported. |
| Coordination rules + how systems connect | Agrees. The strongest evidence: isolated worktrees, one planner, test-gated merge; no chat coordination (CAID +25.6/+14.7 pts; CooperBench −30% for chatting agents; 41.7% PR conflict rate). | Claims file + CI overlap check, impact-based scheduling, per-PR decision fragments, handoffs as contracts (objective, output, tools, limits). CLAUDE.md trimmed to rules (<200 lines, hard rules in the first 20). |
| Records of why | Agrees and adds runtime pieces. | Required `why` codes, `data/transitions.jsonl`, a why-not ledger with git SHA + `scripts/why.py <time>`, commit `Why:` lint, `status:` on decisions.md, a small agent-action log. |
| Plan error fixes + next steps | Agrees. R-B says the biggest time lever by far is testing without a live game. | Immediate: first real jumps (Prime delay sync, held offsets). Long term: the "RedZone time machine" (clock seam → stick tape → stick/TV twin → whole-night replay gate in `deploy.py --dry-run` → candidate-in-shadow). |
| (new) Harness hygiene | R-B top 10. | Project `Explore` agent on Haiku, Monitor for deploy watches, a PreToolUse hook that blocks adb to the sticks while a game is on screen, a SessionStart usage-pause line, a pytest-output trim hook. Global settings (push, auto mode) are Nick's call; proposed, not changed. |

Nothing in the original plan was contradicted; two steps changed form (map → generated graph,
review → localize/verify on cheaper models) and three were added (required why + transitions log,
harness hygiene, time machine as the top long-term item).

## Final order

1. Verify the conversion: full suite vs the pre-change baseline, sim, required `why`, transitions log.
2. Graph map: citymap `calls`/`data`/`machine` edges, `--focus`, `--impact`, CI check.
3. Review every subsystem against the North Star from the map; list errors and conflicting rules.
4. Coordination rules: claims + overlap check, impact scheduling, decision fragments, handoff contract, trimmed CLAUDE.md.
5. Why-records: why-not ledger + `scripts/why.py`, commit `Why:` lint, decisions `status:`, agent-action log.
6. Harness hygiene (project-level only).
7. Fix plan for every error + next steps (immediate and long term).
8. Commit, push, PR to `integration/next`. The app stays off throughout.
