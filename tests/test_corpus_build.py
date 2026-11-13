"""Corpus v1 build (B-02): shards, manifest, leak fence, dedup, walk-forward roles, determinism, reader and CLI, on fixture games in tmp dirs.

Why: B-02 done-when 1 to 4 (WORKPLAN): the manifest verifies by hash, a fenced night in the input never reaches a training shard (and a
corpus that holds one fails verify even when its hashes are right), the same input gives byte-identical shards, and the stats report
exists. Nothing here reads a real night or the network; every folder is built from fake ESPN-shaped plays.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
import socket
from pathlib import Path

import numpy as np
import pytest

from redzone.lab import datamanifest as dm
from redzone.lab import evaldata, fence
from redzone.lab.corpus import build, dedup, manifest, reader, shardio, stats, walkforward
from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.records import Light, era_of, night_class, season_of, tier_of
from redzone.lab.corpus.sources import ArchiveSource, FenceGate, HistorySource
from scripts import corpus_build as cli
from scripts import license_check
from tests.corpus_fakes import (espn_record, hoopr_frame, make_plays, nba_night, write_archive_game, write_history_game, write_history_shard,
                                write_hoopr)

REPO = Path(__file__).resolve().parents[1]
SEASONS = (2018, 2019, 2020, 2021, 2022)
FENCED = {"locked": ("900001", "20251220"), "locked_edge": ("900002", "20260105"), "tuning": ("900003", "20261015"),
          "fresh": ("900004", "20261102"), "undated": ("900005", None)}
EDGE = {"before_lock": ("900006", "20251211"), "after_lock": ("900007", "20260106")}      # clear nights next to the locked window


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", boom)


def make_history(root: Path, reverse: bool = False, fenced: bool = True) -> Path:
    """Five clear NBA seasons of six games each (nights 0..5), plus one shard that carries every kind of fenced night."""
    for season in SEASONS:
        recs = [espn_record("nba", season, f"{season}{k:03d}", nba_night(season, k), n=40) for k in range(6)]
        write_history_shard(root, "nba", season, recs[::-1] if reverse else recs)
    if fenced:
        extra = [espn_record("nba", 2026, gid, night, n=40, wall=night is not None) for gid, night in {**FENCED, **EDGE}.values()]
        write_history_shard(root, "nba", 2026, extra[::-1] if reverse else extra)
    return root


def cfg_for(history: Path, out: Path, tag: str = "corpus-test-v1", **kw) -> build.BuildConfig:
    return build.BuildConfig(tag=tag, out=out, history_root=history, **kw)


def run_build(history: Path, out: Path, **kw) -> dict:
    return build.build_corpus(cfg_for(history, out, **kw), log=lambda *_: None)


def gids_in(root: Path, doc: dict, **where) -> set[str]:
    out = set()
    for row in doc["corpus"]["shards"]:
        if all(row[k] == v for k, v in where.items()):
            out |= {r[0] for r in shardio.read_shard(root.joinpath(*row["path"].split("/"))).header["index"]}
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("corpus")
    out = base / "out"
    doc = run_build(make_history(base / "history"), out)
    return doc, out


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---- shards and the manifest ---------------------------------------------------------------------------------

def test_the_build_writes_shards_by_league_season_tier_and_role(built):
    doc, out = built
    rows = {r["path"]: r for r in doc["corpus"]["shards"]}
    assert all(r["league"] == "nba" and r["tier"] == "wall" and r["fence"] == "clear" and r["train_ok"] for r in rows.values())
    assert {r["role"] for r in rows.values()} == {"train", "valid", "holdout"}
    assert {r["era"] for r in rows.values()} == {"2010s", "2020s"}
    for path, r in rows.items():
        sh = shardio.read_shard(out.joinpath(*path.split("/")))
        assert shardio.shard_problems(sh) == []
        assert sh.header["games"] == r["games"] == sh.games and sh.header["events"] == r["events"] and sh.header["tokens"] == r["ids"]
        assert sh.header["providers"] == ["espn"] and "personal-use" in sh.header["license"]
        assert [row[3] for row in sh.header["index"]] == sorted(row[3] for row in sh.header["index"])      # ordered by night
        assert sh.tokens.dtype == np.dtype("<u2") and not sh.tokens.flags.writeable                       # a read-only memory map


def test_the_manifest_verifies_by_hash_and_is_scrubbed(built):
    doc, out = built
    assert manifest.verify_corpus(doc, out) == []
    assert doc["manifest_sha256"] == dm.manifest_hash(doc) and doc["format"] == manifest.CORPUS_MANIFEST_FORMAT
    assert doc["tree_sha256"] == evaldata.tree_digest({f["path"]: f["sha256"] for f in doc["files"]})
    assert dm.render(doc) == (out / "corpus-test-v1.manifest.json").read_text()
    assert dm.scrub_problems(doc) == [] and str(out) not in dm.render(doc)
    paths = {f["path"] for f in doc["files"]}
    assert {manifest.CORPUS_NAMES_FILE, manifest.CORPUS_DEDUP_FILE, manifest.CORPUS_STATS_FILE} <= paths
    assert doc["corpus"]["vocab"]["sha256"] == tk.vocab_fingerprint() and doc["sources"] == ["espn"]
    terms = json.loads((REPO / "docs" / "datasources" / "terms.json").read_text())
    assert [x for x in license_check.check([out / "corpus-test-v1.manifest.json"], terms) if x[0] == "FAIL"] == []


def test_a_changed_byte_a_missing_file_or_an_edited_manifest_fails_verify(built, tmp_path):
    doc, out = built
    work = tmp_path / "copy"
    shutil.copytree(out, work)
    shard = next(r["path"] for r in doc["corpus"]["shards"] if r["role"] == "train")
    p = work.joinpath(*shard.split("/"))
    raw = bytearray(p.read_bytes())
    raw[-3] ^= 0xFF
    p.write_bytes(bytes(raw))
    assert any("sha256" in x and shard in x for x in manifest.verify_corpus(doc, work))
    p.write_bytes(bytes(raw[:-10]))
    assert any("size" in x for x in manifest.verify_corpus(doc, work))
    p.unlink()
    assert any("missing" in x for x in manifest.verify_corpus(doc, work))
    edited = copy.deepcopy(doc)
    edited["corpus"]["params"]["gap_days"] = 9
    assert any("manifest_sha256" in x for x in manifest.check_corpus_manifest(edited))
    with pytest.raises(dm.ManifestError, match="REFUSED"):
        reader.load_corpus(out / "corpus-test-v1.manifest.json", work)


def test_a_vocab_that_moved_is_refused(built, monkeypatch):
    doc, out = built
    monkeypatch.setattr(tk, "vocab_fingerprint", lambda: "0" * 64)
    problems = manifest.verify_corpus(doc, out)
    assert any("vocab" in x for x in problems)


# ---- determinism ----------------------------------------------------------------------------------------------

def test_same_input_gives_byte_identical_shards_and_manifest(tmp_path):
    a = run_build(make_history(tmp_path / "ha"), tmp_path / "oa")
    b = run_build(make_history(tmp_path / "hb"), tmp_path / "ob")
    fa, fb = tree_bytes(tmp_path / "oa"), tree_bytes(tmp_path / "ob")
    assert fa.keys() == fb.keys() and len(fa) == 10 and all(fa[k] == fb[k] for k in fa)
    assert a["manifest_sha256"] == b["manifest_sha256"]
    assert all(str(tmp_path).encode() not in v for v in fa.values())                         # no absolute path in any shard, sidecar or manifest


def test_the_order_the_games_are_stored_in_never_changes_a_shard(tmp_path):
    a = run_build(make_history(tmp_path / "ha"), tmp_path / "oa")
    b = run_build(make_history(tmp_path / "hb", reverse=True), tmp_path / "ob")
    fa, fb = tree_bytes(tmp_path / "oa"), tree_bytes(tmp_path / "ob")
    same = {k for k in fa if not k.endswith(".manifest.json")}
    assert same == {k for k in fb if not k.endswith(".manifest.json")} and all(fa[k] == fb[k] for k in same)
    assert [r["sha256"] for r in a["files"]] == [r["sha256"] for r in b["files"]]            # every file entry, shards and sidecars alike
    assert a["corpus"]["inputs"] != b["corpus"]["inputs"]                                    # only the digest of the input files differs


def test_building_the_same_tag_again_is_a_no_op_and_a_different_corpus_under_it_is_refused(tmp_path):
    hist, out = make_history(tmp_path / "h"), tmp_path / "out"
    first = run_build(hist, out)
    before = tree_bytes(out)
    assert run_build(hist, out)["manifest_sha256"] == first["manifest_sha256"] and tree_bytes(out) == before
    with pytest.raises(dm.ManifestError, match="different content"):
        run_build(hist, out, gap_days=3)
    assert tree_bytes(out) == before and not (tmp_path / "out.building").exists()


# ---- the leak fence ---------------------------------------------------------------------------------------------

def test_fenced_nights_never_reach_a_training_shard_or_the_tokeniser(tmp_path, monkeypatch):
    seen: list[str] = []
    real = tk.tokenise_game
    monkeypatch.setattr(tk, "tokenise_game", lambda rec, names=None: (seen.append(rec.gid), real(rec, names))[1])
    out = tmp_path / "out"
    doc = run_build(make_history(tmp_path / "h"), out)
    fenced = {g for g, _ in FENCED.values()}
    assert fenced.isdisjoint(seen) and fenced.isdisjoint(gids_in(out, doc))
    assert {g for g, _ in EDGE.values()} <= gids_in(out, doc)                         # the clear nights beside the locked window are kept
    assert doc["corpus"]["excluded"]["fence"] == {"nba": {"fresh": 1, "locked": 2, "tuning": 1, "undated": 1}}
    assert all(r["train_ok"] for r in doc["corpus"]["shards"]) and manifest.verify_corpus(doc, out) == []
    for row in doc["corpus"]["shards"]:
        for game in shardio.read_shard(out.joinpath(*row["path"].split("/"))).header["index"]:
            assert fence.fenced(game[3]) is None and game[3] < "20261020"


def test_hold_mode_keeps_tuning_nights_only_in_a_shard_that_refuses_training(tmp_path):
    out = tmp_path / "out"
    doc = run_build(make_history(tmp_path / "h"), out, fenced="hold")
    tuning = [r for r in doc["corpus"]["shards"] if r["role"] == "tuning"]
    assert len(tuning) == 1 and tuning[0]["fence"] == "tuning" and tuning[0]["train_ok"] is False
    assert gids_in(out, doc, role="tuning") == {FENCED["tuning"][0]}
    held_out = {FENCED[k][0] for k in ("locked", "locked_edge", "fresh", "undated")}
    assert held_out.isdisjoint(gids_in(out, doc)) and FENCED["tuning"][0] not in gids_in(out, doc, train_ok=True)
    assert manifest.verify_corpus(doc, out) == []
    c = reader.load_corpus(out / "corpus-test-v1.manifest.json")
    with pytest.raises(fence.FenceError, match="REFUSED"):
        list(c.shards(("tuning",)))
    with pytest.raises(fence.FenceError):
        list(c.games(("train", "tuning")))
    assert [s.header["role"] for s in c.shards(("tuning",), for_training=False)] == ["tuning"]
    assert FENCED["tuning"][0] not in {r["gid"] for r, _ in c.games(("train", "valid", "holdout"))}


def retarget(doc: dict, root: Path, rel: str, mutate) -> dict:
    """Rewrite one shard's header, restamp its hash and the manifest hash: hashes then verify, so only the proofs can catch the change."""
    doc = copy.deepcopy(doc)
    sh = shardio.read_shard(root.joinpath(*rel.split("/")))
    h = copy.deepcopy(sh.header)
    mutate(h)
    size, sha = shardio.write_shard(root.joinpath(*rel.split("/")), h, np.array(sh.tokens))
    for f in doc["files"]:
        if f["path"] == rel:
            f["bytes"], f["sha256"] = size, sha
    doc.update(manifest.derive(doc["files"], doc["corpus"]["shards"]))
    doc["manifest_sha256"] = dm.manifest_hash(doc)
    return doc


