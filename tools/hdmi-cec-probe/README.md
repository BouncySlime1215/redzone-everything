# Experimental app-preserving CEC probe

Source preparation only, November 2, 2026. **Not compiled, copied to a stick, executed, or proven on Fire OS.** This helper has no connection to the RedZone application, service installer, or unattended control loop.

The current Samsung physically switched to HDMI1 after Home was sent to the older stick. That proves one CEC switching path, but Home leaves the running app. Both sticks reject `cmd hdmi_control` with "No shell command implementation". A missing shell command handler does not establish whether the underlying service's named API is accessible.

## Intended operation

`HdmiOneTouchPlay.java` resolves the device's own `IHdmiControlService.Stub.asInterface`, reads supported CEC device types, and checks the named manager/client/callback API. `--probe` ends there without constructing a manager or requesting activation. `--activate` creates the platform `HdmiControlManager`, obtains its playback client and invokes `oneTouchPlay` exactly once. A Java proxy implements the pure Java `OneTouchPlayCallback`; the platform client supplies its actual Binder callback wrapper. There are no hardcoded Binder transaction IDs, generated copies of platform Binder interfaces, `setAccessible` calls, permission grants, settings changes, key events, activity launches, or root identity.

The process requires the existing ADB shell UID 2000. The controlling agent must first inspect the actual device's `dumpsys package com.android.shell` and confirm `android.permission.HDMI_CEC` is granted. **The hardware owner's read on the older stick found zero `HDMI_CEC` entries, so its grant is not established and this route is currently blocked there.** AOSP's service also enforces that permission on the supported-types read and activation. Failure to resolve or access any interface, a permission denial, or a missing playback client is a stop condition. Do not change hidden-API policy or grant/escalate permissions to make it work. These are hidden/system APIs, not an officially supported Amazon public API, and availability can differ between firmware builds.

The first action sends CEC Text View On and Active Source; it does not choose an Android activity. The receiving TV may turn on. The helper waits at most 25 seconds for the callback after invocation, then exits. A controller must additionally bound the whole remote process: Binder setup/calls are synchronous and are not covered by the callback timeout. A timeout or lost connection is ambiguous delivery; never automatically retry or fall back to Home. Platform service logic may itself repeat its CEC messages while waiting for the television's power response.

## Build and execution gates

Local inspection found no installed Java runtime (`/usr/libexec/java_home -V` failed), and no `d8`/`dx` or Android SDK in the usual local SDK, Homebrew, and application paths. `/usr/bin/java` and `/usr/bin/javac` are macOS launch stubs. Nothing was installed or downloaded. Therefore no executable DEX artifact exists.

The source uses only JDK classes at compile time; Android names are resolved on-device. A future build requires a JDK and Android D8 dex compiler supplied through an authorized workflow. Compile for Java 8 and dex for the actual oldest target API. The normal D8 lambda transformation is required. Do not substitute a hand-written DEX or guessed Binder parcel to avoid missing build tools. Compilation and a local stub-based test would be needed before considering this source ready for a device.

Do not push or execute it until the hardware owner grants a device lane. Probe first. Activation is a separate explicit action after an accepted probe and a recorded baseline foreground/media state. Record the callback, resulting foreground activity, and media position/state; physically confirm the selected Samsung HDMI input and continuity. Repeat on the other stick only with its own permission and probe evidence. Even if the target remains in the foreground, an app may pause or reconnect when its HDMI input becomes inactive; switching speed alone does not prove warm playback.

## Evidence and limitations

- [AOSP HdmiPlaybackClient](https://android.googlesource.com/platform/frameworks/base/+/master/core/java/android/hardware/hdmi/HdmiPlaybackClient.java), reviewed blob `3e41d63b0365dc757c6ab5adb12bca943ace4e8c`: `@SystemApi`/`@hide`; `oneTouchPlay` leaves internal app selection to its caller and wraps the Java callback with the platform Binder stub.
- [AOSP HdmiControlManager](https://android.googlesource.com/platform/frameworks/base/+/master/core/java/android/hardware/hdmi/HdmiControlManager.java), reviewed blob `91b05c2815fae082964ead4a9feb0266e291c126`: named constructor and `getPlaybackClient`. Its constructor may register a hotplug listener; therefore probe mode only resolves it.
- [Older AOSP OneTouchPlayAction](https://android.googlesource.com/platform/frameworks/base/+/4f868ed/services/core/java/com/android/server/hdmi/OneTouchPlayAction.java), blob `7db991a65f84bffd915c1730907dab97cfbae813`: sends CEC messages and treats a TV-on power response as success. **Callback success does not prove the television visibly selected that HDMI input.**
- [AOSP HdmiControlService](https://android.googlesource.com/platform/frameworks/base/+/4f868ed/services/core/java/com/android/server/hdmi/HdmiControlService.java): enforces `android.permission.HDMI_CEC` for the service calls. [Current Shell manifest](https://android.googlesource.com/platform/frameworks/base/+/master/packages/Shell/AndroidManifest.xml) requests it; this is not evidence that Amazon granted it on either stick.
- [Android non-SDK restrictions](https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces): reflected names remain subject to runtime restrictions. No exemption or policy change is part of this approach.

The source/API references establish an intended AOSP route, not Amazon firmware compatibility. The new stick was identified as Fire OS 8.1.6.0 (RS8160/3380); its actual framework implementation has not been read or matched to these source revisions. The old stick must be checked independently.

## Samsung network fallback

The exact Samsung model/year and its own IP are needed before selecting a LAN protocol. The installed `samsungtvws` 3.0.7 package includes modern WebSocket, older encrypted, and legacy modules, but `redzone/tv.py` currently uses only port 8002 modern WebSocket control. Its inclusion does not establish this Samsung's compatibility. The [library's own documentation](https://github.com/xchwarze/samsung-tv-ws-api) separates modern Tizen, 2014/part-2015 encrypted, and pre-2014 legacy protocols. Connection authorization may require the normal TV prompt. Direct HDMI keys are model-dependent and must be verified physically or by independent selected-TV input status.

Samsung's documented `tizen.tvwindow.setSource` runs inside a TV application; it is not a Mac-to-TV LAN input-control endpoint. No network scans, TV pairing, app installation, or Samsung network commands were performed for this investigation.
