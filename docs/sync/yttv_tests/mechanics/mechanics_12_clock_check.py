#!/usr/bin/env python3
"""mechanics_12 clock_check: are the clocks every E computation relies on good to 0.1 s? (read-only)

Hypothesis: E = mac_wall - pos_now uses the Mac clock and the stick's uptime (`updated` is uptime ms).
(a) stick wall clock is within 0.1 s of the Mac (NTP on both); (b) boot epoch (mac_mid - uptime) is stable
to <= 0.03 s across reads (no uptime drift vs Mac over the session, < 0.05 s/h); (c) adb shell RTT p90 is
<= 0.3 s so the midpoint error is <= 0.15 s. If (b) drifts, every long-running E needs re-anchoring.

Procedure (live, READ-ONLY, both sticks): every --poll s for --minutes: `date +%s.%N; cat /proc/uptime`
via adb; log offset_s, boot_epoch, rtt. Fit boot_epoch vs time (drift s/h).
Dry run: boot-epoch estimates from recordings per stick (min of t - updated/1000 per boot) and their
spread across nights; the latest overnight/t1 logs if present.

Pass rule: |median offset| <= 0.1 s, sd(boot_epoch) <= 0.03 s (or |drift| <= 0.05 s/h over >= 10 min), p90 rtt <= 0.3 s.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_12_clock_check"


def dry(args, out):
    for d in args.dates.split(","):
        rows = M.load_probe(d)
        segs = M.boot_segments(rows)
        for h, v in segs.items():
            out("boot_estimate", date=d, host=h, segments=[(round(a, 1), round(b, 3)) for a, b in v])
    all_rows = M.load_probe(args.dates)
    segs = M.boot_segments(all_rows)
    for h, v in segs.items():
        out("boot_across_nights", host=h, segments=len(v), boots=[round(b, 3) for _, b in v],
            note="one segment across both nights => no reboot; estimate is an upper bound within one poll")
    out("would_measure", plan=f"live: every {args.poll}s for {args.minutes} min, date/uptime on both sticks")
    out.verdict(None, "live-only rule: |offset| <= 0.1 s, sd(boot) <= 0.03 s or drift <= 0.05 s/h, p90 rtt <= 0.3 s")


def live(args, out):
    hosts = sorted(M.HOSTS.values())
    M.preflight(args, out, need_action=False)
    data = {h: [] for h in hosts}
    end = time.time() + args.minutes * 60
    while time.time() < end:
        for h in hosts:
            c = M.stick_clock(h)
            if "err" not in c:
                c["t"] = time.time()
                data[h].append(c)
                out("clock", host=h, **{k: v for k, v in c.items() if k != "t"})
        time.sleep(max(0.5, args.poll))
    oks = []
    for h, xs in data.items():
        if len(xs) < 3:
            continue
        off = M.summary([x["offset_s"] for x in xs])
        boot = M.summary([x["boot_epoch"] for x in xs])
        rtt = M.summary([x["rtt"] for x in xs])
        dr = M.slope([x["t"] / 3600 for x in xs], [x["boot_epoch"] for x in xs])
        out("host_summary", host=h, offset=off, boot_sd=boot["sd"], drift_s_per_h=dr and round(dr, 4), rtt=rtt)
        span_h = (xs[-1]["t"] - xs[0]["t"]) / 3600
        drift_ok = span_h >= 1 / 6 and dr is not None and abs(dr) <= 0.05  # drift only meaningful over >= 10 min
        oks.append(abs(off["p50"]) <= 0.1 and (boot["sd"] <= 0.03 or drift_ok)
                   and rtt["p90"] <= 0.3)
    out.verdict(all(oks) if oks else None, "|offset| <= 0.1 s, sd(boot) <= 0.03 s or drift <= 0.05 s/h, p90 rtt <= 0.3 s")


def extra(ap):
    ap.add_argument("--poll", type=float, default=5.0)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=5, extra=extra)
