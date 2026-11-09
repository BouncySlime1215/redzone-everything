"""Shared engine for the Kaggle fetch scripts: resumable, disk-capped, sharded parquet plus one manifest row.

    from agent.kaggle import data_fetch   # used by agent/kaggle/fetch_<source>.py, run in a Kaggle notebook

Each fetch_<source>.py only says WHERE the data is (a SourceSpec: item list, licence, sampling rule). This
file does the rest: download with HTTP Range resume, read csv/parquet (also inside zip/tar/gz) as a stream,
write parquet shards, keep state.json so a new session skips finished items, stop before the disk cap or the
time budget, and write manifest_<source>.json/.parquet (source, license text + URL, rows, unique token
estimate, sha256, fetch date). Stdlib + pyarrow only. Never needs a secret or API key: every URL is public.
Why: data job 10 (docs/agents/data/2026-11-09-data-confirmation.md): approved sets must reach Kaggle at $0 by Nick's own launch.

A "token" is one distinct (column, value) pair of a string or integer column; the unique-token figure is a
k-minimum-values estimate (exact below K_MIN distinct values). Sampling is always a deterministic hash rule
(sampled_items, keep_hash_rows) and is written into the manifest as `sampling_rule`.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

GB = 1024 ** 3
DEFAULT_CAP_GB = 18.0
DEFAULT_MAX_HOURS = 11.0          # a Kaggle session ends at 12 h; stop clean, resume next session
SHARD_ROWS = 5_000_000
K_MIN = 2048
RETRIES = 4
CHUNK = 1 << 20


@dataclasses.dataclass
class Item:
    """One unit of resume: a shard group built from one or more URLs (or local paths)."""
    name: str
    urls: list[str]
    sizes: list[int | None] = dataclasses.field(default_factory=list)
    checksums: list[str | None] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class SourceSpec:
    name: str
    license_text: str
    license_url: str
    sampling_rule: str                     # "none (all rows)" or the exact rule, shown in the manifest
    list_items: Callable[["Fetcher", argparse.Namespace], list[Item]]
    row_filter: Callable[[pa.Table], pa.Table] | None = None
    csv_columns: list[str] | None = None   # headerless csv: column names
    csv_delimiter: str = ","
    all_strings: bool = False              # read csv as text (safe against type drift; cast downstream)
    add_args: Callable[[argparse.ArgumentParser], None] | None = None


class Fetcher:
    """What the engine needs from the network. Tests pass a fake with the same three methods."""

    def get_json(self, url: str):
        raise NotImplementedError

    def size(self, url: str) -> int | None:
        raise NotImplementedError

    def download(self, url: str, dest: Path, start: int) -> None:
        """Append the bytes of url from offset `start` to dest (create it when start == 0)."""
        raise NotImplementedError


class HttpFetcher(Fetcher):
    """Plain public HTTPS: no cookies, no tokens, Range for resume."""

    def _open(self, url: str, start: int = 0, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "redzone-data-fetch/1"})
        if start:
            req.add_header("Range", f"bytes={start}-")
        return urllib.request.urlopen(req, timeout=60)

    def get_json(self, url: str):
        with self._open(url) as r:
            return json.loads(r.read().decode("utf-8"))

    def size(self, url: str) -> int | None:
        try:
            with self._open(url, method="HEAD") as r:
                n = r.headers.get("Content-Length")
                return int(n) if n else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def download(self, url: str, dest: Path, start: int) -> None:
        try:
            r = self._open(url, start)
        except urllib.error.HTTPError as e:
            if e.code == 416 and start:     # already complete
                return
            raise
        with r:
            if start and r.status != 206:   # server ignored Range: restart the file
                start = 0
            with open(dest, "ab" if start else "wb") as f:
                while chunk := r.read(CHUNK):
                    f.write(chunk)


# ---------- sampling ----------

def hash_int(key: str, seed: int = 0) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big")


def sampled_items(items: list[Item], stride: int, seed: int = 0) -> list[Item]:
    """Keep ~1/stride of the items by name hash and order them by that hash, so ANY prefix (what a disk cap
    or a session limit leaves) is still a uniform sample, not just the oldest files."""
    keep = [i for i in items if hash_int(i.name, seed) % max(stride, 1) == 0]
    return sorted(keep, key=lambda i: hash_int(i.name, seed + 1))


def keep_hash_rows(col: pa.ChunkedArray | pa.Array, frac: float, seed: int = 0) -> pa.Array:
    """Boolean mask: keep rows whose key hashes into the first `frac` of the space. Same key, same answer, so
    whole entities (a drive, a user) stay together. Hashes only the distinct keys of the batch."""
    enc = pc.dictionary_encode(col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col)
    dictionary = enc.dictionary.to_pylist()
    limit = int(frac * (1 << 64))
    keep = pa.array([hash_int(str(k), seed) < limit for k in dictionary], type=pa.bool_())
    return pc.take(keep, enc.indices)


# ---------- unique-token sketch (k minimum values) ----------

class Sketch:
    def __init__(self, k: int = K_MIN, mins: list[int] | None = None):
        self.k = k
        self.mins = sorted(set(mins or []))[:k]

    def add_table(self, table: pa.Table) -> None:
        pool = set(self.mins)
        for name in table.column_names:
            col = table.column(name)
            if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)
                    or pa.types.is_integer(col.type)):
                continue
            for v in pc.unique(col).to_pylist():
                if v is not None:
                    pool.add(hash_int(f"{name}\x1f{v}") >> 1)
        self.mins = sorted(pool)[: self.k]

    def estimate(self) -> int:
        if len(self.mins) < self.k:
            return len(self.mins)
        return int((self.k - 1) * (1 << 63) / max(self.mins[-1], 1))


# ---------- reading ----------

def _open_csv(stream, spec: SourceSpec) -> Iterator[pa.Table]:
    read = pacsv.ReadOptions(block_size=64 << 20, column_names=spec.csv_columns)
    parse = pacsv.ParseOptions(delimiter=spec.csv_delimiter)
    conv = pacsv.ConvertOptions(strings_can_be_null=True)
    if spec.all_strings:
        if spec.csv_columns:
            conv = pacsv.ConvertOptions(column_types={c: pa.string() for c in spec.csv_columns})
        else:  # header names: peek the first line without consuming it
            head = stream.peek(1 << 16).split(b"\n", 1)[0]
            names = next(csv.reader(io.StringIO(head.decode("utf-8", "replace")), delimiter=spec.csv_delimiter))
            conv = pacsv.ConvertOptions(column_types={c: pa.string() for c in names})
    for batch in pacsv.open_csv(stream, read_options=read, parse_options=parse, convert_options=conv):
        yield pa.Table.from_batches([batch])


def _read_stream(name: str, opener: Callable[[], io.IOBase], spec: SourceSpec) -> Iterator[pa.Table]:
    low = name.lower()
    if low.endswith(".parquet"):
        with opener() as f:
            for batch in pq.ParquetFile(f).iter_batches(batch_size=1 << 18):
                yield pa.Table.from_batches([batch])
    elif low.endswith(".gz"):
        with opener() as raw, gzip.open(raw, "rb") as f:
            yield from _open_csv(io.BufferedReader(f, 1 << 20), spec)
    elif low.endswith((".csv", ".tsv", ".txt")):
        with opener() as f:
            yield from _open_csv(io.BufferedReader(f, 1 << 20), spec)


def read_tables(path: Path, spec: SourceSpec) -> Iterator[pa.Table]:
    """Stream tables out of a file or directory: parquet, csv, .gz, .zip and .tar(.gz) members, no extraction."""
    if path.is_dir():
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            yield from read_tables(p, spec)
        return
    low = path.name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if not info.is_dir():
                    yield from _read_stream(info.filename, lambda i=info: z.open(i), spec)
    elif low.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(path, "r|*") as t:
            for m in t:
                if m.isfile():
                    yield from _read_stream(m.name, lambda m=m: t.extractfile(m), spec)
    else:
        yield from _read_stream(path.name, lambda: open(path, "rb"), spec)


# ---------- state, shards, manifest ----------

def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, path)


class Run:
    """One session of one source. state.json in out_dir is the resume point; --resume-from reads an older one."""

    def __init__(self, spec: SourceSpec, args: argparse.Namespace, fetcher: Fetcher):
        self.spec, self.args, self.fetcher = spec, args, fetcher
        self.out = Path(args.out_dir) / spec.name
        self.shards = self.out / "shards"
        self.dl = self.out / "_download"
        self.cap = int(args.cap_gb * GB)
        self.deadline = time.monotonic() + args.max_hours * 3600
        self.shards.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        for base in (self.out, Path(self.args.resume_from) / self.spec.name if self.args.resume_from else None):
            if base and (base / "state.json").exists():
                return json.loads((base / "state.json").read_text())
        return {"items": {}, "failed": {}, "skipped": {}}

    def save(self) -> None:
        _write_json(self.out / "state.json", self.state)

    def used(self) -> int:
        return dir_bytes(self.shards) + dir_bytes(self.dl)

    def over_time(self) -> bool:
        return time.monotonic() > self.deadline

    # -- download one url, resumable --
    def fetch_one(self, url: str, size: int | None, checksum: str | None) -> Path:
        if url.startswith("/") or url.startswith("file://"):
            return Path(url.removeprefix("file://"))
        self.dl.mkdir(parents=True, exist_ok=True)
        dest = self.dl / hashlib.sha256(url.encode()).hexdigest()[:16]
        part = dest.with_name(dest.name + ".part")
        last: Exception | None = None
        for attempt in range(RETRIES):
            have = part.stat().st_size if part.exists() else 0
            if size is not None and have >= size:
                break
            try:
                self.fetcher.download(url, part, have)
                break
            except (urllib.error.URLError, OSError, ConnectionError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404, 410):
                    raise
                time.sleep(min(2 ** attempt, 30) if not self.args.no_sleep else 0)
        else:
            raise RuntimeError(f"download failed after {RETRIES} tries: {url}: {last}")
        got = part.stat().st_size if part.exists() else 0
        if size is not None and got != size:
            raise RuntimeError(f"size mismatch for {url}: got {got}, want {size}")
        if checksum and checksum.startswith("md5:"):
            h = hashlib.md5()
            with open(part, "rb") as f:
                while chunk := f.read(CHUNK):
                    h.update(chunk)
            if h.hexdigest() != checksum[4:]:
                part.unlink()
                raise RuntimeError(f"md5 mismatch for {url}")
        os.replace(part, dest.with_name(dest.name + _suffix(url)))
        return dest.with_name(dest.name + _suffix(url))

    # -- convert one item into shards --
    def build_item(self, idx: int, item: Item) -> dict:
        tmp = self.out / "_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        files: list[dict] = []
        rows_total, sketch = 0, Sketch()
        writer, schema_key, writer_rows, part_no = None, None, 0, 0
        truncated = False

        def close():
            nonlocal writer
            if writer is not None:
                writer.close()
                writer = None

        for n, url in enumerate(item.urls):
            size = item.sizes[n] if n < len(item.sizes) else None
            checksum = item.checksums[n] if n < len(item.checksums) else None
            path = self.fetch_one(url, size, checksum)
            try:
                for table in read_tables(path, self.spec):
                    if self.spec.row_filter is not None:
                        table = self.spec.row_filter(table)
                    if table.num_rows == 0:
                        continue
                    sketch.add_table(table)
                    key = str(table.schema)
                    while table.num_rows:
                        if writer is None or key != schema_key or writer_rows >= self.args.shard_rows:
                            close()
                            name = f"{self.spec.name}-{idx:05d}-{part_no:03d}.parquet"
                            part_no += 1
                            writer = pq.ParquetWriter(tmp / name, table.schema, compression="zstd")
                            schema_key, writer_rows = key, 0
                            files.append({"file": name, "rows": 0})
                        piece = table.slice(0, self.args.shard_rows - writer_rows)
                        table = table.slice(piece.num_rows)
                        writer.write_table(piece)
                        writer_rows += piece.num_rows
                        files[-1]["rows"] += piece.num_rows
                        rows_total += piece.num_rows
                    if self.used() + dir_bytes(tmp) > self.cap:
                        truncated = True
                        break
            finally:
                if not (url.startswith("/") or url.startswith("file://")):
                    path.unlink(missing_ok=True)
            if truncated:
                break
        close()
        for f in files:
            p = tmp / f["file"]
            f["sha256"], f["bytes"] = sha256_file(p), p.stat().st_size
            os.replace(p, self.shards / f["file"])
        shutil.rmtree(tmp, ignore_errors=True)
        return {"files": files, "rows": rows_total, "sketch": sketch.mins, "truncated": truncated}

    def run(self) -> dict:
        items = self.spec.list_items(self.fetcher, self.args)
        status = "complete"
        max_item = int(self.args.max_item_gb * GB) if self.args.max_item_gb else self.cap // 3
        for idx, item in enumerate(items):
            if item.name in self.state["items"] or item.name in self.state["skipped"]:
                continue
            known = sum(s for s in item.sizes if s) if item.sizes else 0
            if known > max_item:
                self.state["skipped"][item.name] = f"{known} bytes > per-item limit {max_item}"
                self.save()
                continue
            if self.over_time():
                status = "partial: time budget reached, run again to resume"
                break
            if self.used() + known > self.cap:
                status = "partial: disk cap reached"
                break
            try:
                done = self.build_item(idx, item)
            except Exception as e:  # one bad item (404, bad zip) must not lose the others
                self.state["failed"][item.name] = f"{type(e).__name__}: {e}"[:300]
                self.save()
                continue
            self.state["items"][item.name] = done
            self.state["failed"].pop(item.name, None)
            self.save()
            if done["truncated"]:
                status = "partial: disk cap reached inside an item (that item keeps only its leading rows)"
                break
        if status == "complete" and (self.state["failed"] or self.state["skipped"]):
            status = "partial: some items failed or were too large (see state.json)"
        return self.write_manifest(status, len(items))

    def write_manifest(self, status: str, n_items: int) -> dict:
        done = self.state["items"]
        shard_hashes = sorted(f["sha256"] for d in done.values() for f in d["files"])
        sketch = Sketch()
        pool = {m for d in done.values() for m in d["sketch"]}
        sketch.mins = sorted(pool)[: sketch.k]
        row = {
            "source": self.spec.name,
            "license_text": self.spec.license_text,
            "license_url": self.spec.license_url,
            "rows": sum(d["rows"] for d in done.values()),
            "unique_tokens_est": sketch.estimate(),
            "sha256": hashlib.sha256("\n".join(shard_hashes).encode()).hexdigest(),
            "fetch_date": dt.date.today().isoformat(),
            "sampling_rule": self.spec.sampling_rule,
            "status": status,
            "items_done": len(done),
            "items_total": n_items,
            "shards": len(shard_hashes),
            "bytes": sum(f["bytes"] for d in done.values() for f in d["files"]),
        }
        _write_json(self.out / f"manifest_{self.spec.name}.json", row)
        pq.write_table(pa.Table.from_pylist([row]), self.out / f"manifest_{self.spec.name}.parquet")
        return row


def _suffix(url: str) -> str:
    base = url.split("?")[0].lower()
    for s in (".tar.gz", ".csv.gz", ".tsv.gz", ".zip", ".tgz", ".tar", ".parquet", ".csv", ".gz"):
        if base.endswith(s):
            return s
    return ""


# ---------- shared listings ----------

def hf_items(f: Fetcher, repo: str, prefix: str, ext: str = ".parquet") -> list[Item]:
    """Files of a public Hugging Face dataset repo (no token): tree API, then /resolve/main URLs."""
    tree = f.get_json(f"https://huggingface.co/api/datasets/{repo}/tree/main/{prefix}?recursive=true")
    out = []
    for e in tree:
        if e.get("type") == "file" and e["path"].endswith(ext):
            out.append(Item(e["path"], [f"https://huggingface.co/datasets/{repo}/resolve/main/{e['path']}"],
                            [e.get("size")]))
    return sorted(out, key=lambda i: i.name)


def zenodo_items(f: Fetcher, record: int, want: Callable[[str], bool]) -> list[Item]:
    """Files of a public Zenodo record (API needs no key); carries the md5 so the download is verified."""
    meta = f.get_json(f"https://zenodo.org/api/records/{record}")
    out = []
    for e in meta.get("files", []):
        key = e["key"]
        if want(key):
            url = e.get("links", {}).get("self") or f"https://zenodo.org/records/{record}/files/{key}?download=1"
            out.append(Item(key, [url], [e.get("size")], [e.get("checksum")]))
    return sorted(out, key=lambda i: i.name)


def local_items(path: str, name: str) -> list[Item]:
    p = Path(path)
    files = sorted(x for x in p.rglob("*") if x.is_file()) if p.exists() else []
    return [Item(x.relative_to(p).as_posix() or name, [str(x)], [x.stat().st_size]) for x in files]


# ---------- command line ----------

def parser(spec: SourceSpec) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=f"Fetch {spec.name} into parquet shards (Kaggle notebook).")
    ap.add_argument("--out-dir", default="/kaggle/working/data")
    ap.add_argument("--cap-gb", type=float, default=DEFAULT_CAP_GB, help="disk cap for shards + downloads")
    ap.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS)
    ap.add_argument("--shard-rows", type=int, default=SHARD_ROWS)
    ap.add_argument("--max-item-gb", type=float, default=0.0, help="skip items bigger than this (default cap/3)")
    ap.add_argument("--resume-from", default="", help="a previous session's output dir (read-only state.json)")
    ap.add_argument("--no-sleep", action="store_true", help="tests: no retry backoff")
    if spec.add_args:
        spec.add_args(ap)
    return ap


def cli(spec: SourceSpec, argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    args = parser(spec).parse_args(argv)
    row = Run(spec, args, fetcher or HttpFetcher()).run()
    print(json.dumps(row, indent=1))
    return 0
