"""PARK on Prime Video: which games the show may take from Prime, what a Prime tune costs, that Prime has no back
route, and each Prime game's K from the hidden stick's own score-bug reads. Behind `park.prime.enabled` (OFF).

Everything here reuses what the repo already has; nothing is measured anew:
  - which apps can be sent back (rewound): `replay.apps` (YouTube TV only; MEDIA_REWIND is continuous on Prime and a
    Prime seek is untested, decisions 2026-11-07), so a Prime game gets no back route in the planner;
  - what a Prime tune takes: `decider.switch_cost.prime` through `contracts.switching.ConfigSwitchPaths` (27 s, the
    LatencyBook median), plus the pause-hold that puts a fresh Prime stream where a YouTube TV tune and its read put
    one (Prime's pause holds: 60 s pause -> 67.7 s behind, held 4 min, decisions 2026-11-07);
  - where a Prime stream is: the media-session position is an EPOCH (ms since 1970, `sync_edge.EPOCH_POSITION`) on
    the Nov 4 content, the wall-clock stamp of the frame on screen, so a Prime tune's place is read with no gesture;
  - each Prime game's K (content minus feed time, as the PARK timing model defines it): a score-bug read (`screen.scorebug`, Nick's Nov 5
    OK: numbers only, never frames) of a PAUSED frame has an exact frame time (its epoch), so its running game clock
    against the feed's plays (`decider.sync.clock_sample`) is one K sample, fused and gated by `sync_edge.EdgeTracker`
    with the `sync.edge.k_min_reads` / `k_sd_max_s` bars.

Pure: no device, no network, no file. With the flag off `takes` answers exactly what the show took before (YouTube TV
channels only) and nothing else here is called.

Why: Thursday Night Football and Friday NBA games are on Prime Video, and PARK took YouTube TV channels only; the repo
already held the Prime tuner, the pause-hold, the epoch position and the score-bug reader, so PARK reuses them.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable

from redzone.screen.scorebug import PRIME  # the repo's own name for the Prime app


def _setting(key: str, get: Callable[..., object] | None = None) -> float:
    """park.prime.<key> from `get` (the app's config) when it gives a number, else the shipped default (rule K2)."""
    from redzone import config
    v = get(f"park.prime.{key}", None) if get is not None else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(config.default(f"park.prime.{key}"))


def app_of(target: str) -> str:
    return str(target or "").split(":", 1)[0]


def prime_on(get: Callable[..., object]) -> bool:
    return get("park.prime.enabled", False) is True


def takes(target: str, get: Callable[..., object]) -> bool:
    """May the PARK show take a game on this target? YouTube TV always (as before); Prime only with park.prime.enabled
    and an event link (a GTI, as channels._tunable says)."""
    t = str(target or "")
    if t.startswith("youtubetv:"):
        return True
    return prime_on(get) and t.startswith("prime:amzn1.")


def rewinds(target: str, get: Callable[..., object]) -> bool:
    """Can a stream of this app be sent back (the read gesture's LEFT presses)? `replay.apps` is the one list."""
    apps = get("replay.apps", None) or ["youtubetv"]
    return app_of(target) in {str(a) for a in apps}


def hold_s(show_delay: float, tune_lag: float) -> float:
    """How long a fresh Prime stream is paused after its tune so it sits where a YouTube TV tune with its read sits:
    `tune_lag` behind the show's edge, the show itself `show_delay` behind the live edge."""
    return max(0.0, float(show_delay) + float(tune_lag))


def tune_cost(target: str, cfg, *, yttv_s: float, show_delay: float, tune_lag: float) -> float:
    """Seconds from the tune key to a stream ready to park. YouTube TV: `yttv_s` (the show's own number, unchanged).
    Prime: decider.switch_cost.prime (via ConfigSwitchPaths, the single source) plus the hold."""
    if app_of(target) != PRIME:
        return float(yttv_s)
    from redzone.contracts.switching import ConfigSwitchPaths
    return ConfigSwitchPaths(cfg).tune(PRIME) + hold_s(show_delay, tune_lag)


def chunks(total_s: float, chunk_s: float) -> list[float]:
    """A hold cut into equal pauses no longer than `chunk_s` (buffered_jump.hold_chunk_s: one 75 s Prime pause came back
    'stopped' 3 of 3), none shorter than 1 s (prime_pause holds nothing under 1 s). Under 1 s in all: no hold."""
    total, c = max(0.0, float(total_s)), max(1.0, float(chunk_s))
    if total < 1.0:
        return []
    n = max(1, min(math.ceil(total / c), math.floor(total)))
    return [total / n] * n


# ---------------------------------------------------------------------------------------------- where a Prime stream is
def epoch_s(position_s: float | None) -> float | None:
    """The frame's wall-clock stamp (s) when the media-session position is Prime's epoch, else None (a counter)."""
    from redzone.sync_edge import EPOCH_POSITION
    if position_s is None or position_s * 1000.0 < EPOCH_POSITION:
        return None
    return float(position_s)


# ---------------------------------------------------------------------------------------------- K from the bug
def k_sample(tracker, target: str, bug_clock_s: float, frame_epoch: float, plays: list[tuple[float, float]],
             sd: float, get: Callable[..., object] | None = None) -> float | None:
    """One K sample (content minus feed time) from a bug clock read on a frame whose epoch is `frame_epoch`, against
    the current period's plays [(feed wallclock, clock s remaining)], fused into `tracker` (sync_edge.EdgeTracker,
    which gates outliers). None when no running-clock pair of plays brackets the reading."""
    from redzone.decider.sync import clock_sample
    offset = _setting("pair_offset_s", get)
    t = float(frame_epoch) + offset
    d = clock_sample(float(bug_clock_s), t, plays)
    if d is None:
        return None
    tracker.epoch_at[target] = (t, offset)                   # the screen sits `offset` behind this "frame clock"
    return tracker.frame_k(target, d, t, sd, 1.0)


def game_k(tracker, target: str, get: Callable[..., object]) -> tuple[float, float, int] | None:
    """The Prime game's K once it passes the existing bars (sync.edge.k_min_reads samples, sd under k_sd_max_s):
    (K, its sd, reads). The show turns it into its timing model's GameK (park_run)."""
    got = tracker.k_of(target)
    if got is None:
        return None
    k, sd, n = got
    if n < int(get("sync.edge.k_min_reads", 5) or 5) or sd > float(get("sync.edge.k_sd_max_s", 0.5) or 0.5):
        return None
    return round(k, 3), sd, n


def parked_in_stop(bug_clock_s: float | None, stop_clock_s: float | None, get: Callable[..., object] | None = None) -> bool | None:
    """Is a parked Prime frame inside the stoppage the plan parked on? Its bug clock (stopped) equals the clock of
    that stoppage's whistle. None = cannot tell (no read)."""
    if bug_clock_s is None or stop_clock_s is None:
        return None
    return abs(float(bug_clock_s) - float(stop_clock_s)) <= _setting("stop_match_s", get)


# ---------------------------------------------------------------------------------------------- [PARK-NFL-LAG] football play clock
PLAY_CLOCK_FULL = 40     # the NFL play clock: shown at 40 from the snap through the play, counting down from the dead ball
_PC = re.compile(r"^:?(\d{1,2})$")
_ORD = re.compile(r"(?:\d(?:ST|ND|RD|TH)|&|AND)$")


def play_clock(observations, bug, lower_from: float = 2 / 3) -> int | None:
    """The play clock on a football score bug in one OCR read (normalized top-left boxes), or None (absent or ambiguous).

    A standalone number 0-40 (":07" or "7") in the bug's band that is not one of the two scores (the bug's two tallest
    numbers, `scorebug.parse_bug`), not part of the game clock ("12:34") or of down & distance ("3rd & 7"). Prime's bug
    layout on live TNF is UNKNOWN until live: the watcher read it on 76% of frames of a Thursday replay (Nov 11)."""
    rows = [o for o in observations if o.y >= lower_from and o.confidence >= 0.5]
    if bug is None or not rows:
        return None
    scores = {bug.home, bug.away}
    tall = max((o.height for o in rows), default=0.0)
    cands = []
    for o in rows:
        m = _PC.match(o.text.strip())
        if not m:
            continue
        v = int(m.group(1))
        if v > PLAY_CLOCK_FULL:
            continue
        if v in scores and o.height >= 0.75 * tall and not o.text.strip().startswith(":"):
            continue                                       # a score, drawn big
        if any(r is not o and abs(r.y - o.y) < 0.03 and -0.005 <= o.x - (r.x + r.width) < 0.04
               and ("&" in r.text or _ORD.search(r.text.upper())) for r in rows):
            continue                                       # the distance of "3rd & 7"
        cands.append(v)
    return cands[0] if len(cands) == 1 else None


def play_over(pc: int | None) -> bool | None:
    """Is the play over by the play clock? Below 40 it counts down from a dead ball (True); at 40 a play runs or has just
    been whistled (False); None = not read."""
    if pc is None:
        return None
    return pc < PLAY_CLOCK_FULL
