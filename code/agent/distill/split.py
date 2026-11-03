"""Split moments BY TIME, whole nights at a time, per league (Nick's no-overfitting rules 1, 2, 6).

    locked  the held-out nights of docs/architecture.md §7 (NFL last 2 slates up to 2026-11-01,
            NBA/NHL 2026-10-29..11-01, the Nov 1 demo day), widened to the newest nights until it
            holds at least LOCKED_SHARE of each league's nights. Never in teacher requests, gold
            labels or training. kaggle-inputs leaves these rows out entirely; they're scored only
            for the release PR.
    train   the oldest nights of the rest
    valid   the next block (threshold picks, early stopping)
    test    the newest block before the locked nights ("dev-test": per-round reporting)

A game never spans two splits (one night per game); ``check`` asserts that.
"""
from __future__ import annotations

import datetime as dt

LOCKED_SHARE = 0.25
VALID_SHARE, TEST_SHARE = 0.15, 0.15          # of the non-locked nights, newest-last
HELD_OUT_FROM = {"nba": "20261029", "nhl": "20261029"}
HELD_OUT_TO = "20261101"
NFL_SLATES = 2
DEMO_NIGHT = "20261101"


def _d(n: str) -> dt.date:
    return dt.date(int(n[:4]), int(n[4:6]), int(n[6:]))


def nfl_slate(night: str) -> str:
    """NFL slate key: the week Tue..Mon it falls in (Thu/Sun/Mon games of one week together)."""
    d = _d(night)
    return (d - dt.timedelta(days=(d.weekday() - 1) % 7)).strftime("%Y%m%d")


def plan(nights_by_league: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """{league: {night: split}}."""
    out: dict[str, dict[str, str]] = {}
    for lg, nights in nights_by_league.items():
        ns = sorted(set(n for n in nights if n <= HELD_OUT_TO) | set(nights))
        locked = {n for n in ns if n == DEMO_NIGHT}
        if lg == "nfl":
            slates = sorted({nfl_slate(n) for n in ns if n <= HELD_OUT_TO})[-NFL_SLATES:]
            locked |= {n for n in ns if nfl_slate(n) in slates}
        elif lg in HELD_OUT_FROM:
            locked |= {n for n in ns if HELD_OUT_FROM[lg] <= n <= HELD_OUT_TO}
        locked |= {n for n in ns if n > HELD_OUT_TO}          # anything newer is fresh test data
        need = -(-len(ns) * LOCKED_SHARE // 1)
        for n in reversed(ns):
            if len(locked) >= need:
                break
            locked.add(n)
        cut = min(locked) if locked else None
        rest = [n for n in ns if n not in locked and (cut is None or n < cut)]
        stray = [n for n in ns if n not in locked and n not in rest]
        locked |= set(stray)                                  # never let a locked gap leak into train
        k_test = max(1, round(len(rest) * TEST_SHARE)) if len(rest) >= 3 else 0
        k_valid = max(1, round(len(rest) * VALID_SHARE)) if len(rest) >= 3 else 0
        m = {}
        for i, n in enumerate(rest):
            m[n] = "test" if i >= len(rest) - k_test else "valid" if i >= len(rest) - k_test - k_valid else "train"
        m.update({n: "locked" for n in locked})
        out[lg] = m
    return out


def assign(rows: list[dict]) -> dict:
    """Set r['split'] by time in place; returns the plan summary."""
    nights: dict[str, list[str]] = {}
    for r in rows:
        nights.setdefault(r["league"], []).append(str(r["night"]))
    p = plan(nights)
    for r in rows:
        r["split"] = p[r["league"]][str(r["night"])]
    check(rows)
    summary = {}
    for lg, m in p.items():
        s = summary.setdefault(lg, {})
        for n, sp in sorted(m.items()):
            e = s.setdefault(sp, {"nights": 0, "first": n, "last": n, "rows": 0})
            e["nights"] += 1
            e["last"] = n
        for r in rows:
            if r["league"] == lg:
                s[r["split"]]["rows"] += 1
    return summary


def check(rows: list[dict]) -> None:
    """Leakage checks: no game in two splits; every train night older than every valid/test/locked night."""
    game: dict[str, str] = {}
    for r in rows:
        g = ":".join(r["id"].split(":")[:2])
        assert game.setdefault(g, r["split"]) == r["split"], f"game {g} spans splits"
    order = {"train": 0, "valid": 1, "test": 2, "locked": 3}
    for lg in {r["league"] for r in rows}:
        lo: dict[str, tuple[str, str]] = {}
        for r in rows:
            if r["league"] == lg:
                n = str(r["night"])
                a, b = lo.get(r["split"], (n, n))
                lo[r["split"]] = (min(a, n), max(b, n))
        sp = sorted(lo, key=order.get)
        for x, y in zip(sp, sp[1:]):
            assert lo[x][1] < lo[y][0], f"{lg}: {x} ends {lo[x][1]} after {y} starts {lo[y][0]}"
