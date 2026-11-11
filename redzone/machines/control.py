"""State machines owned by the control area (redzone/control.py, redzone/tv.py, redzone/tuners/).
See redzone/fsm.py for the rules. Only stdlib + redzone.fsm here.

Why: the control area's states, declared apart from control.py under the 2026-11-08 explicit state-machine move (decisions 2026-11-08).
"""
from __future__ import annotations

from redzone.fsm import ANY, Spec  # noqa: F401

# --- the hidden (standby) stick ------------------------------------------------------------------

STANDBY_READY = ("ready", "snap_parked", "replay_parked", "left_hold")   # flippable / usable states
STANDBY_BUSY = ("loading", "scrubbing", "holding", "stuck")

STANDBY = Spec(
    "control.standby", owner="control.Controller.standby_m",
    doc="What the hidden stick holds for the next flip: one state replaces hidden_target/hidden_ready/parked/"
        "_snap_park/_holding/_left/_stuck_since, so 'ready while holding' or 'left hold and parked' can't happen.",
    states={
        "empty": "holds nothing we know of (start, after a drop, a failed or cancelled tune)",
        "loading": "a target is being tuned, rebuilt or is unconfirmed: never flipped to",
        "scrubbing": "a fresh tune is being scrubbed behind live by the overlay (yttv_seek): never flipped to",
        "holding": "a pause-hold is running (time-shift buffer): resumes behind live, not flippable meanwhile",
        "stuck": "not ready with nothing running on it (buffered jump drops it after stuck_s)",
        "ready": "playing the target, confirmed: a flip shows it (live, or behind live per control.offset)",
        "snap_parked": "ready and paused on the line-up before its next snap: PLAY rolls it at the flip",
        "replay_parked": "rewound to a replay and held still: old footage, never used for a live switch",
        "left_hold": "the game we just left, paused at the dead ball we left it on (STK-07)",
    },
    edges={
        "empty": ("loading", "scrubbing", "ready", "replay_parked", "holding"),
        "loading": ("empty", "scrubbing", "holding", "stuck", "ready", "replay_parked", "left_hold"),
        "scrubbing": ("empty", "loading", "ready", "holding"),
        "holding": ("empty", "loading", "scrubbing", "ready"),
        "stuck": ("empty", "loading", "scrubbing", "holding", "ready"),
        "ready": ("empty", "loading", "scrubbing", "holding", "snap_parked", "replay_parked", "left_hold"),
        "snap_parked": ("empty", "loading", "scrubbing", "ready", "replay_parked"),
        "replay_parked": ("empty", "loading", "scrubbing", "ready"),
        "left_hold": ("empty", "loading", "scrubbing", "ready", "replay_parked"),
    },
    initial="empty")

PRELOAD_SLOT = Spec(
    "control.preload_slot", owner="control.Controller.slot_m",
    doc="The hidden stick's one background job slot (_preload_task): which kind of job owns it. With the switch "
        "task and the command lock it is the one answer to 'is a stick busy?' (Controller.stick_job).",
    states={
        "idle": "no background job on the hidden stick",
        "preload": "the planner's (or the phone's) preload: tune, then buffer or park",
        "refresh": "reloading the game we just left at the live edge (any planner preload displaces it)",
        "hold": "buffered jump: pause-hold of a ready standby (_hold_standby)",
        "rescrub": "re-tune + overlay scrub of the standby (buffer_standby / label_stale)",
        "rebuild": "STK-06: rebuilding a lost or drifted hold (_rebuild_buffer)",
        "reseek": "pulling a drifting parked replay back (_reseek)",
        "verify": "pre-game check: tune the target on the hidden stick and report (verify_hidden)",
    },
    edges={s: ("idle", "preload", "refresh", "hold", "rescrub", "rebuild", "reseek", "verify")
           for s in ("idle", "preload", "refresh", "hold", "rescrub", "rebuild", "reseek", "verify")},
    initial="idle")

# --- the screen: which stick the TV shows ----------------------------------------------------------

CONTROL_SCREEN = Spec(
    "control.screen", owner="control.Controller.screen_m",
    doc="Which stick the TV shows and whether we trust it: replaces active/_flipping/input_unknown as loose "
        "flags. Every flip start bumps _flip_gen (a read that spans a flip is stale).",
    states={
        "known": "the TV shows sticks[active] (our flip, the TV's readback or CEC agreed)",
        "flipping": "an input change is in progress: not cancellable, no reads trusted",
        "resuming": "the flip landed; confirming the game plays again on it (still not cancellable)",
        "unknown": "TV input unknown (unconfirmed flip, someone's remote, CEC lost): no switch until known",
        "reasserting": "input unknown and a bounded HOME re-assert runs on the stick meant to be on screen",
        "fallback": "the on-screen stick dropped off adb: `main` is the other stick, which the TV is NOT "
                    "showing, so the visible watchdog/restore don't touch it until the stick is back",
    },
    edges={
        "known": ("flipping", "resuming", "unknown", "reasserting", "fallback"),
        "flipping": ("known", "resuming", "unknown", "reasserting", "fallback"),
        "resuming": ("known", "flipping", "unknown", "reasserting", "fallback"),
        "unknown": ("known", "flipping", "reasserting", "resuming", "fallback"),
        "reasserting": ("unknown", "known", "flipping", "resuming", "fallback"),
        "fallback": ("known", "unknown", "flipping", "resuming", "reasserting"),
    },
    initial="known")

