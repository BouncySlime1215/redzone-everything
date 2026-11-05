#!/usr/bin/env python3
"""mechanics_05 session_monitor: push-based PlaybackState stream instead of polling (read-only).

Hypothesis: `cmd media_session monitor starboard` (stdin held open by `sleep N |`, so it never receives a
command) prints every PlaybackState change the moment YTTV sets it. Each update carries position + updated
(stick uptime ms), so E per update = boot_epoch + updated/1000 - position/1000 is exact to ~1 ms and needs
no extrapolation; the Mac arrival delay is < 0.5 s; and E is constant (sd <= 0.05 s) between pauses/stalls.
It also yields the update cadence during ad pods (DAI spot boundaries every 10-16 s) as a precise event
train for the burst/cross-stick tests, and decodes the advertised actions bitmask (SEEK_TO etc.).

Procedure (live, READ-ONLY, either stick; default both): stick_clock() for boot epoch; actions mask from
one dumpsys; run the monitor for --minutes per stick in parallel threads (one adb shell each, no Mac loop);
parse every PlaybackState line; log E_upd, arrival_lag, gap since previous update. Never writes to the
monitor's stdin (it accepts play/pause there).
Dry run: per recorded PLAYING run: E sd (extrapolated, boot-estimated), update cadence from fresh `updated`
changes, and how many updates fall in ad-pod-like bursts (gaps 8-20 s).

Pass rule: >= 1 run with >= 3 updates; within runs sd(E_upd) <= 0.05 s; p90 arrival_lag <= 0.5 s.
"""
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_05_session_monitor"
PS_RE = re.compile(r"state=(\d+), position=(-?\d+).*?speed=(-?[\d.]+), updated=(\d+)")


def runs_of(evts, jump=0.3):
    runs, cur = [], []
    for e in evts:
        if e["state"] != 3:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
            continue
        if cur and abs(e["E"] - cur[-1]["E"]) > jump:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
        cur.append(e)
    if len(cur) > 1:
        runs.append(cur)
    return runs


def judge(out, by_host: dict):
    ok_runs, sds, lags = 0, [], []
    for h, evts in by_host.items():
        rs = runs_of(evts)
        gaps = [b["upd_epoch"] - a["upd_epoch"] for a, b in zip(evts, evts[1:])]
        lags += [e["arrival_lag"] for e in evts if e.get("arrival_lag") is not None]
        for r in rs:
            if len(r) >= 3:
                ok_runs += 1
                sds.append(M.summary([e["E"] for e in r])["sd"])
        out("host_summary", host=h, updates=len(evts), runs=len(rs), gap_s=M.summary(gaps),
            burst_gaps_8_20s=sum(1 for g in gaps if 8 <= g <= 20))
    lag = M.summary(lags)
    ok = None
    if ok_runs:
        ok = max(sds) <= 0.05 and (lag.get("n", 0) == 0 or lag["p90"] <= 0.5)
    out.verdict(ok, ">=1 run with >=3 updates, sd(E_upd) <= 0.05 s, p90 arrival lag <= 0.5 s",
                runs=ok_runs, worst_sd=max(sds) if sds else None, arrival_lag=lag)


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    by_host = {}
    for h in M.HOSTS.values():
        last_upd, evts = None, []
        for r in M.yt_rows(rows, h):
            ms = r["ms"]
            if ms["updated"] == last_upd:
                continue
            last_upd = ms["updated"]
            boot = M.boot_at(segs, h, r["t"])
            if boot is None:
                continue
            code = {"playing": 3, "paused": 2, "buffering": 6}.get(ms.get("state"), 0)
            ue = boot + ms["updated"] / 1000
            evts.append(dict(state=code, E=ue - ms["position"] / 1000, upd_epoch=ue, arrival_lag=None))
        by_host[h] = evts
    out("would_measure", plan="live: `sleep N | cmd media_session monitor starboard` on both sticks, "
                              "E per pushed update, arrival lag, cadence (read-only)",
        note="dry E uses boot estimated from recordings (+-0.5 s), so the sd rule is informative only")
    judge(out, by_host)


def monitor(host, secs, out, sink):
    clk = M.stick_clock(host)
    boot = clk.get("boot_epoch")
    r = M.read(host)
    out("actions", host=host, mask=r and r["actions"], decoded=M.decode_actions(r and r["actions"]), clock=clk)
    cmd = f"sleep {int(secs)} | cmd media_session monitor starboard"
    p = subprocess.Popen(["adb", "-s", host, "shell", cmd], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    evts = sink.setdefault(host, [])
    for line in p.stdout:
        t_arr = time.time()
        m = PS_RE.search(line)
        if not m or boot is None:
            if line.strip():
                out("monitor_line", host=host, line=line.strip()[:200])
            continue
        state, pos, speed, upd = int(m.group(1)), int(m.group(2)), float(m.group(3)), int(m.group(4))
        ue = boot + upd / 1000
        e = dict(state=state, pos=pos / 1000, speed=speed, upd_epoch=round(ue, 3), E=round(ue - pos / 1000, 3),
                 arrival_lag=round(t_arr - ue, 3), gap_s=round(ue - evts[-1]["upd_epoch"], 3) if evts else None)
        evts.append(e)
        out("update", host=host, **e)
    p.wait(timeout=10)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=False)
    if args.stick == "hidden":
        hosts = sorted(M.HOSTS.values())  # read-only test: default watches both sticks
    sink = {}
    th = [threading.Thread(target=monitor, args=(h, args.minutes * 60, out, sink), daemon=True) for h in hosts]
    for t in th:
        t.start()
    for t in th:
        t.join(timeout=args.minutes * 60 + 30)
    judge(out, sink)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=10)
