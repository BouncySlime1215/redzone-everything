"""Leakage checks for evaluation (research 05 §3, `docs/research/2026-11-03/05-eval-and-learning-loop.md`).

    python scripts/leak_check.py            # exit 1 on any FAIL; PENDING lines never fail the build
    python scripts/leak_check.py --json

Each check prints PASS, FAIL or PENDING (cannot be checked yet, e.g. no stamp or no pinned data).
Covered now (report 05 list numbers):
  1/2  splits: no night or game in two splits, time order kept (data/archive, eval-data-v1)
  2    the e2e night (redzone/lab/night/run.py GAMES) replays no locked-set game
  3/4  a replayed moment carries no end-of-game fields (leader board, final team records)
  5    model files stamped `trained_through` before their league's test start (PENDING when unstamped)
  10   KNOWN_ERRORS allowlist entries in the night are not past their expiry date
Still to add: artifact-level (league, gid) disjointness (1, over files), head cut nights (6), grounded
teacher answers (7), identity features (8), the auto-tune window (9).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHIVE = ROOT / "data" / "archive"
LEAGUES = ("nfl", "nba", "nhl")


MIN_LOCKED = 8   # the release gate's minimum locked nights per league (cycle 01 plan, EVAL2)


def _split(league: str):
    from redzone.lab import evaldata, splits
    # Only nights inside the pin (window end), the same nights the scorecards grade: nights the live
    # recorder adds later are fresh, never part of the locked split.
    an = splits.archive_nights(ARCHIVE / league, evaldata.window_end(league, None))
    return an, splits.league_split(league, an)


def check_locked_nights() -> list[tuple[str, str, str]]:
    """PASS when a league's locked set has >= MIN_LOCKED nights, else PENDING (thin data can't reach
    PASS on the locked lines; it is not a leak, so it never fails the build). eval-data-v2 fixes v1's 4."""
    out = []
    for lg in LEAGUES:
        if not (ARCHIVE / lg).exists():
            continue
        _, s = _split(lg)
        n = len(s.test)
        out.append(("PASS" if n >= MIN_LOCKED else "PENDING", f"locked_nights_{lg}",
                    f"{n} locked nights (from {s.test[0] if s.test else '-'}), need >= {MIN_LOCKED}"))
    return out


def check_splits() -> list[tuple[str, str, str]]:
    from redzone.lab import splits
    out = []
    for lg in LEAGUES:
        if not (ARCHIVE / lg).exists():
            out.append(("PENDING", f"splits_{lg}", "no data/archive (python scripts/fetch_eval_data.py)"))
            continue
        an, s = _split(lg)
        bad = splits.leaks(s, an)
        out.append(("FAIL" if bad else "PASS", f"splits_{lg}",
                    "; ".join(bad) if bad else f"{len(s.train)}/{len(s.valid)}/{len(s.test)} nights train/valid/test"))
    return out


def locked_games() -> dict[str, set[str]]:
    """league -> game ids on LOCKED (test) nights of the pinned archive."""
    out = {}
    for lg in LEAGUES:
        if (ARCHIVE / lg).exists():
            an, s = _split(lg)
            out[lg] = {g for n in s.test for g in an.get(n, ())}
    return out


def check_locked_unseen_by_previous_pin() -> list[tuple[str, str, str]]:
    """When grading on another pin (RZ_EVAL_LOCK), its LOCKED nights must not be nights the default pin
    (eval-data.lock.json) let anyone tune on (that pin's train/valid). Moving the window forward makes
    the newest 25 % slide back over old dev nights, and everything tuned on them would score high there."""
    from redzone.lab import evaldata, splits
    default = ROOT / "eval-data.lock.json"
    if evaldata.LOCK == default or not default.exists():
        return []
    prev = evaldata.load_lock(default)
    out = []
    for lg in LEAGUES:
        w = prev["window"].get(lg)
        if not w or not (ARCHIVE / lg).exists():
            continue
        an, s = _split(lg)
        old = splits.split_nights([n for n in an if w["start"] <= n <= w["end"]],
                                  dev_before=(prev.get("dev_before") or {}).get(lg))
        seen = sorted(set(s.test) & set(old.train + old.valid))
        out.append(("FAIL" if seen else "PASS", f"locked_unseen_{lg}",
                    f"{len(seen)} locked nights were {prev['tag']} dev nights ({', '.join(seen[:5])})" if seen else
                    f"no locked night was a {prev['tag']} dev night"))
    return out


