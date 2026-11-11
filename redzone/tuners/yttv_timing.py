"""[POSITION-FIX] Which content second a YouTube TV stick shows: the Nov 10 timing findings as small, flagged helpers.

Why: the TV session of Nov 10 (research POSITION-TRUTH, ledger L7, L13, L18) measured where the app's position doubt comes
from; each fix ships OFF behind its own `timing.*` flag (decisions.md, Nov 10), and with every flag off nothing here runs.

Measured on the hidden 2023 stick, espn and fs1, one night (numbers only; FINDINGS sections 1, 2, 5a-5f, 9):
  * The media session, read WITH the YouTube TV package, is an exact meter inside a tune (0.02 s), right after every
    seek and pause (102 of 102). Without the package a paused YouTube TV session is invisible and another app's idle
    session comes back instead.                                                   -> timing.playback_with_package
  * CENTER pauses the player; the session publishes "paused" with the exact position (0.70 s after the key, sd 0.08,
    n=102). The overlay label is a whole-second floor of that frozen frame. The right stamp for a label read is that
    pause event, not the screenshot (which comes 4.5-6 s later).                 -> timing.label_stamp_at_pause
  * One LEFT: the commit lands exactly 15.000 s before the frozen frame (69 of 69). At the live edge a RIGHT does not
    undo a LEFT: the commit jumps to about live (2 of 2).                       -> timing.no_right_probe_at_live
  * Pause and resume keys, per stick model (MEDIA_PLAY_PAUSE, n=11 and 6):     -> timing.key_constants

Pure helpers plus two read-only session reads; no key is pressed in this module.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from redzone.sync_edge import parse_uptime, position_now

LEFT_STEP_S = 15.000            # one committed LEFT, measured exactly (69 of 69 single steps; x3 45.000, x6 90.000)
LABEL_SD_S = 0.29               # one label read: a whole-second floor (uniform over 1 s)
EDGE_READ = "cat /proc/uptime; dumpsys media_session"   # the session and the clock its `updated` counts in, one call

FLAGS = ("label_stamp_at_pause", "playback_with_package", "no_right_probe_at_live", "per_tune_offset",
         "stats_for_nerds", "key_constants", "game_offset")

# Shipped key constants when timing.key_constants is on and the config names no stick (FINDINGS 5e and 9).
KEY_DEFAULTS = {
    "Fire TV Stick 4K (2023)": {"pause_s": 0.70, "resume_s": 0.69, "sd_s": 0.13},
    "Fire TV Stick 4K Plus": {"pause_s": 0.60, "resume_s": 0.53, "sd_s": 0.08},
}


def on(cfg, name: str) -> bool:
    """timing.<name>, false when unset or unreadable."""
    try:
        return bool(cfg.get(f"timing.{name}", False)) if cfg is not None and hasattr(cfg, "get") else False
    except Exception:
        return False


def _app(target: str | None) -> str:
    return str(target or "").split(":", 1)[0]


# ------------------------------------------------------------------------- item 2: the package on every read
def yttv_package(stick, target: str | None, cfg, pkg: str | None = None) -> str | None:
    """The package a playback read for `target` should pass. Flag off: `pkg` as given (today). On, for a YouTube TV
    target (or with no target, the YouTube TV path's own reads): the stick's YouTube TV package when `pkg` is empty."""
    if pkg or not on(cfg, "playback_with_package"):
        return pkg
    if target is not None and _app(target) != "youtubetv":
        return pkg
    return (getattr(stick, "packages", None) or {}).get("youtubetv") or pkg


# ------------------------------------------------------------------------- item 1: the pause event's stamp
@dataclass(frozen=True)
class SessionRead:
    """One read of the media session with the stick's uptime: where the stream is, and when (Mac wall clock)."""
    wall: float                  # Mac wall time of the read (midpoint of the round trip)
    state: str
    position_s: float | None     # the report's own position (at its `updated` instant)
    pos_now_s: float | None      # where the stream is at `wall`
    event_wall: float | None     # Mac wall time of the session's last state change (pause, play, seek)


def session_from_text(text: str, wall: float, pkg: str | None) -> SessionRead:
    """Pure: the EDGE_READ output -> SessionRead. event_wall = wall - (uptime - updated): the stick stamps every state
    change on its boot clock, so the pause instant is known to the read's round trip, not to the key's."""
    from redzone.tuners.adb import parse_playback
    ms = parse_playback(text or "", pkg)
    up = parse_uptime(text or "")
    pos = ms.get("position")
    upd = ms.get("updated")
    ev = None
    if up is not None and upd is not None and up >= upd / 1000.0:
        ev = wall - (up - upd / 1000.0)
    return SessionRead(wall, str(ms.get("state") or "none"), None if pos is None or pos < 0 else pos / 1000.0,
                       position_now(ms, up), ev)


async def read_session(stick, pkg: str | None) -> SessionRead | None:
    """One read-only EDGE_READ on the stick. None when the shell failed."""
    t0 = time.time()
    try:
        r = await stick.shell(EDGE_READ, timeout=4)
    except Exception:
        return None
    t1 = time.time()
    if not getattr(r, "ok", False) and not str(getattr(r, "out", "") or "").strip():
        return None
    return session_from_text(str(getattr(r, "out", "") or ""), (t0 + t1) / 2.0, pkg)


@dataclass(frozen=True)
class PauseStamp:
    """The frozen frame: its stream position and the wall time the picture stopped on it."""
    wall: float
    position_s: float


def pause_stamp(read: SessionRead | None) -> PauseStamp | None:
    """A paused session read -> the pause event. None unless the session says paused, with a position and a clock."""
    if read is None or read.state != "paused" or read.position_s is None or read.event_wall is None:
        return None
    return PauseStamp(read.event_wall, read.position_s)


@dataclass(frozen=True)
class TuneClock:
    """One tune's position counter on YouTube's clock, from ONE absolute read. `origin` = YouTube-clock time (program
    start + label) of stream position 0. Holds through pauses, seeks and resumes on the same tune (FINDINGS 5b: 27 reads
    over 15 minutes inside a 0.03 s band)."""
    origin: float
    sd_s: float = LABEL_SD_S
    kind: str = "label"

    @classmethod
    def from_label(cls, prog_start: float, label_s: float, stamp: PauseStamp) -> "TuneClock":
        return cls(prog_start + label_s - stamp.position_s)

    def frame_time(self, position_s: float) -> float:
        """YouTube-clock time of the frame at this stream position."""
        return self.origin + position_s

    def ui_delay(self, wall: float, position_s: float) -> float:
        """How far the picture runs behind YouTube's clock: the same quantity as LabelRead.ui_delay_s."""
        return wall - self.frame_time(position_s)


# ------------------------------------------------------------------------- item 6: per-stick key constants
@dataclass(frozen=True)
class KeyConstants:
    pause_s: float               # key sent -> the picture is frozen on the frame of that instant + pause_s
    resume_s: float              # key sent -> the picture plays again
    sd_s: float


def key_constants(cfg, stick) -> KeyConstants | None:
    """The pause/resume constants for this stick, or None (flag off, or a stick the config does not name)."""
    if not on(cfg, "key_constants"):
        return None
    table = cfg.get("timing.key_constants_by_stick") if hasattr(cfg, "get") else None
    table = table if isinstance(table, dict) and table else KEY_DEFAULTS
    for name in (getattr(stick, "name", ""), getattr(stick, "model", "")):
        row = table.get(name) if name else None
        if isinstance(row, dict) and "pause_s" in row and "resume_s" in row:
            return KeyConstants(float(row["pause_s"]), float(row["resume_s"]), float(row.get("sd_s", 0.13)))
    return None


def paused_span(t_pause_key: float, t_play_key: float, kc: KeyConstants) -> float:
    """Seconds the picture stood still between a pause key and a resume key, from the key times alone."""
    return max(0.0, (t_play_key + kc.resume_s) - (t_pause_key + kc.pause_s))


def root_sum(*sds: float) -> float:
    return math.sqrt(sum(float(s) ** 2 for s in sds))
