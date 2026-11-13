#!/usr/bin/env python3
"""Data-lake manifests: build, verify, diff, list and package a pinned version of the archive, history or feed-arrival data.

Why: WORKPLAN v2 task F-05: a training run must pin exactly the files it trained on (the registry's data_manifest_hash), and a
notebook must refuse data that is not what the manifest says. Library and format: redzone/lab/datamanifest.py. Runbook:
docs/memory/runbooks/data-manifests.md.

    python scripts/data_manifest.py build archive --tag archive-nba-202610-v1 --start 20261020 --end 20261031 --leagues nba
    python scripts/data_manifest.py build feedlag --tag feedlag-20261112-v1          # also: history, frames (private)
    python scripts/data_manifest.py verify archive-nba-202610-v1                      # re-hash every file; exit 1 on any mismatch
    python scripts/data_manifest.py list [--verify]                                   # every manifest: tag, window, files, fence, hash
    python scripts/data_manifest.py diff TAG_A TAG_B                                  # added, removed, changed, moved; exit 1 if different
    python scripts/data_manifest.py package TAG [--out DIR] [--include-private-frames]   # upload layout, DRY RUN: nothing is uploaded

Data folders default to data/archive, data/history, data/feedlag and data/vision-v1 (override with --root or
$RZ_DATA_ROOT_<KIND>); manifests live in data/manifests (git-ignored; override with --dir or $RZ_DATA_MANIFESTS). Output names
files by manifest-relative path and prints hashes, sizes and counts, never file contents or absolute paths. A tag is a pin:
building it again with different content is refused, so a new version needs a new tag. Exit codes: 0 ok, 1 refused or mismatch.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from redzone.lab import datamanifest as dm  # noqa: E402
from redzone.lab import fence  # noqa: E402
from scripts import license_check  # noqa: E402
from scripts.history import licences as history_licences  # noqa: E402

TERMS_FILE = _REPO_DIR / "docs" / "datasources" / "terms.json"
PRIVATE_TAG = "private, local only, never committed or uploaded"
PRIVATE_FRAMES_LICENCE = {
    "terms_class": "private", "status": "local-only", "tag": PRIVATE_TAG,
    "licence": "Screen captures of licensed broadcasts. Private: local only, never in a repo, never uploaded by any tool; "
               "training copies them to Nick's own Colab or Drive by hand."}


def licence_entry(source: str) -> dict:
    """The licence record for one source: the history registry first, then docs/datasources/terms.json; unknown sources are refused."""
    if source == dm.FRAMES_SOURCE:
        return dict(PRIVATE_FRAMES_LICENCE)
    s = history_licences.SOURCES.get(source)
    if s is not None:
        return {"terms_class": s.terms_class, "status": s.status, "licence": s.licence,
                "tag": history_licences.PERSONAL_TAG if s.terms_class != "open" else ""}
    terms = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    cls = (terms.get("sources") or {}).get(source)
    if cls is None:
        raise dm.ManifestError(f"source {source!r} is not classified in docs/datasources/terms.json: ask the data scouts to classify it first")
    return {"terms_class": cls, "status": "terms-json", "licence": f"classified {cls} in docs/datasources/terms.json (F8)",
            "tag": terms.get("tag", history_licences.PERSONAL_TAG) if cls != "open" else ""}


class _Licences(dict):
    """Looks a source up on first use, so build asks only about the sources it met."""

    def get(self, key, default=None):          # noqa: D102
        if key not in self:
            self[key] = licence_entry(key)
        return self[key]


def short(h: str) -> str:
    return h[:12]


def describe(doc: dict) -> str:
    w = doc.get("window") or {}
    f = doc["fence"]["counts"]
    t = doc["totals"]
    return (f"{doc['tag']}: kind={doc['kind']}{' PRIVATE' if doc['private'] else ''} window={w.get('start', '-')}..{w.get('end', '-')} "
            f"files={t['files']} bytes={t['bytes']} rows={t['rows'] if t['rows'] is not None else '-'} sources={','.join(doc['sources'])} "
            f"fence(locked={f['locked']},tuning={f['tuning']},none={f['none']},unknown={f['unknown']}) train_ok={doc['fence']['train_ok']} "
            f"tree={short(doc['tree_sha256'])} manifest={short(doc['manifest_sha256'])}")


def show(problems: list[str], limit: int = dm.SHOWN_PROBLEMS) -> None:
    for p in problems[:limit]:
        print(f"  {p}", file=sys.stderr)
    if len(problems) > limit:
        print(f"  ... {len(problems) - limit} more", file=sys.stderr)


def resolve(target: str, directory: Path | None) -> tuple[Path, dict]:
    path = Path(target) if target.endswith(".json") else dm.find_manifest(target, directory)
    return path, dm.read_manifest(path)


def cmd_build(a: argparse.Namespace) -> int:
    root = a.root or dm.default_root(a.kind)
    leagues = [x for x in a.leagues.split(",") if x] if a.leagues else None
    doc, report = dm.build_manifest(a.kind, root, a.tag, licences=_Licences(), start=a.start, end=a.end, leagues=leagues,
                                    settle_seconds=a.settle_seconds)
    with tempfile.TemporaryDirectory() as td:       # the repo's own licence check (F8) on the manifest before it is written
        probe = Path(td) / "manifest.json"
        probe.write_text(dm.render(doc), encoding="utf-8")
        fails = [why for lvl, _, why in license_check.check([probe], json.loads(TERMS_FILE.read_text(encoding="utf-8"))) if lvl == "FAIL"]
    if fails:
        raise dm.ManifestError("license check failed: " + "; ".join(fails[:3]))
    dest, created = dm.write_manifest(doc, a.dir)
    print(describe(doc))
    print(f"{'wrote' if created else 'already pinned, identical:'} {dest.name}")
    skipped = {k: v for k, v in report["skipped"].items() if v}
    if skipped:
        print("left out: " + ", ".join(f"{k}={v}" for k, v in sorted(skipped.items())))
    for n in doc["notes"]:
        print(f"note: {n}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    _, doc = resolve(a.target, a.dir)
    root = a.root or (dm.default_root(doc["kind"]) if doc.get("kind") in dm.DATA_KINDS else None)
    problems = dm.verify_manifest(doc, root) if root is not None else dm.check_manifest(doc)
    if a.json:
        print(json.dumps({"tag": doc.get("tag"), "ok": not problems, "problems": problems[:200], "manifest_sha256": doc.get("manifest_sha256")}, indent=1))
    elif problems:
        print(f"VERIFY FAILED {doc.get('tag', a.target)}: {len(problems)} problem(s)", file=sys.stderr)
        show(problems)
    else:
        print(f"OK {describe(doc)}")
    return 1 if problems else 0


def cmd_list(a: argparse.Namespace) -> int:
    d = Path(a.dir) if a.dir else dm.default_manifest_dir()
    paths = sorted(p for p in d.glob("*.manifest.json") if p.is_file()) if d.is_dir() else []
    rows = []
    bad = 0
    for p in paths:
        try:
            doc = dm.read_manifest(p)
            row = {"tag": doc["tag"], "line": describe(doc)}
        except (dm.ManifestError, KeyError, TypeError):
            rows.append({"tag": p.name, "line": f"{p.name}: UNREADABLE manifest"})
            bad += 1
            continue
        if a.verify:
            problems = dm.verify_manifest(doc, a.root or dm.default_root(doc["kind"])) if doc.get("kind") in dm.DATA_KINDS else ["unknown kind"]
            row["ok"] = not problems
            row["line"] += "  VERIFIED" if not problems else f"  FAILED ({len(problems)} problem(s))"
            bad += bool(problems)
        rows.append(row)
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        print("\n".join(r["line"] for r in rows) if rows else "no manifests (build one: scripts/data_manifest.py build KIND --tag TAG)")
    return 1 if bad else 0


def cmd_diff(a: argparse.Namespace) -> int:
    (_, da), (_, db) = resolve(a.a, a.dir), resolve(a.b, a.dir)
    d = dm.diff_manifests(da, db)
    if a.json:
        print(json.dumps(d, indent=1))
    else:
        print(f"{d['tags'][0]} -> {d['tags'][1]}: " + ("identical" if d["same"] else
              f"added={len(d['added'])} removed={len(d['removed'])} changed={len(d['changed'])} moved={len(d['moved'])} unchanged={d['unchanged']}"))
        for k in ("added", "removed", "changed", "moved"):
            for item in d[k][:dm.SHOWN_PROBLEMS]:
                print(f"  {k}: {' -> '.join(item) if isinstance(item, list) else item}")
            if len(d[k]) > dm.SHOWN_PROBLEMS:
                print(f"  {k}: ... {len(d[k]) - dm.SHOWN_PROBLEMS} more")
        if d["metadata_changed"]:
            print("  metadata changed: " + ", ".join(d["metadata_changed"]))
    return 0 if d["same"] else 1


def cmd_package(a: argparse.Namespace) -> int:
    _, doc = resolve(a.target, a.dir)
    problems = dm.check_manifest(doc)
    if problems:
        raise dm.ManifestError(f"REFUSED package {a.target}: the manifest itself is not intact: " + "; ".join(problems[:3]))
    plan = dm.plan_bundle(doc, include_private=a.include_private_frames)
    written = dm.write_bundle_skeleton(doc, plan, a.out) if a.out else []
    if a.json:
        print(json.dumps(plan, indent=1, sort_keys=True))
    else:
        print(f"DRY RUN package {plan['tag']} (kind {plan['kind']}{', PRIVATE' if plan['private'] else ''}): {plan['files']} files, "
              f"{plan['bytes']} bytes, manifest {short(plan['manifest_sha256'])}. Nothing uploaded, no credentials used.")
        print(f"  kaggle: {plan['kaggle']['dataset-metadata.json']['id']} (private dataset)   drive: {plan['drive']['folder']}")
        print(f"  bundle layout: {plan['layout']['metadata']}, {plan['layout']['manifest']}, {plan['layout']['data_prefix']}<path>")
        for name, d in plan["dirs"].items():
            print(f"    {plan['layout']['data_prefix']}{'' if name == '.' else name + '/'}  {d['files']} files{' at the top' if name == '.' else ''}, {d['bytes']} bytes")
        print("  wrote skeleton: " + ", ".join(written) if written else "  (no --out: nothing written)")
        for s in plan["next_steps"]:
            print(f"  next: {s}")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser, root: bool = True) -> None:
        p.add_argument("--dir", type=Path, default=None, help="manifests folder (default data/manifests)")
        if root:
            p.add_argument("--root", type=Path, default=None, help="the data folder (default: data/<kind>)")

    b = sub.add_parser("build", help="pin a version of a data folder")
    b.add_argument("kind", choices=dm.DATA_KINDS)
    b.add_argument("--tag", required=True)
    b.add_argument("--start", help="first night, YYYYMMDD (inclusive)")
    b.add_argument("--end", help="last night, YYYYMMDD (inclusive)")
    b.add_argument("--leagues", help="comma list, e.g. nba,nhl")
    b.add_argument("--settle-seconds", type=float, default=dm.DEFAULT_SETTLE_SECONDS,
                   help="refuse files written more recently than this (they may still be growing); 0 turns the check off")
    common(b)
    v = sub.add_parser("verify", help="re-hash every file against a manifest")
    v.add_argument("target", help="a tag, or the path of a manifest file")
    v.add_argument("--json", action="store_true")
    common(v)
    ls = sub.add_parser("list", help="every manifest in the folder")
    ls.add_argument("--verify", action="store_true", help="also re-hash each one (slow for big data)")
    ls.add_argument("--json", action="store_true")
    common(ls)
    d = sub.add_parser("diff", help="what changed between two manifests")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--json", action="store_true")
    common(d, root=False)
    p = sub.add_parser("package", help="upload bundle layout for Kaggle and Drive (dry run)")
    p.add_argument("target", help="a tag, or the path of a manifest file")
    p.add_argument("--out", type=Path, default=None, help="write dataset-metadata.json, the manifest and LAYOUT.json here (git-ignored folder)")
    p.add_argument("--include-private-frames", action="store_true", help="allow a plan for private data (frames, picture data)")
    p.add_argument("--json", action="store_true")
    common(p, root=False)
    return ap


COMMANDS = {"build": cmd_build, "verify": cmd_verify, "list": cmd_list, "diff": cmd_diff, "package": cmd_package}


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except dm.ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except fence.FenceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
