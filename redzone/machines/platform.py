"""State machines owned by the platform area. See redzone/fsm.py for the rules.

Why: platform states, stdlib + fsm only, so scripts/deploy.py and the watchdog's venv can import them (docstring; decisions 2026-11-08).

Stdlib + redzone.fsm only: scripts/deploy.py and the watchdog's venv import this module too.
"""
from __future__ import annotations

from redzone.fsm import ANY, Spec  # noqa: F401

# --- supervisor (redzone/supervisor.py) ------------------------------------------------------------

SUPERVISOR_TARGET = Spec(
    "supervisor.target", owner="supervisor.Supervisor.targets", keyed=True,
    doc="One way to watch (app:channel) as the repeat_failures rule sees it. Memory only: a restart "
        "forgets back-offs and night mutes (the engine's own cool-downs still apply).",
    states={
        "available": "no supervisor penalty; may be tuned",
        "backed_off": "failed backoff_after+ times in fail_window_s: engine cool-down pushed out backoff_s",
        "muted_night": "source failures reached mute_after (or it caused a stuck safe mode): skipped "
                       "until the night ends (6 AM ET)",
    },
    edges={
        "available": ("backed_off", "muted_night"),
        "backed_off": ("available", "muted_night"),
        "muted_night": ("available",),
    },
    initial="available")

SUPERVISOR_APP = Spec(
    "supervisor.app", owner="supervisor.Supervisor.apps", keyed=True,
    doc="One streaming app (youtubetv, prime, espn...). A muted app mutes every one of its targets, "
        "including ones that show up later in the night. Memory only.",
    states={
        "available": "the app's targets are judged one by one",
        "muted_night": "app_mute_targets of its targets muted (or app_mute_failures source failures): "
                       "every target of this app is skipped until the night ends",
    },
    edges={"available": ("muted_night",), "muted_night": ("available",)},
    initial="available")

SUPERVISOR_LOOP = Spec(
    "supervisor.loop", owner="supervisor.Supervisor.loop",
    doc="Is the supervisor's background loop running, and is it acting on its rules right now?",
    states={
        "stopped": "run() isn't running (not started yet, or it ended/was cancelled)",
        "running": "loop alive; every rule runs each step",
        "disabled": "loop alive but supervisor.enabled is off: steps do nothing",
        "yielded": "loop alive but a recorded session drives the screen: steps do nothing",
    },
    edges={
        "stopped": ("running",),
        "running": ("disabled", "yielded", "stopped"),
        "disabled": ("running", "yielded", "stopped"),
        "yielded": ("running", "disabled", "stopped"),
    },
    initial="stopped")

SUPERVISOR_CONDITION = Spec(
    "supervisor.condition", owner="supervisor.Supervisor.conditions", keyed=True,
    doc="A watched condition (idle_with_live, stale_feeds, adb:<host>, delay_doubt:<target>...): how long "
        "it has held without a break. A rule acts only once it has held long enough.",
    states={
        "clear": "the condition is false (or a fix just re-armed its clock)",
        "held": "the condition has been true since `since`",
    },
    edges={"clear": ("held",), "held": ("clear",)},
    initial="clear")

SUPERVISOR_SCREEN = Spec(
    "supervisor.screen_check", owner="supervisor.Supervisor.screen",
    doc="The screen_not_game two-look confirm: the TV must look wrong on two checks screen_confirm_s "
        "apart before the supervisor touches the visible stick.",
    states={
        "clear": "nothing wrong seen (or blocked: dry run, switch in flight, replay, watchdog acting...)",
        "suspect": "home screen / app page seen once for this target; waiting to look again",
        "restoring": "confirmed twice: restore_visible is running on the stick on screen",
    },
    edges={
        "clear": ("suspect",),
        "suspect": ("clear", "restoring"),
        "restoring": ("clear", "suspect"),   # suspect again = the fix was rate-limited
    },
    initial="clear")

SUPERVISOR_SAFE_EPISODE = Spec(
    "supervisor.safe_episode", owner="supervisor.Supervisor.safe_episode",
    doc="The decider's safe mode as recorded episodes for the report and error budget (an edge "
        "detector over decider.safe_mode; the decider owns safe mode itself).",
    states={
        "unknown": "not looked yet this run (a first look at 'off' records nothing)",
        "off": "last recorded: safe mode off",
        "on": "last recorded: safe mode on (an 'episode on' row was written)",
    },
    edges={"unknown": ("off", "on"), "off": ("on",), "on": ("off",)},
    initial="unknown")

# --- recorded playback + background device work -----------------------------------------------------

