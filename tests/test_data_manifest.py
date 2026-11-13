"""Data-lake manifests (F-05): build, verify, diff, list, package and load_pinned, on fake data in tmp dirs.

Why: a training run pins its data by manifest hash, so the proof that a manifest verifies by hash, that a notebook's
`load_pinned` refuses a tampered file, that fenced nights are marked, and that frames, addresses and tokens can never reach
a commit has to run in CI without the real archive or any network (any real socket connection fails these tests).
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

from redzone.lab import datamanifest as dm
from redzone.lab import evaldata, feedlag, fence
from scripts import data_manifest as cli
from scripts import license_check
from scripts.history import licences as history_licences
from scripts.history import store as hstore

REPO = Path(__file__).resolve().parents[1]
PLAN = {   # league -> night -> game ids: one clear night, one tuning night, one locked night
    "nba": {"20261101": ["401000001", "401000002"], "20261015": ["401000003"], "20251220": ["401000004"]},
    "nhl": {"20261102": ["402000001"]},
}
ORPHAN = "cbb/summary_403000001.json"      # a game no scoreboard lists: its night is unknown


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", boom)


def lic():
    return cli._Licences()


def make_archive(root: Path, plan: dict = PLAN, orphan: bool = True) -> Path:
    for league, nights in plan.items():
        d = root / league
        d.mkdir(parents=True, exist_ok=True)
        for night, ids in nights.items():
            (d / f"scoreboard_{night}.json").write_text(json.dumps({"events": [{"id": i} for i in ids]}))
            for i in ids:
                (d / f"summary_{i}.json").write_text(json.dumps({"id": i, "plays": list(range(int(i[-3:])))}))
    if orphan:
        (root / "cbb").mkdir(exist_ok=True)
        (root / ORPHAN).write_text(json.dumps({"id": "403000001"}))
    return root


def build(root: Path, tag: str = "archive-test-v1", kind: str = "archive", **kw):
    kw.setdefault("settle_seconds", 0)
    return dm.build_manifest(kind, root, tag, licences=lic(), **kw)[0]


@pytest.fixture
def archive(tmp_path):
    return make_archive(tmp_path / "archive")


@pytest.fixture
def env(tmp_path, archive, monkeypatch):
    """Manifests folder and archive root where load_pinned looks by default."""
    monkeypatch.setenv("RZ_DATA_MANIFESTS", str(tmp_path / "manifests"))
    monkeypatch.setenv("RZ_DATA_ROOT_ARCHIVE", str(archive))
    return tmp_path


# ---- the manifest -------------------------------------------------------------------------------

def test_build_lists_every_file_with_hash_size_night_and_fence(archive):
    doc = build(archive)
    by = {f["path"]: f for f in doc["files"]}
    assert list(by) == sorted(by) and len(by) == 10
    f = by["nba/summary_401000001.json"]
    raw = (archive / f["path"]).read_bytes()
    assert f["sha256"] == hashlib.sha256(raw).hexdigest() and f["bytes"] == len(raw)
    assert (f["night"], f["fence"], f["league"], f["sources"]) == ("20261101", "none", "nba", ["espn"])
    assert by["nba/summary_401000003.json"]["fence"] == "tuning" and by["nba/scoreboard_20261015.json"]["fence"] == "tuning"
    assert by["nba/summary_401000004.json"]["fence"] == "locked"
    assert by[ORPHAN]["night"] is None and by[ORPHAN]["fence"] == "unknown"
    assert doc["fence"]["counts"] == {"locked": 2, "tuning": 2, "none": 5, "unknown": 1} and doc["fence"]["train_ok"] is False
    assert doc["nights"]["20251220"] == {"fence": "locked", "files": 2}
    assert doc["window"] == {"start": "20251220", "end": "20261102"} and doc["leagues"] == ["cbb", "nba", "nhl"]
    assert doc["totals"]["files"] == 10 and doc["totals"]["bytes"] == sum(f["bytes"] for f in doc["files"]) and doc["totals"]["rows"] is None
    assert doc["tree_sha256"] == evaldata.tree_digest({f["path"]: f["sha256"] for f in doc["files"]})
    assert doc["manifest_sha256"] == dm.manifest_hash(doc)
    assert doc["private"] is False and doc["sources"] == ["espn"] and doc["license"] == history_licences.PERSONAL_TAG
    assert doc["licences"]["espn"]["terms_class"] == "restrictive" and doc["format"] == dm.MANIFEST_FORMAT
    assert fence.FENCES[0].name == "locked" and doc["fence"]["windows"]["tuning"] == ["20261001", "20261031"]


def test_same_files_give_the_same_bytes_whatever_the_creation_order(tmp_path, archive):
    reverse = {lg: dict(reversed(list(n.items()))) for lg, n in reversed(list(PLAN.items()))}
    other = make_archive(tmp_path / "other", reverse)
    a, b = build(archive), build(other)
    assert dm.render(a) == dm.render(b) and a["manifest_sha256"] == b["manifest_sha256"]
    assert json.loads(dm.render(a)) == a


def test_window_and_league_filters_pin_only_what_they_name(archive):
    doc = build(archive, start="2026-11-01", end="20261102", leagues=["nba"])
    assert sorted(f["path"] for f in doc["files"]) == ["nba/scoreboard_20261101.json", "nba/summary_401000001.json", "nba/summary_401000002.json"]
    assert doc["window"] == {"start": "20261101", "end": "20261101"} and doc["fence"]["train_ok"] is True
    assert doc["selection"] == {"end": "20261102", "leagues": ["nba"], "start": "20261101"}
    with pytest.raises(dm.ManifestError, match="no archive files matched"):
        build(archive, start="20300101")


def test_licence_line_travels_with_every_file_and_the_repo_licence_check_reads_it(tmp_path, archive):
    doc = build(archive)
    assert all(f["license"] == history_licences.PERSONAL_TAG for f in doc["files"])
    path = tmp_path / "m.json"
    path.write_text(dm.render(doc))
    terms = json.loads((REPO / "docs" / "datasources" / "terms.json").read_text())
    assert [x for x in license_check.check([path], terms) if x[0] == "FAIL"] == []
    stripped = copy.deepcopy(doc)
    stripped["license"] = ""
    for f in stripped["files"]:
        f["license"] = ""
    path.write_text(dm.render(stripped))
    assert any("no license line" in x[2] for x in license_check.check([path], terms))      # the rows really are checked
    red = copy.deepcopy(doc)
    red["files"][0]["sources"] = ["hockeytech"]
    path.write_text(dm.render(red))
    assert any("red" in x[2] for x in license_check.check([path], terms))


def test_sources_without_a_usable_licence_are_refused(tmp_path):
    for d, why in (("mystery_pbp", "not classified"), ("hockeytech_pbp", "red"), ("nflverse_pbp", "blocked")):
        root = tmp_path / d
        (root / "datasets" / d).mkdir(parents=True)
        (root / "datasets" / d / "play_by_play_2025.parquet").write_bytes(b"PAR1")
        with pytest.raises(dm.ManifestError, match=why):
            build(root, kind="history")


# ---- verify --------------------------------------------------------------------------------------

def test_verify_is_clean_and_ignores_extra_files(archive):
    doc = build(archive)
    assert dm.verify_manifest(doc, archive) == []
    (archive / "nba" / "summary_999999999.json").write_text("{}")      # tonight's new game is not part of the pin
    assert dm.verify_manifest(doc, archive) == []


def test_verify_names_every_changed_missing_resized_or_swapped_file(tmp_path, archive):
    doc = build(archive)
    p = archive / "nba" / "summary_401000001.json"
    raw = p.read_bytes()
    p.write_bytes(raw[:-1] + (b"X" if raw[-1:] != b"X" else b"Y"))                   # same size, one byte different
    (archive / "nba" / "summary_401000002.json").write_text("{}")                     # different size
    (archive / "nba" / "summary_401000003.json").unlink()                             # gone
    swap = archive / "nhl" / "summary_402000001.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(swap.read_bytes())
    swap.unlink()
    swap.symlink_to(elsewhere)                                                         # a link to identical bytes is still refused
    problems = dm.verify_manifest(doc, archive)
    text = "\n".join(problems)
    assert "nba/summary_401000001.json: sha256" in text and "nba/summary_401000002.json: size" in text
    assert "nba/summary_401000003.json: missing" in text and "nhl/summary_402000001.json: is a symlink" in text
    assert len(problems) == 4 and str(tmp_path) not in text


def test_a_folder_swapped_for_a_link_out_of_the_data_root_is_refused(tmp_path, archive):
    doc = build(archive)
    (archive / "nba").rename(tmp_path / "moved_nba")
    (archive / "nba").symlink_to(tmp_path / "moved_nba")
    text = "\n".join(dm.verify_manifest(doc, archive))
    assert "resolves outside the data folder" in text


def test_an_edited_manifest_is_caught_even_when_the_files_are_fine(archive):
    doc = build(archive)

    def edited(fn):
        d = copy.deepcopy(doc)
        fn(d)
        return "\n".join(dm.check_manifest(d))

    assert "manifest_sha256" in edited(lambda d: d["files"][0].update(rows=5))
    assert "tree_sha256 does not match" in edited(lambda d: d["files"][0].update(sha256="0" * 64))
    assert "stale" in edited(lambda d: next(f for f in d["files"] if f["fence"] == "tuning").update(fence="none"))
    assert "unsafe path" in edited(lambda d: d["files"][0].update(path="../evil.json"))
    assert "unsafe path" in edited(lambda d: d["files"][0].update(path="nba//x.json"))
    assert "bad tag" in edited(lambda d: d.update(tag="../x"))
    assert "private flag" in edited(lambda d: d.update(private=True))
    assert "listed twice" in edited(lambda d: d["files"].append(dict(d["files"][0])))
    assert "not sorted" in edited(lambda d: d["files"].reverse())
    assert "looks like a credential" in edited(lambda d: d.update(api_key="x"))
    assert dm.check_manifest({"format": "other"}) == [f"not a {dm.MANIFEST_FORMAT} document"]
    assert dm.check_manifest(doc) == []


def test_fence_windows_that_move_later_make_old_manifests_stale(archive, monkeypatch):
    doc = build(archive)
    moved = fence.Window("tuning", "20261101", "20261130", "moved")
    monkeypatch.setattr(fence, "FENCES", (fence.LOCKED, moved))
    monkeypatch.setattr(fence, "fenced", lambda night, fences=(fence.LOCKED, moved): next(
        (w for w in fences if w.start <= fence.night_key(night) <= w.end), None))
    text = "\n".join(dm.check_manifest(doc))
    assert "stale" in text and "rebuild under a new tag" in text


# ---- what a data folder may hold -----------------------------------------------------------------

def test_symlinks_inside_the_data_folder_are_never_followed(tmp_path, archive):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (archive / "archive").symlink_to(archive)                      # the loop the real archive has
    (archive / "nba" / "summary_555555555.json").symlink_to(outside)
    (archive / "-q").mkdir()
    doc, report = dm.build_manifest("archive", archive, "archive-test-v1", licences=lic(), settle_seconds=0)
    assert len(doc["files"]) == 10 and report["skipped"]["symlinks"] == 2 and report["skipped"]["other_dirs"] >= 1


def test_the_data_folder_itself_may_be_a_symlink(tmp_path, archive):
    link = tmp_path / "link"
    link.symlink_to(archive)
    assert build(link)["manifest_sha256"] == build(archive)["manifest_sha256"]
    assert dm.verify_manifest(build(archive), link) == []


def test_files_that_look_like_secrets_are_skipped_without_being_opened(tmp_path, monkeypatch):
    root = tmp_path / "vision"
    (root / "20261111-game" / "frames").mkdir(parents=True)
    (root / "20261111-game" / "frames" / "1.jpg").write_bytes(b"jpg")
    secret = root / "20261111-game" / "tv_token.json"
    secret.write_text("never read")
    secret.chmod(0)
    opened = []
    real = dm._digest
    monkeypatch.setattr(dm, "_digest", lambda path, count: (opened.append(path), real(path, count))[1])
    try:
        doc, report = dm.build_manifest("frames", root, "frames-test-v1", licences=lic(), settle_seconds=0)
    finally:
        secret.chmod(0o600)
    assert [f["path"] for f in doc["files"]] == ["20261111-game/frames/1.jpg"] and report["skipped"]["secret_names"] == 1
    assert not any("token" in p for p in opened) and "token" not in dm.render(doc)


def test_files_still_being_written_are_refused_until_they_settle(archive):
    with pytest.raises(dm.ManifestError, match="may still be written"):
        dm.build_manifest("archive", archive, "archive-test-v1", licences=lic())
    assert dm.build_manifest("archive", archive, "archive-test-v1", licences=lic(), now=time.time() + 3600)[0]["totals"]["files"] == 10
    old = time.time() - 7200
    for p in archive.rglob("*.json"):
        os.utime(p, (old, old))
    assert dm.build_manifest("archive", archive, "archive-test-v1", licences=lic())[0]["totals"]["files"] == 10


def test_unreadable_scoreboards_are_noted_and_their_games_have_no_night(tmp_path):
    root = make_archive(tmp_path / "a", {"nba": {"20261101": ["401000001"]}}, orphan=False)
    (root / "nba" / "scoreboard_20261101.json").write_text("{not json")
    doc = build(root)
    assert {f["path"]: f["night"] for f in doc["files"]}["nba/summary_401000001.json"] is None
    assert any("scoreboard unreadable" in n for n in doc["notes"])


# ---- feedlag and history -------------------------------------------------------------------------

def make_feedlag(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lines = [{"ev": "start", "t": 1.0, "schema": 1, "sources": ["espn_summary", "nba_cdn"]},
             {"ev": "row", "t": 2.0, "src": "espn_summary", "lg": "nba", "game": "1", "row": "a", "kind": "foul"},
             {"ev": "row", "t": 3.0, "src": "nba_cdn", "lg": "nba", "game": "1", "row": "b", "kind": "timeout"},
             {"ev": "stop", "t": 4.0, "why": "done"}]
    (root / "2026-11-12.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + '\n{"ev":"ro')       # a torn last line
    (root / "2026-11-13.jsonl").write_text(json.dumps({"ev": "start", "sources": ["espn_summary"]}) + "\n")
    (root / "2026-11-14.jsonl").write_text(json.dumps({"ev": "clock", "skew": 0.1}) + "\n")
    (root / "logger.out").write_text("raw log line\n")
    (root / "logger.pid").write_text("123\n")
    return root


def test_feedlag_manifest_counts_rows_reads_providers_and_leaves_raw_logs_out(tmp_path):
    doc = build(make_feedlag(tmp_path / "feedlag"), "feedlag-test-v1", "feedlag")
    by = {f["path"]: f for f in doc["files"]}
    assert sorted(by) == ["2026-11-12.jsonl", "2026-11-13.jsonl", "2026-11-14.jsonl"]
    assert by["2026-11-12.jsonl"]["rows"] == 5 and by["2026-11-12.jsonl"]["night"] == "20261112"
    assert by["2026-11-12.jsonl"]["sources"] == ["espn", "nba_cdn"] and by["2026-11-13.jsonl"]["sources"] == ["espn"]
    assert by["2026-11-14.jsonl"]["sources"] == ["espn", "nba_cdn"]          # no source named: the stricter reading
    assert doc["totals"]["rows"] == 7 and doc["sources"] == ["espn", "nba_cdn"] and doc["fence"]["train_ok"] is True
    assert dm.verify_manifest(doc, tmp_path / "feedlag") == []


def test_feedlag_source_names_match_the_logger_and_an_unmapped_feed_is_refused(tmp_path):
    assert set(dm.FEEDLAG_SOURCE_NAMES) == set(feedlag.FEED_SOURCES)
    root = tmp_path / "feedlag"
    root.mkdir()
    (root / "2026-11-12.jsonl").write_text(json.dumps({"ev": "row", "src": "fastcast"}) + "\n")
    with pytest.raises(dm.ManifestError, match="no provider mapping"):
        build(root, kind="feedlag")


def make_history(root: Path) -> hstore.Store:
    store = hstore.Store(root)

    def game(league, season, gid, night, plays=3):
        rec = {"v": 1, "id": gid, "league": league, "season": season, "night": night, "plays": [{"id": str(i)} for i in range(plays)]}
        n, sha = hstore.write_game(store.game_path(league, season, gid), rec)
        store.append_ledger("espn", league, {"id": gid, "league": league, "season": season, "night": night, "status": "ok", "plays": plays,
                                             "bytes": n, "sha256": sha})

    game("nba", 2026, "401000001", "20261105", 3)
    game("nba", 2026, "401000002", "20261015", 5)          # tuning night
    ds = root / "datasets" / "hoopr_nba"
    ds.mkdir(parents=True)
    (ds / "play_by_play_2025.parquet").write_bytes(b"PAR1 pretend parquet PAR1")
    store.append_ledger("datasets", "hoopr_nba", {"id": "2025", "season": 2025, "league": "nba", "status": "ok", "rows": 500,
                                                  "sha256": hashlib.sha256((ds / "play_by_play_2025.parquet").read_bytes()).hexdigest()})
    (root / "shards").mkdir()
    (root / "shards" / "espn_nba_2026.jsonl.gz").write_bytes(gzip.compress(b"{}\n", mtime=0))
    return store


def test_history_manifest_takes_night_rows_and_source_from_the_fetch_ledgers(tmp_path):
    root = tmp_path / "history"
    make_history(root)
    doc = build(root, kind="history", tag="history-test-v1")
    by = {f["path"]: f for f in doc["files"]}
    g = by["espn/nba/2026/401000001.json.gz"]
    assert (g["night"], g["rows"], g["fence"], g["league"], g["sources"]) == ("20261105", 3, "none", "nba", ["espn"])
    assert by["espn/nba/2026/401000002.json.gz"]["fence"] == "tuning"
    d = by["datasets/hoopr_nba/play_by_play_2025.parquet"]
    assert (d["rows"], d["sources"], d["league"], d["fence"]) == (500, ["hoopr"], "nba", "unknown")
    assert by["shards/espn_nba_2026.jsonl.gz"]["sources"] == ["espn"] and not any("ledger" in p for p in by)
    assert doc["sources"] == ["espn", "hoopr"] and set(doc["licences"]) == {"espn", "hoopr"}
    assert dm.verify_manifest(doc, root) == []


def test_a_game_file_that_no_longer_matches_its_fetch_ledger_is_not_pinned(tmp_path):
    root = tmp_path / "history"
    store = make_history(root)
    p = store.game_path("nba", 2026, "401000001")
    p.write_bytes(p.read_bytes() + b"!")
    with pytest.raises(dm.ManifestError, match="differs from the fetch ledger"):
        build(root, kind="history")


# ---- frames and the committable rule -------------------------------------------------------------

def make_frames(root: Path) -> Path:
    game = root / "20261111-espn-GS-HOU"
    (game / "frames").mkdir(parents=True)
    for i in range(3):
        (game / "frames" / f"17944524{i}.5.jpg").write_bytes(bytes([i]) * 10)
    (game / "labels.jsonl").write_text('{"a":1}\n{"a":2}\n')
    (root / "README.md").write_text("readme")
    return root


def test_frames_can_be_listed_but_the_manifest_is_private(tmp_path):
    doc = build(make_frames(tmp_path / "vision-v1"), "frames-test-v1", "frames")
    by = {f["path"]: f for f in doc["files"]}
    assert doc["private"] is True and doc["kind"] == "frames" and doc["sources"] == ["tv_frames"]
    assert by["20261111-espn-GS-HOU/labels.jsonl"]["rows"] == 2 and by["20261111-espn-GS-HOU/labels.jsonl"]["night"] == "20261111"
    assert by["README.md"]["night"] is None and doc["licences"]["tv_frames"]["terms_class"] == "private"
    dest, created = dm.write_manifest(doc, tmp_path / "manifests")
    assert created and dest.name == "frames-test-v1.private.manifest.json"
    assert dm.verify_manifest(doc, tmp_path / "vision-v1") == []


def test_package_refuses_frames_unless_asked_and_never_uploads_or_needs_credentials(tmp_path, monkeypatch):
    for k in ("KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_API_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(k, raising=False)
    frames = build(make_frames(tmp_path / "vision-v1"), "frames-test-v1", "frames")
    with pytest.raises(dm.ManifestError, match="REFUSED package"):
        dm.plan_bundle(frames)
    plan = dm.plan_bundle(frames, include_private=True)
    assert plan["private"] is True and plan["dry_run"] is True and plan["uploaded"] is False and plan["needs_login"] is False


def test_package_plan_is_a_dry_run_layout_with_a_metadata_skeleton(tmp_path, archive):
    doc = build(archive, "archive-test-with-a-rather-long-tag-name-v1")
    plan = dm.plan_bundle(doc)
    assert (plan["dry_run"], plan["uploaded"], plan["needs_login"], plan["private"]) == (True, False, False, False)
    assert plan["files"] == 10 and sum(d["files"] for d in plan["dirs"].values()) == 10 and plan["dirs"]["nba"]["files"] == 7
    meta = plan["kaggle"]["dataset-metadata.json"]
    slug = meta["id"].split("/", 1)[1]
    assert meta["id"].startswith("KAGGLE_USERNAME/") and 3 <= len(slug) <= 50 and re.fullmatch(r"[a-z0-9-]+", slug)
    assert 6 <= len(meta["title"]) <= 50 and meta["licenses"] == [{"name": "other"}] and history_licences.PERSONAL_TAG in meta["description"]
    assert plan["layout"]["data_prefix"] == "data/" and plan["drive"]["folder"] == f"RedZone/data-lake/archive/{doc['tag']}"
    out = tmp_path / "bundle"
    written = dm.write_bundle_skeleton(doc, plan, out)
    assert sorted(p.name for p in out.iterdir()) == sorted(written) == sorted(["LAYOUT.json", "dataset-metadata.json", f"{doc['tag']}.manifest.json"])
    assert json.loads((out / f"{doc['tag']}.manifest.json").read_text()) == doc                # the notebook verifies against this copy


def test_a_tag_is_a_pin_and_cannot_be_rewritten_with_other_content(tmp_path, archive):
    doc = build(archive)
    d = tmp_path / "manifests"
    assert dm.write_manifest(doc, d)[1] is True
    assert dm.write_manifest(doc, d)[1] is False                                              # identical: a no-op
    (archive / "nba" / "summary_401000001.json").write_text("{}")
    with pytest.raises(dm.ManifestError, match="already pinned to different content"):
        dm.write_manifest(build(archive), d)


def test_a_private_manifest_cannot_be_written_where_git_would_commit_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    doc = build(make_frames(tmp_path / "vision-v1"), "frames-test-v1", "frames")
    with pytest.raises(dm.ManifestError, match="could be committed"):
        dm.write_manifest(doc, repo / "manifests")
    with pytest.raises(dm.ManifestError, match="could be committed"):
        dm.write_bundle_skeleton(doc, dm.plan_bundle(doc, include_private=True), repo / "bundle")
    (repo / ".gitignore").write_text("*.private.manifest.json\nbundle/\n")
    assert dm.write_manifest(doc, repo / "manifests")[1] is True
    assert dm.write_bundle_skeleton(doc, dm.plan_bundle(doc, include_private=True), repo / "bundle")


def test_the_repo_ignores_private_manifests_and_bundles_and_tracks_no_frames_or_data():
    for probe in ("x/y.private.manifest.json", "data-bundles/t/LAYOUT.json", "data/manifests/a.manifest.json", "data/bundles/t/LAYOUT.json"):
        assert subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", probe]).returncode == 0, probe
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    bad = [p for p in tracked if p.endswith(".private.manifest.json") or p.startswith(("data/", "data-bundles/")) or "vision-v1" in p
           or re.search(r"(^|/)frames/.*\.(jpe?g|png)$", p)]
    assert bad == []


# ---- nothing that must not be committed ----------------------------------------------------------

ADDRESS_AND_TOKEN_PATTERNS = (
    re.compile(r"/(Users|home|private|var|tmp|Volumes)/"), re.compile(r"~/"), re.compile(r"[A-Za-z]:\\"), re.compile(r"://"),
    re.compile(r"(?<![\d.])\d{1,3}(\.\d{1,3}){3}(?![\d.])"), re.compile(r"[\w.+-]+@[\w-]+\.\w+"), re.compile(r"(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|sk-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[abprs]-|Bearer "),
    re.compile(r'(?i)"[^"]*(token|secret|passw|api_?key|credential|cookie)[^"]*"\s*:'),
)


@pytest.mark.parametrize("kind", ["archive", "history", "feedlag", "frames"])
def test_no_manifest_holds_a_home_path_address_url_or_token(tmp_path, kind):
    root = {"archive": lambda p: make_archive(p), "history": lambda p: (make_history(p), p)[1], "feedlag": make_feedlag, "frames": make_frames}[kind](
        tmp_path / "data" / kind)
    doc = build(root, f"{kind}-test-v1", kind)
    text = dm.render(doc)
    assert dm.scrub_problems(doc) == []
    assert str(tmp_path) not in text and str(Path.home()) not in text and str(REPO) not in text
    for rx in ADDRESS_AND_TOKEN_PATTERNS:
        assert not rx.search(text), rx.pattern
    out = tmp_path / "bundle"
    plan = dm.plan_bundle(doc, include_private=True)
    dm.write_bundle_skeleton(doc, plan, out)
    for f in out.iterdir():
        for rx in ADDRESS_AND_TOKEN_PATTERNS:
            assert not rx.search(f.read_text()), (f.name, rx.pattern)


def test_licence_lines_used_in_manifests_are_themselves_clean():
    for key in list(history_licences.SOURCES) + ["nba_cdn", "tv_frames"]:
        assert dm.scrub_problems(cli.licence_entry(key)) == [], key


@pytest.mark.parametrize("value", ["/Users/someone/data/x.json", "/home/runner/work/x", "~/data", "C:\\Users\\x", "https://example.test/a",
                                   "sftp://host/a", "192.168.1.20", "fe80:0:0:0:1:2:3:4", "aa:bb:cc:dd:ee:ff", "me@example.test",
                                   "ghp_" + "a" * 24, "sk-ant-" + "b" * 10, "AKIA" + "A" * 16, "Bearer " + "c" * 20, "password=hunter2",
                                   "-----BEGIN RSA PRIVATE KEY-----", "/etc/hosts"])
def test_scrub_flags_every_kind_of_thing_that_must_not_be_committed(value):
    assert dm.scrub_problems({"files": [{"path": value}]}), value
    assert dm.scrub_problems({"note": value}), value


def test_scrub_flags_credential_looking_keys_and_passes_plain_manifest_text():
    assert dm.scrub_problems({"auth_token": "x"}) and dm.scrub_problems({"nested": {"Api-Key": 1}})
    assert dm.scrub_problems({"path": "nba/summary_401000001.json", "sha256": "a" * 64, "t": "20261101", "ver": "3.12.4", "n": 12}) == []


def test_file_names_that_look_like_addresses_cannot_be_pinned(tmp_path):
    root = make_frames(tmp_path / "vision-v1")
    (root / "20261111-espn-GS-HOU" / "frames" / "192.168.1.20.jpg").write_bytes(b"x")
    with pytest.raises(dm.ManifestError, match="look like addresses or paths"):
        build(root, kind="frames")


# ---- load_pinned (the notebook side) -------------------------------------------------------------

def test_load_pinned_returns_the_files_of_a_pinned_version(env, archive):
    doc = build(archive)
    dm.write_manifest(doc)
    data = dm.load_pinned("archive-test-v1")
    assert data.tag == "archive-test-v1" and data.kind == "archive" and data.manifest_sha256 == doc["manifest_sha256"]
    assert data.tree_sha256 == doc["tree_sha256"] and len(data.files) == 10 and all(p.is_file() for p in data.files)
    assert [p.name for p in data.paths(night="2026-11-01", suffix=".json", league="nba")] == [
        "scoreboard_20261101.json", "summary_401000001.json", "summary_401000002.json"]
    assert data.nights == ["20251220", "20261015", "20261101", "20261102"] and len(data.paths(league="nhl", source="espn")) == 2
    assert dm.load_pinned(dm.find_manifest("archive-test-v1")).manifest_sha256 == doc["manifest_sha256"]       # by manifest path, as on Kaggle


def test_load_pinned_refuses_a_tampered_missing_or_unknown_version(env, archive):
    dm.write_manifest(build(archive))
    p = archive / "nba" / "summary_401000001.json"
    original = p.read_bytes()
    p.write_bytes(original.replace(b"401000001", b"401000009"))
    with pytest.raises(dm.ManifestError, match=r"REFUSED archive-test-v1.*nba/summary_401000001\.json: sha256") as e:
        dm.load_pinned("archive-test-v1")
    assert str(env) not in str(e.value)
    p.write_bytes(original)
    assert dm.load_pinned("archive-test-v1")                                                  # restored: loads again
    (archive / "nba" / "summary_401000002.json").unlink()
    with pytest.raises(dm.ManifestError, match="summary_401000002.json: missing"):
        dm.load_pinned("archive-test-v1")
    with pytest.raises(dm.ManifestError, match="no manifest for tag"):
        dm.load_pinned("archive-never-built-v1")
    for bad in ("../archive-test-v1", "x", "A B", "a/b"):
        with pytest.raises(dm.ManifestError, match="bad tag"):
            dm.load_pinned(bad)


def test_load_pinned_refuses_a_manifest_that_was_edited(env, archive):
    doc = build(archive)
    path, _ = dm.write_manifest(doc)
    edited = json.loads(path.read_text())
    edited["files"][0]["sha256"] = hashlib.sha256(b"other").hexdigest()
    path.write_text(json.dumps(edited))
    with pytest.raises(dm.ManifestError, match="REFUSED"):
        dm.load_pinned("archive-test-v1")
    path.write_text("{broken")
    with pytest.raises(dm.ManifestError, match="cannot read manifest"):
        dm.load_pinned("archive-test-v1")


def test_load_pinned_for_training_refuses_fenced_nights(env, archive):
    dm.write_manifest(build(archive))
    with pytest.raises(fence.FenceError, match=r"REFUSED training set: 1 locked night\(s\).*1 tuning night\(s\)"):
        dm.load_pinned("archive-test-v1", for_training=True)                                  # nights, not files, are counted
    assert dm.load_pinned("archive-test-v1")                                                  # evaluation use is fine
    dm.write_manifest(build(archive, "archive-clear-v1", start="20261101", end="20261102"))
    assert len(dm.load_pinned("archive-clear-v1", for_training=True).files) == 5


def test_unknown_nights_block_training_unless_the_caller_says_the_fetcher_kept_fences_out(tmp_path, monkeypatch):
    root = make_archive(tmp_path / "a2", {"nba": {"20261101": ["401000001"]}})          # plus the game no scoreboard lists
    monkeypatch.setenv("RZ_DATA_MANIFESTS", str(tmp_path / "manifests"))
    monkeypatch.setenv("RZ_DATA_ROOT_ARCHIVE", str(root))
    dm.write_manifest(build(root, "archive-dateless-v1"))
    with pytest.raises(dm.ManifestError, match="1 file.s. have no night"):
        dm.load_pinned("archive-dateless-v1", for_training=True)
    assert len(dm.load_pinned("archive-dateless-v1", for_training=True, allow_unknown_nights=True).files) == 3


# ---- diff ----------------------------------------------------------------------------------------

def test_diff_reports_added_removed_changed_and_moved_files(tmp_path, archive):
    a = build(archive)
    other = tmp_path / "next"
    make_archive(other)
    (other / "nba" / "summary_401000001.json").write_text('{"id": "changed"}')                 # changed
    (other / "nba" / "summary_401000003.json").unlink()                                        # removed
    (other / "nba" / "summary_401000002.json").rename(other / "nba" / "summary_777000002.json")   # moved: same bytes, new name
    (other / "nhl" / "summary_402000099.json").write_text("{}")                                # added
    b = build(other, "archive-test-v2")
    d = dm.diff_manifests(a, b)
    assert d["changed"] == ["nba/summary_401000001.json"] and d["removed"] == ["nba/summary_401000003.json"]
    assert d["moved"] == [["nba/summary_401000002.json", "nba/summary_777000002.json"]] and d["added"] == ["nhl/summary_402000099.json"]
    assert d["unchanged"] == 7 and d["same"] is False and d["tags"] == ["archive-test-v1", "archive-test-v2"]
    same = dm.diff_manifests(a, copy.deepcopy(a))
    assert same["same"] is True and same["added"] == same["removed"] == same["changed"] == same["moved"] == []


# ---- the command line ----------------------------------------------------------------------------

def run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_cli_build_verify_list_diff_package_round_trip(tmp_path, archive, capsys):
    d, base = tmp_path / "manifests", ["--dir", tmp_path / "manifests"]
    code, out, err = run(capsys, "build", "archive", "--tag", "archive-cli-v1", "--root", archive, "--settle-seconds", 0, *base)
    assert code == 0 and "files=10" in out and "fence(locked=2,tuning=2,none=5,unknown=1)" in out and "wrote archive-cli-v1.manifest.json" in out
    code, out, _ = run(capsys, "build", "archive", "--tag", "archive-cli-v1", "--root", archive, "--settle-seconds", 0, *base)
    assert code == 0 and "already pinned, identical" in out
    code, out, _ = run(capsys, "verify", "archive-cli-v1", "--root", archive, *base)
    assert code == 0 and out.startswith("OK archive-cli-v1") and "manifest=" in out
    code, out, _ = run(capsys, "list", "--verify", "--root", archive, *base)
    assert code == 0 and "archive-cli-v1" in out and "VERIFIED" in out
    code, out, _ = run(capsys, "list", "--json", *base)
    assert json.loads(out)[0]["tag"] == "archive-cli-v1"
    code, out, _ = run(capsys, "build", "archive", "--tag", "archive-cli-v2", "--root", archive, "--settle-seconds", 0, "--leagues", "nba", *base)
    assert code == 0
    code, out, _ = run(capsys, "diff", "archive-cli-v1", "archive-cli-v2", *base)
    assert code == 1 and "removed=3" in out and "removed: nhl/summary_402000001.json" in out
    code, out, _ = run(capsys, "diff", "archive-cli-v1", "archive-cli-v1", *base)
    assert code == 0 and "identical" in out
    code, out, _ = run(capsys, "package", "archive-cli-v1", "--out", tmp_path / "bundle", *base)
    assert code == 0 and "DRY RUN" in out and "Nothing uploaded, no credentials used" in out
    assert sorted(p.name for p in (tmp_path / "bundle").iterdir()) == ["LAYOUT.json", "archive-cli-v1.manifest.json", "dataset-metadata.json"]
    assert d.is_dir()


def test_cli_verify_fails_loudly_and_prints_no_file_contents_or_absolute_paths(tmp_path, archive, capsys):
    base = ["--dir", tmp_path / "manifests", "--root", archive]
    run(capsys, "build", "archive", "--tag", "archive-cli-v1", "--settle-seconds", 0, *base)
    (archive / "nba" / "summary_401000001.json").write_text("SECRET-LOOKING CONTENT THAT MUST NOT BE PRINTED")
    (archive / "nhl" / "summary_402000001.json").unlink()
    code, out, err = run(capsys, "verify", "archive-cli-v1", *base)
    assert code == 1 and "VERIFY FAILED archive-cli-v1: 2 problem(s)" in err
    assert "nba/summary_401000001.json" in err and "nhl/summary_402000001.json: missing" in err
    assert "SECRET-LOOKING" not in out + err and str(tmp_path) not in out + err
    code, out, _ = run(capsys, "verify", "archive-cli-v1", "--json", *base)
    assert code == 1 and json.loads(out)["ok"] is False


def test_cli_refuses_frames_packages_unless_asked_and_keeps_frames_manifests_private(tmp_path, capsys):
    frames = make_frames(tmp_path / "vision-v1")
    base = ["--dir", tmp_path / "manifests"]
    code, out, _ = run(capsys, "build", "frames", "--tag", "frames-cli-v1", "--root", frames, "--settle-seconds", 0, *base)
    assert code == 0 and "PRIVATE" in out and "frames-cli-v1.private.manifest.json" in out
    code, out, err = run(capsys, "package", "frames-cli-v1", *base)
    assert code == 1 and "REFUSED package" in err and "DRY RUN" not in out
    code, out, err = run(capsys, "package", "frames-cli-v1", "--include-private-frames", *base)
    assert code == 0 and "DRY RUN" in out and "PRIVATE" in out and "no --out: nothing written" in out
    code, out, _ = run(capsys, "list", *base)
    assert "PRIVATE" in out and str(tmp_path) not in out


def test_cli_licence_entries_come_from_the_history_registry_then_terms_json():
    espn = cli.licence_entry("espn")
    assert espn["licence"] == history_licences.SOURCES["espn"].licence and espn["tag"] == history_licences.PERSONAL_TAG
    assert cli.licence_entry("nba_cdn")["terms_class"] == "restrictive" and cli.licence_entry("nba_cdn")["tag"] == history_licences.PERSONAL_TAG
    assert cli.licence_entry("tv_frames")["terms_class"] == "private"
    with pytest.raises(dm.ManifestError, match="not classified"):
        cli.licence_entry("some_new_feed")
