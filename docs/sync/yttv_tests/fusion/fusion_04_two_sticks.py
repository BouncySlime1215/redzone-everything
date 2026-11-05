#!/usr/bin/env python3
"""fusion_04_two_sticks: can one stick's calibration be transferred to the other?

Hypothesis: the two sticks land at different positions on the same channel (ESPN ~46,790 on .150 vs
~46,800 on .181; TNT the same ~46,790 on both). Either (H_delay) that difference is a real delay
difference, D_edge(hdmi1) - D_edge(hdmi2) = L(hdmi2) - L(hdmi1), or (H_frame) it is only a position-frame
offset and both sticks sit equally far behind live, D_edge(hdmi1) = D_edge(hdmi2). Separately,
(H_shared) if the position frame were shared by both sticks, then for concurrent same-channel playback
E1 - E2 = D1 - D2 would be constant across tune pairs (an exact cross-stick transfer).

Procedure:
  1. Sessions from recorded sync_probe (+ READ-ONLY live polling of both sticks for --minutes).
  2. For every pair of overlapping same-channel sessions on the two sticks (both playing): dE = E1 - E2,
     and dt_land = t_land1 - t_land2 (fresh sessions). H_shared predicts sd(dE) small across pairs;
     per-session frames predict dE - dt_land = L2 - L1 constant instead.
  3. Per channel with anchor fits on both sticks (Kalman, fusion_01): dD = D_edge1 - D_edge2, compared with
     0 (H_frame) and with L2 - L1 (H_delay).

Pass rule (per channel): at least one hypothesis is consistent within 1.5 s and the other is rejected
(|diff| > 2 sigma) -> transfer rule is known. Without anchors on both sticks: 'insufficient' and the
test prints which anchors it WOULD need. Read-only.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_04_two_sticks"
H1, H2 = L.HOSTS["hdmi1"], L.HOSTS["hdmi2"]


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args, want_synthetic=False)
    L.header(ID, args, B["info"])
    ch_f = L.norm_ch(args.channel) if args.channel != "all" else None
    S = B["sessions"]
    pairs = []
    for a in S:
        if a.host != H1 or (ch_f and a.ch != ch_f):
            continue
        for b in S:
            if b.host != H2 or b.ch != a.ch:
                continue
            lo, hi = max(a.t0, b.t0), min(a.t1, b.t1)
            if hi - lo < 20:
                continue
            mid = (lo + hi) / 2
            ea, eb = a.E_at(mid), b.E_at(mid)
            if ea is None or eb is None:
                continue
            p = dict(channel=a.ch, t=L.fmt_t(mid), overlap_s=round(hi - lo), dE=L.r2(ea - eb),
                     fresh=a.fresh and b.fresh, L1=L.r2(a.L), L2=L.r2(b.L))
            if a.fresh and b.fresh:
                p["dE_minus_dtland"] = L.r2((ea - eb) - (a.t_land - b.t_land))
            pairs.append(p)
            out.write("pair", **p)
    by_ch = {}
    for p in pairs:
        by_ch.setdefault(p["channel"], []).append(p)
    # landing medians per stick
    Lmed = {}
    for s in S:
        if s.fresh:
            Lmed.setdefault((s.ch, s.stick), []).append(s.L)
    # median_low: with an even, bimodal sample the plain median can sit > 3 s from every value
    Lmed = {k: statistics.median([x for x in v if abs(x - statistics.median_low(v)) <= 3]) for k, v in Lmed.items()}
    fits = {}
    for (ch, st), g in L.group(L.usable(B["anchors"])).items():
        fits[(ch, st)] = L.kalman(g, L.prior_for(ch))
    chans = sorted({k[0] for k in Lmed} | set(by_ch))
    if ch_f:
        chans = [c for c in chans if c == ch_f]
    rows = []
    for ch in chans:
        ps = by_ch.get(ch, [])
        dEs = [p["dE"] for p in ps]
        res = [p["dE_minus_dtland"] for p in ps if "dE_minus_dtland" in p]
        l1, l2 = Lmed.get((ch, "hdmi1")), Lmed.get((ch, "hdmi2"))
        dL = (l2 - l1) if l1 is not None and l2 is not None else None
        f1, f2 = fits.get((ch, "hdmi1")), fits.get((ch, "hdmi2"))
        verdict, dD, sdD = "insufficient", None, None
        if f1 and f2 and dL is not None:
            dD, sdD = f1["mu"] - f2["mu"], (f1["sd"] ** 2 + f2["sd"] ** 2) ** 0.5
            frame_ok, delay_ok = abs(dD) <= max(1.5, 2 * sdD), abs(dD - dL) <= max(1.5, 2 * sdD)
            if frame_ok and not delay_ok:
                verdict = "H_frame (same delay both sticks)"
            elif delay_ok and not frame_ok:
                verdict = "H_delay (landing diff is real delay)"
            elif frame_ok and delay_ok:
                verdict = "undecided (need smaller sd)"
            else:
                verdict = "neither (sticks disagree)"
        row = dict(channel=ch, n_pairs=len(ps), dE_sd=L.r2(statistics.pstdev(dEs)) if len(dEs) >= 2 else None,
                   dE_range=L.r2(max(dEs) - min(dEs)) if dEs else None,
                   frame_residual_sd=L.r2(statistics.pstdev(res)) if len(res) >= 2 else None,
                   H_shared=(len(dEs) >= 2 and statistics.pstdev(dEs) <= 0.5), L_hdmi1=L.r2(l1), L_hdmi2=L.r2(l2),
                   dL=L.r2(dL), dD_edge=L.r2(dD), dD_sd=L.r2(sdD), verdict=verdict,
                   pass_=verdict.startswith("H_"),
                   would_need=None if (f1 and f2) else f"anchors on {'hdmi1' if not f1 else ''} {'hdmi2' if not f2 else ''}".strip())
        out.write("channel", **row)
        rows.append(row)
        print(f"   {ch:10s} pairs={len(ps):3d} dE_sd={row['dE_sd']} frame_res_sd={row['frame_residual_sd']} "
              f"L1={row['L_hdmi1']} L2={row['L_hdmi2']} dL={row['dL']} dD_edge={row['dD_edge']}+-{row['dD_sd']} "
              f"-> {verdict}" + (f" (WOULD need {row['would_need']})" if row["would_need"] else ""))
    tested = [r for r in rows if r["verdict"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_channels=len(tested), n_pass=len(ok),
              n_pairs=len(pairs))
    print(f"SUMMARY {ID}: {len(pairs)} concurrent pairs; transfer rule known for {len(ok)}/{len(tested)} channels -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
