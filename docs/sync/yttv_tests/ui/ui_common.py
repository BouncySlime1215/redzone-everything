"""Shared plumbing for the YTTV "ui" test family (ui_NN_*.py).

Everything a test needs: arg parsing, the on-screen-stick safety guard, guarded key presses, screencap,
local OCR (Apple Vision via redzone.screen_ocr), pixel reads of the progress bar (via macOS `sips` -> BMP,
no PIL needed), parsers for the YTTV player overlay, recorded-data loaders and a JSONL writer.

Overlay geometry (measured from real 1920x1080 screencaps taken 2026-11-05, ESPN, both sticks):
  progress bar   y ~ 880 px, x ~ 96 .. 1823 px (red = played, gray 76,76,76 = not yet aired)
  knob           white disc ~40 px wide centred on the playhead
  scrub label    'MM:SS' (elapsed since program start) above the knob when the bar has focus
  LIVE badge     at (0.05, 0.62) normalized, next to 'Nov 5, 2:30 AM - SportsCenter'
  program window '2:30 AM - 3:00 AM' at (0.81, 0.72)
  bottom row     (0.07..0.85, 0.89): [more] HD CC [check] Networks Multiview Episodes 'More to watch'

SAFETY: keys/tunes only ever go to a stick that the app reports as NOT on screen. If the app state is
unreadable or ambiguous, both sticks count as visible and the test runs read-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
_STICKS = (__import__("os").environ.get("RZ_STICKS") or "hdmi1:5555,hdmi2:5555").split(",")  # stick addresses stay off the public repo: set RZ_STICKS=host1,host2 on the Mac

REPO = Path(__import__("os").environ.get("RZ_REPO") or "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone")
def _sync_dir():
    """RZ_SYNC, else Nick's Nov 4 scratchpad if it still exists (reference PNGs, codec logs), else this bank's parent."""
    import os as _os
    env = _os.environ.get("RZ_SYNC")
    if env:
        return env
    old = "/private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/sync"
    here = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
    return old if _os.path.isdir(old) else here


SYNC = Path(_sync_dir())
TESTS = Path(__file__).resolve().parent.parent
RESULTS = TESTS / "results"
WORK = TESTS / "work"
RECIPES = TESTS / "ui" / "recipes.json"
for p in (str(REPO), str(SYNC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from t1_retune import PKG, read as _ms_read, sh as _sh, tune as _tune  # noqa: E402

HOSTS = {"HDMI1": _STICKS[0], "HDMI2": _STICKS[1]}
CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
         "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
         "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
         "btn": "1D2LKf_AW5w"}
STATE_URL = "http://localhost:8778/api/state"
MIN_POLL_S = 0.5

# Recorded fixtures (real screencaps from 2026-11-05; file mtime = capture time).
FIX = {
    "overlay": SYNC / "cc_ui.png",          # overlay up: LIVE badge, headline, program window, bar + knob
    "scrub": SYNC / "a3_cc_focus.png",      # bar focused: scrub label '23:36' above the knob
    "scrub2": SYNC / "f1.png",              # bar focused: '25:06'
    "guide": SYNC / "a3_ui.png",            # overlay + 'More to watch' row with LIVE tiles
    "blank": SYNC / "f2.png",               # black video, no UI
    "cc": SYNC / "cc_3.png",                # after CC toggle (no caption text visible)
}
DRY_DATE = "2026-11-05"


# ----------------------------------------------------------------------------------------- args / out
def parser(test_id: str, doc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=test_id, description=(doc or "").split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=doc)
    ap.add_argument("--stick", default="hidden",
                    help="hidden (default) | HDMI1 | HDMI2 | 150 | 181 | host:port")
    ap.add_argument("--channel", default="espn", choices=sorted(CODES))
    ap.add_argument("--game", default=None, help="ESPN game id (plays matched against it)")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true", help="offline: recorded files only, no adb")
    ap.add_argument("--date", default=None, help="recordings date (default: today live, %s dry)" % DRY_DATE)
    ap.add_argument("--out", default=str(RESULTS / f"{test_id}.jsonl"))
    return ap


class Out:
    def __init__(self, test_id: str, args):
        self.id, self.args = test_id, args
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        self.f = open(args.out, "a")
        self.n = 0

    def write(self, kind: str, **kw):
        row = dict(t=round(time.time(), 3), test=self.id, kind=kind, dry=bool(self.args.dry_run),
                   stick=getattr(self.args, "host", None), channel=self.args.channel, game=self.args.game)
        row.update(kw)
        line = json.dumps(row, default=str)
        self.f.write(line + "\n"); self.f.flush(); self.n += 1
        print(line, flush=True)

    def close(self):
        self.f.close()


def say(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------ safety
def _find_on_screen(state) -> set[str] | None:
    """Hosts the app says are on screen. None = can't tell (caller must fail safe)."""
    try:
        ag = (state.get("health") or {}).get("agent") or {}
        v = ag.get("on_screen")
        if isinstance(v, str) and v:
            for h in HOSTS.values():
                if v == h or v.split(":")[0] == h.split(":")[0]:
                    return {h}
            if v.upper() in HOSTS:
                return {HOSTS[v.upper()]}
        # other field spellings: look for any 'visible'/'on_screen'/'active_stick' scalar naming a stick
        stack = [state]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                for k, val in o.items():
                    if k == "games":
                        continue
                    kl = k.lower()
                    if isinstance(val, str) and any(s in kl for s in ("on_screen", "visible", "active_stick",
                                                                        "screen_stick", "current_input")):
                        for name, h in HOSTS.items():
                            if val == h or val.upper() == name or h.split(":")[0] in val:
                                return {h}
                    elif isinstance(val, (dict, list)):
                        stack.append(val)
            elif isinstance(o, list):
                stack.extend(o[:20])
    except Exception:
        return None
    return None


