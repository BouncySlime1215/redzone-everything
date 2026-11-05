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

Contract (redzone/contracts/trace.py, docs/architecture.md "Trace"): each trace also becomes a few
TraceEvents, emitted to the engine's TraceSink (JsonlTrace -> data/traces.jsonl) when the switch
reports back. switch_id is trace.switch_id(submitted_at), the same `id` the stored trace carries:

    decided   the decider picked it                       (Decision.at; component "decider")
    guarded   the safe-moment guard let it through        (safe_done; "guard", rule "no mid-play cut").
              Once the engine runs core guards through contracts.guard.traced(), that emits
              `guarded` for the decision itself; this one stays as the controller's last re-check.
    command   the first command that changes the screen   (flip_start on two sticks, else tune_start; "control")
    picture   the new picture is confirmed                (Outcome.confirmed_at; "control")
    aborted   held back, not the target's fault: no safe moment, cancelled, input unknown, dry run
    failed    anything else that didn't end on a confirmed picture (incl. ok-but-unconfirmed)

    .venv/bin/python -m redzone.trace [N]      the last N traces, one line each
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

from redzone.contracts.trace import MemoryTrace, Stage, TraceEvent, TraceSink

__all__ = ["JsonlTrace", "MemoryTrace", "Stage", "TraceEvent", "TraceSink", "build", "emit", "events",
           "of_row", "switch_id"]

log = logging.getLogger(__name__)


class JsonlTrace:
    """The contract's file sink: one TraceEvent per line in data/traces.jsonl (the supervisor's
    log_growth rule keeps every data/*.jsonl under its size cap)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def emit(self, ev: TraceEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(ev.jsonl() + "\n")


# Paths that change the picture on the stick already on screen: the cut is the first command there.
# "prime_tile" is a Prime multiview tile switch (redzone/tuners/prime_multiview.py).
IN_PLACE = ("single", "prime_tile")

def switch_id(submitted_at: float) -> str:
    """The id every component tags this switch's events with (ms since epoch, hex)."""
    return f"{int(submitted_at * 1000):x}"


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
          from_replay: bool = False, now: float | None = None, provenance: dict | None = None,
          forced: bool = False) -> dict:
    """The trace for one finished switch (any outcome, dry runs included). `provenance` is
    {module: what it contributed} (Engine._provenance + Outcome.provenance): the phone's Trace sheet
    reads it from switches.detail.trace.provenance. forced: the cut went through on your second
    "Go now" tap (Nick, Nov 5: it switches at once, even mid-play); graded apart from the
    never-join-mid-play line (entries_mid_play_forced)."""
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
        elif path in IN_PLACE and tune_start is not None:
            cut = tune_start
    resumed = getattr(out, "resumed", None)
    labels = labels or {}
    rec = {
        "v": VERSION,
        "id": switch_id(submitted_at),
        "action": getattr(d, "action", ""), "kind": getattr(d, "kind", ""), "reason": str(getattr(d, "reason", ""))[:200],
        "from": before, "from_label": labels.get(before) if before else None, "from_target": before_target,
        "from_replay": bool(from_replay),
        "to": getattr(d, "target", None), "to_label": labels.get(getattr(d, "target", None)), "target": target,
        "rewind_s": _r(getattr(d, "rewind_s", 0.0) or 0.0),
        "ok": bool(getattr(out, "ok", False)), "dry_run": bool(getattr(out, "dry_run", False)),
        "path": path, "stick": getattr(out, "stick", None),
        "picture_confirmed": bool(picture is not None and resumed is not False
                                  and not getattr(out, "dry_run", False)),
        "detail": str(getattr(out, "detail", ""))[:300],
        "settled": getattr(out, "settled", None), "stall_s": getattr(out, "stall_s", None),
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
        "provenance": dict(provenance or {}),
        "forced": bool(forced),
    }
    rec["stages"] = [[e.stage.value, _r(e.at)] for e in events(rec)]
    return rec


def events(t: dict) -> list:
    """The contract's TraceEvents for one trace, in time order (redzone/contracts/trace.py)."""
    sid = str(t.get("id") or "")
    steps = {n: (t.get("decided_at") or 0) + (off or 0) for n, off in (t.get("steps") or [])}
    sp = t.get("spans") or {}
    out = []

    def add(stage, at, component, **detail):
        if at is not None:
            out.append(TraceEvent(sid, Stage(stage), float(at), component,
                                  {k: v for k, v in detail.items() if v is not None}))

    add("decided", t.get("decided_at"), "decider", kind=t.get("kind"), action=t.get("action"),
        frm=t.get("from"), to=t.get("to"), target=t.get("target"), reason=t.get("reason"),
        forced=True if t.get("forced") else None)
    from redzone.supervisor import NOT_THE_TARGET   # held back / cancelled / refused: not a failure
    held = bool(t.get("dry_run")) or (not t.get("ok") and
                                      str(t.get("detail") or "").lower().startswith(NOT_THE_TARGET))
    if steps.get("safe_done") is not None:
        add("guarded", steps["safe_done"], "guard", rule="no mid-play cut", safe_wait_s=sp.get("safe_wait"),
            queued_s=sp.get("queued"))
    cmd = steps.get("flip_start") if steps.get("flip_start") is not None else (
        steps.get("tune_start") if t.get("path") in IN_PLACE else None)
    add("command", cmd, "control", path=t.get("path"), stick=t.get("stick"), tune_s=sp.get("tune"),
        flip_s=sp.get("flip"))
    end = t.get("done_at")
    if t.get("picture_confirmed"):
        add("picture", t.get("picture_at"), "control", to_picture_s=sp.get("to_picture"),
            dead_air_s=sp.get("dead_air"), resume_s=sp.get("resume"))
    elif held:
        add("aborted", end, "control", why=t.get("detail"))
    else:
        add("failed", end, "control", why=t.get("detail"), picture_at=t.get("picture_at"))
    out.sort(key=lambda e: e.at)
    return out


def emit(sink: Any, t: dict) -> int:
    """Send one trace's events to a TraceSink; a broken sink never breaks the switch. Events sent."""
    if sink is None:
        return 0
    n = 0
    for ev in events(t):
        try:
            sink.emit(ev)
            n += 1
        except Exception:
            log.exception("trace sink failed")
            break
    return n


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
