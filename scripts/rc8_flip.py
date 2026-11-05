#!/usr/bin/env python3
"""[RC8] Turn the time-shift buffer ON (or back OFF) for ONE league. Nick runs this himself; nothing else does.

    python scripts/rc8_flip.py nba on                 # adaptive B (redzone/buffer_rc8.buffer_for)
    python scripts/rc8_flip.py nba on --seconds 75    # pin B (e.g. the sweep's chosen value)
    python scripts/rc8_flip.py nba off                # rollback: this league only, every other key kept
    python scripts/rc8_flip.py nba on --dry-run       # print the change and the B it gives, send nothing
    python scripts/rc8_flip.py --status               # every league's RC8 state and B

By default the change goes to the running app (POST /api/settings on 127.0.0.1:<server.port>), so it takes effect
on the next fresh hidden-stick tune with no restart. --offline writes data/config.yaml instead (app stopped).
Only league_overrides.<league> is ever sent: never a global key (global keys leak into ungraded leagues).
The screen moves only after the next fresh tune of that league's game; the game on screen now is not re-held.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import buffer_rc8 as R  # noqa: E402
from redzone.config import Config, _deep_merge, validate  # noqa: E402
from redzone.models import League  # noqa: E402


class _View:
    """cfg.get over a plain dict (the config as it would be after the change)."""

    def __init__(self, data: dict):
        self.data = data

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _post(url: str, changes: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps({"changes": changes}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _parse_set(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise ValueError(f"--set wants key=value (got {it!r})")
        out[k.strip()] = yaml.safe_load(v)
    return out


def change_for(cfg_overrides: dict, league: str, on: bool, seconds: float | None, extra: dict | None = None) -> dict:
    """The league_overrides delta to send: only this league's RC8 keys (+ any graded companion key, on only)."""
    new = R.flip(cfg_overrides, league, seconds) if on else R.rollback(cfg_overrides, league)
    blk = new[league]
    delta = {R.RC8_FLAG: blk[R.RC8_FLAG], R.RC8_FIXED: blk[R.RC8_FIXED]}
    if on:
        delta.update(extra or {})
    return {"league_overrides": {league: delta}}


def status(cfg) -> list[dict]:
    out = []
    for lg in League:
        b, parts = R.buffer_for(cfg, lg.value)
        out.append({"league": lg.value, "rc8_on": R.is_on(cfg, lg.value), "buffer_s": b,
                    "formula_s": parts["formula_s"], "ratchet_min_s": parts["ratchet_min_s"],
                    "pinned_s": parts.get("pinned_s")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("league", nargs="?", choices=[lg.value for lg in League])
    ap.add_argument("state", nargs="?", choices=("on", "off"))
    ap.add_argument("--seconds", type=float, default=None, help="pin B for this league (default: the formula)")
    ap.add_argument("--offline", action="store_true", help="write data/config.yaml (the app must be stopped)")
    ap.add_argument("--url", default=None, help="the app's settings endpoint (default 127.0.0.1:<server.port>)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="a companion setting the sweep graded with RC8 (e.g. decider.entry.start_timed=true), "
                         "this league only; with off, undo it yourself the same way")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--config", type=Path, default=None, help="user config path (tests)")
    a = ap.parse_args(argv)
    cfg = Config(a.config, a.config.parent / "config_history") if a.config else Config()
    if a.status:
        print(json.dumps(status(cfg), indent=1))
        return 0
    if not a.league or not a.state:
        ap.error("league and on|off are required (or --status)")
    if a.state == "off" and a.seconds is not None:
        ap.error("--seconds only goes with on")
    if a.state == "off" and a.set:
        ap.error("--set only goes with on")
    changes = change_for(cfg.overrides, a.league, a.state == "on", a.seconds, _parse_set(a.set))
    validate(changes, cfg.defaults)   # same check the app runs; raises ConfigError on a bad value
    # what B the league would run with after the change
    b, parts = R.buffer_for(_View(_deep_merge(cfg.data, changes)), a.league)
    print(json.dumps({"change": changes, "league": a.league, "rc8_on": a.state == "on",
                      "buffer_s": b if a.state == "on" else None, "parts": parts}, indent=1))
    if a.dry_run:
        return 0
    if a.offline:
        cfg.update(changes)
        print(f"wrote {cfg.user_path}")
        return 0
    url = a.url or f"http://127.0.0.1:{cfg.get('server.port', 8777)}/api/settings"
    try:
        res = _post(url, changes)
    except OSError as e:
        print(f"could not reach the app at {url} ({e}); nothing changed. Use --offline with the app stopped.",
              file=sys.stderr)
        return 2
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
