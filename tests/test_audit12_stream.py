"""AUDIT-12, phone stream: coming back to the page rebuilds the live stream.

Why: on visibilitychange the phone only re-opened the EventSource when it said CLOSED. A phone that slept keeps a
dead socket that still says OPEN (server pings are SSE comments the page never sees), so the screen sat on stale
state under a green "Connected" until the user killed the tab. One GET /api/state refreshed the data once but
nothing after it. Also: every module must import rz-api.js at one version, or the page runs two streams.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "redzone" / "web" / "static"


def test_visible_again_always_reopens_the_stream():
    js = (STATIC / "rz-api.js").read_text()
    m = re.search(r'addEventListener\("visibilitychange".*?\}\);', js, re.S)
    assert m and "open();" in m.group(0) and "readyState" not in m.group(0)


def test_one_version_of_rz_api_everywhere():
    seen = set()
    for f in [*STATIC.glob("*.js")]:
        seen |= set(re.findall(r'rz-api\.js\?v=(\d+)', f.read_text()))
    assert len(seen) == 1, seen
