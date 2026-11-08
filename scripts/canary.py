"""[Canary, item 8] Daily pre-game self-test of the HIDDEN stick. See redzone/canary.py for what it checks.

    .venv/bin/python scripts/canary.py --dry-run   # preflight only (API, no game soon, which stick is hidden)
    .venv/bin/python scripts/canary.py             # the test: exit 0 PASS, 1 FAIL, 2 deferred (game live/soon), 4 WARN
    .venv/bin/python scripts/canary.py --once-per-day   # skip (exit 0) when today already has a PASS or WARN
    .venv/bin/python scripts/canary.py --plist > ~/Library/LaunchAgents/com.redzone.canary.plist
                                                   # a daily launchd job (10:00 and 16:30, --once-per-day). Rendering
                                                   # changes nothing; loading it is Nick's call (a Mac service change).

Prints one JSON row (also appended to data/canary.jsonl). Never prints a stick address. FAIL / WARN go to the phone.
Why: Daily pre-game self-test of the hidden stick, item 8 (docstring, canary commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import canary  # noqa: E402
from redzone.config import Config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preflight only; no key is pressed")
    ap.add_argument("--api", default="", help="the app's API (default: deploy/deploy.yaml api)")
    ap.add_argument("--once-per-day", action="store_true", help="skip when today already passed (or warned)")
    ap.add_argument("--plist", action="store_true", help="print a launchd plist for a daily run and exit")
    a = ap.parse_args(argv)
    if a.plist:
        sys.stdout.write(plist(ROOT).decode())
        return 0
    if a.once_per_day and canary.done_today(ROOT / "data/canary.jsonl"):
        print(json.dumps({"status": "SKIPPED", "reason": "already passed today"}))
        return 0
    logging.basicConfig(level=logging.WARNING)
    api = a.api
    if not api:
        try:
            import yaml
            api = (yaml.safe_load((ROOT / "deploy/deploy.yaml").read_text()) or {}).get("api") or ""
        except Exception:
            api = ""
    api = api or "http://127.0.0.1:8778"
    cfg = Config()
    res = asyncio.run(canary.Canary(cfg, canary.real_env(cfg, ROOT, api)).run(dry_run=a.dry_run))
    print(json.dumps(res.row(), indent=1))
    return canary.EXIT.get(res.status, 1)


def plist(root: Path) -> bytes:
    """The daily job: the TCC-safe launcher of scripts/service.py (python changes into the checkout itself)."""
    import plistlib
    script = str(root / "scripts/canary.py")
    prog = (f"import os, runpy, sys; os.chdir({str(root)!r}); sys.argv = [{script!r}, '--once-per-day']; "
            f"runpy.run_path({script!r}, run_name='__main__')")
    logs = Path.home() / "Library/Logs/redzone"
    return plistlib.dumps({
        "Label": "com.redzone.canary",
        "ProgramArguments": [str(root / ".venv/bin/python"), "-u", "-c", prog],
        "StartCalendarInterval": [{"Hour": 10, "Minute": 0}, {"Hour": 16, "Minute": 30}],
        "RunAtLoad": False, "ProcessType": "Background", "Nice": 10, "Umask": 63,
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": str(logs / "canary.log"), "StandardErrorPath": str(logs / "canary.log"),
    }, sort_keys=False)


if __name__ == "__main__":
    raise SystemExit(main())
