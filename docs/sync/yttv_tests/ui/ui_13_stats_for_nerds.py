#!/usr/bin/env python3
"""ui_13 YTTV TV app 'Stats for nerds' overlay on the hidden stick: does it show live latency or ingest time?

HYPOTHESIS (overseer 7:35 AM Nov 5): the web player exposes the frame's ingest epoch
(getMediaReferenceTime, ui_12). The TV app's 'Stats for nerds' overlay may print the same thing (a live
latency, a 'Date' / ingest time, buffer health). The overlay is UI, so screencap shows it even while the
video layer is DRM-blanked. We read text only (OCR numbers); no frame is kept beyond the OCR call.

PROCEDURE (hidden stick only, every press re-checks that the stick is still hidden). Menu layout from the
overseer's manual test (8:10 AM Nov 5): the player's ⋮ menu shows tiles 'Audio options | Auto-pause |
Broadcast delay (<value>) | Send feedback | Stats for nerds (Off)'. On the SCRUBBER, LEFT/RIGHT SEEK, so this
test never presses LEFT/RIGHT while the focus is on the scrubber (a focused time label like '7:59 AM' or
'23:36', or no focused label at all):
  0. 'Who's watching?' on screen (the app was closed): step RIGHT along the profile row to the tile that
     reads Nick and CENTER it; no such tile in 6 steps -> stop.
  1. tune --channel; DPAD_DOWN to reveal the overlay; while the focus is on the scrubber (or unknown),
     DPAD_DOWN again (at most 3); then step RIGHT along the button row only while the focused label is a
     known button, until it matches the ⋮ / more / options button; CENTER.
  2. menu open: OCR the tiles and log the Broadcast delay value (READ ONLY: never CENTER on it; Nick
     hasn't decided whether to change it). Step along the tiles (RIGHT) to 'Stats for nerds'; CENTER.
  3. every --every s: screencap + OCR; keep every line matching /latency|ingest|live|buffer|date|time|
     health|res|codec|bandwidth/; parse 'Live Latency 12.34s', 'Buffer Health 30.1 s', and any epoch or
     clock time. Each frame logs the stick's E (media_session, same Mac clock) beside it, so fusion_13 can
     compare an overlay latency with the web reference.
  4. turn Stats for nerds back off the same way (menu -> tile -> CENTER), BACK until the overlay is
     gone, re-tune --channel (lands on the live edge again).
Run it once per stick (each time on the hidden one) to read both sticks' Broadcast delay values.

PASS (informational): the overlay opens and at least 10 frames carry a latency or ingest number; it
reports shows_latency / shows_ingest and the latency's sd. FAIL = overlay not found (logs why).
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_13_stats_for_nerds"
KEEP = re.compile(r"latency|ingest|live|buffer|date|time|health|res|codec|bandwidth|dropped", re.I)
LAT = re.compile(r"(?:live\s*)?latency[^0-9-]*(-?\d+(?:\.\d+)?)\s*(ms|s)?", re.I)
BUF = re.compile(r"buffer\s*health[^0-9]*(\d+(?:\.\d+)?)\s*(ms|s)?", re.I)
EPOCH = re.compile(r"\b(1[6-9]\d{8}(?:\.\d+)?|1[6-9]\d{11})\b")
HMS = re.compile(r"\b(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?\b")


def extra(ap):
    ap.add_argument("--every", type=float, default=1.0)


def _secs(v: str, unit: str | None) -> float:
    x = float(v)
    return x / 1000.0 if (unit or "").lower() == "ms" else x


def parse_nerds(o: dict) -> dict:
    lines = [t["text"].strip() for t in o.get("obs", []) if KEEP.search(t["text"] or "")]
    res = dict(lines=lines[:20], latency=None, buffer=None, epoch=None, hms=None)
    for s in lines:
        m = LAT.search(s)
        if m and res["latency"] is None:
            res["latency"] = _secs(m.group(1), m.group(2))
        m = BUF.search(s)
        if m and res["buffer"] is None:
            res["buffer"] = _secs(m.group(1), m.group(2))
        m = EPOCH.search(s)
        if m and res["epoch"] is None:
            v = float(m.group(1))
            res["epoch"] = v / 1000.0 if v > 1e11 else v
        m = HMS.search(s)
        if m and res["hms"] is None and re.search(r"date|ingest|time", s, re.I):
            res["hms"] = m.group(0)
    return res


SCRUB = re.compile(r"^\s*(\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?)\s*$")
BUTTONS = re.compile(r"\u22ee|\u2026|more|options|settings|hd|cc|networks|multiview|episodes|stats|key plays|"
                     r"scores|live chat|info|captions", re.I)
MORE = re.compile(r"\u22ee|\u2026|more options|^more$|options|settings", re.I)
BDELAY = re.compile(r"broadcast\s*delay\W*(\w[\w ]{0,20})?", re.I)


def _close(g):
    g.key("BACK", "BACK", "BACK")


def _focus(g, tag):
    _, _, o = U.snap(g, tag)
    return U.focused_text(o).strip(), o


def _on_scrubber(ft: str) -> bool:
    return not ft or bool(SCRUB.match(ft))


def _profile(g, out) -> bool:
    """'Who's watching?' -> Nick's tile. True = no picker, or Nick picked."""
    ft, o = _focus(g, "profile")
    if not any(re.search(r"who.?s watching", t["text"], re.I) for t in o["obs"]):
        return True
    for i in range(7):
        if re.search(r"\bnick\b", ft, re.I):
            g.key("DPAD_CENTER"); time.sleep(4); out.write("profile", picked=ft, steps=i)
            return True
        if i < 6:
            g.key("DPAD_RIGHT"); ft, _ = _focus(g, "profile")
    out.write("profile", picked=None, why="no Nick tile in 6 steps")
    return False


def _open_menu(g, out) -> dict | None:
    """Reveal the overlay, get off the scrubber, step the button row to the more/options button, CENTER."""
    g.key("DPAD_DOWN"); time.sleep(1)
    ft, _ = _focus(g, "nav")
    keys = ["DPAD_DOWN"]
    for _ in range(3):
        if not _on_scrubber(ft):
            break
        g.key("DPAD_DOWN"); keys.append("DPAD_DOWN"); ft, _ = _focus(g, "nav")
    for i in range(10):
        if MORE.search(ft):
            g.key("DPAD_CENTER"); time.sleep(1.5)
            return dict(keys=keys + ["DPAD_CENTER"], label=ft)
        if _on_scrubber(ft) or not BUTTONS.search(ft):
            out.write("nav_stop", focused=ft, why="focus not on a known button: LEFT/RIGHT could seek")
            return None
        g.key("DPAD_RIGHT"); keys.append("DPAD_RIGHT"); ft, _ = _focus(g, "nav")
    return None


def _menu_tile(g, out, pattern) -> tuple[bool, dict]:
    """In the open menu: log every tile (Broadcast delay value, read only), step RIGHT to `pattern`, CENTER."""
    rx = re.compile(pattern, re.I)
    ft, o = _focus(g, "menu")
    texts = [t["text"] for t in o["obs"]]
    bd = next((m.group(0) for t in texts for m in [BDELAY.search(t)] if m), None)
    out.write("menu", tiles=texts[:20], broadcast_delay=bd)
    for i in range(8):
        if rx.search(ft) and not re.search(r"broadcast|delay", ft, re.I):
            # never CENTER while the focus text mentions Broadcast delay (Nick hasn't decided to change it)
            g.key("DPAD_CENTER"); time.sleep(2)
            return True, dict(broadcast_delay=bd, steps=i)
        if re.search(r"broadcast", ft, re.I) is None and _on_scrubber(ft):
            break
        g.key("DPAD_RIGHT"); ft, _ = _focus(g, "menu")
    return False, dict(broadcast_delay=bd)


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    g.tune(args.channel); time.sleep(4)
    if not _profile(g, out):
        _close(g)
        return dict(overlay=False, why="profile picker without a Nick tile", pass_=False)
    nav = _open_menu(g, out)
    out.write("nav_more", found=bool(nav), **(nav or {}))
    if not nav:
        _close(g); g.tune(args.channel)
        return dict(overlay=False, why="no more/options button reached safely", pass_=False)
    ok, info = _menu_tile(g, out, r"stats\s*for\s*nerds")
    if not ok:
        _close(g); g.tune(args.channel)
        return dict(overlay=False, why="no 'Stats for nerds' tile", pass_=False, **info)
    U.save_recipe("stats_for_nerds", dict(keys=nav["keys"] + ["DPAD_RIGHT"] * info["steps"] + ["DPAD_CENTER"]))
    lat, frames, n_ing = [], 0, 0
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        if not g.ok():
            break
        p = U.WORK / f"{ID}.png"; w = U.screencap(g.host, p); o = U.ocr(p)
        nerd = parse_nerds(o); m = U.ms_raw(g.host)
        frames += 1
        if nerd["latency"] is not None:
            lat.append(nerd["latency"])
        if nerd["epoch"] is not None or nerd["hms"] is not None:
            n_ing += 1
        out.write("frame", wall=round(w, 3), E=round(m["E"], 3) if m.get("E") else None, **nerd)
        U.sleep_poll(args.every)
    if g.ok() and _open_menu(g, out):          # turn Stats for nerds back off
        _menu_tile(g, out, r"stats\s*for\s*nerds")
    _close(g); g.tune(args.channel)
    s = U.summarize(lat)
    okk = (s.get("n", 0) + n_ing) >= 10
    return dict(overlay=True, frames=frames, shows_latency=bool(lat), shows_ingest=n_ing > 0, latency=s,
                broadcast_delay=info.get("broadcast_delay"), pass_=bool(okk))


def dry(args, out):
    samples = [
        dict(obs=[dict(text="Live Latency 12.34s"), dict(text="Buffer Health 30.10 s"), dict(text="Codec avc1")]),
        dict(obs=[dict(text="Latency: 9876 ms"), dict(text="Date Thu Nov 05 2026 19:22:41")]),
        dict(obs=[dict(text="Ingest 1793000123.5"), dict(text="Resolution 1920x1080@60")]),
    ]
    got = [parse_nerds(o) for o in samples]
    for o, r in zip(samples, got):
        out.write("parse", text=[t["text"] for t in o["obs"]], **{k: v for k, v in r.items() if k != "lines"})
    scrub_ok = _on_scrubber("7:59 AM") and _on_scrubber("23:36") and _on_scrubber("") and not _on_scrubber("Stats for nerds")
    bd = BDELAY.search("Broadcast delay Moderate")
    out.write("nav_rules", scrubber_detect=scrub_ok, broadcast_delay=bd.group(1) if bd else None)
    ok = scrub_ok and bool(bd) and (got[0]["latency"] == 12.34 and got[0]["buffer"] == 30.1
                                    and abs(got[1]["latency"] - 9.876) < 1e-9
                                    and got[1]["hms"] == "19:22:41" and got[2]["epoch"] == 1793000123.5)
    out.write("would", steps=["tune", "profile picker -> Nick (if shown)", "DOWN (again while on the scrubber)",
                              "RIGHT only on known buttons until the more/options button", "CENTER",
                              "log Broadcast delay (read only)", "RIGHT to 'Stats for nerds'", "CENTER",
                              "poll screencap+OCR + E", "menu -> Stats for nerds off", "BACK x3", "re-tune"])
    return dict(parser_ok=bool(ok), pass_=bool(ok))


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
