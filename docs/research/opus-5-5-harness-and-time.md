# Opus 5.5 + Claude Code: spend less, wait less, test without a live game

Research note, 2026-11-08. It builds on [agent-coordination-and-rationale.md](agent-coordination-and-rationale.md)
and does not repeat it: repo maps, CAID or CooperBench coordination, ADRs, AGENTS.md studies and
StateFlow are covered there. This note covers five things: the Claude Code harness as it is in Oct-Nov 2026,
the weekly cap, the edit → live-game loop, more ways to record "why", and ADHD-friendly async work.

**How to read it.**
- **Sources.** Each source was opened on 2026-11-08. Sources dated before October 2026 are marked **[older]**.
  The Claude Code docs pages have no publication date; they are the live docs as read on 2026-11-08, and the
  feature versions come from the changelog (v2.1.309 on 2026-10-22 through v2.1.319 on 2026-11-06).
- **Estimates.** Hour and saving figures marked *(est.)* are my estimates for RedZone. No measured RedZone
  result is in this note.
- **Prices.** Dollar prices are API list prices. On a subscription they are a proxy for how fast the weekly
  cap drains, not money spent.

---

## 1. Top 10 actions, ranked by time saved per hour of setup

| # | Action | Setup *(est.)* | Saves *(est.)* | Cost | Source |
|---|---|---|---|---|---|
| 1 | **Turn on mobile push for prompts.** Run `/remote-control`, then `/config` → "Push when actions required". Set `CLAUDE_CLIENT_PRESENCE_FILE` so pushes come only when Nick is away from the Mac. | 15 min | 30-60 min/day of sessions stalled on a prompt nobody saw | $0 | Remote Control docs [D9] |
| 2 | **Route reads and cheap work off Opus.** Add a project agent named `Explore` with `model: haiku`. On a subscription, the built-in Explore runs on Opus by default. Also add a `scoper` (Haiku), an `implementer` (Sonnet 5.5) and `CLAUDE_CODE_SUBAGENT_EFFORT=low` for the read-only agents. | 30 min | About 40% of usage per PR (Grandi measured $3.59 → $2.14 per PR) | $0 | Subagents docs [D6], Grandi [P1], pricing [A3] |
| 3 | **Fix the session hygiene that breaks the cache.** Pick model and effort once at session start. Never toggle `/fast`. Use `/rewind` to drop a dead end rather than `/compact`. Use `/clear` between tasks. Run `/usage` weekly and press `f`. Show the cache hit % in the status line. | 30 min | 10-25% of weekly usage | $0 | Prompt caching [D2], costs [D1] |
| 4 | **Use Monitor, not polling, for the 12-minute deploy health watch.** Claude starts `scripts/deploy.py` and streams `data/deploy/deploy.jsonl` through Monitor. Use push on exit codes 3/4/5. No `/loop 1m` and no sleep-poll. | 30 min | A watched deploy is about 1 turn of cost instead of 12; Nick doesn't have to watch | $0 | Monitor [D15], scheduled tasks [D7] |
| 5 | **"Night in a box" replay gate (phase 1 of §3).** Replay the last N recorded nights through the real decider with fake sticks, at full speed, inside `deploy.py --dry-run`. Refuse the deploy if `entries_mid_play`, `plays_missed_entry` or `dead_air_pct` gets worse. | 8-10 h | Most "found it live" bugs move to minutes after the edit. Each one avoided saves a game window plus a 12-min deploy | $0 | Chronicle [R4], DST guide [T1], simloop [T2] |
| 6 | **Auto mode with hard edges.** `permissions.ask` on `git push` and `scripts/deploy.py deploy`. `permissions.deny` on the stick hosts. A PreToolUse hook that blocks `adb` to the live sticks while `GET /api/machines` says a game is on screen. | 45 min | Most approval waits gone, with no TV disruption | $0 | Auto mode config [D14], hooks [D12] |
| 7 | **`/goal` with a provable end and a turn cap** for unattended work, e.g. `/goal pytest -q passes and replay gate shows 0 mid-play entries, or stop after 25 turns`. | 10 min each | Fewer "it stopped and waited" gaps overnight | $0 (small model evaluates) | `/goal` docs [D10], Codeman [P2] |
| 8 | **Candidate-in-shadow on live nights (phase 3 of §3).** Generalize `buffer_shadow.py` so any candidate decider runs beside the live one and logs its decisions without touching a stick. Load it as a separate light process, so no app restart is needed. | 5-6 h | Most decider changes skip one deploy-and-watch cycle; each live night grades 1-3 candidates instead of 1 | $0 | Shadow/canary practice [P5] **[older]**, existing `buffer_shadow.py` |
| 9 | **Why-not ledger and `scripts/why.py <time>`.** Each tick writes a compact row: candidates, chosen action, every veto by rule name, and code SHA. `why.py` answers "why didn't it cut at 8:42?" | 4 h | 20-40 min of log digging per live bug | $0 | Semantic provenance why-not [R7], NovaFabric [R5] |
| 10 | **Cheap scheduled jobs.** A Desktop scheduled task at 07:00 runs the night report and replay sweep in a worktree and pushes one summary. Use a cloud routine only for jobs that need no local data. | 1 h | A morning triage step Nick doesn't have to start | $0, but draws on plan usage | Desktop scheduled tasks [D8], scheduled tasks [D7] |

