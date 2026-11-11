# RedZone

NFL RedZone, for every sport. A Mac app watches live games and drives one Samsung TV and two Fire TV sticks.
It cuts from game to game, and only at a dead ball (a moment when the play is over and the ball is not live).
Your phone is the dashboard and remote.

League order: NFL, then college football, NBA, college basketball, NHL, MLB. Nick's teams (Washington, UNC) come
first, and crunch time (close and late) comes first of all. The full spec is the
[North Star](docs/context/claude-memory/redzone-north-star.md). The gap to perfect is in
[docs/northstar.md](docs/northstar.md).

**Status (2026-11-11).** The app starts in dry run: it shows what it would do and never touches the TV. The
two-stick show described below is built and tested in replay with fake sticks. **It has not run on the real TV
through the app yet**, and it is off by default. The app is for a trusted home network, not public hosting.

## Rule #1

Join a game only at a dead ball. Never leave mid-play. Never leave a high-leverage moment: close and late, or
overtime. In basketball a join may come right after a made or missed shot, unless that moment is a transition play.

A frozen grader judges this on replayed nights (`redzone/grading.py`, `redzone/lab/cutgrade.py`). CI counts
`entries_mid_play` (it must stay 0), plus mid-play leaves of high-leverage moments, missed plays and dead air. The full
rule is in [CLAUDE.md](CLAUDE.md).

## The two-stick show (PARK)

One stick is on the TV. The other, the hidden stick, is tuned to the next game and paused on a dead-ball frame.
A jump resumes the hidden stick, flips the TV input, and pauses the stick that was left. A jump needs a **warrant**
worked out first (why go there, why now). Before each jump a hard guard re-checks that the leave and the landing
are both inside padded dead time and that nothing is too far behind live. If anything fails, the picture on screen
stays.

It supports basketball, football (including Prime Video and Thursday Night Football) and an NFL-night slider. It is
behind `park.enabled` (off). Details, config keys and what is not proven: [docs/park.md](docs/park.md).

## Run it

You need Python 3.12 and a checkout with `.venv` set up (`pip install -r requirements.txt`).

```sh
# The app, in dry run (the default: mode.dry_run is true). Open the printed LAN address or QR code on a phone.
.venv/bin/python -m redzone
PORT=8778 .venv/bin/python -m redzone          # another port, if an older session is running

# Replay the PARK show on a recorded night with fake sticks and a fake TV (no device is touched)
python scripts/fetch_eval_data.py              # pinned eval data, once
.venv/bin/python scripts/park_replay.py --night 20261023 --games 4

# Tests and checks
.venv/bin/python -m pytest -q tests/<file>.py   # prefer targeted tests; the full suite when the app is idle
ruff check .
python scripts/citymap.py --check               # code map is current
python scripts/check_memory.py                  # doc links resolve, decisions logged
```

Running the app as a login service: [docs/service-operation.md](docs/service-operation.md) (use
`scripts/install_service.sh check` for a read-only readiness check). Never start live tests or deploys without
Nick: `scripts/deploy.py --dry-run` is the only deploy an agent runs.

## Devices

- Two Fire OS sticks, controlled over ADB. Vega OS sticks are not supported.
- One Samsung TV. The TV input flip uses SmartThings today. Decision N1 (Nov 8) dropped it; Nick turned it back on
  Nov 10 because it was the only flip that worked on his TV (see `docs/memory/decisions.md`). The free access ends
  2026-12-15 and the $0 rule forbids a paid plan, so a second flip path is needed before then.
- YouTube TV and Prime Video, signed in by Nick. Agents never type passwords.
- The app records feed observations and command results, never stream video.

## Where to look

| You want | Read |
|---|---|
| The rules for every AI and the protected paths | [CLAUDE.md](CLAUDE.md) |
| The only plan | [docs/PLAN.md](docs/PLAN.md) |
| How agents work together (roles, claims, merge path) | [docs/coordination.md](docs/coordination.md) |
| Interfaces, owners, the 95% release gate | [docs/architecture.md](docs/architecture.md) |
| How the parts connect | [docs/system-map.md](docs/system-map.md), [docs/citymap.md](docs/citymap.md) |
| The PARK show | [docs/park.md](docs/park.md) |
| Every doc, indexed | [docs/README.md](docs/README.md) |
| Live status and verify-first commands | [docs/handoff.md](docs/handoff.md) |
| Every decision, and every lesson | [docs/memory/decisions.md](docs/memory/decisions.md), [docs/memory/lessons.md](docs/memory/lessons.md) |

New to the repo: run `python scripts/context_pack.py`, then the "Verify first" commands in
[docs/handoff.md](docs/handoff.md). To find code, use `python scripts/citymap.py --focus <file>`.

## Hard rules

- **The TV stays muted.** PARK refuses to run unless the sound guard is on and silence is required. A sound
  incident stops the show until a person clears it.
- **$0 budget.** Ask Nick before anything that costs money.
- **No secrets in this public repo.** Do not read, print or commit keys, tokens, passwords or stick addresses.
  Nick adds them. Fantasy and account credentials stay in local `.env` and `data/`, which are not committed.
- **No hardware from an agent.** No adb, stick or TV scripts unless Nick unlocks them (`data/.agent_hw_ok`). Use
  `tests/fake_adb.py` or the simulator.
- **Branches.** Every PR targets `integration/next`. Nothing goes to `main`, `integration/release` or the old
  `codex/resume-redzone` branch, and no force-push.
- **Protected paths** (`.claude/`, `.github/`, `tests/conftest.py`, the release gate, leak check, eval scripts and
  locks, gate baseline, grader freeze) change only in a grader PR for Nick.
- **Heavy jobs** (training, big evals) go to Kaggle or CI, not the 8 GB Mac that runs the live app.
- **Every state change names a reason** (`machine.to("state", why="...")`), and every commit has a `Why:` line.
- **No Peacock.**

## Data sources

ESPN public scoreboards and play-by-play, plus NBA, NHL and MLB feeds, merged by `redzone/sources/`. These are
unofficial endpoints and can change or fail; polling backs off and caches. Viewing games needs your own provider
accounts. See [docs/data-sources.md](docs/data-sources.md).
