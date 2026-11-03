# RedZone Everything

A local sports director for NFL, NBA, NHL and MLB. It ranks live games using the moment,
Washington teams, bets and fantasy interests, then proposes or performs a switch when the
estimated screen timing permits it. Your phone is the dashboard and remote.

**Current status:** software recovery and safety review are complete; physical Fire Stick,
provider, replay-position and display-input validation are still required. The app starts in
**dry-run**. No paid data service is required; viewing games still requires your own provider
access. It does not record or rebroadcast stream video.

## Run locally

```bash
.venv/bin/python -m redzone
# Use another port if an older session is still running:
PORT=8778 .venv/bin/python -m redzone
```

The app prints a LAN address and QR code. Keep the Mac awake and on the same network as the
phone. `scripts/install_service.sh check` performs a read-only service readiness check;
it does not start anything. A separately installed login service can run without Codex open.
See [unattended operation](docs/service-operation.md) for installation, enforced dry-run,
restart behavior, logs, credentials and sleep limits. Local runtime data is under `data/`.

## What the engine knows

Sports feeds describe events on the field. Fire TV media reports describe playback transport.
Neither directly identifies whether the TV currently shows a live play, an editorial replay,
an ad, or a loading screen. The Screen timing panel keeps those facts separate.

- Play-by-play timestamps plus a per-channel delay estimate produce **estimated** play, break
  and gap intervals. Missing timestamps, stale feeds and conflicting calibration widen
  uncertainty; absent evidence holds automatic switching.
- A safe exit must cover the command's full latency interval. The engine checks again after
  preparation and immediately before wake, launch, retry and input commands.
- Destination timing is refreshed before entry. A missed opportunity is abandoned; a failed
  command does not become the watched game.
- A new stream transition or uninterrupted known stream is required for tune confirmation.
  This is stronger than a timer or “app opened,” but still does not independently prove the
  exact matchup displayed. Provider links and game-title mappings need device validation.
- A display input command being accepted does not prove the input changed. An uncertain
  result stops further mutations until the visible input is established.

There is **no arbitrary switch quota, minimum dwell, cooldown or switch-count freeze**.
Selection considers the content, actual switching cost, explicit user focus/locks and timing
evidence. An operation already in flight is not duplicated. Zero mid-play cuts and complete
highlight coverage are goals, not guarantees from these public feeds.

## Device setup

First confirm the exact model and OS, finish the device's normal setup, and sign in to the
streaming apps yourself. Current control uses Fire OS/Android ADB. Vega devices have different
Amazon developer tooling and are **unsupported by this app's current controller**.

For a known Fire OS stick, enable ADB debugging through its developer settings. Use its IP
from the device's Network page. Only approve a debugging prompt on the intended device for
this Mac. The setup checker can connect and trigger that prompt; it is not a dry-run command.

```bash
# Target only the intended stick; do not scan unrelated devices.
.venv/bin/python scripts/doctor.py --no-scan --ip STICK_IP --display-type monitor
# A second intended stick can be supplied with another --ip.
```

The monitor option clears a previously saved Samsung target. A newly discovered Samsung is
never selected automatically. For an intended Samsung, select it explicitly with `--tv-ip TV_IP`.

**One stick:** the app can change its streaming app/channel after playback validation.
**Two sticks:** hot standby additionally requires supported, verified display-input control.
The current readback adapter is for an explicitly selected Samsung SmartThings device;
a normal monitor is not assumed to support it. Without readback, use one stick and select
its HDMI input manually. Do not point the app at an unrelated TV to make dual-stick mode appear ready.

Provider subscriptions, local channels, blackouts and stream delay must be checked on the
actual device. Settings → Channels accepts manual channel links/corrections; verified discovery
preserves those corrections. Automatic device discovery is opt-in and disabled in dry-run.
Account, purchase and sign-in screens stop automated discovery instead of being clicked through.

After setup, validate a user-selected channel, playback continuity, actual delay, pause/resume,
rewind step and recovery before enabling unattended switching. The CLI `scripts/rz.py` has
explicit device diagnostics (`now`, `tune`, `bench`, `flipcheck`); tune/bench/flipcheck change
playback or display inputs and should only be run when ready. The separate `screencap` diagnostic
captures a device screenshot; the normal engine does not use video capture.

