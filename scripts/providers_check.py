"""Check every streaming app on your Fire TV sticks, then (optionally) tune one for real.

    .venv/bin/python scripts/providers_check.py                    # apps + today's links; TV untouched
    .venv/bin/python scripts/providers_check.py --ip 192.168.1.42  # a stick the setup doctor hasn't saved
    .venv/bin/python scripts/providers_check.py --tune peacock     # really tune: CHANGES WHAT'S ON THE TV
    .venv/bin/python scripts/providers_check.py --tune fubo --ref 10179   # ESPN on Fubo

Read-only unless you pass --tune.  What it learns (installed apps, signed out / not
subscribed) is saved to data/providers.json, which the app uses too.
Why: Checks every streaming app on the sticks, and optionally tunes one for real (docstring).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.discovery import providers as prov  # noqa: E402
from redzone.sources import eventlinks  # noqa: E402
from redzone.tuners import providers as more  # noqa: E402
from redzone.tuners.adb import Stick, adb_available  # noqa: E402
from redzone.tuners.tuners import TUNERS  # noqa: E402

DEVICES = ROOT / "data" / "devices.json"
ET = ZoneInfo("America/New_York")
OK, WARN, BAD = "✅", "⚠️ ", "❌"


def say(icon: str, text: str) -> None:
    print(f"  {icon} {text}")


def stick_hosts(extra: list[str]) -> list[str]:
    hosts = [h if ":" in h else f"{h}:5555" for h in extra]
    try:
        saved = json.loads(DEVICES.read_text()) if DEVICES.exists() else {}
    except ValueError:
        saved = {}
    for s in saved.get("sticks", []):
        if s.get("host") and s["host"] not in hosts:
            hosts.append(s["host"])
    return hosts


async def connect(host: str) -> Stick | None:
    st = Stick(host)
    if not await st.connect():
        say(BAD if st.state != "unauthorized" else WARN,
            f"{host}: {'approve the prompt on the TV, then rerun' if st.state == 'unauthorized' else 'not reachable'}")
        return None
    await st.identify()
    return st


async def show_apps(st: Stick, book: prov.ProviderBook) -> None:
    await book.refresh_sticks([st], force=True)
    print(f"\n  {st.name or st.model} ({st.host}) — {st.os}")
    for p in prov.PROVIDERS:
        pkg = st.packages.get(p.key)
        say(OK if pkg else "  ", f"{p.name:12} {pkg or 'not installed'}")


def upcoming(links: list[dict], app: str) -> list[dict]:
    """Main-broadcast links for this app, live or starting within 12 hours, soonest first."""
    now = time.time()
    keys = [k for k, a in prov.LINK_APPS.items() if a == app]
    out = [it for it in links if it.get("provider") in keys and not it.get("alt")
           and it.get("geo_ok") is not False and (it.get("start") or 0) > now - 4 * 3600
           and (it.get("start") or 0) < now + 12 * 3600]
    return sorted(out, key=lambda it: it.get("start") or 0)


def ref_of(app: str, it: dict) -> str | None:
    if app == "prime":
        return it.get("gti")
    if app == "espn":
        return it.get("play_id")
    if app == "appletv":
        return it.get("event_url")
    return it.get("ref")


def show_links(links: list[dict], st: Stick | None) -> None:
    print(f"\n3. Today's game links ({len(links)} found)")
    for app in sorted({prov.LINK_APPS[p] for p in prov.LINK_APPS}, key=lambda a: prov.RANK.get(a, 99)):
        items = upcoming(links, app)
        if not items:
            continue
        it = items[0]
        when = dt.datetime.fromtimestamp(it["start"], ET).strftime("%a %I:%M %p") if it.get("start") else "?"
        say(OK, f"{prov.BY_KEY[app].name}: {len(items)} upcoming — e.g. {it['away']} @ {it['home']} {when}")
        tuner = more.EXTRA_TUNERS.get(app)
        ref = ref_of(app, it)
        if tuner and ref:
            pkg = (st.packages.get(app) if st else None) or tuner.packages[0]
            print(f"       adb -s {st.host if st else '<stick>'} shell \"{tuner.command(ref, pkg)}\"")


async def tune(st: Stick, app: str, ref: str | None, links: list[dict], book: prov.ProviderBook) -> None:
    more.register(TUNERS)
    tuner = TUNERS.get(app)
    if tuner is None:
        say(BAD, f"No tuner for {app}")
        return
    if not ref:
        if app == "fubo":
            ref = prov.FUBO_CHANNELS["espn"]
        else:
            items = upcoming(links, app)
            ref = ref_of(app, items[0]) if items else None
    if not ref:
        say(WARN, f"No {app} game found today — pass --ref <id>")
        return
    target = f"{app}:{ref}"
    print(f"\n4. Tuning {st.host} to {target} (watch the TV)…")
    res = await tuner.tune(st, ref, target)
    for s in res.steps:
        print(f"       · {s}")
    say(OK if res.ok else BAD, f"{'Playing' if res.ok else 'Failed'} after {res.took:.1f}s {res.error}")
    note = book.learn(target, res.ok, f"{res.error} | {'; '.join(res.steps)}")
    if note:
        say(WARN, note)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", action="append", default=[], help="stick IP (repeatable)")
    ap.add_argument("--tune", metavar="APP", help="really tune the first stick: " + ", ".join(sorted(more.EXTRA_TUNERS)))
    ap.add_argument("--ref", help="the id to tune (default: the next game found for that app)")
    ap.add_argument("--date", default=dt.datetime.now(ET).date().isoformat())
    a = ap.parse_args()
    book = prov.ProviderBook()

    print("\nRedZone Everything — streaming apps check\n\n1. adb")
    sticks: list[Stick] = []
    if await adb_available():
        say(OK, "adb found")
        print("\n2. Apps on each stick")
        for host in stick_hosts(a.ip):
            st = await connect(host)
            if st:
                await show_apps(st, book)
                sticks.append(st)
        if not sticks:
            say(WARN, "No stick connected — run scripts/doctor.py first, or pass --ip")
    else:
        say(BAD, "adb missing — run: brew install --cask android-platform-tools")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        links = await eventlinks.fetch_event_links(client, a.date)
    show_links(links, sticks[0] if sticks else None)
    if a.tune:
        if not sticks:
            say(BAD, "Can't tune: no stick connected")
        else:
            await tune(sticks[0], a.tune, a.ref, links, book)
    states = book.states()
    print("\n5. What the app will use")
    for p in prov.PROVIDERS:
        s = states[p.key]
        if s.installed:
            say(OK if s.usable else WARN, f"{p.name}: {'ready' if s.usable else s.why_not()}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
