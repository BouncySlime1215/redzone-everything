---
id: W1-HARNESS
title: Claude on the Mac with the least user interaction
date: 2026-11-08
stream: harness
author: wave-1 harness agent
status: draft
supersedes: []
sources: 23
verified: 22
affects: [.claude/settings.json, .claude/hooks/guard.py, scheduled tasks, ~/.claude/settings.json]
gate: []
---
# Mac harness: a coordinator that keeps going, asks rarely, and cannot hurt the TV

Research note, 2026-11-08. Builds on [opus-5-5-harness-and-time.md](../../research/opus-5-5-harness-and-time.md)
(its ten actions are not repeated). Goes deeper on permissions, keep-alive, scheduled agents, notifications,
compaction and the Desktop/claude.ai settings. No product code or setting was changed to write this.

**How to read it.**
- **Verified** = I opened the page or ran the read-only command on 2026-11-08. Bibliography at the end: 26 entries.
- Claude Code docs pages carry no publication date. Version and date come from the changelog (latest entry v2.1.319, 2026-11-06).
- **[local]** = observed on this Mac with a read-only tool (`get_settings`, `get_usage`, `pmset -g`, `man`). Not a doc claim.
- **[unverified]** = could not confirm from an opened source. Test it with "Run now" before relying on it.
- Search-engine snippets were not used as evidence. Web search returned only March-April 2026 press; nothing from Oct-Nov.

---

## 0. The answer in 60 seconds

**Do not build one immortal session. Build a chain of short ticks plus one chat session.**

Why one immortal session fails (all verified):
1. A recurring `/loop` or cron task dies after **7 days** [S5].
2. A self-paced `/loop` now **ends** if a turn finishes without rescheduling (v2.1.319, 2026-11-06); the old fallback needs `CLAUDE_CODE_LOOP_KEEPALIVE=1` [S16]. A self-paced loop is **not restored** on `--resume`, and running Monitors are not restarted [S5].
3. Every idle wake-up re-sends the whole context. A day-old session pays for the whole day on every fire [S17].
4. A weekly limit that resets **more than 24 h away** is never auto-waited, and auto-continue stops after 3 tries [S13].
5. **Desktop scheduled tasks** persist across app restarts, start a **fresh** session each time, skip a run that overlaps the last one, and run one catch-up after sleep [S4]. That is the durable primitive.

**Design.** (details in section 4)
- `rz-coordinator-tick`: a Desktop scheduled task, hourly 06:07-23:07. Each tick is a fresh Opus 5.5 session. It reads `docs/handoff.md`, checks the usage budget, does one bounded chunk, writes state, and exits.
- One pinned **"RedZone Chat"** session is where Nick talks. It is not a worker. It reads the inbox.
- Standing agents (research, data, training prep, usage watch) are separate scheduled tasks on cheaper models.
- Nick hears from the system by **one push per real decision** plus **one morning digest**.

---

## 1. State of this Mac and Nick's setup [local]

| Fact | Value | Why it matters |
|---|---|---|
| Plan | Ultra. Weekly all-models **45%** used, resets **2026-11-11 21:00 UTC** (16:00 EST). Extra usage (credits) **disabled** | Keeps the $0 rule. 88% = hard stop. 43 points of room for about 2.9 days |
| User `defaultMode` | `bypassPermissions`, plus `skipDangerousModePermissionPrompt` | No prompts today, but nothing but project `deny`/`ask` and hooks stand between a mistake and the sticks |
| User settings also hold | an allow rule for `ssh -i ~/.ssh/research_lab_vm *`, hooks from three tools (one is `~/claude-handoff/ensure.sh`), `agentPushNotifEnabled: true` | Push to phone is already enabled in settings |
| Desktop prefs | keep-awake while working **On**, also on battery **On**; Remote Control for new sessions **On**; permission and question banners **On**; task-complete banner **On**; prompt suggestions **On**; auto-archive inactive **Never** | See section 8 |
| Power | `pmset -g`: `sleep 1`, `displaysleep 0`, on AC and battery. Held awake right now by `powerd` ("Prevent sleep while display is on") and by Claude's own `NoIdleSleepAssertion` (50 h old) | The Mac stays awake only while the display is on or Claude holds an assertion. Lid close, a manual Sleep, or the display going off ends it |
| Baseline context of one fresh session | system tools 32k + MCP tools 26k + system prompt 11k + skills 9k + MCP instructions 3k = about **82k tokens** before any task | Every tick pays this (cached after its first request). Trim connectors (section 8) |
| This session's context | 724k of 1M used (72%), compacts at 97% | A long session is exactly what ticks avoid |

---

## 2. Permissions: what to use

### 2.1 Facts (verified)

- **Order of evaluation:** deny, then ask, then allow; first match wins. A narrow allow cannot carve out of a broad deny [S2].
- **Modes:** `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions` [S1].
  - `bypassPermissions` ignores **allow** rules, but **deny rules still block** and **ask rules still prompt** [S1].
  - A `PreToolUse` hook that exits 2 blocks before permission rules run, in every mode [S2, S10].
  - `dontAsk` auto-denies anything not pre-approved. It is not in the Desktop selector and never in the Shift+Tab cycle [S1, S6].
