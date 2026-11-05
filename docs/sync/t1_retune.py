"""Repo copy of the overseer's Nov 4 stick helper, so the YTTV test bank no longer depends on a /private/tmp scratchpad.

The bank imports exactly this surface: PKG, CODES, sh(host, cmd, timeout), read(host), tune(host, ch).
ui_common/adslib/mech_common/fusion_lib find it through RZ_SYNC, else the old scratchpad if it still exists, else
docs/sync (this file). No stick addresses live here: callers pass the host (RZ_STICKS on the Mac).

Safety: this module only wraps adb. It never decides which stick is hidden; every caller guards that first
(run_all's state check + watchdog, Guard in mech_common, guard_hidden in adslib). It changes no setting.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

PKG = "com.amazon.firetv.youtube.tv"
ADB = shutil.which("adb") or "/opt/homebrew/bin/adb"
CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
         "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
         "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
         "btn": "1D2LKf_AW5w"}


def _parse_playback(text: str) -> dict:
    try:
        from redzone.tuners.adb import parse_playback
    except ImportError:  # bank run outside the repo: make the repo importable once
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from redzone.tuners.adb import parse_playback
    return parse_playback(text, PKG)


def _serial(host: str) -> str:
    return host if ":" in host else f"{host}:5555"


def sh(host: str, cmd: str, timeout: float = 8.0) -> str:
    """adb shell on one stick; returns stdout (raises on adb failure or timeout)."""
    s = _serial(host)
    r = subprocess.run([ADB, "-s", s, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and ("not found" in r.stderr or "offline" in r.stderr):
        subprocess.run([ADB, "connect", s], capture_output=True, text=True, timeout=timeout)
        r = subprocess.run([ADB, "-s", s, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"adb shell rc={r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout


def read(host: str) -> dict | None:
    """YTTV media_session, extrapolated: {st, sub, title, upd, pos, E, wall}. E = wall - pos (s). None if no session."""
    t0 = time.time()
    out = sh(host, "cat /proc/uptime; dumpsys media_session")
    t1 = time.time()
    up_ms = float(out.split()[0]) * 1000
    b = _parse_playback(out)
    if b.get("position") is None or b.get("updated") is None:
        return None
    sp = b.get("speed", 1.0) if b["state"] == "playing" else 0.0
    pos = (b["position"] + (up_ms - b["updated"]) * sp) / 1000
    wall = (t0 + t1) / 2
    return dict(st=b["state"], sub=b.get("subtitle", ""), title=b.get("title", ""), upd=b["updated"],
                pos=pos, E=wall - pos, wall=wall)


def tune(host: str, ch: str, timeout: float = 25.0) -> dict:
    """Deep-link one channel's live edge; wait for PLAYING. {ch, ok, t_link, t_playing, dt, L, E}."""
    if ch not in CODES:
        raise KeyError(f"no deep link for {ch!r}")
    try:
        before = read(host)
    except Exception:  # noqa: BLE001
        before = None
    t_link = time.time()
    sh(host, f"am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/{CODES[ch]} {PKG}")
    end, prev_upd = t_link + timeout, (before or {}).get("upd")
    while time.time() < end:
        time.sleep(0.5)
        try:
            r = read(host)
        except Exception:  # noqa: BLE001  a busy stick can drop one read
            continue
        if r and r["st"] == "playing" and r["upd"] != prev_upd:
            # the first PLAYING update after the link (a new `updated`, not the old channel's) is the landing
            return dict(ch=ch, ok=True, t_link=t_link, t_playing=r["wall"], dt=round(r["wall"] - t_link, 2),
                        L=round(r["pos"], 2), E=round(r["E"], 3))
    return dict(ch=ch, ok=False, t_link=t_link, dt=None, L=None, E=None)


if __name__ == "__main__":
    import json
    if len(sys.argv) < 2:
        print("usage: t1_retune.py HOST [read|tune CH]")
        sys.exit(2)
    h, op = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "read")
    print(json.dumps(read(h) if op == "read" else tune(h, sys.argv[3])))
