"""State machines owned by the decider area (redzone/decider/, redzone/engine.py). See redzone/fsm.py for the rules.

Why: decider states, stdlib + fsm only, so docs and tests read the specs without importing the decider (docstring; decisions 2026-11-08).

Only stdlib + redzone.fsm here: the specs are read by docs and tests without importing the decider.
"""
from __future__ import annotations

from redzone.fsm import ANY, Spec  # noqa: F401

# ----- the decider (redzone/decider/decider.py) ------------------------------------------------------------------

OWNER = Spec(
    "decider.owner", owner="decider.Decider.owner_m",
    doc="Which decider owns the TV this tick: set once per decide(), the others' gates read it",
    states={
        "idle": "no tick yet",
        "safe": "safe mode: repeated errors, nothing switches until Resume (or the timed auto-resume)",
        "user": "your override: paused, a locked / held game, focus mode, or hands-off after a tap",
        "replay": "a catch-up replay is on screen or just ended: the replay lifecycle picks the way back to live",
        "hard": "jump mode on, but the normal decider's must-go switch (crunch, ended, your tap, a chase...) goes",
        "normal": "the normal decider (jump mode off, or the screen's game isn't in the rotation)",
        "jump": "jump mode's rotation: it decides every optional switch; the normal decider's become stays",
        "buffered": "buffered jump (both sticks behind live): the editor decides, incl. the screen-gone free cut",
    },
    edges={s: (ANY,) for s in ("idle", "safe", "user", "replay", "hard", "normal", "jump", "buffered")},
    initial="idle")

DECIDER_SCREEN = Spec(
    "decider.screen", owner="decider.Decider.screen_m",
    doc="What the TV picture is: nothing known, a tune landing, a live game, a replay, or old replay footage",
    states={
        "unknown": "no game confirmed on screen (start, restart, dry run turned off)",
        "tuning": "a switch was committed; the picture is black until the tune's estimated arrival",
        "live": "a live game is on screen (decider.current)",
        "replaying": "a catch-up replay clip is playing (decider.replaying)",
        "after_replay": "the clip ended or was cut short: the picture is old footage until a live tune",
    },
    edges={
        "unknown": ("tuning", "live", "replaying"),
        "tuning": ("live", "tuning", "replaying", "after_replay", "unknown"),
        "live": ("tuning", "replaying", "unknown"),
        "replaying": ("after_replay", "unknown"),
        "after_replay": ("tuning", "live", "replaying", "unknown"),
    },
    initial="unknown")

START = Spec(
    "decider.start", owner="decider.Decider.start_m",
    doc="The first pick after a (re)start: free to pick, or backing off after picks that didn't land",
    states={
        "ready": "no failed start pending: the next start pick may go",
        "backoff": "a start pick didn't land: the next waits until start_retry_at (3 s doubling, max 60 s)",
    },
    edges={"ready": ("backoff",), "backoff": ("ready", "backoff")},
    initial="ready")

SCREEN_GONE = Spec(
    "decider.screen_gone", owner="decider.Decider.gone_m",
    doc="[buffered jump] the screen's game is unknown/gone and a buffered standby is held from a start/ended switch",
    states={
        "present": "nothing held (the screen's game is known, or there's no buffered standby to keep)",
        "keeping": "holding the buffered standby for buffered_jump.keep_standby_s (since = window start)",
        "let_go": "the keep window ran out: the normal start/ended switch goes",
    },
    edges={"present": ("keeping",), "keeping": ("let_go", "present"), "let_go": ("present",)},
    initial="present")

DECIDER_HOME = Spec(
    "decider.home", owner="decider.Decider.home_m",
    doc="A break visit: the game we left only for its break (home_break_visit) and when to head back",
    states={
        "none": "no visit: we're home, or nothing to go back to",
        "visit": "visiting during home's break; back at its next due moment",
        "visit_timed": "a long-break visit with a latest return time (home_return_at)",
    },
    edges={"none": ("visit", "visit_timed"), "visit": ("none", "visit_timed", "visit"),
           "visit_timed": ("none", "visit", "visit_timed")},
    initial="none")

SAFE = Spec(
    "decider.safe", owner="decider.Decider.safe_m",
    doc="Safe mode: three errors in five minutes hold the TV still until Resume (or the timed auto-resume)",
    states={"ok": "switching normally", "safe": "holding still after repeated errors (since = safe_since)"},
    edges={"ok": ("safe",), "safe": ("ok",)},
    initial="ok")

OVERRIDE = Spec(
    "decider.override", owner="decider.Decider.override_m",
    doc="Your strongest override this tick, in the order the decider honours them (a view of UserState + safe mode)",
    states={
        "auto": "autopilot",
        "focus": "focus mode on a game: leave only at its breaks (or for something huge)",
        "manual": "you just picked a game: hands off for decider.manual_pause_s",
        "locked": "you locked / held a game ('Watch this')",
        "paused": "you paused auto-switching",
        "safe": "safe mode",
    },
    edges={s: (ANY,) for s in ("auto", "focus", "manual", "locked", "paused", "safe")},
    initial="auto")

LOCK_WAIT = Spec(
    "decider.lock_wait", owner="decider.Decider.lock_m",
    doc="[lock wait] your locked game can't be reached: wait with a reason and a retry, then give the lock up",
    states={
        "idle": "the locked game is reachable (or nothing is locked)",
        "waiting": "can't reach it (no channel / a failed tune) since lock_since; retries at lock_retry_at",
        "expired": "gave up after decider.lock_wait.max_s: the engine releases the lock",
    },
    edges={"idle": ("waiting",), "waiting": ("idle", "expired"), "expired": ("idle", "waiting")},
    initial="idle")