Not on the list on purpose:
- **Fast mode.** On subscriptions it bills to usage credits only, which is real money and breaks the $0 rule [D4].
- **Ultracode, agent teams and big workflows.** Ultracode raises usage per request [D5]. Teams use about 7x the
  tokens of a standard session in plan mode [D1]. Use workflows only with the `small` size guideline and only
  when asked.

---

## 2. Findings by question

### Q1. Opus 5.5 and the Claude Code harness, Oct-Nov 2026

**The model.**
- Opus 5.5 was released on 2026-09-22 **[older, the only primary source]** [A1]. API prices: $4 input,
  $20 output, $5 cache write (5 min), $8 cache write (1 h) and $0.20 cache read per MTok [A3]. That cache read
  is 0.05x base, against the usual 0.1x.
- Anthropic reports output more than 30% faster than Opus 5, and about 40% lower cost on typical workloads at
  default settings [A1].
- Opus 5.5 defaults to **medium** effort in Claude Code. Other effort-capable models default to `high` [D3].
  `ultrathink` runs one turn at `xhigh` (v2.1.311) [D16].
- Thinking cannot be turned off on Opus 5.5 [D1].
- Opus 5.5, Sonnet 5.5 and Haiku 5.5 keep the prompt cache when you change effort mid-session on a
  subscription. Other models rebuild it [D2].

**Features that matter here**, with each one's effect on usage and on wall-clock time:

| Feature | What it does | Usage effect | Time effect |
|---|---|---|---|
| Subagents (background by default) [D6] | Fresh context. Model order: per-call param > frontmatter > `CLAUDE_CODE_SUBAGENT_MODEL` > session model. 20 concurrent by default. | Adds requests, but keeps the parent's context small, which is where most savings come from [P1]. 5-min cache TTL by default. | Parallel |
| Forks [D6, D2] | Inherit the parent's system prompt, tools and history. The first request reads the parent's cache. | Cheap to start, but carries the whole parent context | Parallel |
| `isolation: worktree` [D6] | Temporary worktree, branched from the **default branch**, not the parent's HEAD | The cache is scoped per directory, so each worktree session builds its own prefix [D2] | Safe parallel edits |
| Dynamic workflows [D5] | A JavaScript script fans out agents. Up to 16 concurrent agents and 1,000 per run. Resumable. Pauses at a usage limit only if the reset is within 24 h. | Large; "Large workflow" warns above 25 agents or 1.5M tokens. Pro defaults to the `small` size. | Big fan-outs, background |
| Skills [D1] | Load on demand | Move rare instructions out of CLAUDE.md. The docs suggest keeping CLAUDE.md under 200 lines. | — |
| Hooks [D12] | 30+ events. Types: command, http, prompt, agent, mcp_tool. | A PreToolUse hook can filter test output to failures only [D1] | Automates gates |
| Memory [D6, A2] | `memory: project` on a subagent loads the first 200 lines or 25 KB of its `MEMORY.md` | Small and always loaded | Fewer re-explanations |
| Plan mode [D2, A4] | Implemented as a tool plus a message, so it doesn't break the cache. `opusplan` does switch models, so it **does** break the cache. | Use plan mode; avoid `opusplan` | Less rework |
| Compaction [D2] | A warm `/compact` is cheap. A cold one (after the TTL) re-reads everything uncached. Idle compaction runs in Desktop for 200K+ idle conversations (v2.1.314). | Compact at task breaks; `/rewind` instead of compacting a dead end | — |
| Prompt cache TTL [D2] | On a subscription: 1 h for the main conversation, 5 min for subagents, workflows, forks and compaction. Settings `promptCacheTtl` and `subagentPromptCacheTtl`. | A subagent idle past 5 min re-reads uncached. v2.1.318 moves a subagent's shell command to the background after 3 min to keep its cache warm [D16]. | — |
| Background tasks, `/tasks` [D16] | Finished subagents can be reopened and messaged (v2.1.319) | Resuming reuses the warmed cache [D2] | — |
| Monitor [D15] | Streams each line of a background script or WebSocket into the session | The docs call it often more token-efficient than re-running a prompt on an interval [D7] | Reacts at once |
| `/loop`, CronCreate [D7] | Session-scoped; 1-min minimum; expires after 7 days | **Each fire sends the full context** [D1] | — |
| Desktop scheduled tasks [D8] | Fresh local session on a schedule. Optional worktree. Runs only while the app is open and the Mac is awake; one catch-up run after sleep. | One fresh session per run | Unattended |
| Cloud routines [D7, D8] | Run on a fresh clone with the Mac off; 1-h minimum | Plan usage; no local `data/` | Unattended |
| `/goal` [D10] | A small model checks a condition after each turn. At most 3 idle check-ins per goal. | Small evaluator cost | Fewer stalls |
| Auto mode [D14] | A classifier approves routine calls. `permissions.ask` and `deny` run before it. `autoMode` is read only from user or managed settings, never from the repo. | Slight cost per classified call | Removes most prompts |
| Remote Control and push [D9] | Phone steers the local session; push on "actions required" | — | Unblocks from anywhere |
| Fast mode [D4] | Up to 2.5x faster on Opus 5.5 at $8/$40 per MTok. **Subscription: usage credits only.** | Real money | Faster, but off-limits ($0) |
| Artifacts [D16] | Pages from the Artifact tool | — | Good as a morning "inbox" page |

