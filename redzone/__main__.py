"""Start RedZone Everything:  .venv/bin/python -m redzone

Prints the phone address and a QR code, then runs until stopped.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess

import uvicorn

from redzone.config import Config
from redzone.engine import Engine
from redzone.web.server import build_app


def phone_url(port: int) -> str:
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = socket.gethostname().split(".")[0]
    return f"http://{name}.local:{port}"


def print_qr(url: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:
        pass


def _log_transitions() -> None:
    """Every state-machine move goes to data/transitions.jsonl with the running commit (redzone/fsm.py), so
    `why is it in this state?` survives a restart. Read it with GET /api/machines/history."""
    from pathlib import Path

    from redzone import fsm
    from redzone.config import DATA
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=3, cwd=Path(__file__).resolve().parents[1]).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    try:
        fsm.log_transitions(DATA / "transitions.jsonl", commit=sha)
    except OSError:
        logging.getLogger(__name__).warning("transition log not started", exc_info=True)


async def main(cfg: Config | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = cfg if cfg is not None else Config()
    _log_transitions()
    engine = Engine(cfg)
    port = int(os.environ.get("PORT") or cfg.get("server.port", 8777))
    url = phone_url(port)
    print("\n  RedZone Everything is running.")
    print(f"  On your phone (same Wi-Fi): {url}")
    print(f"  Mode: {'DRY RUN (TV not touched)' if cfg.get('mode.dry_run', True) else 'TV CONTROL ON'}\n")
    print_qr(url)
    server = uvicorn.Server(uvicorn.Config(build_app(engine), host="0.0.0.0", port=port, log_level="warning"))
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run_services(server, engine, stop)


async def run_services(server, engine, stop: asyncio.Event) -> None:
    """A failed producer must not leave a healthy-looking frozen dashboard.

    Exit so launchd can restart the whole app, and always release device shells,
    HTTP clients and the keep-awake process after the loops have stopped.
    """
    async def serve():
        try:
            await server.serve()
        except SystemExit as exc:
            raise RuntimeError(f"Phone server could not start (exit {exc.code})") from exc

    web = asyncio.create_task(serve())
    producer = asyncio.create_task(engine.run())
    stopped = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({web, producer, stopped}, return_when=asyncio.FIRST_COMPLETED)
        for task in (web, producer):
            if task in done:
                task.result()  # surface a crash instead of hiding it behind stop.wait()
                if not stop.is_set():
                    name = "Producer" if task is producer else "Phone server"
                    raise RuntimeError(f"{name} stopped unexpectedly")
    finally:
        server.should_exit = True
        producer.cancel()
        stopped.cancel()
        await asyncio.gather(producer, stopped, return_exceptions=True)
        await engine.close()
        try:
            await asyncio.wait_for(web, 5)
        except (Exception, asyncio.CancelledError):
            web.cancel()
            await asyncio.gather(web, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