TWO_STICK = Spec(
    "control.two_stick", owner="control.Controller.two_stick_m",
    doc="Two-stick (hot standby) mode health: a failed flip degrades to single-stick (hidden is None) until "
        "TWO_STICK_RETRY_S has passed and the input is known again. Config/stick-count reasons are in view.",
    states={
        "on": "two-stick mode allowed (still needs config, 2 usable sticks, a flip path and HDMI inputs)",
        "degraded": "an input flip failed: single-stick until the retry time and a known input",
    },
    edges={"on": ("degraded",), "degraded": ("on",)},
    initial="on")

CEC = Spec(
    "control.cec", owner="control.Controller.cec_m", keyed=True,
    doc="Each stick's HDMI-CEC setting (key = host). Off = it never pauses on input loss; a HOME fallback "
        "turns it on for one flip and a restore task turns it off again.",
    states={
        "unread": "not read yet (or unreadable)",
        "on": "hdmi_control_enabled=1",
        "off": "hdmi_control_enabled=0 (scripts/stick_cec.py)",
        "restoring": "turned on for a HOME fallback; a task turns it off again in a few seconds",
    },
    edges={"unread": ("on", "off", "restoring"), "on": ("off", "restoring", "unread"),
           "off": ("on", "restoring", "unread"), "restoring": ("off", "on", "unread")},
    initial="unread")

# --- behind-live offset per target --------------------------------------------------------------

OFFSET = Spec(
    "control.offset", owner="control.Controller.offset_m", keyed=True,
    doc="How far behind the live edge a target's stream runs (key = target); `held` keeps the seconds "
        "(persisted to data/held.json). A fresh tune puts it back at live.",
    states={
        "live": "at the live edge (held 0)",
        "held": "behind live by a pause-hold, a left-game hold, a snap park or a booked stall",
        "scrubbed": "behind live by the overlay scrub, read off the time label",
        "restored": "behind live per data/held.json after a restart: no stick anchor yet, never re-scrubbed "
                    "just because the anchor is missing",
    },
    edges={"live": ("held", "scrubbed", "restored"), "held": ("live", "scrubbed", "restored"),
           "scrubbed": ("live", "held", "restored"), "restored": ("live", "held", "scrubbed")},
    initial="live")

# --- the visible-stick watchdog -----------------------------------------------------------------

VISIBLE = Spec(
    "control.visible", owner="control.Controller.visible_m",
    doc="The stick on screen should be playing what we put there: replaces _visible_bad_since/_visible_state "
        "as loose fields (the 'how' resets when healthy).",
    states={
        "ok": "playing (or nothing to watch)",
        "bad": "seen not playing (paused / frozen / stopped / none); re-checked before any repair",
        "repairing": "the resume ladder runs (MEDIA_PLAY, prompt, re-link) on the stick on screen",
        "restoring": "the supervisor's restore_visible runs (relink or resume)",
    },
    edges={"ok": ("bad", "repairing", "restoring"), "bad": ("ok", "repairing", "restoring"),
           "repairing": ("ok", "bad", "restoring"), "restoring": ("ok", "bad", "repairing")},
    initial="ok")

# --- the switch command -------------------------------------------------------------------------

COMMAND = Spec(
    "control.command", owner="control.Controller.command_m",
    doc="One switch command (submit -> _run -> execute): the newest wins, a flip is never cancelled. The "
        "command lock's owner and age are in Controller.view (a stuck lock is visible).",
    states={
        "idle": "no switch command",
        "queued": "submitted, waiting for the command lock",
        "running": "holds the lock: tuning / scrubbing / parking on the way to the flip",
        "waiting_safe": "waiting for the safe moment on the visible game (may_cut)",
    },
    edges={"idle": ("queued", "running"), "queued": ("running", "idle", "queued"),
           "running": ("waiting_safe", "idle", "queued"), "waiting_safe": ("running", "idle", "queued")},
    initial="idle")

ADOPT = Spec(
    "control.adopt", owner="control.Controller.adopt_m",
    doc="After a restart the screen may already show a game: adopt it once, never after a rewound picture.",
    states={
        "fresh": "just started: the first switch (or read_screen) may adopt what is on screen",
        "adopted": "adopted, or the chance passed: switches tune as usual",
        "replayed": "a rewound picture was shown since start: never read back as live",
    },
    edges={"fresh": ("adopted", "replayed"), "adopted": ("replayed",), "replayed": ()},
    initial="fresh")

