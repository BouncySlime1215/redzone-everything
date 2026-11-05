#!/usr/bin/env python3
"""ui_13 YTTV TV app 'Stats for nerds' overlay on the hidden stick: does it show live latency or ingest time?

HYPOTHESIS (overseer 7:35 AM Nov 5): the web player exposes the frame's ingest epoch
(getMediaReferenceTime, ui_12). The TV app's 'Stats for nerds' overlay may print the same thing (a live
latency, a 'Date' / ingest time, buffer health). The overlay is UI, so screencap shows it even while the
video layer is DRM-blanked. We read text only (OCR numbers); no frame is kept beyond the OCR call.

PROCEDURE (hidden stick only, every press re-checks that the stick is still hidden):
  1. tune --channel; reveal the overlay (DPAD_DOWN); step along the button row until the focused label
     matches /more|options|settings|⋮|…/ (never CENTER on anything else); CENTER opens the menu.
  2. step DOWN until the focused item matches /stats for nerds/; CENTER. Not found in 12 steps: log the
     labels seen, BACK x3, re-tune, stop.
  3. every --every s: screencap + OCR; keep every line matching /latency|ingest|live|buffer|date|time|
     health|res|codec|bandwidth/; parse 'Live Latency 12.34s', 'Buffer Health 30.1 s', and any epoch or
     clock time. Each frame logs the stick's E (media_session, same Mac clock) beside it, so fusion_13 can
     compare an overlay latency with the web reference.
  4. BACK until the overlay is gone, re-tune --channel (lands on the live edge again).

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


def _close(g):
    g.key("BACK", "BACK", "BACK")


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    g.tune(args.channel); time.sleep(4)
    nav = U.navigate_to(g, r"more|options|settings|⋮|…")
    out.write("nav_more", **nav)
    if not nav["found"]:
        _close(g); g.tune(args.channel)
        return dict(overlay=False, why="no more/options button", pass_=False)
    g.key("DPAD_CENTER"); time.sleep(1.5)
    nav2 = U.navigate_to(g, r"stats\s*for\s*nerds", row_key="DPAD_DOWN", max_steps=12, reveal=())
    out.write("nav_nerds", **nav2)
    if not nav2["found"]:
        _close(g); g.tune(args.channel)
        return dict(overlay=False, why="no 'Stats for nerds' item", pass_=False)
    g.key("DPAD_CENTER"); time.sleep(2)
    U.save_recipe("stats_for_nerds", dict(keys=nav["keys"] + ["DPAD_CENTER"] + nav2["keys"] + ["DPAD_CENTER"]))
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
    _close(g); g.tune(args.channel)
    s = U.summarize(lat)
    ok = (s.get("n", 0) + n_ing) >= 10
    return dict(overlay=True, frames=frames, shows_latency=bool(lat), shows_ingest=n_ing > 0, latency=s,
                pass_=bool(ok))


def dry(args, out):
    samples = [
        dict(obs=[dict(text="Live Latency 12.34s"), dict(text="Buffer Health 30.10 s"), dict(text="Codec avc1")]),
        dict(obs=[dict(text="Latency: 9876 ms"), dict(text="Date Thu Nov 05 2026 19:22:41")]),
        dict(obs=[dict(text="Ingest 1793000123.5"), dict(text="Resolution 1920x1080@60")]),
    ]
    got = [parse_nerds(o) for o in samples]
    for o, r in zip(samples, got):
        out.write("parse", text=[t["text"] for t in o["obs"]], **{k: v for k, v in r.items() if k != "lines"})
    ok = (got[0]["latency"] == 12.34 and got[0]["buffer"] == 30.1 and abs(got[1]["latency"] - 9.876) < 1e-9
          and got[1]["hms"] == "19:22:41" and got[2]["epoch"] == 1793000123.5)
    out.write("would", steps=["tune", "DPAD_DOWN", "RIGHT.. until /more|options/", "CENTER",
                              "DOWN.. until /stats for nerds/", "CENTER", "poll screencap+OCR + E",
                              "BACK x3", "re-tune"])
    return dict(parser_ok=bool(ok), pass_=bool(ok))


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
