"""One command, at home, ~2 minutes: make the TV's own input change the default flip.

    .venv/bin/python scripts/tv_setup.py                  # find the TV by MAC, pair, test, SmartThings if needed
    .venv/bin/python scripts/tv_setup.py --ip 192.168.1.40   # try this address instead of searching
    .venv/bin/python scripts/tv_setup.py --smartthings    # (re)do only the SmartThings sign-in
    .venv/bin/python scripts/tv_setup.py --yes            # no questions: CEC readback decides, CEC turned off
    .venv/bin/python scripts/tv_setup.py --keep-cec       # leave HDMI-CEC on the sticks alone

Have the TV on, both sticks on, and the Mac on the home Wi-Fi. Watch the TV: it switches between
the sticks four times, and asks you to confirm each time. Everything it learns goes to
data/devices.json (tokens to data/tv_token.txt and data/smartthings_oauth.json, never git).
See docs/instant-switching.md.
Why: Makes the TV's own input change the default flip; HOME is the last resort (instant switching commit).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import tvsetup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", help="the TV's address, if you know it (still checked against its MAC)")
    ap.add_argument("--smartthings", action="store_true", help="only set up SmartThings")
    ap.add_argument("--no-smartthings", action="store_true", help="never set up SmartThings")
    ap.add_argument("--client-id", help="an existing SmartThings OAuth client id (skips creating the app)")
    ap.add_argument("--client-secret")
    ap.add_argument("--redirect-uri", default=tvsetup.DEFAULT_REDIRECT)
    ap.add_argument("--yes", action="store_true", help="don't ask me to look at the TV (CEC evidence only)")
    ap.add_argument("--keep-cec", action="store_true", help="don't turn HDMI-CEC off on the sticks")
    a = ap.parse_args()
    d = tvsetup.load()
    home = tvsetup.Home(d.get("sticks") or [])
    st = True if a.smartthings else (False if a.no_smartthings else None)
    rep = asyncio.run(tvsetup.run(home, ip=a.ip, ask=not a.yes, smartthings=st,
                                  cec_off=False if a.keep_cec else (True if a.yes else None),
                                  client_id=a.client_id, client_secret=a.client_secret,
                                  redirect_uri=a.redirect_uri, shots=ROOT / "data" / "lab"))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
