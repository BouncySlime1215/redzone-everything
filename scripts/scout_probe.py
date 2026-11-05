#!/usr/bin/env python3
"""Data scout probe: for each scout.json source, read the host's robots.txt and fetch ONE probe URL.

Run on Kaggle/Colab CPU (the cloud dev container's network allowlist blocks most sports hosts; never the Mac).
Stdlib only. One request every 2 s, an honest User-Agent, no keys, no cookies, no retries past one.
It never follows a login, never retries a 403/429 (a bot block is a stop, not a puzzle).

    python scripts/scout_probe.py --json docs/datasources/scout.json --out probe.json
Prints one line per source: robots verdict, HTTP status, bytes. Paste probe.json back to the scout thread.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

UA = "redzone-datascout/0.1 (personal research; polite, 1 request per 2 s)"


def robots_ok(url: str) -> tuple[str, bool]:
    u = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{u.scheme}://{u.netloc}/robots.txt")
    try:
        req = urllib.request.Request(rp.url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            rp.parse(r.read().decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return f"robots {e.code}", False         # disallow-all per RFC 9309 practice
        return f"robots {e.code} (none)", True        # 404 = no rules
    except Exception as e:  # noqa: BLE001
        return f"robots error {type(e).__name__}", False
    return "robots ok" if rp.can_fetch(UA, url) else "robots DISALLOW", rp.can_fetch(UA, url)


def fetch(url: str) -> tuple[int, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, len(r.read(5_000_000))
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception:  # noqa: BLE001
        return 0, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", default="docs/datasources/scout.json")
    ap.add_argument("--out", default="probe.json")
    ap.add_argument("--only", nargs="*", help="source ids to probe")
    a = ap.parse_args(argv)
    d = json.load(open(a.json))
    res = []
    for s in d["candidates"]:
        url = s.get("probe", "")
        if not url or (a.only and s["id"] not in a.only):
            continue
        why, ok = robots_ok(url)
        time.sleep(2)
        code, n = fetch(url) if ok else (-1, 0)
        time.sleep(2)
        verdict = "OK" if ok and 200 <= code < 300 else ("STOP bot-block" if code in (401, 403, 429) else "CHECK")
        res.append({"id": s["id"], "url": url, "robots": why, "http": code, "bytes": n, "verdict": verdict})
        print(f"{s['id']:22s} {why:24s} http {code:4d} {n:>9,d} B  {verdict}", flush=True)
    json.dump({"ua": UA, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": res}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
