"""Replay demo: watch how the app WOULD have switched during a past day's real games.

    data/venv/bin/python -m redzone.demo 20261101 --speed 30 --port 8790
    data/venv/bin/python -m redzone.demo 20261101 --start 13:00 --end 17:00
    data/venv/bin/python -m redzone.demo 20261101 --summary          # timeline + report card, no server

Runs the real engine in dry run on a simulated clock. Never sends anything to the TV.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time

from redzone.demo.download import day_dir, download
from redzone.demo.engine import DemoEngine, demo_config, et_at, find_user_config, hm, load_day, report_text


def build(day: str, speed: float, step: float) -> DemoEngine:
    folder = day_dir(day)
    if not any(folder.glob("*/summary_*.json")):
        print(f"Downloading {day} play-by-play into {folder} ...")
        download(day)
    reps, tv = load_day(folder)
    if not reps:
        sys.exit(f"No finished games with play-by-play in {folder}")
    cfg = demo_config(folder / "_work", find_user_config())
    return DemoEngine(reps, tv, cfg, day=day, speed=speed, step=step, work=folder / "_work")


def fast_forward(e: DemoEngine, start: float | None) -> None:
    """Jump to --start: run everything before it quietly, so the app's memory is right when you arrive."""
    if start and start > e.clock.t:
        e.quiet = True
        e.advance(start)
        e.quiet = False


async def serve(e: DemoEngine, port: int, end: float) -> None:
    import uvicorn

    from redzone.__main__ import phone_url
    from redzone.web.server import build_app
    server = uvicorn.Server(uvicorn.Config(build_app(e), host="0.0.0.0", port=port, log_level="warning"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    srv = asyncio.create_task(server.serve())
    print(f"\n  Replay demo of {e.day} at {e.speed:g}x — DRY RUN, the TV is never touched.")
    print(f"  On your phone (same Wi-Fi): {phone_url(port)}")
    print(f"  Starting at {hm(e.clock.t)} ET. Ctrl-C to stop (prints the report card).\n")
    tick, last_push, real0, sim0 = 0.1, 0.0, time.monotonic(), e.clock.t
    while not stop.is_set() and e.clock.t < end and not e.finished:
        e.advance(sim0 + (time.monotonic() - real0) * e.speed)
        if time.monotonic() - last_push > 0.5:
            e.broadcast()
            last_push = time.monotonic()
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick)
        except asyncio.TimeoutError:
            pass
    print(report_text(e.report(start=sim0)))
    if not stop.is_set():
        print("\n  Replay finished. The page stays up until Ctrl-C.")
        e.broadcast()
        await stop.wait()
    server.should_exit = True
    await srv
    await e.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m redzone.demo", description=__doc__.split("\n")[0])
    p.add_argument("day", help="YYYYMMDD, e.g. 20261101")
    p.add_argument("--speed", type=float, default=30.0, help="how many times faster than real life")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--start", help="ET clock time to jump to, e.g. 13:00")
    p.add_argument("--end", help="ET clock time to stop at, e.g. 17:00")
    p.add_argument("--step", type=float, default=2.0, help="sim seconds per engine step")
    p.add_argument("--summary", action="store_true", help="print the full timeline + report card, no server")
    p.add_argument("--json", action="store_true", help="with --summary: report card as JSON")
    a = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)   # the timeline shows up live even when piped
    logging.basicConfig(level=logging.WARNING)
    e = build(a.day, a.speed, a.step)
    start = et_at(a.day, a.start) if a.start else None
    end = et_at(a.day, a.end) if a.end else e.t1
    fast_forward(e, start)
    if a.summary:
        print(f"Replay of {a.day}: {len(e.replays)} games, {hm(e.clock.t)} to {hm(end)} ET (dry run, no TV)\n")
        e.advance(end)
        card = e.report(start=start)
        print(json.dumps(card, indent=1) if a.json else report_text(card))
        return
    asyncio.run(serve(e, a.port, end))


if __name__ == "__main__":
    main()
