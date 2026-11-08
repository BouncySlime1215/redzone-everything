"""Screen Lab: compare device reports with supervised labels, including yesterday's DVR.

    .venv/bin/python scripts/screen_lab.py --host 192.168.1.42:5555 --duration 120
    .venv/bin/python scripts/screen_lab.py --report --out data/lab/screen_<session>.jsonl

Open the desired game/replay yourself, then label what you actually see in the phone's
Lab tab. Recording reads playback, foreground app and occasional UI text; it does not
switch streams or capture video. A media session saying 'playing' is NOT evidence that
a sports play is in progress. DVR tests cannot calibrate the delay of a live broadcast.
Why: Compares device reports with supervised labels, including yesterday's DVR (docstring).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners.adb import Stick  # noqa: E402

LAB = ROOT / "data" / "lab"
DB = ROOT / "data" / "redzone.db"


def stamp(clock=time.time, monotonic=time.monotonic) -> dict:
    unix = clock()
    return {"utc": datetime.fromtimestamp(unix, timezone.utc).isoformat(),
            "unix": unix, "monotonic": monotonic()}


def select_stick(host: str, devices: dict) -> Stick:
    """Only the explicitly supplied host may be contacted; saved order is irrelevant."""
    if not host or not host.strip():
        raise ValueError("--host must explicitly identify the stick being observed")
    host = host.strip()
    if ":" not in host:
        host += ":5555"
    saved = next((s for s in devices.get("sticks", []) if s.get("host") == host), {})
    stick = Stick(host, name=saved.get("name", ""))
    stick.packages = dict(saved.get("packages", {}))
    return stick


async def probe(call, clock=time.time, monotonic=time.monotonic) -> dict:
    """Bracket each readback; its value is not an instantaneous screen observation."""
    result = {"begin": stamp(clock, monotonic)}
    try:
        result["value"] = await call()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["end"] = stamp(clock, monotonic)
    return result


async def record(host: str, out: Path | None = None, duration: float | None = None) -> Path:
    devices_path = ROOT / "data" / "devices.json"
    devices = json.loads(devices_path.read_text()) if devices_path.exists() else {}
    stick = select_stick(host, devices)
    session_id = uuid.uuid4().hex
    out = out or LAB / f"screen_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{session_id[:8]}.jsonl"
    await _record(stick, out, session_id=session_id, duration=duration)
    return out


async def _record(stick: Stick, out: Path, *, session_id: str, duration: float | None = None,
                  clock=time.time, monotonic=time.monotonic, sleep=asyncio.sleep) -> None:
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("duration must be finite and positive")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Observing only {stick.host}. Session {session_id}. Writing {out}.")
    print("Label what you see on the phone; media 'playing' does not identify a live sports play.")
    began = stamp(clock, monotonic)
    with out.open("a", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps({"session_id": session_id, **row}) + "\n")
            stream.flush()
        write({"kind": "session_start", "at": began, "host": stick.host, "duration_s": duration,
               "signals": "device metadata; no visual classification or video capture"})
        status = "complete"
        try:
            connection = await probe(stick.connect, clock, monotonic)
            write({"kind": "connection", **connection})
            if connection.get("value") is not True:
                raise RuntimeError(f"cannot reach selected stick {stick.host}; connection unconfirmed")
            n, last_text = 0, float("-inf")
            while duration is None or monotonic() - began["monotonic"] < duration:
                start = stamp(clock, monotonic)
                probes = {"playback": await probe(stick.playback, clock, monotonic),
                          "foreground": await probe(stick.foreground, clock, monotonic)}
                if monotonic() - last_text >= 10:
                    async def ui_text():
                        return (await stick.screen_text())[:400]
                    probes["ui_text"] = await probe(ui_text, clock, monotonic)
                    last_text = monotonic()
                write({"kind": "sample", "sequence": n, "begin": start,
                       "end": stamp(clock, monotonic), "probes": probes})
                n += 1
                wait = max(0.0, 2.0 - (monotonic() - start["monotonic"]))
                if duration is not None:
                    wait = min(wait, max(0.0, duration - (monotonic() - began["monotonic"])))
                if wait:
                    await sleep(wait)
        except BaseException:
            status = "interrupted"
            raise
        finally:
            closed = await probe(stick.close, clock, monotonic)
            write({"kind": "session_end", "at": stamp(clock, monotonic), "status": status, "close": closed})


def load_sessions(out: Path) -> dict:
    """Ignore legacy unbounded rows and incomplete JSON; never invent their session."""
    sessions = {}
    if not out.exists():
        return sessions
    for line in out.read_text().splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict) or not row.get("session_id"):
            continue
        sid, kind = row["session_id"], row.get("kind")
        if kind == "session_start":
            sessions[sid] = {"start": row, "samples": [], "end": None}
        elif sid in sessions and kind == "sample":
            sessions[sid]["samples"].append(row)
        elif sid in sessions and kind == "session_end":
            sessions[sid]["end"] = row
    return sessions


def summarize(session: dict, labels: list, label_max_age: float = 30.0) -> dict:
    """Descriptive co-occurrence, not classifier accuracy or visual ground truth."""
    if label_max_age <= 0:
        raise ValueError("label maximum age must be positive")
    start = session["start"]["at"]["unix"]
    samples = session["samples"]
    end = session["end"]["at"]["unix"] if session["end"] else max(
        (row["end"]["unix"] for row in samples), default=start)
    # A label from yesterday or the preceding recording never initializes this session.
    labels = sorted((float(t), str(lab)) for t, lab in labels if start <= t <= end)
    counts, speeds = {}, {}
    labeled = 0
    previous = None
    for row in samples:
        reading = row.get("probes", {}).get("playback", {})
        lo, hi = reading.get("begin", {}).get("unix"), reading.get("end", {}).get("unix")
        pb = reading.get("value")
        if lo is None or hi is None or hi < lo or not isinstance(pb, dict):
            previous = None
            continue
        eligible = [(t, lab) for t, lab in labels if t <= lo and hi - t <= label_max_age]
        # A human changed the label while this query ran: the interval is ambiguous.
        if not eligible or any(lo < t <= hi for t, _ in labels):
            previous = None
            continue
        label = eligible[-1]
        state = str(pb.get("state", "unknown"))
        counts[(label[1], state)] = counts.get((label[1], state), 0) + 1
        labeled += 1
        pos = pb.get("position")
        at = (reading["begin"]["monotonic"] + reading["end"]["monotonic"]) / 2
        if previous and previous["label"] == label and previous["pkg"] == pb.get("pkg"):
            elapsed = at - previous["at"]
            prior_pos = previous["pos"]
            if isinstance(pos, (int, float)) and isinstance(prior_pos, (int, float)) \
                    and 0 < elapsed <= 10 and pos >= prior_pos:
                speeds.setdefault(label[1], []).append((pos - prior_pos) / 1000 / elapsed)
        previous = {"label": label, "pkg": pb.get("pkg"), "pos": pos, "at": at}
    return {"counts": counts, "speeds": speeds, "labeled": labeled, "total": len(samples),
            "start": start, "end": end}


def report(out: Path, db_path: Path = DB, label_max_age: float = 30.0) -> None:
    sessions = load_sessions(Path(out))
    if not sessions:
        print("No session-bound recording found. Record a new session with an explicit --host.")
        return
    labels = []
    if Path(db_path).exists():
        with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
            try:
                labels = db.execute("SELECT ts, label FROM labels ORDER BY ts").fetchall()
            except sqlite3.OperationalError:
                pass
    print("Device metadata beside your recent labels — descriptive counts, not classifier accuracy.")
    print("Playing media can contain live play, a break, ads, or replay; metadata alone cannot distinguish them.")
    for sid, session in sessions.items():
        stats = summarize(session, labels, label_max_age)
        print(f"\nSession {sid}: {stats['labeled']}/{stats['total']} samples have an unambiguous label "
              f"no older than {label_max_age:g}s.")
        for (label, state), count in sorted(stats["counts"].items()):
            print(f"  you labeled {label:<12} media reported {state:<12} x{count}")
        for label, speeds in sorted(stats["speeds"].items()):
            speeds.sort()
            print(f"  {label}: median media-position speed {speeds[len(speeds)//2]:.2f}x (n={len(speeds)})")


def positive(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="explicit stick host/IP (optional :5555)")
    parser.add_argument("--out", type=Path, help="recording path; required for --report")
    parser.add_argument("--duration", type=positive, help="seconds to observe, otherwise until Ctrl-C")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--label-max-age", type=positive, default=30.0,
                        help="maximum age of a human label in reports (seconds; default 30)")
    args = parser.parse_args()
    if args.report:
        if not args.out:
            parser.error("--report requires --out for a specific recording")
        report(args.out, label_max_age=args.label_max_age)
    else:
        if not args.host:
            parser.error("recording requires --host; no saved stick is selected automatically")
        try:
            asyncio.run(record(args.host, args.out, args.duration))
        except KeyboardInterrupt:
            print("\nObservation stopped.")


if __name__ == "__main__":
    main()