## Phone controls

- **Now:** current proposal or confirmed stream, reasons, screen evidence, Focus, Back, Skip and Pause.
- **Focus:** prefer one game during meaningful action, with other games/replays during estimated breaks.
  Garbage-time handling can release a home-team preference.
- **Bets/Fantasy:** personalize scoring. Fantasy credentials stay in local `.env`; never commit them.
- **“I just saw it”:** match an exact-timestamp play while watching a confirmed live stream.
  Ambiguous taps, unknown timestamps, simulations and replays cannot establish precise delay.
  The action also accepts an explicit play ID. Microphone crowd-noise matches are advisory only.
- **Lab:** archived simulations and stored user observations. Labels/votes are observations for
  later analysis; they do not establish a trained visual classifier.
- **Pause:** stops automatic actions. Unknown outcomes require establishing the actual screen
  before resuming. Dry-run simulates decisions without device discovery or control.

Catch-up replay uses provider seek controls. Failed seek commands do not count as successful
replays. Completion is modeled only after an uninterrupted clip interval; exact footage and
seek behavior still need real-device validation. When no bounded break budget exists, the
engine does not invent time for a replay.

## Data and resilience

The project includes ESPN public scoreboards/play-by-play, NBA/NHL/MLB sources, source merging,
and provider event-link discovery. These unofficial endpoints can change or fail. Polling uses
caching/backoff; the recorder keeps feed observations and command outcomes, not stream video.
Optional ESPN Fastcast push has protocol/fixture coverage and remains **off by default** until
validated against current major-league events; polling remains the fallback.

Background jobs handle source refresh, verified provider discovery and recovery. They respect
dry-run, selected devices, unknown power state and controller locks. A sleeping/unknown standby
is not woken merely to preload. The server rejects non-LAN clients and cross-origin mutation
requests; it is intended for a trusted home network, not public deployment.

The supervisor (`redzone/supervisor.py`) checks the live system every ~10 s and fixes what it can:
the TV on the Fire TV home or an app page instead of the game, a stuck switch, idle with live
games on, a target/app that keeps failing (back off, mute for the night, use another source),
safe mode, stale feeds, adb drops, delay doubt that never narrows, a hold on a finished game, and
log growth. Every fix is rate-limited and logged to `data/supervisor.jsonl`. Each morning it
writes `data/reports/YYYY-MM-DD.md` (switches, failures by cause, key moments seen/missed, delays
measured, what it fixed). Set `supervisor.ntfy_topic` to get that summary on your phone through
the free ntfy app. Any night by hand: `.venv/bin/python -m redzone.supervisor report 2026-11-02`.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/eval.py
.venv/bin/python scripts/eval_sports.py nba --nights 999
.venv/bin/python scripts/eval_sports.py nhl --nights 999
.venv/bin/python scripts/eval_sports.py mlb --nights 999
# Combined feed/delay faults (same flags work with eval.py):
.venv/bin/python scripts/eval_sports.py mlb --nights 999 \
  --true-delay 41 --sigma 5 --lag-jitter --drop --late-plays --no-wallclock
```

See [verification notes](docs/verification-2026-11-02.md) for measured software results and limits.
Report cards separate modeled stoppage time from modeled tuning blackout. NFL/MLB live credit
requires the whole modeled play interval; replay credit requires modeled completion. NBA/NHL
coverage is event coverage, not proof that an entire possession was viewed. Some archived NFL
timestamps are estimated and archives are not video ground truth. Do not equate a scorecard
with real-TV validation, or an old optimizer report with the current build.

## Code map

`engine.py` coordinates feeds, scoring, decisions, commands and phone state. `decider/` models
screen timing and selection. `control.py`, `tuners/` and `tv.py` handle device transactions.
`standby.py` plans the hidden stick. `discovery/` and `autonomy.py` coordinate provider checks.
`sources/` contains public feeds and parsing; `lab/` contains simulations and grading;
`web/` contains the LAN API and phone UI. Configuration defaults are in
`redzone/default_config.yaml`; validated local overrides are in `data/config.yaml`.
