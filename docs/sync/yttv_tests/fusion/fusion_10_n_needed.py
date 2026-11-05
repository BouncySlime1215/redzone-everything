#!/usr/bin/env python3
"""fusion_10_n_needed: how many anchors of each family do we need tonight to reach sd <= 1 s?

Hypothesis: a family's REAL scatter can be larger than its stated sd (e.g. ad-pod returns ~8 s,
UI clock ~0.5-1 s). With the empirical scatter s_f, the IV sd after n anchors is s_f / sqrt(n), so the
anchors needed for the 1 s goal is ceil(s_f^2). This decides which families are worth running during
games (a family with s_f = 8 s needs ~64 anchors: useless in one night).

Procedure: per (channel, stick, family) with >= 3 anchors: stated sd (median), empirical scatter
(1.4826 * MAD of D_edge around the Huber mean), calibration ratio = scatter / stated, n_needed for
sd <= 1.0 and <= 2.0, and a bootstrap (200 resamples, seeded) of the Huber mean at the current n.
Anchors: results/*.jsonl + recorded pods (READ-ONLY live polling for --minutes); dry run also on
flagged synthetic anchors.

Pass rule (per group): some family (or the fused set) already has n >= n_needed(1 s) with
calibration ratio <= 1.5 AND bootstrap sd <= 1.0 s. Read-only.
"""
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_10_n_needed"


def analyse(anchors, out, label, seed):
    rnd = random.Random(seed)
    rows = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        fams = L.group(g, key=lambda a: a.family)
        fams["*fused*"] = g
        frows = []
        for f, ga in sorted(fams.items()):
            if len(ga) < 3:
                frows.append(dict(family=f, n=len(ga), note="need >= 3"))
                continue
            xs, sds = [a.D_edge for a in ga], [a.sd_edge for a in ga]
            mu, sd, _ = L.huber(xs, sds)
            scat = 1.4826 * statistics.median([abs(x - mu) for x in xs])
            stated = statistics.median(sds)
            per = max(scat, stated)
            need1, need2 = math.ceil(per ** 2 / 1.0), math.ceil(per ** 2 / 4.0)
            boots = []
            for _ in range(200):
                idx = [rnd.randrange(len(xs)) for _ in xs]
                boots.append(L.huber([xs[i] for i in idx], [sds[i] for i in idx], iters=10)[0])
            bsd = statistics.pstdev(boots)
            ok = len(ga) >= need1 and (scat / stated if stated else 9) <= 1.5 and bsd <= 1.0
            frows.append(dict(family=f, n=len(ga), mu=L.r2(mu), stated_sd=L.r2(stated), scatter=L.r2(scat),
                              calib=L.r2(scat / stated) if stated else None, n_needed_1s=need1, n_needed_2s=need2,
                              boot_sd=L.r2(bsd), pass_=ok))
        ok = any(fr.get("pass_") for fr in frows)
        row = dict(channel=ch, stick=st, families=frows, pass_=ok, source=label, synthetic=label == "synthetic",
                   grade="pass" if ok else ("insufficient" if all("note" in fr for fr in frows) else "fail"))
        out.write("n_needed", **row)
        rows.append(row)
        print(f"   [{label}] {ch} {st}:")
        for fr in frows:
            if "note" in fr:
                print(f"        {fr['family']:16s} n={fr['n']} ({fr['note']})")
            else:
                print(f"        {fr['family']:16s} n={fr['n']:3d} stated={fr['stated_sd']} scatter={fr['scatter']} "
                      f"calib={fr['calib']} need(1s)={fr['n_needed_1s']} need(2s)={fr['n_needed_2s']} boot_sd={fr['boot_sd']}"
                      f"{' PASS' if fr['pass_'] else ''}")
    return rows


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    rows = analyse(L.usable(B["anchors"]), out, "real", args.seed)
    if B["synth"]:
        analyse(B["synth"], out, "synthetic", args.seed)
    tested = [r for r in rows if r["grade"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_groups=len(tested), n_pass=len(ok))
    print(f"SUMMARY {ID}: {len(ok)}/{len(tested)} real groups have enough anchors for sd<=1 s -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
