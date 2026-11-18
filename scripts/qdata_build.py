#!/usr/bin/env python3
"""Decisive-Q dataset CLI (task A-02): the training-night pool, shard builds, the tie-share table, verify, cost, data volume.

    python scripts/qdata_build.py pool --data-root data/archive                  # allowed training nights (fence + cutoff)
    python scripts/qdata_build.py build --shard-range 0 2 --master-seed 7 --data-root data/archive --out OUT
    python scripts/qdata_build.py ties OUT [--eps 1e-6] [--json]                 # tie share by padding / scenario / mix,
                                                                                 # also over executed options only
    python scripts/qdata_build.py verify OUT                                     # F-05 manifest re-hash (data_manifest.py's check)
    python scripts/qdata_build.py cost --data-root data/archive --cpu 50 --out OUT   # toy build under a CPU budget; extrapolation
    python scripts/qdata_build.py volume --data-root data/archive                # the files and MB the job needs uploaded

The library is redzone/lab/gym2/qdata.py, the resumable job entry redzone/lab/gym2/qdata_job.py, the runbook docs/qdata.md.
Only training nights are ever opened: fenced nights (locked 2025-12-12..2026-01-05, tuning Oct 2026), nights at or after
the cutoff and test-split nights are refused before any game file is read. Prints counts, hashes and sizes, never data.
Why: WORKPLAN v2 A-02: the coordinator needs the pool, the cost and the data volume measured before the Colab job.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from redzone.lab import datamanifest as dm  # noqa: E402
from redzone.lab import fence  # noqa: E402
from redzone.lab.gym2 import qdata as Q  # noqa: E402
from redzone.lab.gym2 import qdata_job as J  # noqa: E402

COLAB_VCPU = 2


def _pool(a) -> Q.NightPool:
    nights = [x for x in (a.nights or "").split(",") if x] or None
    return Q.night_pool(a.data_root, cutoff=a.cutoff, max_nfl=a.max_nfl, nights=nights)


def cmd_pool(a) -> int:
    p = _pool(a)
    games = sum(len(v) for v in p.nights.values())
    print(f"allowed training nights: {len(p.nights)} ({games} scheduled games); cutoff {p.cutoff}; max NFL per slate {p.max_nfl}")
    print(f"refused nights: {p.refused}")
    if p.nights:
        print(f"window: {min(p.nights)}..{max(p.nights)}; pool sha256 {p.digest[:16]}")
    return 0


def cmd_ties(a) -> int:
    rows = Q.all_rows(a.out)
    tab = Q.tie_table(rows, eps=a.eps)
    ex = Q.exec_tie_table(rows, eps=a.eps)
    if a.json:
        print(json.dumps(dict(tab, executed=ex), indent=1, sort_keys=True))
    else:
        print(Q.tie_table_text(tab) + "\n\n" + Q.exec_tie_table_text(ex))
    return 0


def cmd_verify(a) -> int:
    doc = json.loads((Path(a.out) / "qdata.manifest.json").read_text())
    bad = dm.verify_manifest(doc, a.out)
    if bad:
        print(f"VERIFY FAILED: {len(bad)} problem(s): " + "; ".join(bad[:5]), file=sys.stderr)
        return 1
    print(f"OK {doc['tag']}: files={doc['totals']['files']} rows={doc['totals']['rows']} bytes={doc['totals']['bytes']} "
          f"train_ok={doc['fence']['train_ok']} manifest {doc['manifest_sha256'][:16]}")
    return 0


def cmd_cost(a) -> int:
    """Slates in index order until the CPU budget is spent; every number printed is measured except the lines marked
    ESTIMATE, which extrapolate linearly from them."""
    p = _pool(a)
    if not p.nights:
        print("no training night here: nothing to measure", file=sys.stderr)
        return 2
    plan = Q.SlatePlan(p, a.master_seed, slates=a.slates, per_shard=a.per_shard, episode_min=a.episode_min, points=a.points,
                       execution=a.execution)
    loader = Q.QLoader(a.data_root)
    c0 = time.process_time()
    rows, slates, load_s, per = [], 0, 0.0, []
    while time.process_time() - c0 < a.cpu and slates < plan.slates:
        d = Q.draw_slate(p, plan.master_seed, slates)
        l0 = time.process_time()
        for lg, gid in zip(d.leagues, d.games):
            loader.game(lg, gid)
        l1 = time.process_time()
        r, s = Q.build_slate(d, loader, episode_min=plan.episode_min, points=plan.points, horizon=plan.horizon,
                             master_seed=plan.master_seed, execution=plan.execution)
        l2 = time.process_time()
        for x in r:
            x["shard"] = 0
        load_s += l1 - l0
        per.append((d.pad, len(r), l2 - l1))
        rows.extend(r)
        slates += 1
    cpu = time.process_time() - c0
    files = Q.shard_files(rows)
    nbytes = sum(len(b) for b in files.values())
    if a.out:
        for rel, data in files.items():
            Q.qd_atomic_write(Path(a.out) / rel, data)
    npt = len(rows)
    opts = sum(r["n_legal"] for r in rows)
    build = cpu - load_s
    print(f"MEASURED on this machine: {slates} slates, {npt} decision points, {sum(1 for x in per if x[1] == 0)} slates with none")
    print(f"  CPU {cpu:.1f} s (loading games {load_s:.1f} s, building {build:.1f} s); {cpu / max(1, slates):.3f} s per slate; "
          f"{build / max(1, npt):.4f} s per decision point (build only)")
    print(f"  legal options per point {opts / max(1, npt):.2f}; masked per point {sum(r['n_masked'] for r in rows) / max(1, npt):.2f}; "
          f"gzip bytes per point {nbytes / max(1, npt):.0f}")
    for pad in Q.QD_PADDINGS:
        xs = [x for x in per if x[0] == pad]
        if xs:
            print(f"  padding {pad:>2}: {len(xs)} slates, {sum(x[1] for x in xs) / len(xs):.1f} points per slate, "
                  f"{sum(x[2] for x in xs) / len(xs):.3f} s per slate")
    tab = Q.tie_table(rows)
    print("TOY tie table:\n" + Q.tie_table_text(tab))
    print("TOY executed-options table:\n" + Q.exec_tie_table_text(Q.exec_tie_table(rows)))
    n = a.target
    per_slate = cpu / max(1, slates)
    hours = per_slate * n / 3600.0
    print(f"ESTIMATE (linear from the toy, same machine speed, same night mix): {n} slates = {hours:.1f} CPU-hours, "
          f"{hours / COLAB_VCPU:.1f} wall hours on {COLAB_VCPU} Colab vCPUs run as {COLAB_VCPU} processes; "
          f"{npt / max(1, slates) * n:.0f} decision points; {nbytes / max(1, slates) * n / 1e6:.0f} MB of gzip shards")
    return 0


def cmd_volume(a) -> int:
    """Per allowed night: the scoreboard and summary files the job reads, and their MB (none of it is in git)."""
    p = _pool(a)
    root = Path(a.data_root)
    total, nfiles, lines = 0, 0, []
    for night in p.keys():
        seen_lg = sorted({x[1] for x in p.nights[night]})
        fs = [root / lg / f"scoreboard_{night}.json" for lg in seen_lg]
        fs += [root / x[1] / f"summary_{x[2]}.json" for x in p.nights[night]]
        b = sum(f.stat().st_size for f in fs if f.is_file())
        total += b
        nfiles += len(fs)
        lines.append(f"  {night}: {len(fs)} files ({', '.join(seen_lg)}), {b / 1e6:.1f} MB")
    print(f"allowed training nights {len(p.nights)}: {nfiles} files, {total / 1e6:.1f} MB to upload (relative paths "
          f"<league>/scoreboard_<night>.json and <league>/summary_<id>.json under the data root)")
    print("\n".join(lines))
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def pool_args(p):
        p.add_argument("--data-root", required=True)
        p.add_argument("--cutoff", default=Q.QD_CUTOFF)
        p.add_argument("--max-nfl", type=int, default=Q.QD_MAX_NFL)
        p.add_argument("--nights", default="", help="comma list: only these nights (each must pass the fence)")
    p = sub.add_parser("pool", help="allowed training nights")
    pool_args(p)
    sub.add_parser("build", help="shards [A, B): the job entry's arguments (qdata_job --help)", add_help=False)
    t = sub.add_parser("ties", help="tie-share table from shards")
    t.add_argument("out")
    t.add_argument("--eps", type=float, default=None)
    t.add_argument("--json", action="store_true")
    v = sub.add_parser("verify", help="re-hash the shards against OUT/qdata.manifest.json")
    v.add_argument("out")
    c = sub.add_parser("cost", help="toy build under a CPU budget, with the extrapolation")
    pool_args(c)
    c.add_argument("--cpu", type=float, default=50.0, help="CPU seconds to spend (the Mac's cap is 60 in total)")
    c.add_argument("--master-seed", type=int, default=1)
    c.add_argument("--slates", type=int, default=Q.QD_SLATES)
    c.add_argument("--per-shard", type=int, default=Q.QD_PER_SHARD)
    c.add_argument("--episode-min", type=float, default=Q.QD_EPISODE_MIN)
    c.add_argument("--points", type=int, default=Q.QD_POINTS)
    c.add_argument("--target", type=int, default=Q.QD_SLATES, help="slates to extrapolate to")
    c.add_argument("--out", default="", help="write the toy rows here (git-ignored scratch folder)")
    c.add_argument("--execution", default="a02", choices=Q.QD_EXECUTION, help="how forced jumps are run (docs/qdata.md)")
    vol = sub.add_parser("volume", help="files and MB the Colab job needs")
    pool_args(vol)
    return ap


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["build"]:
        return J.by_hand(argv[1:])
    a = parser().parse_args(argv)
    try:
        return {"pool": cmd_pool, "ties": cmd_ties, "verify": cmd_verify, "cost": cmd_cost, "volume": cmd_volume}[a.cmd](a)
    except fence.FenceError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