def visible_hosts() -> set[str]:
    try:
        with urllib.request.urlopen(STATE_URL, timeout=3) as r:
            st = json.load(r)
    except Exception:
        return set(HOSTS.values())
    v = _find_on_screen(st)
    return v if v else set(HOSTS.values())


def resolve_stick(arg: str, dry: bool) -> tuple[str | None, bool]:
    """(host, may_press). 'hidden' -> the stick not on screen (None if unknown)."""
    if arg == "both":  # read-only multi-stick tests
        return HOSTS["HDMI2"], False
    if dry:
        a = (arg or "hidden")
        if a in ("hidden", "visible"):
            return HOSTS["HDMI2"], False
        return _host(a), False
    vis = visible_hosts()
    if arg in (None, "", "hidden"):
        hidden = [h for h in HOSTS.values() if h not in vis]
        if len(hidden) != 1:
            say("on-screen stick unknown: read-only on HDMI2")
            return HOSTS["HDMI2"], False
        return hidden[0], True
    if arg == "visible":
        h = sorted(vis)[0]
        return h, False
    h = _host(arg)
    return h, h not in vis


def _host(a: str) -> str:
    a = str(a)
    if a.upper() in HOSTS:
        return HOSTS[a.upper()]
    for h in HOSTS.values():
        if a == h or h.split(":")[0].endswith(a):
            return h
    raise SystemExit(f"unknown stick {a!r}")


class Guard:
    """The only way a test touches a stick's state. Re-checks visibility before every press/tune."""

    def __init__(self, host: str, may_press: bool, dry: bool):
        self.host, self.may, self.dry = host, may_press, dry
        self.log: list[tuple[float, str]] = []

    def ok(self) -> bool:
        if self.dry or not self.may:
            return False
        if self.host in visible_hosts():
            say(f"{self.host} is ON SCREEN now: refusing input")
            self.may = False
            return False
        return True

    def key(self, *codes: str, gap: float = 0.7) -> bool:
        for c in codes:
            c = c if c.startswith("KEYCODE_") else "KEYCODE_" + c
            if self.dry or not self.may:
                say(f"WOULD press {c} on {self.host}")
                self.log.append((time.time(), "would:" + c))
                continue
            if not self.ok():
                return False
            _sh(self.host, f"input keyevent {c}")
            self.log.append((time.time(), c))
            time.sleep(max(gap, MIN_POLL_S))
        return self.may and not self.dry

    def tune(self, ch: str):
        if self.dry or not self.ok():
            say(f"WOULD tune {self.host} -> {ch}")
            return None
        return tune_any(self.host, ch)


