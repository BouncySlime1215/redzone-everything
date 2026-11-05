"""Entry autopsy (PROPOSAL, test writers; EXP-700 / EXP-727 / EXP-736). Not frozen. Diagnostic only.

Re-runs a league scorecard exactly as eval_sports.py / eval.py do and, for every ENTRY the frozen cut grader
(redzone/lab/cutgrade.py) scores, records why the switch happened (SwitchLog kind + reason), what the destination
was doing at the landing (stoppage kind, seconds to restart) and whether it counts as mid-play or mid-commercial.
It reads the grader's own dead_at, so its counts must equal the scorecard's (checked at the end).

    python scripts/entry_autopsy.py nba --split fit --real-lag [--out autopsy.json]
    python scripts/entry_autopsy.py nfl --split fit --real-lag

No decider or grader code changes: it wraps cutgrade.grade and calls the original.
"""
from __future__ import annotations

import collections
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redzone.lab import cutgrade  # noqa: E402
from redzone.lab.cutgrade import BREAK_TAIL_BAR  # noqa: E402
from redzone.models import Stoppage  # noqa: E402
from redzone.sources import espn  # noqa: E402

ROWS: list[dict] = []
TOTALS = collections.Counter()
_SEEN: set = set()
_orig = cutgrade.grade


def _stop_before(league, plays, i):
    """The break that opened this dead window, as the grader's dead_at finds it (walk back to the first REAL_BREAK
    stoppage before the last live action); otherwise the first stoppage seen (a dead ball that is not a break)."""
    from redzone.models import REAL_BREAKS
    k, first = i, None
    while k >= 0:
        s = espn.stoppage_for(league, plays[k])
        if s in REAL_BREAKS:
            return s.value
        if s != Stoppage.NONE and first is None:
            first = s.value
        if cutgrade._action(league, plays[k]):
            break
        k -= 1
    return first or ("between_plays" if k >= 0 else "pregame")


def _wrapped(replays, switches, lag_true, tune_s):
    card = _orig(replays, switches, lag_true, tune_s)
    key = (tuple((s.t, s.to, s.kind) for s in switches), float(lag_true), tune_s)
    if key in _SEEN:   # the same night graded twice (a second pass of the scorecard): count it once
        return card
    _SEEN.add(key)
    from redzone.lab.backtest import lag_of
    by_id = {r.gid: r for r in replays}
    for n, s in enumerate(switches):
        if s.kind == "replay":
            continue
        dst = by_id.get(s.to or "")
        if dst is None:
            continue
        arrive = s.t + tune_s + (getattr(s, "arrive_dt", 0.0) or 0.0)
        off = getattr(s, "offset", 0.0) or 0.0
        st = arrive - lag_of(lag_true, dst.gid) - off
        if st < dst.wall[0] or st > dst.wall[-1]:
            continue
        dead, brk, wait = cutgrade._dead_at(dst.league, dst.wall, dst.plays, st)
        i = cutgrade._idx(dst.wall, st)
        src = by_id.get(s.frm or "")
        src_state = None
        if src is not None:
            ss = s.t + (getattr(s, "leave_dt", 0.0) or 0.0) - lag_of(lag_true, src.gid)
            sd, sb, sw = cutgrade._dead_at(src.league, src.wall, src.plays, ss)
            src_state = "break" if sb else ("dead" if sd else "live")
        import datetime as _dt
        ROWS.append({"gid": dst.gid, "league": dst.league.value, "t": round(s.t, 1),
                     "night_et": _dt.datetime.fromtimestamp(st - 5 * 3600, _dt.timezone.utc).strftime("%Y%m%d"),
                     "jitter": round(getattr(s, "jitter", 0.0) or 0.0, 1), "flash": round(getattr(s, "flash", 0.0) or 0.0, 1),
                     "arrive_dt": round(getattr(s, "arrive_dt", 0.0) or 0.0, 1),
                     "landing_play": (dst.plays[i].get("text") or dst.plays[i].get("type", {}).get("text") or "")[:70] if i >= 0 else "",
                     "s_after_play": round(st - dst.wall[i], 1) if i >= 0 else None,
                     "kind": s.kind, "reason": (s.reason or "")[:80], "matchup": getattr(dst, "matchup", dst.gid),
                     "mid_play": not dead, "in_break": bool(brk), "mid_break": bool(brk and wait > BREAK_TAIL_BAR),
                     "wait_s": round(wait, 1), "stop": _stop_before(dst.league, dst.plays, i) if dead else "live",
                     "source": src_state, "offset": round(off, 1)})
    s = card.summary()
    TOTALS["graded_nights"] += 1
    for k in ("entries", "entries_mid_play", "entries_mid_break"):
        TOTALS[k] += s[k]
    return card


def _reason_key(r: str) -> str:
    return r.split("(")[0].split(":")[0].strip()[:40] or "-"


def report(rows: list[dict]) -> dict:
    mb = [r for r in rows if r["mid_break"]]
    mp = [r for r in rows if r["mid_play"]]
    c = lambda xs, f: collections.Counter(f(x) for x in xs).most_common()  # noqa: E731
    waits = sorted(r["wait_s"] for r in mb)
    return {"entries": len(rows), "mid_break": len(mb), "mid_play": len(mp),
            "mid_break_by_kind": c(mb, lambda r: r["kind"]), "mid_break_by_reason": c(mb, lambda r: _reason_key(r["reason"])),
            "mid_break_by_stoppage": c(mb, lambda r: r["stop"]), "mid_break_by_source": c(mb, lambda r: r["source"]),
            "mid_break_wait_s_q25_q50_q75": [waits[len(waits) // 4], waits[len(waits) // 2], waits[3 * len(waits) // 4]] if waits else [],
            "mid_play_by_kind": c(mp, lambda r: r["kind"]), "mid_play_by_reason": c(mp, lambda r: _reason_key(r["reason"])),
            "all_by_kind": c(rows, lambda r: r["kind"])}


def main(argv: list[str]) -> int:
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    league = argv[0]
    cutgrade.grade = _wrapped
    import io
    import contextlib
    target = ROOT / "scripts" / ("eval.py" if league == "nfl" else "eval_sports.py")
    args = (["--n", "999"] + argv[1:]) if league == "nfl" else ([league, "--nights", "999"] + argv[1:])
    sys.argv = [str(target)] + args
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            runpy.run_path(str(target), run_name="__main__")
        except SystemExit:
            pass
    rep = report(ROWS)
    rep["check"] = {"scorecard": dict(TOTALS), "autopsy": {"entries": rep["entries"], "entries_mid_play": rep["mid_play"],
                                                          "entries_mid_break": rep["mid_break"]}}
    print(json.dumps(rep, indent=1))
    if out:
        Path(out).write_text(json.dumps({"report": rep, "rows": ROWS}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
