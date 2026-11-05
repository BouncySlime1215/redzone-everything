#!/usr/bin/env python3
"""fusion_09_app_audit: is the live app's YTTV sync filter (data/sync.jsonl) as good as the fusion?

Hypothesis: the app's filter still runs on priors for YTTV (ESPN 29 +- 8, others 42 +- 15, never
'synced'), so its mu can be 10+ s off and its sd is 8-15 s; the fused D_now from fusion anchors should
replace it. Where the app IS synced, its mu must agree with fusion within 2 s.

Procedure: read data/sync.jsonl rows for youtubetv:* targets (kind tune/position/clock; mu, sd, synced,
uncertain, stick). Per channel: last mu/sd, fraction synced, n rows. Fusion side: Kalman per
(channel, stick) on all anchors (results/*.jsonl + recorded pods; READ-ONLY live polling for
--minutes) propagated to the latest session (D_now). Compare where both exist.

Pass rule (per channel): app synced with sd <= 2 AND |app mu - fusion D_now| <= 2 s, OR fusion sd <= 1 s
(fusion can replace the app's number). 'insufficient' when neither has data. Read-only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_09_app_audit"


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args, want_synthetic=False)
    L.header(ID, args, B["info"])
    app = {}
    for r in L.read_jsonl(os.path.join(L.DATA, "sync.jsonl")):
        tg = r.get("target") or ""
        if not tg.startswith("youtubetv:"):
            continue
        ch = L.norm_ch(tg)
        a = app.setdefault(ch, dict(n=0, synced=0, last=None, kinds={}))
        a["n"] += 1
        a["synced"] += bool(r.get("synced"))
        a["kinds"][r.get("kind")] = a["kinds"].get(r.get("kind"), 0) + 1
        a["last"] = r
    hosts = {v: k for k, v in L.SHORT.items()}
    fus = {}
    for (ch, st), g in L.group(L.usable(B["anchors"])).items():
        k = L.kalman(g, L.prior_for(ch))
        s = L.latest_session(B["sessions"], hosts.get(st, st), ch)
        fus.setdefault(ch, []).append(dict(stick=st, D_edge=k["mu"], sd=k["sd"], n=k["n"], D_now=L.d_now(k, s)))
    chans = sorted(set(app) | set(fus))
    if args.channel != "all":
        chans = [c for c in chans if c == L.norm_ch(args.channel)]
    rows = []
    for ch in chans:
        a, fl = app.get(ch), fus.get(ch, [])
        last = (a or {}).get("last") or {}
        best = min(fl, key=lambda f: f["sd"]) if fl else None
        diff = (last.get("mu") - best["D_now"]) if (best and best["D_now"] is not None and last.get("mu") is not None) else None
        app_ok = bool(last.get("synced")) and (last.get("sd") or 99) <= 2 and diff is not None and abs(diff) <= 2
        fus_ok = bool(best) and best["sd"] <= 1.0
        grade = "pass" if (app_ok or fus_ok) else ("insufficient" if not a and not fl else "fail")
        row = dict(channel=ch, app_n=(a or {}).get("n", 0), app_synced_frac=L.r2((a["synced"] / a["n"]) if a else None),
                   app_kinds=(a or {}).get("kinds"), app_mu=last.get("mu"), app_sd=last.get("sd"),
                   app_synced=last.get("synced"), app_last_t=L.fmt_t(last.get("t")),
                   fusion=[{k: L.r2(v) if isinstance(v, float) else v for k, v in f.items()} for f in fl],
                   diff_app_minus_fusion=L.r2(diff), grade=grade, pass_=grade == "pass")
        out.write("audit", **row)
        rows.append(row)
        fs = ", ".join(f"{f['stick']} {f['D_edge']:.1f}+-{f['sd']:.1f}(n{f['n']})" for f in fl) or "-"
        print(f"   {ch:9s} app: n={row['app_n']} synced={row['app_synced_frac']} last mu={row['app_mu']} sd={row['app_sd']} "
              f"| fusion: {fs} | diff={row['diff_app_minus_fusion']} -> {grade}")
    tested = [r for r in rows if r["grade"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_channels=len(tested), n_pass=len(ok))
    print(f"SUMMARY {ID}: {len(ok)}/{len(tested)} channels have a trustworthy (<=1-2 s) YTTV delay -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