- **Precedence of settings:** managed > command line (`--settings`) > `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`. Lists such as `permissions.allow` **merge** across files [S7].
- **Keys that only work from user settings:** `permissions.defaultMode` values `auto` and `bypassPermissions`, and the whole `autoMode` block. In the project files they are silently ignored, so a repo can never switch itself to bypass or add its own classifier allow rules [S1, S3, S7].
- **Desktop:** the mode picked in the selector is remembered **per folder** and overrides a project `defaultMode` [S1, S6]. Shortcut Cmd+Shift+M [S6].
- **Auto mode** [S1, S3]:
  - A server-side classifier reviews each call. All plans. Opus 4.6+ and Haiku 5.5 qualify.
  - `permissions.ask` and `permissions.deny` run **before** the classifier. A content-scoped `ask` such as `Bash(git push *)` always prompts, even in auto.
  - Narrow `Bash(...)` allow rules still apply; broad ones (`Bash(*)`, wildcard interpreters, anything naming `Monitor`) are dropped. `autoMode.classifyAllShell: true` drops all of them [S3].
  - **3 blocks in a row, or 20 in total, pauses auto mode and prompts.** Not configurable [S1]. A prompt that nobody answers is a stall.
  - Subagents are checked at spawn, per action, and at return. A subagent's own `permissionMode` is ignored [S1, S21].
  - If the classifier gets no verdict, the call is denied, not run [S1].
- **Deny/ask rules are not a security boundary around a program.** `Bash(git push *)` does not match `git -C . push`; `Bash(rm *)` does not match `/bin/rm` or `bash -c 'rm ...'` [S2].
- **New 2026-11-06 (v2.1.319):** `permissions.denyInsteadOfPrompting`: a call that would wait on a prompt is denied and the turn continues "in terminal sessions, subagents and teammates" [S16]. **[unverified]** whether it applies in Desktop scheduled sessions; test it.
- **Monitor** commands obey Bash rules, and a `Bash` hook matcher also runs on them (v2.1.314+), but a hook cannot approve or rewrite them [S10, S9].
- **Sandbox** (`sandbox.enabled`, Seatbelt, off by default): sandboxed Bash needs no prompt in any mode. Do not use it here: adb and local-network tools need `excludedCommands`, and `osascript` and `docker` break [S18].

### 2.2 Recommendation

| Session | Mode | Reason |
|---|---|---|
| **RedZone Chat** and every **tick** (Opus) | **Auto** | Only layer that judges intent ("this python -c opens adb"). Hook regexes cannot. Stall risk is handled below |
| **Standing agents** (Sonnet/Haiku) | **Auto** in the task toolbar, with a **narrow allow list** | `dontAsk` is not offered by the Desktop form [S4, S6]. Narrow allows skip the classifier, so they are fast and cheap |
| Anything on the sticks | **Never unattended.** Needs `data/.agent_hw_ok`, which only Nick creates (guard G3) | Rule: no disruptive TV tests while Nick watches |

Why not stay on `bypassPermissions`, which Nick uses today:
- It never stalls, which is attractive for "least interaction".
- But the only things protecting the sticks are `deny` rules (bypassable by quoting) and `guard.py` regexes (section 2.4 lists holes). One wrong `bash -c "adb ..."` reaches a live TV.
- Auto keeps the same "no routine prompts" experience and adds a second, semantic gate.
- **If auto stalls too often** (more than 2 pauses a week), fall back to bypass **and** apply every hardening in 2.4. That is Nick's call (decision card).

Stall handling in auto: the repeated-block pause becomes a phone push [S11]. The fix is almost always an `autoMode.environment` line or a narrow allow, so the tick records each block in `docs/memory/lessons.md` and proposes the rule (via `PermissionDenied` hook, [S10]).

### 2.3 Exact configuration

**A. `~/.claude/settings.json` (user file). Nick pastes; merge into the existing file, do not replace it.**
Existing keys (hooks, plugins, ssh allow, `agentPushNotifEnabled`) stay.

```json
{
  "permissions": {
    "defaultMode": "auto",
    "allow": [
      "Bash(python -m pytest *)",
      "Bash(python3 -m pytest *)",
      "Bash(ruff check *)",
      "Bash(python scripts/check_memory.py*)",
      "Bash(python scripts/context_pack.py*)",
      "Bash(python scripts/ci_scorecard.py *)",
      "Bash(python scripts/release_gate.py *)",
      "Bash(python scripts/fetch_eval_data.py*)",
      "Bash(python scripts/deploy.py --dry-run*)",
      "Bash(python scripts/deploy.py status*)",
      "Bash(git status*)", "Bash(git diff*)", "Bash(git log *)", "Bash(git add *)",
      "Bash(git commit *)", "Bash(git switch *)", "Bash(git fetch*)", "Bash(git worktree *)",
      "Bash(gh pr view *)", "Bash(gh pr list *)", "Bash(gh pr checks *)", "Bash(gh run list *)"
    ]
  },
  "autoContinueAtUsageLimit": false,
  "autoCompactWindow": 400000,
  "agentPushNotifEnabled": true,
  "inputNeededNotifEnabled": true,
  "autoMode": {
    "environment": [
      "$defaults",
      "Repository visibility: the working repository is public. Never commit or push tokens, passwords, keys, or the addresses of the Fire TV sticks or the TV.",
      "Host containment: this is a Mac that also runs a live app for a TV. The Fire TV sticks and the Samsung TV are off limits unless the file data/.agent_hw_ok exists.",
      "Branch policy: pushes go to feature branches and pull requests target integration/next. Never push to main, codex/resume-redzone or integration/release."
    ],
    "soft_deny": [
      "$defaults",
      "Running adb, scrcpy, cec-ctl, or any script or python code that talks to a Fire TV stick or the TV, unless data/.agent_hw_ok exists",
      "Running scripts/deploy.py without --dry-run or status, unless data/.agent_deploy_ok exists",
      "Running model training or calibration on this Mac; those run on Kaggle or in CI",
      "Reading or printing files whose names suggest tokens, keys, passwords, .env or oauth"
    ]
  }
}
```
- Every key name above appears in the docs [S3, S7, S19, S20]. `inputNeededNotifEnabled` appears only in the settings index (no default stated); `agentPushNotifEnabled` appears there too.
- Verify after saving: `claude auto-mode config` (shows the effective rules), `claude auto-mode critique` (flags vague rules) [S3].
- `autoContinueAtUsageLimit: false` is deliberate. Auto-continue only fires at 100% usage, which is past Nick's 88% stop. A continuation would resume mid-task at reset with no pacing check. A tick at reset time re-checks the budget instead.
- `autoCompactWindow: 400000` compacts earlier than the default window for Opus 5.5. Range 100000-1000000 [S20]. **[unverified]** the exact default for Opus 5.5.
- Do **not** add `"skipDangerousModePermissionPrompt"` changes. Leave it.

