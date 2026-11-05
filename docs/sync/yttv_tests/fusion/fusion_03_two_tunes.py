#!/usr/bin/env python3
"""fusion_03_two_tunes: do two deep-link tunes of the same (channel, stick) land at the same delay?

Hypothesis: every deep-link tune lands at the live edge at a fixed position L per (channel, stick)
(ESPN ~46,803 on .181, ~46,790 on .150, TNT ~46,792.7), so the landing delay D_edge is repeatable to
< 0.5 s and ONE calibration per (channel, stick) carries over to every later tune. Landings far from
the mode (e.g. 46,846 or 46,720) are not edge landings (resume, rejoin, scrub) and must be flagged,
never used as E_land.

Procedure:
  1. Sessions from recorded sync_probe (+ READ-ONLY live polling for --minutes; the live app and other
     families' tests do the tuning, this test only watches media_session on either stick).
  2. A fresh landing = first media_session update of a session whose position is in 46,000-47,500 s.
  3. Per (channel, stick): mode L (median), MAD-robust sd, outliers |L - median| > 3 s.
  4. Anchor cross-check: if >= 2 fresh sessions of the same (channel, stick) each hold anchors, compare
     the per-session D_edge means (they must agree within 1.5 s).

Pass rule (per group with >= 3 edge landings): robust sd(L) <= 0.5 s; and when the anchor cross-check is
available, max session-to-session D_edge difference <= 1.5 s. Read-only.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_03_two_tunes"


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args, want_synthetic=False)
    L.header(ID, args, B["info"])
    ch_f = L.norm_ch(args.channel) if args.channel != "all" else None
    groups = {}
    for s in B["sessions"]:
        if s.fresh and (ch_f is None or s.ch == ch_f):
            groups.setdefault((s.ch, s.stick), []).append(s)
    real = L.usable(B["anchors"])
    rows = []
    for (ch, st), ss in sorted(groups.items()):
        Ls = [s.L for s in ss]
        med = statistics.median(Ls)
        edge = [x for x in Ls if abs(x - med) <= 3.0]
        outl = [dict(t=L.fmt_t(s.t_land), L=L.r2(s.L)) for s in ss if abs(s.L - med) > 3.0]
        mad = statistics.median([abs(x - med) for x in edge]) * 1.4826 if edge else None
        sd = statistics.pstdev(edge) if len(edge) >= 2 else None
        # per-session anchor means
        per = []
        for s in ss:
            g = [a for a in real if a.channel == ch and a.stick == st and s.t0 - 5 <= a.t <= s.t1 + 5]
            if g:
                m, e = L.iv_mean([a.D_edge for a in g], [a.sd_edge for a in g])
                per.append(dict(t=L.fmt_t(s.t_land), n=len(g), D_edge=L.r2(m), sd=L.r2(e)))
        spread = (max(p["D_edge"] for p in per) - min(p["D_edge"] for p in per)) if len(per) >= 2 else None
        ok = len(edge) >= 3 and mad is not None and mad <= 0.5 and (spread is None or spread <= 1.5)
        row = dict(channel=ch, stick=st, n_landings=len(Ls), n_edge=len(edge), L_median=L.r2(med, 2),
                   L_sd=L.r2(sd), L_mad_sd=L.r2(mad), L_range=L.r2(max(edge) - min(edge)) if edge else None,
                   outliers=outl, session_anchor_means=per, anchor_spread=L.r2(spread),
                   pass_=ok, grade="pass" if ok else ("insufficient" if len(edge) < 3 else "fail"))
        out.write("landing", **row)
        rows.append(row)
        print(f"   {ch:10s} {st:6s} landings={len(Ls):3d} edge={len(edge):3d} L={row['L_median']} "
              f"sd={row['L_sd']} mad_sd={row['L_mad_sd']} range={row['L_range']} outliers={len(outl)} "
              f"anchor_sessions={len(per)} -> {row['grade']}")
    tested = [r for r in rows if r["grade"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_groups=len(tested), n_pass=len(ok))
    print(f"SUMMARY {ID}: {len(ok)}/{len(tested)} (channel, stick) groups land repeatably (<=0.5 s) -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
