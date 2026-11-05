#!/usr/bin/env python3
"""mechanics_01 landing_repeat: is the deep-link landing position L fixed per channel per stick?

Hypothesis: every YTTV deep-link tune lands at a fixed media position L per (stick, channel)
(ESPN ~46,800.3 s on .181, ~46,790.1 s on .150, TNT ~46,790.2 s) with sd <= 0.5 s, so the landing is a
reproducible anchor (live edge minus a constant) that later tests can calibrate against plays.

Procedure (live, HIDDEN stick only, re-checked before every tune):
  repeat until --n landings or --minutes: tune --alt channel, wait 4 s, tune --channel; record
  L = position at the first PLAYING media_session update after the deep link, play_s (poll and stick
  timestamp), E_land, title. Sleep >= 4 s between tunes.
Dry run: extract every recorded landing from sync_probe (channel change or E jump) plus overnight.jsonl
exp A, and summarise L per (stick, channel).

Pass rule: for the tested (stick, channel): n >= 5 landings and sd(L) <= 0.5 s (robust: MAD*1.48 <= 0.5).
"""
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402
_STICKS = (__import__("os").environ.get("RZ_STICKS") or "hdmi1:5555,hdmi2:5555").split(",")  # stick addresses stay off the public repo: set RZ_STICKS=host1,host2 on the Mac

ID = "mechanics_01_landing_repeat"


def mad(xs):
    if len(xs) < 2:
        return 0.0
    m = st.median(xs)
    return round(1.4826 * st.median([abs(x - m) for x in xs]), 3)


def judge(out, groups: dict, min_n: int = 5):
    for (h, ch), Ls in sorted(groups.items()):
        s = M.summary(Ls)
        out("group", host=h, ch=ch, L=s, L_mad=mad(Ls), L_minus_46800=s.get("p50") and round(s["p50"] - 46800, 3))
    target = [(k, v) for k, v in groups.items() if len(v) >= min_n]
    ok = bool(target) and all(mad(v) <= 0.5 for _, v in target)
    out.verdict(ok if target else None, "n>=5 per (stick,channel) and robust sd(L) <= 0.5 s",
                groups_checked=len(target))


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    out("boot_estimates", segs={h: v for h, v in segs.items()})
    groups = {}
    for e in M.tune_events(rows, segs):
        if 46000 < e["L"] < 47600 and (e["from_ch"] != e["ch"] or not e["from_playing"]):  # real tunes only  # live-edge landings only (excludes DVR/VOD sessions)
            groups.setdefault((e["host"], e["ch"]), []).append(e["L"])
            out("landing", **e)
    for r in M.load_jsonl(M.SYNC / "overnight.jsonl"):
        if r.get("exp") == "A" and r.get("L_raw"):
            groups.setdefault((_STICKS[1], r["ch"] + "@overnight_L3s"), []).append(r["L_raw"] - 3)
    out("would_measure", plan=f"live: alternate {args.alt}->{args.channel} on the hidden stick x{args.n}, "
                             "log L/play_s per landing")
    judge(out, groups)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    if args.alt == args.channel:
        args.alt = "espn2" if args.channel != "espn2" else "tnt"
    groups = {}
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        while time.time() < end and len(groups.get((host, args.channel), [])) < args.n:
            out("tune_alt", **M.tune_measure(g, args.alt))
            time.sleep(4)
            res = M.tune_measure(g, args.channel)
            out("landing", host=host, **res)
            if res.get("ok"):
                groups.setdefault((host, args.channel), []).append(res["L"])
            time.sleep(4)
    judge(out, groups)


def extra(ap):
    ap.add_argument("--alt", default="tnt", help="channel to tune away to between landings")
    ap.add_argument("--n", type=int, default=6, help="landings to collect")


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=6, extra=extra)
