#!/usr/bin/env python3
"""Push token shards to the exchange store, or pull them to a worker (compute exchange item b).

    python scripts/exchange/shards.py push DIR hf:BouncySlime1215/rz-shards [--set v2]      # needs HF_TOKEN
    python scripts/exchange/shards.py pull hf:BouncySlime1215/rz-shards DIR [--set v2] [--leagues nfl,nba]
    python scripts/exchange/shards.py manifest DIR                                          # local check

Layout in the store: <set>/<league>/<season>.npy + .json (the trainer's --tokens layout) and <set>/MANIFEST.json
(per shard: tokens, games, sha256, license). A shard without a license field is refused: every dataset carries
its license line (F8), and restrictive ones carry "personal-use, not for commercial release". The Kaggle mirror is
the same layout as a private Kaggle dataset (kaggle_push.py dataset), mounted read-only at /kaggle/input/<slug>.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import open_store  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest(root: Path) -> dict:
    out = {}
    for js in sorted(root.glob("*/*.json")):
        if js.name.endswith("_edges.json"):
            continue
        info = json.loads(js.read_text())
        npy = js.with_suffix(".npy")
        if not info.get("complete") or not npy.exists():
            continue
        if not info.get("license"):
            raise SystemExit(f"{js}: no license field; every shard needs its license line before it is shared")
        out[str(js.relative_to(root).with_suffix(""))] = {
            "tokens": info.get("tokens"), "games": info.get("games"), "vocab_version": info.get("vocab_version"),
            "license": info["license"], "sha256": sha256(npy)}
    return out


def push(root: Path, spec: str, set_name: str) -> dict:
    st = open_store(spec, "dataset")
    m = manifest(root)
    old = {}
    tmp = root / ".exchange_manifest.json"
    if st.get(f"{set_name}/MANIFEST.json", tmp):
        old = json.loads(tmp.read_text())
    for key, meta in m.items():
        if old.get(key, {}).get("sha256") == meta["sha256"]:
            continue                                   # unchanged: skip the upload
        st.put(root / f"{key}.npy", f"{set_name}/{key}.npy", f"shard {key}")
        st.put(root / f"{key}.json", f"{set_name}/{key}.json", f"index {key}")
    tmp.write_text(json.dumps({**old, **m}, indent=1))
    st.put(tmp, f"{set_name}/MANIFEST.json", "manifest")
    return m


def pull(spec: str, root: Path, set_name: str, leagues: list[str] | None = None) -> int:
    st = open_store(spec, "dataset")
    root.mkdir(parents=True, exist_ok=True)
    mpath = root / "MANIFEST.json"
    if not st.get(f"{set_name}/MANIFEST.json", mpath):
        raise SystemExit(f"{spec}: no {set_name}/MANIFEST.json")
    m = json.loads(mpath.read_text())
    n = 0
    for key, meta in m.items():
        if leagues and key.split("/")[0] not in leagues:
            continue
        npy = root / f"{key}.npy"
        if npy.exists() and sha256(npy) == meta["sha256"]:
            continue
        st.get(f"{set_name}/{key}.json", root / f"{key}.json")
        st.get(f"{set_name}/{key}.npy", npy)
        if sha256(npy) != meta["sha256"]:
            raise SystemExit(f"{key}: sha256 mismatch after download")
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("push", "pull", "manifest"))
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--set", default="v2")
    ap.add_argument("--leagues", default="")
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        print(json.dumps(manifest(Path(a.a)), indent=1))
    elif a.cmd == "push":
        print(len(push(Path(a.a), a.b, a.set)), "shards in the store")
    else:
        print(pull(a.a, Path(a.b), a.set, [x for x in a.leagues.split(",") if x]), "shards downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
