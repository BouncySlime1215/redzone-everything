# Initial device trial — November 2, 2026

These were supervised selections through ADB remote keys. They do not demonstrate unattended
game discovery, navigation, or end-to-end automatic switching. The live app stayed in dry-run.
No account access was purchased or changed.

## Provider observations

| Provider | Selected recording | Evidence |
|---|---|---|
| YouTube TV | November 1 Philadelphia Eagles at Washington Commanders | Library action explicitly dated November 1; DVR player title matched; media-session description matched the teams and NBC 4. |
| Prime Video | November 1 Raptors vs. Lakers, Lakers Broadcast | Detail page dated November 1, 2026, stated included with the existing League Pass subscription; replay player title matched and showed duration 2:34:37. |

The user-selected profiles were used. Their names and device addresses remain in local runtime
configuration rather than this source-controlled report.

Two separate 45-second metadata captures completed locally:

- YouTube TV: 21 samples reported `playing`; foreground was the YouTube TV application.
- Prime Video: 22 samples reported `playing`; the selected player overlay identified the replay.
- Both returned empty UI text in all four accessibility reads per capture.
- Both retained the same playback base position and update timestamp throughout capture. The raw
  media sessions reported speed 1.0. A static base position is not proof of frozen playback, and
  extrapolation would not prove visible video progress.
- Prime's media description was only `PrimeVideo`, so it cannot identify the requested game.
- ADB screenshots exposed menus/player titles but showed black video planes. Actual game frames,
  play boundaries, commercial detection, and first-picture latency were not verified.

Local measurements are in `data/lab/nfl_yttv_device_trial_20261102.jsonl` and
`data/lab/nba_prime_device_trial_20261102.jsonl`; these files are excluded from source upload.

## Monitor control

Both sticks accepted authorized ADB commands. Both reported HDMI-CEC enabled. A Home command
on the older stick sent an Active Source request and changed its local active-source flag.
The user observed that the Monoprice monitor did **not** switch inputs. A local CEC flag must
therefore never be treated as proof of the monitor's selected input.

These Fire OS builds reported `No shell command implementation.` for `cmd hdmi_control`.
The new stick's SurfaceFlinger display-identification query returned no identification data.
Read-only Mac checks found no connected display, no attached Thunderbolt/USB peripheral, and
zero online CoreGraphics displays. There is no currently verified Mac-to-monitor DDC path.

## What remains unproven

1. Starting and confirming multiple different games without human/model navigation between steps.
2. Correct recovery from provider menu/profile changes, unavailable events, and loading states.
3. A verified automatic display-switch command with actual output observation.
4. Repeatable switching latency and independently measured live feed-to-screen delay.
5. Visible play-boundary decisions on aligned recordings and then actual live broadcasts.

Passing offline simulations or observing `playing` must not change these items to passed.

## Later Samsung trial

Both sticks were moved to a Samsung 6 Series 55-inch television. The user confirmed that
sending Home to the older stick switched the physical TV to HDMI 1. CEC topology places the
new stick on HDMI 2. This is a positive input-switch observation, but Home also leaves the
streaming app, so it does not establish a playback-preserving fast switch.

Sending WAKEUP to the already-awake older stick did not issue a new Active Source request.
The Fire OS HDMI shell command remains unavailable. No privileged helper has been installed
or run. The TV was subsequently connected to Wi-Fi and the user supplied its network address;
initial direct remote-interface probes and ARP resolution failed from the Mac, and a targeted
ping also failed from the new stick. Network control is not yet established.

Unattended Prime search has now selected two distinct November 1 games with exact date,
subscription-access and player-title checks. See [provider routing readiness](provider-routing-readiness.md)
for measured runs and negative cases. This does not establish unattended YouTube TV navigation,
visible play-boundary detection, or the complete two-provider switching loop.

### Input-and-resume fallback

An authorized trial sent Home to the new stick, then brought its observed existing YouTube
TV activity forward. The two commands returned after 2.100 seconds, but the same NFL title
with a new `playing`/speed-1 report was not observed until 9.642 seconds. Intermediate
reports retained the old metadata, then reported `playing` with speed 0 and no title while
the player reloaded. Command-return time is therefore not usable switch latency. This
fallback has not been enabled in automatic control.

The older Prime player paused when it lost the active HDMI source. Bringing its existing
activity forward alone did not claim the input. One Play command while it was hidden left
the media session at state 0 after the settling check. The NBA stream must not be described
as a verified warm standby from this test. Both findings require a real input-control path
and further selected-input/player recovery tests.

Raw local evidence: `data/lab/yttv_home_resume_trial.json`,
`data/lab/prime_activity_only_input_trial.json`, and
`data/lab/prime_hidden_resume_trial.json` (excluded from source upload).

## Resumed Quick Settings and network checks

After the Mac connection returned, both authorized ADB devices were reachable. The installed
service had independently been changed to permit live control. Automatic control was paused
and saved dry-run enabled before the following isolated tests.

The older stick's ordinary `android.intent.action.ALL_APPS` action resolved to the launcher's
`QuickSettingsProxyActivity`. A single launch with `am start -W` exceeded the 12-second
local timeout. It was not retried. Subsequent activity, screenshot, media, and CEC observations
showed the same YouTube TV NBA TV player, paused, with the other stick still the active source.
This did not establish Quick Settings display or an input change, and the paused starting
state cannot establish preservation of playing video.

A separate normal launch of the quick-settings package's declared `StartQSUIActivity` was
explicitly denied by Fire OS for missing `com.amazon.tv.permission.LAUNCHER_SETTINGS`.
No permission changes or protected-broadcast alternatives were attempted. The stock `input`
command lists `keyevent --longpress` but no configurable key-hold duration; its earlier Home
test opened the normal launcher, so it is not a verified physical held-Home equivalent.

The supplied Samsung address still had no resolved ARP entry. Connections to its modern
remote-control ports 8001/8002 and older protocol ports 55000/8080 all timed out. This shows
the TV is not reachable from this Mac on those paths; it does not establish why. The user's
confirmation of the same Wi-Fi passkey does not by itself demonstrate device-to-device
reachability. No TV network command or pairing was performed.

Local raw results are retained under `data/lab/quicksettings_*` and excluded from source
upload. Neither these tests nor unit tests establish fast unattended HDMI switching.