**Where the cap and the clock go.**
- **Usage savers, biggest first:**
  1. Keep the main context small: clear between tasks and delegate verbose reads.
  2. Route subagents to Haiku or Sonnet.
  3. Keep the cache warm and the prefix stable.
  4. Use Monitor instead of polling.
- **Time savers:**
  1. Push approvals to the phone.
  2. Run background subagents in parallel.
  3. Use `/goal` and auto mode for unattended turns.
  4. Run replay tests locally (§3), which is the biggest lever by far.

**Evidence that harness overhead dominates (Oct 2026).**
- *What Does a Harness Buy? Tokens, Mostly* ran five models through Claude Code, mini-SWE-agent and OpenCode on
  SWE-bench Verified [R1]. The heaviest and lightest harnesses scored within 5 points of each other, but cost per
  task varied up to 3x. The authors trace most of the gap to "fixed preamble × number of steps".
- The lesson for us: fewer steps and a smaller always-loaded preamble (CLAUDE.md, MCP, tools) beat clever
  prompting.

### Q2. Getting the most from a weekly cap

**What costs the most**, from the docs [D1, D2] and the papers [R1-R3]:
1. **Long-lived contexts.** Every turn and every tool batch re-sends the whole history. Cached, it bills at the
   cache rate; uncached, at the full rate. A one-line question in an all-day session pays for the whole day.
2. **Cache misses.** These come from switching model or fast mode, adding or removing MCP servers, `opusplan`
   toggles, upgrading Claude Code mid-week, accumulating many images, and coming back after the TTL. Two
   cases matter here:
   - Subagents have only a 5-min TTL on a subscription, so a subagent that idles longer re-reads uncached.
   - Each worktree directory has its own cache prefix [D2].
3. **Background firing.** `/loop` and cron fires, cross-session messages and goal check-ins each send the full
   context while idle [D1].
4. **Fan-out.** Each subagent and workflow agent pays its own preamble. Agent teams are about 7x [D1].
5. **Explore on Opus.** On a subscription the built-in Explore runs on Opus [D6]. Haiku 5.5 is $0.10 input and
   $0.01 cache read per MTok, against $4 and $0.20 on Opus 5.5 [A3]: 40x cheaper on fresh input and 20x on
   cache reads.
6. **Failure loops and re-reads.**
   - TokenEconomizer cut token cost 18.4-30.9% across Claude Code, Codex and Copilot CLI by injecting small
     skills, pruning irrelevant tools and stopping unproductive loops [R2].
   - HarnessMM cut tokens by reusing earlier decisions instead of re-asking the model: 66.7k → 47.0k per task
     on HumanEval, 96.3k → 11.6k on one harness [R3].

**Tactics, with numbers where they exist:**

