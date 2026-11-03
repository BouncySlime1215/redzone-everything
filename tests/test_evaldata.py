"""The pinned eval dataset: deterministic build, verify, safe fetch/extract."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from redzone.lab import evaldata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_eval_data  # noqa: E402
import fetch_eval_data  # noqa: E402

WINDOW = {"nfl": {"start": "20261001", "end": "20261101"}, "nba": {"start": "20261025", "end": "20261101"}}


def _night(arch: Path, day: str, ids: list[str]) -> None:
    arch.mkdir(parents=True, exist_ok=True)
    (arch / f"scoreboard_{day}.json").write_text(json.dumps({"events": [{"id": i} for i in ids]}))
    for i in ids:
        (arch / f"summary_{i}.json").write_text(json.dumps({"id": i, "plays": [day]}))


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "src" / "data"
    _night(d / "archive/nfl", "20260920", ["old"])        # before the window
    _night(d / "archive/nfl", "20261012", ["a", "b"])
    _night(d / "archive/nfl", "20261101", ["c"])
    _night(d / "archive/nfl", "20261108", ["late"])       # after: the live service kept recording
    _night(d / "archive/nba", "20261030", ["n1"])
    _night(d / "archive/nhl", "20261030", ["h1"])         # league not in the window
    (d / "archive/nfl/summary_orphan.json").write_text("{}")
    (d / "demo/20261101/nfl").mkdir(parents=True)
    (d / "demo/20261101/nfl/scoreboard_20261101.json").write_text("{}")
    (d / "demo/20261101/_work").mkdir()
    (d / "demo/20261101/_work/config.yaml").write_text("{}")      # demo scratch: never pinned
    (d / "eval_config.yaml").write_text("teams:\n  nfl: [WSH]\n")
    return tmp_path


def _build(repo, out="out", tag="eval-data-test"):
    return build_eval_data.build(repo / "src/data", repo / out, tag, WINDOW, ["20261101"])


def test_select_takes_only_the_window(repo):
    files = evaldata.select(repo / "src/data", WINDOW, ["20261101"])
    assert "data/archive/nfl/summary_a.json" in files and "data/archive/nfl/summary_c.json" in files
    assert "data/archive/nba/summary_n1.json" in files
    for gone in ("summary_old", "summary_late", "summary_orphan", "nhl/", "_work"):
        assert not any(gone in f for f in files), gone
    assert "data/eval_config.yaml" in files and "data/demo/20261101/nfl/scoreboard_20261101.json" in files


def test_build_is_deterministic(repo):
    a, b = _build(repo, "o1"), _build(repo, "o2")
    assert a["sha256"] == b["sha256"] and a["tree_sha256"] == b["tree_sha256"]
    assert (repo / "o1/eval-archive.tgz").read_bytes() == (repo / "o2/eval-archive.tgz").read_bytes()
    assert a["games"] == {"nfl": 3, "nba": 1}
    line = (repo / "o1/eval-archive.tgz.sha256").read_text()
    assert line == f"{a['sha256']}  eval-archive.tgz\n"


def test_build_refuses_profile_names(repo):
    (repo / "src/data/eval_config.yaml").write_text("providers:\n  profiles:\n    prime: Someone\n")
    with pytest.raises(SystemExit, match="providers"):
        _build(repo)


def test_build_refuses_without_eval_config(repo):
    (repo / "src/data/eval_config.yaml").unlink()
    with pytest.raises(SystemExit):
        _build(repo)


def test_fetch_extract_then_verify_and_detect_drift(repo, tmp_path):
    lock = _build(repo)
    dst = tmp_path / "checkout"
    (dst / "data/archive/nfl").mkdir(parents=True)
    (dst / "data/archive/nfl/summary_zzz.json").write_text("{}")  # unrelated local night: kept
    (dst / evaldata.LOCK.name).write_text(json.dumps(lock))
    fetch_eval_data.extract(repo / "out/eval-archive.tgz", lock, dst, force=False)
    ok, problems = evaldata.verify(dst)
    assert ok, problems
    assert (dst / "data/archive/nfl/summary_zzz.json").exists()
    assert evaldata.stamp(dst) == "eval-data-test"
    # The recorder refreshes a pinned summary: grading now says so instead of silently drifting.
    (dst / "data/archive/nfl/summary_a.json").write_text("{}")
    ok, problems = evaldata.verify(dst)
    assert not ok and problems == ["changed data/archive/nfl/summary_a.json"]
    assert evaldata.stamp(dst).startswith("UNPINNED: changed")
    # Re-extracting won't clobber a differing local file without --force.
    with pytest.raises(SystemExit):
        fetch_eval_data.extract(repo / "out/eval-archive.tgz", lock, dst, force=False)
    fetch_eval_data.extract(repo / "out/eval-archive.tgz", lock, dst, force=True)
    assert evaldata.verify(dst)[0]


def test_verify_flags_a_stale_tag(repo, tmp_path):
    lock = _build(repo)
    dst = tmp_path / "c"
    dst.mkdir()
    (dst / evaldata.LOCK.name).write_text(json.dumps({**lock, "tag": "eval-data-v9", "tree_sha256": "x"}))
    fetch_eval_data.extract(repo / "out/eval-archive.tgz", lock, dst, force=False)
    ok, problems = evaldata.verify(dst)
    assert not ok and "eval-data-v9" in problems[0]


def test_no_pin_is_reported(tmp_path):
    assert evaldata.stamp(tmp_path).startswith("UNPINNED: no data/eval_pin.json")


@pytest.mark.parametrize("name,kind", [("../evil", tarfile.REGTYPE), ("/etc/x", tarfile.REGTYPE),
                                       ("scripts/x.py", tarfile.REGTYPE), ("data/link", tarfile.SYMTYPE)])
def test_extract_refuses_unsafe_members(tmp_path, name, kind):
    tgz = tmp_path / "bad.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        ti = tarfile.TarInfo(name)
        ti.type = kind
        ti.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
        tar.addfile(ti, io.BytesIO(b""))
    with pytest.raises(SystemExit, match="refusing"):
        fetch_eval_data.extract(tgz, {"tag": "t"}, tmp_path / "dst", force=False)


def test_tampered_member_is_rejected(repo, tmp_path):
    lock = _build(repo)
    src = repo / "out/eval-archive.tgz"
    bad = tmp_path / "bad.tgz"
    with tarfile.open(src, "r:gz") as tin, tarfile.open(bad, "w:gz") as tout:
        for m in tin.getmembers():
            data = tin.extractfile(m).read()
            if m.name.endswith("summary_a.json"):
                data = b'{"tampered": 1}'
                m.size = len(data)
            tout.addfile(m, io.BytesIO(data))
    with pytest.raises(SystemExit, match="pinned hash"):
        fetch_eval_data.extract(bad, lock, tmp_path / "dst", force=False)


def test_lock_validation(tmp_path):
    p = tmp_path / "lock.json"
    p.write_text(json.dumps({"tag": "t", "asset": "a", "sha256": "s", "window": {"nfl": {"start": "2026", "end": "x"}}}))
    with pytest.raises(ValueError):
        evaldata.load_lock(p)
    p.write_text(json.dumps({"tag": "t", "asset": "a", "sha256": "s",
                             "window": {"nfl": {"start": "20261102", "end": "20261101"}}}))
    with pytest.raises(ValueError, match="starts after"):
        evaldata.load_lock(p)
    assert evaldata.load_lock(tmp_path / "none.json") is None


def test_committed_lock_is_valid_and_matches_scorecards():
    lock = evaldata.load_lock()
    if lock is None:
        pytest.skip("no eval-data.lock.json yet")
    assert set(lock["window"]) >= {"nfl", "nba", "nhl"}
    assert len(lock["sha256"]) == 64 and lock["asset"] == "eval-archive.tgz"
    assert evaldata.asset_url(lock).startswith("https://github.com/")
    for lg in ("nfl", "nba", "nhl"):
        assert evaldata.window_end(lg) == lock["window"][lg]["end"]


def test_check_cli_fails_cleanly_without_data(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/fetch_eval_data.py"), "--check", "--root", str(tmp_path)],
                       capture_output=True, text=True)
    if evaldata.load_lock() is None:
        assert r.returncode == 1 and "no eval-data.lock.json" in r.stderr
    else:
        assert r.returncode == 1 and "NOT pinned" in r.stdout


def test_sha_helper():
    assert evaldata.tree_digest({"b": "2", "a": "1"}) == hashlib.sha256(b"a\t1\nb\t2\n").hexdigest()
