#!/usr/bin/env python3
"""mechanics_09 ui_probe: read YTTV's own player overlay (LIVE badge, scrub time, program times, any
broadcast-delay / latency setting) and pair it with media_session position at the same instant.

Hypothesis: the overlay (not FLAG_SECURE; video layer is black in screencap) shows (a) 'LIVE' when at the
live edge, (b) a scrub/elapsed label like '25:06' or '-0:12' behind live, (c) program slot '7:00 PM - 9:30 PM'.
Pairing (scrub label, pos_now, wall) per capture tells whether position maps to program time (an anchor for D)
and whether YTTV exposes a delay/latency option (expected: none).

Procedure:
  read-only (either stick, default both): screencap -p -> OCR (Apple Vision, local) -> parse; plus one
    media_session read bracketing the capture.
  --nav (HIDDEN stick only, guarded): press DPAD_DOWN (20) to raise the player controls, capture at
    +0.7 s / +1.8 s / +3.5 s, then optional --settings-url deep link capture, then re-tune --channel to live.
  Keywords searched: delay, latency, broadcast, live edge, go live, jump to live, settings, stats.
Dry run: OCR the captures already saved in sync/*.png (up to --ocr-limit) and the recorded sync_probe
'ui' texts; print what each live capture would parse.

Pass rule: >= 1 capture with OCR ok AND a parsed LIVE badge or time label; report delay_setting_found
(True/False) as the finding (either answer is useful).
"""
import glob
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_09_ui_probe"
KEYS = ("delay", "latency", "broadcast", "live edge", "go live", "jump to live", "settings", "stats for nerds",
        "behind live", "catch up")
SCRUB = re.compile(r"(?<![\d:])(-?\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])")
SLOT = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AP]M)")


def parse_text(obs) -> dict:
    texts = [o.text for o in obs]
    full = " | ".join(texts)
    slots = SLOT.findall(full)
    scrubs = [m for t in texts for m in SCRUB.findall(t) if not re.search(r"[AP]M", t)]
    live = [o for o in obs if o.text.strip().upper() in ("LIVE", "LIVI")]
    return dict(n_text=len(texts), live_badges=len(live),
                live_badge_xy=[(round(o.x, 2), round(o.y, 2)) for o in live][:3], slots=slots[:4],
                scrub_labels=scrubs[:6], keywords=[k for k in KEYS if k in full.lower()], text=full[:300])


def ocr(path):
    from redzone.screen_ocr import recognize
    try:
        r = recognize(path)
    except Exception as e:
        return None, dict(err=repr(e)[:120])
    return r, (parse_text(r.observations) if r.read_ok else dict(status=r.status, err=r.error[:120]))


def judge(out, caps):
    ok_caps = [c for c in caps if c.get("live_badges") or c.get("scrub_labels") or c.get("slots")]
    found = any(any(k in ("delay", "latency", "broadcast", "behind live") for k in c.get("keywords", []))
                for c in caps)
    out.verdict(bool(ok_caps) if caps else None, ">=1 OCR-ok capture with LIVE badge or time label",
                readable=len(ok_caps), captures=len(caps), delay_setting_found=found)


def dry(args, out):
    caps = []
    pngs = [p for p in sorted(glob.glob(str(M.SYNC / "*.png"))) if not re.search(r"(_s|_small|_c)\.png$", p)]
    for p in pngs[: args.ocr_limit]:
        _, parsed = ocr(p)
        parsed["file"] = os.path.basename(p)
        caps.append(parsed)
        out("capture", **parsed)
    seen = set()
    for r in M.load_probe(args.dates):
        u = (r.get("ui") or {}).get("text") or ""
        if u and u[:40] not in seen:
            seen.add(u[:40])
            u = re.sub(r"(?:[0-9A-Fa-f]{2}:){5,}[0-9A-Fa-f]{2}", "<redacted-fingerprint>", u)
            out("recorded_ui", host=r["host"], t=r["t"], hints=(r.get("ui") or {}).get("hints"), text=u[:200])
    out("would_measure", plan="live: screencap+OCR both sticks (read-only); --nav on hidden: DPAD_DOWN then "
                              "captures at +0.7/+1.8/+3.5 s paired with media_session pos_now")
    judge(out, caps)


def capture(host, tag, out):
    RES = M.RESULTS / "mechanics_ui"
    RES.mkdir(parents=True, exist_ok=True)
    path = RES / f"{host.split(':')[0].split('.')[-1]}_{tag}_{int(time.time() * 1000)}.png"
    r0 = M.read(host)
    with open(path, "wb") as f:
        subprocess.run(["adb", "-s", host, "exec-out", "screencap -p"], stdout=f, timeout=15)
    r1 = M.read(host)
    _, parsed = ocr(str(path))
    rec = dict(host=host, tag=tag, file=str(path), pos_now=r1 and round(r1["pos_now"], 3),
               E=r1 and round(r1["E"], 3), st=r1 and r1["st"], sub=r1 and r1["sub"],
               bracket_s=round(r1["t"] - r0["t"], 2) if r0 and r1 else None, **parsed)
    out("capture", **rec)
    return rec


def live(args, out):
    hosts, ss = M.preflight(args, out, need_action=False)
    if args.stick == "hidden" and not args.nav:
        hosts = sorted(M.HOSTS.values())
    caps = []
    end = time.time() + args.minutes * 60
    for h in hosts:
        caps.append(capture(h, "passive", out))
    if args.nav:
        hid = sorted(ss["hidden"])
        if not hid:
            raise M.Refused("--nav needs a confirmed hidden stick")
        with M.Guard(hid[0], out, args.state_url) as g:
            while time.time() < end:
                g.key(20)
                t0 = time.time()
                for dt in (0.7, 1.8, 3.5):
                    time.sleep(max(0.0, t0 + dt - time.time()))
                    caps.append(capture(g.host, f"nav+{dt}", out))
                if args.settings_url and re.match(r"^https://tv\.youtube\.com/[\w/?=&.-]*$", args.settings_url):
                    g.check()
                    out("action", host=g.host, deeplink=args.settings_url)
                    M.T.sh(g.host, f"am start -a android.intent.action.VIEW -d {shlex.quote(args.settings_url)} {M.PKG}")
                    time.sleep(3)
                    caps.append(capture(g.host, "settings", out))
                out("relink", **M.tune_measure(g, args.channel))
                if not args.repeat:
                    break
                time.sleep(20)
    judge(out, caps)


def extra(ap):
    ap.add_argument("--nav", action="store_true", help="hidden stick only: raise controls with DPAD_DOWN")
    ap.add_argument("--settings-url", default=None, help="optional https://tv.youtube.com/... deep link to try for a settings page")
    ap.add_argument("--repeat", action="store_true", help="repeat the nav capture every 20 s until --minutes")
    ap.add_argument("--ocr-limit", type=int, default=8)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=3, extra=extra)
