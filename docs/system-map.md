# System map: how RedZone's parts connect

The short version for people and agents. The full, generated, always-current map is
[citymap.md](citymap.md) (356 modules, call edges, data files, machines). Use the commands instead of reading
many files:

```sh
python scripts/citymap.py --focus redzone/decider/cuts.py --tokens 1500   # the code around one file
python scripts/citymap.py --impact redzone/control.py                     # what a change can break
curl -s localhost:8778/api/machines                                       # every state machine, live
curl -s 'localhost:8778/api/machines/history?name=control.standby'        # saved moves, with why
```

## The loop, left to right

```
 feeds ──► merge ──► scoring ──► decider ──► engine ──► control ──► tuners / TV
 (ESPN poll,  (one game   (excitement,   (who owns    (tick loop,  (two sticks,   (adb deep links,
  fastcast,    state per   leverage,      the TV,      may_cut,     standby,       scrub, HDMI flip
  league APIs) game)       crunch)        when to cut) pending)     flip)          via Tizen/CEC/ST)
        ▲                                     ▲             │
        │                                     │             ▼
   supervisor / governor / autonomy  ◄── phone + API (pause, lock, skip, focus) ── userstate
   deploy.py + agent/watchdog (restart, rollback)            data/*.jsonl logs, transitions.jsonl
```

| Stage | Main modules | State machines (redzone/machines/) | Writes |
|---|---|---|---|
| Feeds | `sources/espn.py`, `sources/fastcast.py`, `sources/eventlinks.py` | `fastcast.connection`, `fastcast.topic` | feed health |
| Merge | `sources/merge.py` | `merge.feed` (trusted / regressing / benched) | `source_status` |
| Scoring | `scoring/engine.py`, `scoring/drive.py`, `crunch.py`, `lab/leverage.py` (grader) | `phone.crunch` | scores with reasons |
| Decide | `decider/decider.py`, `decider/buffered.py`, `decider/cuts.py`, `decider/screen.py` | `decider.owner`, `decider.screen`, `decider.home`, `decider.override`, `decider.delay_label`, ... | Decisions, `jump_status.jsonl` |
| Engine | `engine.py` | `engine.pending`, `engine.standby_plan`, `engine.hold`, `engine.skip`, `engine.fail_cool` | switches table, `jumps.jsonl` |
| Control | `control.py` | `control.standby`, `control.screen`, `control.command`, `control.offset`, `control.visible`, `control.two_stick` | `buffer.jsonl`, `held.json` |
| Devices | `tuners/adb.py`, `tuners/tuners.py`, `tuners/yttv_search.py`, `tv.py` | `tuners.stick_link`, `tuners.adb_shell`, `tv.smartthings` | device logs |
| Keep running | `supervisor.py`, `governor.py`, `autonomy.py`, `agent/watchdog.py`, `scripts/deploy.py` | `supervisor.target`, `governor.level`, `deploy.lifecycle`, `autonomy.job` | `supervisor.jsonl`, `deploy.jsonl` |
| You | `web/server.py`, `web/phone_v2.py`, `userstate.py` | `decider.override`, `engine.hold` | `userstate.json` |

Every machine's states and allowed moves: [state-machines.md](state-machines.md). Every move is saved with its
reason to `data/transitions.jsonl` (survives restarts).

## Where Rule #1 is decided

1. `decider/cuts.py` and `decider/buffered.py` decide when a cut lands in dead time.
2. `engine.may_cut()` re-checks at the moment of the flip (vetoes are named in `jumps.jsonl`).
3. `control.py` flips only to a ready standby (`control.standby` state `ready` / `snap_parked`).
4. `lab/leverage.py` and the scorecards grade it afterwards; CI counters gate PRs.

Known gaps from the 2026-11-08 audits ([audits](archive/audits/)): college leagues and MLB are not covered by the
leverage grader or several entry rules; user taps can skip the leave check. See the fix plan.

## Who owns what

Owners per area are in [architecture.md](architecture.md) and the `owner` column of [citymap.md](citymap.md)
(191 modules are still unowned). Agent coordination rules: [coordination.md](coordination.md).
