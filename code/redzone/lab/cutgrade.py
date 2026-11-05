"""Cut-timing scorecard: WHERE in each game we left, and WHERE we landed.

The mid-play-cut check grades leaving. This grades both ends of every switch against the TRUE
archive (real wallclocks, the stream's real delay), per league:

  entries_mid_play     the picture came up while a play was in progress (must be 0)
                       NFL/MLB: inside a play's [snap, whistle] on screen
                       NBA/NHL: the ball/puck was live (not a dead ball on screen)
  entry_wait_s         seconds of dead time watched after landing, until play resumed on screen
                       (huddle/line-up in football, the rest of a timeout, the faceoff wait)
  entries_in_break     landed inside a commercial break (timeout, score, period end...)
  plays_missed_entry   plays that began airing on the destination during the tune (black screen)
                       or were already under way when the picture came up
  leaves_in_dead_pct   share of leaves made inside a dead window: an official stoppage or a dead
                       ball (free throws, a foul, a review, a timeout), not just "between plays"

Resume times are the real restart, not the next logged event: NFL/MLB the next play's start,
NBA/CBB worked back from the first event that moved the game clock (wall - clock that ran),
NHL the faceoff (or the next event less the resume lead).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from redzone.decider.screen import CONTINUOUS, _is_marker
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.sources import espn

PLAY_LEN = {League.NFL: 7.0, League.MLB: 6.0, League.NBA: 0.0, League.NHL: 0.0}
RESUME_LEAD = {League.NBA: 26.0, League.NHL: 5.0}
REDZONE = "redzone"


@dataclass
class CutCard:
    entries: int = 0
    entries_mid_play: int = 0
    entries_in_break: int = 0
    entry_wait_s: float = 0.0
    plays_missed_entry: int = 0
    leaves: int = 0
    leaves_in_dead: int = 0
    leaves_between_plays: int = 0
    rewinds: int = 0            # entries that came up behind live on purpose (DVR rewind to a dead ball)
    rewind_s: float = 0.0
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

    def landed(self, which: str, dead: bool, left_s: float) -> None:
        h = self.leave_hist if which == "leave" else self.entry_hist
        b = hist_bucket(dead, left_s)
        h[b] = h.get(b, 0) + 1

    def landings(self) -> tuple[int, int]:
        """(landings in on-screen dead time, all landings): both ends of every switch (North Star)."""
        good = self.leaves_in_dead + self.leaves_between_plays + self.entries - self.entries_mid_play
        return good, self.leaves + self.entries

    def summary(self) -> dict:
        pct = lambda a, b: round(100 * a / max(1, b), 1)  # noqa: E731
        return {"entries": self.entries, "entries_mid_play": self.entries_mid_play,
                "entries_in_break": self.entries_in_break,
                "entry_wait_s": round(self.entry_wait_s / max(1, self.entries), 1),
                "plays_missed_entry": self.plays_missed_entry,
                "plays_missed_per_entry": round(self.plays_missed_entry / max(1, self.entries), 2),
                "leaves": self.leaves, "leaves_in_dead_pct": pct(self.leaves_in_dead, self.leaves),
                "leaves_between_plays": self.leaves_between_plays,
                "entry_rewinds": self.rewinds,
                "entry_rewind_s": round(self.rewind_s / max(1, self.rewinds), 1),
                # [landing offsets] the gate line: >= 95 % of landings (both ends) in on-screen dead time
                "landings_in_dead_pct": pct(self.leaves_in_dead + self.leaves_between_plays
                                            + self.entries - self.entries_mid_play, self.leaves + self.entries),
                "leave_to_restart_hist": _ordered(self.leave_hist),
                "entry_to_restart_hist": _ordered(self.entry_hist)}


HIST_EDGES = (3, 10, 30, 60, 120)   # seconds of dead time left on screen until the restart


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


def _base(league: League) -> League:
    return League.NBA if league.base == League.NBA else league


def _plen(league: League) -> float:
    return PLAY_LEN.get(_base(league), 0.0)


def _type(p: dict) -> str:
    return (espn._get(p, "type", "text") or "").lower()


def _clock(league: League, p: dict) -> float | None:
    c = espn._clock_s(espn._get(p, "clock", "displayValue"))
    if c is not None and league == League.NHL:
        per = espn._int(espn._get(p, "period", "number")) or 1
        c = max(0.0, (300 if per >= 4 else 1200) - c)
    return c


def _action(league: League, p: dict) -> bool:
    """A play that is live action (not bookkeeping or a dead-ball entry)."""
    if league in CONTINUOUS or league.base == League.NBA:
        return espn.stoppage_for(league, p) == Stoppage.NONE and not _is_marker(espn._kind(league, p), league)
    from redzone.lab.sportsgrade import _marker
    return not (_marker(_type(p)) and not p.get("scoringPlay"))


def _idx(wall: list[float], t: float) -> int:
    lo, hi = 0, len(wall)
    while lo < hi:
        mid = (lo + hi) // 2
        if wall[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def resume_after(league: League, wall: list[float], plays: list[dict], i: int) -> float:
    """Real time play restarts after the dead ball that play i left (inf = never in this game)."""
    if league not in CONTINUOUS and league.base != League.NBA:
        j = next((j for j in range(i + 1, len(plays)) if _action(league, plays[j])), None)
        return wall[j] - _plen(league) if j is not None else float("inf")
    j = next((j for j in range(i + 1, len(plays)) if _action(league, plays[j])), None)
    if j is None:
        return float("inf")
    if league == League.NHL:
        fo = next((k for k in range(i + 1, j + 1) if _type(plays[k]) == "face off"), None)
        return wall[fo] if fo is not None else wall[j] - RESUME_LEAD[League.NHL]
    # Basketball: the inbound isn't logged. The clock that ran before the first clock-moving event.
    per_i = espn._int(espn._get(plays[i], "period", "number"))
    c0 = _clock(league, plays[i])
    for k in range(i + 1, min(len(plays), j + 40)):
        c = _clock(league, plays[k])
        if espn._int(espn._get(plays[k], "period", "number")) != per_i:
            break
        if c is not None and c0 is not None and c < c0 - 0.05:
            return max(wall[i], wall[k] - (c0 - c))
    return max(wall[i], wall[j] - RESUME_LEAD[League.NBA])


def _dead_at(league: League, wall: list[float], plays: list[dict], st: float) -> tuple[bool, bool, float]:
    """(dead ball on screen, inside a commercial break, seconds until play resumes) at screen time st."""
    i = _idx(wall, st)
    if i < 0 or st > wall[-1]:
        return True, False, 0.0
    if league not in CONTINUOUS and league.base != League.NBA:
        plen = _plen(league)
        for w, p in zip(wall[max(0, i - 3):i + 4], plays[max(0, i - 3):i + 4]):
            if _action(league, p) and w - plen <= st <= w:
                return False, False, 0.0
        res = resume_after(league, wall, plays, i)
        brk = espn.stoppage_for(league, plays[i]) in REAL_BREAKS
        return True, brk, max(0.0, min(res, 900.0 + st) - st)
    last = plays[i]
    stop = espn.stoppage_for(league, last)
    res = resume_after(league, wall, plays, i)
    dead = stop != Stoppage.NONE or (bool(last.get("scoringPlay")) and st - wall[i] <= 4.0)
    if dead and st < res:
        # The commercial is whatever started this dead ball (a timeout followed by subs is a break).
        k, brk = i, False
        while k >= 0 and not brk:
            brk = espn.stoppage_for(league, plays[k]) in REAL_BREAKS
            if _action(league, plays[k]):
                break
            k -= 1
        return True, brk, max(0.0, min(res, 900.0 + st) - st)
    return False, False, 0.0


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
            st = s.t - lag_of(lag_true, src.gid) - left_off[n]
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
        arrive = s.t + tune_s
        off = getattr(s, "offset", 0.0) or 0.0
        lag = lag_of(lag_true, dst.gid)
        st = arrive - lag - off
        card.rewinds += off > 0
        card.rewind_s += off
        if st < dst.wall[0] or st > dst.wall[-1]:
            continue   # before the game started or after it ended on screen
        nxt = switches[n + 1].t if n + 1 < len(switches) else float("inf")
        card.entries += 1
        dead, brk, wait = _dead_at(dst.league, dst.wall, dst.plays, st)
        card.landed("entry", dead, wait)
        if not dead:
            card.entries_mid_play += 1
            card.mid_entries.append((s.t, s.kind, f"{dst.matchup}"))
        card.entries_in_break += brk
        card.entry_wait_s += min(wait, max(0.0, nxt - arrive))
        # plays that started airing while the screen was black, or were under way when it came up
        plen = _plen(dst.league)
        lo, hi = s.t - lag - off, st
        for k in range(max(0, _idx(dst.wall, lo)), len(dst.wall)):
            w, p = dst.wall[k], dst.plays[k]
            if w - plen > hi:
                break
            if w >= lo and _action(dst.league, p) and w - plen < hi:
                card.plays_missed_entry += 1
    return card
