"""GameValue for men's college basketball (personal-use), and the raw files that are never downloaded.

Why: Nick ruled to respect robots; github.com's robots file disallows release paths, so kaggle_gamevalue.py may only use staged raw files.
"""
from __future__ import annotations

import json
import shutil

import pytest

from redzone.noggin import gamevalue as G
from scripts import kaggle_gamevalue as K
from tests.test_gamevalue import FakeTrainer, _raw_dir, _shards, nba_summary

LINE = f"{G.PERSONAL_USE}; hoopR ESPN men's college basketball"


@pytest.fixture(scope="module")
def cbb_shards(tmp_path_factory):
    return _shards(tmp_path_factory.mktemp("cbb") / "shards", "cbb", nba_summary, "hoopr_espn_cbb", LINE, "B", games=6)


def _args(root, out, *extra):
    return ["--shards", str(root), "--out", str(out), "--raw", str(_raw_dir(root)), "--leagues", "cbb", "--folds", "2", *extra]


class Net:
    """Records every URL anything asks for; serves nothing."""

    def __init__(self, monkeypatch):
        self.urls = []
        import requests
        monkeypatch.setattr(requests, "get", lambda url, *a, **k: self.urls.append(url) or (_ for _ in ()).throw(AssertionError(url)))
        monkeypatch.setattr(requests.Session, "get", lambda self_, url, *a, **k: self.urls.append(url) or (_ for _ in ()).throw(AssertionError(url)))


def test_cbb_is_a_gamevalue_league_tagged_personal_use():
    assert "cbb" in G.GV_LEAGUES and "hoopr_espn_cbb" in G.GV_PERSONAL_SOURCES


@pytest.mark.parametrize("flag", ["--personal-nba", "--personal-hoopr"])
def test_cbb_trains_with_the_flag_and_tags_model_and_manifest(cbb_shards, tmp_path, flag):
    out = tmp_path / "out"
    assert K.main(_args(cbb_shards, out, flag), trainer=FakeTrainer()) == 0
    man = json.loads((out / "manifest.json").read_text())
    assert man["license_tag"] == G.PERSONAL_USE and man["leagues"]["cbb"]["license_tag"] == G.PERSONAL_USE
    for version in ("v0", "v1"):
        assert man["leagues"]["cbb"]["models"][version]["license_tag"] == G.PERSONAL_USE
        assert G.load(G.model_path("cbb", version, out), "cbb").meta["license_tag"] == G.PERSONAL_USE


def test_cbb_without_the_flag_refuses_and_trains_nothing(cbb_shards, tmp_path):
    trainer = FakeTrainer()
    assert K.main(_args(cbb_shards, tmp_path / "out"), trainer=trainer) == K.EXIT_REFUSED
    assert trainer.calls == []


def test_cbb_model_without_the_personal_tag_does_not_load(cbb_shards, tmp_path):
    out = tmp_path / "out"
    assert K.main(_args(cbb_shards, out, "--personal-nba"), trainer=FakeTrainer()) == 0
    arrays, meta = K.fit_v0(K.load_rows(cbb_shards, "cbb")[0], "cbb", {"passed": True})
    meta["license_tag"] = ""
    with pytest.raises(G.ModelRejected, match="personal-use"):
        G.GameValueModel(arrays, meta, "cbb")


def test_missing_raw_files_stop_the_run_and_request_no_url(cbb_shards, tmp_path, monkeypatch, capsys):
    net, trainer = Net(monkeypatch), FakeTrainer()
    argv = _args(cbb_shards, tmp_path / "out", "--personal-nba")
    argv[argv.index("--raw") + 1] = str(tmp_path / "none")
    assert K.main(argv, trainer=trainer) == K.EXIT_REFUSED
    said = capsys.readouterr().out
    assert "pbp_w0.parquet" in said and "not attached" in said
    assert net.urls == [] and trainer.calls == []


def test_fetch_raw_requests_no_github_release_url(cbb_shards, tmp_path, monkeypatch):
    net = Net(monkeypatch)
    with pytest.raises(K.Refused, match="missing"):
        K.fetch_raw(cbb_shards, tmp_path / "empty", ["cbb"], True)
    assert net.urls == []
    from scripts import noggin_factory
    monkeypatch.setattr(noggin_factory, "_fetch", lambda url, dest: net.urls.append(url) or False)
    with pytest.raises(K.Refused):
        K.fetch_raw(cbb_shards, tmp_path / "empty", ["cbb"], True)
    assert net.urls == [] and not any("github.com" in u for u in net.urls)


def test_fetch_raw_passes_on_staged_files_and_copies_nothing(cbb_shards, tmp_path, monkeypatch):
    net = Net(monkeypatch)
    staged = tmp_path / "raw"
    shutil.copytree(_raw_dir(cbb_shards), staged)
    K.fetch_raw(cbb_shards, staged, ["cbb"], True)
    assert net.urls == []


def test_nfl_refuses_when_its_raw_files_are_not_attached(tmp_path, monkeypatch, capsys):
    from tests.test_gamevalue import nfl_summary
    root = _shards(tmp_path / "s", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A")
    net, trainer = Net(monkeypatch), FakeTrainer()
    assert K.main(["--shards", str(root), "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "none")], trainer=trainer) == K.EXIT_REFUSED
    assert "not attached" in capsys.readouterr().out and net.urls == [] and trainer.calls == []