def tune_any(host: str, ch: str):
    import t1_retune
    t1_retune.CODES.update(CODES)
    return _tune(host, ch)


# ------------------------------------------------------------------------------------- stick reads
def ms(host: str):
    """media_session read: {st, sub, title, upd, E} (E = wall - extrapolated position s)."""
    try:
        return _ms_read(host)
    except Exception as e:  # noqa: BLE001
        return {"err": repr(e)}


def ms_raw(host: str) -> dict:
    """position (s) right now plus the read's wall midpoint."""
    from redzone.tuners.adb import parse_playback
    try:
        t0 = time.time(); out = _sh(host, "cat /proc/uptime; dumpsys media_session"); t1 = time.time()
        up = float(out.split()[0]) * 1000
        b = parse_playback(out, PKG)
    except Exception:  # noqa: BLE001
        return {}
    if b.get("position") is None:
        return {}
    sp = b.get("speed", 1.0) if b["state"] == "playing" else 0.0
    pos = (b["position"] + (up - b["updated"]) * sp) / 1000
    return dict(st=b["state"], pos=pos, wall=(t0 + t1) / 2, E=(t0 + t1) / 2 - pos,
                title=b.get("title", ""), sub=b.get("subtitle", ""))


def screencap(host: str, path: Path) -> float:
    """PNG of the UI layer (video is black). Returns capture wall (midpoint)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(path, "wb") as f:
        subprocess.run(["adb", "-s", host, "exec-out", "screencap", "-p"], stdout=f, timeout=15)
    return (t0 + time.time()) / 2


class FixtureMissing(FileNotFoundError):
    """A dry run's reference PNG (Mac-only, kept off the public repo) is not on this machine."""


def fixture(name: str) -> tuple[Path, float]:
    p = FIX[name]
    if not p.exists():
        raise FixtureMissing(f"reference PNG {p.name} not in RZ_SYNC={SYNC} (Mac-only; live runs don't need it)")
    return p, p.stat().st_mtime


# ------------------------------------------------------------------------------------------- OCR
def ocr(path: Path) -> dict:
    from redzone.screen_ocr import recognize
    r = recognize(str(path))
    obs = [dict(text=o.text, conf=round(o.confidence, 2), x=o.x, y=o.y, w=o.width, h=o.height)
           for o in r.observations]
    foc = [dict(x=f.x, y=f.y, w=f.width, h=f.height, text=f.text) for f in r.focus_boxes]
    status = r.focus_status
    if not foc and r.status in ("ok", "empty"):
        # The app's parser keeps a filled-button focus only when its text is a playback action
        # (Resume, Watch key plays...). The YTTV player row (HD, CC, Networks, Multiview, Stats...)
        # is all filled buttons, so ui_10 saw no focus (Nov 5 night). Re-read the helper's raw
        # candidates here, test-only; the app's own gate is unchanged.
        foc, status = bank_focus(path, obs)
    return dict(status=r.status, W=r.width or 1920, H=r.height or 1080, obs=obs, focus=foc,
                focus_status=status)


