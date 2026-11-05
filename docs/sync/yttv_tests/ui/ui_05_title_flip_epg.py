#!/usr/bin/env python3
"""ui_05 Program-title flip vs the EPG boundary (read-only, both sticks, also runs on old recordings).

HYPOTHESIS: YTTV switches the program metadata (media_session title; the overlay's program window) when
the PLAYHEAD crosses the EPG boundary in YTTV's timeline (e.g. 7:30:00 PM). The flip fires a
media_session update, so its exact stick-uptime is in 'updated' and
    t_flip = boot + updated/1000          (boot = wall - /proc/uptime; stick clock within 0.1 s of Mac)
    D_flip = t_flip - boundary            (YTTV's playhead delay vs its own wall clock, like ui_02's D_ui)
    C_flip = boundary - position_at_flip  (same C as ui_02; E - C = D at any later time, no screenshots)
If D_flip is stable per channel (and equals ui_02's D_ui), every half-hour boundary is a free calibration.
Recorded sync_probe rows have no uptime, so dry-run estimates boot = min over rows of (t - updated/1000)
(an update can't be after the read) - tight whenever a row was read right after an update (ad pods
update every 10-16 s).

PROCEDURE: poll `cat /proc/uptime; dumpsys media_session` every --poll s (>= 0.5 s) on the stick
(--stick both = both sticks), for --minutes. On every (subtitle,title) change with a new 'updated':
log t_flip, nearest :00/:30 boundary, D_flip, E, C_flip. Read-only: no keys, safe on the visible stick.
Best run across 7:00/7:30/8:00/... boundaries (games that overrun don't flip on the grid: those
flips are logged with |D_flip| > 120 s and excluded).

PASS: on some channel+stick, >= 3 grid flips (|D_flip| <= 120 s) lie within +-1.0 s of their median and
  they are >= 60% of that channel's grid flips.
DRY-RUN RESULT (recorded 2026-11-04, HDMI1 .150 ESPN, never paused): D_flip = 17.51, 18.27, 18.14, 18.24,
  18.09, 18.14, 22.97 s -> median 18.14, MAD 0.10 s; C_flip = 1793770580.0 +- 0.1 (stable for 5 h).
  .181 ESPN (left ~66 s behind after pause tests): 84.2 +- 0.05 s. -> PASSES on recordings.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_05_title_flip_epg"
GRID = 1800.0


def extra(ap):
    ap.add_argument("--poll", type=float, default=1.0)


def boundary(t: float) -> float:
    return round(t / GRID) * GRID


def flips_from_rows(rows, boot):
    """rows: dicts with t, ms. Returns flip dicts."""
    out, last = [], None
    for r in rows:
        m = r.get("ms") or {}
        if not m.get("title"):
            continue
        key = (m.get("subtitle"), m.get("title"))
        if last and last[0] != key and last[0][0] == key[0] and m.get("updated") != last[1]:
            tf = boot + m["updated"] / 1000 if boot is not None else r["t"]
            b = boundary(tf)
            out.append(dict(channel=key[0], frm=last[0][1], to=key[1], t_flip=round(tf, 2),
                            boundary=b, D_flip=round(tf - b, 2),
                            C_flip=round(b - m["position"] / 1000, 2) if m.get("position") is not None else None,
                            read_gap=round(r["t"] - last[2], 1), state=m.get("state")))
        last = (key, m.get("updated"), r["t"])
    return out


def grid_ok(flips):
    by = {}
    for f in flips:
        if abs(f["D_flip"]) <= 120:
            by.setdefault((f.get("host"), f["channel"]), []).append(f["D_flip"])
    best = {}
    for k, v in by.items():
        sm = U.summarize(v)
        core = [x for x in v if abs(x - sm["median"]) <= 1.0]
        sm["core_n"], sm["core_spread"] = len(core), round(max(core) - min(core), 2) if core else None
        best[k] = sm
    ok = any(s["core_n"] >= 3 and s["core_n"] >= 0.6 * s["n"] for s in best.values())
    return best, ok


def live(args, g, out):
    hosts = list(U.HOSTS.values()) if args.stick == "both" else [g.host]
    from redzone.tuners.adb import parse_playback
    last, flips = {}, []
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        for h in hosts:
            try:
                t0 = time.time(); txt = U._sh(h, "cat /proc/uptime; dumpsys media_session"); t1 = time.time()
                boot = (t0 + t1) / 2 - float(txt.split()[0])
                b = parse_playback(txt, U.PKG)
            except Exception as e:  # noqa: BLE001
                out.write("err", host=h, err=repr(e)); continue
            row = dict(t=(t0 + t1) / 2, ms=dict(title=b.get("title"), subtitle=b.get("subtitle"),
                                                  updated=b.get("updated"), position=b.get("position"),
                                                  state=b.get("state")))
            seq = last.setdefault(h, [])
            seq.append(row); del seq[:-2]
            for f in flips_from_rows(seq, boot):
                if not any(x["t_flip"] == f["t_flip"] and x["host"] == h for x in flips):
                    f["host"] = h; flips.append(f); out.write("flip", **f)
        U.sleep_poll(args.poll)
    best, ok = grid_ok(flips)
    return dict(flips=len(flips), by_channel={f"{k[0]}|{k[1]}": v for k, v in best.items()}, pass_=bool(ok))


def dry(args, out):
    allf = []
    for date in ([args.date] if args.date else ["2026-11-04", "2026-11-05"]):
        rows = U.load_probe(date, True)
        for h in U.HOSTS.values():
            hr = [r for r in rows if r.get("host") == h and (r.get("ms") or {}).get("updated") is not None]
            if not hr:
                continue
            # boot changes if the stick rebooted: split where t - updated/1000 jumps by > 60 s
            cands = [r["t"] - r["ms"]["updated"] / 1000 for r in hr]
            boot = None
            segs, cur = [], [0]
            for i in range(1, len(hr)):
                if abs(cands[i] - cands[i - 1]) > 3600:
                    segs.append(cur); cur = []
                cur.append(i)
            segs.append(cur)
            for seg in segs:
                boot = min(cands[i] for i in seg)
                fl = flips_from_rows([hr[i] for i in seg], boot)
                for f in fl:
                    f.update(host=h, date=date, boot_est=round(boot, 2))
                    out.write("flip", **f)
                allf += fl
                out.write("boot", host=h, date=date, boot_est=round(boot, 2), rows=len(seg),
                          tightness_s=round(sorted(cands[i] for i in seg)[min(5, len(seg) - 1)] - boot, 2))
    best, ok = grid_ok(allf)
    return dict(flips=len(allf), grid_flips={f"{k[0]}|{k[1]}": v for k, v in best.items()}, pass_=bool(ok),
                note="dry-run on recorded probes (sparse reads, boot estimated)")


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
