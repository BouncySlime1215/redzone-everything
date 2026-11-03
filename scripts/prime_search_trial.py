#!/usr/bin/env python3
"""Explicit, supervised Prime replay navigation trial on one authorized stick.

Does not connect/pair devices, change monitor inputs, or enable the live engine.
The --execute flag permits search/navigation and --play additionally permits the
verified replay button. Individual menu screenshots stay in a temporary folder.
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.screen_ocr import OCRResult, prepare, recognize
from redzone.tuners.adb import ADB, PRIME_PACKAGES, Stick
from redzone.tuners.prime_search import CapturedScreen, SearchProof, run_prime_search


async def adb_bytes(*args, timeout=12):
    proc = await asyncio.create_subprocess_exec(ADB, *args, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        raise
    if proc.returncode:
        raise RuntimeError("ADB read failed; device authorization/connection must already work")
    return stdout


async def main(args):
    if not args.execute:
        raise SystemExit("No device action taken. Add --execute for an explicitly selected supervised trial.")
    preparation = await asyncio.to_thread(prepare)
    if preparation.status != "ready":
        raise SystemExit("Local OCR is not ready; no device action taken: " + preparation.error)
    devices = (await adb_bytes("devices")).decode()
    if not any(line.split() == [args.host, "device"] for line in devices.splitlines()):
        raise SystemExit("The exact host is not already authorized; no connect or pairing attempted.")
    stick = Stick(args.host)
    stick.state = "connected"
    packages = (await adb_bytes("-s", args.host, "shell", "pm", "list", "packages")).decode()
    installed = {line.removeprefix("package:").strip() for line in packages.splitlines()}
    package = next((p for p in PRIME_PACKAGES if p in installed), None)
    if not package:
        raise SystemExit("Prime package not found on the selected stick.")
    stick.packages["prime"] = package
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="redzone-prime-trial-") as directory:
            image = Path(directory) / "menu.png"
            capture_sequence = 0

            async def observe():
                nonlocal capture_sequence
                capture_sequence += 1
                captured_at = time.monotonic()
                try:
                    image.write_bytes(await adb_bytes("-s", args.host, "exec-out", "screencap", "-p"))
                    observation = await asyncio.to_thread(recognize, image)
                except (RuntimeError, OSError, asyncio.TimeoutError):
                    observation = OCRResult("error", error="fresh menu capture failed")
                return CapturedScreen(observation, capture_sequence, captured_at)

            # A time cap cancels safely; no mutation is replayed after cancellation.
            result = SearchProof(requested_date=args.date.isoformat())
            try:
                await asyncio.wait_for(run_prime_search(stick, observe,
                    query=args.query, home=args.home, away=args.away, requested=args.date,
                    allow_play=args.play, proof=result), timeout=180)
            except (RuntimeError, asyncio.TimeoutError) as exc:
                result.reason = "trial timed out; no commands retried" if isinstance(exc, asyncio.TimeoutError) else str(exc)
                result.elapsed_s = round(time.monotonic() - started, 3)
        payload = asdict(result)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2))
        print(json.dumps({"status": result.status, "reason": result.reason,
                          "selected_card": result.selected_card, "elapsed_s": result.elapsed_s,
                          "report": str(args.out)}))
        return result.status
    finally:
        await stick.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="exact already-authorized adb serial/IP:port")
    parser.add_argument("--query", required=True)
    parser.add_argument("--home", required=True, help="displayed full team name or unambiguous mascot")
    parser.add_argument("--away", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--out", required=True, type=Path, help="local JSON result; prefer ignored data/lab")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--play", action="store_true")
    status = asyncio.run(main(parser.parse_args()))
    raise SystemExit(0 if status in {"recording_opened", "player_title_observed"} else 1)
