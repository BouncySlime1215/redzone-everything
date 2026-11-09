"""Tests for the Kaggle fetch scripts with fake inputs and a fake fetcher: no network, no download.

Why: data job 10: the scripts run once, unattended, in Kaggle, so resume, disk cap, sampling and the manifest must be proven here.
"""
from __future__ import annotations

import gzip
import importlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from agent.kaggle import data_fetch as df

SOURCES = ("kuairand27k", "kuairec", "zvuk", "gdelt", "wikimedia", "backblaze", "lichess", "criteo", "azure")


class FakeFetcher(df.Fetcher):
    def __init__(self, blobs: dict[str, bytes], jsons: dict[str, object] | None = None, break_once: set[str] = ()):
        self.blobs, self.jsons, self.break_once = blobs, jsons or {}, set(break_once)
        self.calls: list[tuple[str, int]] = []

    def get_json(self, url):
        return self.jsons[url]

    def size(self, url):
        return len(self.blobs[url]) if url in self.blobs else None

    def download(self, url, dest, start):
        self.calls.append((url, start))
        if url not in self.blobs:
            raise df.urllib.error.HTTPError(url, 404, "nope", None, None)
        data = self.blobs[url][start:]
        with open(dest, "ab" if start else "wb") as f:
            if url in self.break_once:
                self.break_once.discard(url)
                f.write(data[: len(data) // 2])
                raise ConnectionError("dropped")
            f.write(data)


def csv_bytes(n: int, start: int = 0) -> bytes:
    return ("id,user,val\n" + "".join(f"{i},u{i % 7},{i * 2}\n" for i in range(start, start + n))).encode()


def zip_of(**members: bytes) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return b.getvalue()


def spec_for(items, **kw) -> df.SourceSpec:
    base = dict(name="fake", license_text="CC0 test", license_url="https://example.test/l",
                sampling_rule="none (test)", list_items=lambda f, a: items, all_strings=True)
    base.update(kw)
    return df.SourceSpec(**base)


def run(spec, tmp_path, fetcher, *extra) -> dict:
    args = df.parser(spec).parse_args(["--out-dir", str(tmp_path), "--no-sleep", *extra])
    return df.Run(spec, args, fetcher).run()


def read_rows(tmp_path: Path, name: str = "fake") -> int:
    return sum(pq.read_metadata(p).num_rows for p in (tmp_path / name / "shards").glob("*.parquet"))


def test_zip_item_to_shards_and_manifest(tmp_path):
    blob = zip_of(**{"a.csv": csv_bytes(250), "b.csv": csv_bytes(50, 250)})
    f = FakeFetcher({"u://z.zip": blob})
    row = run(spec_for([df.Item("z", ["u://z.zip"], [len(blob)])]), tmp_path, f, "--shard-rows", "100")
    assert row["rows"] == 300 == read_rows(tmp_path)
    assert row["shards"] >= 3 and row["status"] == "complete"
    for key in ("source", "license_text", "license_url", "rows", "unique_tokens_est", "sha256", "fetch_date"):
        assert row[key] not in ("", None)
    on_disk = json.loads((tmp_path / "fake" / "manifest_fake.json").read_text())
    assert on_disk == row
    assert pq.read_table(tmp_path / "fake" / "manifest_fake.parquet").num_rows == 1
    assert not list((tmp_path / "fake" / "_download").glob("*"))      # download deleted after use


def test_tar_gz_and_gz_csv(tmp_path):
    t = io.BytesIO()
    with tarfile.open(fileobj=t, mode="w:gz") as tar:
        data = csv_bytes(40)
        info = tarfile.TarInfo("logs.csv")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    f = FakeFetcher({"u://t.tar.gz": t.getvalue(), "u://c.csv.gz": gzip.compress(csv_bytes(10))})
    items = [df.Item("t", ["u://t.tar.gz"]), df.Item("c", ["u://c.csv.gz"])]
    assert run(spec_for(items), tmp_path, f)["rows"] == 50


def test_headerless_tsv_with_names(tmp_path):
    raw = zip_of(**{"d.CSV": b"1\tx\n2\ty\n"})
    spec = spec_for([df.Item("d", ["u://d.zip"])], csv_columns=["a", "b"], csv_delimiter="\t")
    assert run(spec, tmp_path, FakeFetcher({"u://d.zip": raw}))["rows"] == 2
    assert pq.read_table(next((tmp_path / "fake" / "shards").glob("*.parquet"))).column_names == ["a", "b"]


def test_resume_after_session_limit_skips_done_items(tmp_path):
    blobs = {f"u://{i}.csv.gz": gzip.compress(csv_bytes(20, i * 20)) for i in range(4)}
    items = [df.Item(str(i), [f"u://{i}.csv.gz"]) for i in range(4)]
    first = run(spec_for(items), tmp_path, FakeFetcher(blobs), "--max-hours", "0")
    assert first["status"].startswith("partial") and first["items_done"] == 0
    f2 = FakeFetcher(blobs)
    # simulate a session that finished 2 items then stopped
    run(spec_for(items[:2]), tmp_path, f2)
    f3 = FakeFetcher(blobs)
    final = run(spec_for(items), tmp_path, f3)
    assert {u for u, _ in f3.calls} == {"u://2.csv.gz", "u://3.csv.gz"}
    assert final["rows"] == 80 == read_rows(tmp_path) and final["status"] == "complete"


def test_resume_from_previous_output_dir(tmp_path):
    blobs = {f"u://{i}.csv.gz": gzip.compress(csv_bytes(5)) for i in range(2)}
    items = [df.Item(str(i), [f"u://{i}.csv.gz"]) for i in range(2)]
    run(spec_for(items[:1]), tmp_path / "s1", FakeFetcher(blobs))
    f = FakeFetcher(blobs)
    row = run(spec_for(items), tmp_path / "s2", f, "--resume-from", str(tmp_path / "s1"))
    assert [u for u, _ in f.calls] == ["u://1.csv.gz"]
    assert row["items_done"] == 2


def test_interrupted_download_resumes_with_range(tmp_path):
    blob = zip_of(**{"a.csv": csv_bytes(2000)})
    f = FakeFetcher({"u://z.zip": blob}, break_once={"u://z.zip"})
    row = run(spec_for([df.Item("z", ["u://z.zip"], [len(blob)])]), tmp_path, f)
    assert row["rows"] == 2000
    assert f.calls[0][1] == 0 and f.calls[1][1] > 0            # second try starts at the saved offset


def test_bad_item_is_recorded_and_others_continue(tmp_path):
    f = FakeFetcher({"u://ok.csv.gz": gzip.compress(csv_bytes(5))})
    items = [df.Item("missing", ["u://missing.csv.gz"]), df.Item("ok", ["u://ok.csv.gz"])]
    row = run(spec_for(items), tmp_path, f)
    assert row["rows"] == 5 and row["status"].startswith("partial")
    assert "missing" in json.loads((tmp_path / "fake" / "state.json").read_text())["failed"]


def test_md5_mismatch_fails_the_item(tmp_path):
    blob = gzip.compress(csv_bytes(5))
    items = [df.Item("x", ["u://x.csv.gz"], [len(blob)], ["md5:" + "0" * 32])]
    row = run(spec_for(items), tmp_path, FakeFetcher({"u://x.csv.gz": blob}))
    assert row["rows"] == 0 and "md5" in json.dumps(json.loads((tmp_path / "fake" / "state.json").read_text()))


def test_disk_cap_stops_before_and_inside_items(tmp_path):
    blobs = {f"u://{i}.csv.gz": gzip.compress(csv_bytes(3000, i * 3000)) for i in range(3)}
    items = [df.Item(str(i), [f"u://{i}.csv.gz"], [len(blobs[f"u://{i}.csv.gz"])]) for i in range(3)]
    row = run(spec_for(items), tmp_path, FakeFetcher(blobs), "--cap-gb", "0.00002")
    assert row["status"].startswith("partial") and row["items_done"] < 3
    assert df.dir_bytes(tmp_path / "fake" / "shards") < 0.00002 * df.GB + 40_000   # one shard of slack at most


def test_item_over_per_item_limit_is_skipped_and_said(tmp_path):
    blob = gzip.compress(csv_bytes(5))
    items = [df.Item("big", ["u://b.csv.gz"], [10 * df.GB])]
    row = run(spec_for(items), tmp_path, FakeFetcher({"u://b.csv.gz": blob}))
    assert row["rows"] == 0 and row["status"].startswith("partial")
    assert "big" in json.loads((tmp_path / "fake" / "state.json").read_text())["skipped"]


def test_local_input_is_read_in_place_and_not_deleted(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "a.csv").write_bytes(csv_bytes(30))
    row = run(spec_for(df.local_items(str(src), "x")), tmp_path / "out", FakeFetcher({}))
    assert row["rows"] == 30 and (src / "a.csv").exists()


def test_parquet_input(tmp_path):
    import pyarrow as pa
    buf = io.BytesIO()
    pq.write_table(pa.table({"k": ["a", "b", "a"], "n": [1, 2, 3]}), buf)
    row = run(spec_for([df.Item("p", ["u://p.parquet"])]), tmp_path, FakeFetcher({"u://p.parquet": buf.getvalue()}))
    assert row["rows"] == 3 and row["unique_tokens_est"] == 5       # k:a k:b n:1 n:2 n:3


def test_sketch_exact_when_small_and_close_when_large():
    import pyarrow as pa
    s = df.Sketch()
    s.add_table(pa.table({"a": [str(i) for i in range(500)] * 2}))
    assert s.estimate() == 500
    big = df.Sketch()
    big.add_table(pa.table({"a": [str(i) for i in range(60_000)]}))
    assert abs(big.estimate() - 60_000) / 60_000 < 0.1


def test_sampled_items_is_deterministic_and_any_prefix_is_a_sample():
    items = [df.Item(f"f{i:03d}", ["u"]) for i in range(400)]
    a, b = df.sampled_items(items, 10), df.sampled_items(items, 10)
    assert [i.name for i in a] == [i.name for i in b]
    assert 20 <= len(a) <= 60
    first_half = sorted(i.name for i in a[: len(a) // 2])
    assert first_half[-1] > "f200"                       # not just the oldest files


def test_keep_hash_rows_keeps_whole_entities():
    import pyarrow as pa
    col = pa.array([f"d{i % 50}" for i in range(1000)])
    mask = df.keep_hash_rows(col, 0.3).to_pylist()
    kept = {v for v, m in zip(col.to_pylist(), mask) if m}
    assert all(m == (v in kept) for v, m in zip(col.to_pylist(), mask))
    assert 5 <= len(kept) <= 25


def test_hf_and_zenodo_listings():
    f = FakeFetcher({}, {
        "https://huggingface.co/api/datasets/o/r/tree/main/data?recursive=true": [
            {"type": "file", "path": "data/a.parquet", "size": 5}, {"type": "file", "path": "README.md", "size": 1},
            {"type": "directory", "path": "data/x"}],
        "https://zenodo.org/api/records/9": {"files": [
            {"key": "Big-27K.tar.gz", "size": 7, "checksum": "md5:ab", "links": {"self": "https://z/api/f"}},
            {"key": "notes.pdf", "size": 1}]}})
    hf = df.hf_items(f, "o/r", "data")
    assert [(i.name, i.sizes) for i in hf] == [("data/a.parquet", [5])]
    assert hf[0].urls == ["https://huggingface.co/datasets/o/r/resolve/main/data/a.parquet"]
    z = df.zenodo_items(f, 9, lambda k: k.endswith(".tar.gz"))
    assert z[0].checksums == ["md5:ab"] and z[0].urls == ["https://z/api/f"]


@pytest.mark.parametrize("name", SOURCES)
def test_every_spec_is_complete_and_secret_free(name):
    mod = importlib.import_module(f"agent.kaggle.fetch_{name}")
    spec = mod.SPEC
    assert spec.name == name and spec.license_text and spec.license_url.startswith("https://")
    assert spec.sampling_rule
    text = Path(mod.__file__).read_text().lower()
    for bad in ("authorization", "api_key", "apikey", "password", "bearer", "hf_token", "kaggle.json"):
        assert bad not in text
    assert df.DEFAULT_CAP_GB == 18.0
    assert df.parser(spec).parse_args([]).cap_gb == 18.0


def test_gdelt_columns_days_and_groups():
    from agent.kaggle import fetch_gdelt as g
    assert len(g.COLUMNS) == 58 and g.COLUMNS[0] == "GLOBALEVENTID" and g.COLUMNS[-1] == "SOURCEURL"
    args = df.parser(g.SPEC).parse_args(["--start", "2013-04-01", "--end", "2013-05-31"])
    items = g.SPEC.list_items(None, args)
    assert [i.name for i in items] == ["201304", "201305"]
    assert sum(len(i.urls) for i in items) == 9                       # days 0,7,...,56 of 61
    assert items[0].urls[1].endswith("20130408.export.CSV.zip")


def test_backblaze_keeps_every_failure_and_thins_the_rest():
    import pyarrow as pa
    from agent.kaggle import fetch_backblaze as b
    serials = [f"s{i % 200}" for i in range(4000)]
    fail = ["1" if i in (7, 1234, 3999) else "0" for i in range(4000)]
    out = b._thin(pa.table({"serial_number": serials, "failure": fail}))
    assert sum(1 for v in out.column("failure").to_pylist() if v == "1") == 3
    assert 0.03 * 4000 < out.num_rows < 0.25 * 4000


def test_fetch_all_dispatch_lists_and_rejects(capsys):
    from agent.kaggle import fetch_all
    assert fetch_all.main(["list"]) == 0 and "kuairand27k" in capsys.readouterr().out
    assert fetch_all.main(["nope"]) == 2