**B. `.claude/settings.json` (project, committed). Needs a PR by Nick: `.claude/**` is protected by guard G6, so no agent can edit it.**
Current file has 4 `deny` rules and 3 hooks. Proposed additions:

```json
{
  "permissions": {
    "deny": [
      "Bash(git push --force:*)", "Bash(git push -f:*)",
      "Bash(git push origin main:*)", "Bash(git push origin codex/resume-redzone:*)",
      "Bash(adb *)", "Bash(*/adb *)", "Bash(scrcpy *)", "Bash(cec-ctl *)",
      "mcp__terminal__run_in_terminal",
      "mcp__computer-use__*"
    ],
    "ask": [
      "Bash(git push *)",
      "Bash(gh pr create *)",
      "Bash(gh pr merge *)",
      "mcp__ccd_session__start_session"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|Read|Grep|Glob|Monitor",
        "hooks": [
          { "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py\"",
            "timeout": 10, "onFailure": "block" }
        ]
      }
    ],
    "PermissionDenied": [
      { "hooks": [ { "type": "command",
        "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/log_denial.py\"", "timeout": 5 } ] }
    ]
  }
}
```
- `ask` on `git push *` fires in **every** mode including bypass [S1, S3]. Push is the one routine action Nick wants a human on. Batch pushes so it costs one phone tap per batch (section 6).
- `mcp__terminal__run_in_terminal`: deny so shell work goes through `Bash`, where the guard sees it. Currently **`guard.py` does not see it** (hole 1 below).
- `mcp__computer-use__*`: **[unverified]** glob syntax for MCP names containing `-`. Rules accept globs in the tool-name position and names containing `_` or `*` skip the typo warning [S2]; run `claude doctor` after merging.
- `onFailure: "block"` needs v2.1.295+ and makes a hook that cannot start or times out block, instead of allowing [S10].
- `log_denial.py` and `PermissionDenied` are new; `PermissionDenied` receives the exact `tool_input` [S3, S10]. Stdlib only, append one line to `data/denials.jsonl`.
- Permission **allow** rules in a committed file wait for workspace trust; deny and ask do not [S7].

### 2.4 Holes in the current `guard.py` (read from the file, not tested)

| # | Hole | Effect | Fix |
|---|---|---|---|
| 1 | Matcher is `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit\|Read`. MCP tools `mcp__terminal__run_in_terminal` and `mcp__computer-use__*` are not matched | G3 (adb), G5 (deploy) and G1 can be bypassed by running the same text in the terminal panel | Add them to the matcher, or deny them (2.3 B) |
| 2 | `HARDWARE` needs adb preceded by start, whitespace, `;&\|(` or backtick. `bash -c 'adb shell'` has a quote before `adb` | Quoted or `sh -c`-wrapped adb passes | Add `'"` to the leading class; also match `python.*-c.*adb` |
| 3 | Only `Read` is checked for secret names. `Grep` and `Glob` over `.env` are not | Secret content can enter context | Add `Grep\|Glob` and check `path`/`glob` |
| 4 | Stdin parse failure or any exception returns without blocking (fail open) | A broken hook silently stops guarding. CI re-checks, but late | Keep fail-open for crashes but log to `data/guard_errors.log`; add `onFailure: "block"` for timeouts |
| 5 | A python script that calls adb internally (`python -m redzone.tvsetup` is covered; any other module is not) | Hardware touch with no `adb` in the command text | The classifier in auto mode is the backstop; also keep `data/.agent_hw_ok` checks inside `AdbShell` itself (product code, not this task) |

---

## 3. Keeping a coordinator alive

### 3.1 What each mechanism does (verified)

| Mechanism | Persists across app restart? | Survives sleep? | Cost shape | Verdict |
|---|---|---|---|---|
| **Desktop scheduled task** [S4] | **Yes** (stored on disk at `~/.claude/scheduled-tasks/<name>/SKILL.md`; schedule/model/folder in app state) | Run is **skipped** if the Mac sleeps through; one **catch-up** run for the most recent miss within 7 days on wake/launch | One fresh session per run (cache cold on first request) | **Backbone** |
| Local task fields [S4] | Hourly, Daily, Weekdays, Weekly, Manual, Custom cron in the form (min **1 h**); Claude itself can set 1 minute or a one-off. Each run has a few minutes of deterministic delay. Optional **Worktree** per run | | | |
| **Cloud scheduled task / routine** [S12] | Yes, runs with the Mac off on a fresh clone; `claude/` branches; starts in auto mode; min 1 h; 100 scheduled runs/h per account; draws normal plan usage; with usage credits **off**, runs are rejected after the limit | n/a | No local `data/`, no sticks | Good for web research only |
| **`/loop`, CronCreate, ScheduleWakeup** [S5, S16] | **No.** Cron tasks come back on `--resume` (not expired, not past one-shots); self-paced loops do not; background shells and Monitors are not restarted | Fires only while the session is running and idle; no catch-up | Full context on every fire | Use for a short watch inside one task, never as the backbone |
| **Monitor** [S9] | No (dies with the session) | n/a | One event per output line, cheap | Use for deploy health streams; **deadline 5 min default, 30 max** per watch, then one notice |
| **Background sessions / `claude agents`** [S8] | Run under a supervisor; survive closing the terminal and **sleep**; a reboot stops them (failed if progress in last 48 h; restartable) | Yes | Each session its own quota draw | Option for a terminal workflow. In Desktop, scheduled tasks cover the need. **[unverified]** whether Desktop shows these |
| **`/goal`** [S14] | Restored on `--resume`, counters reset | n/a | Small-model check per turn; idle check-ins capped at 3 | Use **inside a tick** to bound it: "...or stop after 25 turns" |
| **Remote Control** [S11] | Interactive sessions reconnect after sleep/network loss on their own | Yes | None | The phone window onto a running session. Server mode exits after about 10 min offline |
| **Auto-continue at usage limit** [S13] | Only in an open session; not for resets over 24 h away; 3 max | | | Turned off (2.3 A) |
| **Idle compaction** [S17, S20] | Default on (`idleCompaction`); compacts an idle 200K+ conversation so the next message sends a summary | | Avoids a cold full re-read | Leave on |