def pick_focus(cands: list[dict], obs: list[dict]) -> tuple[list[dict], str]:
    """From raw helper focus candidates, the one focused element: a rectangle border wins (YTTV draws it
    around the focused control); else the single most confident filled button. Text = OCR rows inside it."""
    out = []
    for c in cands or []:
        try:
            x, y, w, h, cf, src = (float(c["x"]), float(c["y"]), float(c["width"]), float(c["height"]),
                                   float(c["confidence"]), c["source"])
        except (KeyError, TypeError, ValueError):
            continue
        floor = 0.9 if src == "neutral_rectangle_border" else 0.68 if src == "neutral_filled_button" else None
        if floor is None or cf < floor or not (0 < w <= 1 and 0 < h <= 1):
            continue
        txt = "\n".join(t["text"] for t in obs if t["x"] >= x - 0.005 and t["y"] >= y - 0.005
                        and t["x"] + t["w"] <= x + w + 0.005 and t["y"] + t["h"] <= y + h + 0.005)
        txt = "\n".join(ln for ln in txt.splitlines() if ln.strip() not in {"•", "▶", "►", "▸", "●", ">"})
        out.append(dict(x=x, y=y, w=w, h=h, text=txt, conf=cf, src=src))
    if not out:
        return [], "none"
    rect = [f for f in out if f["src"] == "neutral_rectangle_border"]
    pool = rect or out
    best = max(pool, key=lambda f: f["conf"])
    tie = [f for f in pool if f is not best and f["conf"] >= best["conf"] - 0.02]
    return [best], ("bank_unique" if not tie else "bank_best_of_%d" % (len(tie) + 1))


def bank_focus(path: Path, obs: list[dict]) -> tuple[list[dict], str]:
    """Raw focus candidates from the same Vision helper the app uses (macOS only)."""
    import json as _json
    import platform
    if platform.system() != "Darwin":
        return [], "none"
    try:
        from redzone.screen_ocr import _helper
        proc = subprocess.run([str(_helper(45.0)), str(path)], capture_output=True, text=True, timeout=15)
        cands = _json.loads(proc.stdout).get("focus_candidates", [])
    except Exception:  # noqa: BLE001
        return [], "none"
    return pick_focus(cands, obs)


# ------------------------------------------------------------------------------------------ pixels
class Pixels:
    """RGB reads from a PNG via `sips -s format bmp` (macOS built-in, ~0.2 s)."""

    def __init__(self, png: Path):
        fd, tmp = tempfile.mkstemp(suffix=".bmp"); os.close(fd)
        subprocess.run(["sips", "-s", "format", "bmp", str(png), "--out", tmp],
                       capture_output=True, timeout=20)
        b = Path(tmp).read_bytes(); os.unlink(tmp)
        self.off, = struct.unpack_from("<I", b, 10)
        w, h, _, bpp = struct.unpack_from("<iiHH", b, 18)
        self.W, self.H, self.topdown, self.bpp = w, abs(h), h < 0, bpp // 8
        self.b = b

    def px(self, x: int, y: int) -> tuple[int, int, int]:
        yy = y if self.topdown else self.H - 1 - y
        stride = (self.W * self.bpp + 3) // 4 * 4
        i = self.off + yy * stride + x * self.bpp
        B, G, R = self.b[i], self.b[i + 1], self.b[i + 2]
        return R, G, B


def classify(rgb) -> str:
    r, g, b = rgb
    if r > 220 and g > 220 and b > 220:
        return "white"
    if r > 180 and g < 90:
        return "red" if b < 80 else "pink"
    if abs(r - g) < 14 and abs(g - b) < 14:
        if r < 40:
            return "black"
        if r < 100:
            return "gray_dark"
        if r < 170:
            return "gray_mid"
        return "gray_light"
    return "other"


def runs(labels: list[str], x0: int = 0) -> list[list]:
    out = []
    for i, c in enumerate(labels):
        if out and out[-1][0] == c:
            out[-1][2] = x0 + i
        else:
            out.append([c, x0 + i, x0 + i])
    return out


