"""Fetch the pinned eval dataset (eval-data.lock.json) into data/, verified, so every thread and CI
grades on the same nights. Stdlib only; the repo is public so no token is needed.

    python scripts/fetch_eval_data.py            # download, check sha256, extract, verify
    python scripts/fetch_eval_data.py --check    # only verify data/ against the pin (no network)
    python scripts/fetch_eval_data.py --tgz eval-archive.tgz   # use a tarball you already have

Extracting overwrites only the pinned files (data/archive/<league>/..., data/eval_config.yaml,
data/demo/<day>/, data/eval_pin.json); other nights in data/archive are left alone. On the Mac,
where data/ is the live service's, it refuses to replace a file whose content differs unless you
pass --force (the live recorder may have refreshed it; the pin wins for grading).
Why: Pins one eval dataset so every thread and CI grades identical nights (eval-data-v1 commit).
"""
import argparse
import hashlib
import json
import os
import ssl
import sys
import tarfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import evaldata  # noqa: E402

CACHE = Path(os.environ.get("RZ_EVAL_CACHE", Path.home() / ".cache" / "redzone-eval"))


def _ctx() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=ca) if ca and Path(ca).exists() else ssl.create_default_context()


def download(url: str, dest: Path, tries: int = 4) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "redzone-eval-fetch"})
            with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r, tmp.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.replace(dest)
            return
        except OSError as e:
            if i == tries - 1:
                raise SystemExit(f"download failed: {url}: {e}")
            time.sleep(2 ** (i + 1))


def safe_members(tar: tarfile.TarFile, lock: dict):
    """Regular files under data/ only: no links, no absolute paths, no '..'."""
    for m in tar.getmembers():
        p = PurePosixPath(m.name)
        if not m.isfile() or p.is_absolute() or ".." in p.parts or p.parts[0] != "data":
            raise SystemExit(f"refusing tarball member {m.name!r}")
        yield m


def extract(tgz: Path, lock: dict, root: Path, force: bool) -> None:
    with tarfile.open(tgz, "r:gz") as tar:
        members = list(safe_members(tar, lock))
        pin = json.load(tar.extractfile(f"data/{evaldata.PIN_NAME}"))
        if pin.get("tag") != lock["tag"] or pin.get("tree_sha256") != lock.get("tree_sha256"):
            raise SystemExit(f"tarball is {pin.get('tag')}, the lock says {lock['tag']}")
        clash = []
        for m in members:
            dst = root / m.name
            want = pin["hashes"].get(m.name)
            if dst.exists() and want and evaldata.sha256_file(dst) != want:
                clash.append(m.name)
        if clash and not force:
            raise SystemExit(f"{len(clash)} local files differ from the pin (e.g. {clash[0]}); "
                             "re-run with --force to replace them with the pinned copies")
        for m in members:
            dst = root / m.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(m).read()
            want = pin["hashes"].get(m.name)
            if want and hashlib.sha256(data).hexdigest() != want:
                raise SystemExit(f"{m.name} in the tarball doesn't match its pinned hash")
            tmp = dst.with_name(dst.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only, no download")
    ap.add_argument("--tgz", type=Path, help="a local eval-archive.tgz instead of downloading")
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root to extract into")
    ap.add_argument("--force", action="store_true", help="replace local files that differ from the pin")
    a = ap.parse_args()
    lock = evaldata.load_lock()
    if not lock:
        sys.exit(f"no {evaldata.LOCK.name} in this checkout")
    if a.check:
        ok, problems = evaldata.verify(a.root)
        print("ok:", lock["tag"] if ok else "\n  ".join(["NOT pinned:"] + problems))
        sys.exit(0 if ok else 1)
    ok, _ = evaldata.verify(a.root)
    if ok and not a.tgz:
        print("already pinned:", lock["tag"])
        return
    tgz = a.tgz
    if not tgz:
        CACHE.mkdir(parents=True, exist_ok=True)
        tgz = CACHE / f"{lock['tag']}-{lock['asset']}"
        if not tgz.exists() or evaldata.sha256_file(tgz) != lock["sha256"]:
            print("downloading", evaldata.asset_url(lock), f"({lock.get('bytes', 0) / 1e6:.0f} MB)")
            download(evaldata.asset_url(lock), tgz)
    got = evaldata.sha256_file(tgz)
    if got != lock["sha256"]:
        sys.exit(f"{tgz}: sha256 {got} != pinned {lock['sha256']}")
    extract(tgz, lock, a.root, a.force)
    ok, problems = evaldata.verify(a.root)
    if not ok:
        sys.exit("extracted but verify failed:\n  " + "\n  ".join(problems))
    print("ok:", lock["tag"], f"({lock.get('files')} files, window {json.dumps(lock['window'])})")


if __name__ == "__main__":
    main()