### 3.2 Keep-awake

| Layer | Needs sudo? | Status / action |
|---|---|---|
| Desktop **"Keep computer awake while Claude works"** (+ "on battery") | No | **Already On** [local]. Holds only while a session is working, releases "shortly after". It does **not** cover the gap between ticks |
| Display-on assertion | No | Currently the reason the Mac stays up [local]: `displaysleep 0` plus `powerd` "Prevent sleep while display is on". Fragile: a screen saver, lock, or the display powering off can end it |
| `caffeinate -i` (user process) | **No.** Prevents idle sleep only; `-s` only on AC; `-t secs` or `-w pid` to bound it [man caffeinate, local] | Recommended **belt**: `nohup caffeinate -i -w $(pgrep -x Claude \| head -1) >/dev/null 2>&1 &`. Ends when the Claude app exits. Coordinator can start it; it dies on logout/reboot, so a tick re-checks `pgrep caffeinate` |
| `pmset -a sleep 0` / `pmset -a disablesleep 1` | **Yes, root** ("pmset must be run as root in order to modify any settings", man pmset, local) | Optional. Nick runs it in Terminal himself. Changes system-wide behavior and the Mac is also the live TV host; the coordinator must not run it |
| `mcp__ccd_host__request_keep_awake` | No (card approval; `until: "session_idle"`) | Available to a session. A hold lasts only while the session lives [local tool description]. Not a backbone |
| Lid closed or manual Sleep | n/a | Nothing above prevents it. Leave the lid open or use clamshell mode with power and an external display |

Verdict: the Mac stays awake on current settings as long as the display stays on. Add the `caffeinate` belt (zero sudo). Leave `pmset` alone unless a missed tick shows up in task history.

---

## 4. Scheduled tasks to create

Time zone: local (EST, UTC-5 today). Times avoid `:00`/`:30` (one-shot early-run rule; recurring tasks get a delay anyway [S5]).
Create them through the Desktop **Scheduled** page or by asking Claude in a session [S4]. Each task needs **Run now** once to see prompts [S4].
Model and permission mode are set in the task toolbar. All local tasks use folder = the main checkout, **Worktree on** (a worktree per run).
**[unverified]** `worktree.baseRef` default is the remote default branch (`main`). This repo's base is `codex/resume-redzone`, and every PR targets `integration/next`. Check the `worktree.baseRef` entry in [S19] and set it, or have each prompt run `git switch -c <task>/<date> origin/integration/next` first.

| # | Name | Where | Cron (local) | Model | Budget rule |
|---|---|---|---|---|---|
| 1 | `rz-coordinator-tick` | Local | `7 6-23 * * *` (18/day) | Opus 5.5, medium effort | Stops if weekly at or above pace line |
| 2 | `rz-usage-watch` | Local | `47 */2 * * *` | Haiku 5.5 | about 0.1% per run (est.) |
| 3 | `rz-morning-digest` | Local | `3 8 * * *` | Sonnet 5.5 | 1 push/day |
| 4 | `rz-data-nightly` | Local | `43 3 * * *` | Sonnet 5.5 | Light scripts only |
| 5 | `rz-research-sweep` | **Cloud** | `17 2 * * 1,4` | Sonnet 5.5 | at most 1.5% of weekly (est.) |
| 6 | `rz-training-prep` | Local | `31 22 * * 0` | Sonnet 5.5 | Prepare and check only |
| 7 | `rz-consolidate-lessons` | Local | `13 9 * * 0` | Sonnet 5.5 | Memory hygiene |

### 4.1 Pacing (read by every task first)

The week resets at 2026-11-11 21:00 UTC and then every 7 days (the `resetsAt` value in `get_usage` [local]; read it, do not hard-code).
Hard stop **88%** (CLAUDE.md). Soft pace line:

```
pace_line = 88 * (hours_since_reset / 168)      # where we "should" be
used      = get_usage().plan.windows["Weekly · all models"].percentUsed
over      = used - pace_line
GREEN  : over <= 0           -> full tick (Opus work allowed)
AMBER  : 0 < over <= 6       -> cheap tick: Sonnet subagents only, no Opus fan-out, no new research
RED    : over > 6 or used>=80 -> bookkeeping only (handoff, inbox), no code work
STOP   : used >= 88          -> write data/usage_pause, push ONE notification, end. Next ticks exit in 3 calls.
```
- `get_usage` is the tool `mcp__ccd_session_mgmt__get_usage` [local]. **[unverified]** it is available inside a scheduled (non-chat) session; if not, `rz-usage-watch` writes `data/usage_state.json` from a session where it is, and every other task reads that file.
- `/usage` figures are computed from local history and miss other devices [S17]; `get_usage` is account-level.
- Early in the week the pace line is low by design: day 1 allows about 12.6 points. Do not "catch up" on Mondays.
- Reset moment: the first tick after `resetsAt` clears `data/usage_pause`. Auto-continue is off, so nothing resumes unchecked.

