# Running without Codex

The app is a local Python process. Codex is not a runtime dependency. A foreground
`.venv/bin/python -m redzone` lasts only as long as its process/session survives.
The optional macOS LaunchAgent runs independently of Codex after the user logs in.
On November 2, the user-authorized installation was completed on port 8778 in enforced
dry-run. The job reported running, the HTTP state endpoint responded, and the ESPN source
and event-link refresh succeeded. This establishes background startup, not unattended TV
operation. A later session changed the installed job to `--allow-live`. On resuming this
work, the job was running and the saved mode permitted TV control. The hardware investigation
then explicitly set dry-run and paused automatic decisions because the current input path
could report success before playback recovered. The installation now permits device control
as a capability, but the saved mode is dry-run; it is no longer the original enforced-dry-run
installation. Hardware validation remains incomplete.

## Prepare without starting

Use a permanent project location before installing. The job stores absolute paths to
this checkout and its `.venv`; moving either requires removing and reinstalling the job.
Create the virtual environment with Python 3.12+ and install `requirements.txt` first.

```sh
scripts/install_service.sh check
scripts/install_service.sh render
```

No arguments also runs `check`. These commands do not scan, connect to devices, fetch
feeds, change configuration or register a job. `render` writes the proposed plist to
stdout. The check validates saved settings strictly, checks dependency availability,
selected-device records, data-directory permissions, local port occupancy and relevant
credential presence. It does not print credential values or prove hardware readiness.
Run it through the shell script so it uses this checkout's virtual environment.

## Install and stop

The following commands change the Mac's login service. `install` starts the app now:

```sh
scripts/install_service.sh install
scripts/install_service.sh status
scripts/install_service.sh remove
```

The default service **enforces dry-run in memory**, including after settings changes
and configuration rollback. It preserves saved settings on disk and rejects requests
to enable live control through the dashboard. Microphone calibration, if separately
configured, still needs its normal macOS permission; dry-run refers to device control.

The installer refuses to overwrite an existing job or to start on an occupied dashboard
port. Stop any existing foreground instance deliberately before installation. An explicit
`PORT=8778 scripts/install_service.sh install` stores that port in the job. Normal terminal
environment variables are not otherwise copied into launchd.

After physical validation and an explicit decision to run live unattended, remove the
dry-run job and install with `--allow-live`. This permits the saved mode; it does not
turn live mode on or validate the display/providers. Do not use it to bypass readiness.
`remove` unloads the job and deletes its plist, retaining runtime data. Merely killing
the Python process is not a durable stop: launchd will restart it.

## Recovery and logs

The job launches at user login, and launchd retries exits with a 15-second throttle.
The app propagates producer crashes and unexpected phone-server exits, and cleans up
its engine/device clients on shutdown. A per-checkout lock prevents duplicate service
runners. Port checks do not replace that lock and cannot eliminate all launch races.
The service allows 20 seconds for shutdown before launchd may terminate it forcibly.

Primary logs rotate at about 5 MiB each, with three backups:

```text
data/service.log
data/service.log.1 ... service.log.3
~/Library/Logs/redzone/service-bootstrap.log
```

The bootstrap log is for failures before the Python logging setup and is not rotated. It lives in
`~/Library/Logs/redzone`, not in the checkout, and the job has no `WorkingDirectory`: the checkout is
under `~/Documents`, which macOS privacy (TCC) closes to launchd itself, so a job pointed there exits
with EX_CONFIG (78) before Python starts. The job runs `python -c` that changes into the checkout and
runs `scripts/service_runtime.py`; give the venv's Python (python3.12) Full Disk Access once in
System Settings > Privacy & Security.
Recordings, archives and the SQLite database have separate retention behavior; they are
not bounded by log rotation. Check available disk space during extended operation.
`status` describes launchd process state, not feed freshness, account access or the screen.
A successful bootstrap means registration succeeded; check the log and dashboard to
verify that the app actually started.

