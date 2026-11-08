# Samsung TV input switching and state reading: research (2026-11-08)

Scope: fastest reliable HDMI1/HDMI2 switching on a Samsung Tizen TV (Nick's is a "6 Series 55-inch",
exact model not recorded in the repo: `docs/device-trial-2026-11-02.md`). No code was changed.
Sources are numbered [n] and listed in section 4. "Verified" means I opened the page this session and
the claim is on it. "Unverified" claims are marked as such. Where I found no measurement, I say so.

## 0. Headline: the SmartThings cloud path just became a paid, deadline-bound path

Read this before anything else. It changes the ranking.

- SmartThings says free API access for individual developers was available **through November 3, 2026**,
  and updated plans rolled out **November 4, 2026** [1][2][3].
- If an API Access App owner has not picked a paid plan by **December 15, 2026**, "access to their API
  integrations may be limited, paused, or discontinued" and active API keys are deactivated [1][2].
  Between Nov 4 and Dec 15, already-installed integrations keep working but you cannot create or edit apps [1].
- Developer Plan: flat monthly rate, up to 100,000 cloud API calls per month, up to 10 installed apps,
  limited to certain countries at launch [1]. A third-party article quotes $4.99/month [3]; SmartThings' own blog
  page I fetched does not state the price. The plan page says "Plan details, pricing, and availability are
  subject to change" [3].
- Personal access tokens (PATs): the blog says they are meant for "temporary, individual developer testing
  and lightweight scripts within standard platform limits, rather than ongoing integrations" [1]. PATs made after
  30 Dec 2024 live 24 h [4][5]. Direct OAuth cloud API calls for individual use are listed as paid [1].
- Local integrations and the SmartThings app stay free [1].

Consequence for RedZone: `redzone/tv.py` `SmartThingsAuth` (OAuth app created through `POST /v1/apps`, refresh
token in `data/smartthings_oauth.json`) is exactly an "API Access App". The $0-budget rule in CLAUDE.md means
we should assume it stops working on **Dec 15, 2026** (about 5 weeks from today) unless Nick chooses to pay
(about $5/month, needs approval per the "ask before anything that costs money" rule; country eligibility
unknown). Whether a 24 h PAT keeps working for light personal scripting is not settled by the sources: the
blog calls PATs "temporary" and "lightweight", which does not read as a free ongoing path. Treat the cloud
path as a fallback that can vanish, not as the readback backbone.

## 1. Ranked findings (switch time and reliability)

Latency honesty: I found **no published, independent measurement** of Samsung HDMI switch time, of Tizen
websocket key latency, or of SmartThings command latency. The numbers below are either vendor claims,
one forum anecdote, or RedZone's own repo notes. They are labelled. Real numbers need a timing run on Nick's TV
(section 2, item R1).

