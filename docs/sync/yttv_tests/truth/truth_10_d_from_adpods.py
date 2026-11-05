#!/usr/bin/env python3
"""truth_10 d_from_adpods: D from YTTV ad pods (DAI bursts) against the real stoppage times.

Hypothesis: during a TV timeout YTTV's DAI pod makes media_session 'updated' fire once per spot
(every 10-16 s). The pod END on the stick (last update + one spot) sits a fairly constant lead before
the real restart (broadcast returns, then the inbound), so D_raw_end = pod_end - real_restart is
stable per channel (MAD <= 3 s) and D_est = D_raw_end + lead (prior 10 +- 6 s) lands inside the YTTV
ESPN prior (29 +- 8). If the spread is small, the lead can be calibrated once per channel (with one
clock-based D from truth_08) and every later pod is a free D sample with ~2-3 s error.

Procedure (live, READ-ONLY on --stick, any stick incl. on screen): read the stick boot epoch (bracketed
/proc/uptime), then every 1 s for --minutes read dumpsys media_session (YTTV only); every distinct
'updated' becomes an event at real time boot + updated; bursts = same title, 8-17 s gaps, position
advancing by the gap. Match each burst to --game (or the ESPN game whose teams appear in the YTTV
title) and to the latest certified stoppage called before the pod start whose restart is within
[pod_end - 150, pod_end + 30] s. --dry-run: recorded sync_probe (boot from cache/boots_live.json when it
agrees with the recording, truth_09) + cached truth lines.

Pass rule: >= 3 matched pods on a stick/channel AND MAD(D_raw_end) <= 3 s AND median D_est within
29 +- 16 s. Fewer than 3 pods -> N/A (log what was seen).
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_10"


def live_events(host, minutes):
    boot, rtt = T.stick_boot_epoch(host)
    ev, seen = [], set()
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        r = T.read_session(host)
        if r and r["upd"] not in seen:
            seen.add(r["upd"])
            w = boot + r["upd"] / 1000
            ev.append(dict(w=w, host=host, pos=r["pos"] / 1000, E=w - r["pos"] / 1000, title=r["title"],
                           sub=r["sub"], st=r["st"], role=None))
        time.sleep(1.0)
    return ev, boot


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    if a.dry_run:
        print(f"[{ID}] dry-run: would poll media_session (read-only) on {a.stick} every 1 s for {a.minutes} min; "
              "matching recorded YTTV bursts to cached stoppages")
        samples, boots, ev = T.adpod_samples(a.days.split(","))
        out.write("boots", boots=boots, events=len(ev))
    else:
        host = T.resolve_stick(a.stick)
        if not host:
            sys.exit("no stick resolved; pass --stick HDMI1|HDMI2 (read-only is allowed on either)")
        ev, boot = live_events(host, a.minutes)
        if not a.game:
            sys.exit("live mode needs --game to fetch the truth line")
        nid = T.espn_to_nba(a.game, False)
        T.nba_pbp(nid, False, refresh=True)
        T.espn_summary("nba", a.game, False, refresh=True)
        tl = T.truth_for(a.game, True)[2]
        samples = []
        for b in T.bursts(ev):
            cands = [s for s in (tl.stoppages() if tl else []) if s["restart"] and s["wall"] <= b["start"]
                     and b["end"] - 150 <= s["restart"] <= b["end"] + 30]
            if not cands:
                samples.append(dict(b, game=a.game, why="no stoppage brackets the pod"))
                continue
            s = max(cands, key=lambda x: x["wall"])
            d = b["end"] - s["restart"]
            samples.append(dict(b, game=a.game, stop=s, D_raw_end=round(d, 2),
                                D_raw_start=round(b["start"] - s["wall"], 2), D_est=round(d + 10, 2), err=6.0))
    groups = {}
    for s in samples:
        out.write("pod", **{k: v for k, v in s.items() if k != "stop"},
                  stop=({k: s["stop"][k] for k in ("type", "period", "clock", "wall", "restart", "dur")}
                        if s.get("stop") else None))
        if "D_raw_end" in s:
            groups.setdefault(f"{s['host']}|{s['sub']}", []).append(s)
    res = {}
    for k, ss in groups.items():
        d = [s["D_raw_end"] for s in ss]
        res[k] = dict(n=len(d), D_raw_end_median=round(statistics.median(d), 2),
                      MAD=round(T.mad(d), 2) if len(d) > 1 else None,
                      D_est_median=round(statistics.median(s["D_est"] for s in ss), 2),
                      D_raw_start=[s["D_raw_start"] for s in ss][:10])
    judged = {k: v for k, v in res.items() if v["n"] >= 3}
    passed = None if not judged else all(v["MAD"] <= 3 and abs(v["D_est_median"] - 29) <= 16 for v in judged.values())
    out.verdict(passed, ">=3 pods per stick/channel, MAD(D_raw_end)<=3 s, median D_est in 29+-16",
                pods_seen=len(samples), matched=sum(len(v) for v in groups.values()), groups=res,
                unmatched_titles=sorted({s["title"][:30] for s in samples if "D_raw_end" not in s})[:10])


if __name__ == "__main__":
    main()