def test_a_locked_night_hidden_in_a_train_shard_fails_verify_even_though_every_hash_matches(built, tmp_path):
    doc, out = built
    work = tmp_path / "copy"
    shutil.copytree(out, work)
    rel = next(r["path"] for r in doc["corpus"]["shards"] if r["role"] == "train")

    def lock_one(h):
        h["index"][0][3] = "20251220"
    bad = retarget(doc, work, rel, lock_one)
    assert dm.verify_files(bad, work) == []                       # the bytes match the manifest: only the fence proof can see it
    problems = manifest.verify_corpus(bad, work)
    assert any("20251220" in x and "locked" in x for x in problems)


def test_a_tuning_night_in_a_valid_shard_and_a_swapped_role_both_fail_verify(built, tmp_path):
    doc, out = built
    work = tmp_path / "copy"
    shutil.copytree(out, work)
    rel = next(r["path"] for r in doc["corpus"]["shards"] if r["role"] == "valid")
    bad = retarget(doc, work, rel, lambda h: h["index"][0].__setitem__(3, "20261015"))
    assert any("tuning" in x and "valid" in x for x in manifest.verify_corpus(bad, work))
    row = next(r for r in doc["corpus"]["shards"] if r["role"] == "holdout")
    flipped = copy.deepcopy(doc)
    next(r for r in flipped["corpus"]["shards"] if r["path"] == row["path"])["role"] = "train"
    flipped["manifest_sha256"] = dm.manifest_hash(flipped)
    assert manifest.check_corpus_manifest(flipped)                 # a holdout shard relabelled train breaks the cut and the header match


