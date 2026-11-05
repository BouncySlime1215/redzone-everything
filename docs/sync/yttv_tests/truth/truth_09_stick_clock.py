#!/usr/bin/env python3
"""truth_09 stick_clock: stick time -> Mac/real time, and the boot epoch, to <= 0.2 s.

Hypothesis: (a) each stick's wall clock is within 0.2 s of the Mac (NTP on both), and boot epoch =
wall - /proc/uptime is stable to < 0.1 s over a night (no drift worth modelling), so any stick
timestamp (media_session 'updated', logcat time, uptime) converts to real time well inside the 1-2 s
budget. (b) From a recording alone, boot = p10 over fresh media_session updates of (row t -
updated/1000) lands within 0.5 s of the live bracket (the plain minimum does NOT: row t is stamped
before dumpsys returns, so it can fall seconds below boot).

Procedure (live, READ-ONLY on --stick or both when --stick all; allowed on the on-screen stick too):
every 30 s for --minutes take 5 RTT-bracketed samples of 'date +%s.%N' and 'cat /proc/uptime', keep
the lowest-RTT one: offset = stick_date - Mac midpoint; boot = Mac midpoint - uptime. Report offset,
RTT, boot drift (max - min), and refresh cache/boots_live.json. (--dry-run) per stick per recorded
day: the p10 estimate and the plain minimum vs cache/boots_live.json (measured read-only on
2026-11-05; same boot unless the stick rebooted); writes cache/boots.json for truth_10/11.

Pass rule: live |offset| <= 0.2 s (p90) and boot drift <= 0.1 s; dry-run |p10 estimate - live boot|
<= 0.5 s for every stick-day (N/A when boots_live.json is missing).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_09"


def live(out, hosts, minutes):
    res = {h: [] for h in hosts}
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        for h in hosts:
            best = None
            for _ in range(5):
                m0 = time.time()
                o = T.sh(h, "date +%s.%N; cat /proc/uptime")
                m1 = time.time()
                try:
                    d, up = float(o.split()[0]), float(o.split()[1])
                except (IndexError, ValueError):
                    continue
                c = dict(rtt=m1 - m0, offset=d - (m0 + m1) / 2, boot=(m0 + m1) / 2 - up)
                if best is None or c["rtt"] < best["rtt"]:
                    best = c
                time.sleep(0.5)
            if best:
                res[h].append(best)
                out.write("sample", host=h, **{k: round(v, 4) for k, v in best.items()})
        time.sleep(30)
    verdict = {}
    for h, xs in res.items():
        if not xs:
            continue
        boots = [x["boot"] for x in xs]
        verdict[h] = dict(offset=T.summ([abs(x["offset"]) for x in xs]), rtt=T.summ([x["rtt"] for x in xs]),
                          boot_drift=round(max(boots) - min(boots), 3), boot=round(min(boots), 3))
    lp = os.path.join(T.CACHE, "boots_live.json")
    cur = json.load(open(lp)) if os.path.exists(lp) else {}
    for h, v in verdict.items():
        best = min(res[h], key=lambda x: x["rtt"])
        cur[h] = dict(boot=round(best["boot"], 3), rtt=round(best["rtt"], 3), offset=round(best["offset"], 3))
    cur["measured_at"] = time.time()
    json.dump(cur, open(lp, "w"), indent=1)
    ok = [v["offset"]["p90"] <= 0.2 and v["boot_drift"] <= 0.1 for v in verdict.values()]
    out.verdict(all(ok) if ok else None, "|offset| p90<=0.2 s and boot drift<=0.1 s", sticks=verdict)


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    if not a.dry_run:
        hosts = list(T.STICKS.values()) if a.stick == "all" else [T.resolve_stick(a.stick)]
        hosts = [h for h in hosts if h]
        if not hosts:
            sys.exit("no stick resolved (hidden unknown); pass --stick HDMI1|HDMI2|all (read-only is allowed)")
        return live(out, hosts, a.minutes)
    print(f"[{ID}] dry-run: would sample date+uptime (read-only) every 30 s for {a.minutes} min; "
          "estimating boot epochs from recorded sync_probe")
    lp = os.path.join(T.CACHE, "boots_live.json")
    live_boots = json.load(open(lp)) if os.path.exists(lp) else {}
    boots, verdict = {}, {}
    for day in a.days.split(","):
        rows = T.load_probe([day])
        for h in sorted({r.get("host") for r in rows if r.get("host")}):
            est = T.boot_estimate(rows, h)
            vals = T.boot_envelope(sorted(rows, key=lambda r: r["t"]), h)
            if est is None:
                continue
            lv = (live_boots.get(h) or {}).get("boot")
            v = dict(updates=len(vals), boot_p10=round(est, 3),
                     p10_minus_live=round(est - lv, 3) if lv else None,
                     min_minus_live=round(min(vals) - lv, 3) if lv else None)
            verdict[f"{h}@{day}"] = v
            boots.setdefault(h, {})[day] = round(est, 3)
            out.write("stickday", host=h, day=day, **v)
    json.dump(boots, open(os.path.join(T.CACHE, "boots.json"), "w"), indent=1)
    judged = [v for v in verdict.values() if v["p10_minus_live"] is not None]
    ok = [abs(v["p10_minus_live"]) <= 0.5 for v in judged]
    out.verdict(all(ok) if ok else None, "|p10 estimate - live boot| <= 0.5 s per stick-day", sticks=verdict)


if __name__ == "__main__":
    main()
