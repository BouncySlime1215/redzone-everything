# Home reliability: what to do next for RedZone on an 8 GB Mac

Date: 2026-11-08. Research only; no code changed. Sources are numbered [n] and listed in section 4.
Each source is tagged **F** (page fetched and read), **S** (seen only in search results) or **old**
(before 2024; the idea is stable but check against today's macOS). Opinions are mine and marked
"proposal". Nothing here was measured on Nick's Mac; every threshold below is a starting guess to
calibrate with one week of the logging in rec 3.

## 0. What the code already does (so we do not rebuild it)

- Two launchd jobs, `KeepAlive=true`, `ThrottleInterval=15`, `ExitTimeOut=20`
  (`deploy/com.redzone.everything.plist.template`, `agent/com.redzone.agent.plist`).
- A separate watchdog process with a fixed action ladder and a verify-after-fix step
  (`agent/watchdog.py`, `agent/policy.py`), a 12 actions/hour cap across all action kinds, and a
  "never restart in crunch time" guard (`policy.guard`).
- An in-app supervisor with named fix rules, limiter and mutes (`redzone/supervisor.py`), a
  resource governor with normal/eco/critical (`redzone/governor.py`), circuit breaker with doubling
  backoff (`redzone/platform/breaker.py`), fault injection (`redzone/platform/faults.py`,
  `scripts/fault_drill.py`), invariants (`redzone/platform/health.py`), a per-night error budget
  (`redzone/observability.py`), and a deploy with health watch, error-baseline comparison and
  auto-rollback (`scripts/deploy.py`, keeps 5 releases).
- Explicit state machines (`redzone/fsm.py`, `redzone/machines/*`) including `deploy.lifecycle`
  and `governor.level`.

So the work is gap-filling, not new architecture. Gaps found by reading the code:

1. The governor measures memory with `ps -axo rss` (`governor.parse_ps`). On macOS, RSS counts only
   pages currently in RAM; under pressure the OS compresses or swaps dirty pages and RSS falls
   while the process's real cost does not [22][26]. With 6+ GB swap, `service_hard_mb=1200` may
   under-read. (Evidence is secondary; verify on the Mac, see rec 1.)
2. `swap_eco_mb=1500` is an absolute threshold. If swap sits above 6 GB all day, the governor is
   in "eco" permanently and the signal carries no information. Sources agree swap-used alone is
   normal; swap growing while pressure is elevated is the warning [1][24].
3. `health.peak_rss_mb()` uses `ru_maxrss`, a lifetime peak, not current size, so it cannot show a
   slow leak or its slope.
4. No `tracemalloc`, `faulthandler` or event-loop-lag instrumentation anywhere in `redzone/`,
   `agent/` or `scripts/` (grep found only `getrusage` uses).
5. App log goes through a plain `RotatingFileHandler` (`scripts/service_runtime.py:78`). On a
   swapping Mac, a slow disk write inside a logging call blocks the event loop [13].
6. Restart storms are bounded only by the shared 12/hour cap. `restart_service` has no own
   intensity limit, unlike an OTP supervisor [6].
7. Backoff is doubling without jitter (`breaker.py`). With two sticks this is low risk (rec 8).

## 1. Ranked recommendations (risk reduced per hour of work)

Ranking = my estimate of (chance it prevents or shortens a live-game outage) / (hours to do it).
Hours are for a focused human or AI session including a test.

| # | Recommendation | Risk it cuts | Effort | Why it ranks here |
|---|---|---|---|---|
| 1 | Measure memory as footprint, not RSS | Hard-cap watchdog and eco level fire at the wrong time | about 1 h | Smallest change, fixes the input to three existing controls |
| 2 | Fix the swap trigger: use pressure level plus swap growth rate, not an absolute | Governor stuck in eco (or useless) on a Mac that always swaps | about 1 h | Pure config + 15 lines; decides whether load shedding means anything |
| 3 | Leak telemetry: 5-minute JSONL of footprint/RSS, task count, GC counts, loop lag; tracemalloc toggled by signal; faulthandler on a signal | Unknown slow leak, unknown hang | about 3 h | Turns "it is at 1.2 GB again" into a file path and a line number |
| 4 | Per-action restart intensity for `restart_service` (OTP style) plus escalate to "stop and tell Nick" | Restart loop during a game, crash loop under swap | about 1 h | 3 restarts in 30 min then hold; cheap and testable with the fake clock |
| 5 | Queue the file log handler (QueueHandler + QueueListener) | Event loop stalls on a slow disk while swapping | about 45 min | Standard-library, no new dependency [13] |
| 6 | Planned rejuvenation: restart the app at a safe window when footprint passes the soft line, before the hard cap | Hard-cap restart landing near a game | about 2 h | Reuses `deploy.busy()` / `long_break` logic; replaces a crash with a choice |
| 7 | Chaos scenarios for memory and sleep/wake (add to `fault_drill.py`) | Untested recovery paths rot [9] | about 3 h | Recovery code you never run "often doesn't work" [9] |
| 8 | Retry token bucket for adb reconnects; jitter on periodic timers only if more clients appear | Retry amplification | about 1 h | Low value at 2 sticks; do after the above |
| 9 | Burn-rate style alert on the existing error budget (count-based, over nights) | Slow degradation that no single check flags | about 1.5 h | Needs small-sample care, see rec 9 notes |
| 10 | Deploy audit: schema backward compatibility for rollback, import smoke test before swap | A rollback that cannot start | about 1 h | Verify first; may already be handled |
| 11 | Do NOT adopt OpenTelemetry SDK yet | Memory and complexity on an 8 GB Mac | 0 h | See rec 11 |

### Rec 1. Footprint instead of RSS (about 1 hour)

- What: next to `rss`, record `phys_footprint` for the service PID. Two routes: shell out to
  `footprint -p <pid>`, which reports `phys_footprint` and `phys_footprint_peak` [22], or read
  `task_info(TASK_VM_INFO).phys_footprint` / `proc_pid_rusage` through ctypes (stdlib only, the
  watchdog venv must stay dependency-light).
- Why: the XNU compressor-shortage action on macOS kills the process holding the most compressed
  pages when it holds over half of them [1]. That is a footprint-style cost, not RSS. A process
  that looks small in `ps` can still be the one the kernel picks.
- How to verify before trusting: for one evening, log `ps rss`, `footprint`, `vm.swapusage` and
  `kern.memorystatus_vm_pressure_level` every minute and plot them. If RSS and footprint track
  within about 10 percent, rec 1 is cosmetic; if they diverge under pressure, switch the caps.
  The RSS-vs-footprint gap claims in [26] are one author's anecdote; treat as a hypothesis.
- Files: `redzone/governor.py` (`parse_ps`, `Sample.procs`, `service_mb`), `agent/watchdog.py:233`,
  `redzone/platform/health.py:189`.

### Rec 2. Make "swap" mean something (about 1 hour)

- What: keep `mem_pressure` (1 normal, 2 warn, 4 critical, as already read from
  `kern.memorystatus_vm_pressure_level`) as the primary signal. Replace "swap used > 1500 MB" with
  "swap grew by more than X MB in the last 10 minutes while pressure >= 2" (proposal X = 300, calibrate).
  Keep an absolute swap line only as `critical` (for example above 90 percent of `total` in
  `vm.swapusage`, which `parse_swap` already reads; add the total).
- Why: third-party explainers consistently say some swap use is normal and the thing to watch is
  growth while pressure is yellow or red [24]. The kernel itself acts on low-swap and
  compressor shortage, not on a swap total [1].
- Files: `governor.assess`, `DEFAULTS["swap_eco_mb"]`, `tests/test_governor.py`. A PR that changes a
  default must add a line to `docs/memory/decisions.md` (project rule).
- Check first: look at the governor's current level in `data/` or the status API. If it already
  reads "eco" nearly all day, this rec is confirmed.

### Rec 3. Leak and hang telemetry that costs almost nothing (about 3 hours)

1. **Heartbeat row every 5 minutes** to a JSONL (cheap): footprint, RSS, `gc.get_count()`,
   `len(asyncio.all_tasks())`, open file descriptors, and loop lag (a task that sleeps 1 s and
   records how late it woke). asyncio debug mode logs callbacks slower than 0.1 s by default [11];
   do not leave debug mode on in production, just borrow the 100 ms idea as the lag alarm.
2. **tracemalloc by signal, not always on.** Tracing hooks every Python allocation; more stored
   frames cost more memory and CPU, and it only sees allocations after it starts [10]. On SIGUSR2:
   `tracemalloc.start(5)`, take a snapshot, and 20 minutes later `compare_to(old, "lineno")[:20]`
   into `data/leak-<time>.txt`, then `stop()`. Log `get_tracemalloc_memory()` so the tracer's own
   cost is visible [10]. No overhead number is given in the docs; measure it once on the Mac.
3. **faulthandler for hangs.** `faulthandler.register(SIGUSR1)` lets the watchdog dump all
   thread stacks into a file before it restarts a hung app; `dump_traceback_later(timeout,
   repeat=True)` runs on its own thread and works even if the main thread is deadlocked [12].
   Wire it into the watchdog's `restart_service` path: signal first, wait 2 s, then
   `launchctl kickstart`. Keep the file handle open for the process lifetime [12].
4. **Python's memory behaviour to expect.** Small objects (512 bytes or less) live in pymalloc
   arenas that go back to the OS only when every pool in them is empty, so RSS often stays at the
   peak after a burst [23, old 2004]. If tracemalloc shows no growth but
   footprint climbs, that is fragmentation, and the right remedy is rejuvenation (rec 6), not a
   hunt for a leak.
- Files: new small module under `redzone/platform/` (next to `health.py`), hook in
  `scripts/service_runtime.py` (signal registration) and the `restart_service` action in
  `agent/watchdog.py`.

### Rec 4. Restart intensity for the watchdog (about 1 hour)

- What: add an OTP-style limit just for `restart_service`: at most N restarts per T seconds, then
  stop restarting, push one notification, and stay in "needs a human" (proposal: 3 in 30 minutes;
  the existing 12/hour cap counts reconnects and prunes together).
- Why: OTP supervisors give up when more than MaxR restarts occur within MaxT seconds, terminate
  their children and let the parent decide; defaults are 1 restart per 5 seconds [6]. The SRE book
  warns that crash-looping servers fail again immediately under full load and recovery may need
  load cut well below the original level [9]. Also note the OTP warning that nested limits
  multiply [6]: launchd (`ThrottleInterval` 15 s, "jobs will not be spawned more than once every
  10 seconds" by default [2]) sits above the watchdog, so the effective loop is launchd 15 s times
  the watchdog's own rate.
- Already there: `self.restarts` list in `agent/watchdog.py` (appended at each restart). Reuse it.
- Do not set launchd `KeepAlive` to `SuccessfulExit=false` just for this: the Apple guide warns that
  a daemon that exits too quickly may be treated as crashed and throttled or "respawning too fast"
  [3 (old)]; unconditional `KeepAlive` plus the watchdog's counted ladder is easier to reason about.

### Rec 5. Non-blocking logging (about 45 minutes)

- What: in `scripts/service_runtime.py`, attach only a `QueueHandler` to the root logger and run
  the `RotatingFileHandler` in a `QueueListener` thread. The Python cookbook says network and file
  handlers can block the event loop because some asyncio internals log, and prescribes exactly this
  [13]. Use an unbounded queue and catch `queue.Full` only if you bound it.
- Why here: the Mac swaps; a disk write that stalls for a second is a missed dead ball.
- Structured fields: keep plain lines (the deploy health watch parses them,
  `scripts/deploy.py:_LINE`); put JSON only in the existing JSONL journals.

### Rec 6. Planned rejuvenation instead of the hard cap (about 2 hours)

- What: when the footprint passes `service_soft_mb` and stays there, schedule a graceful restart for
  the next safe window: no live game, or the screen game is at a long break (reuse
  `deploy.busy()` and `long_break()` from `scripts/deploy.py`). Keep the hard cap as the last resort.
- Why: proactive, graceful restart into a clean state to defer software aging is an established
  fault-tolerance technique (Huang, Kintala, Kolettis, Fulton 1995 [28]; bibliographic record
  seen, paper itself not read). It converts an unscheduled crash into a scheduled gap.
- Interaction: `deploy_in_progress()` already makes the watchdog stand aside; reuse that flag so the
  two never restart together. Show it in the machine for `deploy.lifecycle` or add a small
  `service.rejuvenate` machine (project convention: explicit FSM).

### Rec 7. Chaos drills for what is not yet drilled (about 3 hours)

The Principles of Chaos steps map directly: define steady state from measurable output (your
error-budget numbers), vary real-world events, automate, limit blast radius [17]. `fault_drill.py`
already has adb_drop, adb_hang, app_crash, prompt_screen, feed_5xx, tv_flip_fail, clock_skew.
Add, in this order:

1. `memory_balloon`: fake `Sample` with footprint over soft then hard; assert the watchdog picks
   rejuvenate in a break and notify in crunch, never restart in crunch (the existing guard).
2. `sleep_wake`: a long monotonic gap plus an adb transport that comes back "offline"; assert
   `WakeWatch` triggers reconnect and no mid-play cut occurs.
3. `sigkill_mid_switch`: kill the app during a switch; assert launchd restarts it and the engine
   does not claim a stale switch (`orphan_switch` invariant).
4. `swap_storm`: governor sees pressure 4 for 10 minutes; assert level goes critical, OCR and model
   shed, and live switching is untouched (the governor's stated promise).
5. `log_disk_stall`: after rec 5, assert loop lag stays under the alarm while the file handler sleeps.
- Stay off the live TV: all of this runs against the fakes, per the project rule on no disruptive
  TV tests.

### Rec 8. Retries: budget first, jitter second (about 1 hour, low priority)

- Amazon's guidance: limit retries with a local token bucket; retry at one layer only; add jitter
  to backoff, and also to periodic jobs [4]. Without jitter, exponential backoff is "the clear
  loser" in simulation [5 (old, 2015)]. With two sticks and one Mac there is no thundering herd, so
  jitter matters little here; the budget matters more (adb reconnect, SmartThings, feeds).
- Note Amazon's caution: circuit breakers add hard-to-test modal behaviour and can lengthen
  recovery [4]; Fowler's description is the pattern you already have [14]. Keep `breaker.py` (it is
  tested and fail-fast saves 12 s per switch per its docstring) but make sure each trip is logged
  and can be reset by a person, which [14] recommends, and consider `Breaker.success()` hints
  already wired to a successful `adb connect`.
- Retry stacking: three retries at each of three layers gives 64 attempts [9]; five layers gives
  243 [4]. Check that `supervisor.rule_adb`, `Stick.connect`, and the watchdog's `reconnect_stick`
  do not all retry the same stick on overlapping clocks. (They share an adb `disconnect/connect`
  in two processes: `supervisor.py:969` and the watchdog.)

### Rec 9. Burn-rate alert for the error budget (about 1.5 hours)

- The SRE Workbook's multiwindow table for a 99.9 percent SLO: page at 14.4x burn over 1 h
  (2 percent of budget), 6x over 6 h (5 percent), ticket at 1x over 3 days (10 percent) [7].
  Those assume many events per hour. RedZone has tens of switches a night, so one bad switch can
  look like a 14x burn. Proposal: keep the nightly rolling window already in
  `redzone/observability.py` and add one rule: push to the phone when a single night has used
  more than 20 percent of any budget, plus a rule on three consecutive nights above 50 percent.
  (The 20 percent postmortem trigger is only from third-party summaries I could not confirm in
  the Workbook text; treat it as a local proposal.)
- Error budget policy in plain words, proposal: if `left_mid_play` exceeds its allowance in a
  window, new feature flags stay OFF and only fixes ship. This matches the project's Rule #1 and
  "ships OFF behind its flag" gate.

### Rec 10. Deploy safety on one host (audit, about 1 hour)

- What you have is blue/green on one machine in spirit: snapshots of releases, a health watch of
  `watch_s=600` against a baseline, auto-rollback, deploys deferred during live games
  (`scripts/deploy.py`). Fowler's blue/green says the hard part is the database: make schema
  changes backward compatible, deploy that first, remove old support later [16]. Audit
  `data/redzone.db` writers (`redzone/store.py`) for any change that an older release cannot read.
  I did not find a migration framework in a quick grep of `store.py`; that does not prove there is
  none, so check.
- Canary advice does not transfer directly (no second host to compare with). The useful parts [8]:
  pick few SLI-style metrics (you have them), compare absolute SLOs as well as relative to
  baseline, and run long enough to cover a full work unit. Your "work unit" is a dead ball plus a
  switch. Consider extending `watch_s` until at least one real switch completed since the restart
  (so a quiet night cannot pass a deploy untested). Fowler's toggle taxonomy: your OFF-by-default
  flags are "release toggles" and should have a removal task and expiry; ops "kill switches"
  may stay [15].
- Add before the swap: `python -c "import redzone"` from the new release in the service venv, so a
  broken import is caught while the old version still runs (about 10 lines in `Deployer`).

### Rec 11. OpenTelemetry: not now

- The official getting-started path is zero-code `opentelemetry-instrument` with console exporters
  and an optional Collector in Docker [25]. I found no overhead figures for small Python services
  in anything I could fetch, so I cannot say what the SDK would cost on this Mac. On an 8 GB machine
  that already swaps, the safe default is: do not add a resident SDK and exporter. You already
  have per-switch traces (`redzone/trace.py`, sqlite) and JSONL journals; add a `trace_id` field
  to the heartbeat row in rec 3 and you have the useful 10 percent. Revisit only if a second host
  appears.

## 2. Wi-Fi, mDNS and adb over Wi-Fi: what the sources support

- adb wireless debugging requires both ends on the same network and pairing; devices reconnect
  automatically when on the same network (Android 11+ flow) [19]. Fire TV sticks use legacy
  `adb connect host:5555`, which the Android page does not cover; the community pattern is
  disconnect then connect after a drop, and a wedged transport sometimes needs the stick rebooted
  [20][29].
- Home Assistant's Android TV docs: a device that drops off Wi-Fi breaks adb, a wake-lock utility
  can help, only one adb client at a time, and they recommend the in-Python adb implementation
  over an adb server unless stability problems appear [20]. RedZone uses the adb server
  (`adbmod._adb("start-server")`). One-client-at-a-time matters: the supervisor and the watchdog
  both call `adb disconnect/connect` (`supervisor.py:969`, `agent/watchdog.py` reconnect_stick). Make
  one of them the owner (proposal: supervisor owns adb; the watchdog asks it, or only acts when
  the app itself is down).
- mDNS: host records default to a 120 second TTL, others 75 minutes; a host that wakes or sees a
  link change must re-probe and re-announce; queriers re-ask at 80/85/90/95 percent of TTL and
  drop at 100 percent [18]. Consequence: after the Mac or a stick wakes, a name can be wrong for
  up to about 2 minutes. RedZone connects by IP and port, which sidesteps this; keep it that way
  and give the sticks DHCP reservations in the router (my proposal; not from a source).
- Sleep: `engine.py:3003` runs `caffeinate -i -s` only while games are live, so the Mac sleeps
  otherwise. `pmset -g assertions` shows what is holding sleep; `pmset -a womp 0` and
  `powernap 0` are the CIS benchmark commands for wake-for-network and Power Nap [27 (S)]. Whether
  idle-time sleep causes the stick drops is untested; test by logging `pmset -g log` wake/sleep
  lines against `adb` offline events for a week before changing any power setting (changing system
  settings is Nick's call).

## 3. Memory-pressure plan for the 8 GB Mac

### What the OS does (sources [1][24])
- The kernel tracks a pressure level (`kern.memorystatus_vm_pressure_level`; the governor already
  reads it as 1 normal, 2 warn, 4 critical). The kernel compresses inactive pages first, then
  writes to swap [24]. Kills run in ascending jetsam priority; on macOS under pressure the
  notify thread kills about one opted-in clean idle daemon per second [1].
- Compressor space shortage on macOS: at most once per 5 seconds, kill the largest process if it
  holds over half the compressed pages, else apply the process's own pcontrol action [1]. If
  RedZone's service is the biggest compressed user, it is the target, and it dies at a time the
  kernel chooses. That is the strongest argument for staying small and for rec 6.
- `launchd` has no enforced RSS cap for user agents in this setup (the governor header says so);
  `HardResourceLimits` exist in the plist [2] but I found no source that they cap resident memory on
  macOS, so do not rely on them.

### Budget (proposal; calibrate after the one-week baseline)
Existing project numbers: service soft 700 MB, hard 1200 MB, model processes 1500 MB combined,
local AI at most about 2 GB (CLAUDE.md).

| Consumer | Today | Proposed rule |
|---|---|---|
| RedZone service | soft 700, hard 1200 (RSS) | same numbers but on footprint; soft = schedule rejuvenation, hard = restart outside crunch |
| Watchdog + `llm_server` | 1500 together | no model while a game is live and pressure >= 2; drop after 15 idle min (exists) |
| Local OCR / Vision | normal, eco | unchanged; shed at critical (exists) |
| Heavy jobs (tests, deploy tests, ffmpeg) | `nice` / `taskpolicy -b` | refuse to start when pressure >= 2 or swap growing; deploy already defers while live |
| Claude/Codex sessions on the same Mac | not governed | project rule already says no training; add: none of the above at pressure 4 |

### Ladder (each rung exists or is a small add)
1. Measure: 5-minute heartbeat row (rec 3). Baseline for a week.
2. Shed optional work at eco: no reels, lab, archive (exists in `governor.ALLOWED`).
3. Shed models and OCR at critical (exists).
4. New: at sustained footprint over soft, schedule planned restart in a break (rec 6).
5. Existing: hard cap restart, never in crunch (`policy.guard`), now bounded by rec 4.
6. New: if the same pattern repeats 3 nights, open an issue automatically with the tracemalloc
   diff from rec 3 attached (redact; the repo is public).

### How to see it working (acceptance)
- Heartbeat JSONL has footprint, RSS, swap, pressure, loop lag at 5-minute spacing.
- Governor level is not "eco" for more than, say, half the day on a normal night (proposal).
- Zero hard-cap restarts during live games; rejuvenations land in breaks (log the reason).
- Loop lag p99 under 100 ms outside restarts (the asyncio slow-callback default [11]) as a
  steady-state hypothesis [17].

## 4. Bibliography

Verification key: **F** fetched and read this session; **S** seen in search results only (not
opened); **old** pre-2024. Dates are as best I could tell; "n/d" = no date on page.

1. XNU `memorystatus_kills.md` (Apple open-source kernel docs, community mirror cs.iossec.tech), n/d. F.
   https://cs.iossec.tech/xnu/latest/source/doc/vm/memorystatus_kills.md
2. `launchd.plist(5)` man page text, mirror at leancrew.com (macOS man page), n/d. F. old-ish, check
   `man launchd.plist` locally. https://leancrew.com/all-this/man/man5/launchd.plist.html
3. Apple, "Creating Launch Daemons and Agents", Daemons and Services Programming Guide (archive). F. old.
   https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
4. Marc Brooker, "Timeouts, retries, and backoff with jitter", Amazon Builders' Library (Dec 2019; now hosted on
   builder.aws.com). F. old but current practice.
   https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter
5. Marc Brooker, "Exponential Backoff And Jitter", AWS Architecture Blog (2015). F (conclusions only; formulas
   were images). old. https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
6. Erlang/OTP 25, "Supervisor Behaviour" design principles. F.
   https://www.erlang.org/docs/25/design_principles/sup_princ.html
7. Google SRE Workbook, "Alerting on SLOs" (multiwindow, multi-burn-rate table 5-8). F. Book 2018.
   https://sre.google/workbook/alerting-on-slos/
8. Google SRE Workbook, "Canarying Releases". F. Book 2018. https://sre.google/workbook/canarying-releases/
9. Google SRE Book, "Addressing Cascading Failures" (2016). F. old. https://sre.google/sre-book/addressing-cascading-failures/
10. Python docs, `tracemalloc`. F. https://docs.python.org/3/library/tracemalloc.html
11. Python docs, "Developing with asyncio" (debug mode, `slow_callback_duration` 0.1 s). F.
    https://docs.python.org/3/library/asyncio-dev.html
12. Python docs, `faulthandler`. F. https://docs.python.org/3/library/faulthandler.html
13. Python docs, Logging Cookbook ("Dealing with handlers that block"). F, first 100k characters only (JSON
    structured-logging section not read). https://docs.python.org/3/howto/logging-cookbook.html
14. Martin Fowler, "CircuitBreaker" (2014). F. old. https://martinfowler.com/bliki/CircuitBreaker.html
15. Pete Hodgson on martinfowler.com, "Feature Toggles (aka Feature Flags)" (2017). F. old.
    https://martinfowler.com/articles/feature-toggles.html
16. Martin Fowler, "BlueGreenDeployment" (2010). F. old. https://martinfowler.com/bliki/BlueGreenDeployment.html
17. Principles of Chaos Engineering. F. https://principlesofchaos.org/
18. RFC 6762, Multicast DNS (2013). F, first half only (sections 1 to 10.2). old.
    https://www.rfc-editor.org/rfc/rfc6762
19. Android Developers, Android Debug Bridge (wireless debugging section). F.
    https://developer.android.com/tools/adb
20. Home Assistant, Android Debug Bridge (androidtv) integration docs, troubleshooting. F.
    https://www.home-assistant.io/integrations/androidtv/
21. systemd `sd_watchdog_enabled(3)` (ping at half the watchdog interval; manager terminates a service that
    misses it). F. Reference pattern only; macOS has no equivalent, RedZone's watchdog polls the API instead.
    https://www.freedesktop.org/software/systemd/man/latest/sd_watchdog_enabled.html
22. Unix Tutorial, "footprint: inspect process memory in macOS" (secondary; reports phys_footprint and
    phys_footprint_peak; says it is not the same measurement as RSS). F.
    https://www.unixtutorial.org/commands/footprint/
23. Evan Jones on python-dev, "Changing pymalloc behaviour for long running processes" (19 Oct 2004). F. old.
    https://mail.python.org/archives/list/python-dev@python.org/thread/FXCAZMOSO477PSDXW2OXXUFRUCF5OC7H/?sort=date
24. Memory-pressure explainers (all secondary, vendor-adjacent): 24.1 "Mac memory pressure explained" (mole.fit,
    a vendor of a Mac cleaning tool) https://mole.fit/blog/mac-memory-pressure-explained ; 24.2 Greg Gant,
    "macOS memory management" (2024-07-03) https://blog.greggant.com/posts/2024/07/03/macos-memory-management.html .
    S only (search summaries); used only for the ordering compress then swap and "pressure over swap-used".
25. OpenTelemetry docs, Python "Getting Started by Example". F. https://opentelemetry.io/docs/languages/python/getting-started/
26. Alexey Pelykh, "The RSS illusion" (dev.to). S only; single-author anecdote of RSS vs footprint gap.
    https://dev.to/alexey-pelykh/the-rss-illusion-63-gb-process-on-a-32-gb-machine-298n
27. CIS Apple macOS benchmark audit item "Disable Wake for network access and Power Nap" (tenable.com audit
    file). S only. https://www.tenable.com/audits/items/CIS_Apple_macOS_10.13_v1.1.0_Level_1.audit:047a5ef245ea9d1e5572f2107b2f875a  (old, macOS 10.13)
28. Huang, Kintala, Kolettis, Fulton, "Software Rejuvenation: Analysis, Module and Applications", FTCS-25, 1995
    (pp. 381-390). S only (bibliographic record; paper not read). old.
    https://www.ibr.cs.tu-bs.de/bib/xml/HKKF95.xml and https://www.dependability.org/?p=134
    29. mythtv-users, "FireTV 4K stick" (Aug 2019): adb dropped without `disconnect` sometimes needs a stick reboot.
    S only; anecdote. old. https://lists.mythtv.org/pipermail/mythtv-users/2019-August/401285.html

## 5. Gaps and what I did not verify

- No measurement on the real Mac. Rec 1 and 2 are hypotheses until the one-week baseline.
- XNU pressure-level threshold doc (`memorystatus_notify.md`) was blocked by a bot wall; the 1/2/4
  mapping comes from the repo's own code comment, not a source I read.
- No source for tracemalloc or OpenTelemetry overhead numbers; measure locally.
- The postmortem-at-20-percent rule and the "14.4x = 2 percent in 1 hour" claim: the second is in
  Workbook table 5-8 [7]; the first I could not confirm.
- `launchctl kickstart -k` semantics, `EnablePressuredExit` and rlimit memory behaviour on current
  macOS were not verified; I avoided recommending them.