| Tactic | Number | Source |
|---|---|---|
| Opus main session (decide and review) + Sonnet implementer + Haiku scoper and shipper | -40% per PR; about 10 → 16 PRs per 5-h window; context per request 119k → 107k | [P1] |
| Custom `Explore` agent with `model: haiku` (`CLAUDE_CODE_SUBAGENT_MODEL` alone doesn't change Explore without `..._FORCE=1`) | Haiku costs 1/40 of Opus input | [D6, A3] |
| Fix model and effort at session start; on Opus 5.5 an effort change keeps the cache | Avoids a full uncached re-read | [D2] |
| `/rewind` instead of `/compact` to abandon a path; `/recap` keeps the cache | — | [D2] |
| Compact only while warm (within 1 h); `/clear` is free | A cold compact re-reads the whole history at full price | [D2] |
| CLAUDE.md under 200 lines; rare workflows as skills; `/usage` → `f` | `/usage` rows flag anything ≥10% of usage | [D1] |
| PreToolUse hook that trims pytest output to failures | "tens of thousands of tokens to hundreds" | [D1] |
| Monitor instead of `/loop 1m` for deploy and log watches | One streamed event per change instead of a full-context turn per minute | [D7, D15] |
| `CLAUDE_CODE_SUBAGENT_EFFORT=low` for read-only agents (v2.1.316) | — | [D16] |
| Workflows: `workflowSizeGuideline=small` (fewer than 5 agents) | Bounded fan-out | [D5] |
| Keep parallel sessions that share context in the **same directory** | Matching prefixes share the cache | [D2] |
| Prefer CLI tools (`gh`) over MCP servers | No per-tool listing | [D1] |

**RedZone-specific tactics** *(est.)*:
- `context_pack.py` is the right idea. Check that its output stays under about 3k tokens and is the *only*
  thing a new session reads first.
- Use `scripts/citymap.py --impact` (from the earlier note) through a Haiku scoper, not an Opus read of 20 files.
- Split files over about 1,500 lines (`decider.py` is past line 2,800). Every Read of a huge file is a large
  uncached input on first touch.
- At 88% weekly: the usage-pause runbook already stops work. Add a `SessionStart` hook that prints the runbook
  line, so no new session starts blind.

### Q3. Cutting the edit → live-game → bug loop

**What others do (2026):**
- **Deterministic simulation testing (DST).** Swap the clock, randomness, network and disk for simulated
  versions driven by one seed. Run thousands of seeds, and save any failing seed as a regression [T1]
  (Oct 22, 2026).
  - Python options: `simloop` runs asyncio with seeded scheduling, virtual time, fault injection and exact
    replay from a seed. It *fences* threads, subprocesses and raw sockets [T2].
  - `looptime` fast-forwards asyncio time in pytest, but does not touch `time.time()` in synchronous code [T3]
    **[undated]**.
  - Antithesis does the same for whole systems with a hypervisor. In WarpStream's case, 6 wall-clock hours
    simulated 280 application hours, and it found a race that tens of thousands of CI hours had missed [T4]
    **[older, 2024]**.
- **Anthropic at scale.** When code changes landed faster than humans could gate them, Anthropic built hermetic
  simulations and fuzzing, and moved the safety net to automated post-merge and pre-deploy testing [A5]
  (Oct 13, 2026). This is the same move as §3, at a much larger scale.
- **Cut-point replay.** Record a run at its non-deterministic boundaries. Then replay some boundaries from the
  recording and run the rest live with new code, which turns a recorded failure into a CI regression test.
  Chronicle reports recording overhead of about 23 µs per boundary, full replay bit-stable across 20 runs,
  and all 6 recorded incidents caught [R4] **[older, Sep 2026]**.
  - The NovaFabric caveat: replay that substitutes only model responses completed just 2 of 10 tool-using
    workloads. Tool responses must be recorded too [R5] **[older, Sep 2026]**.
- **Fake devices.**
  - Amazon has no Fire TV emulator. The options are an Android TV AVD, or Amazon's cloud Fire TV farm
    ("Live Device Interaction") with an adb shell [T5] **[older, Jun 2026]**.
  - Both are too heavy or too remote for an 8 GB Mac. A **behavioral twin** in Python, fed by recorded
    latencies, is the practical choice *(est.)*.
- **Shadow, canary, replay.** These are complementary, not substitutes: contract tests in CI, a replay corpus at
  merge, a canary at ship [P5] **[older]**. Shadow means the new version sees real inputs and its outputs are
  discarded.
- **Hot reload.** `jurigged` patches function code objects in a running process. Running loops, generators and
  async functions keep the old code, and base-class changes need a restart [T6] **[older]**.
  - The consensus is not to hot-patch production. Use it only in the simulator.
  - For the live app, reload *parameters* (config) and run *code* candidates in shadow (§3) *(est.)*.

**What RedZone already has**, in the worktree:
- `recorder.py`: feed lag and the source race per play.
- `replay_check.py`: replays one archived date without feeds or devices.
- `buffered_jump_sim.py`: Rule #1 counts on recorded NFL slates.
- `buffer_shadow.py`: a second decider that never touches a stick.
- `trace.py`: one trace per switch.
- `tests/fake_adb.py`: a fake adb binary for tests.

**What is missing:**
1. A **clock seam**. About 520 direct `time.time()` and `time.monotonic()` calls under `redzone/` mean virtual
   time can't drive the engine.
2. A **stick tape**: recorded adb latencies and failures.
3. A **stateful stick/TV twin**.
4. One command that replays a *whole night* end to end and scores it.

§3 designs these.

### Q4. Capturing why, and how things connect (beyond the earlier note)

1. **A why-not ledger, not just a decision log.**
   - Guardrails often expose only accept or reject. Developers need the *refusal evidence* [R7] (ESWC'26
     workshop, published 2026-06-09, **[older]**). That paper answers why, why-not and impact questions over a
     PROV-aligned trace.
   - RedZone already names jump vetoes (commit e1bab0fc). The next step is one row per decide tick:
     `{t, sha, game_on_screen, candidates[{game, ev}], chosen, vetoes[{rule, target, detail}], leverage}`.
     Add a query CLI (`why.py 20:42`) that prints the chain in plain words.
2. **Version provenance on every row.** Stamp the git SHA, config revision and deploy ID into every decision
   row and switch trace. Then any live bug maps to the exact code, and replay can check out that SHA.
3. **One trace from agent to app.**
   - Claude Code exports OpenTelemetry traces (beta): `claude_code.interaction` → `llm_request` / `tool` →
     `tool.execution`. Bash subprocesses inherit `TRACEPARENT` [D13].
   - If `deploy.py` reads `TRACEPARENT` and writes it into `deploy.jsonl`, an agent session links to the deploy,
     the code version and the night's decisions.
   - Use the OTel GenAI agent-span names (`invoke_agent`, `plan`) as vocabulary even without a collector. The
     spec is still marked "Development" [T7].
   - A full collector costs RAM; keep this file-based on the Mac *(est.)*.
4. **Fitness functions in the agent's verify command, not only in CI.**
   - Encode the boundaries as import-linter or Tach contracts, e.g. "only `redzone/tuners` imports adb" and
     "`decider` never imports `tuners`". Run them in the same command the agent runs after each edit, so the
     agent learns before the PR [T8] **[older]**.
   - Treat the Rule #1 metrics from the replay gate as *behavioral* fitness functions.
5. **Approvals as receipts that expire.** In Koch's transition contract, evidence is bound to the exact
   artifact and policy baseline, approvals are scoped and expire, and authorization is re-checked at merge or
   deploy [R6] **[older, Sep 2026]**. For RedZone: a decisions.md entry that authorizes a risky default gets a
   `scope:` and an `expires:` field, and `check_memory.py` flags expired ones.
6. **Agent memory that stays small and true.** Anthropic, 2026-11-06 [A2]:
   - Keep injected memory small.
   - Let the model choose what to save. A fixed "record only failures" rule *lowered* scores.
   - Consolidate notes into one reference and mark which facts are confirmed.
   - "Dreaming" passes check notes against transcripts.
   - Wiki plus search beat search alone (61% vs 53%) and saved about 20k tokens per question.

   For RedZone:
   - Give the recurring subagents `memory: project`.
   - Add a weekly scheduled "consolidate lessons.md against last week's transcripts" task.
   - Keep `handoff.md` as the one always-loaded page.
7. **Session journals by hook.**
   - A `Stop` hook appends one line per finished task to `docs/memory/journal/YYYY-MM-DD.md`: goal, files, result, next.
   - A `PreCompact` hook's stdout becomes compaction instructions, capped at 10,000 characters [D12, D16]. Use it to
     protect the Rule #1 constraints and open risks from being summarized away.
   - A `SessionStart` hook with matcher `compact` re-injects them [D12].

### Q5. An async workflow for an ADHD solo developer

**Notifications that find Nick:**
- **Remote Control push** with "Push when actions required" for permission prompts and questions, and "Push
  when Claude decides" for task done [D9].
- Set `CLAUDE_CLIENT_PRESENCE_FILE`, so pushes stop while he is at the Mac [D9].
- Approve permission prompts from the phone. Remote Control keeps permission prompts open until answered [D9].

**Chat in, from the phone.**
- **Channels** push messages into a running session [D11].
- iMessage self-chat needs no bot token, but it needs Full Disk Access for the terminal. That is a macOS
  security setting **Nick must grant himself**, so only if he wants it.
- Telegram needs a bot token, which **Nick adds himself** per CLAUDE.md.
- Channels are in research preview.

**One-tap gates.**
- Keep `permissions.ask` for push and deploy. Everything else goes through auto mode [D14].
- Third-party phone-approval tools exist, e.g. Pushary: lock-screen approve or deny, a kill switch and a daily
  budget [P4] **[older, updated Sep 28]**. The built-in push covers the core need at $0.

**Batching.**
- A guide on coding with ADHD recommends one task per agent and letting notifications pick which needs
  attention. It warns that "fire off ten tasks" can become avoidance [P3] **[older, Jun 2026]**.
- Cap it at the CLAUDE.md limit of 5 threads, and publish one morning "inbox" page (an Artifact or
  `night_report`) that lists only decisions waiting on Nick.

**Overnight autonomy that is safe** [P2, D8, D10, D14]:
- A Desktop scheduled task in its own **worktree**.
- Auto mode, with `deny` on stick hosts and `ask` on push and deploy.
- `/goal ... or stop after N turns`.
- Constraints and progress kept in repo files, not chat, because compaction can drop chat-only rules [D14, P2].
- A morning push.
- `caffeinate` is irrelevant here, since the Mac already stays on for the live app.
- **Never** run heavy jobs locally. Replay is light (one process, about 200 MB *(est.)*). Seed sweeps over 100 go
  to Kaggle.

**Usage limits.**
- Workflows pause at a usage limit only when the reset is within 24 hours [D5]. A weekly limit can be further out.
- The 88% stop rule fires well before that anyway, so do not count on auto-continue.

---

## 3. The "RedZone time machine": replay harness, fake sticks, shadow mode

**Goal:** most bugs are found within 5 minutes of an edit, on recorded nights, with no TV and no deploy. Live
games are spent only on what a replay cannot know.

```
                 recorded night (data/recordings/<date>/, data/archive/, stick tape)
                                        │
   virtual clock ──► feeds replayer ──► ENGINE + DECIDER (real code) ──► controller ──► FakeStick ×2 + FakeTV
        ▲                                     │                                         (latency/failures
        │                                     ▼                                          sampled from tape)
   seed (faults, jitter)              why-not ledger rows ──► scorer vs truth (archive PBP wallclocks)
                                                               └─► entries_mid_play, plays_missed_entry,
                                                                   dead_air_pct, high-leverage leaves
```

| Phase | Step | What | Hours *(est.)* |
|---|---|---|---|
| **1 MVP** | 1.1 | `redzone/clock.py`: `now()`, `mono()`, `sleep()`, with a `set_clock(VirtualClock)` for tests. Codemod only the engine/decider/controller path first, about 100 of the 520 call sites. Add a lint rule banning new `time.time()` in those packages. | 3 |
| | 1.2 | `scripts/replay_night.py <date>`: drive the engine from `plays.jsonl` / `race.jsonl` / archive play-by-play at virtual time. Sticks are the existing `tests/fake_adb.py` with fixed latencies. Reuse `replay_check.py` loaders and the `buffered_jump_sim.py` truth model. | 4 |
| | 1.3 | Scorer: Rule #1 metrics against truth. JSON output plus a before/after table, the PR template format. | 1.5 |
| | 1.4 | Gate: `deploy.py --dry-run` replays the last 3 nights and fails on any regression in the hard metrics. | 1 |
| **2 Twin** | 2.1 | **Stick tape.** Wrap `AdbShell` so live nights log `{t, stick, cmd_kind, latency_ms, outcome}` to `data/recordings/<date>/sticks.jsonl`. Log command *kinds*, never hosts, because the repo is public. | 2 |
| | 2.2 | **FakeStick and FakeTV state machines.** App and channel, playback position and stream delay, HDMI input, dialogs, offline. Sample latency and failures from the tape. Reuse the states in `redzone/machines/` so the twin and the real FSM share names. | 5 |
| | 2.3 | **Seeded faults:** feed-lag jitter, tune time, stick offline, dialog pop-ups. `--seeds N` sweep. Save failing seeds to `tests/replay_seeds.json`. | 2 |
| **3 Live** | 3.1 | **Cut-point freezer.** `scripts/freeze_bug.py <date> <HH:MM> ±10m` saves that window's inputs plus the stick tape as a pytest fixture. The bug becomes a test before anyone fixes it (Chronicle pattern [R4]). | 2.5 |
| | 3.2 | **Shadow candidate.** Generalize `buffer_shadow.py` into a separate low-memory process. It reads the live app's state (existing `/api` endpoints), runs a candidate decider from a git ref, and writes `data/shadow/<ref>.jsonl`. No stick access is possible: the `deny` hook plus no adb import. | 5 |
| | 3.3 | **Parameter hot reload.** Tunables change through the existing config revision path with no restart. Code changes go through the shadow, then a normal deploy at a dead time. No `jurigged` in the live app. | 1.5 |
| | 3.4 | **Hardware-truth probes only.** A short scheduled probe on the real sticks (tune latency, HDMI flip) runs only when no game is on and Nick isn't watching. It refreshes the stick tape weekly. | 2 |
| **4 Scale** | 4.1 | Nightly sweep: 3 nights × 20 seeds locally, light. 100+ seeds on Kaggle with eval-data-v1. | 2 |
| | 4.2 | Why-not ledger and `why.py`: the Q4 item, shared with replay output. | 4 |

- **Totals:** Phase 1 is about 9.5 h. All phases come to about 39 h.
- **Expected effect** *(est.)*: a 4-hour night replays in 1-3 minutes on one core, since the decider is the only
  real work.
- **Order:** do Phase 1 before any new decider feature, because every later PR gets the gate for free.

**Risks.**
- **The twin drifts from the real sticks.** Mitigation: refresh the tape with 3.4, and flag any metric where
  replay and live differ by more than X on the same night.
- **Overfitting to recorded nights.** The release gate's locked set still applies. Replay nights are dev data,
  not the locked set.
- **The clock codemod touches hot paths.** Do it in one PR with the full suite plus `replay_night` on the
  archive, before and after.
- **simloop fences subprocesses.** RedZone's adb is a subprocess, so use the FakeStick in-process for
  simulation, and keep simloop only for pure-asyncio units [T2].

---

## 4. Bibliography

All accessed 2026-11-08. **[older]** marks sources dated before October 2026.

**Claude Code docs** (live documentation, no publication date; versions per the changelog):
- [D1] Manage costs effectively. https://code.claude.com/docs/en/costs
- [D2] How Claude Code uses prompt caching. https://code.claude.com/docs/en/prompt-caching
- [D3] Model configuration (effort levels, defaults, aliases). https://code.claude.com/docs/en/model-config
- [D4] Speed up responses with fast mode. https://code.claude.com/docs/en/fast-mode
- [D5] Orchestrate subagents at scale with dynamic workflows. https://code.claude.com/docs/en/workflows
- [D6] Create custom subagents. https://code.claude.com/docs/en/sub-agents
- [D7] Run prompts on a schedule (`/loop`, cron). https://code.claude.com/docs/en/scheduled-tasks
- [D8] Schedule recurring tasks in Claude Code Desktop. https://code.claude.com/docs/en/desktop-scheduled-tasks
- [D9] Remote Control, including mobile push notifications. https://code.claude.com/docs/en/remote-control
- [D10] Keep Claude working toward a goal (`/goal`). https://code.claude.com/docs/en/goal
- [D11] Push events into a running session with channels (research preview). https://code.claude.com/docs/en/channels
- [D12] Hooks reference. https://code.claude.com/docs/en/hooks
- [D13] Monitoring usage (OpenTelemetry metrics, events, beta traces). https://code.claude.com/docs/en/monitoring-usage
- [D14] Configure auto mode. https://code.claude.com/docs/en/auto-mode-config
- [D15] Tools reference (Monitor tool). https://code.claude.com/docs/en/tools-reference
- [D16] Claude Code changelog, v2.1.309 (2026-10-22) to v2.1.319 (2026-11-06). https://code.claude.com/docs/en/changelog

**Anthropic:**
- [A1] Anthropic, "Claude Opus 5.5," 2026-09-22 **[older]**. https://www.anthropic.com/news/claude-opus-5-5
- [A2] L. Martin (Anthropic), "The components of agent memory," 2026-11-06. https://www.anthropic.com/research/components-of-agent-memory
- [A3] Claude Platform pricing (Opus/Sonnet/Haiku 5.5 rows, cache multipliers), live page. https://platform.claude.com/docs/en/about-claude/pricing
- [A4] T. Shihipar, "Lessons from building Claude Code: Prompt caching is everything," 2026-04-30 **[older]**. https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/
- [A5] Anthropic, "Redesigning source control at Anthropic," 2026-10-13 (updated 10-15). https://www.anthropic.com/research/redesigning-source-control

**Papers:**
- [R1] Y. Liu, Z. Han, "What Does a Harness Buy? Tokens, Mostly," arXiv:2610.04433, 2026-10-03. https://arxiv.org/abs/2610.04433
- [R2] A. J. Li et al., "TokenEconomizer: Experience-Driven Black-Box Cost Control for Coding Agents," arXiv:2610.22732, 2026-10-15. https://arxiv.org/abs/2610.22732
- [R3] Y. Jiang et al., "HarnessMM: Recursive Self-Improvement for Harness Token Efficiency," arXiv:2610.32813, 2026-10-23. https://arxiv.org/abs/2610.32813
- [R4] T. Chawla, S. Koul, "Chronicle: Cut-Point Replay for Regression Testing of LLM Agents," arXiv:2609.20625, 2026-09-17 **[older]**. https://arxiv.org/abs/2609.20625
- [R5] M. Seyedkazemi Ardebili, "NovaFabric: Tamper-Evident, Replayable Evidence for Autonomous AI Agent Runs," arXiv:2609.12582, 2026-09-11 **[older]**. https://arxiv.org/abs/2609.12582
- [R6] C. Koch, "From Agent Output to Authorized Transition," arXiv:2609.28216, 2026-09-23 **[older]**. https://arxiv.org/abs/2609.28216
- [R7] P. R. Aryan, F. J. Ekaputra, "Semantic Provenance and Policy-Aware Explanations for Agentic Coding Systems," ESWC'26 TAAPAAI workshop, CEUR-WS Vol-4210, published 2026-06-09 **[older]**. https://ceur-ws.org/Vol-4210/paper6.pdf
- [R8] R. Mudasiru, "Deterministic Replay for AI Agent Systems," arXiv:2607.16200, v1 dated 2026-04-30 on the abs page **[older]**. 250 replays, fidelity 1.0, 98.3% lower median per-step latency. https://arxiv.org/abs/2607.16200

**Testing and tools:**
- [T1] Tenzed, "Deterministic Simulation Testing (DST) in 2026: The Complete Engineering Guide," 2026-10-22. https://www.tenzed.com/blog/deterministic-simulation-testing-dst-distributed-systems-guide-2026
- [T2] simloop: deterministic simulation testing for Python asyncio (MIT, Python 3.12+), GitHub, undated. https://github.com/dhruvl/simloop
- [T3] looptime: time dilation in asyncio event loops for tests, GitHub, undated. https://github.com/nolar/looptime
- [T4] R. Artoul, "Deterministic Simulation Testing for Our Entire SaaS," WarpStream, 2024-03-12 **[older]**. https://www.warpstream.com/blog/deterministic-simulation-testing-for-our-entire-saas
- [T5] Amazon, "Live Device Interaction - Virtual Devices," updated 2026-06-30 **[older]**. https://developer.amazon.com/docs/app-testing/live-device-interaction-virtual.html
- [T6] Zendot, "jurigged: Hot Reloading for Python Without Restarting Your Program," undated **[older]**. https://zendot.org/en/posts/breuleux-jurigged
- [T7] OpenTelemetry, GenAI agent spans spec (status: Development), live. https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- [T8] "Architecture Fitness Function," Encyclopedia of Agentic Coding Patterns, undated **[older]**. https://aipatternbook.com/architecture-fitness-function

**Practitioner:**
- [P1] A. Grandi, "How I use Claude Code subagents to make my Claude Pro limits last longer," 2026-10-07. https://www.andreagrandi.it/posts/claude-code-subagents-to-save-usage/
- [P2] Codeman, "Can Claude Code run overnight? How to keep it working 24/7," updated 2026-10-18. https://getcodeman.com/guides/run-claude-code-overnight
- [P3] A. Mercer, "Coding With ADHD: How AI Agents Actually Help," Backgrind, 2026-06-21 **[older]**. https://backgrind.com/blog/coding-with-adhd/
- [P4] A. Ghani, "How to run an AI agent overnight and stay in control from your phone," Pushary, 2026-06-17 (updated 09-28) **[older]**. https://pushary.com/blog/run-ai-agent-overnight
- [P5] PandaStack, "Shadow Traffic and Replay for Service Rewrites," undated **[older]**. https://www.pandastack.ai/blog/traffic-replay-shadow-testing-ephemeral-microvms/
- [P6] Developers Digest, "The New Rules of Context Engineering for Claude 5 Models," 2026-07-26 (updated 10-16). It doesn't cover 5.5. https://www.developersdigest.tech/blog/context-engineering-claude-5-new-rules-2026

**Verification notes.**
- One search summary called Emergent's claim that Opus 5.5 was never announced unreliable. It is contradicted by
  Anthropic's own page [A1], so that claim is ignored here.
- Third-party claims about weekly-limit changes in September 2026 were not verified and are not used.
- The OpenTelemetry GenAI release-version history is conflicting across secondary sources; only the live spec
  is cited [T7].
