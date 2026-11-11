# Why did it do that?

Every answer comes from records the app already keeps. Run from the repo folder; nothing here touches the TV.

| Question | Command |
|---|---|
| What happened around 8:42 PM tonight? | `python scripts/why.py 20:42` (add `--date 2026-11-07`, `--window 300`) |
| How did last night go, and what held the TV? | `python scripts/why.py --night 2026-11-07` |
| Why is a setting what it is? | `python scripts/why.py decider.entry.crunch_now` (decisions + lessons, newest first) |
| Why does this file work this way? | `python scripts/why.py redzone/decider/cuts.py` |
| What has one part been doing (survives restarts)? | `python scripts/why.py --machine control.standby` |
| What is everything doing right now? | `curl -s localhost:8778/api/machines` (live app) |
| Same, saved history | `curl -s 'localhost:8778/api/machines/history?name=decider.owner'` |
| What can a state be, and how does it move? | [docs/state-machines.md](../../state-machines.md) (generated) |
| What does this file connect to? | `python scripts/citymap.py --focus <file>` / `--impact <file>` |
| Why was this commit made? | `git log` (every commit has a `Why:` line, checked in CI) |

Where the records live (Mac, `data/`, local only, never committed):
- `transitions.jsonl`: every state move with its reason and the running commit (redzone/fsm.py).
- `jumps.jsonl`: each jump, ok or failed, the veto at the flip, the commit (`sha`).
- `jump_status.jsonl`: every ~30 s, who owned the TV and what held it (hold codes), the commit.
- `buffer.jsonl`: the hidden stick's scrub and hold steps.

What keeps this true after today (tests/test_explains_itself.py, in CI):
- a state move without a reason fails the tests;
- every state machine needs a meaning for each state and an owner that exists;
- docs/state-machines.md must match the code;
- the app must start the transition log and stamp decision rows with its commit;
- `why.py` must answer a time, a night and a setting.

If a "why" is missing, that is a bug: add the reason at the code that made the move, not a note here.
