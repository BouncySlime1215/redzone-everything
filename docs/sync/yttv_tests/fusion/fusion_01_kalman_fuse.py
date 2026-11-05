#!/usr/bin/env python3
"""fusion_01_kalman_fuse: one Kalman estimate of D_edge per (channel, stick) from every anchor family.

Hypothesis: the picture delay right after a deep-link tune (D_edge) is a near-constant per
(channel, stick); every anchor family (UI clock, ad pods, scoreline changes, ...) observes
D(t) = D_edge + (E(t) - E_land) with its own stated sd, so an inverse-variance Kalman filter with a
small random walk (0.5 s/sqrt(h)) and 3-sigma Huber gating reaches sd <= 1 s after a few good anchors.

Procedure:
  1. Load recorded sync_probe rows (+ --minutes of READ-ONLY media_session polling when live) and split
     each stick's YTTV playback into sessions; fresh deep-link landings give E_land.
  2. Load anchors: every other family's results/*.jsonl row carrying channel + D + sd, plus recorded
     ad-pod anchors (pod return vs restart play, sd ~8 s). Convert each to D_edge via the session E track.
  3. Per (channel, stick): Kalman from the app prior (ESPN 29+-8, others 42+-15), gate at 3 sigma.
  4. Write one 'estimate' row per (channel, stick) with D_edge, sd, n, NIS, current D (latest session),
     and the filter trace. In --dry-run also run the same filter on flagged synthetic anchors with known
     truth and report the error (never mixed into real estimates).

Pass rule (per channel, stick): n >= 3 real anchors, posterior sd <= 1.0 s (pass) / <= 2.0 s (marginal),
and NIS (mean squared innovation z) <= 3 (the stated sds are honest). Read-only: never presses keys.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_01_kalman_fuse"


def run_groups(anchors, sessions, out, label, truth=None):
    hosts = {v: k for k, v in L.SHORT.items()}
    rows = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        k = L.kalman(g, L.prior_for(ch))
        sess = L.latest_session(sessions, hosts.get(st, st), ch) if sessions else None
        gr = L.grade(k["sd"], k["n"])
        if gr == "pass" and k["nis"] is not None and k["nis"] > 3:
            gr = "fail_nis"
        row = dict(channel=ch, stick=st, D_edge=L.r2(k["mu"]), sd=L.r2(k["sd"]), n=k["n"], nis=L.r2(k["nis"]),
                   families=sorted({a.family for a in g}), D_now=L.r2(L.d_now(k, sess)), grade=gr,
                   pass_=gr == "pass", source=label, trace=k["trace"][-12:])
        if truth:
            row["truth"] = truth.get((ch, st))
            row["err"] = L.r2(k["mu"] - truth[(ch, st)]) if (ch, st) in truth else None
            row["synthetic"] = True
        out.write("estimate", **row)
        rows.append(row)
        print(f"   [{label}] {ch:7s} {st:6s} D_edge={row['D_edge']:>7} sd={row['sd']:>5} n={row['n']:>3} "
              f"nis={row['nis']} D_now={row['D_now']} -> {gr}" + (f"  err_vs_truth={row['err']}" if truth else ""))
    return rows


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    real = L.usable(B["anchors"])
    print(f"-- real anchors: {len(real)} (ambiguous dropped: {len(B['anchors']) - len(real)})")
    rows = run_groups(real, B["sessions"], out, "real")
    if not rows:
        print("   no real anchors yet: WOULD fuse anchors from results/*.jsonl once other families run live")
    srows = []
    if B["synth"]:
        print("-- synthetic self-check (known truth, flagged synthetic, never counted)")
        srows = run_groups(B["synth"], [], out, "synthetic", B["truth"])
    ok = [r for r in rows if r["pass_"]]
    out.write("summary", pass_=bool(rows) and len(ok) == len(rows), n_groups=len(rows), n_pass=len(ok),
              synth_max_err=max((abs(r["err"]) for r in srows if r.get("err") is not None), default=None))
    print(f"SUMMARY {ID}: {len(ok)}/{len(rows)} (channel, stick) groups meet sd<=1.0 s -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