# --- the TV (redzone/tv.py) ---------------------------------------------------------------------

SMARTTHINGS = Spec(
    "tv.smartthings", owner="tv.SamsungTV.st_m",
    doc="SmartThings cloud fault (hardening.smartthings): replaces st_fault/_auth_failed. The breaker "
        "(st_breaker) paces cloud_down and rate_limited; auth is blocked until the OAuth file changes or an hour.",
    states={
        "ok": "no fault",
        "auth": "sign-in refused: cloud blocked until tv_setup.py rewrites the OAuth file or AUTH_RETRY_S passes",
        "auth_retry": "an auth fault whose block expired: the next call (or the keepalive probe) tries once more",
        "rate_limited": "HTTP 429: no call until Retry-After (capped)",
        "cloud_down": "5xx / network: the breaker opens after two in a row",
    },
    edges={"ok": ("auth", "rate_limited", "cloud_down"),
           "auth": ("auth_retry", "ok", "rate_limited", "cloud_down"),
           "auth_retry": ("ok", "auth", "rate_limited", "cloud_down"),
           "rate_limited": ("ok", "auth", "cloud_down"),
           "cloud_down": ("ok", "auth", "rate_limited")},
    initial="ok")

# --- tuners -------------------------------------------------------------------------------------

STICK_LINK = Spec(
    "tuners.stick_link", owner="tuners.adb.Stick.link_m",
    doc="One Fire TV stick's adb connection (Stick.state). A failed connect waits _next_retry (one keepalive).",
    states={
        "unknown": "not tried yet",
        "connected": "adb connect + a shell probe answered",
        "offline": "unreachable, or adb says device offline",
        "unauthorized": "adb is not authorized on the stick (accept the prompt on the TV)",
    },
    edges={"unknown": ("connected", "offline", "unauthorized"),
           "connected": ("offline", "unauthorized", "unknown"),
           "offline": ("connected", "unauthorized", "unknown"),
           "unauthorized": ("connected", "offline", "unknown")},
    initial="unknown")

ADB_SHELL = Spec(
    "tuners.adb_shell", owner="tuners.adb.AdbShell.m",
    doc="The always-open `adb shell` to one stick. Broken = one adb process per command until retry_at.",
    states={
        "closed": "no shell process (not opened yet, killed after a hang or a lost reply)",
        "opening": "starting the process and its handshake",
        "ready": "open and answering",
        "broken": "failed to start/handshake: backing off (5 s doubling to 300 s) until retry_at",
    },
    edges={"closed": ("opening", "broken"), "opening": ("ready", "closed", "broken"),
           "ready": ("closed", "opening"), "broken": ("opening", "closed")},
    initial="closed")

PRIME_TUNE = Spec(
    "tuners.prime_tune", owner="tuners.tuners.PrimeTuner.tune_m", keyed=True,
    doc="One Prime deep-link tune per stick (key = host): what the confirm loop has seen since the link.",
    states={
        "idle": "no Prime tune yet on this stick",
        "linked": "deep link sent; waiting for a new player screen",
        "page": "the detail page (or its Watch Live panel) was seen",
        "pressed": "we pressed Watch / Watch Live on the page",
    },
    edges={"idle": ("linked",), "linked": ("page", "pressed"), "page": ("pressed", "linked"),
           "pressed": ("linked",)},
    initial="idle")

_YTTV_STAGES = ("none", "preparing_local_ocr", "local_ocr_ready", "checking_selected_connection",
                "selected_connection_verified", "search_opened", "team_catalog_opened", "matching_caption_observed",
                "recording_selection_sent", "dated_watch_selected", "replay_start_sent", "player_identity_observed",
                "player_title_observed")

YTTV_SEARCH = Spec(
    "tuners.yttv_search", owner="tuners.yttv_search.YTTVSearchProof.stage_m",
    doc="How far a recorded-game navigation (recorded.py + yttv_search) got: stages only move forward.",
    states={
        "none": "nothing done yet",
        "preparing_local_ocr": "recorded.py: preparing the local screen reader",
        "local_ocr_ready": "recorded.py: the screen reader is ready",
        "checking_selected_connection": "recorded.py: checking the selected stick only",
        "selected_connection_verified": "recorded.py: the selected stick answered as the saved model",
        "search_opened": "YouTube TV search opened",
        "team_catalog_opened": "the team's catalog page opened",
        "matching_caption_observed": "a caption matching the requested game was read",
        "recording_selection_sent": "the recording was selected",
        "dated_watch_selected": "the dated Watch entry was selected",
        "replay_start_sent": "start-from-beginning was sent",
        "player_identity_observed": "the player shows the requested game",
        "player_title_observed": "the player title was read (proof complete)",
    },
    edges={s: _YTTV_STAGES[i + 1:] for i, s in enumerate(_YTTV_STAGES)},
    initial="none")
