#!/usr/bin/env python3
"""fusion_05_two_families: do independent anchor families agree on D_edge?

Hypothesis: two anchor families that share no inputs (e.g. the YTTV UI clock/progress bar vs. ad-pod
return vs. play-by-play restarts) measure the same D_edge for a (channel, stick). If they disagree by
more than their combined sd, one family has a bias (e.g. pod return lags the restart by the inbound
time) and must be corrected or dropped before it can enter the fused estimate.

Procedure: build D_edge anchors (results/*.jsonl from every family + recorded ad pods; READ-ONLY live
polling for --minutes). Per (channel, stick) and family: Huber estimate (mu, sd, n). For every pair of
families with n >= 2 each: diff, z = diff / sqrt(sd1^2 + sd2^2), and the implied bias of the weaker family.
Dry run also shows it on flagged synthetic anchors (3 families).

Pass rule (per pair): |diff| <= 2.0 s AND |z| <= 2. Group passes when every pair passes.
Read-only.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_05_two_families"


def agree(anchors, out, label):
    rows = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        fam = {}
        for f, ga in L.group(g, key=lambda a: a.family).items():
            mu, sd, _ = L.huber([a.D_edge for a in ga], [a.sd_edge for a in ga])
            fam[f] = dict(mu=mu, sd=sd, n=len(ga))
        pairs = []
        for (fa, a), (fb, b) in itertools.combinations(sorted(fam.items()), 2):
            if a["n"] < 2 or b["n"] < 2:
                continue
            d = a["mu"] - b["mu"]
            z = d / (a["sd"] ** 2 + b["sd"] ** 2) ** 0.5
            weak = fa if a["sd"] > b["sd"] else fb
            pairs.append(dict(a=fa, b=fb, diff=L.r2(d), z=L.r2(z), weaker=weak, bias_of_weaker=L.r2(d if weak == fa else -d),
                              pass_=abs(d) <= 2.0 and abs(z) <= 2))
        ok = bool(pairs) and all(p["pass_"] for p in pairs)
        row = dict(channel=ch, stick=st, families={k: {kk: L.r2(vv) for kk, vv in v.items()} for k, v in fam.items()},
                   pairs=pairs, pass_=ok, grade="pass" if ok else ("insufficient" if not pairs else "fail"),
                   source=label, synthetic=label == "synthetic")
        out.write("families", **row)
        rows.append(row)
        fs = " ".join(f"{k}={v['mu']:.1f}+-{v['sd']:.1f}(n{v['n']})" for k, v in fam.items())
        print(f"   [{label}] {ch:7s} {st:6s} {fs}")
        for p in pairs:
            print(f"        {p['a']} vs {p['b']}: diff={p['diff']} z={p['z']} -> {'ok' if p['pass_'] else 'DISAGREE'}")
        if not pairs:
            print("        (WOULD compare once two families each have >= 2 anchors here)")
    return rows


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    rows = agree(L.usable(B["anchors"]), out, "real")
    if B["synth"]:
        agree(B["synth"], out, "synthetic")
    tested = [r for r in rows if r["grade"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_groups=len(tested), n_pass=len(ok))
    print(f"SUMMARY {ID}: families agree in {len(ok)}/{len(tested)} real groups -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
