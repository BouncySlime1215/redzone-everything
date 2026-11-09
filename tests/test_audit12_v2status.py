"""AUDIT-12, phone v2 boot: a failed /api/v2/status ask is retried, not read as "v2 is off".

Why: rz-v2.js asked /api/v2/status once at page load and cached the promise; any failure (the Mac still starting,
a Wi-Fi blink) set v2.on = false for good, so tap status, switch chips, the held line and the orientation beat were
gone until the page was reloaded.
"""
import re
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "redzone" / "web" / "static" / "rz-v2.js").read_text()


def test_failed_status_check_clears_the_cache_and_retries():
    m = re.search(r"function check\(\) \{.*?\n\}\ncheck\(\);", JS, re.S)
    assert m
    body = m.group(0)
    assert "checking = null" in body and "setTimeout(check" in body
