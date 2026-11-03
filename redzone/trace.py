"""One trace per switch: decision -> command -> picture confirmed, with the time each step took.

The controller stamps the steps it runs (`Controller._mark`) into a list that rides back on the
`Outcome`; the engine adds the decision's side (when it was decided, why, the delays it planned
with) and saves the result in the switch's row (`switches.detail.trace` in data/redzone.db), so
there is exactly one trace per switch and nothing new to rotate.

Every time is wallclock (time.time()), so a trace lines up with the feeds, the supervisor's log and
the nightly report. Spans are derived from the steps and are None when the step didn't run (a
preloaded switch has no tune; a single-stick switch has no flip).

    decided     the decider picked the target (Decision.at)
    submitted   the engine handed it to the controller
    started     the controller had the lock (queued behind a flip or another switch until then)
    tune_*      the stick that tunes: the hidden one (two sticks) or the visible one (one stick)
    safe_*      held for a safe moment to leave the game on screen
    flip_*      the TV input change (two sticks); the cut happens here
    resume_done after a HOME flip: the relaunched app is playing the game again
    picture     the controller confirmed the picture (Outcome.confirmed_at)

`cut_at` is when the old picture left the screen: the flip on two sticks, the start of the visible
tune on one. The supervisor grades it against the feed ~90 s later (was a play on screen in the game
we left? did we land mid-play?) and the nightly report rolls the traces up.

    .venv/bin/python -m redzone.trace [N]      the last N traces, one line each
"""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

VERSION = 1

# Step names, in the order they can happen. Anything else a caller marks is kept but not spanned.
STEPS = ("started", "tune_start", "tune_done", "park_done", "safe_start", "safe_done",
         "flip_start", "flip_done", "resume_done", "rewind_done", "done")


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 2)


def _first(marks: list[tuple[str, float]], name: str) -> float | None:
    for n, t in marks:
        if n == name:
            return t
    return None


def _last(marks: list[tuple[str, float]], name: str) -> float | None:
    for n, t in reversed(marks):
        if n == name:
            return t
    return None


