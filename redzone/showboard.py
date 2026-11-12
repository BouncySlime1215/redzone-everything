"""The show's state for the phone's Show tab: `state.json` (rewritten whole, by rename, about once a second) and
`events.jsonl` (one line added per event), in the shape `redzone/web/show.py` reads (the show state contract).

`Board.state(show, t, now, mode)` builds the dict from the PARK controller (`redzone/noggin/park_show.Show`);
`write` puts it in the folder (`show.state_dir`, or `data/show`) and keeps the latest in memory (`Board.latest`) for
`Engine.snapshot()`. Numbers and short public names only (team short names, channel names): never an address, an
id, a token, an account name or a frame. A failed write never stops the show; the first one is logged once.

Why: the Show tab already reads this contract from the local show runner's folder; with park.enabled the app's own
show has to fill the same files (integration plan unit SHOW-STATE, moved from tools/park_live/showstate.py).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from redzone.noggin.park_core import delivered

log = logging.getLogger(__name__)
PACE_MINUTES = (1, 5, 10, 20, 30, 45, 60)


def write_whole(path: Path, obj) -> None:
    """Write a temp file, then rename: a reader never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


class Board:
    def __init__(self, folder: Path | str | None, every: float = 1.0, now=time.time):
        self.dir, self.every, self.last, self.errors = (Path(folder) if folder else None), every, 0.0, 0
        self.mode, self.now, self.started, self.planned, self.t0, self.last_error = "live", now, now(), None, None, ""
        self.latest: dict = {}
        self.events: list = []                               # the newest events, kept in memory too

    def start(self, show, mode: str, minutes: float) -> None:
        self.mode, self.started, self.planned, self.t0 = mode, self.now(), minutes, show.night.span[0]
        if self.dir is not None:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.errors += 1

    def event(self, kind: str, **kw) -> None:
        row = {"t": round(self.now(), 1), "kind": kind, **kw}
        if kind == "alert":
            self.last_error = str(kw.get("text", ""))
        self.events = (self.events + [row])[-200:]
        if self.dir is None:
            return
        try:
            with (self.dir / "events.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            self.errors += 1

    def write(self, show, t: int, mode: str | None = None, force: bool = False) -> None:
        now = self.now()
        if not force and now - self.last < self.every:
            return
        self.last = now
        try:
            self.latest = self.state(show, t, now, mode or self.mode)
            if self.dir is not None:
                write_whole(self.dir / "state.json", self.latest)
        except Exception:                                    # the Show tab's file must never stop the show
            self.errors += 1
            if self.errors == 1:
                log.exception("show state not written")

    def state(self, show, t: int, now: float, mode: str) -> dict:
        S, A, rig, n = show.S, show.A, show.rig, show.night.n
        t0 = self.t0 if self.t0 is not None else t

        def at(p) -> int:
            return min(n - 1, max(0, int(p)))

        def what(g: int, p: int) -> str:
            x = A[g]
            if show.raw[g] is None and show.feed.edge(g) < 10 ** 8:
                return "unknown"
            return "break" if x.brk[p] else "dead ball" if x.dead[p] else "live play"
        names, ports = getattr(rig, "names", ("stick 1", "stick 2")), getattr(rig, "ports", ("", ""))
        sp = at(t - S.sb + S.se)
        since = show.since if show.since is not None else t
        on = {"game": show.names[S.sg], "league": show.leagues[S.sg], "channel": show.chans[S.sg], "stick": names[show.on],
              "input": ports[show.on], **show.feed.line(S.sg, sp), "behind_live_s": round(S.sb + show.delay, 1),
              "since": round(now - (t - since), 1), "look_s": t - since, "state": what(S.sg, sp)}
        parked = None
        if S.hg >= 0:
            parked = {"game": show.names[S.hg], "league": show.leagues[S.hg], "channel": show.chans[S.hg], "stick": names[1 - show.on],
                      "ready": bool(S.h_paused and t >= S.h_ready and show.h_op is None),
                      "parked_for_s": (t - show.parked_at) if (S.h_paused and show.parked_at is not None) else None,
                      "behind_live_s": round(S.hb + show.delay, 1)}
        go = show.pl.go
        if show.stop:
            nxt = {"action": "stay", "to_game": None, "in_s": None, "reason": f"stopped: {show.stop}"}
        elif go is not None and S.sg == go[0]:
            x, i = A[S.sg], at(go[1])
            nxt = {"action": "jump", "to_game": show.names[go[3]], "in_s": max(0, go[6] - t),
                   "reason": ("timeout or break" if x.brk[i] else "dead ball" if x.dead[i] else "the moment after a shot") + " on the game we are watching"}
        elif show.h_op is not None:
            nxt = {"action": "park", "to_game": show.names[S.hg] if S.hg >= 0 else None, "in_s": None, "reason": "getting the other stick ready"}
        else:
            nxt = {"action": "stay" if show.stay[1] else "wait", "to_game": None, "in_s": None, "reason": show.stay[0] or "watching"}
        mins = max(1e-9, (t - t0) / 60.0)
        ja, st, j = show.jump_at, show.st, show.judge

        def last(w: int) -> int:
            return sum(1 for x in ja if x > t - 60 * w)
        chans = {}
        for g in range(show.N):
            tg = show.tm.g[g]
            chans[show.chans[g] or f"game {g + 1}"] = {"k_s": tg.k.k if tg.k is not None else None, "k_spread_s": tg.k.spread if tg.k is not None else None,
                                                    "game": show.names[g], "padding_s": show.pad[g], "tune_offset_known": bool(show.read_now[g] and show.tm.reads(g))}
        games = []
        seen = getattr(show, "seen_state", None)                # [SEEN] park.seen: None when off (no fields added)
        for g in range(show.N):
            e = at(min(t, show.feed.edge(g)) if show.feed.edge(g) < 10 ** 8 else t)
            games.append({"game": show.names[g], "league": show.leagues[g], "channel": show.chans[g], **show.feed.line(g, at(t + show.know)),
                          "state": what(g, e), "value": round(float(A[g].lev[e]), 2), "on_screen": g == S.sg, "parked": g == S.hg,
                          **(seen.view(g, show.names[g]) if seen is not None else {})})
        sound = rig.sound() if hasattr(rig, "sound") else {}
        if j is not None:
            rule1 = {"mid_play_joins": j.mid_entry, "mid_play_leaves": j.mid_leave, "high_leverage_leaves": j.hl_leave, "judged": "replay"}
        elif sum(show.eyes_says.values()):
            rule1 = {"mid_play_joins": show.eyes_says["joins_bad"], "mid_play_leaves": show.eyes_says["leaves_bad"],
                     "high_leverage_leaves": None, "judged": "eyes", "seen": dict(show.eyes_says)}
        else:
            rule1 = {"mid_play_joins": None, "mid_play_leaves": None, "high_leverage_leaves": None, "judged": "unknown live"}
        return {
            "updated": round(now, 1), "mode": mode,
            "session": {"started": round(self.started, 1), "minutes": round(mins, 1), "planned_minutes": self.planned},
            "sound": sound, "on_screen": on, "parked": parked, "next": nxt,
            "pace": {"jumps": len(ja), "per_min_now": round(last(5) / min(5.0, mins), 2), "per_min_session": round(len(ja) / mins, 2),
                     "windows": {str(w): (last(w) if mins >= w else None) for w in PACE_MINUTES}},
            "planned": {"scheduled": len(st.sched), "delivered": delivered(st)},
            "rule1": rule1,
            "timing": {"padding_s": show.pad[S.sg], "show_delay_s": show.delay, "channels": chans},
            "stay_reasons": dict(show.stayed),
            "health": {"tv": "ok" if sound.get("ok", True) else "not silent", "feed": show.feed.health(t),
                       "sticks": {names[k]: ("ok" if rig.game[k] is not None else "not on a game") for k in (0, 1)},
                       "eyes": show.eyes.health() if (show.eyes is not None and hasattr(show.eyes, "health")) else "off",
                       "last_error": self.last_error},
            "games": games}
