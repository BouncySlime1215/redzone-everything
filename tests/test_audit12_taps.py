"""AUDIT-12, phone taps: a button whose request is still out ignores a second tap.

Why: act(), bet-save, the settings savers and "I missed that" set aria-busy on the button while in flight, but the
delegated click handler (app.js) only honoured disabled / aria-disabled. A double tap sent Skip twice (two games
skipped), added a bet twice and logged two misses; each act() tap gets a fresh id, so the server could not dedupe.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "redzone" / "web" / "static"


def test_click_handler_ignores_a_busy_button():
    js = (STATIC / "app.js").read_text()
    m = re.search(r'document\.addEventListener\("click".*?const fn = ACTIONS', js, re.S)
    assert m and 'aria-busy") === "true"' in m.group(0)


def test_every_busy_marker_is_cleared_in_a_finally():
    for f in STATIC.glob("rz-*.js"):
        if f.name == "rz-mock.js":
            continue
        js = f.read_text()
        assert js.count('setAttribute("aria-busy", "true")') <= js.count('removeAttribute("aria-busy")'), f.name