def find_bar(pix: Pixels, y_from: float = 0.70, y_to: float = 0.92) -> dict | None:
    """The progress bar: the row with the longest red+gray span; knob = white run on it."""
    best = None
    for y in range(int(pix.H * y_from), int(pix.H * y_to), 2):
        labs = [classify(pix.px(x, y)) for x in range(0, pix.W, 2)]
        bar = [i for i, c in enumerate(labs) if c in ("red", "pink", "gray_dark", "gray_mid", "white")]
        red = sum(1 for c in labs if c in ("red", "pink"))
        if red < 10 or len(bar) < pix.W * 0.25:
            continue
        # the player bar is one continuous span inset from both edges (guide tiles are not)
        span = bar[-1] - bar[0] + 1
        if len(bar) < 0.9 * span or bar[0] * 2 < pix.W * 0.02 or bar[-1] * 2 > pix.W * 0.97:
            continue
        if best is None or len(bar) > best[1]:
            best = (y, len(bar))
    if not best:
        return None
    y = best[0]
    labs = [classify(pix.px(x, y)) for x in range(pix.W)]
    rr = [r for r in runs(labs) if r[0] != "black" and r[2] - r[1] >= 2]
    bar_runs = [r for r in rr if r[0] in ("red", "pink", "gray_dark", "gray_mid", "gray_light", "white")]
    if not bar_runs:
        return None
    x0, x1 = bar_runs[0][1], bar_runs[-1][2]
    whites = [r for r in bar_runs if r[0] == "white" and 15 <= r[2] - r[1] <= 80]
    knob = (whites[0][1] + whites[0][2]) / 2 if whites else None
    if knob is None:  # knob hidden: playhead = end of red
        reds = [r for r in bar_runs if r[0] in ("red", "pink")]
        knob = reds[-1][2] if reds else None
    # bar ends hide under the knob's rounded caps; widen by the cap radius
    return dict(y=y, x0=x0, x1=x1, knob_x=knob, frac=(knob - x0) / max(1, x1 - x0) if knob else None,
                runs=[r for r in bar_runs if r[2] - r[1] >= 3])


# ----------------------------------------------------------------------------------- overlay parse
_WIN = re.compile(r"(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*[-–—~]\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])")
_HEAD = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{1,2}:\d{2})\s*([AP]M)\s*[•·.]?\s*(.*)")
_MMSS = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                     "Nov", "Dec"], 1)}


def clock_to_epoch(hm: str, ampm: str, near: float) -> float:
    h, m = map(int, hm.split(":"))
    h = h % 12 + (12 if ampm.upper() == "PM" else 0)
    base = dt.datetime.fromtimestamp(near)
    cands = []
    for dd in (-1, 0, 1):
        d = (base + dt.timedelta(days=dd)).replace(hour=h, minute=m, second=0, microsecond=0)
        cands.append(d.timestamp())
    return min(cands, key=lambda e: abs(e - near))


def mmss(text: str) -> float | None:
    m = _MMSS.match(text.strip())
    if not m:
        return None
    hh, mm, ss = m.groups()
    return (int(hh) * 3600 if hh else 0) + int(mm) * 60 + int(ss)


def parse_overlay(o: dict, cap_wall: float) -> dict:
    """Read the YTTV player overlay from OCR observations (normalized top-left coordinates)."""
    res = dict(overlay=False, live_badge=None, headline=None, prog_start=None, prog_end=None,
               scrub_label=None, scrub_s=None, scrub_x=None, buttons=[])
    texts = o["obs"]
    for t in texts:
        s = t["text"].strip()
        m = _WIN.search(s)
        if m and t["y"] > 0.2:
            a = clock_to_epoch(m.group(1), m.group(2), cap_wall)
            b = clock_to_epoch(m.group(3), m.group(4), a + 3600)
            if b <= a:
                b += 86400
            res.update(prog_start=a, prog_end=b, overlay=True)
        mh = _HEAD.search(s)
        if mh and t["y"] > 0.4:
            res["headline"] = s
            if res["prog_start"] is None:
                res["prog_start"] = clock_to_epoch(mh.group(3), mh.group(4), cap_wall)
            res["overlay"] = True
        if s.upper() in ("LIVE", "LIVI", "LTVE") and t["x"] < 0.12 and 0.5 < t["y"] < 0.7:
            res["live_badge"] = dict(x=t["x"], y=t["y"], w=t["w"], h=t["h"])
        sec = mmss(s)
        if sec is not None and t["y"] < 0.8 and not _WIN.search(s):
            res.update(scrub_label=s, scrub_s=sec, scrub_x=t["x"] + t["w"] / 2, overlay=True)
        if t["y"] > 0.85:
            for k in ("Networks", "Multiview", "Episodes", "More to watch", "Stats", "Key plays", "Key Plays",
                      "Scores", "Live chat", "Info", "HD", "cc", "CC"):
                if k.lower() in s.lower():
                    res["buttons"].append(k)
                    res["overlay"] = True
    return res