### 4.2 Prompts

All prompts start with: *"Read CLAUDE.md, docs/handoff.md, docs/status.md. Obey the 88% usage rule in docs/memory/runbooks/usage-pause.md. If `data/usage_pause` exists, do nothing but say so in one line. If a catch-up run (the clock is more than 90 minutes past the scheduled time), do only items marked CATCHUP-OK."* Abbreviated here as **[PRE]**.

1. **`rz-coordinator-tick`**
   > [PRE] Compute the usage band (docs/agents/wave1/mac-harness.md section 4.1). Then: (1) list open PRs and CI with `gh` (read-only); (2) pick the single highest-ranked unblocked item in docs/roadmap.json that fits the band; (3) delegate it to a subagent (`model: sonnet` for edits, `haiku` for reading/searching; never more than 3 at once; isolation worktree); (4) run `python -m pytest -q -rs` and `ruff check .` on its result; (5) commit locally on a feature branch; do **not** push; add a line to `docs/inbox.md` only if it needs Nick (decision, push approval, hardware test). Finish with `/goal`-style bound: stop after 25 turns or 45 minutes. Before ending, rewrite the "Now / Next / Blocked" block at the top of docs/handoff.md in under 15 lines and list any known risks.

2. **`rz-usage-watch`** (CATCHUP-OK)
   > Read usage with get_usage. Write `data/usage_state.json` {used, resetsAt, pace_line, band, ts}. If used >= 88 create `data/usage_pause`; if used < 80 and the pause file exists and resetsAt has passed, delete it. If used >= 88 and no push was sent in the last 12 h, send one PushNotification: "Usage at X%. Work paused until <resetsAt>." Nothing else.

3. **`rz-morning-digest`**
   > Summarize since yesterday 08:00: PRs opened and merged, tests, scorecard deltas (entries_mid_play, plays_missed_entry, dead_air_pct), blocked items, usage used vs pace. Publish or update one private artifact "RedZone Inbox" listing only decisions waiting on Nick, newest first, each with: question, two options, my pick, deadline. Send one PushNotification only if the list is non-empty ("N decisions waiting").

4. **`rz-data-nightly`**
   > [PRE] No training, no hardware, no deploy. Run `python scripts/fetch_eval_data.py` if the pinned data is missing, then `python scripts/ci_scorecard.py --nfl-n 2 --nights 4` only for leagues touched in the last 24 h of commits. Write the before/after table to `reports/` and a 5-line summary to docs/handoff.md. Stop if memory pressure is high (`vm_stat`, `memory_pressure`).

5. **`rz-research-sweep`** (cloud routine; repo is public, no secrets)
   > Take the first unchecked topic in docs/roadmap.md under "Research queue". Use only dated sources from Oct-Nov 2026 when available, open every source, mark snippet-only claims. Write docs/research/<topic>.md with a bibliography of at least 15 dated entries. Push to a `claude/` branch and stop. Do not touch code.

6. **`rz-training-prep`**
   > [PRE] Follow docs/memory/runbooks/kaggle-run.md up to the point that needs Nick's credentials. Prepare the notebook and dataset manifest, run the leakage checks, write a "ready to run" card to docs/inbox.md. Do not run anything on this Mac that trains or calibrates (guard G4). Never read or request tokens.

7. **`rz-consolidate-lessons`**
   > Read docs/memory/lessons.md and the last 7 days of docs/memory/journal/. Merge duplicates, mark which facts are confirmed, keep the always-loaded layer short, and propose deletions in a PR-ready diff. Anthropic's Nov 6 memory post: model-chosen notes beat fixed rules, and consolidation against transcripts fixes wrong notes [S22].

### 4.3 Standing-agent rules that keep usage predictable
- Tick prompts name a **turn/time bound** (a Desktop task has no `--max-turns`; a `/goal` clause bounds it [S14]; `CLAUDE_CODE_MAX_TURNS` exists in env-vars [S20]).
- Project agents `explore.md`, `scorecard-runner.md`, `rule1-reviewer.md` already exist in `.claude/agents/`. Give recurring ones `model:` in frontmatter and `memory: project`, `maxTurns` [S21].
- `CLAUDE_CODE_SUBAGENT_EFFORT=low` (v2.1.316) for read-only agents [S16].
- Same working directory for parallel sessions shares cache prefixes; each worktree builds its own [S17b]. Prefer **fewer worktrees**: only code-changing tasks (1) use one.

---

## 5. Context and compaction hygiene

| Rule | Why (source) |
|---|---|
| **Ticks are fresh sessions.** The coordinator never accumulates context | A day-old context re-sent on every request is the main hidden cost [S17] |
| `docs/handoff.md` top block is the only state a tick needs (under 15 lines: Now, Next, Blocked, Known risks) | Memory should be small, model-chosen and consolidated [S22]. SessionStart hook already prints branch/claims/handoff age |
| `PreCompact` hook already pins Rule #1, current task, decisions, branch. Keep it | Hook stdout becomes compaction instructions, capped at 10,000 chars [S10, S16] |
| `SessionStart` `compact` matcher already re-injects facts | [S10] |
| Chat session: `/rename` then `/clear` between topics; `/rewind` to drop a dead end (keeps cache); compact only while warm | [S17b, S17] |
| `autoCompactWindow: 400000` | Compacts earlier than the full 1M so each request stays cheaper [S20] |
| Leave `idleCompaction` on | A 200K+ idle conversation is summarized so the next message sends the summary, not the history [S20, S16] |
| Do not toggle model, MCP servers, or `/fast` mid-session | Each switch re-reads the whole history uncached. Opus 5.5 keeps cache on an effort change [S17b] |
| 1-hour cache is the main-conversation default on a subscription; subagents get 5 min | Leave `promptCacheTtl` unset. Setting `subagentPromptCacheTtl: "1h"` costs more per write and only pays if subagents idle 5-60 min [S17b] |
| Use `/usage` Loops rows and the `Prompt cache (main)` line weekly; press `f` to apply fixes | [S17] |
| Trim the 26k MCP-tool baseline (section 8) | Fresh ticks re-pay it [local] |