RECORDED_SESSION = Spec(
    "recorded.session", owner="recorded.RecordedPlayback.session",
    doc="A recorded-game session on one stick. While it is anything but idle, live switching, the "
        "supervisor and background device work stay off.",
    states={
        "idle": "no recorded session",
        "preparing": "start() accepted: live work cancelled and drained, device jobs suspended",
        "navigating": "the navigation task is driving the selected stick",
        "finished": "navigation ended (playback requested or nothing found); waiting for Stop",
        "failed": "preparation or navigation stopped early (error, timeout, cancelled); waiting for Stop",
    },
    edges={
        "idle": ("preparing",),
        "preparing": ("navigating", "failed", "idle"),
        "navigating": ("finished", "failed", "idle"),
        "finished": ("idle",),
        "failed": ("idle",),
    },
    initial="idle")

DISCOVERY_DEVICE_WORK = Spec(
    "discovery.device_work", owner="discovery.runtime.DiscoveryRuntime.device_work",
    doc="Background device maintenance (stick package reads, autonomy jobs). A recorded session "
        "suspends it; recorded.active also counts as suspended while set.",
    states={
        "running": "device work may run on its 15 s loop (still gated by autonomy.enabled, dry run)",
        "suspended": "a recorded session drained it; nothing touches a device until resume",
    },
    edges={"running": ("suspended",), "suspended": ("running",)},
    initial="running")

# --- fastcast (redzone/sources/fastcast.py) ---------------------------------------------------------

FASTCAST_CONNECTION = Spec(
    "fastcast.connection", owner="sources.fastcast.FastcastClient.conn",
    doc="The one ESPN Fastcast websocket.",
    states={
        "idle": "nothing wanted (no live games): no socket, no reconnect warnings",
        "connecting": "looking up the host and opening the socket",
        "connected": "session started (ESPN said C); topics subscribe over it",
        "backing_off": "the last session failed; waiting backoff_s(failures) before reconnecting",
        "stopped": "stop() was called: run() returns and never reconnects",
    },
    edges={
        "idle": ("connecting", "stopped"),
        "connecting": ("connected", "backing_off", "idle", "stopped"),
        "connected": ("idle", "backing_off", "connecting", "stopped"),
        "backing_off": ("connecting", "idle", "stopped"),
        "stopped": (),
    },
    initial="idle")

FASTCAST_TOPIC = Spec(
    "fastcast.topic", owner="sources.fastcast._Sub.m", keyed=True,
    doc="One subscribed Fastcast topic (one game). Only `live` with a usable document reaches the merger.",
    states={
        "subscribing": "S sent; waiting for ESPN's answer",
        "subscribed": "ESPN accepted; waiting for the H checkpoint pointer",
        "loading": "downloading a checkpoint; updates meanwhile wait in TopicDoc.pending",
        "live": "checkpoint installed; patches apply in mid order",
        "dropped": "unsubscribed or dropped for a retry (client._retry says when)",
    },
    edges={
        "subscribing": ("subscribed", "loading", "live", "dropped"),
        "subscribed": ("loading", "live", "dropped"),
        "loading": ("live", "dropped"),
        "live": ("loading", "dropped"),
        "dropped": (),
    },
    initial="subscribing")

# --- merger (redzone/sources/merge.py) ---------------------------------------------------------------

MERGE_FEED = Spec(
    "merge.feed", owner="sources.merge.Merger.feeds", keyed=True,
    doc="One source's copy of one game (key 'source|game'): trusted, or benched after backward jumps.",
    states={
        "trusted": "its updates may lead the merged game",
        "regressing": "jumped backwards inside REGRESSION_WINDOW_S, fewer than UNHEALTHY_REGRESSIONS times",
        "benched": "UNHEALTHY_REGRESSIONS backward jumps: ignored for this game for BENCH_S, then re-baselined",
    },
    edges={
        "trusted": ("regressing", "benched"),
        "regressing": ("trusted", "benched"),
        "benched": ("trusted",),
    },
    initial="trusted")

# --- autonomy (redzone/autonomy.py) -------------------------------------------------------------------

AUTONOMY_JOB = Spec(
    "autonomy.job", owner="autonomy.Scheduler.state[job].m", keyed=True,
    doc="One scheduled chore (Sunday Ticket scan, nightly app check...): single-flight, time-boxed.",
    states={
        "idle": "nothing due: between windows, or this window's slot is done",
        "waiting": "inside a window but its safety check (or its group) said not now; re-checks after not_before",
        "running": "a run is in flight (scheduled or 'run now'); nothing else may start it",
        "retrying": "the last try failed inside the window; tries again after not_before",
    },
    edges={
        "idle": ("waiting", "running"),
        "waiting": ("running", "idle", "retrying"),
        "running": ("idle", "retrying", "waiting"),
        "retrying": ("running", "waiting", "idle"),
    },
    initial="idle")

AUTONOMY_STICK = Spec(
    "autonomy.stick_presence", owner="autonomy.DeviceHealer.sticks", keyed=True,
    doc="Is a saved stick on the network? Missing 2+ minutes -> the healer looks for its new address.",
    states={
        "present": "connected, or asking for adb approval (that address is right)",
        "missing": "not answering since `since` (offline_since)",
    },
    edges={"present": ("missing",), "missing": ("present",)},
    initial="present")

