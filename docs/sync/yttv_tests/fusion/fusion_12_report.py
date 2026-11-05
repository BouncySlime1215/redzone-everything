#!/usr/bin/env python3
"""fusion_12_report: one table per channel from every YTTV test result (all families).

Hypothesis: n/a (generator). Its job is to make tonight's triage fast: which tests produced usable
anchors, what D each (channel, stick) has, how sure we are, and whether it meets the 1-2 s goal.

Procedure:
  1. Read every results/*.jsonl (fusion, ui, ads, mechanics, ... whatever exists).
  2. Anchor table: every row carrying channel + D + sd (tolerant keys) grouped by
     (channel, stick, family) -> Huber D, sd, n; plus the fused Kalman D_edge per (channel, stick)
     (same code as fusion_01) and the latest fusion_01 'estimate' row if present.
  3. Test table: the last 'summary'-like row of each result file (keys pass_/pass/passed/verdict),
     its mode (live / dry-run) and time.
  4. Print both tables, write them as JSONL rows to --out and as Markdown next to it (<out>.md).
Live rows are preferred; dry-run and synthetic rows are listed only with --dry-run (labelled).

Pass rule per (channel, stick): fused sd <= 1.0 s = PASS, <= 2.0 s = MARGINAL, else FAIL;
fewer than 3 anchors = INSUFFICIENT. The report itself exits 0 whenever it could read the folder.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_12_report"


def last_summaries(results_dir, include_dry):
    out = []
    for p in sorted(glob.glob(os.path.join(results_dir, "*.jsonl"))):
        if os.path.basename(p).startswith(ID):
            continue
        last, n = None, 0
        fdry = ".dryrun." in os.path.basename(p)
        for r in L.read_jsonl(p):
            n += 1
            if not isinstance(r, dict):
                continue
            r["dry_run"] = bool(r.get("dry_run") or r.get("mode") == "dry-run" or fdry)
            if r["dry_run"] and not include_dry:
                continue
            if any(k in r for k in ("pass_", "pass", "passed", "verdict")) and (r.get("kind") in (None, "summary", "result", "verdict")):
                last = r
        if last is None:
            out.append(dict(file=os.path.basename(p), rows=n, result="no summary row"))
            continue
        v = last.get("pass_", last.get("pass", last.get("passed", last.get("verdict"))))
        out.append(dict(file=os.path.basename(p), rows=n, result="PASS" if v is True else ("FAIL" if v is False else str(v)),
                        mode="dry-run" if last.get("dry_run") else "live", t=L.fmt_t(last.get("t"))))
    return out


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args, want_synthetic=False)
    L.header(ID, args, B["info"])
    anchors = L.usable(B["anchors"])
    if args.dry_run:   # include fusion_01's synthetic estimate demo only as labelled rows below
        pass
    md = [f"# YTTV delay report ({'DRY-RUN' if args.dry_run else 'live'})", "",
          "| channel | stick | D_edge (s) | sd (s) | n | families | D_now (s) | goal 1-2 s |",
          "|---|---|---|---|---|---|---|---|"]
    print(f"\n{'channel':9s} {'stick':6s} {'D_edge':>7s} {'sd':>5s} {'n':>3s}  {'D_now':>7s}  goal       families")
    hosts = {v: k for k, v in L.SHORT.items()}
    table = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        k = L.kalman(g, L.prior_for(ch))
        s = L.latest_session(B["sessions"], hosts.get(st, st), ch)
        gr = L.grade(k["sd"], k["n"]).upper()
        fams = {}
        for f, ga in L.group(g, key=lambda a: a.family).items():
            mu, sd, _ = L.huber([a.D_edge for a in ga], [a.sd_edge for a in ga])
            fams[f] = f"{mu:.1f}+-{sd:.1f}(n{len(ga)})"
        dn = L.d_now(k, s)
        row = dict(channel=ch, stick=st, D_edge=L.r2(k["mu"]), sd=L.r2(k["sd"]), n=k["n"], D_now=L.r2(dn), grade=gr,
                   families=fams, pass_=gr == "PASS")
        out.write("table", **row)
        table.append(row)
        fs = "; ".join(f"{a}={b}" for a, b in fams.items())
        print(f"{ch:9s} {st:6s} {k['mu']:7.2f} {k['sd']:5.2f} {k['n']:3d}  {dn if dn is None else round(dn, 2)!s:>7s}  {gr:10s} {fs}")
        md.append(f"| {ch} | {st} | {k['mu']:.2f} | {k['sd']:.2f} | {k['n']} | {fs} | {row['D_now']} | {gr} |")
    if not table:
        print("(no anchors yet: rows appear here as soon as any family writes channel + D + sd)")
        md.append("| - | - | - | - | 0 | no anchors yet | - | INSUFFICIENT |")
    # fusion_01 estimates (labelled, incl. synthetic in dry run)
    est = [r for r in L.read_jsonl(os.path.join(args.results_dir, "fusion_01_kalman_fuse.jsonl"))
           if r.get("kind") == "estimate" and (args.dry_run or not r.get("dry_run"))]
    if est:
        lastrun = est[-1]["run"]
        md += ["", f"## fusion_01 estimates (run {lastrun})", "", "| channel | stick | D_edge | sd | n | grade | source |", "|---|---|---|---|---|---|---|"]
        for r in est:
            if r["run"] == lastrun:
                md.append(f"| {r['channel']} | {r['stick']} | {r['D_edge']} | {r['sd']} | {r['n']} | {r['grade']} | {r.get('source')} |")
    tests = last_summaries(args.results_dir, args.dry_run)
    print(f"\n{'result file':42s} {'rows':>5s}  result        mode     time")
    md += ["", "## Tests", "", "| result file | rows | result | mode | time |", "|---|---|---|---|---|"]
    for t in tests:
        out.write("test", **t)
        print(f"{t['file']:42s} {t['rows']:5d}  {t['result']:12s}  {t.get('mode', '-'):8s} {t.get('t', '-')}")
        md.append(f"| {t['file']} | {t['rows']} | {t['result']} | {t.get('mode', '-')} | {t.get('t', '-')} |")
    mdp = os.path.splitext(args.out)[0] + ".md"
    with open(mdp, "w") as f:
        f.write("\n".join(md) + "\n")
    npass = sum(r["pass_"] for r in table)
    out.write("summary", pass_=bool(table) and npass == len(table), n_groups=len(table), n_pass=npass,
              n_result_files=len(tests), markdown=mdp)
    print(f"\nSUMMARY {ID}: {npass}/{len(table)} (channel, stick) at the 1 s goal; {len(tests)} result files -> {args.out} + {mdp}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
