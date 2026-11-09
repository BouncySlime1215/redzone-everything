"""Cut-timing scorecard: WHERE in each game we left, and WHERE we landed.

The mid-play-cut check grades leaving. This grades both ends of every switch against the TRUE
archive (real wallclocks, the stream's real delay), per league:

  entries_mid_play     the picture came up while a play was in progress (must be 0)
                       NFL/MLB: inside a play's [snap, whistle] on screen
                       NBA/NHL: the ball/puck was live (not a dead ball on screen)
                       NBA/CBB entries only: clean from a made basket until min(8 s NBA / 9 s CBB, the next shot)
                       (grader v49; Nick, Nov 9: "change it"; decisions.md). Leaves are unchanged.
  entries_mid_play_strict  the same count without that window (the scoring before v49), so old results stay readable
  entry_wait_s         seconds of dead time watched after landing, until play resumed on screen
                       (huddle/line-up in football, the rest of a timeout, the faceoff wait)
  entries_in_break     landed inside a commercial break (timeout, score, period end...)
  entries_mid_break    landed inside a break with more than BREAK_TAIL_BAR of dead time left before
                       the restart: mid-commercial (Nick, Nov 4 9:45 PM: never join at a break; land
                       just before the restart). Must be 0.
  plays_missed_entry   plays that began airing on the destination during the tune (black screen)
                       or were already under way when the picture came up
  possession_start_entry_pct  entries that came up on a dead ball with no play missed (the viewer
                       sees the whole possession from its start)
  leaves_in_dead_pct   share of leaves made inside a dead window: an official stoppage or a dead
                       ball (free throws, a foul, a review, a timeout), not just "between plays"
  landed_dead_pct      share of switches whose picture came up on a dead ball (100 - mid-play
                       entries): the "cuts not random" line, driven toward 100 by scripts/tune.py

Resume times are the real restart, not the next logged event: NFL/MLB the next play's start,
NBA/CBB worked back from the first event that moved the game clock (wall - clock that ran),
NHL the faceoff (or the next event less the resume lead).

Why: a switch is only good if it lands where Nick can see the play start; this is the number that says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from redzone.decider.screen import CONTINUOUS, _is_marker
from redzone.grading import PLAY_LEN, RESUME_LEAD, resume_after  # noqa: F401  [one brain] moved, step 3
from redzone.grading import _base, dead_at as _dead_at, idx as _idx, is_action as _action  # noqa: F401
from redzone.grading import play_clock as _clock, plen as _plen, raw_type as _type  # noqa: F401
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.sources import espn

REDZONE = "redzone"


@dataclass
class CutCard:
    entries: int = 0
    entries_mid_play: int = 0
    entries_in_break: int = 0
    entries_mid_break: int = 0
    entry_wait_s: float = 0.0
    plays_missed_entry: int = 0
    leaves: int = 0
    leaves_in_dead: int = 0
    leaves_between_plays: int = 0
    possession_starts: int = 0  # entries on a dead ball with no play missed: the whole possession seen
    rewinds: int = 0            # entries that came up behind live on purpose (DVR rewind to a dead ball)
    rewind_s: float = 0.0
    flash_mid_play: int = 0     # [seek model] on-screen rewinds whose LIVE edge showed play before the seek took hold
    entries_after_bucket: int = 0   # [grader v49] live-ball entries the made-basket window made clean; the old rule's extra mid-play entries
    mid_entries: list = field(default_factory=list)
    # [landing offsets] seconds of dead time left on screen when we cut away (leave) / when the new
    # picture came up (entry), until play restarted; mid-play landings are their own bucket
    leave_hist: dict = field(default_factory=dict)
    entry_hist: dict = field(default_factory=dict)

    def add(self, o: "CutCard") -> None:
        for k, v in vars(o).items():
            if isinstance(v, list):
                getattr(self, k).extend(v)
            elif isinstance(v, dict):
                mine = getattr(self, k)
                for b, n in v.items():
                    mine[b] = mine.get(b, 0) + n
            else:
                setattr(self, k, getattr(self, k) + v)

    @property
    def entries_mid_play_strict(self) -> int:
        """[grader v49] entries_mid_play as it was scored before the made-basket window."""
        return self.entries_mid_play + self.entries_after_bucket

    def landed(self, which: str, dead: bool, left_s: float) -> None:
        h = self.leave_hist if which == "leave" else self.entry_hist
        b = hist_bucket(dead, left_s)
        h[b] = h.get(b, 0) + 1

    def landings(self) -> tuple[int, int]:
        """(landings in on-screen dead time, all landings): both ends of every switch (North Star)."""
        good = self.leaves_in_dead + self.leaves_between_plays + self.entries - self.entries_mid_play_strict
        return good, self.leaves + self.entries

    def summary(self) -> dict:
        pct = lambda a, b: round(100 * a / max(1, b), 1)  # noqa: E731
        return {"entries": self.entries, "entries_mid_play": self.entries_mid_play,
                "entries_mid_play_strict": self.entries_mid_play_strict,
                "landed_dead_pct": pct(self.entries - self.entries_mid_play_strict, self.entries) if self.entries else 100.0,
                "entries_in_break": self.entries_in_break,
                "entries_mid_break": self.entries_mid_break,
                "entry_wait_s": round(self.entry_wait_s / max(1, self.entries), 1),
                "plays_missed_entry": self.plays_missed_entry,
                "plays_missed_per_entry": round(self.plays_missed_entry / max(1, self.entries), 2),
                "leaves": self.leaves, "leaves_in_dead_pct": pct(self.leaves_in_dead, self.leaves),
                "leaves_between_plays": self.leaves_between_plays,
                "possession_start_entry_pct": pct(self.possession_starts, self.entries),
                "entry_rewinds": self.rewinds,
                "entry_rewind_s": round(self.rewind_s / max(1, self.rewinds), 1),
                # [landing offsets] the gate line: >= 95 % of landings (both ends) in on-screen dead time
                "landings_in_dead_pct": pct(self.leaves_in_dead + self.leaves_between_plays
                                            + self.entries - self.entries_mid_play_strict, self.leaves + self.entries),
                "leave_to_restart_hist": _ordered(self.leave_hist),
                "entry_to_restart_hist": _ordered(self.entry_hist)}


# [RC7 gate 3] a break landing with at most this much dead time left is "just before the restart"
# (the network is back from commercial: the crowd shot, the line-up, the faceoff wait). More is mid-commercial.
# Seconds. A frozen grader bar, not app timing, so it stays out of config.py (lanes tune config, not graders).
BREAK_TAIL_BAR = 10.0
HIST_EDGES = (3, 10, 30, 60, 120)   # seconds of dead time left on screen until the restart
# [grader v49] Nick, Nov 9, "change it": a basketball entry is clean this long after a made basket, or until the next shot if
# that is sooner. Fit-night 5th percentile, make to the next shot (scripts/bucket_join_measure.py). Entries only. Seconds.
# A frozen grader bar like BREAK_TAIL_BAR, not app timing, so it stays out of config.py.
# Why: he wants to join right after a bucket in transition, and the old rule called those joins mid-play.
BUCKET_ENTRY_BAR = {League.NBA: 8.0, League.CBB: 9.0}


def hist_bucket(dead: bool, left_s: float) -> str:
    """'mid-play', or the dead time left until play restarts: '0-3', '3-10', ... '120+'."""
    if not dead:
        return "mid-play"
    lo = 0
    for e in HIST_EDGES:
        if left_s < e:
            return f"{lo}-{e}"
        lo = e
    return f"{HIST_EDGES[-1]}+"


def _ordered(h: dict) -> dict:
    keys = ["mid-play"] + [hist_bucket(True, x) for x in (0, *HIST_EDGES)]
    return {k: h[k] for k in keys if h.get(k)}


def after_bucket(league: League, wall: list[float], plays: list[dict], st: float) -> bool:
    """Is screen time st inside the entry window after a made basket? The last shot on screen must be a made field goal,
    at most BUCKET_ENTRY_BAR old. A later shot of any kind, or a miss and its rebound, closes it; so does a free throw."""
    limit = BUCKET_ENTRY_BAR.get(league)
    if limit is None:
        return False
    for k in range(_idx(wall, st), -1, -1):
        if st - wall[k] > limit:
            return False
        if plays[k].get("shootingPlay"):
            # no space: college feeds write "MadeFreeThrow", the NBA feed "Free Throw - 1 of 1"
            return bool(plays[k].get("scoringPlay")) and "freethrow" not in _type(plays[k]).replace(" ", "")
    return False


def grade(replays, switches, lag_true: float, tune_s: float) -> CutCard:
    """Both ends of every live switch. `switches` are backtest SwitchLogs, in time order."""
    card = CutCard()
    from redzone.lab.backtest import lag_of, leave_offsets
    by_id = {r.gid: r for r in replays}
    prev_kind = None
    left_off = leave_offsets(switches)
    for n, s in enumerate(switches):
        was_replay, prev_kind = prev_kind == "replay", s.kind
        # ---- leaving
        src = by_id.get(s.frm or "")
        if src is not None and not was_replay:
            card.leaves += 1
            st = s.t + (getattr(s, "leave_dt", 0.0) or 0.0) - lag_of(lag_true, src.gid) - left_off[n]
            dead, _, left = _dead_at(src.league, src.wall, src.plays, st)
            card.landed("leave", dead, left)
            i = _idx(src.wall, st)
            stopped = 0 <= i and (espn.stoppage_for(src.league, src.plays[i]) != Stoppage.NONE or st > src.wall[-1])
            if dead and stopped:
                card.leaves_in_dead += 1
            elif dead:
                card.leaves_between_plays += 1
        # ---- entering
        if s.kind == "replay":
            continue
        dst = by_id.get(s.to or "")
        if dst is None:
            continue
        arrive = s.t + tune_s + (getattr(s, "arrive_dt", 0.0) or 0.0)
        off = getattr(s, "offset", 0.0) or 0.0
        lag = lag_of(lag_true, dst.gid)
        st = arrive - lag - off
        card.rewinds += off > 0
        card.rewind_s += off
        if st < dst.wall[0] or st > dst.wall[-1]:
            continue   # before the game started or after it ended on screen
        nxt = switches[n + 1].t if n + 1 < len(switches) else float("inf")
        card.entries += 1
        fl = getattr(s, "flash", 0.0) or 0.0
        if fl > 0:
            # [seek model] the live edge is on screen while the presses go out: play there is a glimpse mid-play
            live = arrive - lag - (getattr(s, "jitter", 0.0) or 0.0)
            card.flash_mid_play += not (_dead_at(dst.league, dst.wall, dst.plays, live)[0]
                                        and _dead_at(dst.league, dst.wall, dst.plays, live + fl)[0])
        dead, brk, wait = _dead_at(dst.league, dst.wall, dst.plays, st)
        card.landed("entry", dead, wait)
        if not dead and after_bucket(dst.league, dst.wall, dst.plays, st):
            card.entries_after_bucket += 1
        elif not dead:
            card.entries_mid_play += 1
            card.mid_entries.append((s.t, s.kind, f"{dst.matchup}"))
        card.entries_in_break += brk
        card.entries_mid_break += bool(brk and wait > BREAK_TAIL_BAR)
        card.entry_wait_s += min(wait, max(0.0, nxt - arrive))
        # plays that started airing while the screen was black, or were under way when it came up
        plen = _plen(dst.league)
        lo, hi = s.t + (getattr(s, "leave_dt", 0.0) or 0.0) - lag - off, st   # [seek model] the old game stayed on through a cue
        if s.kind == "resume":
            lo = hi   # [replay and resume] back on the frame we paused: what came before it was on screen
        missed0 = card.plays_missed_entry
        for k in range(max(0, _idx(dst.wall, lo)), len(dst.wall)):
            w, p = dst.wall[k], dst.plays[k]
            if w - plen > hi:
                break
            if w >= lo and _action(dst.league, p) and w - plen < hi:
                card.plays_missed_entry += 1
        card.possession_starts += dead and card.plays_missed_entry == missed0
    return card
