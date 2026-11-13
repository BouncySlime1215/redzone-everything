"""F-03 job runner: JobSpec refusals, the canonical hash, manifest-pinned data, and worker/spec hash agreement."""
from __future__ import annotations

import json

import pytest

from redzone.lab import datamanifest
from tools.jobs import worker
from tools.jobs.spec import PUBLIC_REPO_URL, JobSpec, SpecError, known_manifest_hashes, load

SHA = "0123456789abcdef0123456789abcdef01234567"
DATA = "ab" * 32
GOLDEN = "953d25656557a7b6a4a90121439aa3758a4d20539f72876ca372db2b33f212f8"


def make(**kw) -> JobSpec:
    base = dict(job_id="toy-1", repo_url=PUBLIC_REPO_URL, commit=SHA, entry={"module": "tools.jobs.toy", "args": []},
                seed=7, steps=[0, 100], ckpt_every=10, outputs=["metrics.json", "model.bin", "state.json"],
                budget_s=3600, stop_margin_s=600, config={"lr": 0.01, "layers": [2, 3]}, seed_libs=["random"])
    base.update(kw)
    return JobSpec(**base)


def test_a_good_spec_validates_and_its_hash_is_canonical():
    a = make().validate()
    b = make(config={"layers": [2, 3], "lr": 0.01})                  # same config, other key order
    assert a.spec_hash() == b.spec_hash()
    again = JobSpec.from_dict(json.loads(json.dumps(a.to_dict(), indent=3)))
    assert again.spec_hash() == a.spec_hash() and again.to_json() == a.to_json()
    assert make(seed=8).spec_hash() != a.spec_hash()


def test_the_hash_is_pinned():
    # A change here means every recorded spec hash changes: bump SPEC_FORMAT instead of editing this value.
    assert make().spec_hash() == GOLDEN


@pytest.mark.parametrize("kw, why", [
    ({"commit": "0123456"}, "full 40-hex"),
    ({"commit": "HEAD"}, "full 40-hex"),
    ({"commit": SHA.upper()}, "full 40-hex"),
    ({"commit": "integration/next"}, "full 40-hex"),
    ({"seed": None}, "seed is required"),
    ({"seed": True}, "seed is required"),
    ({"seed": "7"}, "seed is required"),
    ({"data": [DATA]}, "not in any verified manifest"),
    ({"data": ["abc"]}, "64-hex"),
    ({"repo_url": "https://user:pw@github.com/a/b.git"}, "plain https"),
    ({"repo_url": "http://github.com/a/b.git"}, "plain https"),
    ({"repo_url": "git@github.com:a/b.git"}, "plain https"),
    ({"steps": [5, 5]}, "steps"),
    ({"ckpt_every": 0}, "ckpt_every"),
    ({"outputs": ["../x"]}, "outputs"),
    ({"outputs": ["/abs/x"], "metrics_file": None}, "outputs"),
    ({"metrics_file": "other.json"}, "metrics_file"),
    ({"entry": {"module": "x; rm -rf /"}}, "no shell"),
    ({"entry": "python -m x"}, "no shell"),
    ({"stop_margin_s": 3600}, "stop_margin_s"),
    ({"config": {"api_key": "x"}}, "credential"),
    ({"config": {"path": "/Users/someone/data"}}, "absolute"),
    ({"seed_libs": ["jax"]}, "seed_libs"),
])
def test_refusals(kw, why):
    with pytest.raises(SpecError, match=why):
        make(**kw).validate()


def test_unknown_fields_are_refused():
    d = make().to_dict()
    d["shell"] = "rm -rf /"
    with pytest.raises(SpecError, match="unknown spec fields"):
        JobSpec.from_dict(d)


def _manifest(tmp_path, tag, tamper=False):
    doc = {"format": datamanifest.MANIFEST_FORMAT, "tag": tag, "kind": "archive", "files": []}
    doc["manifest_sha256"] = datamanifest.manifest_hash(doc)
    if tamper:
        doc["tag"] = tag + "-x"
    (tmp_path / f"{tag}{datamanifest.MANIFEST_SUFFIX}").write_text(json.dumps(doc))
    return doc["manifest_sha256"]


def test_data_hashes_must_come_from_a_verified_manifest(tmp_path):
    good = _manifest(tmp_path, "good-v1")
    bad = _manifest(tmp_path, "bad-v1", tamper=True)
    known = known_manifest_hashes(tmp_path)
    assert known == {good}
    make(data=[good]).validate(known)
    with pytest.raises(SpecError, match="not in any verified manifest"):
        make(data=[good, bad]).validate(known)
    assert known_manifest_hashes(tmp_path / "missing") == frozenset()


def test_load_reads_and_validates(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(make().to_dict(), indent=1))
    assert load(p).spec_hash() == make().spec_hash()
    p.write_text(json.dumps(make(commit="abc").to_dict()))
    with pytest.raises(SpecError):
        load(p)


def test_the_worker_hashes_and_checks_like_the_spec():
    s = make()
    assert worker.spec_hash(s.to_dict()) == s.spec_hash()
    assert worker.check_spec(s.to_dict()) == []
    assert worker.check_spec(make(commit="0123456").to_dict()) == ["commit is not a full 40-hex sha"]
    assert "seed missing or not an integer" in worker.check_spec(make(seed=None).to_dict())