---

## 6. Notifications that reach Nick only for real decisions

**What counts as a decision (the only reasons to interrupt):**
1. A push, PR creation, or merge approval (the `ask` rules, one tap per batch).
2. A change to a default or gate (needs a `docs/memory/decisions.md` line).
3. Hardware test needing `data/.agent_hw_ok`, or a deploy needing `data/.agent_deploy_ok`.
4. A secret, token, or any cost above $0.
5. Usage at the 88% stop (one push, then silence).

**Everything else** goes to `docs/inbox.md` (and the "RedZone Inbox" artifact) and the single 08:03 digest.

| Channel | Setting | Value | Source |
|---|---|---|---|
| Phone push for prompts and questions | `agentPushNotifEnabled`, `inputNeededNotifEnabled`; `/config` labels "Push when Claude decides" and "Push when actions required" | On (first already true [local]) | [S11, S19] |
| Phone requirement | Claude mobile app signed in; Remote Control connected; on iOS, Focus and notification summaries can delay pushes (Settings, Notifications, Claude) | Allow Claude in the active Focus | [S11] |
| `PushNotification` tool | Desktop notification locally, phone push with Remote Control | Tick may use it for decisions only | [S15] |
| Desktop banners | `permission_request_notifications`, `question_notifications` | `banner` (keep) | [local] |
| Desktop banner for every tick | `task_complete_notifications` | **`off`** (18 ticks/day would be noise) | [local] |
| Skip pushes while Nick is at the Mac | `CLAUDE_CLIENT_PRESENCE_FILE` (v2.1.181+) needs a screen-lock listener to create/delete the file | **Skip.** Needs a third-party tool; low value | [S20] |
| Scheduled `/loop` sessions | Notify only when they need input | n/a | [S8] |
| Phone approval of a stalled prompt | Remote Control keeps prompts open until answered; Desktop approvals in Dispatch sessions expire after 30 min | Answer from the phone | [S11, S6] |

**Decision-card format (one line each in the inbox):** `Q: ... | A) ... B) ... | my pick: A because ... | needed by: <time> | cost if ignored: ...`.
**Rate cap:** 2 pushes/day outside the 88% case. A third waits for the digest.
**[unverified]** Whether a Desktop scheduled-task session is Remote-Control connected and can push to the phone. Test: run `rz-usage-watch` with "Run now" and a test push.

---

## 7. Risks

| Risk | Likelihood / impact | Control |
|---|---|---|
| **Runaway usage** | Medium / high. 18 Opus ticks/day, each re-paying about 82k baseline tokens (est. $0.4 list on the first uncached write at $5/MTok; cached reads at $0.20/MTok [A-pricing in prior note]) | Pace line (4.1), 88% hard stop, `rz-usage-watch`, AMBER/RED bands that skip Opus, `autoContinueAtUsageLimit: false`, extra usage stays **off** [local] |
| Cloud routines keep running on overage | Low (credits off) | Keep **usage credits off**; "None of these hourly limits has overage"; without credits, runs are rejected [S12] |
| Unsafe auto-approval of hardware | Low-medium | Section 2: ask on push, deny on adb forms, classifier soft_deny, hook holes closed, G3/G5 flag files only Nick creates |
| Bypass left in user settings by accident | Medium | If `defaultMode` stays `bypassPermissions`, the safety layer is only `deny`/`ask` + hooks. Nick's decision card |
| Auto-mode stall (3 consecutive or 20 blocks) | Medium | Phone push; `PermissionDenied` log; add narrow allow or `autoMode.environment`; fall back to bypass only with 2.4 fixes |
| Mac sleeps (lid, display off, manual) | Medium | Section 3.2. A skipped tick is harmless; one catch-up runs. A catch-up can fire at 23:00 for a 09:00 task, so prompts use the CATCHUP-OK guard [S4] |
| Desktop app restart or auto-update mid-tick | Medium | Tasks persist; the dead tick is retried next hour; state lives in handoff.md, not chat. Background processes of the dead session are not resumed [S5] |
| Disk and worktree sprawl (18 sessions/day, a worktree each) | Medium on an 8 GB Mac | `auto_archive_inactive_days` = 2; only tick #1 uses a worktree; Desktop removes archived worktrees' changes about 30 days later [S6]. `mcp__ccd_host__clean_up_worktrees` exists for tidy-ups |
| Heavy jobs on the Mac | Low | Guard G4; training prep only prepares; Kaggle needs Nick's credentials |
| Prompt injection via tool output or web pages | Low-medium | Classifier reads user messages and commands, not output [S3]; tasks never obey instructions found in fetched content; research task has no push rights |
| Skills/hooks written by a tick weaken the guard | Low | `.claude/**` and `data/.agent_*` are protected (G6) |
| Repeated-compaction loses Rule #1 | Low | PreCompact hook; Rule #1 is in `CLAUDE.md` and in hooks' output; durable boundaries use deny/ask, not chat [S3] |
| `permissions.denyInsteadOfPrompting` surprises | Unknown | **[unverified]** in Desktop; do not rely on it until tested |

---

## 8. Desktop and claude.ai settings worth changing

`set_setting` (the Desktop-prefs tool) shows Nick an **approval card per change** [local tool text]; the coordinator proposes, Nick taps. Items marked **Chrome** are on claude.ai in the browser, which Nick said the coordinator may drive. I could not open the signed-in claude.ai pages here, so UI labels come from docs and the settings tool, not from screenshots.