def _span(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return _r(max(0.0, b - a))


def _delay(book: Any, target: str | None) -> dict | None:
    if book is None or not target:
        return None
    try:
        d = book.get(target)
        return {"mu": _r(d.mu), "sigma": _r(d.sigma)}
    except Exception:
        return None


def build(d: Any, before: str | None, target: str | None, out: Any, submitted_at: float, *,
          delays: Any = None, before_target: str | None = None, labels: dict | None = None,
          from_replay: bool = False, now: float | None = None) -> dict:
    """The trace for one finished switch (any outcome, dry runs included)."""
    now = time.time() if now is None else now
    marks = [(str(n), float(t)) for n, t in (getattr(out, "marks", None) or [])]
    decided = float(getattr(d, "at", None) or submitted_at)
    picture = getattr(out, "confirmed_at", None)
    picture = float(picture) if picture is not None and getattr(out, "ok", False) else None
    started = _first(marks, "started")
    flip_start, flip_done = _first(marks, "flip_start"), _last(marks, "flip_done")
    tune_start, tune_done = _first(marks, "tune_start"), _last(marks, "tune_done")
    safe_start, safe_done = _first(marks, "safe_start"), _last(marks, "safe_done")
    path = getattr(out, "path", "") or ""
    # When the old picture left the screen. Two sticks: the input change. One stick: the visible
    # stick starts tuning (a dry run, "already on it" or a refusal never cut anything).
    cut = None
    if getattr(out, "ok", False) and not getattr(out, "dry_run", False) and path != "already":
        if flip_start is not None:
            cut = flip_done if flip_done is not None else flip_start
        elif path == "single" and tune_start is not None:
            cut = tune_start
    resumed = getattr(out, "resumed", None)
    labels = labels or {}
    rec = {
        "v": VERSION,
        "id": f"{int(submitted_at * 1000):x}",
        "action": getattr(d, "action", ""), "kind": getattr(d, "kind", ""), "reason": str(getattr(d, "reason", ""))[:200],
        "from": before, "from_label": labels.get(before) if before else None, "from_target": before_target,
        "from_replay": bool(from_replay),
        "to": getattr(d, "target", None), "to_label": labels.get(getattr(d, "target", None)), "target": target,
        "rewind_s": _r(getattr(d, "rewind_s", 0.0) or 0.0),
        "ok": bool(getattr(out, "ok", False)), "dry_run": bool(getattr(out, "dry_run", False)),
        "path": path, "stick": getattr(out, "stick", None),
        "picture_confirmed": bool(picture is not None and resumed is not False),
        "detail": str(getattr(out, "detail", ""))[:300],
        "decided_at": _r(decided), "submitted_at": _r(submitted_at), "cut_at": _r(cut),
        "picture_at": _r(picture), "done_at": _r(now),
        "spans": {
            "decide": _span(decided, submitted_at),            # decider -> command (should be ~0)
            "queued": _span(submitted_at, started),            # waiting for the controller's lock
            "tune": _span(tune_start, tune_done),
            "safe_wait": _span(safe_start, safe_done),
            "flip": _r(getattr(out, "flip_s", None)) if getattr(out, "flip_s", None) is not None
            else _span(flip_start, flip_done),
            "resume": _r(getattr(out, "resume_s", None)),
            "dead_air": _span(cut, picture),                    # the old game is gone, the new one isn't up
            "to_picture": _span(decided, picture),              # what you wait for: decision -> picture
            "total": _span(decided, now),
        },
        "steps": [[n, _r(t - decided)] for n, t in marks],
        "leave_delay": _delay(delays, before_target),
        "enter_delay": _delay(delays, target),
    }
    return rec


def of_row(row: dict) -> dict | None:
    """The trace saved in a switches row (None for rows written before traces existed)."""
    d = row.get("detail")
    if isinstance(d, str):
        try:
            d = json.loads(d or "{}")
        except ValueError:
            return None
    t = d.get("trace") if isinstance(d, dict) else None
    return t if isinstance(t, dict) else None


def line(t: dict) -> str:
    """One readable line: 21:14:03 better BUF@MIA tuned ok  decide->picture 7.4s (tune 4.1, wait 2.0, flip 1.1)."""
    from datetime import datetime
    from redzone.supervisor import ET
    at = datetime.fromtimestamp(float(t.get("decided_at") or 0), ET).strftime("%H:%M:%S")
    sp = t.get("spans") or {}
    parts = [f"{k} {sp[k]:.1f}" for k in ("queued", "tune", "safe_wait", "flip", "resume") if sp.get(k)]
    status = "dry" if t.get("dry_run") else ("ok" if t.get("picture_confirmed") else
                                             ("ok, picture unconfirmed" if t.get("ok") else "FAILED"))
    to = t.get("to_label") or t.get("to") or "?"
    head = f"{at} {t.get('kind') or t.get('action')} -> {to} [{t.get('path') or '-'}] {status}"
    if sp.get("to_picture") is not None:
        head += f"  decision->picture {sp['to_picture']:.1f}s"
    if sp.get("dead_air") is not None:
        head += f", dead air {sp['dead_air']:.1f}s"
    if parts:
        head += " (" + ", ".join(parts) + ")"
    if not t.get("ok") and t.get("detail"):
        head += f": {t['detail'][:80]}"
    return head


def pct(xs: list[float], q: float) -> float | None:
    """The q-quantile (0..1) of xs, nearest rank; None for no data."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[k]


def span_medians(traces: list[dict]) -> dict[str, float]:
    out = {}
    for k in ("queued", "tune", "safe_wait", "flip", "resume", "dead_air", "to_picture"):
        xs = [float(t["spans"][k]) for t in traces if (t.get("spans") or {}).get(k) is not None]
        if xs:
            out[k] = round(statistics.median(xs), 1)
    return out


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    n = int(args[0]) if args and args[0].isdigit() else 20
    from redzone.store import Store
    rows = Store().recent_switches(n)
    shown = 0
    for r in reversed(rows):
        t = of_row(r)
        if t is not None:
            print(line(t))
            shown += 1
    if not shown:
        print("No traced switches yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
