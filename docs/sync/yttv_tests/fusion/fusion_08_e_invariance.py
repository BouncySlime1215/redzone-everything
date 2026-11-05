#!/usr/bin/env python3
"""fusion_08_e_invariance: is the E track trustworthy enough to carry an anchor across a whole session?

Hypothesis: E = wall - extrapolated position stays constant (< 0.2 s jitter) while YTTV plays, including
across DAI ad-spot updates (media_session 'updated' every 10-16 s in a pod); pauses add exactly their
length; an auto-rejoin-live after a pause shows up as a position jump (E falls) and must start a new
session. If this holds, ONE anchor per session fixes D for the whole session (fusion only needs the
per-session origin) and the error budget is set by the anchors, not by propagation.

Procedure (recorded sync_probe; + READ-ONLY live polling of --stick for --minutes):
  1. Per session: the distinct media_session updates while playing; E at each; jitter = sd and
     max |E - E_first|; separately the subset that fell inside ad-pod bursts.
  2. Pause transitions (paused -> playing): dE vs pause length from the two 'updated' stamps;
     rejoin = position at resume > position at pause + 1 s.
  3. Unexplained E steps while playing (> 0.5 s, same channel, no landing) are counted.

Pass rule: median per-session jitter sd <= 0.2 s, 95th pct max|dE| <= 0.5 s, burst-update jitter
<= 0.3 s, unexplained steps per hour of playback <= 0.5. Read-only.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_08_e_invariance"


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args, want_synthetic=False)
    L.header(ID, args, B["info"])
    ch_f = L.norm_ch(args.channel) if args.channel != "all" else None
    burst_upd = {(b[0].host, s.upd_wall) for b in L.bursts(B["samples"]) for s in b}
    jit, mx, bj, steps, play_h, pauses = [], [], [], 0, 0.0, []
    for s in B["sessions"]:
        if ch_f and s.ch != ch_f:
            continue
        if args.stick not in ("both", "hidden") and s.stick != L.stick_name(args.stick):
            continue
        ups, seen = [], set()
        for x in s.samples:
            if x.st == "playing" and x.upd_wall not in seen:
                seen.add(x.upd_wall)
                ups.append(x)
        play_h += sum(b.t - a.t for a, b in zip(s.samples, s.samples[1:]) if a.st == "playing") / 3600
        if len(ups) >= 2:
            es = [u.E for u in ups]
            jit.append(statistics.pstdev(es))
            mx.append(max(abs(e - es[0]) for e in es))
            bes = [u.E for u in ups if (u.host, u.upd_wall) in burst_upd]
            if len(bes) >= 2:
                bj.append(statistics.pstdev(bes))
            steps += sum(1 for a, b in zip(ups, ups[1:]) if abs(b.E - a.E) > 0.5)
        # pause transitions
        prev = None
        for x in s.samples:
            if prev and prev.st == "paused" and x.st == "playing" and x.upd_wall != prev.upd_wall:
                before = [u for u in s.samples if u.st == "playing" and u.t < prev.t]
                if before:
                    dE = x.E - before[-1].E
                    plen = x.upd_wall - prev.upd_wall
                    rejoin = x.pos_upd > prev.pos_upd + 1.0
                    pauses.append(dict(stick=s.stick, ch=s.ch, t=L.fmt_t(x.t), dE=L.r2(dE), pause_s=L.r2(plen),
                                       resid=L.r2(dE - plen), rejoin=rejoin))
            prev = x
    p95 = sorted(mx)[int(0.95 * (len(mx) - 1))] if mx else None
    row = dict(n_sessions=len(jit), jitter_median=L.r2(statistics.median(jit), 3) if jit else None,
               max_dev_p95=L.r2(p95, 3), burst_jitter_median=L.r2(statistics.median(bj), 3) if bj else None,
               n_burst_sessions=len(bj), unexplained_steps=steps, play_hours=L.r2(play_h),
               steps_per_hour=L.r2(steps / play_h) if play_h else None, pauses=pauses[:30], n_pauses=len(pauses))
    ok = (jit and statistics.median(jit) <= 0.2 and p95 is not None and p95 <= 0.5
          and (not bj or statistics.median(bj) <= 0.3) and (play_h == 0 or steps / play_h <= 0.5))
    row["pass_"] = bool(ok)
    out.write("invariance", **row)
    print(f"   sessions={row['n_sessions']} jitter_med={row['jitter_median']} s  max|dE| p95={row['max_dev_p95']} s  "
          f"burst_jitter_med={row['burst_jitter_median']} s (n={len(bj)})  steps={steps} over {row['play_hours']} h")
    for p in pauses[:8]:
        print(f"   pause {p}")
    out.write("summary", **{k: v for k, v in row.items() if k != "pauses"})
    print(f"SUMMARY {ID}: E invariant while playing -> {'PASS' if ok else 'FAIL'} -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
