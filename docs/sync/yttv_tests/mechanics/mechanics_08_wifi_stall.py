#!/usr/bin/env python3
"""mechanics_08 wifi_stall: does Wi-Fi quality move E, and only through visible stalls?

Hypothesis: while PLAYING, E changes only when the player stalls (BUFFERING state, or position not
advancing): every E step > 0.2 s coincides with a stall/buffering/pause of about the same length, and RSSI
alone (no stall) never shifts E. If true, the delay model only needs to add measured stall time; if false,
YTTV is silently retiming (e.g. DAI splices) and E must be re-anchored.

Procedure (live, READ-ONLY, both sticks by default): every --poll s (>= 1): read media_session (E, state);
every 10 s read RSSI/link (dumpsys wifi | grep -m1 mWifiInfo). Classify each E step > 0.2 s on the same
channel as 'stall' (a non-PLAYING state seen since the last step; media_session extrapolates position, so a frozen
frame without a state change is invisible here), 'pause', or
'unexplained'. Correlate RSSI with step sizes.
Dry run: same classification on sync_probe rows (wifi.rssi, cond.stalls_per_min, stall_s, state).

Pass rule: >= 90% of E steps > 0.2 s explained by a stall/pause seen in the same interval, AND the 'no-stall'
intervals show |dE| <= 0.2 s regardless of RSSI. (Report steps by class and corr(RSSI, |dE|).)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_08_wifi_stall"


def corr(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    return None if sx == 0 or sy == 0 else round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3)


def classify(samples, out, host):
    """samples: [(t, E|None, state, ch, rssi, stall_hint)] in time order."""
    steps, prevE, seen = [], None, set()
    quiet = []
    for t, E, stt, ch, rssi, hint in samples:
        if stt != "playing" or E is None:
            seen.add("pause" if stt == "paused" else "stall")
            continue
        if hint:
            seen.add("stall")
        if prevE and prevE[1] == ch:
            dE = E - prevE[0]
            if abs(dE) > 0.2:
                cls = "stall" if "stall" in seen else ("pause" if "pause" in seen else "unexplained")
                if abs(dE) > 120:
                    cls = "relink/seek"
                steps.append(dict(host=host, t=t, dE=round(dE, 2), cls=cls, rssi=rssi))
            else:
                quiet.append((rssi, abs(dE)))
            seen = set()
        prevE = (E, ch)
    return steps, quiet


def judge(out, steps, quiet):
    real = [s for s in steps if s["cls"] != "relink/seek"]
    cls = {}
    for s in steps:
        cls[s["cls"]] = cls.get(s["cls"], 0) + 1
    expl = sum(1 for s in real if s["cls"] in ("stall", "pause"))
    pairs = [(s["rssi"], abs(s["dE"])) for s in real if s["rssi"] is not None]
    out("summary", classes=cls, steps=len(real), explained=expl,
        unexplained_dE=M.summary([s["dE"] for s in real if s["cls"] == "unexplained"]),
        corr_rssi_absdE=corr([p[0] for p in pairs], [p[1] for p in pairs]), quiet_intervals=len(quiet),
        rssi=M.summary([q[0] for q in quiet if q[0] is not None]))
    ok = None if len(real) < 3 else expl / len(real) >= 0.9
    out.verdict(ok, ">=90% of E steps >0.2 s explained by a stall/pause in the same interval")


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    all_steps, all_quiet = [], []
    for h in M.HOSTS.values():
        samples = []
        for r in rows:
            if r["host"] != h:
                continue
            ms = r.get("ms") or {}
            if ms.get("pkg") != M.PKG or ms.get("position") is None or ms["position"] < 1000:
                continue
            E = M.rec_E(r, segs) if ms.get("state") == "playing" else None
            hint = bool(r.get("stall_s")) or bool((r.get("cond") or {}).get("stall_s_sum"))
            samples.append((r["t"], E, ms.get("state"), M.chan_key(ms.get("subtitle")),
                            (r.get("wifi") or {}).get("rssi"), hint))
        st_, q = classify(samples, out, h)
        all_steps += st_
        all_quiet += q
    for s in all_steps[:30]:
        out("step", **s)
    out("note", why="recorded probe cadence is ~5-10 s: short stalls between samples are invisible, so "
                    "'unexplained' is an upper bound; DAI ad-pod splices are a known non-stall E mover")
    out("would_measure", plan=f"live: poll both sticks every {args.poll}s for {args.minutes} min, RSSI every 10 s")
    judge(out, all_steps, all_quiet)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=False)
    if args.stick == "hidden":
        hosts = sorted(M.HOSTS.values())  # read-only: watch both
    samples = {h: [] for h in hosts}
    rssi = {h: None for h in hosts}
    last_w = 0.0
    end = time.time() + args.minutes * 60
    while time.time() < end:
        if time.time() - last_w >= 10:
            for h in hosts:
                w = M.wifi(h)
                rssi[h] = w.get("rssi")
                out("wifi", host=h, **w)
            last_w = time.time()
        for h in hosts:
            r = M.read(h)
            if not r:
                continue
            samples[h].append((r["t"], r["E"] if r["st"] == "playing" else None, r["st"], M.chan_key(r["sub"]),
                               rssi[h], r["st"] in ("buffering", "connecting")))
        time.sleep(max(1.0, args.poll))
    steps, quiet = [], []
    for h in hosts:
        s, q = classify(samples[h], out, h)
        steps += s
        quiet += q
    for s in steps:
        out("step", **s)
    judge(out, steps, quiet)


def extra(ap):
    ap.add_argument("--poll", type=float, default=2.0)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=30, extra=extra)
