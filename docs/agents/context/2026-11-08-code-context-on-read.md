---
id: W1-CONTEXT
title: Why / routes / relied-on context whenever an agent reads code
date: 2026-11-08
stream: context
author: wave-1 context agent
status: draft
supersedes: []
sources: 24
verified: 20
affects: [.claude/settings.json, .claude/hooks/, scripts/citymap.py, redzone/ module docstrings]
gate: []
---
# Code context on read: why this file exists, where it routes, what relies on it

Date: 2026-11-08. Scope: research + design only (no product code changed). Author: research agent, wave 1.
Nick's requirement: "Each time an AI views code it should understand why that code exists, where it routes to,
and what relies on it", plus "any other major questions I'm missing or steps that need to be taken."

Source status key used below: **[opened]** = I fetched and read the page (or its abstract page); **[snippet]** =
seen only in a search-result snippet, not opened (treat as unverified); **[repo]** = measured in this repo today.

## 0. Answer in five lines

1. The mechanism exists and is documented: a `PostToolUse` hook matched on `Read` can return
   `hookSpecificOutput.additionalContext`; Claude Code wraps it in a system reminder "next to the tool result".
   Hard cap 10,000 characters per hook output. [opened: hooks reference]
2. RedZone already has the data: `docs/citymap.json` (377 modules, with importers, callees, data files, machines,
   owner, north-star tags), `scripts/citymap.py --impact`, `scripts/why.py`. A prototype card for
   `redzone/control.py` built from them is 10 lines / 1,184 chars / 0.07 s [repo, section 4].