AUTONOMY_TV = Spec(
    "autonomy.tv_presence", owner="autonomy.DeviceHealer.tv",
    doc="The Samsung TV as the healer last saw it (power, lost-since and on-LAN move together).",
    states={
        "unknown": "not checked yet, or the Mac is away from home (clocks start fresh)",
        "on": "the TV's status page answered: on",
        "standby": "the TV's status page answered: standby",
        "not_answering": "no answer since `since` (tv_lost_since); tv_on_lan says if its MAC is at the saved address",
    },
    edges={
        "unknown": ("on", "standby", "not_answering"),
        "on": ("standby", "not_answering", "unknown"),
        "standby": ("on", "not_answering", "unknown"),
        "not_answering": ("on", "standby", "unknown"),
    },
    initial="unknown")

AUTONOMY_HEALER = Spec(
    "autonomy.healer", owner="autonomy.DeviceHealer.pass_",
    doc="The device healer's single-flight pass (every ~15 s, time-boxed).",
    states={"idle": "no pass running", "checking": "a pass is running; another check() returns at once"},
    edges={"idle": ("checking",), "checking": ("idle",)},
    initial="idle")

# --- governor (redzone/governor.py) -------------------------------------------------------------------

GOVERNOR_LEVEL = Spec(
    "governor.level", owner="governor.Governor.m",
    doc="How much optional work the Mac can take (reels, lab, archive, OCR, local model). Up at once, "
        "down only after calm_s.",
    states={
        "normal": "everything runs",
        "eco": "battery/busy/hot/memory/disk: no reels, lab or archive; OCR and idle polls slower",
        "critical": "as eco, plus no score-bug OCR and no local model",
    },
    edges={"normal": ("eco", "critical"), "eco": ("normal", "critical"), "critical": ("normal", "eco")},
    initial="normal")

# --- crunch (redzone/web/phone_v2.py) -----------------------------------------------------------------

CRUNCH = Spec(
    "phone.crunch", owner="web.phone_v2.CrunchTracker", keyed=True,
    doc="A game's crunch-time label on the phone (one per game): entering as it heats up, in, then "
        "leaving for a short while after it cools down.",
    states={
        "out": "not crunch time",
        "entering": "crunch score at the edge (within the entering band below the floor)",
        "in": "crunch time (score at or over the floor)",
        "leaving": "was in crunch time and dropped below the edge: shown for leave_s, then out",
    },
    edges={
        "out": ("entering", "in"),
        "entering": ("in", "out"),
        "in": ("leaving", "out"),        # out = the game left the list (ended, no longer live)
        "leaving": ("in", "entering", "out"),
    },
    initial="out")

# --- deploy (scripts/deploy.py) -------------------------------------------------------------------------

DEPLOY = Spec(
    "deploy.lifecycle", owner="scripts.deploy.Deployer.m",
    doc="The safe-deploy lifecycle, persisted in data/deploy/state.json ('machine'). The deploy gate, "
        "the watchdog marker and `deploy.py status` all read this one state.",
    states={
        "idle": "no deploy running (initial; also after tests failed, a dry run or 'already current')",
        "testing": "the suite runs on the new commit in data/deploy/stage; nothing changed yet",
        "waiting_idle": "tests passed; waiting for no live game (live_wait_s)",
        "deferred": "a game was live: nothing changed; try again after the games",
        "switching": "config/models saved, checking out the new commit and restarting (marker on)",
        "watching": "health watch of the new version against the old one (marker on)",
        "deployed": "healthy: HEAD recorded as good",
        "rolling_back": "worse (or asked by hand): putting the last good code + config/models back",
        "rolled_back": "the rollback came up",
        "rollback_failed": "the rollback did not come up: deploys refuse; confirm needs --force",
        "interrupted": "a switch/watch never finished (process died, Mac slept): deploys refuse until "
                       "rollback or confirm",
    },
    # Every "-> deployed" from a resting state is `deploy.py confirm` (mark HEAD good by hand).
    edges={
        "idle": ("testing", "waiting_idle", "rolling_back", "deployed"),
        "testing": ("idle", "waiting_idle"),
        "waiting_idle": ("deferred", "switching", "idle"),
        "deferred": ("testing", "waiting_idle", "rolling_back", "idle", "deployed"),
        "switching": ("watching", "rolling_back", "interrupted"),
        "watching": ("deployed", "rolling_back", "interrupted"),
        "deployed": ("testing", "waiting_idle", "rolling_back", "idle"),
        "rolling_back": ("rolled_back", "rollback_failed", "interrupted"),
        "rolled_back": ("testing", "waiting_idle", "rolling_back", "idle", "deployed"),
        "rollback_failed": ("rolling_back", "deployed"),     # deployed = confirm --force only
        "interrupted": ("rolling_back", "deployed"),
    },
    initial="idle")