def check_night_fixtures(locked: dict[str, set[str]] | None = None) -> list[tuple[str, str, str]]:
    from redzone.lab.night.run import GAMES
    locked = locked_games() if locked is None else locked
    if not locked:
        return [("PENDING", "night_fixtures_unlocked", "no data/archive to read the locked set from")]
    hits = [f"{g.league} {g.fixture.split('_')[-1]}" for g in GAMES
            if g.fixture.split("_")[-1] in locked.get(g.league.lower(), set())]
    return [("FAIL" if hits else "PASS", "night_fixtures_unlocked",
             f"the e2e night replays locked games: {', '.join(hits)}" if hits else
             f"{len(GAMES)} night games, none locked")]


def check_replay_fields() -> list[tuple[str, str, str]]:
    """A replayed moment (Replay.state_at) must not carry the final leader board or final records."""
    from redzone.lab.backtest import Replay
    from redzone.models import League
    out = []
    for f in sorted((ROOT / "tests" / "fixtures").glob("espn_*_summary_*.json")):
        lg = f.name.split("_")[1]
        try:
            league = League(lg)
        except ValueError:
            continue
        r = Replay.load(league, f)
        if r is None or not r.plays:
            continue
        g = r.state_at(min(10, len(r.plays) - 1))
        if g is None:
            continue
        bad = []
        if g.leaders:
            bad.append("leaders")
        if any(t.wins is not None or t.losses is not None for t in (g.home, g.away)):
            bad.append("team records")
        out.append(("FAIL" if bad else "PASS", f"replay_fields_{f.stem}",
                    f"end-of-game fields at play 10: {', '.join(bad)}" if bad else "no end-of-game fields"))
    return out or [("PENDING", "replay_fields", "no summary fixtures")]


def check_model_stamps() -> list[tuple[str, str, str]]:
    """Every model file the eval path loads says what it trained through, before its league's test start."""
    import numpy as np
    out = []
    for p in sorted((ROOT / "redzone").glob("**/models/*.npz")) + sorted((ROOT / "redzone").glob("**/brain_models/*.npz")):
        lg = p.stem.split("_")[0].lower()
        try:
            with np.load(p, allow_pickle=False) as z:
                raw = z["meta"] if "meta" in z.files else None
                meta = {} if raw is None else json.loads(raw.tobytes().decode() if raw.dtype == np.uint8 else str(raw))
        except Exception as e:   # unreadable or pickled meta
            out.append(("PENDING", f"stamp_{p.relative_to(ROOT)}", f"meta unreadable: {type(e).__name__}"))
            continue
        tt = str(meta.get("trained_through") or "")
        if not tt:
            out.append(("PENDING", f"stamp_{p.relative_to(ROOT)}", "no trained_through stamp"))
            continue
        if lg in LEAGUES and (ARCHIVE / lg).exists():
            _, s = _split(lg)
            start = min(s.test) if s.test else ""
            ok = not start or tt < start
            out.append(("PASS" if ok else "FAIL", f"stamp_{p.relative_to(ROOT)}",
                        f"trained_through {tt}, test starts {start}"))
        else:
            out.append(("PENDING", f"stamp_{p.relative_to(ROOT)}", f"trained_through {tt}; no archive for {lg}"))
    return out


def check_known_errors(today: date | None = None) -> list[tuple[str, str, str]]:
    from redzone.lab.night.run import KNOWN_ERRORS
    today = today or date.today()
    out = []
    for e in KNOWN_ERRORS:
        if len(e) < 3:
            out.append(("FAIL", f"known_error {e[0]!r}", "no expiry date: every allowlisted bug needs one"))
            continue
        exp = date.fromisoformat(e[2])
        out.append(("FAIL" if exp < today else "PASS", f"known_error {e[0]!r}",
                    f"expires {exp} (owner {e[3] if len(e) > 3 else '?'})"))
    return out or [("PASS", "known_errors", "allowlist empty")]


def run_all() -> list[tuple[str, str, str]]:
    rows = []
    for fn in (check_splits, check_locked_nights, check_locked_unseen_by_previous_pin, check_night_fixtures, check_replay_fields, check_model_stamps, check_known_errors):
        try:
            rows += fn()
        except Exception as e:
            rows.append(("FAIL", fn.__name__, f"check crashed: {type(e).__name__}: {e}"))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="leak_check")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = run_all()
    if a.json:
        print(json.dumps([{"status": s, "check": c, "detail": d} for s, c, d in rows], indent=1))
    else:
        for s, c, d in rows:
            print(f"{s:8} {c}: {d}")
        n = {k: sum(1 for r in rows if r[0] == k) for k in ("PASS", "FAIL", "PENDING")}
        print(f"\n{n['PASS']} pass, {n['FAIL']} fail, {n['PENDING']} pending")
    return 1 if any(r[0] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
