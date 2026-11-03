"""Build the pinned eval snapshot from a data/ folder that has the archive (the Mac, or a session
that copied it). Writes a deterministic tarball + .sha256 and rewrites eval-data.lock.json.

    .venv/bin/python scripts/build_eval_data.py --tag eval-data-v2 --out /tmp/evaldata \
        --window nfl:20250904:20261101 --window nba:20261001:20261101 --window nhl:20261001:20261101 \
        --demo 20261101

Then publish it as a release (docs/eval-data.md: push to the eval-data branch, whose workflow creates
the release) and commit the new eval-data.lock.json. Never overwrite an existing tag's asset: old PRs
pinned it.
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import evaldata  # noqa: E402


def _member(name: str, size: int, mode: int = 0o644) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = size, mode, 0, 0, 0, "", ""
    return ti


def build(data: Path, out: Path, tag: str, window: dict, demo_days: list[str]) -> dict:
    files = evaldata.select(data, window, demo_days)
    if not any(f.startswith("data/archive/") for f in files):
        sys.exit(f"no archive files under {data} in that window")
    if "data/eval_config.yaml" not in files:
        sys.exit(f"{data}/eval_config.yaml is missing: the scorecards would grade on defaults")
    import yaml
    if "providers" in (yaml.safe_load((data / "eval_config.yaml").read_text()) or {}):
        # The snapshot is public: account profile names don't belong in it, and grading never reads them.
        sys.exit(f"{data}/eval_config.yaml has a providers: block; remove it before publishing")
    base = data.parent
    hashes = {f: evaldata.sha256_file(base / f) for f in files}
    counts = {lg: sum(f.startswith(f"data/archive/{lg}/summary_") for f in files) for lg in window}
    pin = dict(tag=tag, asset="eval-archive.tgz", window=window, demo_days=demo_days, games=counts,
               files=len(files), tree_sha256=evaldata.tree_digest(hashes), hashes=hashes)
    out.mkdir(parents=True, exist_ok=True)
    tgz = out / "eval-archive.tgz"
    # Deterministic: sorted names, zero mtimes/owners, gzip header without a timestamp, so the
    # same data always gives the same sha256.
    with tgz.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        pin_bytes = json.dumps(pin, indent=1, sort_keys=True).encode()
        tar.addfile(_member(f"data/{evaldata.PIN_NAME}", len(pin_bytes)), io.BytesIO(pin_bytes))
        for f in files:
            p = base / f
            with p.open("rb") as fh:
                tar.addfile(_member(f, p.stat().st_size), fh)
    sha = hashlib.sha256(tgz.read_bytes()).hexdigest()
    (out / "eval-archive.tgz.sha256").write_text(f"{sha}  eval-archive.tgz\n")
    lock = {k: pin[k] for k in ("tag", "asset", "window", "demo_days", "games", "files", "tree_sha256")}
    lock.update(repo=evaldata.REPO, sha256=sha, bytes=tgz.stat().st_size)
    return lock


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--window", action="append", required=True, help="league:YYYYMMDD:YYYYMMDD")
    ap.add_argument("--demo", action="append", default=[], help="demo day YYYYMMDD (data/demo/<day>)")
    ap.add_argument("--no-lock", action="store_true", help="don't rewrite eval-data.lock.json")
    a = ap.parse_args()
    window = {}
    for w in a.window:
        lg, start, end = w.split(":")
        window[lg] = {"start": start, "end": end}
    lock = build(a.data.resolve(), a.out, a.tag, window, a.demo)
    text = json.dumps(lock, indent=1, sort_keys=True) + "\n"
    if not a.no_lock:
        evaldata.LOCK.write_text(text)
        evaldata.load_lock()  # validates what was just written
    print(text)


if __name__ == "__main__":
    main()