3. The gap is not code facts, it is the **Why**. [repo] 362 of 377 modules have a docstring (purpose is nearly
   covered) but only 7 of 211 `redzone/` docstrings contain "Why", 0 of 377 first-add commits have a `Why:`
   line (control.py's first commit is "RedZone Everything: core engine..."), and only 6 of the last 400 commit
   bodies have one. Rationale lives in `docs/memory/decisions.md` (1,477 lines) and `lessons.md`, found only by
   text match. So: write `Why:` headers, and make the card show the newest 2 matching decision lines.
4. Keep it small: the one controlled study of agent context files found repository overviews did not help,
   and context files raised cost over 20% on average. So the card carries only facts the agent cannot get
   cheaply from the file itself (reasons, dependents, covering tests, invariants), at most 15 lines, once per
   file per session. Measure it (section 4.9) before keeping it on.
5. Do now, under 2 minutes: ask Nick to approve adding the hook entry from section 4.2 to `.claude/settings.json`
   (guard G6 protects `.claude/**`, so an agent cannot add it).

## 1. Findings, ranked by evidence

**F1. Hooks can inject text at the moment of a Read; this is documented and the size is capped.** [opened]
- Hooks reference: `additionalContext` inside `hookSpecificOutput` "is wrapped in a system reminder and inserted
  into the conversation at the point where the hook fired", placed "next to the tool result" for PreToolUse,
  PostToolUse, PostToolUseFailure, PostToolBatch; several hooks' values are all delivered.
- Cap: `additionalContext`, `systemMessage` and plain stdout are capped at 10,000 characters; over the cap the
  output is written to a file and replaced by a path plus a 2,000-character preview, and "Claude Code doesn't ask
  Claude to read the file". So a card must sit far under the cap (target <= 1,800 chars).
- PostToolUse exit 2 cannot block (tool already ran); other non-zero exits are non-blocking errors, so a crashing
  card hook cannot stop work. Default command-hook timeout is 600 s, so set our own (3 s).
- `PostToolUse` plain stdout on exit 0 is not shown to the model; only the JSON field is. Use JSON.
- PreToolUse can also return `additionalContext` (added in v2.1.9 per issue #18427's bot comment). PostToolUse
  is preferable for Read because the card then sits beside the file text and failed reads get no card.
- Caveat on reliability: issue #18427 ("PostToolUse hooks cannot inject context visible to Claude", Jan 2026,
  closed "not planned" by a bot on Feb 28 2026) records that for built-in tools the injected text did not appear
  in the UI or transcript on v2.1.22, and the reporter could not tell whether the model received it. Issue #43750
  (Apr 5 2026, v2.1.92) states additionalContext works for Read/Edit/Write/Glob/Grep, but that issue was closed
  by its author as their own bug, so it is weak evidence, not a maintainer confirmation. [opened both]
  **Action:** ship with a canary (section 4.9, test T1): the card contains a nonce word; a test session is asked
  to quote it. Do not trust the doc alone.

**F2. Giving agents caller/callee structure measurably helps code tasks in research settings.** [opened abstracts]
- RepoScope (arXiv 2507.14791, ICSE 2026): repository structural graph, callers and likely callees of the target
  as context, up to 36.35% relative pass@1 gain on CoderEval/DevEval, static analysis only, no extra LLM calls.
- InlineCoder (arXiv 2601.00376, FSE 2026): "upstream" = place the function inside its callers to show how it is
  used; "downstream" = add callees. Same caller/callee framing. No numbers on the abstract page.
- RepoGraph (arXiv 2410.14684, ICLR 2025): plug-in repo-level graph improved all four SWE-bench systems tested.
- DualView (arXiv 2607.01929, Jul 2026): four views (module coupling, call graph, class hierarchy, program
  dependence) for issue-resolution agents; ablations credit part of the gain to presenting dependencies as
  visuals, not just text. Abstract has no numbers.
- LocAgent (arXiv 2503.09089): graph of files/classes/functions linked by imports, calls, inheritance; reported
  92.7% file-level localization with a fine-tuned 32B model. [snippet only]
  Takeaway: callers + callees + importers is the evidence-backed minimum; our citymap already stores it.

**F3. More context is not better; unconditional repo overviews can hurt.** [opened]
- "Evaluating AGENTS.md" (arXiv 2602.11988, Feb 2026, rev. v3 Sep 29 2026): context files did not generally
  improve task success; inference cost rose over 20% on average; agents obey the instructions well, but
  repository overviews did not help; human-written files beat LLM-generated ones; recommended minimal files
  limited to non-standard practice, rigorously evaluated before deployment. (No success-rate figure was visible
  on the abstract page; I did not open the PDF.)
- Anthropic, "Effective context engineering" (Sep 29 2025): "the smallest possible set of high-signal tokens";
  just-in-time loading of lightweight references; progressive disclosure; recall degrades as context grows.
- Chroma "Context Rot" (Jul 14 2025): 18 models, performance gets less reliable as input grows even on simple
  tasks.
  Implication for design: a card is justified only for facts that are (a) not inferable from the file, (b) cheap,
  (c) decision-relevant. "Why" and "what depends on it" qualify; a summary of what the code does does not.

**F4. Claude Code already has a lazy, path-scoped delivery mechanism, but it is not generated.** [opened: memory docs]
- Subdirectory `CLAUDE.md` loads once when Claude reads/writes/edits a file there (including `cat`/`head` of one
  file); `.claude/rules/*.md` with `paths:` frontmatter load when matching files are touched; docs target under 200
  lines per CLAUDE.md and warn that long files reduce adherence. `InstructionsLoaded` hook logs lazy loads.
  Use it for human-written rules per directory (for example `redzone/decider/` Rule #1), and use the hook for
  generated per-file facts. They complement each other.

**F5. Rationale needs a home that agents can find, and commit messages are the cheapest.** [opened]
- Nygard's ADR post (Nov 15 2011): without recorded rationale newcomers "blindly accept or blindly reverse"
  past decisions; one-to-two-page records with context, decision, status, consequences.
- Lore (arXiv 2603.15566, Mar 2026): commit messages lose the reasoning ("Decision Shadow"); proposes git trailers
  for constraints, rejected alternatives, agent directives and verification. No empirical results yet (author
  says validation is future work). Qodo's glossary cites a preprint with ~10% time and tool-call savings from
  rationale-rich records; **[snippet only, unverified]**.
- Fits RedZone: decisions.md already records reason per decision (format `YYYY-MM-DD: **decision.** Why.`), and
  recent merge commits already carry `Why:`. What is missing is the file -> decision link.

**F6. Off-the-shelf code-graph tools exist but are query tools, not read-time injectors.** [opened]
- Aider repo map: tree-sitter, files as graph nodes, ranking, default 1k-token budget (`--map-tokens`). Shows
  signatures of the most referenced symbols only. Shows the "budget + rank, drop the rest" pattern.
- CodeGraph (MIT, May 29 2026 post): local SQLite code graph served over MCP; vendor benchmarks claim 71% fewer tool
  calls, 57% fewer tokens (7 repos, v0.9.4). Vendor-reported, one source; and the post notes gains depend on
  the primary agent querying the graph instead of reading raw files.
- I found **no** published open-source hook that injects dependents on Read (two searches; negative result,
  not proof). So the design in section 4 is original assembly, not a copy of a proven recipe. That is the
  reason for the canary and A/B test.

**F7. "Understand before editing" is an old, documented norm.** Google's review guide asks reviewers whether tests
exist and would fail if the code broke, whether comments explain why, whether docs were updated, and whether the
change fits the whole system [opened]. Chesterton's-fence write-ups point at git blame/log as the provenance tool
[snippet only, secondary blogs]. See the checklist in section 2.

## 2. Questions an agent should answer before editing a file

Each has: question, where RedZone answers it today, the hook tier that surfaces it, and its source.
Tier R = in the Read card (every first read). Tier E = in the edit card (first Edit/Write of that file this session).
Tier G = a gate/CI, not context.

| # | Question | RedZone source today | Tier | Documented basis |
|---|---|---|---|---|
| Q1 | Why does it exist? Which decision/lesson created or shaped it? | module docstring + `Why:`; `scripts/why.py <file>` over decisions/lessons | R | Nygard ADR; Lore; Google "comments explain why" |
| Q2 | Where does it route to (callees, data files, state machines it drives)? | citymap `inputs`, `calls`, `data`, `machines` | R | RepoScope, InlineCoder, LocAgent, RepoGraph |
| Q3 | What relies on it (importers, transitive depth, contracts)? | `citymap --impact` | R (top 6) / E (full counts) | same as Q2 |
| Q4 | Which tests cover it, and which must I run? | `--impact` lists direct tests (41 for control.py); name match `tests/test_<stem>*.py` | E | Google: tests exist and fail if code breaks; Anthropic best practices: give Claude a check to run. Line-level "who covers this line" is possible with coverage.py `dynamic_context = test_function` [opened] but costs a full run; keep as a nightly artifact, not a hook |
| Q5 | Which invariants does it touch? Rule #1 (never join mid-play), leave rule, 0 mid-play entries, G1-G7 guard rules, protected files | CLAUDE.md; `docs/citymap_tags.json` (north-star metrics per path); `.claude/hooks/guard.py` PROTECTED list | R (tags) / E (protected + gate list) | Anthropic best practices: enforce rules that can't have exceptions via hooks, tell Claude what it can't see |
| Q6 | Who owns it? Who else is editing it now (at most 5 threads)? | citymap `owner` (e.g. SWITCHING for control.py); docs/architecture.md file ownership | R (owner) / E (check open PR touching it via `gh pr list --search`) | GitHub CODEOWNERS semantics (owner reviewed on change) [opened]; best practices say Claude uses `gh` for the issue tracker/PRs |
| Q7 | What changed recently and why? | `git log -3 -- file` subjects + `Why:` body lines | R | Chesterton's fence (git log/blame) [snippet]; Lore |
| Q8 | What does it do at runtime? Evidence from the last live night | `why.py --machine <m>`, `--night`; `data/transitions.jsonl` rows carry the running `sha` | E (one line pointing at the command, not the data) | repo: transitions carry commit sha |
| Q9 | Is there an open issue/PR/known-bug note on it? | `gh issue list --search <stem>`; lessons.md match | E (agent runs on demand; hook never calls the network) | best-practices page: CLI like `gh` is the easiest way in |
| Q10 | Is the change gated (flag default OFF, gate in architecture.md section 7)? Does a default change need a decisions.md line? | `default_config.yaml`, `scripts/check_memory.py` (CI enforces) | G | repo CLAUDE.md |
| Q11 | What is my success check before I say done? | Rule #1 table: entries_mid_play, plays_missed_entry, dead_air_pct before/after; pytest; ruff | E (one line) | Anthropic best practices ("give Claude a way to verify"; `/code-review` fresh-context review) |
| Q12 | Is this file generated or protected (do not hand-edit)? | guard.py G6; "generated by scripts/..." headers | E | guard.py (repo) |

Missing from most agents' habits, and cheap to add: Q4 (name the test to run before editing), Q5 (state which
invariant applies), Q11 (state the before/after metric). These three are the ones I would force with the edit
card, because they change what the agent does, not just what it knows.

## 3. Major questions you may be missing (beyond "what is this file")

1. **Does the card itself help?** No published evidence exists for read-time injection (F6). Until an A/B on
   RedZone tasks shows fewer regressions or fewer tool calls, ship it behind a switch (`data/.ctx_cards_off`
   file, or env var) and treat the result as a measurement, consistent with the repo's "misses its gate ships OFF".
2. **Staleness.** `docs/citymap.json` is generated; a card built from an old map lies confidently. Card shows
   the map's git short-sha / age; the hook refuses to render dependents if the map is older than the file's mtime
   by more than a commit (fall back to "map stale: run python scripts/citymap.py").
3. **Trust and injection.** Card text is built from repo files (docstrings, commit bodies, decisions). Treat as
   data: strip control characters and any `<system-reminder>` tag (Claude Code already escapes `<` in such tags),
   and never include secrets files (guard G1 applies to Read; the hook must skip any path guard.py denies).
4. **Does the agent see the card at all after compaction?** Cards are conversation content and are summarized away
   at compaction. Reset the per-session "already carded" set on `SessionStart` with matcher `compact` (the hooks
   guide uses this matcher to re-inject critical context) so the next read re-cards.
5. **Subagents.** Cards fire per tool call in the session that runs the Read, including in subagents (I did not
   verify this; test T4). If an exploring subagent reads 40 files, that is 40 cards in its context; the once-per-
   session set must be keyed by `session_id` + `agent_id` if the input carries it (not verified in docs I opened).
6. **Who updates the Why?** A header Why that is not maintained is worse than none. Make `Why:` a required
   line in the PR template for any PR that adds a module, and CI-check presence (section 5). Also adopt a
   `Why:` commit-body line going forward (6 of the last 400 commit bodies have one today).
7. **Rule #1 is a metric, not a file.** The card shows tags, but the true gate is the scorecard
   (`scripts/ci_scorecard.py`); the edit card names the command, not a promise.
8. **Cost accounting.** `/usage` breaks usage down by skill, subagent, plugin, MCP server [opened]; hook-injected
   text lands in the main context and cache. Track average cards per session and tokens per card; log them.

## 4. Concrete design for this repo

### 4.1 Files

| Path | Purpose |
|---|---|
| `.claude/hooks/code_card.py` | the hook (stdlib only; one file; fails open) |
| `.claude/hooks/card_reset.py` | SessionStart `compact` hook: clears the seen-set |
| `.claude/settings.json` | register both hooks (Nick applies; protected by G6) |
| `scripts/check_headers.py` | CI check: purpose + Why header on every module, with ratchet baseline |
| `docs/header_baseline.json` | modules allowed to lack a Why today; count may only go down |
| `scripts/draft_why.py` | one-off backfill drafter (section 6) |
| `tests/test_code_card.py` | unit tests: format, size cap, failure modes, dedup |
| `$TMPDIR/rz_cards/<session_id>.json` | per-session seen-set (not in repo; fallback `/tmp` is acceptable for this file only) |

### 4.2 Hook config (add to `.claude/settings.json`; keep guard.py's existing entries)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/code_card.py\" read",
            "timeout": 3
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/code_card.py\" edit",
            "timeout": 3
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/card_reset.py\"",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

Notes: (a) JSON arrays merge with the existing PreToolUse guard entry; the new entry is a separate object, so the guard
still runs and a guard deny wins ("precedence deny > defer > ask > allow", hooks guide [opened]). (b) Only
`additionalContext` is returned, never a `permissionDecision`, so the card can never block or approve a tool call.
(c) The hook gets `tool_input.file_path`, `session_id`, `cwd` on stdin as JSON (documented fields: `session_id`,
`cwd`, `tool_name`, `tool_input`, `tool_response`, `tool_use_id`).

### 4.3 When the card fires

- Read card (PostToolUse, `Read`): only for `*.py` under `redzone/`, `scripts/`, `agent/` that exist in
  `docs/citymap.json`; skipped for `tests/`, `docs/`, data, any path the guard secret pattern matches, files under
  20 lines, and when `tool_input.offset`/`limit` is set AND the file was already carded (the seen-set handles it).
- Edit card (PreToolUse, `Edit|Write|MultiEdit`): first edit of a file per session, max 8 lines, no repeat of the Read
  card. Returned as `additionalContext` with no `permissionDecision`.
- Each file at most once per session per card type. After `compact`, both sets reset.

### 4.4 Card format (Read card) - exact spec

Plain text, no markdown, no decorative characters, `\n`-separated, <= 15 lines, <= 1,800 characters (about 450
tokens at the usual ~4 chars/token), each line <= 200 characters (longer lines clipped with `...`).
First line always starts `CONTEXT `. Every line begins with a fixed 8-character label so the model and humans can scan it.

```
CONTEXT <path> | owner <OWNER> | <purpose: docstring first sentence, <=150 chars>
WHY     <header "Why:" line, <=150 chars; else: "no Why: header (add one)">
DECIDED <date> <newest decisions/lessons line mentioning this file, <=140 chars>      (max 2 lines)
USED-BY <N> direct importers: <top 6 by path, disambiguated from the root, then "+k more">
USES    <direct imports, <=8, shortest unambiguous path>
DATA    r:<files> w:<files> u:<files>
MACHINE <machines driven/declared>
TESTS   <N> direct; run: pytest <glob of name-matched tests>
RULES   <north-star tags from docs/citymap_tags.json, <=4> | protected: yes/no
GIT     <sha> <subject> | Why: <body Why line, <=100 chars>                              (max 3 lines)
MORE    python scripts/citymap.py --impact <path>  |  python scripts/why.py <path>
MAP     citymap <sha> <age>   (only when older than 1 day)
```

Budget rule: lines are emitted in the order above; if the total passes 1,800 chars, drop from the bottom up
(MAP, GIT beyond 1, DECIDED beyond 1, USES, USED-BY tail), never the CONTEXT/WHY/RULES/MORE lines.
Lines with no data are omitted (no "none" filler).

### 4.5 Example for `redzone/control.py`

Built from the real `citymap --impact redzone/control.py` output and the real prototype run today (the prototype
at the bottom of this section produced lines 1, 3-6 and 8-10 verbatim in substance; the TESTS, RULES and DECIDED
lines are composed from the same real citymap/why output: 41 direct tests, the north-star tags in
`docs/citymap_tags.json`, and the 2026-11-07 decisions). Roughly 1,500 characters, 12 lines:

```
CONTEXT redzone/control.py | owner SWITCHING | Turns decisions into TV actions. Dry-run by default.
WHY     no Why: header (add one); header says: hidden stick is ALWAYS the one tuned; flip only at a safe moment
DECIDED 2026-11-08 buffered_jump (default OFF): both sticks scrubbed ~75 s behind live, hidden stick pauses before next snap
DECIDED 2026-11-07 EXP-603/604 FAIL; RC8 stays OFF (mid-play entries up; switches 1.72x)
USED-BY 9 direct (27 transitive): autonomy.py, demo/engine.py, engine.py, lab/night/run.py, supervisor.py, scripts/rz.py +3
USES    channels, config, contracts/switching, fallback_ladder, fsm, machines/control, tuners/{adb,join_watchdog,session_guard,tuners,...}, tv
DATA    r:data/devices.json u:held.json u:joins.jsonl
MACHINE control.{adopt,cec,command,offset,preload_slot,screen,standby,two_stick,visible}
TESTS   41 direct; run: pytest tests/test_control*.py tests/test_hot_standby.py tests/test_switch_lifecycle.py
RULES   Flips PLAYING within 3 s | Phone press to TV p95 | SAFE-08 | STK-01 | protected: no
GIT     528f5cc9 Merge integration/next into state-machines-coordination | Why: PR #423 must land on integration/next ...
GIT     5d7ab818 Explicit state machines everywhere | Why: lifecycle state spread over many flags that several tasks wrote
MORE    python scripts/citymap.py --impact redzone/control.py  |  python scripts/why.py redzone/control.py
```

The prototype's real output for the first-class lines (verbatim from running `card.py` against the worktree):
`USED-BY (9 direct): autonomy.py, engine.py, engine.py, run.py, ...` shows a real defect to fix: bare basenames
collide (`engine.py` twice). The spec above uses paths relative to `redzone/` for that reason.

Also real: the prototype's `WHY` line printed "no `Why:` in header" for control.py and cuts.py: the very
modules at the heart of Rule #1 have no Why header today. That is the backfill's first target.

Reference prototype (tested: control.py -> 1,184 chars, 10 lines, 0.07 s; cuts.py -> 785 chars, 0.125 s). It lives
at `<scratchpad>/card.py` for this research run and is reproduced here as the starting point for implementation:

```python
#!/usr/bin/env python3
"""PostToolUse/PreToolUse context card. Stdlib only. Fails open: any error -> print nothing, exit 0."""
import ast, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
MAXL, MAXC = 15, 1800

def clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "..."

def card(rel):
    g = json.loads((ROOT / "docs/citymap.json").read_text())
    m = g["modules"].get(rel)
    if not m:
        return None
    doc = ast.get_docstring(ast.parse((ROOT / rel).read_text())) or ""
    purpose = clip(doc.split("\n\n")[0], 150) if doc else "NO PURPOSE HEADER"
    why = next((clip(l.split("Why:", 1)[1], 150) for l in doc.splitlines() if "Why:" in l), None)
    imp = m["outputs"]            # citymap 'outputs' = modules that import this one
    L = [f"CONTEXT {rel} | owner {m.get('owner') or '-'} | {purpose}",
         "WHY     " + (why or "no Why: header (add one)"),
         f"USED-BY {len(imp)} direct: " + clip(", ".join(imp[:6]) + (" ..." if len(imp) > 6 else ""), 200)]
    # ... USES, DATA, MACHINE, TESTS, RULES, GIT, MORE as in section 4.4 ...
    return "\n".join(L)

def main():
    mode = sys.argv[1]
    ev = json.load(sys.stdin)
    rel = os.path.relpath(ev["tool_input"]["file_path"], ROOT)
    # skip rules, seen-set (section 4.6), then:
    text = card(rel)
    if text:
        name = "PostToolUse" if mode == "read" else "PreToolUse"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name,
                                                 "additionalContext": text[:MAXC]}}))
try:
    main()
except Exception:
    pass          # fail open; optionally append one line to data/ctx_card_errors.log
sys.exit(0)
```

### 4.6 Caching

- **Citymap**: parse `docs/citymap.json` (343 KB) per call; the whole prototype (parse + git) measured 0.07-0.13 s, so no daemon needed. If profiling shows it
  matters, cache a reduced index (`path -> small dict`, about 60 KB) at `$TMPDIR/rz_cards/index-<mtime>.json`.
- **Seen-set**: `$TMPDIR/rz_cards/<session_id>.json`, a JSON list of `"read:<path>"` / `"edit:<path>"`. Written
  atomically (write temp + rename). Reset by `card_reset.py` on SessionStart `compact`. Entries older than 2 days are
  deleted on write.
- **Decisions lines**: compute with `scripts/why.py` internals (`mentions()`), cap input at 1,477-line files read once
  per call (measured: `why.py control.py` runs in 0.09 s wall); no cache needed.
- **Git**: one `git log -3 -- <file>` with `timeout=2`; skip on timeout (git on the 8 GB Mac with a 1,779-commit
  history took well under 0.1 s here). No network, no `gh` calls in the hook (Q9 is the agent's job).
- **Prompt cache**: the card is appended content, so it is paid once as new input and then cached with the rest
  of the conversation prefix (the best-practices page says the cache lasts about an hour on a subscription, five minutes
  otherwise); I did not measure the cache effect.

### 4.7 Failure behavior (all fail open, none blocks work)

| Failure | Behavior |
|---|---|
| citymap missing/unparseable/stale beyond 1 commit | emit a 1-line card: `CONTEXT <path> | map unavailable: run python scripts/citymap.py`, then nothing else |
| file not in citymap (new file) | emit purpose from the docstring and `new file: not in citymap yet` |
| git slow/failed | omit GIT lines |
| JSON from stdin bad, any exception | print nothing, exit 0 (PostToolUse non-zero would be non-blocking anyway) |
| over 1,800 chars | drop-from-bottom rule (4.4); hard clip |
| timeout (3 s) | Claude Code treats as non-blocking; no card |
| tool_input has `offset`/`limit` | still card once (the seen-set decides) |
| output ever exceeds 10,000 chars | impossible by clip; the doc'd overflow path would hide it |
| `data/.ctx_cards_off` exists | print nothing (kill switch; only Nick creates it, like the other escape hatches) |

Log one line per card (`ts, session_id, mode, path, chars, ms`) to `data/ctx_cards.jsonl` (gitignored like other data/ logs)
so cost and usefulness can be measured.

### 4.8 CI check: every module has a purpose and a Why header

`scripts/check_headers.py` (stdlib `ast`, runs in `ci.yml` next to `check_memory.py`; about 60 lines):
1. For each `*.py` under `redzone/`, `scripts/`, `agent/` (the 377 citymap modules; skip `__init__.py` with no code):
   require a module docstring whose first line is a sentence of 10-100 characters (purpose).
2. Require a line matching `^Why: .{15,}` in the docstring. The text may point at a file with a link,
   e.g. `Why: ... (docs/memory/decisions.md 2026-11-07)`; `check_memory.py` already verifies links resolve, extend it to these.
3. Ratchet: `docs/header_baseline.json` lists modules allowed to lack `Why:` today. CI fails if (a) a module outside
   the list lacks one, or (b) the list grew. Same pattern as the repo's existing `catchup_ratchet.json` and
   `contract_baseline.json`. Initial baseline is about 355 modules; it shrinks as backfill PRs land.
4. Optional lint half: Ruff D100 (`undocumented-public-module`, from pydocstyle) [opened] checks docstring presence;
   our script is still needed for the `Why:` line. Enabling D100 needs a `select` change in `pyproject.toml`, which is
   protected by "CI blocks on real errors only" policy; leave it out and rely on the script.
5. Also `Why:` convention for new commits: add a PR-template line "Why:" (already the norm in merge bodies).
   Do not CI-block commit messages yet (6 of 400 today), only warn.
6. A PR that adds the check is a process change: add a decisions.md line (CI enforces for defaults; do it by hand here).

Header template (what the CI check accepts):

```python
"""Turns decisions into TV actions. Dry-run by default.

Why: the hidden stick is always the one tuned, so a cut never shows a black screen
(docs/memory/decisions.md 2026-11-05 "Go now"; lessons.md 2026-11-07 buffered jumps).
Invariant: never JOIN mid-play (Rule #1); forced "Go now" presses are graded separately.
"""
```

Do not put callers/callees/data files in the header: they drift and citymap generates them. The header holds only what
cannot be derived (purpose, why, invariants), which also matches the AGENTS.md finding to keep context to non-inferable
content.

### 4.9 Acceptance tests and the A/B that decides whether it stays on

- T1 canary: card contains `CANARY-<nonce>`; fresh session reads one file; ask "what nonce did you see?". Pass = model
  quotes it. Run once per Claude Code upgrade (F1 caveat).
- T2 size: for all 377 modules, card <= 15 lines and <= 1,800 chars (pytest over the real citymap).
- T3 latency: p95 < 150 ms over all modules; hook timeout 3 s.
- T4 subagent: Read in a subagent produces a card and does not spam duplicates.
- T5 fail-open: corrupt citymap, missing git, bad stdin, read-only TMPDIR: exit 0, no stdout (or the 1-line fallback).
- T6 dedup + compact: second read of same file prints nothing; after `compact`, prints again.
- A/B (the F3 requirement): 20 recorded tasks from `docs/memory/lessons.md` (each a real past bug), same model, card
  on vs off, 3 runs each. Measure: number of reads before the first edit, tests named before editing, regressions against
  Rule #1 table, total tokens. Keep ON only if tokens do not rise more than 10% and either regressions or wrong-file
  edits fall. Otherwise ship OFF behind the kill switch, per the repo's gate rule. (Cost: runs on Nick's Claude usage;
  mind the 88% pause; the Mac does no heavy work, since this is API calls only.)

## 5. Backfill plan for about 360 `Why:` headers

Measured today [repo]: 377 citymap modules (350 under `redzone/` + `scripts/` per the citymap, 211 `redzone/*.py` by glob),
362 with a docstring (purpose is about 96% covered; 15 say "no docstring"), 7 of 211 `redzone/` docstrings contain
"Why". Git history cannot supply it automatically: every module's first-add commit was checked and 0 of 377 have a `Why:`
line (control.py was added in a mega-commit). The real sources are decisions.md/lessons.md matches (`scripts/why.py`),
EXP-numbered commit subjects (for example cuts.py: EXP-211, EXP-024, EXP-089), PR descriptions, and the module itself.

Steps (about 6 hours of agent time spread over 3 days, plus 15 min of Nick's review per wave; run as normal queue
threads, not all at once, to respect the 5-thread limit):

1. **Wave 0 (30 min, one PR): add `check_headers.py`, baseline JSON, T2 test, `Why:` template in the docs.** Baseline
   lists all missing modules so CI is green.
2. **Wave 1 (about 40 modules, about 2 hours): highest leverage first.** Rank = importer count (citymap `in_degree`) plus any
   north-star tag or Rule #1 relevance: `control.py`, `engine.py`, `decider/*`, `standby.py`, `fsm.py`, `supervisor.py`,
   `autonomy.py`, tuners. These are where a wrong edit breaks Rule #1.
3. **Wave 2 (about 120 modules): rest of `redzone/`**, then **Wave 3 (about 200): `scripts/`, `agent/`, `lab/`** where
   one-line Whys are usually "operator tool for <decision>" and are cheap.
4. **Drafting:** `scripts/draft_why.py` (stdlib; no model needed for the first pass) emits for each module a candidate:
   the newest decisions/lessons line mentioning the file (via `why.py` `mentions()`), the earliest non-mega commit subject
   mentioning it (`git log --format=%s -- file`, skipping subjects over 100 chars or "Everything"), and the docstring's own first
   paragraph. A cheap model (Haiku, as the best-practices page suggests for simple subagent jobs) then writes the
   1-2 line `Why:` from those three inputs only, forbidden from inventing; any module with no evidence gets
   `Why: unknown, ask <owner>` and stays on the baseline list (honest gap beats fiction).
5. **Review rule:** each batch of about 25 modules is one PR (docstring-only diffs; no behavior change; `pytest -q`
   and `ruff check .` still green; the citymap is regenerated since roles change). A second agent or Nick spot-checks 5
   per batch against the cited decision line. Because the edit is docstring-only, no Rule #1 before/after table is
   needed beyond stating "no code change".
6. **Ratchet:** each wave removes its modules from `docs/header_baseline.json`; CI proves the count fell.
7. **Forward fix:** `Why:` line in the PR template and a warning (not a block) on commits without one.

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Injection does not reach the model for built-in tools (issue #18427) | medium | T1 canary; fall back to `PreToolUse` additionalContext or `updatedToolOutput` (documented but must match tool output shape; riskier) |
| Context bloat: AGENTS.md study found cost +20% from context files | medium | <= 1,800 chars, once per file per session, A/B gate, kill switch |
| Card repeats what the file shows | medium | drop the "what it does" part; card = why, dependents, tests, invariants |
| Wrong or stale facts in card | medium | citymap sha/age line; stale-map fallback; CI already regenerates and checks citymap |
| Backfilled Whys are invented | high if unchecked | evidence-only drafting; `unknown` allowed; spot-check 5 per batch |
| Protected `.claude/**`: agent cannot install hook | certain | Nick applies the settings change; hook files created by him or in a normal PR he approves |
| Hook slows every read | low | 3 s timeout, p95 < 150 ms test, no network |
| Cards in subagents multiply | medium | T4; key seen-set per agent |
| Prompt-injection via docstrings/commit bodies | low | strip tags/control chars; only repo-local, committed text |
| Hook behavior changes in new Claude Code versions | medium | canary in CI of the research harness; read the hooks reference at upgrades |
| Header Why rots | medium | PR template line; Why reviewed with the code owner |

## 7. Next step (under 2 minutes)

Reply "go card" and I (or the integrator) will open the Wave 0 PR (`check_headers.py` + baseline + `code_card.py` +
tests), with the settings.json diff for you to paste, since agents cannot edit `.claude/**`.

## 8. Bibliography (verified by opening unless marked)

Official Claude Code docs
1. Claude Code hooks reference, https://code.claude.com/docs/en/hooks - additionalContext semantics, 10,000-character cap,
   matcher syntax, exit codes, `if` filter, PostToolUse `updatedToolOutput`, InstructionsLoaded. [opened; first 200,000 of ~290,000 characters read]
2. Automate actions with hooks (guide), https://code.claude.com/docs/en/hooks-guide - settings format, PostToolUse examples,
   SessionStart `compact` re-injection, additionalContext "injected as a system reminder", multiple hooks combine. [opened]
3. How Claude remembers your project, https://code.claude.com/docs/en/memory - subdirectory CLAUDE.md lazy load on read,
   `.claude/rules` `paths:`, under 200 lines guidance, InstructionsLoaded. [opened]
4. Best practices for Claude Code, https://code.claude.com/docs/en/best-practices - tell Claude what it can't see, give it a check,
   enforce rules via hooks, subagent cost, Haiku for simple subagent jobs, `gh` for issue tracker. [opened]

Issues and practitioner posts
5. anthropics/claude-code issue 18427, https://github.com/anthropics/claude-code/issues/18427 - PostToolUse injection report;
   closed by bot Feb 28 2026; PreToolUse additionalContext added v2.1.9; inconsistent for built-ins on v2.1.22. [opened]
6. anthropics/claude-code issue 43750, https://github.com/anthropics/claude-code/issues/43750 - Apr 5 2026, v2.1.92; claim that
   Read/Edit/Write/Glob/Grep inject works; author retracted the bug for Agent. [opened; weak evidence]
7. CodeGraph write-up (mer.vin), https://mer.vin/2026/05/codegraph-cut-ai-coding-agent-tool-calls-with-a-local-semantic-code-index/ -
   May 29 2026; vendor benchmark numbers (71% fewer tool calls, 57% fewer tokens); MCP, not hooks. [opened; vendor claims]
8. Aider repo map docs, https://aider.chat/docs/repomap.html and blog https://aider.chat/2023/10/22/repomap.html - graph ranking, 1k-token
   default budget, signatures only. [opened both; blog is 2023]

Papers
9. Gloaguen et al., Evaluating AGENTS.md, https://arxiv.org/abs/2602.11988 - Feb 12 2026 (v3 Sep 29 2026). [opened abstract page]
10. Liu et al., RepoScope, https://arxiv.org/abs/2507.14791 - Jul 2025, ICSE 2026. [opened abstract page]
11. Hu et al., InlineCoder, https://arxiv.org/abs/2601.00376 - Jan 2026, FSE 2026. [opened abstract page]
12. Zhang et al., DualView, https://arxiv.org/abs/2607.01929 - Jul 2 2026. [opened abstract page]
13. Ouyang et al., RepoGraph, https://arxiv.org/abs/2410.14684 - ICLR 2025 (rev. Mar 2025). [opened abstract page]
14. Stetsenko, Lore: commit messages as knowledge protocol, https://arxiv.org/abs/2603.15566 - Mar 16 2026; no empirical results. [opened]
15. LocAgent, https://arxiv.org/abs/2503.09089 - Mar 2025. [snippet only]
16. Anthropic, Effective context engineering for AI agents, https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents -
    Sep 29 2025. [opened]
17. Chroma, Context Rot, https://www.trychroma.com/research/context-rot - Jul 14 2025, 18 models. [opened]

Process and norms
18. Nygard, Documenting Architecture Decisions, https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions - Nov 15 2011. [opened]
19. Google Engineering Practices, What to look for in a code review, https://google.github.io/eng-practices/review/reviewer/looking-for.html. [opened]
20. GitHub Docs, About code owners, https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners. [opened]
21. coverage.py, Measurement contexts, https://coverage.readthedocs.io/en/latest/contexts.html - `dynamic_context = test_function`. [opened]
22. Ruff rule D100 undocumented-public-module, https://docs.astral.sh/ruff/rules/undocumented-public-module/. [opened]
23. Qodo, What is an ADR (rationale-rich records cut time ~10%; cites an unopened preprint), https://www.qodo.ai/glossary/what-is-an-architecture-decision-record/. [snippet only, unverified]
24. Chesterton's fence in software (git blame/log as provenance), e.g. https://rachsmith.com/dont-remove-a-fence/ and
    https://dev.to/dnmeyer06/chestertons-fence-its-significance-in-software-engineering-2a0i. [snippet only, secondary blogs]

Repo evidence (all measured 2026-11-08 in the worktree)
25. `scripts/citymap.py --impact redzone/control.py`, `scripts/why.py redzone/control.py`, `docs/citymap.json` (343 KB, 377 modules),
    `.claude/settings.json`, `.claude/hooks/guard.py` (G1-G7), `docs/memory/decisions.md` (1,477 lines), prototype card run on control.py and cuts.py.

Not verified (do not cite): any claim that a published open-source Read-time dependents hook exists; the hooks
reference's remaining ~90,000 characters; whether subagent Reads fire PostToolUse hooks with a distinct agent id.