| # | Where | Setting | Value | Why |
|---|---|---|---|---|
| 1 | Desktop app, Settings, Code | **Task complete notifications** (`task_complete_notifications`) | `off` | 18 tick banners a day. Keep permission and question banners |
| 2 | Desktop, Settings, Code | **Prompt suggestions** (`prompt_suggestions`) | `false` | Each suggestion is one more model request; useless in unattended sessions [S17] |
| 3 | Desktop, Settings, Code | **Archive inactive sessions** (`auto_archive_inactive_days`) | `2` (picker offers 1, 2, 7, 14, 30) | Tick sessions pile up in the sidebar. Running sessions and ones with background work are never archived [local] |
| 4 | Desktop, Settings, Code | **Keep computer awake while Claude works** and **on battery** | keep `On` | Already On; holds only during work [local] |
| 5 | Desktop, Settings, Code | **Connect new sessions to Remote Control** | keep `On` | Phone window onto sessions [S6] |
| 6 | Desktop, Settings, Code | **Allow bypass permissions mode** | Nick's decision. Leave On now; switch off only if auto proves stable | Turning it off removes the unsafe fallback entirely [S1] |
| 7 | Desktop, Settings, Code | **Worktree location**, **Branch prefix** (`claude`) | keep | Branch prefix `claude/` matches cloud routine pushes [S12, local] |
| 8 | Desktop, Scheduled | Create tasks 1-4, 6, 7; per task: permission mode **Auto**, model as in section 4, **Worktree** only for #1 | | [S4] |
| 9 | claude.ai, `/settings/usage` (**Chrome**) | Confirm **usage credits stay off**; read weekly bars | Off | $0 rule. Overage would let cloud routines keep spending [S12, S17] |
| 10 | claude.ai, `/code/routines` (**Chrome**; Nick must grant GitHub access) | Create `rz-research-sweep` as a cloud scheduled task | schedule `17 2 * * 1,4` local | Runs with the Mac off [S12] |
| 11 | Desktop, Customize (sidebar) or `/mcp` | **Disable unused connectors** (Figma, Gmail, etc.) | Off unless a task needs them | MCP tool definitions were 26k tokens of every fresh tick [local]. Tool search defers definitions, but names and instructions still load [S17] |
| 12 | Phone, Claude app | Notifications allowed; Focus mode exception for Claude | On | Without it pushes are delayed [S11] |
| 13 | claude.ai Settings (**Chrome**), skills/plugins sync | Review `syncClaudeAiSkills`/`syncClaudeAiPlugins` only if tick sessions load unwanted skills | Optional | Skills enabled for the account also load in local sessions [S6, S7] |

Not recommended: turning on **fast mode** (usage credits only on a subscription, breaks $0 [prior note, D4]), **agent teams** (about 7x tokens [S17]), or **Dispatch**-spawned sessions (approvals expire in 30 min [S6]).

---

## 9. Step list

### 9.1 Coordinator can do now (no approval beyond normal tool prompts)
1. Write `docs/inbox.md` (decision-card template) and `data/` state file conventions (`usage_state.json`, `usage_pause`, `denials.jsonl`). About 10 min.
2. Write the seven task prompts into `docs/agents/wave1/tick-prompts/*.md` so Nick can paste or approve creation. About 15 min.
3. Draft the hardened `guard.py` and `log_denial.py` as a **patch file** in `docs/agents/wave1/patches/` (cannot edit `.claude/**`: G6). About 30 min.
4. Start `caffeinate -i -w <Claude pid>` (no sudo). About 1 min.
5. Run `claude auto-mode defaults` and `claude auto-mode critique` against the proposed block (read-only) and report. About 5 min.
6. After Nick's yes: create the scheduled tasks with the scheduled-tasks tool; click **Run now** on `rz-usage-watch`, test a push, check task history. About 20 min.
7. After Nick's yes: propose the Desktop prefs (section 8 items 1-3), one card each.
8. Chrome, read-only: open claude.ai/settings/usage and confirm credits are off; report real labels so this note's **[unverified]** UI names can be fixed.

### 9.2 Nick must click (about 15 minutes total, once)
1. **Mode selector:** in the Code tab, pick **Auto** for the RedZone folder (remembered per folder, overrides project settings [S1, S6]). 10 seconds.
2. **User settings:** paste block A (section 2.3) into `~/.claude/settings.json`, merging with what is there. Restart the Chat session. About 5 min. (Coordinator must not edit user settings.)
3. **Approve** each `set_setting` card (3 cards) and the scheduled-task creation (one yes).
4. **PR:** review and merge the hardened `.claude/settings.json` and `guard.py` patch into `integration/next` (agents cannot touch `.claude/**`). About 10 min.
5. **Phone:** open the Claude app once so the push token registers; allow notifications; add Claude to the Focus exceptions. About 3 min.
6. **claude.ai/code/routines:** connect GitHub so the cloud research routine can clone and push to `claude/` branches. About 3 min.
7. **Only when you want a hardware test or deploy:** create `data/.agent_hw_ok` or `data/.agent_deploy_ok` yourself, and delete it after.
8. **Optional:** `sudo pmset -a sleep 0` in Terminal if a tick is ever skipped for sleep. Default: do nothing.

### 9.3 Decision cards for Nick (answer when convenient)
- Stay on `bypassPermissions` or move to **Auto** (pick: Auto).
- Hourly ticks 06-23 or every 2 h (pick: hourly for week 1, then read `/usage` Loops rows and adjust).
- Allow the coordinator to push after one batched tap (pick: yes, via the `ask` rule).

**Next action under two minutes:** in the Code tab, open the mode selector (Cmd+Shift+M) for the RedZone folder and choose **Auto**.