def badge_color(pix: Pixels, box: dict) -> dict:
    """Dominant non-white colour inside the LIVE badge box: red = at live edge (YouTube style), gray = behind."""
    x0, y0 = int(box["x"] * pix.W) - 6, int(box["y"] * pix.H) - 4
    x1, y1 = int((box["x"] + box["w"]) * pix.W) + 6, int((box["y"] + box["h"]) * pix.H) + 4
    cnt: dict[str, int] = {}
    rgbs = []
    for y in range(max(0, y0), min(pix.H, y1), 2):
        for x in range(max(0, x0), min(pix.W, x1), 2):
            p = pix.px(x, y); c = classify(p)
            if c not in ("white", "black"):
                cnt[c] = cnt.get(c, 0) + 1; rgbs.append(p)
    dom = max(cnt, key=cnt.get) if cnt else "none"
    avg = tuple(round(sum(v[i] for v in rgbs) / len(rgbs)) for i in range(3)) if rgbs else None
    return dict(dominant=dom, counts=cnt, avg_rgb=avg)


# ------------------------------------------------------------------------------------- focus / nav
def focused_text(o: dict) -> str:
    """Text inside (or nearest) the focus rectangle the OCR helper found."""
    if not o.get("focus"):
        return ""
    f = o["focus"][0]
    if f.get("text"):
        return f["text"]
    cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
    best = None
    for t in o["obs"]:
        tx, ty = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
        d = abs(tx - cx) + abs(ty - cy)
        if best is None or d < best[0]:
            best = (d, t["text"])
    return best[1] if best and best[0] < 0.15 else ""


def snap(g: Guard, tag: str) -> tuple[Path, float, dict]:
    p = WORK / f"{tag}_{int(time.time() * 1000)}.png"
    w = screencap(g.host, p)
    return p, w, ocr(p)


def navigate_to(g: Guard, pattern: str, row_key: str = "DPAD_RIGHT", max_steps: int = 10,
                reveal: tuple[str, ...] = ("DPAD_DOWN",)) -> dict:
    """Reveal the overlay, then step focus along a row until the focused label matches `pattern`.
    Never presses CENTER. Returns {found, steps, keys, text}."""
    rx = re.compile(pattern, re.I)
    keys = []
    if not g.key(*reveal):
        return dict(found=False, why="no input allowed", keys=list(reveal))
    keys += reveal
    for i in range(max_steps + 1):
        _, _, o = snap(g, "nav")
        ft = focused_text(o)
        if ft and rx.search(ft):
            return dict(found=True, steps=i, keys=keys, text=ft)
        if i < max_steps:
            g.key(row_key); keys.append(row_key)
    return dict(found=False, steps=max_steps, keys=keys, text="")


def load_recipes() -> dict:
    try:
        return json.loads(RECIPES.read_text())
    except Exception:
        return {}


def save_recipe(name: str, rec: dict):
    r = load_recipes(); r[name] = dict(rec, saved=time.time())
    RECIPES.write_text(json.dumps(r, indent=1))


# ------------------------------------------------------------------------------------ recorded data
def rec_dir(date: str | None, dry: bool) -> Path:
    d = date or (DRY_DATE if dry else dt.date.today().isoformat())
    return REPO / "data" / "recordings" / d


