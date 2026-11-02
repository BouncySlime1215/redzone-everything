# Resume state — 2026-11-02

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