def test_one_game_in_two_shards_is_caught(built, tmp_path):
    doc, out = built
    work = tmp_path / "copy"
    shutil.copytree(out, work)
    train = [r["path"] for r in doc["corpus"]["shards"] if r["role"] == "train"]
    first = shardio.read_shard(work.joinpath(*train[0].split("/"))).header["index"][0][0]
    bad = retarget(doc, work, train[1], lambda h: h["index"][0].__setitem__(0, first))
    assert any(f"game {first} is also in" in x for x in manifest.verify_corpus(bad, work))


def test_night_class_covers_every_verdict_and_judges_a_date_only_night_on_three_days():
    assert [night_class("nba", n) for n in ("20251211", "20251212", "20260105", "20260106")] == ["clear", "locked", "locked", "clear"]
    assert night_class("nba", "20261015") == "tuning" and night_class("nba", "20261102") == "fresh" and night_class("nba", None) == "undated"
    assert night_class("nfl", "20250905") == "fresh" and night_class("nfl", "20250904") == "clear"
    assert night_class("nba", "20251211", exact=False) == "locked" and night_class("nba", "20260106", exact=False) == "locked"
    assert night_class("nba", "20251210", exact=False) == "clear"
    with pytest.raises(ValueError):
        night_class("xyz", "20200101")


