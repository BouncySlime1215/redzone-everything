"""Phone commands that land exactly once: an id, an ack, a status, and safe retries.

The phone gives every tap an id (POST /api/action {"id": ...}). If the answer is lost (Wi-Fi blip,
the phone slept, the server restarted mid-reply) the phone sends the SAME id again, and this log
answers with the first result instead of running the tap twice. That matters more than it sounds:
a second "Watch this" is "Go now" (it cuts over at once, even mid-play), and a second Skip skips two
games. GET /api/action/{id} reads a tap's status, so the phone can confirm what happened.

Taps without an id (old phone pages, scripts) run every time, as before.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Callable

log = logging.getLogger(__name__)

KEEP = 512            # remembered taps (a night has a few hundred at most)
TTL_S = 15 * 60.0     # a retry this late is a new tap
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{6,64}$")


def valid_id(cid) -> str | None:
    """The tap's id if it is a usable one, else None (then it runs un-deduplicated)."""
    return cid if isinstance(cid, str) and ID_RE.match(cid) else None


class Commands:
    def __init__(self, keep: int = KEEP, ttl_s: float = TTL_S):
        self.keep, self.ttl_s = keep, ttl_s
        self._log: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.runs = 0          # taps actually executed
        self.duplicates = 0    # retries answered from the log

    def _expire(self, now: float) -> None:
        while self._log:
            k, v = next(iter(self._log.items()))
            if now - v["t"] <= self.ttl_s and len(self._log) <= self.keep:
                break
            self._log.pop(k)

    def run(self, cid: str | None, action: str, game_id, value,
            fn: Callable[[], tuple[bool, str]]) -> dict:
        """Run the tap once per id. Returns its record: id, action, game_id, ok, message, t,
        duplicate (True when this was a retry answered from the log)."""
        now = time.time()
        cid = valid_id(cid)
        with self._lock:
            self._expire(now)
            seen = self._log.get(cid) if cid else None
            if seen is not None and now - seen["t"] <= self.ttl_s:
                log.info("playbook=BTN-03 tap %s (%s) resent: answered from the log, not run again", cid, action)
                self.duplicates += 1
                seen["retries"] = seen.get("retries", 0) + 1
                return {**seen, "duplicate": True}
            # Claimed before it runs, so a retry racing the first try waits on the lock, then sees it.
            try:
                ok, msg = fn()
            except Exception as e:   # a bad tap never crashes the server
                ok, msg = False, f"Something went wrong: {e}"
            self.runs += 1
            rec = {"id": cid, "action": action, "game_id": game_id, "value": value, "ok": bool(ok),
                   "message": msg, "t": now}
            if cid:
                self._log.pop(cid, None)   # an expired id reused: it's a new tap, newest last
                self._log[cid] = rec
            return {**rec, "duplicate": False}

    def status(self, cid: str) -> dict | None:
        with self._lock:
            self._expire(time.time())
            rec = self._log.get(cid)
            return dict(rec) if rec and time.time() - rec["t"] <= self.ttl_s else None
