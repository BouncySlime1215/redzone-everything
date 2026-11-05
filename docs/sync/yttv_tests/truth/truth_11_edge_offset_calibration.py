#!/usr/bin/env python3
"""truth_11 edge_offset_calibration: one true D per channel -> D for every later tune from E alone.

Hypothesis: E = wall - extrapolated position is constant while YTTV plays (pauses add exactly), and
each deep-link tune lands at the same latency behind real for a given channel + stick. Then
K = D - (E_now - E_land) is a per-(stick, channel) constant (sd <= 1.5 s across tunes), and after one
truth-based D (truth_08 clock reading, or truth_10 pod) every later moment's D = K + (E_now - E_land)
with no picture at all.

Procedure: split the stick's YTTV media_session events into landings (channel change, or E jumping
> 2 s while the position is back at the edge ~46,790-46,805 s: YTTV re-lands / auto-rejoins live; a
forward E jump with position away from the edge = pause, stays in the tune, unless the position is
closer to the edge than the time since the last event - a re-land the poll may have missed: new tune
flagged ambiguous, its K samples are logged but not judged; E_land = first E).
(A) E rigidity: within a tune, |dE| between consecutive 'playing' events whose position advanced by
>= 1 s (pure play, no pause in between) - the carry-forward premise. (B) For each D sample
(live: rows from --obs-file written by truth_08 with host + obs_wall + D; dry-run: truth_10's matched
pods on the recorded nights) find its tune, K = D - (E_at_sample - E_land), group by stick+channel.
Live: poll media_session READ-ONLY on --stick every 1 s for --minutes (allowed on either stick).

Pass rule: (A) |dE| p50 <= 0.3 s and p90 <= 1.0 s over pure-play steps; and (B) where a
stick/channel has >= 3 D samples over >= 2 tunes, sd(K) <= 1.5 s. (B) N/A when too few samples;
overall = (A) and (B if judged).
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_11"


def seg_at(segs, host, w):
    best = None
    for s in segs:
        if s["host"] == host and s["t_land"] <= w + 1:
            best = s
    return best


def e_at(seg, w):
    prev = [e for e in seg["events"] if e["w"] <= w + 1]
    return (prev[-1] if prev else seg["events"][0])["E"]


def main():
    ap = T.parser(ID, __doc__)
    ap.add_argument("--obs-file", help="truth_08 output JSONL (rows kind=d with D, host, obs_wall)")
    a = ap.parse_args()
    out = T.Out(a.out, ID, a)
    if a.dry_run:
        print(f"[{ID}] dry-run: would poll media_session (read-only) every 1 s for {a.minutes} min and read "
              "D rows from --obs-file; using recorded probe + truth_10 pods")
        samples, boots, ev = T.adpod_samples(a.days.split(","))
        ds = [dict(host=s["host"], sub=s["sub"], w=s["end"], D=s["D_est"], src="adpod")
              for s in samples if "D_est" in s]
    else:
        host = T.resolve_stick(a.stick)
        if not host:
            sys.exit("no stick resolved; pass --stick HDMI1|HDMI2")
        boot = T.stick_boot_epoch(host)[0]
        ev, seen = [], set()
        t0 = time.time()
        while time.time() - t0 < a.minutes * 60:
            r = T.read_session(host)
            if r and r["upd"] not in seen:
                seen.add(r["upd"])
                w = boot + r["upd"] / 1000
                ev.append(dict(w=w, host=host, pos=r["pos"] / 1000, E=w - r["pos"] / 1000, title=r["title"],
                               sub=r["sub"], st=r["st"], role=None))
            time.sleep(1.0)
        ds = []
        for row in T.load_jsonl(a.obs_file) if a.obs_file else []:
            if row.get("kind") == "d" and row.get("D") is not None and row.get("obs_wall"):
                ds.append(dict(host=T.resolve_stick(row.get("host") or host), sub=row.get("channel", ""),
                               w=row["obs_wall"], D=row["D"], src="clock"))
    segs = T.tune_segments(ev)
    sds = []
    for s in segs:
        steps = [abs(b["E"] - a["E"]) for a, b in zip(s["events"], s["events"][1:]) if b["pos"] - a["pos"] >= 1]
        sds += steps
        es = [e["E"] for e in s["events"]]
        out.write("tune", host=s["host"], sub=s["sub"], t_land=s["t_land"], E_land=round(s["E_land"], 3),
                  edge=round(s["edge"], 1), n=len(es), pure_steps=len(steps),
                  max_step=round(max(steps), 3) if steps else None, behind_end=round(es[-1] - es[0], 2))
    groups = {}
    for d in ds:
        s = seg_at(segs, d["host"], d["w"])
        if not s:
            continue
        behind = e_at(s, d["w"]) - s["E_land"]
        k = d["D"] - behind
        if not s.get("ambiguous"):
            groups.setdefault(f"{d['host']}|{s['sub']}", []).append(dict(K=k, tune=s["t_land"], behind=behind))
        out.write("K", host=d["host"], sub=s["sub"], src=d["src"], D=d["D"], behind=round(behind, 3), K=round(k, 3),
                  tune=s["t_land"], ambiguous=s.get("ambiguous", False))
    res = {}
    for k, xs in groups.items():
        ks = [x["K"] for x in xs]
        res[k] = dict(n=len(ks), tunes=len({x["tune"] for x in xs}), K_median=round(statistics.median(ks), 2),
                      K_sd=round(statistics.pstdev(ks), 2) if len(ks) > 1 else None)
    med_sd = T.pct(sds, .5)
    a_ok = None if med_sd is None else (med_sd <= 0.3 and T.pct(sds, .9) <= 1.0)
    judged = {k: v for k, v in res.items() if v["n"] >= 3 and v["tunes"] >= 2}
    b_ok = None if not judged else all(v["K_sd"] <= 1.5 for v in judged.values())
    passed = a_ok if b_ok is None else (a_ok and b_ok)
    out.verdict(passed, "(A) pure-play |dE| p50<=0.3, p90<=1.0 s; (B) sd(K)<=1.5 s where >=3 samples over >=2 tunes",
                tunes=len(segs), pure_steps=T.summ(sds), A=a_ok, B=b_ok, K=res)


if __name__ == "__main__":
    main()
