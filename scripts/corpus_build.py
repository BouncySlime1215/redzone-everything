#!/usr/bin/env python3
"""Corpus v1: tokenise the local play-by-play (B-01 history folder, recorded archive) with vocab v1 into hashed shards, then verify and report.

Why: WORKPLAN v2 task B-02: Track B trains only on shards whose manifest verifies by hash, whose games passed the leak fence and whose splits
are walk-forward by night. Library: redzone/lab/corpus/. Runbook: docs/memory/runbooks/corpus-v1.md.

    python scripts/corpus_build.py build --tag corpus-smoke-v1 --history-root data/history        # fenced nights excluded (default)
    python scripts/corpus_build.py build --tag corpus-smoke-v2 --history-root data/history --archive-root data/archive \\
        --archive-nights 20261021,20261022 --fenced hold            # tuning-window nights kept in train_ok=false shards
    python scripts/corpus_build.py verify data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json      # hashes, shards, fence, splits; exit 1
    python scripts/corpus_build.py stats  data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json [--json]
    python scripts/corpus_build.py show   data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json --league nba --role train [--game 0]

Output goes to data/corpus/<tag>/ (git-ignored) unless --out says otherwise. Reads only; writes only the output folder. A tag is a pin:
building it again gives the same bytes ("already built, identical") and a different corpus under the same tag is refused. Prints counts,
hashes and relative paths, never file contents or absolute paths. Exit codes: 0 ok, 1 refused or a check failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from redzone.lab import datamanifest as dm  # noqa: E402
from redzone.lab.corpus import build, manifest, reader, stats, walkforward  # noqa: E402
from redzone.lab.corpus import tokenise as tk  # noqa: E402
from scripts import license_check  # noqa: E402
from scripts.history.leagues import LEAGUES, parse_seasons  # noqa: E402

TERMS_FILE = _REPO_DIR / "docs" / "datasources" / "terms.json"


def _csv(s: str | None) -> set[str] | None:
    return {x.strip() for x in s.split(",") if x.strip()} if s else None


def _bad_leagues(found: set[str] | None) -> str | None:
    bad = sorted((found or set()) - set(LEAGUES))
    return f"unknown league(s) {bad}; known: {', '.join(LEAGUES)}" if bad else None


def cmd_build(a: argparse.Namespace) -> int:
    leagues = _csv(a.leagues)
    err = _bad_leagues(leagues)
    if err:
        print(err)
        return 1
    history = Path(a.history_root) if a.history_root else (dm.default_root("history") if dm.default_root("history").is_dir() else None)
    archive = Path(a.archive_root) if a.archive_root else None
    out = Path(a.out) if a.out else _REPO_DIR / "data" / "corpus" / a.tag
    cfg = build.BuildConfig(tag=a.tag, out=out, history_root=history, archive_root=archive, leagues=leagues,
                            seasons=set(parse_seasons(a.seasons)) if a.seasons else None, archive_nights=_csv(a.archive_nights),
                            fenced=a.fenced, valid_frac=a.valid_frac, holdout_frac=a.holdout_frac, gap_days=a.gap_days)
    try:
        doc = build.build_corpus(cfg, log=(lambda *_: None) if a.quiet else print)
    except dm.ManifestError as e:
        print(f"REFUSED: {e}")
        return 1
    t = doc["totals"]
    print(f"built {doc['tag']}: {len(doc['corpus']['shards'])} shards, {doc['corpus']['dedup']['games_kept']} games, "
          f"{t['rows']:,} events, {t['bytes'] / 1e6:.2f} MB")
    print(f"manifest_sha256 {doc['manifest_sha256']}")
    print(f"folder {out.name}/ holds {doc['tag']}{dm.MANIFEST_SUFFIX}; verify with: python scripts/corpus_build.py verify {out.name}/{doc['tag']}{dm.MANIFEST_SUFFIX}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    path = Path(a.manifest)
    try:
        doc = dm.read_manifest(path)
    except dm.ManifestError as e:
        print(f"REFUSED: {e}")
        return 1
    problems = manifest.verify_corpus(doc, Path(a.root) if a.root else path.parent)
    terms = json.loads(TERMS_FILE.read_text()) if TERMS_FILE.exists() else {}
    lic = [x for x in license_check.check([path.resolve()], terms) if x[0] == "FAIL"]
    problems += [f"licence: {where}: {why}" for _lvl, where, why in lic]
    for p in problems[:50]:
        print(f"FAIL {p}")
    if len(problems) > 50:
        print(f"... and {len(problems) - 50} more")
    shards = (doc.get("corpus") or {}).get("shards") or []
    print(f"corpus verify {doc.get('tag', path.name)}: {len(problems)} problem(s), {len(shards)} shards, {len(doc.get('files') or [])} files"
          + (f", manifest_sha256 {doc['manifest_sha256'][:16]}" if not problems else ""))
    return 1 if problems else 0


def cmd_stats(a: argparse.Namespace) -> int:
    path = Path(a.manifest)
    c = reader.load_corpus(path, Path(a.root) if a.root else None, verify=False)
    st = stats.compute(c.doc, c.root)
    if a.json:
        print(json.dumps(st, indent=1, sort_keys=True))
    else:
        print(stats.format_table(st))
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    c = reader.load_corpus(Path(a.manifest), Path(a.root) if a.root else None, verify=False)
    names = c.names()
    for sh in c.shards(roles=(a.role,), leagues=[a.league] if a.league else None, for_training=False):
        h = sh.header
        print(f"{sh.path.name}: {h['league']} {h['season']} {h['tier']} {h['role']} fence={h['fence']} train_ok={h['train_ok']} games={h['games']} "
              f"events={h['events']} providers={','.join(h['providers'])}")
        if a.game is not None and a.game < sh.games:
            row = sh.row(a.game)
            game = tk.detokenise_game(sh.game(a.game), names)
            print(f"  game {row['gid']} night {row['night']} provider {row['provider']}: {len(game['events'])} events, home {game['home']} away {game['away']}")
            for ev in game["events"][: a.events]:
                print("   ", ev)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="tokenise into shards with a manifest")
    b.add_argument("--tag", required=True)
    b.add_argument("--out", help="output folder (default data/corpus/<tag>)")
    b.add_argument("--history-root", help="B-01 history folder (default data/history when it exists)")
    b.add_argument("--archive-root", help="recorded archive folder (opt-in; its nights are nearly all fenced)")
    b.add_argument("--leagues", help="comma list, e.g. nba,cbb")
    b.add_argument("--seasons", help="e.g. 2025-2016 or 2024,2022")
    b.add_argument("--archive-nights", help="comma list of YYYYMMDD nights to read from the archive")
    b.add_argument("--fenced", choices=build.CORPUS_FENCE_MODES, default="exclude")
    b.add_argument("--valid-frac", type=float, default=walkforward.CORPUS_VALID_FRAC)
    b.add_argument("--holdout-frac", type=float, default=walkforward.CORPUS_HOLDOUT_FRAC)
    b.add_argument("--gap-days", type=int, default=0, help="embargo this many days before each cut")
    b.add_argument("--quiet", action="store_true")
    v = sub.add_parser("verify", help="re-hash every file and re-run the shard, fence and split proofs")
    v.add_argument("manifest")
    v.add_argument("--root", help="folder holding the shards (default: the manifest's folder)")
    s = sub.add_parser("stats", help="events per game by league and era, coverage, shard sizes")
    s.add_argument("manifest")
    s.add_argument("--root")
    s.add_argument("--json", action="store_true")
    w = sub.add_parser("show", help="list shards of a role and decode one game")
    w.add_argument("manifest")
    w.add_argument("--root")
    w.add_argument("--league")
    w.add_argument("--role", default="train", choices=("train", "valid", "holdout", "tuning"))
    w.add_argument("--game", type=int)
    w.add_argument("--events", type=int, default=8)
    a = ap.parse_args(argv)
    return {"build": cmd_build, "verify": cmd_verify, "stats": cmd_stats, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