def test_the_evaluation_cuts_equal_the_history_fetchers_and_the_factory():
    from redzone.noggin import factory
    from scripts.history.leagues import EVAL_FIRST_NIGHT
    assert all(EVAL_FIRST_NIGHT[k] == factory.EVAL_FIRST_NIGHT[k] for k in EVAL_FIRST_NIGHT)


def test_a_build_with_nothing_left_after_the_fence_refuses_to_write(tmp_path):
    hist = tmp_path / "h"
    write_history_shard(hist, "nba", 2026, [espn_record("nba", 2026, g, n, n=30) for g, n in FENCED.values() if n])
    with pytest.raises(dm.ManifestError, match="no game survived"):
        run_build(hist, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.building").exists()


# ---- sources ---------------------------------------------------------------------------------------------------

def test_the_archive_reader_never_opens_a_fenced_summary(tmp_path):
    root = tmp_path / "archive"
    write_archive_game(root, "nba", "20240115", "5001")
    write_archive_game(root, "nba", "20251220", "5002")
    write_archive_game(root, "nba", "20261015", "5003")
    write_archive_game(root, "nba", "20261102", "5004")
    write_archive_game(root, "nba", "20240116", "5005", completed=False)
    (root / "nba" / "summary_5006.json").write_text(json.dumps({"header": {}, "plays": []}))        # on no scoreboard: undated
    src = ArchiveSource(root)
    gate, reads = FenceGate(), {}
    got = [g for u in src.units() for g in src.games(u, gate, reads)]
    assert [g.gid for g in got] == ["5001"] and got[0].night == "20240115" and got[0].season == 2024
    assert set(reads) == {"nba/summary_5001.json", "nba/summary_5005.json"}          # 5005 is read to learn it is not final; the fenced four are not opened
    assert gate.tally() == {"nba": {"fresh": 1, "locked": 1, "tuning": 1, "undated": 1}}
    hold, reads2 = FenceGate(hold_tuning=True), {}
    assert sorted(g.gid for u in src.units() for g in src.games(u, hold, reads2)) == ["5001", "5003"] and "nba/summary_5003.json" in reads2
    only = ArchiveSource(root, nights={"20240115"})
    assert [g.gid for u in only.units() for g in only.games(u, FenceGate())] == ["5001"]


def test_history_folder_reads_per_game_files_when_there_is_no_packed_shard_and_skips_symlinks(tmp_path):
    root = tmp_path / "hist"
    write_history_game(root, espn_record("nba", 2020, "7001", nba_night(2020, 1)))
    write_history_game(root, espn_record("nba", 2020, "7002", nba_night(2020, 2)))
    write_history_shard(root, "nba", 2019, [espn_record("nba", 2019, "7003", nba_night(2019, 1))])
    write_history_game(root, espn_record("nba", 2019, "7004", nba_night(2019, 2)))             # 2019 has a packed shard: the loose file is not read
    (root / "shards" / "espn_nba_2021.jsonl.gz").symlink_to(root / "shards" / "espn_nba_2019.jsonl.gz")
    src = HistorySource(root)
    units = src.units()
    assert [(u.league, u.season, u.origin) for u in units] == [("nba", 2019, "history-shard"), ("nba", 2020, "history-games")]
    assert sorted(g.gid for u in units for g in src.games(u, FenceGate())) == ["7001", "7002", "7003"]
    assert [u.season for u in src.units(seasons={2020})] == [2020] and src.units(leagues={"nfl"}) == []


def test_hoopr_rows_become_games_with_exact_nights_and_a_date_only_fallback(tmp_path):
    root = tmp_path / "hist"
    g1 = ("8001", nba_night(2020, 3), make_plays("8001", nba_night(2020, 3), 30))
    g2 = ("8002", nba_night(2020, 4), make_plays("8002", nba_night(2020, 4), 30, wall=False))
    write_hoopr(root, "hoopr_nba", 2020, hoopr_frame([g1, g2]))
    src = HistorySource(root)
    (unit,) = src.units()
    assert (unit.provider, unit.origin, unit.league) == ("hoopr", "dataset", "nba")
    got = {g.gid: g for g in src.games(unit, FenceGate())}
    assert got["8001"].night == nba_night(2020, 3) and got["8001"].night_exact and got["8001"].wall_frac == 1.0
    assert got["8002"].night == nba_night(2020, 4) and not got["8002"].night_exact and got["8002"].wall_frac == 0.0
    assert len(got["8001"].plays) == 30 and got["8001"].home == "HOM"


# ---- dedup ----------------------------------------------------------------------------------------------------

def light(provider="espn", origin="history-shard", gid="1", night="20200101", plays=300, wall=1.0, sig="s" * 20, changes=50, league="nba", ref="x"):
    return Light(provider, origin, league, 2020, gid, night, True, "clear", plays, wall, sig, changes, ref)


def test_dedup_by_id_keeps_the_better_copy_whatever_the_order():
    espn, hoopr = light("espn", gid="9"), light("hoopr", "dataset", gid="9", plays=310, wall=0.0)
    for order in ([espn, hoopr], [hoopr, espn]):
        r = dedup.resolve(order)
        assert list(r.keep) == [espn.key] and r.pairs == [{"league": "nba", "by": "id", "kept": "espn:history-shard:9", "dropped": "hoopr:dataset:9",
                                                       "kept_ref": "x", "dropped_ref": "x"}]
    # a copy with wall clocks beats a longer one without; with equal quality the provider order decides
    assert dedup.resolve([light("hoopr", "dataset", gid="9", wall=1.0), light("wehoop", "dataset", gid="9", wall=1.0)]).keep.keys() == {
        ("nba", "hoopr", "dataset", "9", "x")}
    assert dedup.resolve([light("espn", gid="9", wall=0.0, plays=300), light("hoopr", "dataset", gid="9", wall=1.0, plays=290)]).keep.keys() == {
        ("nba", "hoopr", "dataset", "9", "x")}


def test_dedup_by_signature_catches_another_id_space_and_leaves_ambiguity_alone():
    a, b = light("espn", gid="401"), light("hoopr", "dataset", gid="G-77", night="20200102")        # a day apart: still the same game
    r = dedup.resolve([a, b])
    assert list(r.keep) == [a.key] and r.pairs[0]["by"] == "signature"
    far = dedup.resolve([a, light("hoopr", "dataset", gid="G-78", night="20200105")])
    assert len(far.keep) == 2                                                              # five days apart: two games
    few = dedup.resolve([light("espn", gid="1", changes=3), light("hoopr", "dataset", gid="2", changes=3)])
    assert len(few.keep) == 2                                                              # a short trajectory is too common to trust
    amb = dedup.resolve([light("espn", gid="1"), light("espn", gid="2"), light("hoopr", "dataset", gid="3")])
    assert len(amb.keep) == 3 and amb.ambiguous == 1                                       # two ESPN games with one trajectory: not a copy
    clash = dedup.resolve([light("espn", gid="5", night="20200101"), light("hoopr", "dataset", gid="5", night="20200301")])
    assert len(clash.keep) == 2 and clash.id_conflicts == 1                                # one id on two far-apart nights: kept, counted
    assert dedup.resolve([a]).summary(1) == {"games_in": 1, "games_kept": 1, "removed": 0, "by_id": 0, "by_signature": 0,
                                             "ambiguous_clusters": 0, "id_conflicts": 0}


def test_a_game_in_espn_and_in_hoopr_is_built_once(tmp_path):
    hist = tmp_path / "h"
    make_history(hist, fenced=False)
    shared = [("2020000", nba_night(2020, 0), make_plays("2020000", nba_night(2020, 0), 40)),            # the ESPN id of a game in the ESPN shard
              ("2020001", nba_night(2020, 1), make_plays("2020001", nba_night(2020, 1), 40))]
    other_id = [("55555", nba_night(2020, 1), make_plays("2020001", nba_night(2020, 1), 40))]           # same plays under another provider id
    write_hoopr(hist, "hoopr_nba", 2020, hoopr_frame(shared + other_id))
    out = tmp_path / "out"
    doc = run_build(hist, out)
    d = doc["corpus"]["dedup"]
    assert d["by_id"] == 2 and d["by_signature"] == 1 and d["removed"] == 3 and d["games_kept"] == 30
    assert doc["sources"] == ["espn"] and manifest.verify_corpus(doc, out) == []
    pairs = [json.loads(x) for x in (out / manifest.CORPUS_DEDUP_FILE).read_text().splitlines()]
    assert len(pairs) == 3 and {p["by"] for p in pairs} == {"id", "signature"} and all(p["kept"].startswith("espn:") for p in pairs)
    assert gids_in(out, doc).isdisjoint({"55555"}) and len(gids_in(out, doc, season=2020)) == 6


def test_one_game_filed_in_two_shard_files_is_built_once(tmp_path):
    hist = make_history(tmp_path / "h", fenced=False)
    again = espn_record("nba", 2021, "2020003", nba_night(2020, 3), n=40)                    # season 2020's game 3, mislabelled 2021 in a second shard
    write_history_shard(hist, "nba", 2021, [again] + [espn_record("nba", 2021, f"2021{k:03d}", nba_night(2021, k), n=40) for k in range(6)])
    out = tmp_path / "out"
    doc = run_build(hist, out)
    assert doc["corpus"]["dedup"]["by_id"] == 1 and doc["corpus"]["dedup"]["games_kept"] == 30 and manifest.verify_corpus(doc, out) == []
    (pair,) = [json.loads(x) for x in (out / manifest.CORPUS_DEDUP_FILE).read_text().splitlines()]
    assert pair["kept"] == pair["dropped"] and pair["kept_ref"] != pair["dropped_ref"]
    assert sum(1 for row in doc["corpus"]["shards"] for g in shardio.read_shard(out.joinpath(*row["path"].split("/"))).header["index"]
               if g[0] == "2020003") == 1


def test_a_hoopr_only_season_keeps_its_provider_and_the_no_wall_tier(tmp_path):
    hist = tmp_path / "h"
    games = [(f"3{k:03d}", nba_night(2010, k), make_plays(f"3{k:03d}", nba_night(2010, k), 40, wall=False)) for k in range(5)]
    write_hoopr(hist, "hoopr_nba", 2010, hoopr_frame(games))
    out = tmp_path / "out"
    doc = run_build(hist, out)
    assert {r["tier"] for r in doc["corpus"]["shards"]} == {"nowall"} and doc["sources"] == ["hoopr"]
    assert doc["licences"]["hoopr"]["tag"] == "personal-use, not for commercial release" and manifest.verify_corpus(doc, out) == []
    assert all(r["era"] == "2010s" and r["league"] == "nba" for r in doc["corpus"]["shards"])


# ---- walk-forward roles -----------------------------------------------------------------------------------------

def test_roles_follow_whole_seasons_when_there_are_three_or_more(built):
    doc, out = built
    roles = {}
    for r in doc["corpus"]["shards"]:
        roles.setdefault(r["role"], set()).add(r["season"])
    assert roles == {"train": {2018, 2019, 2020, 2021}, "valid": {2022}, "holdout": {2026}}          # 2026 holds the two clear nights beside the lock
    sp = doc["corpus"]["splits"]["nba"]
    assert sp["mode"] == "season" and sp["valid_from"] == nba_night(2022, 0) and sp["holdout_from"] == EDGE["before_lock"][1]
    nights = {role: sorted(n for r in doc["corpus"]["shards"] if r["role"] == role for n in r["nights"]) for role in roles}
    assert max(nights["train"]) < min(nights["valid"]) <= max(nights["valid"]) < min(nights["holdout"])
    assert sp["folds"] and all(f["fit_last"] < f["check_first"] for f in sp["folds"])         # forward-chaining: always fit on the past


def test_plan_league_modes_cuts_and_embargo():
    ns = [nba_night(s, k) for s in (2019, 2020) for k in range(10)]
    night_mode = walkforward.plan_league("nba", ns)
    assert night_mode.mode == "night" and night_mode.holdout_from == ns[-2] and night_mode.valid_from == ns[-4]
    assert [night_mode.role_of(n) for n in (ns[0], ns[-3], ns[-1])] == ["train", "valid", "holdout"]
    few = walkforward.plan_league("nba", ns[:2])
    assert few.mode == "all-train" and few.role_of(ns[1]) == "train"
    seasons = walkforward.plan_league("nba", [nba_night(s, k) for s in range(2010, 2020) for k in range(3)], valid_frac=0.2, holdout_frac=0.2)
    assert (seasons.valid_from, seasons.holdout_from) == (nba_night(2016, 0), nba_night(2018, 0))
    gap = walkforward.plan_league("nba", [nba_night(s, k) for s in range(2010, 2014) for k in range(3)], gap_days=20)
    assert gap.role_of(nba_night(2013, 0)) == "holdout" and gap.role_of("20121101") is None and gap.role_of("20121001") == "valid"
    assert gap.role_of("20111101") is None and gap.role_of("20111001") == "train"


def test_season_labels_eras_and_tiers():
    assert [season_of("nba", n) for n in ("20231024", "20240415", "20240801")] == [2024, 2024, 2025]
    assert [season_of("nfl", n) for n in ("20230907", "20240115", "20240301")] == [2023, 2023, 2023]
    assert season_of("mlb", "20240401") == 2024 and era_of(2024) == "2020s" and era_of(2009) == "2000s"
    assert tier_of(0.9) == "wall" and tier_of(0.89) == "nowall"


def test_split_problems_flags_a_night_in_two_roles_and_a_game_in_two_roles():
    ok = walkforward.split_problems({"train": ["20200101"], "valid": ["20200102"], "holdout": ["20200103"]})
    assert ok == []
    assert walkforward.split_problems({"train": ["20200101", "20200102"], "valid": ["20200102"]})
    assert walkforward.split_problems({"train": ["20200101"], "valid": ["20200102"]}, {"20200101": ["g"], "20200102": ["g"]})


# ---- reader, stamp, stats, CLI -----------------------------------------------------------------------------------

def test_the_reader_hands_out_games_by_role_and_stamps_a_model_record(built):
    doc, out = built
    c = reader.load_corpus(out / "corpus-test-v1.manifest.json")
    train = list(c.games(("train",)))
    assert len(train) == 24 and all(m["role"] == "train" for m, _ in train)
    assert all(t[0] >= tk.NT.BOS0 and t[-1] == tk.NT.EOS for _, t in train)
    stamp = c.train_stamp(("train",))
    assert stamp["training_cutoff"] == "2020-11-15" and stamp["train_nights"][0] == nba_night(2018, 0)
    assert stamp["train_nights_sha256"] == fence.nights_sha256(stamp["train_nights"]) and stamp["data_manifest_hash"] == doc["manifest_sha256"]
    fence.guard_model(stamp, eval_nights=[nba_night(2022, 0)], name="model trained on train")
    assert "defensive_rebound" in c.names().kind_names("nba", tk.kind_slot_of("defensive_rebound"))
    with pytest.raises(ValueError, match="unknown role"):
        list(c.shards(("test",)))
    assert [s.header["season"] for s in c.shards(("holdout",), leagues=["nba"], tiers=["wall"])] == [2026]


def test_stats_report_events_per_game_by_league_and_era_coverage_and_shard_sizes(built):
    doc, out = built
    st = stats.compute(doc, out)
    assert st == json.loads((out / manifest.CORPUS_STATS_FILE).read_text()) and st["format"] == stats.CORPUS_STATS_FORMAT
    tr = st["scopes"]["trainable"]
    nba = tr["by_league_era"]["nba"]
    assert set(nba) == {"2010s", "2020s", "all"} and tr["all"]["games"] == 32 and tr["all"]["events"] == 32 * 40
    assert nba["2010s"]["games"] == 12 and nba["2020s"]["games"] == 20 and nba["all"]["events_per_game_mean"] == 40.0
    assert nba["all"]["events_per_game_median"] == 40.0 and nba["all"]["tokens_per_game_mean"] == 40 * 6 + 4
    assert nba["all"]["wall_games"] == 32 and nba["all"]["unknown_event_frac"] == 0.0      # every play is stamped, named and classed
    assert tr["all"]["unk_dt_after_first_frac"] == 0.0
    assert st["scopes"]["held"]["by_league_era"] == {} and set(st["roles"]) == {"train", "valid", "holdout"}
    s = st["shard_sizes"]
    assert s["count"] == 6 and s["bytes_min"] <= s["bytes_median"] <= s["bytes_max"] and s["bytes_total"] == sum(
        f["bytes"] for f in doc["files"] if f["path"].startswith("shards/"))
    cov = st["vocab_coverage"]
    assert cov["events"] == 32 * 40 and cov["kind_names"] >= 8 and 0 <= cov["shared_kind_slot_frac"] <= 1
    text = stats.format_table(st)
    assert "nba" in text and "2010s" in text and "vocab coverage" in text and "roles:" in text


def test_a_stale_stats_file_fails_verify(built, tmp_path):
    doc, out = built
    work = tmp_path / "copy"
    shutil.copytree(out, work)
    st = json.loads((work / manifest.CORPUS_STATS_FILE).read_text())
    st["scopes"]["trainable"]["all"]["games"] = 31
    (work / manifest.CORPUS_STATS_FILE).write_text(json.dumps(st))
    bad = copy.deepcopy(doc)
    f = next(x for x in bad["files"] if x["path"] == manifest.CORPUS_STATS_FILE)
    raw = (work / manifest.CORPUS_STATS_FILE).read_bytes()
    f["bytes"], f["sha256"] = len(raw), hashlib.sha256(raw).hexdigest()
    bad.update(manifest.derive(bad["files"], bad["corpus"]["shards"]))
    bad["manifest_sha256"] = dm.manifest_hash(bad)
    assert any(manifest.CORPUS_STATS_FILE in x and "recount" in x for x in manifest.verify_corpus(bad, work))


def test_shard_files_reject_damage(built, tmp_path):
    doc, out = built
    rel = doc["corpus"]["shards"][0]["path"]
    raw = out.joinpath(*rel.split("/")).read_bytes()
    bad = tmp_path / "bad.rzs"
    bad.write_bytes(b"NOTASHARD" + raw[9:])
    with pytest.raises(shardio.ShardError, match="magic"):
        shardio.read_shard(bad)
    bad.write_bytes(raw[:-2])
    with pytest.raises(shardio.ShardError, match="header says"):
        shardio.read_shard(bad)
    sh = shardio.read_shard(out.joinpath(*rel.split("/")))
    h = copy.deepcopy(sh.header)
    h["tokens"] += 1
    with pytest.raises(shardio.ShardError):
        shardio.encode_shard(h, np.array(sh.tokens))
    body = np.array(sh.tokens)
    body[5] = tk.NT.VOCAB_SIZE + 3
    bad.write_bytes(shardio.encode_shard(sh.header, body))
    assert any("outside vocab" in x for x in shardio.shard_problems(shardio.read_shard(bad)))
    body = np.array(sh.tokens)
    body[3] = tk.NT.UNK_DT                                     # a known gap bucket turned into the unknown id: the header counters disagree
    bad.write_bytes(shardio.encode_shard(sh.header, body))
    assert any("coverage" in x or "hash" in x for x in shardio.shard_problems(shardio.read_shard(bad)))


def test_cli_build_verify_stats_and_a_failing_verify(tmp_path, capsys):
    hist, out = make_history(tmp_path / "h"), tmp_path / "out"
    assert cli.main(["build", "--tag", "cli-test-v1", "--out", str(out), "--history-root", str(hist), "--quiet"]) == 0
    text = capsys.readouterr().out
    assert "built cli-test-v1: 6 shards, 32 games" in text and str(tmp_path) not in text
    mp = out / "cli-test-v1.manifest.json"
    assert cli.main(["verify", str(mp)]) == 0 and "0 problem(s)" in capsys.readouterr().out
    assert cli.main(["stats", str(mp)]) == 0 and "[trainable]" in capsys.readouterr().out
    assert cli.main(["stats", str(mp), "--json"]) == 0 and json.loads(capsys.readouterr().out)["tag"] == "cli-test-v1"
    assert cli.main(["show", str(mp), "--league", "nba", "--role", "holdout", "--game", "0", "--events", "2"]) == 0
    assert "kind_slot" in capsys.readouterr().out
    shard = next(out.joinpath("shards", "nba").glob("*-train.rzs"))
    raw = bytearray(shard.read_bytes())
    raw[-1] ^= 1
    shard.write_bytes(bytes(raw))
    assert cli.main(["verify", str(mp)]) == 1 and "FAIL" in capsys.readouterr().out
    assert cli.main(["build", "--tag", "cli-test-v1", "--out", str(out), "--history-root", str(hist), "--quiet", "--gap-days", "4"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert cli.main(["build", "--tag", "x1", "--leagues", "nope", "--history-root", str(hist)]) == 1


def test_nothing_the_show_runs_imports_the_corpus_code():
    """The corpus builder is sidecar tooling: no module under redzone/ outside the lab, and no agent code, may import it."""
    offenders = []
    for top in ("redzone", "agent"):
        for p in (REPO / top).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if rel.startswith("redzone/lab/") or "__pycache__" in rel:
                continue
            for node in ast.walk(ast.parse(p.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                if any("lab.corpus" in n or n.endswith("corpus_build") for n in names):
                    offenders.append(rel)
    assert offenders == []
