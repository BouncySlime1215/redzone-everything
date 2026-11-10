"""[EXP-728 / EXP-736] Land late in a break, not early: the restart a break is predicted to end at.

Why: [EXP-728/736] #277's autopsy found entries landing in commercials with over 10 s left, so entries are held later in the break (decisions 2026-11-05, off).

Test writers' autopsy (Nov 5, #277): NBA dev 33 of 64 entries and NFL 196 of 531 land in a real commercial with
more than 10 s still to air (cutgrade BREAK_TAIL_BAR). The cure they pre-registered: when the destination is in a
break, hold the switch until its picture would come up at most `decider.entry.break_tail_s` before the predicted
restart. The prediction errs EARLY: a low quantile (q25) of the measured stoppage -> real restart lengths,
per league x kind x network (redzone/scoring/broadcast/break_lengths.json, EXP-1500, train nights only), the
league x kind row when the network has too few rows. Pure lookups; the decider owns the timing.
"""
from __future__ import annotations

import json
from functools import lru_cache

from redzone.scoring.dead_left import LENGTHS_FILE

# DeadTracker window kinds (redzone/decider/cuts.after_kind) -> the table's stoppage kinds
WINDOW_KIND = {"timeout": "timeout", "timeout_long": "timeout", "media_timeout": "timeout", "tv_timeout": "timeout", "team_timeout": "timeout",
        "official_timeout": "timeout", "end_period": "end_period", "halftime": "halftime", "review": "review",
        "two_minute": "two_minute_warning", "score": "score_break", "goal": "score_break", "injury": "injury",
        "intermission": "intermission"}


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(LENGTHS_FILE.read_text()).get("table") or {}
    except (OSError, ValueError):
        return {}


def network_of(target: str | None) -> str:
    """'youtubetv:espn' -> 'ESPN', the table's network spelling (upper case, no spaces)."""
    if not target:
        return ""
    return target.split(":", 1)[-1].upper().replace(" ", "").replace("_", "")


def length(league: str, kind: str, target: str | None, q: str) -> float | None:
    """The q quantile (e.g. 'q25') of this break kind's stoppage -> restart length (s), None if unmeasured."""
    k = _table().get(league, {}).get(kind) or {}
    cell = k.get(network_of(target)) or k.get("*")
    v = cell.get(q) if cell else None
    return float(v) if v is not None else None
