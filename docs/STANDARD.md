# Organization standard

How every document, report and finding in this repo is organized. Research outputs follow it from 2026-11-08;
the rest of the repo moves to it as files are touched (and in one back-fill pass per area, tracked in
docs/PLAN.md). Checked in CI by `python scripts/doc_index.py --check`.

## 1. Where things go

| What | Where | Who writes |
|---|---|---|
| The plan (only one) | `docs/PLAN.md` | coordinator; planners only in their section |
| Live TV test queue | `docs/test-queue.md` | coordinator |
| One research or audit report | `docs/agents/<stream>/<YYYY-MM-DD>-<slug>.md` | the agent that ran it |
| A standing stream's shared log | `docs/agents/<stream>/log.md` (append-only) | that stream's agents |
| The dataset database | `docs/agents/data/catalog.md` (append-only table) | data agents |
| Index of all agent reports | `docs/agents/INDEX.md` (generated, never hand-edited) | `scripts/doc_index.py` |
| Decisions and lessons | `docs/memory/decisions.md`, `lessons.md` (fragments: `decisions.d/`) | as today |
| Older research (before the standard) | stays where it is, listed in `docs/research/README.md` | — |

Streams: `process`, `rules`, `context`, `locked-results`, `harness`, `training`, `data`, `audit`, `planner`.
Slugs are short, lowercase, hyphenated. One topic per report; a follow-up is a new dated file that names what it
supersedes, never an edit that rewrites history.

## 2. Every report starts with this front matter

```yaml
---
id: W1-RULES                 # stream wave/short code; unique; findings inside are W1-RULES-01, -02, ...
title: Debugging, validation and merge rules for coding agents
date: 2026-11-08
stream: rules
author: wave-1 rules agent   # which agent or person
status: draft                # draft -> reviewed -> adopted (in PLAN) | rejected | superseded
supersedes: []               # ids this replaces
sources: 28                  # bibliography entries
verified: 24                 # of those, opened and read (the rest are marked snippet-only)
affects: [.claude/settings.json, scripts/, .github/workflows/ci.yml]
gate: [W1-RULES-01, W1-RULES-02]   # findings marked WORTH FIXING (copied to PLAN section 2)
---
```

Then, in this order: **Summary** (5 lines max) - **Findings** (each with its id, evidence, files affected,
WORTH FIXING yes/no) - **What to change in this repo** - **Risks / unverified** - **Bibliography** (each entry: title,
author, date, link, opened or snippet-only).

## 3. Findings and the process gate

- Every finding has a stable id (`W1-RULES-03`). The plan, commits and PRs cite findings by id.
- `WORTH FIXING: yes` copies the finding to docs/PLAN.md section 2. Product work pauses until it is fixed AND
  back-filled across the existing code/docs; then the report's `status` becomes `adopted` and the PLAN row is closed
  with the commit that closed it.
- A finding nobody can verify stays `draft`; it never drives a change on its own.

## 4. Append-only logs

Standing streams append; they never rewrite or reorder. Entry header: `### YYYY-MM-DD <stream>-NN: title`, then
finding, evidence, files, WORTH FIXING. The coordinator marks reviewed entries in the PLAN, not in the log.

## 5. Code (applies as code is touched)

- Every module starts with a docstring whose first line says what it is, then `Why:` (the reason it exists, with a
  decision or finding id when there is one). CI will check this once the back-fill pass is done (PLAN section 2).
- What it routes to and what relies on it come from the generated map (`scripts/citymap.py --focus/--impact`),
  never hand-written lists that go stale.
- Results and test data follow the locked-results design (docs/agents/locked-results/) once adopted.