Bad saved settings fail service preflight rather than silently resetting preferences.
An unusable configuration or missing dependency can cause repeated failed starts until
fixed or removed. A failed bootstrap retains the plist because registration may have
partially succeeded; inspect `status` before retrying. Permission errors are reported as
unknown job state, not as a successful uninstall.

## Data, credentials and real-device limits

The service uses the same `data/config.yaml`, `devices.json`, channel mappings and `.env`
as the foreground app. It does not inherit a Terminal's exports. For persistence across
logins, use the app's project-local `.env` format (`NAME=value`, without shell syntax).
Keep that file private and out of source control.

- Public sports polling needs no configured paid API key. Its endpoints can fail or change.
- SmartThings input readback needs an explicitly selected device and a valid
  `SMARTTHINGS_TOKEN`; token presence does not establish authorization or expiry.
- Private ESPN fantasy leagues need `ESPN_S2` and `ESPN_SWID`; public leagues may not.
- Streaming-app sign-ins and subscriptions remain on the sticks. Local `adb` and the
  previously approved Mac key are required for Fire OS control.
- Dual-stick switching still requires verified active-input readback, correct channel/game
  identity and real playback/delay tests. The service adds no monitor control capability.

The replay proof tools and the app's recorded-playback sessions may need Apple Command
Line Tools (`swiftc`) for local OCR on first use. Enforced dry-run rejects recorded
device control before OCR preparation or navigation. A device-enabled runtime uses the
local reader only after an explicit recorded-session start. No system packages are
installed automatically.

Feed failures are retried by the engine. Live merged game state older than 60 seconds is
marked stale; automatic destination entry rechecks freshness before mutation. Missing
timing evidence holds automatic switches. Restarting does not turn cached data into new
evidence: the merger starts empty and must obtain fresh observations. A loaded service or
responsive dashboard alone is not proof of current sports data. The service does not add
a watchdog for a hung event loop or alter the engine's freshness policy.

The Mac must be powered on, logged in and awake. The engine uses `caffeinate` while it
knows games are live; it does not schedule a sleeping Mac to wake for the next slate.
Lid closure, power loss, logout and macOS privacy restrictions can interrupt operation.
In particular, a checkout under Documents may require access permission in the launchd
context even when Codex can read it. That must be tested after an authorized installation.
Do not claim unattended hardware readiness from the software tests alone.

## Updating the running service (scripts/deploy.py)

`scripts/deploy.py deploy` updates the checkout the login service runs from, without a
person watching. It fetches `integration/release`, runs the test suite on that commit in a
separate worktree (`data/deploy/stage`) at low priority (`nice -n 10`; `test_priority: background` in
deploy/deploy.yaml uses `taskpolicy -b`, about 2x slower; `--test-timeout MIN` overrides the 60 min limit), and only
then waits for a quiet moment: no game live and none starting within 15 minutes. It saves
`data/config.yaml` and `agent/model/` (content-addressed under `data/deploy/store`), checks
out the new commit detached, restarts this job and the watchdog, and watches health for
10 minutes against the old version's last 10 minutes of log. A crash loop, a dead API,
a burst of new errors or switch failures, or a feed/stick that is down only on the new
version rolls code, config and models back to the last good commit and pushes a notice
to the phone. While it watches, the watchdog leaves restarts to the deploy.

```sh
.venv/bin/python scripts/deploy.py deploy --dry-run   # fetch + tests + live check only
.venv/bin/python scripts/deploy.py deploy --wait 240  # wait up to 4 h for games to end
.venv/bin/python scripts/deploy.py status
.venv/bin/python scripts/deploy.py rollback           # undo the last deploy by hand
```

Exit codes: 0 deployed or current, 1 refused, 2 deferred for a live game, 3 tests failed
(nothing changed), 4 rolled back, 5 rollback failed (the app may be down; look at it).
It refuses to run with edited tracked files, a changed `requirements.txt` (install first,
then `--deps-ok`), under 1.5 GB of free disk, or while an earlier deploy's watch never
finished (`rollback` or `confirm` clears that). Settings live in `deploy/deploy.yaml`.
