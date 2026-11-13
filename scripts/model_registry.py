#!/usr/bin/env python3
"""Command line for the model registry (redzone/models_rt/registry.py): register, promote, demote, list, resolve.

Why: WORKPLAN F-02 (2026-11-12): the job runner (F-03) and the evaluation harness (F-04) write one registry line
per run, and a person or the weekly review promotes; the app loads only promoted models.

    python scripts/model_registry.py register --from run.json      # one JSON object with every required field
    python scripts/model_registry.py promote RUN_ID [--note "weekly review 2026-11-20"]
    python scripts/model_registry.py demote RUN_ID --reason "regressed on fresh nights"
    python scripts/model_registry.py list                          # every run: verdict, promoted, artifact
    python scripts/model_registry.py resolve RUN_ID                # exit 1 unless promoted and the sha256 matches
    python scripts/model_registry.py hash FILE ...                 # sha256 of files, for the record's hash fields

Every write goes through scripts/results_ledger.py append (hash chain, flock); `--dir` picks another ledger folder.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from redzone.models_rt import registry as R  # noqa: E402


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, default=R.DEFAULT_DIR, help="ledger folder (default results/)")
    ap.add_argument("--root", type=Path, default=R.ROOT, help="folder artifact paths are relative to")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("register")
    p.add_argument("--from", dest="src", type=Path, required=True)
    p = sub.add_parser("promote")
    p.add_argument("run_id")
    p.add_argument("--note", default="")
    p = sub.add_parser("demote")
    p.add_argument("run_id")
    p.add_argument("--reason", required=True)
    sub.add_parser("list")
    p = sub.add_parser("resolve")
    p.add_argument("run_id")
    p = sub.add_parser("hash")
    p.add_argument("files", type=Path, nargs="+")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.cmd == "register":
            rec = R.register(json.loads(a.src.read_text()), base=a.dir)
            print(f"registered {rec['registry']['run_id']} at ledger row {rec['seq']}")
        elif a.cmd == "promote":
            rec = R.promote(a.run_id, base=a.dir, root=a.root, note=a.note)
            print(f"promoted {a.run_id} at ledger row {rec['seq']}")
        elif a.cmd == "demote":
            rec = R.demote(a.run_id, a.reason, base=a.dir)
            print(f"demoted {a.run_id} at ledger row {rec['seq']}")
        elif a.cmd == "list":
            for rid, st in R.state(a.dir).items():
                flag = "REVOKED" if st["revoked"] else ("promoted" if st["promoted"] else "-")
                print(f"{rid}\t{st['verdict']}\t{flag}\t{st['artifact_path']}\t{st['gpu_type']}\t{st['hours']}h")
        elif a.cmd == "resolve":
            print(R.resolve_promoted(a.run_id, base=a.dir, root=a.root))
        elif a.cmd == "hash":
            for f in a.files:
                print(f"{R.sha256_file(f)}  {f}")
    except (R.RegistryError, OSError, ValueError) as exc:
        print(f"model_registry: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
