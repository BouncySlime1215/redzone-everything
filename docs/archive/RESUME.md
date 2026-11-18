# Resume state — 2026-11-02

## Latest checkpoint

The Samsung has physically switched to the older stick after Home. Home also leaves the
player; a measured YouTube TV restore took 9.642 seconds to fresh playing/speed-1 metadata,
and Prime paused while hidden. Stick-side Active Source is not selected-TV readback. The
resumed review removed automatic Home success based solely on that flag and removed blind
Prime Select presses on unreadable pages. Unknown input blocks automatic device navigation.

Both saved Fire OS sticks are ADB-authorized and reachable. The user-supplied Samsung network
address remains unreachable on the relevant remote-control ports; actual addresses and
profiles are kept only in ignored local configuration. The user confirmed the same Wi-Fi
passkey. Do not send them to create a WhiteSky account: their property-specific instructions
use the leasing-office passkey per device. The reason for the failed TV connection is unknown.
The ordinary Quick Settings proxy did not produce a switch; direct Quick Settings activity
launch was denied by Fire OS. No permission changes, root access or DRM bypass were attempted.

The login job on port 8778 was independently changed to `--allow-live` before this resumed
work. Saved dry-run was explicitly enabled and auto-switching paused for isolated tests.
The service can run independently of Codex, but unattended hardware readiness is incomplete.
Inspect the actual service/config state before any device actions; another session may
change it. Do not race another controller or disrupt the user's active computer work.

Prime replay navigation has bounded device proofs for two November 1 NBA matchups. The
recorded-session UI is Prime-only. YouTube TV's dated-Watch finisher is a standalone helper;
arbitrary Library navigation is not integrated. Protected video frames are unavailable, so
metadata and menu proofs do not establish picture continuity or good-moment switching.
See `docs/device-trial-2026-11-02.md`, `docs/provider-routing-readiness.md`, and
`docs/service-operation.md` for the measured results and limitations.

## Original recovery baseline

The Claude session was recovered into an isolated Codex checkout, preserving the original
project and snapshots of its unfinished worktrees. Do not merge stale worktree branches or
restart old workflows over this tree. The original session may auto-resume independently.

Recovered/integrated: persistent ADB transport, standby planning, optional Fastcast parser,
provider discovery, autonomy scheduling and diagnostics. Additional reviews corrected false
playback success, unsafe command retry, transaction commits before confirmation, stale input
assumptions, per-sport break timing, uncertain calibration, replay completion accounting and
scorecard blind spots. Four focused investigators reviewed screen evidence, switch lifecycle,
latency and measurement. User explicitly removed arbitrary switch-rate/dwell limits.

See README and docs/verification-2026-11-02.md for current behavior and results. The latest
source tests and scorecards supersede the earlier 158-test/zero-cut claims and local optimizer
reports. Those earlier reports describe a different build and weaker measurement.

Hardware remains the next validation boundary. Nick reports the existing stick is live and
is setting up another on a monitor. Exact model/OS/IPs and monitor input capability are pending.
No Fire Stick pairing, network scan, login, tune or HDMI change was performed during recovery.
Use only explicitly identified devices. An ordinary monitor has no supported automatic input
readback adapter yet; keep single-stick operation unless verified control becomes available.
Current app uses ADB, not Vega VDA. Never fall back to a previously saved unrelated Samsung.

Keep dry-run enabled until selected-device validation. Check real app/channel identity,
stream delay bounds, seek step, playback continuity through HDMI changes, blackout/login prompts,
command timeout recovery and actual visible switching latency. Provider playback is not proof
that a sports play is on screen. Timing is inferred from feeds plus delay, not TV pixels.

The isolated git origin originally points at the local Claude checkout. Never push to that
remote. GitHub backup must target the existing private BouncySlime1215/redzone-everything repo
explicitly, with no .env, runtime data, device tokens, local transcripts or virtual environment.
User previously authorized posting the finished work to that private repository.
