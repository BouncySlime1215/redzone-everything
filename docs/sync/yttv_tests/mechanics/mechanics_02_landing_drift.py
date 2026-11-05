#!/usr/bin/env python3
"""mechanics_02 landing_drift: does the landing position L drift across hours, or jump between modes?

Hypothesis: L per (stick, channel) is constant over a whole evening (|slope| <= 0.25 s/hour). The recordings
show two L modes ~10 s apart (46,790.2 vs 46,800.3 = one HLS/DASH segment?), so the test also counts which
mode each landing hits (mode = round((L - 46790) / 10)). If L only ever takes values 46,790 + k*10 (+-0.5),
the landing is "live edge minus k segments" and k must be read per tune, not assumed.

Procedure (live, HIDDEN stick only): every --interval minutes for --minutes: for each channel in --channels:
tune --alt, wait 4 s, tune the channel, record L, play_s, E_land, wall time. Light: one landing pair per
channel per interval.
Dry run: recorded landings (sync_probe) in the live-edge band, per (stick, channel): slope of L vs hours,
mode histogram.

Pass rule: per (stick, channel) with n >= 4 over >= 1 h: |slope(L)| <= 0.25 s/h (dominant mode) AND p90 |L - (46,790 + 10k)| <= 1.0 s
(quantized landing). Report mode counts either way.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_02_landing_drift"


def judge(out, pts: dict):
    verdicts = []
    for (h, ch), xs in sorted(pts.items()):
        ts = [t for t, _ in xs]
        Ls = [L for _, L in xs]
        span_h = (max(ts) - min(ts)) / 3600 if ts else 0
        modes = {}
        resid = []
        for L in Ls:
            k = round((L - 46790) / 10)
            modes[k] = modes.get(k, 0) + 1
            resid.append(abs(L - (46790 + 10 * k)))
        quant = M.summary(resid)["p90"] <= 1.0
        k0 = max(modes, key=modes.get)  # drift is judged inside the dominant mode only (outliers reported)
        main = [(t, L) for t, L in xs if round((L - 46790) / 10) == k0]
        sl = M.slope([t / 3600 for t, _ in main], [L for _, L in main])
        out("group", host=h, ch=ch, n=len(Ls), span_h=round(span_h, 2),
            slope_s_per_h=None if sl is None else round(sl, 4), modes=modes, quantized=quant,
            resid=M.summary(resid))
        if len(Ls) >= 4 and span_h >= 1 and sl is not None:
            verdicts.append(abs(sl) <= 0.25 and quant)
    out.verdict(all(verdicts) if verdicts else None,
                "per (stick,channel), n>=4 over >=1 h: |slope| <= 0.25 s/h and L = 46790+10k (p90 resid <= 1.0)",
                groups_checked=len(verdicts))


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    pts = {}
    for e in M.tune_events(rows, segs):
        if 46000 < e["L"] < 47600 and (e["from_ch"] != e["ch"] or not e["from_playing"]):  # real tunes only
            pts.setdefault((e["host"], e["ch"]), []).append((e["t"], e["L"]))
    out("would_measure", plan=f"every {args.interval} min for {args.minutes} min: landing on {args.channels}")
    judge(out, pts)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    chans = [c.strip() for c in args.channels.split(",") if c.strip()]
    pts = {}
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        while time.time() < end:
            t_round = time.time()
            for ch in chans:
                alt = args.alt if args.alt != ch else "espn2"
                M.tune_measure(g, alt)
                time.sleep(4)
                res = M.tune_measure(g, ch)
                out("landing", host=host, **res)
                if res.get("ok"):
                    pts.setdefault((host, ch), []).append((res["t_cmd"], res["L"]))
                time.sleep(4)
            while time.time() < min(end, t_round + args.interval * 60):
                time.sleep(5)
    judge(out, pts)


def extra(ap):
    ap.add_argument("--channels", default="espn,tnt", help="comma list")
    ap.add_argument("--alt", default="fs1")
    ap.add_argument("--interval", type=float, default=20.0, help="minutes between rounds")


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=240, extra=extra)