| Rank | Path | Expected time to picture | Reliability notes | Cost |
|---|---|---|---|---|
| 1 | Tizen websocket, key `KEY_HDMI1`/`KEY_HDMI2` on an open socket | key delivery ~50 ms on an open socket, ~1 s fresh handshake (RedZone's own code comment in `tv.py`, not independently measured); then the TV's HDMI handshake, see below | Numbered keys are model-dependent. Reports: `KEY_HDMI` cycles but `KEY_HDMI1..4` do nothing and the call still reports success [6]; another user could only cycle with `KEY_HDMI` [8]; some HA users do use `KEY_HDMI1-3` in a source list [9]. No readback on this path. | $0 |
| 2 | Tizen websocket, `KEY_SOURCE` then arrow/Enter menu walk | slower (several keys, each needing the menu to render) | Works on more models; the Node-RED thread's suggested workaround is "input select" then left/right [6]. Fragile: the menu opens on the current input, so you need to know it. RTI's Tizen driver uses key sequences with per-TV delays for the same reason [10]. | $0 |
| 3 | Fire TV sends HDMI-CEC Active Source / One Touch Play (HOME or wake) | RedZone measured: command returns in ~2 s, same stream playing again only after ~9.6 s for YouTube TV (device trial note); Prime 57-73 s or never (`docs/instant-switching.md`) | Brings the stick's launcher forward and kills the player; Samsung TVs by default switch to a device that reports it turned on [11]; false CEC signals cause unwanted switching about every 30 min in one report [12]. | $0 |
| 4 | SmartThings `mediaInputSource.setInputSource` | cloud round trip plus readback polls (code polls up to 8 x 0.3 s); no independent timing found | Gives readback, but: now paid/deadline-bound (section 0); device command limit is 12 requests/minute per device [13]; several users report success returned while the input does not change on some Samsung audio/TV devices [14]. | free until Dec 15, then about $5/month (unverified country eligibility) |
| 5 | HDMI switch box with seamless scaler (hardware) | the TV sees a constant signal, so no TV re-handshake. Vendor claim only: "Don't Blink" seamless [15] | Adds a point of failure, the stick's HDCP is still negotiated, scalers cap at 1080p on cheap units [15]. | $199.99 for PureLink HDS-21RS (1080p, RS-232 + IR) [15]; discontinued or sold-out items exist [16] |
| 6 | Multi View (two inputs on one screen) | none (no switching) | Official page lists **2023 models only** (CU8000/Q70C and up have 2 screens; 4K/60 + 2K/60 or 2K/30) [17]. A "6 Series" is very unlikely to qualify. Audio from both screens plays at once [17]. DRM/HDCP behaviour of HDMI Prime/YouTube TV inside a tile is not documented on the page; unverified. | $0, but needs a newer TV |

### What drives the real switch time: the TV's HDMI handshake, not the command

- Samsung says its InstaPort runs HDCP authentication in the background with all connected sources so "the
  newly selected HDMI source can begin streaming content immediately" and "the time required to switch between HDMI
  inputs is reduced to less than one second" [18]. This is a vendor claim, India support page, no model list.
- The generic mechanism: without fast switching, hot plug, EDID read and HDCP retries (retry period up to 2 s per
  the HDCP spec) can total up to about 10 s; with fast switching about 1 s [19]. That is a chip-vendor (Synopsys)
  claim for its own IP, not a Samsung test.
- One forum user reported 3-5 s switching on an older Samsung (67A850) and varying times by source device [20] (old, 2008-ish
  era, low weight). One Samsung-community user tied a 10 s signal delay to Anynet+ and port changes and fixed it
  by disabling Anynet+ (anecdote) [21].
- Input Signal Plus / Game Mode: Samsung-community reports say Game Mode forces Input Signal Plus on and that
  toggling brings 1-2 s black-screen dropouts on some setups [22]. **I found no data on how Input Signal Plus changes
  handshake time.** A 1080p/4K60 stick should not need Input Signal Plus (it is for 4K 100/120 Hz and HDMI 2.1 sources).
  Test with it off on both ports.

### Practical implications

1. The fast path is: websocket key on a warm socket, HDMI ports that handshake fast, sticks that stay awake and keep
   a steady 1080p/4K60 output. RedZone already keeps the socket warm (`SamsungTV.warm()`/`maintain()`).
2. The 1 s target is only achievable if the TV's InstaPort-style pre-authentication works with Fire TV sticks. Unmeasured; test it.
3. Numbered keys may not work on a 6 Series. The repo already requires `tv_setup.py` to watch a key switch the TV
   before trusting it (`local_trusted`). That gate is correct and should stay.

## 2. Recommendations mapped to real code

R1. **Measure first (about 20 minutes, $0).** Add no new code; use `data/tv_input.jsonl` (`cmd_s`, `verify`
latency per `SamsungTV._log`) and `switch_times()` once `tv_setup.py` has proven a local key. Record per-port time
from key to picture with Input Signal Plus off, Anynet+ off, and the Game Mode setting each way. This is the missing
latency evidence for the whole topic. Needs Nick at the TV (CLAUDE.md: no disruptive TV tests while he watches).

R2. **Make the local path the primary, and plan for SmartThings loss.** In `redzone/tv.py` `set_input()` the order
is local first, which is right. Change the *assumption* (in `docs/instant-switching.md` and the `tvsetup.py` flow) that
SmartThings is the readback source of truth. After Dec 15, 2026 a `smartthings` method that is not paid will fail
as auth/403/401. The existing breaker/`st_fault` handling (`_rate_limited`, `_CloudBreaker`, `AUTH_RETRY_S`) will
turn that into HOME/CEC fallbacks, but Health will show `tv_auth` errors forever. Add an explicit "cloud disabled by
policy" state so it stops probing and stops alerting. (A decision-log line is required by CLAUDE.md when a default changes.)

R3. **Replace SmartThings readback with a local readback.** Options found:
   - Home Assistant's `ha-samsungtv-smart` gets accurate HDMI-vs-TV state only via SmartThings [23][24]; the local-only mode lacks it. No help.
   - The Tizen REST endpoint `http://<tv>:8001/api/v2/` returns TV info and power state but, per Tizen-forum reports, not the active HDMI input [25].
   - Best local readback we already have: the sticks. `dumpsys hdmi_control` active-source flag (`ACTIVE_SOURCE_CMD` in `redzone/control.py`)
     requires CEC on. With CEC off (the design in `instant-switching.md`) there is no readback.
   - A stick's `screencap` captures its own framebuffer, not what the TV shows, so it cannot prove the TV is on that input. Do not rely on it.
   - So: after the cloud is gone, local key delivery is "trusted" evidence only. That matches `evidence.trusted` in `set_input()`. Keep CEC on for **readback only**
     if possible: the CEC-off decision trades readback for not-pausing. Test whether CEC can be on while the sticks keep playing (see R5).

R4. **Do not use `cmd hdmi_control` as the fast path.** AOSP's shell command supports `onetouchplay`/`otp`,
`deviceselect`, `cec_setting`, `setsam` [26], but the repo's own device trial says "The Fire OS HDMI shell command remains
unavailable" (`docs/device-trial-2026-11-02.md`). Keep using HOME + `dumpsys hdmi_control` reads as in `control.py`.
`cec-ctl` [27] needs a Linux CEC adapter (v4l-utils), which the Mac and the sticks do not provide; a Raspberry Pi
plus USB-CEC adapter would cost money and still send Active Source (same effect as HOME without the launcher), so it
is low value here. Also note Fire TV does not implement CEC "power on" [28] and the Active Source behaviour changed
across Fire OS versions [29] (2016 post, old).

R5. **Stop Samsung auto-switching at the TV instead of at the sticks.** Samsung's own page gives a remote code to stop
auto HDMI switching: IR remote Mute, 7, 2, 9, Exit; Bluetooth smart remote Mute, Vol Down, Ch Down, Mute; update firmware first,
some models lack it [11]. Entering this on the 6 Series would let CEC stay on for the sticks (for readback and pause control)
without a stick's wake grabbing the TV. This is a TV-settings change by Nick, not a code change, and is unmeasured for
our case. The `docs/instant-switching.md` text already says the sticks' active-source flags alternated every 30 s on Nov 2-3,
which matches the "forced switch every 30 min" style reports [12].

R6. **Hide or avoid the source banner.** I found **no official consumer setting** to hide the Samsung input banner. One third-party page
mentions an Input Signal Plus toggle and the hotel-TV manual has an "External Source Banner" option; neither is confirmed for
consumer sets [30][31]. Do not build around it. If the banner matters, test on the real TV (R1); a hardware switch (R8) avoids it
because the TV never changes input.

R7. **Fix the key choice in `set_input()`.** It sends `KEY_HDMI1`/`KEY_HDMI2`. Given the reports that `KEY_HDMI1..4` can no-op while
`KEY_HDMI` cycles [6][8], add a second local method to `tv.py` and `tvsetup.py`: `KEY_HDMI` cycle (needs a known start input) and
`KEY_SOURCE` walk (only if the "Source" menu position is readable; today `try_source_key` just reports it). Gate it with the same
`tv_setup.py` observation test. With two sticks on HDMI1 and HDMI2 a single `KEY_HDMI` press toggles between them **only if the TV skips
unused ports**; with other ports present it cycles through them [8]. Hide unused inputs or plug the sticks in adjacent ports. Unverified: whether
a 6 Series lets you hide ports from the cycle.

R8. **Hardware fallback ($0-to-tiny is not realistic for a seamless switch).**
   - A seamless 2x1 scaler switch with RS-232 and IR exists, e.g. PureLink HDS-21RS at $199.99 [15]. 1080p max. This **costs money; needs Nick's approval**.
   - A plain 2x1 switch (no scaler) has IR/RS-232 [16] but the TV still re-handshakes HDCP when the active port changes: no speed benefit unless the switch holds
     the TV's input stable (HDCP is re-authenticated by the source behind the switch). Cheap Amazon-class switches with IR remote exist, but I did not verify a specific
     product's behaviour or price; do not assume.
   - Controlling it from the Mac needs an RS-232-to-USB adapter or an IR blaster: more spend.
   - Verdict: only if R1 shows the TV handshake is the bottleneck (>2 s) and Nick wants to pay. Note a switch also removes the TV-side Source banner and CEC interplay because the TV input never changes.

R9. **Multi View is not an option on this TV.** Only 2023+ models (CU8000/Q70C and up in the US) [17]. If Nick ever buys a newer TV, Multi View
with a large main tile and a small second tile could remove most switching, but audio plays from both unless set [17], and DRM behaviour is unknown.

R10. **Local LAN alternatives.** There is no official SmartThings Edge driver for Tizen TV input control; third-party RTI drivers use key sequences or SmartThings [10].
Home Assistant's `samsungtv_smart` (local keys, `ST_HDMI1` keys need SmartThings) [23][24] adds nothing beyond what `tv.py` does with `samsungtvws`.
Do not add Home Assistant. Keep the Python websocket client (`xchwarze/samsung-tv-ws-api` is documented as supporting Tizen 2016+; subnet/VLAN limit and "ask on every connect"
setting noted) [32]; set the TV's Access Notification to "First Time Only" so a restart does not re-prompt [32].

## 3. Risks

1. **Cloud cutoff on 2026-12-15** (section 0). Highest risk. Mitigation: R2/R3; decide with Nick whether to pay; log in `docs/memory/decisions.md`.
2. **Unverified TV model.** "6 Series 55-inch" does not say year. Capabilities (numbered keys, InstaPort, Multi View, mediaInputSource) vary by model/year.
3. **No local readback with CEC off.** A wrongly delivered key looks like success (success is returned even when the TV ignores it [6]). `local_trusted` proof at setup
   helps but does not detect later firmware changes. Samsung updates have broken things before (a Samsung-community thread titled "Firmware 1520 Breaks CEC For Samsung Frame 2022" appeared in search results; not opened, weak).
4. **Handshake unknown.** If HDCP/EDID negotiation takes 3-10 s on this TV [19][20], "~1 s" is impossible with any software path; only a seamless switch fixes it.
5. **Game Mode / Input Signal Plus side effects** (black dropouts) [22]. Do not enable Game Mode on the sticks' ports without testing during a quiet period.
6. **Websocket drops.** The Tizen socket can be closed by the TV when it sleeps; `key()` already retries once only if nothing was sent. Keep that rule.
7. **Source quality.** Many items here are forum posts (Samsung/HA/openHAB/Node-RED). They show patterns, not specs. Official docs verified: SmartThings rate limits and plans,
   Samsung Multi View, Anynet+ and InstaPort support pages, AOSP CEC docs and shell command.

## 4. Bibliography (all opened this session unless marked)

1. SmartThings Blog, "SmartThings API Additional Details for Developers and Business Partners" (2026). https://blog.smartthings.com/smartthings-updates/smartthings-api-additional-details-for-developers-and-business-partners/ (dates Nov 3/4 and Dec 15, 2026; 100,000 calls; PAT wording; local integrations free).
2. SmartThings Community, "A New Enhanced SmartThings API Experience", post #284 (2026). https://community.smartthings.com/t/a-new-enhanced-smartthings-api-experience/309947/284 (forwarded SmartThings email with the same dates).
3. Local Smart Home Guide, "SmartThings Developer Plan: the Home Assistant integration needs a paid plan after Dec 15, 2026" (Nov 2026, third party). https://localsmarthomeguide.com/articles/smartthings-api-developer-plan-home-assistant-integration-paywall/ ($4.99/month; HA replacement Dec 2).
4. SmartThings Community, "Changes To Personal Access Tokens (PAT)" (staff post, Dec 13 2024). https://community.smartthings.com/t/changes-to-personal-access-tokens-pat/292019 (24 h TTL for new PATs from 30 Dec 2024; lower /devices limits; move to OAuth). Older (2024).
5. Home Assistant `ollo69/ha-samsungtv-smart` docs, Smartthings.md. https://github.com/ollo69/ha-samsungtv-smart/blob/master/docs/Smartthings.md (PAT 24 h since 30 Dec 2024; ST_HDMI keys; accurate state with SmartThings).
6. Node-RED forum, "Samsung TV node KEY_CUSTOM" (Aug 2020, old). https://discourse.nodered.org/t/samsung-tv-node-key-custom/31151 (KEY_HDMI works, KEY_HDMI1..4 no-op with "success").
7. Search-reported openHAB/HA user reports of the same behaviour (not opened individually, treat as weak): the openHAB thread I opened (https://community.openhab.org/t/oh2-samsung-tv-binding-hdmi1-source-issue/18377) did NOT show it (it was a label-quoting issue), so I do not cite it for the key claim.
8. Home Assistant community, "Samsung Smart TV swapping too HDMI1-4 per" (Oct 20, 2023). https://community.home-assistant.io/t/samsung-smart-tv-swapping-too-hdmi1-4-per/629187 (cycles with KEY_HDMI; no direct per-port; likely a 7 Series).
9. Search-reported HA source-list posts mapping KEY_HDMI1-3 (not opened; weak). Cited only as "some users report it works".
10. RTI Control Tizen OS TV driver pages. https://www.rticontrol.com/tizen-os-for-samsung-tvs.html (found via search; not opened: input by sending source key N times; treat as search-reported).
11. Samsung US support, "Samsung TV switches sources by itself" (TSG10002314). https://www.samsung.com/us/support/troubleshoot/TSG10002314 (Anynet+ auto switch; remote codes to disable).
12. Samsung Community (EU), Fire TV forced input switch about every 30 min thread, via search result list (not opened; weak anecdote).
13. SmartThings Developer docs, Rate limits. https://developer.smartthings.com/docs/getting-started/rate-limits (12 requests/min per device for commands; 429/422; headers).
14. SmartThings Community, "mediaInputSource capability has disappeared for some Samsung soundbars" and "setInputSource syntax for SamsungTV?". https://community.smartthings.com/t/setinputsource-syntax-for-samsungtv/219538/2 (opened: example uses virtual device, says nothing confirms Samsung TV values; the "success but no change" claim is search-summarised, weak).
15. Markertek, PureLink HDS-21RS. https://www.markertek.com/product/plk-hds-21rs/purelink-hds-21rs-2-x-1-hdmi-switcher-with-scaling ($199.99; Don't Blink seamless; scaling to 1080p; RS-232 and IR).
16. HDTVSupply, PureLink HDS-21R. https://www.hdtvsupply.com/purelink-hds-21r.html (discontinued; RS-232 or IR; no stated scaling).
17. Samsung UK support, "Use the Multi View feature on your Samsung Smart TV". https://www.samsung.com/uk/support/tv-audio-video/use-the-multi-view-feature-on-your-samsung-smart-tv/ (2023 models only; HDMI OTT boxes supported; both audios at once). Same on Samsung Canada page https://www.samsung.com/ca/support/tv-audio-video/how-to-use-multi-view/.
18. Samsung India support, "What is an HDMI InstaPort technology". https://www.samsung.com/in/support/tv-audio-video/what-is-an-hdmi-instaport-technology-in-samsung-smart-tv/ (under 1 s claim).
19. Synopsys, "HDMI Fast Switching". https://www.synopsys.com/articles/hdmi-fast-switching.html (up to 10 s without, about 1 s with; HDCP retry up to 2 s; vendor claim).
20. AnandTech forum, "Switching to HDMI inputs takes 5-10 seconds" (old). https://forums.anandtech.com/threads/switching-to-hdmi-inputs-takes-5-10-seconds.235924 (search-reported; 3-5 s on a Samsung 67A850; not opened).
21. Samsung Community / Anynet+ 10 s delay anecdote, via search result summary (not opened).
22. Samsung US community, "Game Mode causes intermittent black screen dropouts 1-2 sec" (2025). https://us.community.samsung.com/t5/QLED-and-The-Frame-TVs/Game-Mode-causes-intermittent-black-screen-dropouts-1-2-sec-when/td-p/3517701 (search-reported; not opened).
23. ollo69/ha-samsungtv-smart README. https://github.com/ollo69/ha-samsungtv-smart (local keys; SmartThings adds source list and states; 2016+ TVs; VLAN limit).
24. Same repo, TheFab21 fork notes SmartThings pricing change (search-reported, not opened).
25. Tizen developer forum, "Establish a connection with a Samsung TV through its RESTful API". https://developer.tizen.org/forums/native-application-development/establish-a-connection-with-a-samsung-tv-through-its-restful-api-2 (search-reported: REST endpoint exposes basic info, not the active HDMI input; not opened).
26. AOSP, HdmiControlShellCommand.java. https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/hdmi/HdmiControlShellCommand.java (subcommands onetouchplay/otp, deviceselect, cec_setting).
27. Debian manpage for cec-ctl (v4l-utils). https://manpages.debian.org/unstable/v4l-utils/cec-ctl.1.en.html (search-reported examples `--image-view-on`, `--active-source phys-addr=`; not opened).
28. stb-tester, Fire TV manual. https://stb-tester.com/manual/firetv (Fire TV does not implement CEC power on; adb sleep_timeout workaround).
29. AFTVnews, "Fire TV gains the option to disable HDMI CEC in 5.2.4.0" (Dec 12, 2016, old). https://www.aftvnews.com/?p=19051.
30. Samsung hotel-TV manual "External Source Banner" via ManualsLib (search-reported; commercial models; not opened).
31. Tech Junctions, "How to remove banner from Samsung TV" (third party, search-reported; not opened).
32. xchwarze/samsung-tv-ws-api README. https://github.com/xchwarze/samsung-tv-ws-api (Tizen 2016+; subnet/VLAN limit; "First Time Only" access notification).
33. NickWaterton/samsung-tv-ws-api README. https://github.com/NickWaterton/samsung-tv-ws-api (port 8002 token on Allow; IP Control can switch inputs; no source readback documented; set_input_source example 'HDMI2' only works when watching TV, not art mode).
34. AOSP, HDMI-CEC control service. https://source.android.com/docs/devices/tv/hdmi-cec (One Touch Play wakes TV and makes the device active source; Android 12+ wake/sleep behaviour).
35. RedZone repo (local): `docs/instant-switching.md`, `docs/device-trial-2026-11-02.md`, `redzone/tv.py`, `redzone/tvsetup.py`, `redzone/control.py` (measured flip times, Fire OS hdmi_control unavailable).

Notes on citation quality: entries 7, 9, 10, 12, 20-22, 24, 25, 27, 30, 31 were seen only in search-result summaries and are marked as such; do not treat them as verified. Entries 1-6, 8, 11, 13-19, 23, 26, 28, 29, 32-34 were opened and the cited claim read on the page.
