"""Job entry for the decisive-Q dataset (task A-02): build shards [A, B) with a checkpoint after every shard.

Two ways to run it (the same code builds the same bytes):

    python -m redzone.lab.gym2.qdata_job --shard-range 0 3 --master-seed 7 --data-root data/archive --out OUT
    python -m redzone.lab.gym2.qdata_job          # under the F-03 job worker: steps = shard indexes (unit protocol)

By hand: shards go to OUT/shards, each shard's summary (its done mark) to OUT/summaries; a shard already done for this
plan is skipped, so a killed run started again resumes. Then OUT/qdata.manifest.json (F-05 format, every shard file's
sha256 and row count) and OUT/qdata_summary.json (tie table, counts) are rewritten.

Under the F-03 job worker (docs/jobs.md; docs/qdata.md has a spec): RZ_JOB_STEP_START/END are the shard range of
the unit, RZ_JOB_CONFIG holds the plan (master_seed, slates, per_shard, data_root, out, max_nfl, cutoff, episode_min,
points, horizon, tag), RZ_JOB_WORK is the job's folder. The shard files are written under RZ_JOB_WORK/<out> (default
"qdata"), outside the worker's checkpoints, so a checkpoint never copies or re-hashes the growing data; the unit's
checkpoint (RZ_JOB_NEXT) gets state.json (the shards done, each file's sha256), qdata.manifest.json and
qdata_summary.json. A resumed unit re-checks every listed file's sha256 before it trusts a shard; a shard file left
half-written by a killed unit is simply rebuilt (deterministic, same bytes).

Exit codes: 0 done, 2 refused (fenced night, empty pool, bad config), 3 state mismatch.
Why: WORKPLAN v2 A-02 + F-03: the 60,000-slate build runs on Colab in resumable units, never on the Mac.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from redzone.lab.gym2 import qdata as Q


def plan_of(cfg: dict) -> Q.SlatePlan:
    pool = Q.night_pool(cfg["data_root"], cutoff=str(cfg.get("cutoff", Q.QD_CUTOFF)),
                        max_nfl=int(cfg.get("max_nfl", Q.QD_MAX_NFL)), nights=cfg.get("nights"))
    return Q.SlatePlan(pool, int(cfg["master_seed"]), slates=int(cfg.get("slates", Q.QD_SLATES)),
                       per_shard=int(cfg.get("per_shard", Q.QD_PER_SHARD)),
                       episode_min=float(cfg.get("episode_min", Q.QD_EPISODE_MIN)), points=int(cfg.get("points", Q.QD_POINTS)),
                       horizon=int(cfg.get("horizon", Q.QD_HORIZON)), mask_level=str(cfg.get("mask_level", "strict")))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def finish(out: Path, plan: Q.SlatePlan, tag: str, licences: dict | None = None) -> dict:
    """Rewrite the manifest and the summary over every shard file now in `out`."""
    from redzone.lab import datamanifest as dm
    doc = None
    if any((out / "shards").glob("qdata_s*_*.jsonl.gz")):      # (shards so far may hold no decision point at all)
        doc = Q.manifest_doc(out, tag, licences)
        Q.qd_atomic_write(out / "qdata.manifest.json", dm.render(doc).encode())
    tab = Q.tie_table(Q.all_rows(out))
    sums = sorted(out.glob("summaries/qdata_s*.json"))
    done = [json.loads(p.read_text()) for p in sums]
    summary = {"fmt": Q.QD_FORMAT, "plan": plan.knobs(), "plan_sha256": plan.digest(), "shards_done": [s["shard"] for s in done],
               "slates": sum(len(s["slates"]) for s in done), "void": sum(s["void"] for s in done),
               "rows": sum(s["rows"] for s in done), "manifest_sha256": doc["manifest_sha256"] if doc else None, "ties": tab,
               "pool": {"nights": len(plan.pool.nights), "refused": plan.pool.refused}}
    Q.qd_atomic_write(out / "qdata_summary.json", (Q.qd_canon(summary) + "\n").encode())
    return summary


def run_range(plan: Q.SlatePlan, a: int, b: int, out: Path, tag: str, data_root, *, licences: dict | None = None,
              log=print) -> dict:
    """Shards [a, b) by hand (each checkpointed by its summary; done ones skipped), then the manifest and summary."""
    Q.run_shards(plan, a, b, out, Q.QLoader(data_root), log=log)
    return finish(out, plan, tag, licences)


def _tag(cfg: dict) -> str:
    return str(cfg.get("tag") or f"qdata-m{int(cfg['master_seed'])}-v1")


def by_hand(argv) -> int:
    ap = argparse.ArgumentParser(description="Build decisive-Q shards [A, B) (resumable).")
    ap.add_argument("--shard-range", nargs=2, type=int, required=True, metavar=("A", "B"))
    ap.add_argument("--master-seed", type=int, required=True)
    ap.add_argument("--data-root", required=True, help="the archive folder (league folders of scoreboard_/summary_ files)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--slates", type=int, default=Q.QD_SLATES)
    ap.add_argument("--per-shard", type=int, default=Q.QD_PER_SHARD)
    ap.add_argument("--max-nfl", type=int, default=Q.QD_MAX_NFL)
    ap.add_argument("--cutoff", default=Q.QD_CUTOFF)
    ap.add_argument("--episode-min", type=float, default=Q.QD_EPISODE_MIN)
    ap.add_argument("--points", type=int, default=Q.QD_POINTS)
    ap.add_argument("--tag", default="")
    ap.add_argument("--nights", default="", help="comma list: only these nights (each must pass the fence)")
    a = ap.parse_args(argv)
    cfg = {"master_seed": a.master_seed, "data_root": a.data_root, "slates": a.slates, "per_shard": a.per_shard,
           "max_nfl": a.max_nfl, "cutoff": a.cutoff, "episode_min": a.episode_min, "points": a.points, "tag": a.tag,
           "nights": [x for x in a.nights.split(",") if x] or None}
    from redzone.lab import fence
    try:
        plan = plan_of(cfg)
    except fence.FenceError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if not plan.pool.nights:
        print("refused: no training night can make a slate under this data root", file=sys.stderr)
        return 2
    print(f"allowed training nights: {len(plan.pool.nights)} (refused {plan.pool.refused}); shards {plan.shards}")
    s = run_range(plan, a.shard_range[0], min(a.shard_range[1], plan.shards), Path(a.out), _tag(cfg), a.data_root)
    print(f"done: shards {s['shards_done'][:3]}..{s['shards_done'][-3:] if s['shards_done'] else ''} rows {s['rows']} "
          f"manifest {s['manifest_sha256'][:12]}")
    return 0


def under_worker() -> int:
    """The F-03 unit protocol: steps [start, end) = shards; state in RZ_JOB_STATE / RZ_JOB_NEXT."""
    start, end = int(os.environ["RZ_JOB_STEP_START"]), int(os.environ["RZ_JOB_STEP_END"])
    state_dir, nxt = os.environ.get("RZ_JOB_STATE", ""), Path(os.environ["RZ_JOB_NEXT"])
    cfg = json.loads(Path(os.environ["RZ_JOB_CONFIG"]).read_text())
    work = Path(os.environ.get("RZ_JOB_WORK") or nxt.parent)
    out = work / str(cfg.get("out", "qdata"))
    root = cfg["data_root"]
    cfg["data_root"] = root if os.path.isabs(root) else str(work / root) if (work / root).is_dir() else root
    from redzone.lab import fence
    try:
        plan = plan_of(cfg)
    except fence.FenceError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if not plan.pool.nights:
        print("refused: no training night can make a slate under this data root", file=sys.stderr)
        return 2
    print(f"allowed training nights: {len(plan.pool.nights)} (refused {plan.pool.refused})", flush=True)
    done: dict = {}
    if state_dir:
        st = json.loads((Path(state_dir) / "state.json").read_text())
        if st.get("plan_sha256") != plan.digest() or st.get("step") != start:
            print(f"qdata: state is for another plan or step ({st.get('step')} vs {start})", file=sys.stderr)
            return 3
        done = {int(k): v for k, v in st["files"].items()}
        for k, files in done.items():          # trust a shard only if every file still re-hashes
            if not all((out / f).is_file() and _sha(out / f) == h for f, h in files.items()):
                print(f"qdata: shard {k}'s files changed since the checkpoint; rebuilding it", file=sys.stderr)
                (out / f"summaries/qdata_s{k:05d}.json").unlink(missing_ok=True)
                Q.run_shards(plan, k, k + 1, out, Q.QLoader(cfg["data_root"]))
    elif start:
        print("qdata: no state for a unit that does not start at 0", file=sys.stderr)
        return 3
    loader = Q.QLoader(cfg["data_root"])
    for k in range(start, min(end, plan.shards)):
        (out / f"summaries/qdata_s{k:05d}.json").unlink(missing_ok=True)   # a unit always rebuilds its own shards
        Q.run_shards(plan, k, k + 1, out, loader, log=lambda m: print(m, flush=True))
        s = json.loads((out / f"summaries/qdata_s{k:05d}.json").read_text())
        done[k] = {f: _sha(out / f) for f in s["files"]} | {f"summaries/qdata_s{k:05d}.json": _sha(out / f"summaries/qdata_s{k:05d}.json")}
    summary = finish(out, plan, _tag(cfg), cfg.get("licences"))
    for name in ("qdata.manifest.json", "qdata_summary.json"):
        if (out / name).is_file():
            Q.qd_atomic_write(nxt / name, (out / name).read_bytes())
    state = {"plan_sha256": plan.digest(), "step": end, "files": {str(k): done[k] for k in sorted(done)},
             "manifest_sha256": summary["manifest_sha256"]}
    Q.qd_atomic_write(nxt / "state.json", (Q.qd_canon(state) + "\n").encode())
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv and "RZ_JOB_NEXT" in os.environ:
        return under_worker()
    return by_hand(argv)


if __name__ == "__main__":
    sys.exit(main())
