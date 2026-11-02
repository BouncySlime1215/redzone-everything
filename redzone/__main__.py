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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = Config()
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
    tasks = [asyncio.create_task(server.serve()), asyncio.create_task(engine.run())]
    await stop.wait()
    server.should_exit = True
    for t in tasks[1:]:
        t.cancel()
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