def load_jsonl(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    with open(p) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def load_plays(date, dry, game=None, league=None) -> list[dict]:
    rows = load_jsonl(rec_dir(date, dry) / "plays.jsonl")
    seen, out = set(), []
    for r in rows:
        if game and str(r.get("game")) != str(game):
            continue
        if league and r.get("league") != league:
            continue
        if r.get("wall") is None:
            continue
        k = (r.get("game"), r.get("play"))
        if k in seen:
            continue
        seen.add(k); out.append(r)
    out.sort(key=lambda r: r["wall"])
    return out


def busiest_game(date, dry, league="nba") -> str | None:
    rows = load_plays(date, dry, league=league)
    cnt: dict[str, int] = {}
    for r in rows:
        cnt[r["game"]] = cnt.get(r["game"], 0) + 1
    return max(cnt, key=cnt.get) if cnt else None


def load_probe(date, dry, host=None) -> list[dict]:
    rows = load_jsonl(rec_dir(date, dry) / "sync_probe.jsonl")
    return [r for r in rows if host is None or r.get("host") == host]


def probe_Erel(row: dict) -> float | None:
    """E minus the stick's boot epoch for a sync_probe row: E = boot + (updated - position)/1000.
    (Rows don't carry /proc/uptime, so only this boot-relative E is recoverable; it is constant while
    the stream plays in real time, exactly like E.)"""
    m = row.get("ms") or {}
    if m.get("position") is None or m.get("updated") is None:
        return None
    return (m["updated"] - m["position"]) / 1000


def espn_summary(game: str, league: str = "nba") -> dict | None:
    """ESPN summary (free, public): plays with clock, period, scores and wallclock."""
    sport = {"nba": "basketball/nba", "mbb": "basketball/mens-college-basketball",
             "wcbb": "basketball/womens-college-basketball", "nfl": "football/nfl",
             "cfb": "football/college-football", "nhl": "hockey/nhl"}.get(league, "basketball/nba")
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/summary?event={game}"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.load(r)
    except Exception:
        return None


def summary_plays(js: dict | None) -> list[dict]:
    out = []
    for p in (js or {}).get("plays") or []:
        w = p.get("wallclock")
        try:
            wall = dt.datetime.fromisoformat(w.replace("Z", "+00:00")).timestamp() if w else None
        except Exception:
            wall = None
        out.append(dict(wall=wall, text=p.get("text", ""), type=(p.get("type") or {}).get("text", ""),
                        clock=(p.get("clock") or {}).get("displayValue"),
                        period=(p.get("period") or {}).get("number"),
                        home=p.get("homeScore"), away=p.get("awayScore"),
                        scoring=bool(p.get("scoringPlay"))))
    return [p for p in out if p["wall"]]


# ------------------------------------------------------------------------------------------- stats
def summarize(xs) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return dict(n=0)
    med = statistics.median(xs)
    return dict(n=len(xs), median=round(med, 2), mean=round(statistics.fmean(xs), 2),
                sd=round(statistics.pstdev(xs), 2) if len(xs) > 1 else 0.0,
                mad=round(statistics.median(abs(x - med) for x in xs), 2),
                lo=round(min(xs), 2), hi=round(max(xs), 2))


def sleep_poll(s: float):
    time.sleep(max(MIN_POLL_S, s))


# ------------------------------------------------------------------------------------------ runner
def run(test_id: str, doc: str, live, dry, extra=None) -> int:
    """Parse args, resolve the stick safely, run live(args, guard, out) or dry(args, out), write summary."""
    ap = parser(test_id, doc)
    if extra:
        extra(ap)
    args = ap.parse_args()
    host, may = resolve_stick(args.stick, args.dry_run)
    args.host = host
    if args.dry_run and not args.game:
        args.game = busiest_game(args.date, True)
    g = Guard(host, may, args.dry_run)
    out = Out(test_id, args)
    WORK.mkdir(parents=True, exist_ok=True)
    out.write("start", may_press=may, argv=sys.argv[1:])
    try:
        summ = dry(args, out) if args.dry_run else live(args, g, out)
        out.write("summary", **(summ or {}))
    except FixtureMissing as e:
        # dry run only (live never reads fixtures): report SKIP honestly instead of a crash, exit 0
        out.write("summary", skipped="fixture_missing", why=str(e), pass_=None)
        print(f"[{test_id}] SKIP dry fixture check: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        out.write("aborted", why="ctrl-c")
    finally:
        out.close()
    return 0
