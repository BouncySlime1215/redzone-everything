"""Measure the one-brain break-length table (contracts.breaks.BreakTable) from the archive.

Serves: North Star "dead air" and "landings in dead time" (every break plan reads one length table);
owner: lane 1 (Noggin training), one-brain step 5 (docs/onebrain-migration.md). CLI:
scripts/measure_break_table.py.

Walks every archived game of the NON-LOCKED nights (redzone/lab/splits.py train + valid; the locked test
nights are never read) through the live parser (espn.parse_summary) and the live rules (breaks.BreakBook).
A break = the first play of a run whose stoppage is a REAL_BREAK. Its length = wallclock of the next play
that is neither a break nor a substitution (subs are logged inside the timeout) minus the break play's: feed
seconds, restart included. On NFL and NHL this reproduces breaks.LEN's medians exactly (score 167 / 160 is
the newer nights); NBA's timeouts run longer on the archive than the prior said (long 202 vs 188). Each break is filed under the forecast's LEN key (``rules``) and its Stoppage value
(``stoppage``). Quantiles 10/50/90 per key; a key with fewer than breaks.MIN_N breaks keeps the prior.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from redzone.lab import datasets, splits

LEAGUES = ("nfl", "nba", "nhl")       # what data/archive holds (eval-data-v1)
MAX_S = 1800.0                        # longer than this is a feed gap, not a break


def breaks_of(league: str, data: dict) -> list[tuple[str, str, float]]:
    """(LEN key, stoppage value, seconds) for every break of one archived game."""
    from redzone.models import REAL_BREAKS, League, Phase
    from redzone.scoring import breaks
    from redzone.sources import espn
    lg = League(league)
    plays = espn.flatten_plays(lg, data)
    book = breaks.BreakBook(clock=lambda: 0.0)
    rows, prev_break = [], False
    for i in range(len(plays)):
        g = espn.parse_summary(lg, data, upto=i, plays=plays)
        if g is None or g.phase != Phase.LIVE:
            rows.append(None)
            continue
        book.observe(g)
        w = espn._ts(plays[i].get("wallclock"))
        brk = g.stoppage in REAL_BREAKS
        key = ""
        if brk and not prev_break:
            f = breaks._now(g, len(book.clocks(g.game_id, g.period)))
            key = f.key if f is not None else ""
        sub = "substitution" in (g.last_play.kind if g.last_play else "")
        rows.append((w, g.stoppage, brk and not prev_break, key, sub))
        prev_break = brk
    out = []
    for i, r in enumerate(rows):
        if r is None or not r[2] or r[0] is None:
            continue
        nxt = next((x for x in rows[i + 1:] if x is not None and x[1] not in REAL_BREAKS and not x[4]
                    and x[0] is not None), None)
        if nxt is None:
            continue
        s = nxt[0] - r[0]
        if 0 < s <= MAX_S:
            out.append((r[3], r[1].value, float(s)))
    return out


def _q(xs: list[float]) -> dict:
    a = np.asarray(xs, float)
    return {"n": int(len(a)), "q10_s": round(float(np.percentile(a, 10)), 1),
            "q50_s": round(float(np.percentile(a, 50)), 1), "q90_s": round(float(np.percentile(a, 90)), 1)}


def measure(archive_root: Path | None = None, leagues=LEAGUES) -> dict:
    root = Path(archive_root or datasets.ROOT / "data" / "archive")
    rules, stop = defaultdict(list), defaultdict(lambda: defaultdict(list))
    used = {}
    for lg in leagues:
        d = root / lg
        if not d.exists():
            continue
        nights = splits.archive_nights(d)
        sp = splits.split_nights(nights)
        keep = sorted(sp.train + sp.valid)                  # never the locked test nights
        used[lg] = {"nights": len(keep), "first": keep[0] if keep else None, "last": keep[-1] if keep else None,
                    "locked_skipped": len(sp.test), "games": 0}
        for night in keep:
            for gid in nights[night]:
                p = d / f"summary_{gid}.json"
                if not p.exists():
                    continue
                used[lg]["games"] += 1
                for key, st, s in breaks_of(lg, json.loads(p.read_text())):
                    if key:
                        rules[key].append(s)
                    stop[lg][st].append(s)
    return {"version": "v1",
            "provenance": {"script": "scripts/measure_break_table.py", "source": "data/archive (eval-data-v1)",
                           "split": "redzone/lab/splits.py train + valid (locked test nights never read)",
                           "definition": "break play -> next live play, feed wallclock seconds, restart included",
                           "leagues": used},
            "rules": {k: _q(v) for k, v in sorted(rules.items())},
            "stoppage": {lg: {k: _q(v) for k, v in sorted(t.items())} for lg, t in sorted(stop.items())}}