ENTRY_WAIT = Spec(
    "decider.entry_wait", owner="decider.Decider.entry_m",
    doc="[cut timing] a switch waiting for a dead ball to land on (bounded by decider.entry.max_wait_s.<league>)",
    states={"idle": "no switch waiting for a landing", "waiting": "waiting on a dead ball in the key's game (since = first wait)"},
    edges={"idle": ("waiting",), "waiting": ("idle", "waiting")},
    initial="idle")

LEAVE_WAIT = Spec(
    "decider.leave_wait", owner="decider.Decider.leave_m",
    doc="[legal.leave_at_flip] the arbiter's cut, remembered until the flip's leave rules and a dead-ball landing both allow it",
    states={
        "idle": "no cut is waiting",
        "waiting": "the cut to the key's game goes when leave and landing allow it, or expires (too long, can't be entered, not worth it)",
    },
    edges={"idle": ("waiting",), "waiting": ("idle",)},
    initial="idle")

CATCHUP_ROUTE = Spec(
    "decider.catchup_route", owner="decider.Decider.catchup_m",
    doc="[arbiter.catchup_route] one missed must-see airing in pieces on the hidden stick, in the screen's dead time",
    states={
        "idle": "no clip in pieces",
        "airing": "a piece of the clip is on screen",
        "between": "the live game is back on screen; the next piece waits for dead time, within the 180 s cap",
    },
    edges={"idle": ("airing",), "airing": ("between", "idle"), "between": ("airing", "idle")},
    initial="idle")

JUMP_BREAK = Spec(
    "decider.jump_break", owner="decider.Decider.jump_break_m", keyed=True,
    doc="[jump mode] per game: since when the feed has said 'break' (the break counts as on screen a delay later)",
    states={"play": "the feed doesn't say break", "break": "the feed says break since `since`"},
    edges={"play": ("break",), "break": ("play",)},
    initial="play")

DELAY_LABEL = Spec(
    "decider.delay_label", owner="decider.screen.DelayBook.label_m", keyed=True,
    doc="[edge model] per target: is YouTube TV's overlay time label this tune's behind-live figure?",
    states={
        "none": "no label read for this tune (a fresh tune lands on the live edge)",
        "read": "a label read for the tune now on this target sets its behind_live",
        "stale": "the stream stalled after the read: behind +3 s, doubt +5 s until it's read again",
    },
    edges={"none": ("read",), "read": ("none", "stale", "read"), "stale": ("none", "read", "stale")},
    initial="none")

# ----- the engine (redzone/engine.py) ----------------------------------------------------------------------------

ENGINE_PENDING = Spec(
    "engine.pending", owner="engine.Engine.pending_m",
    doc="The one switch in flight: submitted to the controller until its done() resolves it (or it's superseded)",
    states={
        "idle": "no switch in flight",
        "pending": "a switch / replay was submitted; decisions wait for its outcome",
    },
    edges={"idle": ("pending",), "pending": ("idle", "pending")},
    initial="idle")

STANDBY_PLAN = Spec(
    "engine.standby_plan", owner="engine.Engine.standby_m",
    doc="Who decided what the hidden stick holds this tick (one owner per tick)",
    states={
        "idle": "nothing planned (one stick, paused, or a switch in flight)",
        "peek": "a game you opened on the phone holds the hidden stick",
        "rotation": "jump mode's rotation picks the hidden stick's next game",
        "buffered": "buffered jump: the rotation picks the game, the park step scrubs / parks it",
        "planner": "the standby planner (redzone/standby.py): break returns, replays, the likely next switch",
        "queue": "the game queue (redzone/noggin/gamequeue.py, queue.enabled) chose it",
    },
    edges={s: (ANY,) for s in ("idle", "peek", "rotation", "buffered", "planner", "queue")},
    initial="idle")

HOLD = Spec(
    "engine.hold", owner="engine.Engine.hold_m",
    doc="'Watch this' / lock from the phone: one fact (engine.hold mirrors decider.user.locked_game)",
    states={
        "none": "no hold",
        "held": "staying on the held game until Resume auto",
        "resuming": "the held game ended or went garbage: autopilot takes over at resume_at",
    },
    edges={"none": ("held",), "held": ("none", "resuming", "held"), "resuming": ("held", "none")},
    initial="none")

ENGINE_SKIP = Spec(
    "engine.skip", owner="engine.Engine.skip_m", keyed=True,
    doc="A skipped game: muted for SKIP_S from when the TV leaves it, then unmuted",
    states={
        "none": "not skipped",
        "leaving": "skipped while on screen: waiting for a dead ball to leave (the clock restarts each tick)",
        "muted": "left it: muted until the skip expires",
    },
    edges={"none": ("leaving", "muted"), "leaving": ("muted", "none"), "muted": ("leaving", "none")},
    initial="none")

FOCUS_SUSPEND = Spec(
    "engine.focus_suspend", owner="engine.Engine.focus_m",
    doc="Your focus game went garbage time: focus is suspended (roaming) until it's close again",
    states={"none": "focus as you set it", "suspended": "focus paused for garbage time; restored when it's close"},
    edges={"none": ("suspended",), "suspended": ("none",)},
    initial="none")

FAIL_COOL = Spec(
    "engine.fail_cool", owner="engine.Engine._fail_cool", keyed=True,
    doc="Per tuner target: a failed switch isn't retried before its cool-down ends (decider.fail_cooldown_s)",
    states={"ok": "may be tuned", "cooling": "failed a moment ago: not a candidate target until `until`"},
    edges={"ok": ("cooling",), "cooling": ("ok", "cooling")},
    initial="ok")