---

## 10. Unverified items (test before trusting)

1. `permissions.denyInsteadOfPrompting` in Desktop scheduled sessions (changelog v2.1.319 only; not in the settings reference excerpts I could read).
2. `worktree.baseRef` accepted values and the default for scheduled-task worktrees (index entry only: "remote default branch or your local HEAD").
3. `CLAUDE_CODE_LOOP_KEEPALIVE` (changelog only; not found in the env-vars excerpt read).
4. Whether `get_usage` and `PushNotification` work inside a Desktop scheduled session, and whether that session is Remote-Control connected.
5. `mcp__computer-use__*` glob in deny rules; exact auto-compact default for Opus 5.5.
6. Desktop shows or does not show `claude --bg` sessions.
7. Every claude.ai UI label in section 8 (no signed-in page was opened).
8. Dollar estimates ("est.") are list-price proxies, not billed money.

---

## 11. Bibliography

All opened on 2026-11-08. Claude Code docs have no publication date; "current" = live page, versions from the changelog. **[local]** = observed on this Mac.

**Claude Code docs (https://code.claude.com/docs/en/...)**
- [S1] Permission modes. `/permission-modes`. Current, v2.1.319 era. Modes, auto-mode limits (3 in a row / 20 total), bypass rules, Desktop selector.
- [S2] Configure permissions. `/permissions`. Current. Deny > ask > allow, wildcard and compound-command rules, hook precedence.
- [S3] Configure auto mode. `/auto-mode-config`. Current. `autoMode` keys, `$defaults`, `classifyAllShell`, `auto-mode` CLI, `permissions.ask` recipe.
- [S4] Schedule recurring tasks in Claude Code Desktop. `/desktop-scheduled-tasks`. Current (Desktop 1.1.5368+). Cadences, missed runs, permissions, SKILL.md location.
- [S5] Run prompts on a schedule. `/scheduled-tasks`. Current. `/loop`, CronCreate, 7-day expiry, jitter, limitations, `CLAUDE_CODE_DISABLE_CRON`.
- [S6] Use Claude Code Desktop. `/desktop`. Current. Mode selector, bypass toggle, Remote Control switch, worktrees, notifications, Background tasks pane.
- [S7] Settings files and precedence. `/settings`. Current. Scopes, precedence, list merge, keys ignored from project files.
- [S8] Agent view and background sessions. `/agent-view`. Current (research preview). Supervisor, sleep/reboot behavior, `claude agents`.
- [S9] Tools reference (Monitor section). `/tools-reference`. Current. Monitor deadlines (5/30 min), ScheduleWakeup, PushNotification.
- [S10] Hooks reference. `/hooks`. Current. Events, exit codes, `onFailure`, Notification types, Monitor matcher caveat.
- [S11] Remote Control. `/remote-control`. Current. Push toggles, presence file, reconnect and 10-minute server-mode limit.
- [S12] Automate work with scheduled tasks (cloud routines). `/routines`. Current (research preview). Hourly caps, usage, `claude/` branches, auto mode.
- [S13] Interactive mode, "Wait for a usage limit to reset". `/interactive-mode`. Current (v2.1.234+). Auto-continue behavior and its 24 h and 3-try limits.
- [S14] Keep Claude working toward a goal. `/goal`. Current. Turn-bound clause, check-ins, resume.
- [S15] Tools reference, PushNotification row (same page as S9).
- [S16] Changelog. `/changelog`. Latest entry v2.1.319, 2026-11-06 (v2.1.309 on 2026-10-22 onward). Self-paced `/loop` change, `denyInsteadOfPrompting`, idle compaction, Monitor and auto-mode changes.
- [S17] Manage costs effectively. `/costs`. Current. `/usage` breakdown, why long sessions cost more, background token use.
- [S17b] How Claude Code uses prompt caching. `/prompt-caching`. Current. TTL buckets, invalidating actions, idle compaction, cache scope.
- [S18] Bash sandbox. `/sandboxing`. Current. Seatbelt, auto-allow, limitations.
- [S19] Settings reference. `/settings-reference`. Current. Keys: `idleCompaction`, `autoContinueAtUsageLimit`, `autoCompactWindow`, `permissions.defaultMode`, `promptCacheTtl`, index entries for notification and worktree keys (partially read; 150 KB unread).
- [S20] Environment variables. `/env-vars`. Current (partially read). `CLAUDE_CLIENT_PRESENCE_FILE`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_CODE_MAX_TURNS`, `CLAUDE_CODE_GOAL_CHECKIN_MINUTES`, `CLAUDE_CODE_DISABLE_CRON`.
- [S21] Create custom subagents. `/sub-agents`. Current. Frontmatter fields, background permission behavior, depth and concurrency limits.
- [S23] Model configuration. `/model-config`. Current (partially read). 1M window default on Opus 4.7+, `/autocompact`.

**Anthropic**
- [S22] L. Martin, "The components of agent memory," anthropic.com/research/components-of-agent-memory, 2026-11-06. Small always-loaded memory, model-chosen notes, consolidation passes, one page per entity.

**Local observations, 2026-11-08 [local]**
- [L1] Claude Desktop `get_settings` (Code-tab prefs, read-only).
- [L2] Claude Desktop `get_usage`: weekly 45%, reset 2026-11-11 21:00 UTC, extra usage disabled, this session 72% of 1M.
- [L3] `pmset -g`, `pmset -g custom`, `pmset -g assertions`; `man caffeinate`; `man pmset` ("must be run as root in order to modify any settings").
- [L4] `~/.claude/settings.json` key names and hook commands (values not printed); `.claude/settings.json` and `.claude/hooks/*.py` in this worktree.
- [L5] WebSearch for Oct-Nov 2026 press coverage: returned only March-April 2026 articles. Not used as evidence.
